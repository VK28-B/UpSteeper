from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Callable

from .rewards import boot_enforcement
from .config import EMERGENCY_BLOCKED_APPS, IS_WINDOWS
from .db import log_app_usage, today_iso, get_setting

@dataclass
class SchedulerMessage:
    text: str
    state: dict

# ==========================================
# WINDOWS API HELPERS (PURE CTYPES)
# ==========================================

def get_active_window_details() -> tuple[str, str]:
    """Returns (process_name, window_title) of the active foreground window on Windows."""
    if not IS_WINDOWS:
        return "", ""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        
        # Get window title
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            
        # Get process ID
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        # Open process to get image name
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h_process:
            return "", title
            
        buf_name = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h_process, 0, buf_name, ctypes.byref(size)):
            proc_name = Path(buf_name.value).name.lower()
        else:
            proc_name = ""
        ctypes.windll.kernel32.CloseHandle(h_process)
        return proc_name, title
    except Exception:
        return "", ""

def is_process_running(process_name: str) -> bool:
    """Checks if a process is running on Windows using ctypes Toolhelp snapshot."""
    if not IS_WINDOWS:
        return False
    process_name = process_name.lower()
    
    TH32CS_SNAPPROCESS = 0x00000002
    
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD),
            ("cntUsage", ctypes.wintypes.DWORD),
            ("th32ProcessID", ctypes.wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.wintypes.ULONG)),
            ("th32ModuleID", ctypes.wintypes.DWORD),
            ("cntThreads", ctypes.wintypes.DWORD),
            ("th32ParentProcessID", ctypes.wintypes.DWORD),
            ("pcPriClassBase", ctypes.wintypes.LONG),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260)
        ]
    
    h_snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snapshot == -1:
        return False
        
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    
    retval = ctypes.windll.kernel32.Process32FirstW(h_snapshot, ctypes.byref(pe))
    running = False
    while retval:
        if pe.szExeFile.lower() == process_name:
            running = True
            break
        retval = ctypes.windll.kernel32.Process32NextW(h_snapshot, ctypes.byref(pe))
        
    ctypes.windll.kernel32.CloseHandle(h_snapshot)
    return running

def is_emergency_lock_active() -> bool:
    raw = get_setting("emergency_lock_until", "")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
        return datetime.now() < until
    except Exception:
        return False


# ==========================================
# BACKGROUND SCHEDULER
# ==========================================

class EnforcementScheduler:
    def __init__(self, callback: Callable[[SchedulerMessage], None] | None = None, interval_seconds: int = 180):
        self.callback = callback
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_day: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _emit(self, text: str, state: dict) -> None:
        if self.callback:
            try:
                self.callback(SchedulerMessage(text=text, state=state))
            except Exception:
                pass

    def _run(self) -> None:
        tick_interval = 5.0  # seconds per fast tick
        ticks_per_sync = int(self.interval_seconds / tick_interval)
        if ticks_per_sync < 1:
            ticks_per_sync = 1
            
        tick_count = 0
        
        while not self._stop.is_set():
            try:
                # 1. Fast Tick tasks (App tracking + Emergency lock enforcement)
                self._run_fast_tasks()
                
                # 2. Slow Tick tasks (Daily rules sync)
                if tick_count % ticks_per_sync == 0:
                    self._run_slow_tasks()
                    
                tick_count += 1
            except Exception as exc:
                self._emit(f"Scheduler error: {exc}", {})
                
            # Sleep in 5-second chunks
            end = time.time() + tick_interval
            while not self._stop.is_set() and time.time() < end:
                time.sleep(0.2)

    def _run_fast_tasks(self) -> None:
        # A. App Usage Tracking
        proc_name, title = get_active_window_details()
        if proc_name:
            app_name = "Other"
            proc_lower = proc_name.lower()
            
            # Map processes to friendly names
            if "chrome" in proc_lower or "msedge" in proc_lower or "brave" in proc_lower or "opera" in proc_lower or "vivaldi" in proc_lower or "firefox" in proc_lower:
                title_lower = title.lower()
                if "youtube" in title_lower:
                    app_name = "YouTube"
                elif "netflix" in title_lower:
                    app_name = "Netflix"
                elif "twitch" in title_lower:
                    app_name = "Twitch"
                elif "reddit" in title_lower:
                    app_name = "Reddit"
                elif "facebook" in title_lower or "instagram" in title_lower or "twitter" in title_lower or "x.com" in title_lower:
                    app_name = "Social Media"
                else:
                    app_name = "Browser"
            elif "code" in proc_lower:
                app_name = "VS Code"
            elif "discord" in proc_lower:
                app_name = "Discord"
            elif "steam" in proc_lower:
                app_name = "Steam"
            elif "spotify" in proc_lower:
                app_name = "Spotify"
            else:
                # Standard formatting
                app_name = proc_name.split(".")[0].capitalize()
                
            # Log 5 seconds of usage
            log_app_usage(app_name, 5)
            
        # B. Emergency Lock monitor
        if is_emergency_lock_active():
            # Terminate prohibited processes
            for app in EMERGENCY_BLOCKED_APPS:
                if is_process_running(app):
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/IM", app],
                            capture_output=True,
                            check=False
                        )
                        # Notify GUI via callback
                        self._emit(f"Closed distraction app: {app}", {})
                    except Exception:
                        pass

    def _run_slow_tasks(self) -> None:
        state = boot_enforcement()
        day = state.get("daily", {}).get("day")
        if self._last_day and day and day != self._last_day:
            self._emit("A new day started. Reward rules were reapplied.", state)
        elif state.get("goal_grants"):
            self._emit(f"Granted {len(state['goal_grants'])} goal reward(s).", state)
        else:
            self._emit("Reward rules synced.", state)
        self._last_day = day or self._last_day

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
