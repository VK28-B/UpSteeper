from __future__ import annotations

import os
import queue
import sys
import threading
import subprocess
import json
import tkinter as tk
from datetime import datetime, date, timedelta
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog

try:
    from PIL import Image, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .analytics import build_monthly_chart, export_month_csv, monthly_summary, scoreboard_text, get_ai_coach_insights
from .blocking import (
    apply_daily_youtube_rule, disable_incognito, enable_incognito_for_24h, 
    is_admin, unblock_sites, block_sites, is_emergency_lock_active
)
from .config import (
    ACCENT, ACCENT_2, ACCENT_3, ASSETS_DIR, BG, BORDER, CARD_BORDER, CARD_PAD_X, CARD_PAD_Y, CHART_PATH,
    DANGER, HOVER, INPUT_BG, LOGO_ICON_PATH, LOGO_PATH, MUTED, PANEL, PANEL_2, PANEL_3, SUCCESS, TEXT, WARNING,
    APP_NAME, APP_SUBTITLE, VERSION, EMERGENCY_BLOCKED_APPS, EMERGENCY_BLOCKED_SITES
)
from .db import (
    add_goal, add_score_event, add_task, complete_goal, current_launch_count, delete_goal, delete_task,
    edit_goal, edit_task, get_setting, goal_stats, init_db, list_goals, list_tasks, log_event,
    recent_events, rebuild_rollup, set_setting, task_stats, update_goal_progress,
    update_task_status, today_iso,
    # Habit functions
    add_habit, delete_habit, list_habits, get_habit_history, complete_habit, uncomplete_habit, calculate_habit_streak,
    # Milestone functions
    add_milestone, delete_milestone, toggle_milestone, list_milestones, update_goal_progress_from_milestones,
    # App usage functions
    get_app_usage, get_app_usage_summary, log_app_usage,
    # Rewards and points functions
    get_discipline_points_balance, add_points_transaction, add_custom_reward, delete_custom_reward,
    list_custom_rewards, purchase_reward, list_purchased_rewards
)
from .rewards import boot_enforcement, manual_recalculate
from .scheduler import EnforcementScheduler, SchedulerMessage


# ── Native Windows Notification Function ─────────────────────────────────────

def send_toast_notification(title: str, message: str) -> None:
    """Asynchronously triggers a native Windows system tray balloon tip notification using PowerShell."""
    if sys.platform != "win32":
        return
    ps_cmd = f"""
    [void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms");
    $obj = New-Object System.Windows.Forms.NotifyIcon;
    $obj.Icon = [System.Drawing.SystemIcons]::Information;
    $obj.BalloonTipTitle = "{title.replace('"', '`"')}";
    $obj.BalloonTipText = "{message.replace('"', '`"')}";
    $obj.Visible = $True;
    $obj.ShowBalloonTip(5000);
    """
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception:
        pass


# ── Programmatic logo (Fallback) ─────────────────────────────────────────────

def _make_logo(size: int = 72):
    """Generate a neon-cyan up-arrow logo — no asset file needed."""
    if not PIL_AVAILABLE:
        return None
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    m = 2
    # Dark background circle
    draw.ellipse([m, m, s - m - 1, s - m - 1], fill="#0a1520")
    # Outer neon ring
    draw.ellipse([m, m, s - m - 1, s - m - 1], outline="#18d6ff", width=3)
    # Subtle inner ring
    d = 9
    draw.ellipse([m + d, m + d, s - m - d - 1, s - m - d - 1], outline="#18d6ff40", width=1)
    # Up-arrow polygon
    cx, cy = s // 2, s // 2
    aw, ah = s // 3, s // 3
    sw = max(4, aw // 3)                     # stem width
    tip_y    = cy - ah // 2 - 3
    head_y   = cy + 4
    stem_bot = cy + ah // 2 + 4
    points = [
        (cx,        tip_y),
        (cx - aw,   head_y),
        (cx - sw,   head_y),
        (cx - sw,   stem_bot),
        (cx + sw,   stem_bot),
        (cx + sw,   head_y),
        (cx + aw,   head_y),
    ]
    draw.polygon(points, fill="#18d6ff")
    return ImageTk.PhotoImage(img)


# ── Heatmap Canvas ────────────────────────────────────────────────────────────

class HeatmapCanvas(tk.Canvas):
    """Draws a 10x3 grid of squares representing habit consistency over the last 30 days."""
    def __init__(self, parent, size=14, spacing=3, bg_color=PANEL_2):
        super().__init__(
            parent, width=(size + spacing) * 10, height=(size + spacing) * 3,
            bg=bg_color, highlightthickness=0, bd=0
        )
        self.size = size
        self.spacing = spacing
        self.bg_color = bg_color
        
    def draw_heatmap(self, completed_dates: list[str]):
        self.delete("all")
        today = date.today()
        dates_to_draw = []
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            dates_to_draw.append(d.isoformat())
            
        completed_set = set(completed_dates)
        
        idx = 0
        w = self.size
        sp = self.spacing
        for row in range(3):
            for col in range(10):
                if idx >= 30:
                    break
                day_str = dates_to_draw[idx]
                is_completed = day_str in completed_set
                
                fill_color = SUCCESS if is_completed else PANEL_3
                border_color = BORDER if not is_completed else ACCENT
                
                x1 = col * (w + sp)
                y1 = row * (w + sp)
                x2 = x1 + w
                y2 = y1 + w
                
                self.create_rectangle(
                    x1, y1, x2, y2, fill=fill_color, outline=border_color, width=1
                )
                idx += 1


# ── App Usage Bar Chart ───────────────────────────────────────────────────────

class AppUsageChart(tk.Canvas):
    """Draws horizontal bars mapping duration of active background tracked applications."""
    def __init__(self, parent, width=380, height=220, bg_color=PANEL_2):
        super().__init__(
            parent, width=width, height=height, bg=bg_color, highlightthickness=0, bd=0
        )
        self.width = width
        self.height = height
        
    def draw_chart(self, usage_data: list[dict]):
        self.delete("all")
        if not usage_data:
            self.create_text(
                self.width // 2, self.height // 2, text="No app usage tracked today.",
                fill=MUTED, font=("Segoe UI", 10)
            )
            return
            
        usage_data = usage_data[:6]  # Show top 6
        max_duration = max(item["duration_seconds"] for item in usage_data) if usage_data else 1
        
        y_offset = 12
        bar_height = 14
        spacing = 18
        
        for idx, item in enumerate(usage_data):
            name = item["app_name"]
            sec = item["duration_seconds"]
            
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            dur_str = f"{h}h {m}m" if h > 0 else f"{m}m {s}s"
            
            # Custom colors for visuals
            color_map = {
                "YouTube": DANGER,
                "Browser": ACCENT,
                "VS Code": ACCENT_3,
                "Discord": ACCENT_2,
                "Steam": "#4e6ef2",
                "Spotify": SUCCESS
            }
            bar_color = color_map.get(name, MUTED)
            
            # Name and label
            self.create_text(
                10, y_offset, text=f"{name}", anchor="w", fill=TEXT, font=("Segoe UI", 8, "bold")
            )
            self.create_text(
                self.width - 10, y_offset, text=dur_str, anchor="e", fill=MUTED, font=("Segoe UI", 8)
            )
            
            # Bar placement
            x_start = 10
            x_end = self.width - 10
            total_width = x_end - x_start
            
            # Background track
            self.create_rectangle(
                x_start, y_offset + 8, x_end, y_offset + 8 + bar_height,
                fill=PANEL_3, outline="", width=0
            )
            
            # Colored bar
            pct = sec / max_duration
            bar_w = int(pct * total_width)
            if bar_w > 0:
                self.create_rectangle(
                    x_start, y_offset + 8, x_start + bar_w, y_offset + 8 + bar_height,
                    fill=bar_color, outline="", width=0
                )
                
            y_offset += spacing + bar_height


# ── Scrollable frame ──────────────────────────────────────────────────────────

class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg=PANEL_2, height=300):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, height=height)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner  = tk.Frame(self.canvas, bg=bg)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.inner.bind("<Enter>", self._bind_wheel)
        self.inner.bind("<Leave>", self._unbind_wheel)

    def _on_configure(self, _e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self.window, width=e.width)

    def _bind_wheel(self, _e):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _e):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")


# ── Card ──────────────────────────────────────────────────────────────────────

