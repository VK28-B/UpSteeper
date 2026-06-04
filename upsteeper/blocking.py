from __future__ import annotations

import ctypes
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .config import BLOCKED_SITES, IS_WINDOWS

HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts") if IS_WINDOWS else None
MARK_START = "# UpSteeper START"
MARK_END = "# UpSteeper END"

def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def _read_hosts_text() -> str:
    if not IS_WINDOWS or HOSTS_PATH is None or not HOSTS_PATH.exists():
        return ""
    return HOSTS_PATH.read_text(encoding="utf-8", errors="ignore")

def _write_hosts_text(text: str) -> None:
    if not IS_WINDOWS or HOSTS_PATH is None:
        return
    HOSTS_PATH.write_text(text, encoding="utf-8")
    try:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, check=False)
    except Exception:
        pass

def _strip_managed_block(lines: list[str]) -> list[str]:
    cleaned = []
    inside = False
    for line in lines:
        if line.strip() == MARK_START:
            inside = True
            continue
        if line.strip() == MARK_END:
            inside = False
            continue
        if inside:
            continue
        if any(site in line for site in BLOCKED_SITES):
            continue
        cleaned.append(line)
    return cleaned

def block_sites(sites: list[str] | None = None) -> tuple[bool, str]:
    sites = list(sites or BLOCKED_SITES)
    if not IS_WINDOWS or HOSTS_PATH is None:
        return False, "Hosts-file blocking is available on Windows only."
    if not is_admin():
        return False, "Administrator rights are required to edit the hosts file."
    try:
        text = _read_hosts_text()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        lines = _strip_managed_block(lines)
        block_lines = [MARK_START]
        for site in sites:
            base = site.replace("www.", "")
            block_lines.append(f"127.0.0.1 {base}")
            block_lines.append(f"127.0.0.1 www.{base}")
        block_lines.append(MARK_END)
        _write_hosts_text("\n".join(lines + block_lines) + "\n")
        return True, "YouTube is blocked."
    except Exception as exc:
        return False, f"Blocking failed: {exc}"

def unblock_sites(sites: list[str] | None = None) -> tuple[bool, str]:
    if not IS_WINDOWS or HOSTS_PATH is None:
        return False, "Hosts-file blocking is available on Windows only."
    if not is_admin():
        return False, "Administrator rights are required to edit the hosts file."
    try:
        text = _read_hosts_text()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        lines = _strip_managed_block(lines)
        _write_hosts_text("\n".join(lines).strip() + "\n")
        return True, "YouTube is unblocked."
    except Exception as exc:
        return False, f"Unblocking failed: {exc}"

def _winreg():
    if not IS_WINDOWS:
        return None
    import winreg
    return winreg

def _get_reg_value(root, path: str, name: str) -> int | None:
    winreg = _winreg()
    if winreg is None:
        return None
    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return int(val)
    except Exception:
        return None

def _set_reg_value(root, path: str, name: str, value: int) -> None:
    winreg = _winreg()
    if winreg is None:
        return
    key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE)
    try:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
    finally:
        winreg.CloseKey(key)

def _restart_browsers():
    browsers = [
        "chrome.exe",
        "msedge.exe",
        "brave.exe",
        "opera.exe",
        "vivaldi.exe",
        "firefox.exe"
    ]

    for browser in browsers:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", browser],
                capture_output=True,
                check=False
            )
        except Exception:
            pass

def set_chromium_incognito(enabled: bool) -> tuple[bool, str]:
    if not is_admin():
        return False, "Administrator rights required."
    if not IS_WINDOWS:
        return False, "Incognito control is available on Windows only."
    try:
        winreg = _winreg()
        if winreg is None:
            return False, "Windows registry unavailable."
        value = 0 if enabled else 1
        paths = [
            r"Software\Policies\Google\Chrome",
            r"Software\Policies\Microsoft\Edge",
            r"Software\Policies\BraveSoftware\Brave",
            r"Software\Policies\BraveSoftware\Brave-Browser",
            r"Software\Policies\Opera Software\Opera",
            r"Software\Policies\Vivaldi",
        ]
        
        # Check if change is actually needed to avoid killing user browsers
        change_needed = False
        for path in paths:
            # We check both HKLM and HKCU
            hklm_val = _get_reg_value(winreg.HKEY_LOCAL_MACHINE, path, "IncognitoModeAvailability")
            hkcu_val = _get_reg_value(winreg.HKEY_CURRENT_USER, path, "IncognitoModeAvailability")
            if hklm_val != value or hkcu_val != value:
                change_needed = True
                break
                
        if not change_needed:
            return True, "Chromium policy is already set correctly."
            
        for path in paths:
            for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    _set_reg_value(root, path, "IncognitoModeAvailability", value)
                except Exception:
                    pass
        _restart_browsers()
        return True, "Chromium private mode policy updated."
    except Exception as exc:
        return False, f"Failed to update Chromium policy: {exc}"