class Card(tk.Frame):
    """Dark panel with a 3-px coloured accent stripe at the top."""

    def __init__(self, parent, title: str, subtitle: str = "",
                 accent: str = ACCENT, width: int | None = None):
        kw: dict = {"bg": PANEL_2, "highlightthickness": 1,
                    "highlightbackground": CARD_BORDER}
        if width:
            kw["width"] = width
        super().__init__(parent, **kw)
        if width:
            self.grid_propagate(False)

        # Coloured top stripe
        tk.Frame(self, bg=accent, height=3).pack(fill="x", side="top")

        self.title_lbl = tk.Label(
            self, text=title, bg=PANEL_2, fg=TEXT,
            font=("Segoe UI", 11, "bold"), anchor="w")
        self.title_lbl.pack(fill="x", padx=CARD_PAD_X, pady=(CARD_PAD_Y - 2, 0))

        self.sub_lbl = tk.Label(
            self, text=subtitle, bg=PANEL_2, fg=MUTED,
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=900)
        if subtitle:
            self.sub_lbl.pack(fill="x", padx=CARD_PAD_X, pady=(2, CARD_PAD_Y // 2))

    def set_subtitle(self, text: str):
        self.sub_lbl.config(text=text)


# ── Animated stat card ────────────────────────────────────────────────────────

class AnimatedStatCard(Card):
    def __init__(self, parent, title: str, subtitle: str = "",
                 accent: str = ACCENT, width: int = 220):
        super().__init__(parent, title, subtitle, accent, width)
        self.value_lbl = tk.Label(
            self, text="0", bg=PANEL_2, fg=accent,
            font=("Segoe UI", 28, "bold"), anchor="w")
        self.value_lbl.pack(fill="x", padx=CARD_PAD_X, pady=(0, CARD_PAD_Y))
        self._value  = 0.0
        self._target = 0.0
        self._suffix = ""
        self._prefix = ""
        self.after(16, self._tick)

    def set_value(self, value: float, suffix: str = "", prefix: str = ""):
        self._target = float(value)
        self._suffix = suffix
        self._prefix = prefix

    def _tick(self):
        diff = self._target - self._value
        self._value = self._value + diff * 0.18 if abs(diff) > 0.2 else self._target
        self.value_lbl.config(
            text=f"{self._prefix}{int(round(self._value))}{self._suffix}")
        self.after(16, self._tick)


# ── Progress bar (Meter) ──────────────────────────────────────────────────────

class Meter(tk.Frame):
    def __init__(self, parent, value=0.0, accent=ACCENT, height=12, label=""):
        super().__init__(parent, bg=PANEL_2)
        self.value  = float(value)
        self.target = float(value)
        self.accent = accent
        self.height = height
        self.label  = label
        self.canvas = tk.Canvas(self, height=height, bg=PANEL_2,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", expand=True)
        self.bind("<Configure>", lambda _: self.redraw())
        self.after(16, self._animate)

    def set(self, value: float):
        self.target = max(0.0, min(100.0, float(value)))

    def redraw(self):
        self.canvas.delete("all")
        w = max(1, self.winfo_width())
        h = self.height
        self.canvas.create_rectangle(0, 0, w, h, fill=PANEL_3, outline="", width=0)
        fw = max(0, int(self.value / 100 * w))
        if fw > 0:
            self.canvas.create_rectangle(0, 0, fw, h, fill=self.accent,
                                         outline="", width=0)
        if self.label:
            self.canvas.create_text(8, h // 2, text=self.label, anchor="w",
                                    fill=TEXT, font=("Segoe UI", 7, "bold"))

    def _animate(self):
        diff = self.target - self.value
        if abs(diff) > 0.4:
            self.value += diff * 0.2
            self.redraw()
        elif abs(diff) > 0.01:
            self.value = self.target
            self.redraw()
        self.after(16, self._animate)


# ── Circular meter ────────────────────────────────────────────────────────────

class CircularMeter(tk.Canvas):
    def __init__(self, parent, size=190, thickness=13, accent=ACCENT, bg_color=PANEL_2):
        super().__init__(parent, width=size, height=size,
                         bg=bg_color, highlightthickness=0, bd=0)
        self.size      = size
        self.thickness = thickness
        self.accent    = accent
        self.bg_color  = bg_color
        self.value  = 0.0
        self.target = 0.0
        self.bind("<Configure>", lambda _: self.redraw())
        self.after(16, self._animate)

    def set(self, value: float):
        self.target = max(0.0, min(100.0, float(value)))

    def redraw(self):
        self.delete("all")
        s = min(self.winfo_width() or self.size, self.winfo_height() or self.size)
        t = self.thickness
        pad = t + 6
        self.create_oval(pad, pad, s - pad, s - pad, outline=PANEL_3, width=t)
        extent = -360.0 * (self.value / 100.0)
        if abs(extent) > 0.05:
            self.create_arc(pad, pad, s - pad, s - pad, start=90, extent=extent,
                            outline=self.accent, style="arc", width=t)
        pct_col = (SUCCESS if self.value >= 100 else
                   ACCENT  if self.value >= 70  else
                   WARNING if self.value >= 40  else DANGER)
        self.create_text(s // 2, s // 2 - 10,
                         text=f"{int(round(self.value))}%",
                         fill=pct_col, font=("Segoe UI", 22, "bold"))
        sub = "Complete!" if self.value >= 100 else ("Unlocked" if self.value >= 70 else "Today's goal")
        self.create_text(s // 2, s // 2 + 18, text=sub,
                         fill=MUTED, font=("Segoe UI", 8))

    def _animate(self):
        diff = self.target - self.value
        if abs(diff) > 0.4:
            self.value += diff * 0.18
            self.redraw()
        elif abs(diff) > 0.01:
            self.value = self.target
            self.redraw()
        self.after(16, self._animate)


# ── Buttons ───────────────────────────────────────────────────────────────────

class HoverButton(tk.Button):
    def __init__(self, parent, text: str, command=None,
                 bg_color=ACCENT, fg_color="#061019", width=None):
        super().__init__(
            parent, text=text, command=command,
            bg=bg_color, fg=fg_color,
            activebackground=bg_color, activeforeground=fg_color,
            relief="flat", bd=0, cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=14, pady=7, width=width,
        )
        self._base = bg_color
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    @staticmethod
    def _lighten(c: str) -> str:
        try:
            r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            return f"#{min(255,r+30):02x}{min(255,g+30):02x}{min(255,b+30):02x}"
        except Exception:
            return c

    def _on_enter(self, _e):
        self.config(bg=self._lighten(self._base))

    def _on_leave(self, _e):
        self.config(bg=self._base)


class GhostButton(tk.Button):
    def __init__(self, parent, text: str, command=None, accent=ACCENT, width=None):
        super().__init__(
            parent, text=text, command=command,
            bg=PANEL_3, fg=accent,
            activebackground=PANEL_2, activeforeground=accent,
            relief="flat", bd=0, cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=10, pady=6, width=width,
        )
        self.bind("<Enter>", lambda _: self.config(bg=HOVER))
        self.bind("<Leave>", lambda _: self.config(bg=PANEL_3))


# ── Status pill ───────────────────────────────────────────────────────────────

class StatusPill(tk.Label):
    def __init__(self, parent, text="", bg=ACCENT, fg="#061019"):
        super().__init__(parent, text=text, bg=bg, fg=fg,
                         font=("Segoe UI", 8, "bold"), padx=10, pady=5)

    def set(self, text: str, bg: str | None = None, fg: str | None = None):
        self.config(text=text)
        if bg:
            self.config(bg=bg)
        if fg:
            self.config(fg=fg)


# ── Dialogs ───────────────────────────────────────────────────────────────────

class _StyledDialog(simpledialog.Dialog):
    """Base with dark theming applied."""
    def body(self, master):
        master.configure(bg=PANEL_2)
        try:
            self.configure(bg=PANEL_2)
        except Exception:
            pass

    def _field(self, master, row, label, key, multiline=False, initial=None):
        tk.Label(master, text=label, bg=PANEL_2, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w",
                                             padx=10, pady=(6, 0))
        if multiline:
            w = tk.Text(master, width=44, height=4, bg=INPUT_BG, fg=TEXT,
                        insertbackground=ACCENT, relief="flat", wrap="word",
                        font=("Segoe UI", 10), bd=4)
            w.insert("1.0", initial or "")
        else:
            w = tk.Entry(master, width=46, bg=INPUT_BG, fg=TEXT,
                         insertbackground=ACCENT, relief="flat",
                         font=("Segoe UI", 10), bd=4)
            w.insert(0, str(initial or ""))
        w.grid(row=row, column=1, sticky="ew", padx=10, pady=(6, 0))
        return w


class TaskDialog(_StyledDialog):
    def __init__(self, parent, title: str = "Task", initial: dict | None = None):
        self.initial = initial or {}
        self.result  = None
        super().__init__(parent, title=title)

    def body(self, master):
        super().body(master)
        self.e_title    = self._field(master, 0, "Task title",  "title",    initial=self.initial.get("title", ""))
        self.e_category = self._field(master, 1, "Category",    "category", initial=self.initial.get("category", ""))
        self.e_notes    = self._field(master, 2, "Notes",       "notes",    multiline=True, initial=self.initial.get("notes", ""))
        tk.Label(master, text="Priority", bg=PANEL_2, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", padx=10, pady=6)
        self.priority = ttk.Combobox(master, values=["1", "2", "3", "4", "5"],
                                      width=43, state="readonly")
        self.priority.set(str(self.initial.get("priority", 2)))
        self.priority.grid(row=3, column=1, sticky="ew", padx=10, pady=6)
        master.grid_columnconfigure(1, weight=1)
        return self.e_title

    def apply(self):
        self.result = {
            "title":    self.e_title.get().strip(),
            "category": self.e_category.get().strip(),
            "notes":    self.e_notes.get("1.0", "end").strip(),
            "priority": int(self.priority.get() or 2),
        }


class GoalDialog(_StyledDialog):
    def __init__(self, parent, title: str = "Goal", initial: dict | None = None):
        self.initial = initial or {}
        self.result  = None
        super().__init__(parent, title=title)

    def body(self, master):
        super().body(master)
        self.e_title   = self._field(master, 0, "Goal title", "title",   initial=self.initial.get("title", ""))
        self.e_details = self._field(master, 1, "Details",    "details", multiline=True, initial=self.initial.get("details", ""))
        self.e_target  = self._field(master, 2, "Target %",   "target",  initial=str(self.initial.get("target", 100)))
        master.grid_columnconfigure(1, weight=1)
        return self.e_title

    def apply(self):
        try:
            target = max(1, min(100, int(self.e_target.get().strip() or "100")))
        except Exception:
            target = 100
        self.result = {
            "title":   self.e_title.get().strip(),
            "details": self.e_details.get("1.0", "end").strip(),
            "target":  target,
        }


# ── Task row ──────────────────────────────────────────────────────────────────

_PRI_COLORS  = {1: MUTED, 2: MUTED, 3: WARNING, 4: ACCENT_2, 5: DANGER}
_PRI_LABELS  = {3: "MED", 4: "HIGH", 5: "CRIT"}
_STATUS_COL  = {"done": SUCCESS, "failed": DANGER, "pending": WARNING}


class TaskRow(tk.Frame):
    def __init__(self, parent, task: dict, on_status, on_edit, on_delete):
        super().__init__(parent, bg=PANEL_2, highlightthickness=1,
                         highlightbackground=CARD_BORDER)
        self.task      = task
        self.on_status = on_status
        self.on_edit   = on_edit
        self.on_delete = on_delete

        status  = task.get("status", "pending")
        s_color = _STATUS_COL.get(status, WARNING)

        stripe = tk.Frame(self, width=4, bg=s_color)
        stripe.pack(side="left", fill="y")
        stripe.pack_propagate(False)

        content = tk.Frame(self, bg=PANEL_2)
        content.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=9)

        title_row = tk.Frame(content, bg=PANEL_2)
        title_row.pack(fill="x")
        tk.Label(title_row, text=task["title"], bg=PANEL_2, fg=TEXT,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left")

        pri = int(task.get("priority", 2))
        if pri >= 3:
            tk.Label(title_row, text=f"  {_PRI_LABELS[pri]}",
                     bg=PANEL_2, fg=_PRI_COLORS.get(pri, MUTED),
                     font=("Segoe UI", 8, "bold")).pack(side="left")

        tk.Label(title_row,
                 text=f"  {status.upper()}",
                 bg=PANEL_2, fg=s_color,
                 font=("Segoe UI", 8)).pack(side="left")

        meta = self._meta()
        if meta:
            tk.Label(content, text=meta, bg=PANEL_2, fg=MUTED,
                     font=("Segoe UI", 8), anchor="w",
                     justify="left", wraplength=540).pack(fill="x", pady=(3, 0))

        ctl = tk.Frame(self, bg=PANEL_2)
        ctl.pack(side="right", padx=10, pady=9)

        # Apply locking if emergency is active
        lock_state = "disabled" if is_emergency_lock_active() else "normal"

        GhostButton(ctl, "✓ Done",  command=lambda: self.on_status(self.task["id"], "done"),    accent=SUCCESS).pack(side="left", padx=2)
        GhostButton(ctl, "✕ Fail",  command=lambda: self.on_status(self.task["id"], "failed"),  accent=DANGER ).pack(side="left", padx=2)
        GhostButton(ctl, "○ Reset", command=lambda: self.on_status(self.task["id"], "pending"), accent=WARNING).pack(side="left", padx=2)
        
        sep = tk.Frame(ctl, bg=CARD_BORDER, width=1)
        sep.pack(side="left", fill="y", padx=6)
        
        btn_edit = GhostButton(ctl, "Edit", command=lambda: self.on_edit(self.task["id"]),   accent=ACCENT  )
        btn_edit.config(state=lock_state)
        btn_edit.pack(side="left", padx=2)
        
        btn_del = GhostButton(ctl, "Del",  command=lambda: self.on_delete(self.task["id"]), accent=DANGER  )
        btn_del.config(state=lock_state)
        btn_del.pack(side="left", padx=2)

    def _meta(self) -> str:
        parts: list[str] = []
        cat = self.task.get("category", "")
        if cat:
            parts.append(f"📁 {cat}")
        notes = (self.task.get("notes") or "").strip()
        if notes:
            parts.append(notes[:70] + ("…" if len(notes) > 70 else ""))
        return "  •  ".join(parts)


# ── Goal Card Widget (Milestones Integrated) ───────────────────────────────

class GoalCardWidget(tk.Frame):
    """Futuristic Goal Card containing child Milestone checklists and progress trackers."""
    def __init__(self, parent, goal: dict, app: UpSteeperApp):
        super().__init__(parent, bg=PANEL_2, highlightthickness=1, highlightbackground=CARD_BORDER)
        self.goal = goal
        self.app = app
        self.expanded = False
        self._build_ui()
        
    def _build_ui(self):
        header = tk.Frame(self, bg=PANEL_2)
        header.pack(fill="x", padx=10, pady=8)
        
        progress = int(self.goal.get("progress", 0))
        is_complete = progress >= 100 or self.goal.get("status") == "complete"
        accent = SUCCESS if is_complete else ACCENT_2
        
        self.stripe = tk.Frame(header, width=4, bg=accent)
        self.stripe.pack(side="left", fill="y", padx=(0, 10))
        
        self.exp_btn = GhostButton(header, "▶", command=self.toggle_expand, accent=MUTED)
        self.exp_btn.pack(side="left", padx=(0, 8))
        
        info_frame = tk.Frame(header, bg=PANEL_2)
        info_frame.pack(side="left", fill="both", expand=True)
        
        title_row = tk.Frame(info_frame, bg=PANEL_2)
        title_row.pack(fill="x")
        
        tk.Label(title_row, text=self.goal["title"], bg=PANEL_2, fg=TEXT, font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left")
        badge_text = "  COMPLETE" if is_complete else f"  {progress}%"
        badge_color = SUCCESS if is_complete else (ACCENT_2 if progress >= 50 else WARNING)
        tk.Label(title_row, text=badge_text, bg=PANEL_2, fg=badge_color, font=("Segoe UI", 9, "bold")).pack(side="left")
        
        details = (self.goal.get("details") or "").strip()
        if details:
            tk.Label(info_frame, text=details[:90] + ("…" if len(details) > 90 else ""), bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(2, 0))
            
        self.meter = Meter(info_frame, value=progress, accent=accent, height=8)
        self.meter.pack(fill="x", pady=(5, 0))
        
        ctl = tk.Frame(header, bg=PANEL_2)
        ctl.pack(side="right", padx=(10, 0))
        
        # Disabled during Emergency Lock
        lock_state = "disabled" if is_emergency_lock_active() else "normal"
        
        btn_mile = GhostButton(ctl, "+ Milestone", command=self.toggle_expand, accent=ACCENT)
        btn_mile.config(state=lock_state)
        btn_mile.pack(side="left", padx=2)
        
        if is_complete and int(self.goal.get("reward_claimed", 0)) == 0:
            GhostButton(ctl, "Claim 24h", command=lambda: self.app.claim_goal_reward_ui(self.goal["id"]), accent=WARNING).pack(side="left", padx=2)
            
        btn_edit = GhostButton(ctl, "Edit", command=lambda: self.app.edit_goal_dialog(self.goal["id"]), accent=ACCENT_3)
        btn_edit.config(state=lock_state)
        btn_edit.pack(side="left", padx=2)
        
        btn_del = GhostButton(ctl, "Del", command=lambda: self.app.delete_goal_ui(self.goal["id"]), accent=DANGER)
        btn_del.config(state=lock_state)
        btn_del.pack(side="left", padx=2)
        
        self.milestones_frame = tk.Frame(self, bg=PANEL_3, highlightthickness=1, highlightbackground=BORDER)
        
    def toggle_expand(self):
        if is_emergency_lock_active():
            return
        if self.expanded:
            self.milestones_frame.pack_forget()
            self.exp_btn.config(text="▶")
            self.expanded = False
        else:
            self.milestones_frame.pack(fill="x", padx=14, pady=(0, 10))
            self.exp_btn.config(text="▼")
            self.expanded = True
            self.refresh_milestones()
            
    def refresh_milestones(self):
        for w in self.milestones_frame.winfo_children():
            w.destroy()
            
        title_row = tk.Frame(self.milestones_frame, bg=PANEL_3)
        title_row.pack(fill="x", padx=8, pady=6)
        tk.Label(title_row, text="Project Milestones", bg=PANEL_3, fg=ACCENT_2, font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left")
        
        milestones = list_milestones(self.goal["id"])
        
        for m in milestones:
            row = tk.Frame(self.milestones_frame, bg=PANEL_3)
            row.pack(fill="x", padx=16, pady=2)
            
            var = tk.BooleanVar(value=bool(m["completed"]))
            chk = tk.Checkbutton(
                row, variable=var, bg=PANEL_3, fg=TEXT, activebackground=PANEL_3, activeforeground=TEXT,
                selectcolor=INPUT_BG, highlightthickness=0, bd=0,
                command=lambda m_id=m["id"], v=var: self.toggle_milestone_ui(m_id, v.get())
            )
            chk.pack(side="left")
            
            lbl_font = ("Segoe UI", 9, "overstrike") if m["completed"] else ("Segoe UI", 9)
            lbl_color = MUTED if m["completed"] else TEXT
            tk.Label(row, text=m["title"], bg=PANEL_3, fg=lbl_color, font=lbl_font, anchor="w").pack(side="left", padx=6)
            
            GhostButton(row, "✕", command=lambda m_id=m["id"]: self.delete_milestone_ui(m_id), accent=DANGER).pack(side="right")
            
        # Add milestone form
        add_row = tk.Frame(self.milestones_frame, bg=PANEL_3)
        add_row.pack(fill="x", padx=16, pady=(8, 6))
        
        self.new_m_title = tk.StringVar()
        entry = tk.Entry(add_row, textvariable=self.new_m_title, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 9), width=35)
        entry.pack(side="left", padx=(0, 6), ipady=2)
        entry.bind("<Return>", lambda _: self.add_milestone_ui())
        
        HoverButton(add_row, "+ Add", command=self.add_milestone_ui, bg_color=ACCENT_2, fg_color="#061019").pack(side="left")
        
    def add_milestone_ui(self):
        title = self.new_m_title.get().strip()
        if not title:
            return
        try:
            add_milestone(self.goal["id"], title)
            self.new_m_title.set("")
            self.refresh_milestones()
            self.app.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            
    def toggle_milestone_ui(self, m_id: int, completed: bool):
        try:
            toggle_milestone(m_id, 1 if completed else 0)
            self.refresh_milestones()
            self.app.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            
    def delete_milestone_ui(self, m_id: int):
        try:
            delete_milestone(m_id)
            self.refresh_milestones()
            self.app.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))


# ── Main UpSteeper Application ───────────────────────────────────────────────

class UpSteeperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        init_db()
        self.title(APP_NAME)
        self.geometry("1440x900")
        self.minsize(1200, 780)
        self.configure(bg=BG)

        try:
            if LOGO_ICON_PATH.exists():
                self.iconbitmap(default=str(LOGO_ICON_PATH))
        except Exception:
            pass

        self._queue: queue.Queue[SchedulerMessage] = queue.Queue()
        self._scheduler = EnforcementScheduler(callback=self._scheduler_callback, interval_seconds=180)
        self._chart_photo = None
        self._logo_photo = None
        self._task_rows: dict[int, TaskRow] = {}
        self._goal_widgets: list[GoalCardWidget] = []

        # UI state tracking
        self.current_page = "dashboard"

        self._build_style()
        self._build_sidebar_layout()
        self._load_logo()
        
        # Async: run recalcs after GUI starts
        self.after(200, lambda: self.sync_now(silent=True))
        self._scheduler.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250,    self._poll_queue)
        self.after(1000,   self._tick_clock)
        self.after(60_000, self._periodic_sync)

    # ── Style ──────────────────────────────────────────────────────────────────

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=BG, foreground=TEXT,
                         fieldbackground=INPUT_BG, troughcolor=PANEL_3,
                         bordercolor=BORDER)
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
        style.configure("TNotebook.Tab", background=PANEL_3, foreground=MUTED,
                         padding=(20, 11), borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL_2), ("active", PANEL)],
                  foreground=[("selected", ACCENT),  ("active", TEXT)])
        style.configure("TCombobox", fieldbackground=INPUT_BG,
                         background=PANEL_2, foreground=TEXT, arrowcolor=TEXT)
        style.configure("TScrollbar", background=PANEL_3, troughcolor=BG,
                         bordercolor=BG, arrowcolor=MUTED)
        self.option_add("*Font", ("Segoe UI", 10))

    # ── Custom Sidebar Layout Redesign ─────────────────────────────────────────

    def _build_sidebar_layout(self):
        # 1. Left Sidebar
        self.sidebar_frame = tk.Frame(self, bg=PANEL, width=220, highlightthickness=1, highlightbackground=CARD_BORDER)
        self.sidebar_frame.pack(side="left", fill="y")
        self.sidebar_frame.pack_propagate(False)

        # Sidebar Header (Logo + Title)
        header_wrap = tk.Frame(self.sidebar_frame, bg=PANEL)
        header_wrap.pack(fill="x", padx=14, pady=16)

        self.logo_lbl = tk.Label(header_wrap, bg=PANEL)
        self.logo_lbl.pack(side="left", padx=(0, 8))

        lbl_title = tk.Label(header_wrap, text="FOCUS CORE", bg=PANEL, fg=TEXT, font=("Segoe UI", 12, "bold"))
        lbl_title.pack(anchor="w", pady=(8, 0))
        tk.Label(header_wrap, text=f"v{VERSION}", bg=PANEL, fg=MUTED, font=("Segoe UI", 7)).pack(anchor="w")

        # Visual Separator
        tk.Frame(self.sidebar_frame, bg=BORDER, height=1).pack(fill="x", padx=14, pady=6)

        # Navigation Buttons list
        self.nav_items = [
            ("dashboard", "🏠  Dashboard"),
            ("tasks", "📋  Daily Tasks"),
            ("goals", "🎯  Long Term Goals"),
            ("habits", "🔥  Habit Tracker"),
            ("usage", "📊  App Usage"),
            ("store", "🛍️  Reward Store"),
            ("coach", "🧠  AI Coach"),
            ("settings", "⚙️  Settings & Logs"),
        ]

        self.nav_buttons: dict[str, tuple[tk.Frame, tk.Button]] = {}
        for key, name in self.nav_items:
            btn_frame = tk.Frame(self.sidebar_frame, bg=PANEL)
            btn_frame.pack(fill="x", padx=10, pady=2)
            
            # Active stripe indicator
            stripe = tk.Frame(btn_frame, width=3, bg=PANEL)
            stripe.pack(side="left", fill="y")
            
            btn = tk.Button(
                btn_frame, text=name, bg=PANEL, fg=MUTED, activebackground=PANEL_3, activeforeground=TEXT,
                relief="flat", bd=0, anchor="w", font=("Segoe UI", 9, "bold"), cursor="hand2",
                padx=10, pady=8, command=lambda k=key: self.switch_page(k)
            )
            btn.pack(side="left", fill="x", expand=True)
            
            # Bind hover
            btn.bind("<Enter>", lambda _e, b=btn: b.config(bg=PANEL_3, fg=TEXT))
            btn.bind("<Leave>", lambda _e, b=btn, k=key: self._on_nav_leave(b, k))
            
            self.nav_buttons[key] = (stripe, btn)

        # Separator before Locked mode panel
        tk.Frame(self.sidebar_frame, bg=BORDER, height=1).pack(fill="x", padx=14, pady=10)

        # Sidebar Emergency Lock Display Box
        self.side_lock_box = tk.Frame(self.sidebar_frame, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER)
        self.side_lock_box.pack(fill="x", padx=14, pady=4)
        
        self.side_lock_title = tk.Label(self.side_lock_box, text="LOCK STATUS", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8, "bold"))
        self.side_lock_title.pack(pady=(6, 2))
        self.side_lock_lbl = tk.Label(self.side_lock_box, text="UNLOCKED", bg=PANEL_2, fg=SUCCESS, font=("Segoe UI", 10, "bold"))
        self.side_lock_lbl.pack(pady=(0, 6))

        # Bottom Sidebar Profile Widget (Champion XP)
        self.profile_box = tk.Frame(self.sidebar_frame, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER)
        self.profile_box.pack(side="bottom", fill="x", padx=14, pady=14)
        
        self.side_rank_lbl = tk.Label(self.profile_box, text="CHAMPION", bg=PANEL_2, fg=ACCENT, font=("Segoe UI", 9, "bold"))
        self.side_rank_lbl.pack(anchor="w", padx=10, pady=(6, 2))
        
        self.side_level_lbl = tk.Label(self.profile_box, text="Level 1", bg=PANEL_2, fg=TEXT, font=("Segoe UI", 8))
        self.side_level_lbl.pack(anchor="w", padx=10)
        
        self.side_xp_meter = Meter(self.profile_box, value=0, accent=ACCENT, height=6)
        self.side_xp_meter.pack(fill="x", padx=10, pady=(6, 4))
        self.side_xp_lbl = tk.Label(self.profile_box, text="0 / 100 XP", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 7))
        self.side_xp_lbl.pack(anchor="e", padx=10, pady=(0, 6))

        # 2. Right Side Content Area Container
        self.content_area = tk.Frame(self, bg=BG)
        self.content_area.pack(side="left", fill="both", expand=True)

        self._build_top_header()

        # Initialize Page Frames
        self.pages: dict[str, tk.Frame] = {}
        self.pages["dashboard"] = tk.Frame(self.content_area, bg=BG)
        self.pages["tasks"] = tk.Frame(self.content_area, bg=BG)
        self.pages["goals"] = tk.Frame(self.content_area, bg=BG)
        self.pages["habits"] = tk.Frame(self.content_area, bg=BG)
        self.pages["usage"] = tk.Frame(self.content_area, bg=BG)
        self.pages["store"] = tk.Frame(self.content_area, bg=BG)
        self.pages["coach"] = tk.Frame(self.content_area, bg=BG)
        self.pages["settings"] = tk.Frame(self.content_area, bg=BG)

        # Build each page skeleton
        self._build_dashboard_page()
        self._build_tasks_page()
        self._build_goals_page()
        self._build_habits_page()
        self._build_usage_page()
        self._build_store_page()
        self._build_coach_page()
        self._build_settings_page()

        # 3. Bottom Status Bar
        self._build_status_bar()

        # Pack initial page
        self.pages["dashboard"].pack(fill="both", expand=True, padx=12, pady=(6, 0))
        self.switch_page("dashboard")

    def _on_nav_leave(self, btn: tk.Button, key: str):
        if self.current_page == key:
            btn.config(bg=PANEL_2, fg=ACCENT)
        else:
            btn.config(bg=PANEL, fg=MUTED)

    def switch_page(self, key: str):
        if is_emergency_lock_active() and key not in ["dashboard", "coach", "settings"]:
            messagebox.showwarning("Emergency Lock", "Emergency Lock is active! Access to goals and tasks is blocked until countdown completes.")
            return

        self.pages[self.current_page].pack_forget()
        self.current_page = key
        self.pages[key].pack(fill="both", expand=True, padx=12, pady=(6, 0))

        # Reset button states
        for k, (stripe, btn) in self.nav_buttons.items():
            if k == key:
                stripe.config(bg=ACCENT)
                btn.config(bg=PANEL_2, fg=ACCENT)
            else:
                stripe.config(bg=PANEL)
                btn.config(bg=PANEL, fg=MUTED)

        self.refresh_all()

    # ── Top Digital Header ──────────────────────────────────────────────────

    def _build_top_header(self):
        self.header_panel = tk.Frame(self.content_area, bg=PANEL_2, highlightthickness=1, highlightbackground=CARD_BORDER)
        self.header_panel.pack(fill="x", padx=12, pady=(12, 4))
        
        # Digital Clock (Centered Top)
        clock_wrap = tk.Frame(self.header_panel, bg=PANEL_2)
        clock_wrap.pack(pady=8)
        
        self.clock_val = tk.Label(clock_wrap, text="12:00:00 AM", bg=PANEL_2, fg=ACCENT, font=("Segoe UI", 24, "bold"))
        self.clock_val.pack()
        self.date_val = tk.Label(clock_wrap, text="MAY 20, 2026 • WEDNESDAY", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 9, "bold"))
        self.date_val.pack()

        # Float indicators (Pills)
        pills_wrap = tk.Frame(self.header_panel, bg=PANEL_2)
        pills_wrap.pack(fill="x", padx=12, pady=(0, 8))

        self.admin_pill = StatusPill(pills_wrap, text="ADMIN" if is_admin() else "NO ADMIN", bg=SUCCESS if is_admin() else PANEL_3, fg="#061019" if is_admin() else DANGER)
        self.admin_pill.pack(side="right", padx=4)
        
        self.youtube_pill = StatusPill(pills_wrap, text="YT BLOCKED", bg=DANGER, fg="#061019")
        self.youtube_pill.pack(side="right", padx=4)
        
        self.incog_pill = StatusPill(pills_wrap, text="INCOG LOCK", bg=PANEL_3, fg=MUTED)
        self.incog_pill.pack(side="right", padx=4)
        
        self.lock_status_pill = StatusPill(pills_wrap, text="NORMAL MODE", bg=SUCCESS, fg="#061019")
        self.lock_status_pill.pack(side="left", padx=4)

    def _build_status_bar(self):
        self.status_bar = tk.Frame(self.content_area, bg=PANEL_2, highlightthickness=1, highlightbackground=CARD_BORDER, height=36)
        self.status_bar.pack(fill="x", side="bottom", padx=12, pady=(4, 12))
        self.status_bar.pack_propagate(False)

        self.status_text = tk.Label(self.status_bar, text="System initialized.", bg=PANEL_2, fg=MUTED, anchor="w", font=("Segoe UI", 9))
        self.status_text.pack(side="left", padx=16, fill="x", expand=True)

        GhostButton(self.status_bar, "Manual Recalc", command=self.sync_now, accent=ACCENT).pack(side="right", padx=8, pady=4)
        GhostButton(self.status_bar, "Sync Rules", command=self.refresh_all, accent=ACCENT_2).pack(side="right", padx=4, pady=4)

    # ── Page: Dashboard ────────────────────────────────────────────────────────

    def _build_dashboard_page(self):
        pad = tk.Frame(self.pages["dashboard"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        # ── Row 1: Four Stat Cards ──
        top = tk.Frame(pad, bg=BG)
        top.pack(fill="x", pady=(0, 8))
        for i in range(4):
            top.grid_columnconfigure(i, weight=1)

        self.card_completion = AnimatedStatCard(top, "Completion", "Today's Task progress", accent=ACCENT, width=10)
        self.card_score = AnimatedStatCard(top, "Discipline Score", "Accumulated XP ledger", accent=ACCENT_2, width=10)
        self.card_tasks = AnimatedStatCard(top, "Tasks Done", "Completed / Failed / Pending", accent=SUCCESS, width=10)
        self.card_streak = AnimatedStatCard(top, "Mastery Streak", "Consecutive days completed ≥ 70%", accent=WARNING, width=10)

        for i, card in enumerate([self.card_completion, self.card_score, self.card_tasks, self.card_streak]):
            card.grid(row=0, column=i, sticky="nsew", padx=(0, 6) if i < 3 else 0)

        # ── Row 2: Mission Control & System State ──
        mid = tk.Frame(pad, bg=BG)
        mid.pack(fill="both", expand=True, pady=(0, 8))
        mid.grid_columnconfigure(0, weight=3)
        mid.grid_columnconfigure(1, weight=2)
        mid.grid_rowconfigure(0, weight=1)

        hero = Card(mid, "Mission Control", "Complete commitments to unlock freedoms and earn discipline coins.", accent=ACCENT)
        hero.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        hero_body = tk.Frame(hero, bg=PANEL_2)
        hero_body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.progress_ring = CircularMeter(hero_body, size=180, thickness=12, accent=ACCENT, bg_color=PANEL_2)
        self.progress_ring.pack(side="left", padx=(0, 18))

        info = tk.Frame(hero_body, bg=PANEL_2)
        info.pack(side="left", fill="both", expand=True)

        tk.Label(info, text="Earned Access Threshold", bg=PANEL_2, fg=ACCENT, font=("Segoe UI", 12, "bold"), anchor="w").pack(anchor="w", pady=(8, 2))
        tk.Label(info, text="Maintain daily task completion above 70% to automatically unlock YouTube. Otherwise, buy temporary unlocks in the Reward Store.", bg=PANEL_2, fg=TEXT, font=("Segoe UI", 8), anchor="w", justify="left").pack(anchor="w")

        tk.Frame(info, bg=BORDER, height=1).pack(fill="x", pady=6)

        self.dash_task_bar = Meter(info, value=0, accent=ACCENT, height=12, label="Tasks Status")
        self.dash_task_bar.pack(fill="x", pady=(0, 2))
        self.dash_task_info = tk.Label(info, text="", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8), anchor="w", justify="left")
        self.dash_task_info.pack(anchor="w")

        # Emergency Lock Dashboard Controls
        tk.Frame(info, bg=BORDER, height=1).pack(fill="x", pady=6)
        
        lock_ctrl_frame = tk.Frame(info, bg=PANEL_2)
        lock_ctrl_frame.pack(fill="x")
        
        tk.Label(lock_ctrl_frame, text="Emergency Focus Lock:", bg=PANEL_2, fg=DANGER, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        
        self.dash_lock_dur = ttk.Combobox(lock_ctrl_frame, values=["1 Hour", "2 Hours", "4 Hours"], width=10, state="readonly")
        self.dash_lock_dur.set("1 Hour")
        self.dash_lock_dur.pack(side="left", padx=4)
        
        self.dash_lock_btn = HoverButton(lock_ctrl_frame, "ACTIVATE LOCK", command=self.activate_emergency_lock_dialog, bg_color=DANGER, fg_color=TEXT)
        self.dash_lock_btn.pack(side="left", padx=8)

        # System State Card (Right Column)
        state_card = Card(mid, "System State Analysis", "Locks, security logs, and registry status.", accent=ACCENT_2)
        state_card.grid(row=0, column=1, sticky="nsew")

        self.live_state = tk.Label(state_card, text="", bg=PANEL_2, fg=TEXT, justify="left", anchor="nw", font=("Consolas", 8))
        self.live_state.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # ── Row 3: Performance Trend & Activity Feed ──
        bottom = tk.Frame(pad, bg=BG)
        bottom.pack(fill="both", expand=True)
        bottom.grid_columnconfigure(0, weight=3)
        bottom.grid_columnconfigure(1, weight=2)
        bottom.grid_rowconfigure(0, weight=1)

        chart_card = Card(bottom, "Mastery Trend", "Completion scores and XP rollups.", accent=ACCENT_3)
        chart_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.chart_lbl = tk.Label(chart_card, bg=PANEL_2)
        self.chart_lbl.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        activity_card = Card(bottom, "Recent Activity Feed", "Latest transactions, completed goals, and triggers.", accent=ACCENT_2)
        activity_card.grid(row=0, column=1, sticky="nsew")
        self.activity_feed = ScrollFrame(activity_card, bg=PANEL_2, height=180)
        self.activity_feed.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    # ── Page: Daily Tasks ──────────────────────────────────────────────────────

    def _build_tasks_page(self):
        pad = tk.Frame(self.pages["tasks"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        toolbar = Card(pad, "Commitments List", "Log, manage, and execute tasks for today.", accent=ACCENT)
        toolbar.pack(fill="x", pady=(0, 8))

        form = tk.Frame(toolbar, bg=PANEL_2)
        form.pack(fill="x", padx=14, pady=(0, 12))
        form.grid_columnconfigure(0, weight=1)

        self.task_search = tk.StringVar()
        self.task_search.trace_add("write", lambda *_: self._refresh_tasks())

        tk.Label(form, text="Search tasks", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", pady=(0, 2))
        tk.Entry(form, textvariable=self.task_search, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        btn_frame = tk.Frame(form, bg=PANEL_2)
        btn_frame.grid(row=1, column=1, sticky="e")
        
        # Disabled during Emergency Lock
        self.btn_add_task = HoverButton(btn_frame, "+ Add task", command=self.add_task_dialog, bg_color=ACCENT, fg_color="#061019")
        self.btn_add_task.pack(side="left", padx=4)
        
        self.btn_mark_all = GhostButton(btn_frame, "✓ Mark all done", command=self.mark_all_done, accent=SUCCESS)
        self.btn_mark_all.pack(side="left", padx=4)
        
        GhostButton(btn_frame, "⟳ Sync rules", command=self.sync_now, accent=WARNING).pack(side="left", padx=4)

        self.task_meter = Meter(toolbar, value=0, accent=ACCENT, height=14, label="Completion Progress")
        self.task_meter.pack(fill="x", padx=14, pady=(4, 4))
        self.task_meter_info = tk.Label(toolbar, text="", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.task_meter_info.pack(fill="x", padx=14, pady=(0, 10))

        self.task_scroll = ScrollFrame(pad, bg=BG, height=560)
        self.task_scroll.pack(fill="both", expand=True)
        self.task_container = self.task_scroll.inner

    # ── Page: Goals & Projects ──────────────────────────────────────────────────

    def _build_goals_page(self):
        pad = tk.Frame(self.pages["goals"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        toolbar = Card(pad, "Long-term Goals & Projects", "Break long-term ambitions into structured milestone checklists.", accent=ACCENT_2)
        toolbar.pack(fill="x", pady=(0, 8))

        form = tk.Frame(toolbar, bg=PANEL_2)
        form.pack(fill="x", padx=14, pady=(0, 12))
        form.grid_columnconfigure(0, weight=1)

        self.goal_search = tk.StringVar()
        self.goal_search.trace_add("write", lambda *_: self._refresh_goals())

        tk.Label(form, text="Search goals", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", pady=(0, 2))
        tk.Entry(form, textvariable=self.goal_search, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        btn_frame = tk.Frame(form, bg=PANEL_2)
        btn_frame.grid(row=1, column=1, sticky="e")
        
        self.btn_add_goal = HoverButton(btn_frame, "+ Add goal", command=self.add_goal_dialog, bg_color=ACCENT_2, fg_color="#061019")
        self.btn_add_goal.pack(side="left", padx=4)
        
        GhostButton(btn_frame, "⟳ Check rewards", command=self.sync_now, accent=WARNING).pack(side="left", padx=4)
        GhostButton(btn_frame, "↓ Export CSV", command=self.export_csv, accent=ACCENT_3).pack(side="left", padx=4)

        self.goal_meter = Meter(toolbar, value=0, accent=ACCENT_2, height=14, label="Goal completion progress")
        self.goal_meter.pack(fill="x", padx=14, pady=(4, 4))
        self.goal_meter_info = tk.Label(toolbar, text="", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.goal_meter_info.pack(fill="x", padx=14, pady=(0, 10))

        self.goal_scroll = ScrollFrame(pad, bg=BG, height=560)
        self.goal_scroll.pack(fill="both", expand=True)
        self.goal_container = self.goal_scroll.inner

    # ── Page: Habit Tracker (New!) ───────────────────────────────────────────────

    def _build_habits_page(self):
        pad = tk.Frame(self.pages["habits"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        toolbar = Card(pad, "Habit Consistency Engine", "Track daily or weekly recurring self-improvement behaviors.", accent=SUCCESS)
        toolbar.pack(fill="x", pady=(0, 8))

        form = tk.Frame(toolbar, bg=PANEL_2)
        form.pack(fill="x", padx=14, pady=(0, 12))
        
        tk.Label(form, text="Habit Name:", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.habit_name_var = tk.StringVar()
        tk.Entry(form, textvariable=self.habit_name_var, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 10), width=28).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        tk.Label(form, text="Frequency:", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).grid(row=0, column=1, sticky="w", pady=(0, 2))
        self.habit_freq_var = ttk.Combobox(form, values=["daily", "weekly"], width=12, state="readonly")
        self.habit_freq_var.set("daily")
        self.habit_freq_var.grid(row=1, column=1, sticky="w", padx=(0, 8))

        HoverButton(form, "+ Create Habit", command=self.add_habit_ui, bg_color=SUCCESS, fg_color="#061019").grid(row=1, column=2, padx=4)

        self.habit_scroll = ScrollFrame(pad, bg=BG, height=560)
        self.habit_scroll.pack(fill="both", expand=True)
        self.habit_container = self.habit_scroll.inner

    # ── Page: App Usage Tracking (New!) ──────────────────────────────────────────

    def _build_usage_page(self):
        pad = tk.Frame(self.pages["usage"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        toolbar = Card(pad, "Application Time Analysis", "Identify time distribution and productivity killers.", accent=ACCENT_3)
        toolbar.pack(fill="x", pady=(0, 8))

        ctrl_frame = tk.Frame(toolbar, bg=PANEL_2)
        ctrl_frame.pack(fill="x", padx=14, pady=(0, 12))
        
        self.usage_filter = "today"
        self.btn_today_usage = HoverButton(ctrl_frame, "Today", command=lambda: self.set_usage_filter("today"), bg_color=ACCENT, fg_color="#061019")
        self.btn_today_usage.pack(side="left", padx=4)
        
        self.btn_week_usage = GhostButton(ctrl_frame, "Weekly Summary", command=lambda: self.set_usage_filter("week"), accent=ACCENT_2)
        self.btn_week_usage.pack(side="left", padx=4)

        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        # Left side usage list
        left = Card(body, "App Durations Log", "Time tracked per process.", accent=ACCENT_2)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.usage_scroll = ScrollFrame(left, bg=PANEL_2, height=440)
        self.usage_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Right side horizontal bar chart
        right = Card(body, "Graphical Analysis", "Top app categories by percentage.", accent=ACCENT_3)
        right.grid(row=0, column=1, sticky="nsew")
        
        self.usage_chart = AppUsageChart(right, width=420, height=360, bg_color=PANEL_2)
        self.usage_chart.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    # ── Page: Reward Store (New!) ────────────────────────────────────────────────

    def _build_store_page(self):
        pad = tk.Frame(self.pages["store"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        toolbar = Card(pad, "Discipline Reward Store", "Spend hard-earned Discipline Points (DP) on temporary distraction unlocks.", accent=WARNING)
        toolbar.pack(fill="x", pady=(0, 8))

        balance_frame = tk.Frame(toolbar, bg=PANEL_2)
        balance_frame.pack(fill="x", padx=14, pady=(0, 12))
        
        self.store_points_lbl = tk.Label(balance_frame, text="AVAILABLE BALANCE: 0 DP", bg=PANEL_2, fg=WARNING, font=("Segoe UI", 14, "bold"))
        self.store_points_lbl.pack(side="left")

        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)

        # Shop inventory cards
        left = Card(body, "Available Rewards", "Unlock freedom with discipline.", accent=ACCENT)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.store_item_container = tk.Frame(left, bg=PANEL_2)
        self.store_item_container.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Right sidebar for Custom Rewards Creation + History
        right_panel = tk.Frame(body, bg=BG)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_rowconfigure(0, weight=1)
        right_panel.grid_rowconfigure(1, weight=1)

        custom_box = Card(right_panel, "Add Custom Reward", "Define personalized incentives.", accent=ACCENT_2)
        custom_box.pack(fill="x", pady=(0, 8))
        
        form = tk.Frame(custom_box, bg=PANEL_2)
        form.pack(fill="x", padx=14, pady=(0, 12))
        
        tk.Label(form, text="Reward Description:", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.reward_desc_var = tk.StringVar()
        tk.Entry(form, textvariable=self.reward_desc_var, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 10), width=28).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        tk.Label(form, text="Cost (DP):", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).grid(row=0, column=1, sticky="w", pady=(0, 2))
        self.reward_cost_var = tk.StringVar()
        tk.Entry(form, textvariable=self.reward_cost_var, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 10), width=6).grid(row=1, column=1, sticky="w", padx=(0, 8))

        HoverButton(form, "+ Add Item", command=self.add_custom_reward_ui, bg_color=ACCENT_2, fg_color="#061019").grid(row=1, column=2, padx=4)

        history_box = Card(right_panel, "Purchase Ledger", "Recent checkout history.", accent=MUTED)
        history_box.pack(fill="both", expand=True)
        
        self.store_history_scroll = ScrollFrame(history_box, bg=PANEL_2, height=180)
        self.store_history_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    # ── Page: AI Coach Page (New!) ───────────────────────────────────────────────

    def _build_coach_page(self):
        pad = tk.Frame(self.pages["coach"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        toolbar = Card(pad, "Discipline Mentor & AI Coach", "Statistical insights and personalized coaching feedback.", accent=ACCENT)
        toolbar.pack(fill="x", pady=(0, 8))

        body = tk.Frame(pad, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        # Insights Panel (Strengths, Weaknesses, Recommended Actions)
        left = Card(body, "Productivity Diagnostics", "Automated statistical insights based on your database.", accent=ACCENT_2)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        
        left_scroll = ScrollFrame(left, bg=PANEL_2, height=440)
        left_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.coach_diagnostics_lbl = tk.Label(left_scroll.inner, text="Analyzing statistics...", bg=PANEL_2, fg=TEXT, font=("Segoe UI", 10), justify="left", anchor="nw")
        self.coach_diagnostics_lbl.pack(fill="both", expand=True, padx=8, pady=8)

        # Interactive Chatbot box
        right = Card(body, "Interactive Consultation", "Ask Coach Steeper for guidance.", accent=ACCENT)
        right.grid(row=0, column=1, sticky="nsew")

        chat_frame = tk.Frame(right, bg=PANEL_2)
        chat_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.chat_history = tk.Text(chat_frame, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 9), state="disabled", wrap="word")
        self.chat_history.pack(fill="both", expand=True, pady=(0, 8))

        input_row = tk.Frame(chat_frame, bg=PANEL_2)
        input_row.pack(fill="x")

        self.chat_input_var = tk.StringVar()
        self.chat_entry = tk.Entry(input_row, textvariable=self.chat_input_var, bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT, relief="flat", font=("Segoe UI", 10))
        self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)
        self.chat_entry.bind("<Return>", lambda _: self.ask_ai_coach())

        HoverButton(input_row, "ASK COACH", command=self.ask_ai_coach, bg_color=ACCENT, fg_color="#061019").pack(side="right")

        # Initial Welcome Message in Chat
        self.append_coach_chat("Coach Steeper", "Welcome back! I am your AI Coach. I analyze your tasks, streaks, habits, and application usage patterns daily. Ask me anything about your productivity, how to improve your strengths, or get a personalized report!")

    def append_coach_chat(self, sender: str, text: str):
        self.chat_history.config(state="normal")
        time_str = datetime.now().strftime("%H:%M")
        self.chat_history.insert("end", f"[{time_str}] {sender}: {text}\n\n")
        self.chat_history.see("end")
        self.chat_history.config(state="disabled")

    # ── Page: Settings & Logs ──────────────────────────────────────────────────

    def _build_settings_page(self):
        pad = tk.Frame(self.pages["settings"], bg=BG)
        pad.pack(fill="both", expand=True, pady=8)

        top = Card(pad, "Settings & Tools", "Quick system triggers and logs shortcuts.", accent=ACCENT_3)
        top.pack(fill="x", pady=(0, 8))
        
        control = tk.Frame(top, bg=PANEL_2)
        control.pack(fill="x", padx=14, pady=(0, 12))
        GhostButton(control, "Open Data Folder", command=self.open_data_folder, accent=ACCENT).pack(side="left", padx=4)
        GhostButton(control, "Open Logs Folder", command=self.open_logs_folder, accent=ACCENT_2).pack(side="left", padx=4)
        GhostButton(control, "Reset Launch Count", command=self.reset_launch_count, accent=WARNING).pack(side="left", padx=4)
        GhostButton(control, "Export Month CSV", command=self.export_csv, accent=ACCENT_3).pack(side="left", padx=4)
        GhostButton(control, "About UpSteeper", command=self.show_about, accent=MUTED).pack(side="left", padx=4)

        info_card = Card(pad, "Runtime Diagnostics", "Build integrity and administrator parameters.", accent=ACCENT_2)
        info_card.pack(fill="x", pady=(0, 8))
        self.settings_text = tk.Label(info_card, bg=PANEL_2, fg=TEXT, justify="left", anchor="nw", font=("Consolas", 9))
        self.settings_text.pack(fill="x", padx=14, pady=(0, 14))

        log_card = Card(pad, "System Event Log", "Activity logs ledger.", accent=MUTED)
        log_card.pack(fill="both", expand=True)
        self.log_feed = ScrollFrame(log_card, bg=PANEL_2, height=300)
        self.log_feed.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    # ── Logo loading ───────────────────────────────────────────────────────────

    def _load_logo(self):
        self._logo_photo = None
        if PIL_AVAILABLE:
            if LOGO_PATH.exists():
                try:
                    img = Image.open(LOGO_PATH).convert("RGBA").resize((72, 72), Image.Resampling.LANCZOS)
                    self._logo_photo = ImageTk.PhotoImage(img)
                except Exception:
                    pass
            if self._logo_photo is None:
                try:
                    self._logo_photo = _make_logo(72)
                except Exception:
                    pass
        if self._logo_photo:
            self.logo_lbl.config(image=self._logo_photo)
            try:
                self.iconphoto(False, self._logo_photo)
            except Exception:
                pass

    # ── Scheduler & polling ────────────────────────────────────────────────────

    def _scheduler_callback(self, message: SchedulerMessage):
        self._queue.put(message)

    def _poll_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                self.set_status(msg.text)
                send_toast_notification("UpSteeper Sync", msg.text)
                self.refresh_all(silent=True)
        except queue.Empty:
            pass
        self.after(250, self._poll_queue)

    def _tick_clock(self):
        # Center clock date & time
        self.clock_val.config(text=datetime.now().strftime("%I:%M:%S %p"))
        self.date_val.config(text=datetime.now().strftime("%B %d, %Y • %A").upper())
        
        # Monitor Emergency Focus lock countdown in UI
        raw_lock = get_setting("emergency_lock_until", "")
        if raw_lock:
            try:
                until = datetime.fromisoformat(raw_lock)
                now = datetime.now()
                if now < until:
                    diff = until - now
                    # Format diff as HH:MM:SS
                    secs = int(diff.total_seconds())
                    hrs = secs // 3600
                    mins = (secs % 3600) // 60
                    seconds = secs % 60
                    timer_str = f"LOCK ACTIVE ({hrs:02d}:{mins:02d}:{seconds:02d})"
                    
                    self.side_lock_lbl.config(text=f"{hrs:02d}:{mins:02d}:{seconds:02d}", fg=DANGER)
                    self.side_lock_title.config(text="EMERGENCY LOCK", fg=DANGER)
                    self.lock_status_pill.set(timer_str, bg=DANGER, fg=TEXT)
                    
                    # Update status button availability
                    self._set_ui_lock_state("disabled")
                else:
                    # Timer expired, unblock
                    set_setting("emergency_lock_until", "")
                    self.side_lock_lbl.config(text="UNLOCKED", fg=SUCCESS)
                    self.side_lock_title.config(text="LOCK STATUS", fg=MUTED)
                    self.lock_status_pill.set("NORMAL MODE", bg=SUCCESS, fg="#061019")
                    self._set_ui_lock_state("normal")
                    self.sync_now(silent=True)
                    send_toast_notification("Focus Unlocked", "Emergency Focus Lock period has expired. You are free!")
            except Exception:
                pass
        else:
            self.side_lock_lbl.config(text="UNLOCKED", fg=SUCCESS)
            self.side_lock_title.config(text="LOCK STATUS", fg=MUTED)
            self.lock_status_pill.set("NORMAL MODE", bg=SUCCESS, fg="#061019")
            self._set_ui_lock_state("normal")
            
        self.after(1000, self._tick_clock)

    def _set_ui_lock_state(self, state: str):
        self.dash_lock_dur.config(state="disabled" if state == "disabled" else "readonly")
        self.dash_lock_btn.config(state=state)
        self.btn_add_task.config(state=state)
        self.btn_mark_all.config(state=state)
        self.btn_add_goal.config(state=state)

    def _periodic_sync(self):
        try:
            self.sync_now(silent=True)
        finally:
            self.after(60_000, self._periodic_sync)

    def _on_close(self):
        try:
            self._scheduler.stop()
        except Exception:
            pass
        self.destroy()

    # ── Status & sync ─────────────────────────────────────────────────────────

    def set_status(self, text: str):
        if hasattr(self, "status_text") and self.status_text:
            try:
                self.status_text.config(text=text)
            except Exception:
                pass

    def sync_now(self, silent: bool = False):
        state = manual_recalculate()
        if not silent:
            self.set_status(state["daily"]["block_message"])
        self.refresh_all()

    def refresh_all(self, silent: bool = False):
        rebuild_rollup(today_iso())
        self._refresh_tasks()
        self._refresh_goals()
        self._refresh_habits()
        self._refresh_usage()
        self._refresh_store()
        self._refresh_dashboard()
        self._refresh_settings()
        self._refresh_coach()
        self._refresh_chart_async()
        if not silent:
            self.set_status("Interface refreshed.")

    # ── Refresh Helpers ────────────────────────────────────────────────────────

    def _refresh_dashboard(self):
        stats   = task_stats()
        rollup  = rebuild_rollup()
        goals   = goal_stats()
        summary = monthly_summary()

        self.card_completion.set_value(stats["completion"], suffix="%")
        self.card_score.set_value(rollup["score_points"])
        self.card_tasks.set_value(stats["done"],
                                   suffix=f" / {stats['failed']} / {stats['pending']}")
        self.card_streak.set_value(summary["current_streak"])
        self.progress_ring.set(stats["completion"])

        self.dash_task_bar.set(stats["completion"])
        self.dash_task_info.config(
            text=f"{stats['done']} done  •  {stats['failed']} failed  •  {stats['pending']} pending  •  XP +{rollup['score_points']}")
        
        self.live_state.config(text=self._live_state_text(stats, rollup, summary))

        yt = get_setting("youtube_blocked", "1") == "1"
        self.youtube_pill.set("YT  BLOCKED" if yt else "YT  OPEN",
                               bg=DANGER if yt else SUCCESS, fg="#061019")
        until = get_setting("incognito_enabled_until", "")
        self.incog_pill.set("INCOG  ON" if until else "INCOG  OFF",
                             bg=SUCCESS if until else PANEL_3,
                             fg="#061019" if until else MUTED)

        # Side bar progress XP bar update
        coach = get_ai_coach_insights()
        self.side_rank_lbl.config(text=coach["rank"].upper())
        self.side_level_lbl.config(text=f"Level {coach['level']}")
        self.side_xp_meter.set(coach["xp_current"])
        self.side_xp_lbl.config(text=f"{coach['xp_current']} / 100 XP")

        # Activity feed
        self._clear_frame(self.activity_feed.inner)
        for evt in recent_events(15):
            row = tk.Frame(self.activity_feed.inner, bg=PANEL_2,
                            highlightthickness=1, highlightbackground=CARD_BORDER)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"  {evt['event_type']}", bg=PANEL_2, fg=ACCENT,
                      font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left", padx=4, pady=4)
            tk.Label(row, text=evt["created_at"][-8:], bg=PANEL_2, fg=MUTED,
                      font=("Consolas", 7), anchor="e").pack(side="right", padx=6, pady=4)

    def _live_state_text(self, stats, rollup, summary) -> str:
        # Check active settings
        yt_raw = get_setting("youtube_unblocked_until", "")
        yt_remain = "Blocked"
        if yt_raw:
            try:
                until = datetime.fromisoformat(yt_raw)
                if datetime.now() < until:
                    diff = until - datetime.now()
                    yt_remain = f"Store Unlocked ({int(diff.total_seconds()//60)} mins left)"
            except Exception:
                pass
        if yt_remain == "Blocked" and stats["completion"] >= 70:
            yt_remain = "Unlocked via Tasks"
            
        incog_raw = get_setting("incognito_enabled_until", "")
        incog_remain = "Locked"
        if incog_raw:
            try:
                until = datetime.fromisoformat(incog_raw)
                if datetime.now() < until:
                    diff = until - datetime.now()
                    incog_remain = f"Unlocked ({int(diff.total_seconds()//3600)} hours left)"
            except Exception:
                pass
                
        return "\n".join([
            f"Active Day:      {today_iso()}",
            f"Completion:     {stats['completion']}%",
            f"YouTube Access: {yt_remain}",
            f"Incognito Mode: {incog_remain}",
            f"XP Balance:     {get_discipline_points_balance()} DP",
            f"Streak:         {summary['current_streak']} days",
            f"Active Window:  {self._get_active_app_label()}",
            f"Admin Status:   {'Elevated' if is_admin() else 'Restricted'}",
        ])

    def _get_active_app_label(self) -> str:
        if sys.platform != "win32":
            return "N/A"
        from .scheduler import get_active_window_details
        proc, _ = get_active_window_details()
        return proc if proc else "Idle"

    def _clear_frame(self, frame: tk.Frame):
        for w in frame.winfo_children():
            w.destroy()

    def _refresh_tasks(self):
        self._task_rows.clear()
        self._clear_frame(self.task_container)
        q     = self.task_search.get().strip().lower()
        tasks = list_tasks(today_iso(), query=q)
        for row in tasks:
            data   = dict(row)
            widget = TaskRow(self.task_container, data,
                              self.update_task_status_ui,
                              self.edit_task_dialog,
                              self.delete_task_ui)
            widget.pack(fill="x", pady=3)
            self._task_rows[data["id"]] = widget
        if not self._task_rows:
            tk.Label(self.task_container,
                      text="No tasks commitments logged for today.",
                      bg=BG, fg=MUTED, font=("Segoe UI", 10)
                      ).pack(anchor="w", padx=14, pady=20)
        if hasattr(self, "task_meter"):
            s = task_stats()
            self.task_meter.set(s["completion"])
            self.task_meter_info.config(
                text=f"{s['done']} completed  •  {s['failed']} failed  •  {s['pending']} pending")

    def _refresh_goals(self):
        # Clear existing widgets references
        self._goal_widgets.clear()
        self._clear_frame(self.goal_container)
        q = self.goal_search.get().strip().lower()
        goals = list_goals(query=q)
        for row in goals:
            data = dict(row)
            widget = GoalCardWidget(self.goal_container, data, self)
            widget.pack(fill="x", pady=6)
            self._goal_widgets.append(widget)
        if not self._goal_widgets:
            tk.Label(self.goal_container,
                      text="No long-term goals or projects configured yet.",
                      bg=BG, fg=MUTED, font=("Segoe UI", 10)
                      ).pack(anchor="w", padx=14, pady=20)
        if hasattr(self, "goal_meter"):
            gs  = goal_stats()
            pct = (gs["complete"] / gs["total"] * 100) if gs["total"] else 0.0
            self.goal_meter.set(pct)
            self.goal_meter_info.config(
                text=f"{gs['complete']} projects completed  •  {gs['active']} active")

    # ── Habit Tracker UI (New!) ───────────────────────────────────────────────

    def _refresh_habits(self):
        self._clear_frame(self.habit_container)
        habits = list_habits()
        
        for h in habits:
            row = tk.Frame(self.habit_container, bg=PANEL_2, highlightthickness=1, highlightbackground=CARD_BORDER)
            row.pack(fill="x", pady=4)
            
            # Left stripe color
            stripe = tk.Frame(row, width=4, bg=SUCCESS)
            stripe.pack(side="left", fill="y", padx=(0, 10))
            
            # Toggle checkbutton for completion today
            history = get_habit_history(h["id"])
            completed_today = today_iso() in history
            
            var = tk.BooleanVar(value=completed_today)
            
            chk = tk.Checkbutton(
                row, variable=var, bg=PANEL_2, activebackground=PANEL_2,
                selectcolor=INPUT_BG, highlightthickness=0, bd=0,
                command=lambda h_id=h["id"], v=var: self.toggle_habit_completion(h_id, v.get())
            )
            chk.pack(side="left", padx=10)
            
            # Label
            lbl_font = ("Segoe UI", 10, "bold" if not completed_today else "overstrike")
            lbl_color = TEXT if not completed_today else MUTED
            lbl_txt = f"{h['name']} ({h['frequency'].upper()})"
            tk.Label(row, text=lbl_txt, bg=PANEL_2, fg=lbl_color, font=lbl_font, anchor="w").pack(side="left", fill="x", expand=True)
            
            # Streak count fire symbol
            streak_txt = f"🔥 {h['streak_count']} streak" if h["streak_count"] > 0 else "💤 no streak"
            tk.Label(row, text=streak_txt, bg=PANEL_2, fg=WARNING, font=("Segoe UI", 9, "bold")).pack(side="left", padx=12)
            
            # Heatmap grid Canvas (Past 30 days completions)
            heatmap = HeatmapCanvas(row, bg_color=PANEL_2)
            heatmap.pack(side="left", padx=12, pady=6)
            heatmap.draw_heatmap(history)
            
            # Action controls
            GhostButton(row, "Delete", command=lambda h_id=h["id"]: self.delete_habit_ui(h_id), accent=DANGER).pack(side="right", padx=10)
            
        if not habits:
            tk.Label(self.habit_container, text="No habits added. Create a habit to start tracking consistency.", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=20)

    def add_habit_ui(self):
        name = self.habit_name_var.get().strip()
        freq = self.habit_freq_var.get()
        if not name:
            messagebox.showwarning(APP_NAME, "Habit description cannot be empty.")
            return
        try:
            add_habit(name, freq)
            self.habit_name_var.set("")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def toggle_habit_completion(self, habit_id: int, completed: bool):
        try:
            if completed:
                complete_habit(habit_id)
                send_toast_notification("Habit Checked", "Consistency score +1 DP!")
            else:
                uncomplete_habit(habit_id)
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def delete_habit_ui(self, habit_id: int):
        if messagebox.askyesno(APP_NAME, "Remove this habit behavior?"):
            try:
                delete_habit(habit_id)
                self.refresh_all()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

    # ── App Usage UI (New!) ────────────────────────────────────────────────────

    def set_usage_filter(self, val: str):
        self.usage_filter = val
        if val == "today":
            self.btn_today_usage.config(bg=ACCENT, fg="#061019")
            self.btn_today_usage._base = ACCENT
            self.btn_week_usage.config(bg=PANEL_3, fg=ACCENT_2)
        else:
            self.btn_today_usage.config(bg=PANEL_3, fg=ACCENT)
            self.btn_week_usage.config(bg=ACCENT_2, fg="#061019")
            self.btn_week_usage._base = ACCENT_2
        self._refresh_usage()

    def _refresh_usage(self):
        # Clear usage list
        self._clear_frame(self.usage_scroll.inner)
        
        # Retrieve data based on filter
        if self.usage_filter == "today":
            data = [dict(r) for r in get_app_usage(today_iso())]
        else:
            # Past 7 days
            start = (date.today() - timedelta(days=6)).isoformat()
            end = today_iso()
            data = [dict(r) for r in get_app_usage_summary(start, end)]
            # Map column names
            for item in data:
                item["duration_seconds"] = item["total_duration"]
                
        # Draw chart
        self.usage_chart.draw_chart(data)
        
        # Display list
        for item in data:
            row = tk.Frame(self.usage_scroll.inner, bg=PANEL_2, highlightthickness=1, highlightbackground=CARD_BORDER)
            row.pack(fill="x", pady=2)
            
            sec = item["duration_seconds"]
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            dur_str = f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"
            
            tk.Label(row, text=f"  {item['app_name']}", bg=PANEL_2, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(side="left", pady=6)
            tk.Label(row, text=f"{dur_str}  ", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")
            
        if not data:
            tk.Label(self.usage_scroll.inner, text="No app time records logged.", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=14)

    # ── Reward Store UI (New!) ──────────────────────────────────────────────────

    def _refresh_store(self):
        # Update Balance indicator
        balance = get_discipline_points_balance()
        self.store_points_lbl.config(text=f"AVAILABLE BALANCE: {balance} DP")
        
        # Clear Inventory
        self._clear_frame(self.store_item_container)
        
        # Add Predefined Rewards
        predefined = [
            ("YouTube Access (1 Hour)", 5, "Unblocks YouTube hosts file rules for 1 hour."),
            ("YouTube Access (3 Hours)", 12, "Unblocks YouTube hosts file rules for 3 hours."),
            ("Incognito Access (24 Hours)", 20, "Re-enables Browser Incognito/Private Mode availability for 24 hours."),
        ]
        
        for name, cost, desc in predefined:
            self._draw_store_card(name, cost, desc, is_custom=False)
            
        # Add Custom Rewards from Database
        custom_items = list_custom_rewards()
        for item in custom_items:
            self._draw_store_card(item["name"], item["cost"], "User defined motivation reward.", is_custom=True, reward_id=item["id"])
            
        # Refresh Purchases ledger
        self._clear_frame(self.store_history_scroll.inner)
        purchases = list_purchased_rewards()
        for p in purchases:
            row = tk.Frame(self.store_history_scroll.inner, bg=PANEL_2)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"  {p['created_at'][-8:]}", bg=PANEL_2, fg=MUTED, font=("Consolas", 8), width=10, anchor="w").pack(side="left")
            tk.Label(row, text=p["reward_name"], bg=PANEL_2, fg=TEXT, font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left", padx=8)
            tk.Label(row, text=f"-{p['points_spent']} DP  ", bg=PANEL_2, fg=DANGER, font=("Segoe UI", 8, "bold")).pack(side="right")
        if not purchases:
            tk.Label(self.store_history_scroll.inner, text="No transactions logged.", bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=14, pady=10)

    def _draw_store_card(self, name: str, cost: int, description: str, is_custom: bool, reward_id: int | None = None):
        card = tk.Frame(self.store_item_container, bg=PANEL_2, highlightthickness=1, highlightbackground=CARD_BORDER)
        card.pack(fill="x", pady=3)
        
        # Cost badge
        badge = tk.Label(card, text=f"{cost} DP", bg=WARNING, fg="#061019", font=("Segoe UI", 9, "bold"), padx=6, pady=2)
        badge.pack(side="left", padx=10, pady=8)
        
        # Details
        info = tk.Frame(card, bg=PANEL_2)
        info.pack(side="left", fill="both", expand=True, pady=4)
        
        tk.Label(info, text=name, bg=PANEL_2, fg=TEXT, font=("Segoe UI", 9, "bold"), anchor="w").pack(anchor="w")
        tk.Label(info, text=description, bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(anchor="w")
        
        # Buy button
        balance = get_discipline_points_balance()
        btn_state = "normal" if balance >= cost else "disabled"
        
        btn = HoverButton(card, "PURCHASE", command=lambda: self.purchase_store_item(name, cost), bg_color=SUCCESS, fg_color="#061019")
        btn.config(state=btn_state)
        btn.pack(side="right", padx=10)
        
        # Delete custom item
        if is_custom and reward_id is not None:
            GhostButton(card, "🗑️", command=lambda r_id=reward_id: self.delete_custom_reward_ui(r_id), accent=DANGER).pack(side="right", padx=(0, 4))

    def purchase_store_item(self, name: str, cost: int):
        if not messagebox.askyesno(APP_NAME, f"Deduct {cost} DP to purchase: {name}?"):
            return
            
        success = purchase_reward(name, cost)
        if not success:
            messagebox.showerror(APP_NAME, "Insufficient points balance!")
            return
            
        # Trigger actual system triggers based on shop purchases
        if "YouTube Access" in name:
            hours = 3 if "3 Hours" in name else 1
            expiry = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
            set_setting("youtube_unblocked_until", expiry)
            log_event("store_youtube_unlocked", f"duration={hours}h;until={expiry}")
            send_toast_notification("Reward Unlocked", f"YouTube access unblocked for {hours} hour(s)!")
        elif "Incognito Access" in name:
            expiry = (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")
            set_setting("incognito_enabled_until", expiry)
            enable_incognito_for_24h()
            log_event("store_incognito_unlocked", f"duration=24h;until={expiry}")
            send_toast_notification("Reward Unlocked", "Incognito mode allowed for 24 hours!")
        else:
            # Custom reward checkout confirmation
            messagebox.showinfo(APP_NAME, f"Purchase Successful!\nYou have successfully claimed: {name}\nSpend it wisely!")
            
        self.sync_now(silent=True)

    def add_custom_reward_ui(self):
        name = self.reward_desc_var.get().strip()
        cost_raw = self.reward_cost_var.get().strip()
        if not name or not cost_raw:
            messagebox.showwarning(APP_NAME, "Please fill in description and cost.")
            return
        try:
            cost = int(cost_raw)
            add_custom_reward(name, cost)
            self.reward_desc_var.set("")
            self.reward_cost_var.set("")
            self.refresh_all()
        except ValueError:
            messagebox.showerror(APP_NAME, "Cost must be a valid integer.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def delete_custom_reward_ui(self, reward_id: int):
        try:
            delete_custom_reward(reward_id)
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    # ── Page: AI Coach Page (New!) ───────────────────────────────────────────────

    def _refresh_coach(self):
        coach = get_ai_coach_insights()
        
        # Diagnostic display
        text = [
            f"⚡ Productivity Diagnosis Score: {coach['discipline_score']}%",
            f"🏆 Current Discipline Rank: {coach['rank']} (Level {coach['level']})",
            "",
            "💪 Behavior Strengths:",
        ]
        for s in coach["strengths"]:
            text.append(f"  • {s}")
        if not coach["strengths"]:
            text.append("  • None identified yet. Build logs.")
            
        text.append("")
        text.append("⚠️ Behavioral Weaknesses:")
        for w in coach["weaknesses"]:
            text.append(f"  • {w}")
        if not coach["weaknesses"]:
            text.append("  • None detected. Exceptional focus!")
            
        text.append("")
        text.append("💬 Coach Recommendations & Next Actions:")
        for tip in coach["tips"]:
            text.append(f"  • {tip}")
            
        self.coach_diagnostics_lbl.config(text="\n".join(text))

    def ask_ai_coach(self):
        q = self.chat_input_var.get().strip()
        if not q:
            return
            
        self.append_coach_chat("You", q)
        self.chat_input_var.set("")
        
        # Defer reply slightly to make it feel natural
        self.after(500, lambda: self._generate_coach_reply(q))

    def _generate_coach_reply(self, query: str):
        q = query.lower()
        coach = get_ai_coach_insights()
        
        # Check keyword matches to personalize response
        if "streak" in q:
            reply = f"Your current streak calculation is linked to daily task completions >= 70%. Currently, you are on a streak. Consistency builds identity!"
        elif "youtube" in q or "distraction" in q:
            reply = f"According to app tracking logs, your main distraction is {coach['weaknesses'][0] if coach['weaknesses'] else 'Browser activities'}. Consider buying 1 hour of access in the Reward Store or toggling Emergency Lock Mode if you need immediate deep focus."
        elif "focus" in q or "improve" in q:
            reply = f"To improve focus, I recommend: 1. Break tasks into tiny actions. 2. Lock distracting applications with the Emergency Lock. 3. Target completing tasks before noon."
        elif "points" in q or "dp" in q:
            reply = f"You earn Discipline Points (DP) by checking off daily tasks, completing goals, or completing habits. You can spend points in the Reward Store. Keep stackin' DP!"
        elif "status" in q or "report" in q or "diagnose" in q:
            reply = f"Diagnostics Summary: Discipline score is {coach['discipline_score']}%. Current rank: {coach['rank']}. Strengths count: {len(coach['strengths'])}."
        else:
            reply = "I understand. The key to long-term discipline is regular daily check-ins. Try setting up some tiny milestone goals and complete them today!"
            
        self.append_coach_chat("Coach Steeper", reply)

    # ── Page: Settings & Logs ──────────────────────────────────────────────────

    def _refresh_settings(self):
        summary = monthly_summary()
        from .config import DB_PATH
        self.settings_text.config(text=(
            f"App Version:      {VERSION}\n"
            f"Launch Counter:   {current_launch_count()}\n"
            f"Database File:    {DB_PATH}\n"
            f"Elevated Status:  {'Yes (Admin)' if is_admin() else 'No (Standard Mode)'}\n"
            f"Monthly Rollups:  {summary['score_points']} XP earned\n"
            f"Completed Goals:  {summary['goal_completions']} projects\n"
            f"Rule Sync Mode:   Auto blocking is active\n"
        ))
        self._refresh_logs()

    def _refresh_logs(self):
        self._clear_frame(self.log_feed.inner)
        for evt in recent_events(30):
            row = tk.Frame(self.log_feed.inner, bg=PANEL_2)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"  {evt['created_at']}", bg=PANEL_2, fg=MUTED,
                      font=("Consolas", 8), anchor="w", width=22).pack(side="left")
            tk.Label(row, text=evt["event_type"], bg=PANEL_2, fg=ACCENT,
                      font=("Segoe UI", 8, "bold"), anchor="w", width=22).pack(side="left", padx=8)
            tk.Label(row, text=evt["payload"] or "—", bg=PANEL_2, fg=TEXT,
                      font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True)

    # ── Asynchronous Chart Builder ───────────────────────────────────────────────

    def _refresh_chart_async(self):
        # Build Matplotlib chart in a background thread to prevent UI freezing
        t = threading.Thread(target=self._run_chart_generation, daemon=True)
        t.start()

    def _run_chart_generation(self):
        try:
            chart = build_monthly_chart()
            if PIL_AVAILABLE:
                img = Image.open(chart).convert("RGBA").resize((580, 290), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                # Apply photo safely in the main thread
                self.after(0, lambda: self._apply_chart_photo(photo))
            else:
                self.after(0, lambda: self.chart_lbl.config(text="Install Pillow to view charts.", fg=MUTED))
        except Exception:
            self.after(0, lambda: self.chart_lbl.config(image="", text="Chart building failed", fg=MUTED))

    def _apply_chart_photo(self, photo):
        self._chart_photo = photo
        self.chart_lbl.config(image=self._chart_photo, text="")

    # ── Emergency Lock Functions ───────────────────────────────────────────────

    def activate_emergency_lock_dialog(self):
        # Ask for confirmation
        dur_str = self.dash_lock_dur.get()
        hours_map = {"1 Hour": 1, "2 Hours": 2, "4 Hours": 4}
        hours = hours_map.get(dur_str, 1)
        
        if not messagebox.askyesno("ACTIVATE EMERGENCY LOCK", 
                                  f"WARNING:\nThis will lock YouTube and block focus killer sites ({', '.join(EMERGENCY_BLOCKED_SITES[:4])}).\n"
                                  f"It will forcefully close prohibited apps like Steam, Discord, and games.\n"
                                  f"You CANNOT disable this lock for {dur_str}.\n"
                                  f"Do you want to proceed?"):
            return
            
        # Set database setting
        expiry = (datetime.now() + timedelta(hours=hours)).isoformat()
        set_setting("emergency_lock_until", expiry)
        log_event("emergency_lock_started", f"duration={hours}h;until={expiry}")
        
        # Trigger rules sync
        self.sync_now()
        send_toast_notification("Focus Core Lock Active", f"Emergency Lock has been activated for {dur_str}. Stay focused!")

    # ── Task actions ───────────────────────────────────────────────────────────

    def add_task_dialog(self):
        if is_emergency_lock_active():
            return
        dlg = TaskDialog(self, "Add task commitment")
        if dlg.result and dlg.result["title"]:
            try:
                add_task(**dlg.result)
                self.set_status("Task committed.")
                self.refresh_all()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def edit_task_dialog(self, task_id: int):
        if is_emergency_lock_active():
            return
        task = next((dict(r) for r in list_tasks() if int(r["id"]) == int(task_id)), None)
        if not task:
            return
        dlg = TaskDialog(self, "Edit task", task)
        if dlg.result and dlg.result["title"]:
            try:
                edit_task(task_id, dlg.result["title"], dlg.result["category"],
                          dlg.result["notes"], dlg.result["priority"])
                self.refresh_all()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def update_task_status_ui(self, task_id: int, status: str):
        try:
            update_task_status(task_id, status)
            self.sync_now(silent=True)
            self.refresh_all(silent=True)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def delete_task_ui(self, task_id: int):
        if is_emergency_lock_active():
            return
        if messagebox.askyesno(APP_NAME, "Delete this task commitment?"):
            delete_task(task_id)
            self.refresh_all()

    def mark_all_done(self):
        if is_emergency_lock_active():
            return
        for row in list_tasks(today_iso()):
            update_task_status(int(row["id"]), "done")
        self.sync_now()
        self.set_status("All tasks marked completed.")

    # ── Goal actions ───────────────────────────────────────────────────────────

    def add_goal_dialog(self):
        if is_emergency_lock_active():
            return
        dlg = GoalDialog(self, "Add long-term goal")
        if dlg.result and dlg.result["title"]:
            try:
                add_goal(**dlg.result)
                self.set_status("Long-term goal added.")
                self.refresh_all()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def edit_goal_dialog(self, goal_id: int):
        if is_emergency_lock_active():
            return
        goal = next((dict(r) for r in list_goals() if int(r["id"]) == int(goal_id)), None)
        if not goal:
            return
        dlg = GoalDialog(self, "Edit goal", goal)
        if dlg.result and dlg.result["title"]:
            try:
                edit_goal(goal_id, dlg.result["title"], dlg.result["details"], dlg.result["target"])
                self.refresh_all()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def update_goal_progress_ui(self, goal_id: int, delta: int):
        goal = next((dict(r) for r in list_goals() if int(r["id"]) == int(goal_id)), None)
        if not goal:
            return
        try:
            update_goal_progress(goal_id, min(100, int(goal["progress"]) + int(delta)))
            self.sync_now(silent=True)
            self.refresh_all(silent=True)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def complete_goal_ui(self, goal_id: int):
        try:
            complete_goal(goal_id)
            self.sync_now()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def delete_goal_ui(self, goal_id: int):
        if is_emergency_lock_active():
            return
        if messagebox.askyesno(APP_NAME, "Delete this long-term goal?"):
            delete_goal(goal_id)
            self.refresh_all()

    def claim_goal_reward_ui(self, goal_id: int):
        try:
            current = get_setting("incognito_enabled_until", "")
            if current:
                try:
                    until = datetime.fromisoformat(current)
                except Exception:
                    until = None
                if until and until > datetime.now():
                    self.set_status("Incognito reward is already active.")
                    return
            ok, msg = enable_incognito_for_24h()
            if not ok:
                self.set_status(msg)
                messagebox.showwarning(APP_NAME, msg)
                return
            expiry = set_expiry_from_now(24)
            set_setting("incognito_enabled_until", expiry)
            log_event("manual_incognito_reward", f"id={goal_id};until={expiry}")
            
            # Claim points too
            add_points_transaction(10, "goal_claimed", f"Goal reward claimed for goal id={goal_id}")
            # Mark claimed in DB
            from .db import claim_goal_reward
            claim_goal_reward(goal_id)
            
            self.set_status("Incognito reward granted for 24h & +10 DP!")
            send_toast_notification("Reward Claimed", "Incognito mode allowed for 24 hours + 10 DP!")
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    # ── Folder Open Handlers ───────────────────────────────────────────────────

    def open_data_folder(self):
        from .config import DATA_DIR
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(DATA_DIR))
        except Exception:
            messagebox.showinfo(APP_NAME, f"Data folder path:\n{DATA_DIR}")

    def open_logs_folder(self):
        from .config import LOGS_DIR
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LOGS_DIR))
        except Exception:
            messagebox.showinfo(APP_NAME, f"Logs folder path:\n{LOGS_DIR}")

    def reset_launch_count(self):
        if messagebox.askyesno(APP_NAME, "Reset launcher counter index?"):
            set_setting("launch_count", "0")
            log_event("launch_count_reset", "")
            self.refresh_all()

    def show_about(self):
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} {VERSION}\n\n"
            "A high-discipline productivity command center for daily task commitments, "
            "recurring habits tracking, project milestone goals, and automated locks."
        )
        log_event("about_opened", "")

    def export_csv(self):
        try:
            out = export_month_csv()
            self.set_status(f"Exported to {out.name}")
            messagebox.showinfo(APP_NAME, f"Exported CSV database rollup successfully to:\n{out}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    app = UpSteeperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