def set_firefox_private_browsing(enabled: bool) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Firefox policy control is available on Windows only."
    try:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Mozilla Firefox" / "distribution",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Mozilla Firefox" / "distribution",
        ]
        payload = {"policies": {"DisablePrivateBrowsing": (not enabled)}}
        payload_str = json.dumps(payload, indent=2)
        
        change_needed = False
        for dist in candidates:
            pol_file = dist / "policies.json"
            if not pol_file.exists():
                change_needed = True
                break
            else:
                try:
                    content = pol_file.read_text(encoding="utf-8")
                    if json.loads(content) != payload:
                        change_needed = True
                        break
                except Exception:
                    change_needed = True
                    break
                    
        if not change_needed:
            return True, "Firefox policy is already set correctly."
            
        for dist in candidates:
            try:
                dist.mkdir(parents=True, exist_ok=True)
                (dist / "policies.json").write_text(payload_str, encoding="utf-8")
            except Exception:
                continue
        return True, "Firefox private browsing policy updated."
    except Exception as exc:
        return False, f"Failed to update Firefox policy: {exc}"

def enable_incognito_for_24h() -> tuple[bool, str]:
    ok1, msg1 = set_chromium_incognito(True)
    ok2, msg2 = set_firefox_private_browsing(True)
    return (ok1 or ok2), "; ".join(sorted({msg1, msg2}))

def disable_incognito() -> tuple[bool, str]:
    ok1, msg1 = set_chromium_incognito(False)
    ok2, msg2 = set_firefox_private_browsing(False)
    return (ok1 or ok2), "; ".join(sorted({msg1, msg2}))

def is_emergency_lock_active() -> bool:
    from .db import get_setting
    raw = get_setting("emergency_lock_until", "")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
        return datetime.now() < until
    except Exception:
        return False

def apply_daily_youtube_rule(completion_pct: float) -> tuple[bool, str]:
    # We consolidate hosts rules here
    if not IS_WINDOWS or HOSTS_PATH is None:
        return False, "Hosts-file blocking is available on Windows only."
    if not is_admin():
        return False, "Administrator rights are required to edit the hosts file."
        
    try:
        from .config import EMERGENCY_BLOCKED_SITES, BLOCKED_SITES
        from .db import get_setting
        
        # Check if youtube is temporarily unblocked via store purchase
        yt_unblocked_by_purchase = False
        yt_until_raw = get_setting("youtube_unblocked_until", "")
        if yt_until_raw:
            try:
                yt_until = datetime.fromisoformat(yt_until_raw)
                if datetime.now() < yt_until:
                    yt_unblocked_by_purchase = True
            except Exception:
                pass
        
        # YouTube block status
        yt_blocked = (completion_pct < 70.0) and not yt_unblocked_by_purchase
        # Emergency lock status
        emergency_active = is_emergency_lock_active()
        
        text = _read_hosts_text()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        lines = _strip_managed_block(lines)
        
        sites_to_block = []
        if yt_blocked:
            sites_to_block.extend(BLOCKED_SITES)
        if emergency_active:
            sites_to_block.extend(EMERGENCY_BLOCKED_SITES)
            
        if not sites_to_block:
            _write_hosts_text("\n".join(lines).strip() + "\n")
            return True, "All restrictions lifted."
            
        block_lines = [MARK_START]
        for site in sorted(list(set(sites_to_block))):
            base = site.replace("www.", "")
            block_lines.append(f"127.0.0.1 {base}")
            block_lines.append(f"127.0.0.1 www.{base}")
        block_lines.append(MARK_END)
        
        _write_hosts_text("\n".join(lines + block_lines) + "\n")
        
        status_msg = []
        if yt_blocked:
            status_msg.append("YouTube is BLOCKED")
        elif yt_unblocked_by_purchase:
            status_msg.append("YouTube UNLOCKED (Store Purchase)")
        else:
            status_msg.append("YouTube is OPEN")
            
        if emergency_active:
            status_msg.append("EMERGENCY LOCK ACTIVE")
        
        return True, " & ".join(status_msg)
    except Exception as exc:
        return False, f"Rule application failed: {exc}"

def youtube_state_from_completion(completion_pct: float) -> str:
    return "unblocked" if completion_pct >= 70.0 else "blocked"

def set_expiry_from_now(hours: int = 24) -> str:
    return (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
