from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from .config import DATA_DIR, DB_PATH, DAILY_DONE_POINTS, DAILY_FAILED_POINTS, DAILY_ALL_DONE_BONUS, GOAL_COMPLETION_BONUS

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 2,
    task_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    progress INTEGER NOT NULL DEFAULT 0,
    target INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'active',
    completed_at TEXT,
    reward_claimed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    source TEXT NOT NULL,
    points INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_rollups (
    day TEXT PRIMARY KEY,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    done_tasks INTEGER NOT NULL DEFAULT 0,
    failed_tasks INTEGER NOT NULL DEFAULT 0,
    pending_tasks INTEGER NOT NULL DEFAULT 0,
    completion_pct REAL NOT NULL DEFAULT 0,
    task_points INTEGER NOT NULL DEFAULT 0,
    goal_bonus_points INTEGER NOT NULL DEFAULT 0,
    all_done_bonus_awarded INTEGER NOT NULL DEFAULT 0,
    score_points INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'daily',
    streak_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS habit_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER NOT NULL,
    completed_date TEXT NOT NULL,
    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE,
    UNIQUE(habit_id, completed_date)
);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    app_name TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(day, app_name)
);

CREATE TABLE IF NOT EXISTS custom_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cost INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS points_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    points INTEGER NOT NULL,
    source TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reward_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reward_name TEXT NOT NULL,
    points_spent INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def today_iso() -> str:
    return date.today().isoformat()

def month_prefix(day: str | None = None) -> str:
    return (day or today_iso())[:7]

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _upsert_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO settings(key, value, updated_at)
           VALUES(?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, now_iso()),
    )

def init_db() -> None:
    ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        defaults = {
            "theme": "midnight_neon",
            "youtube_blocked": "1",
            "youtube_last_state_note": "",
            "incognito_enabled_until": "",
            "launch_count": str(int(get_setting("launch_count", "0", conn=conn)) + 1),
            "last_sync_day": today_iso(),
            "auto_block_enabled": "1",
            "startup_synced": "0",
        }
        for key, value in defaults.items():
            _upsert_setting(conn, key, value)
        conn.commit()

def get_setting(key: str, default: str = "", conn: sqlite3.Connection | None = None) -> str:
    if conn is None:
        with connect() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        _upsert_setting(conn, key, value)
        conn.commit()

def log_event(event_type: str, payload: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events(event_type, payload, created_at) VALUES (?, ?, ?)",
            (event_type, payload, now_iso()),
        )
        conn.commit()

def add_task(title: str, category: str = "", notes: str = "", priority: int = 2, task_date: str | None = None) -> int:
    title = title.strip()
    if not title:
        raise ValueError("Task title cannot be empty")
    priority = max(1, min(5, int(priority)))
    task_date = task_date or today_iso()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO tasks(title, category, notes, priority, task_date, status, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (title, category.strip(), notes.strip(), priority, task_date, now_iso(), now_iso()),
        )
        conn.commit()
        log_event("task_added", f"id={cur.lastrowid};title={title}")
        return int(cur.lastrowid)

def edit_task(task_id: int, title: str, category: str = "", notes: str = "", priority: int = 2) -> None:
    title = title.strip()
    if not title:
        raise ValueError("Task title cannot be empty")
    priority = max(1, min(5, int(priority)))
    with connect() as conn:
        conn.execute(
            "UPDATE tasks SET title=?, category=?, notes=?, priority=?, updated_at=? WHERE id=?",
            (title, category.strip(), notes.strip(), priority, now_iso(), task_id),
        )
        conn.commit()
        log_event("task_edited", f"id={task_id};title={title}")

def update_task_status(task_id: int, status: str) -> None:
    if status not in {"pending", "done", "failed"}:
        raise ValueError("Invalid task status")
    
    with connect() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return
        old_status = row["status"]
        conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now_iso(), task_id))
        conn.commit()
        
    # Calculate point delta
    # done = +1, failed = -1, pending = 0
    status_points = {"done": 1, "failed": -1, "pending": 0}
    old_pts = status_points.get(old_status, 0)
    new_pts = status_points.get(status, 0)
    delta = new_pts - old_pts
    
    if delta != 0:
        add_points_transaction(delta, "task_status_change", f"Task id={task_id} transitioned from {old_status} to {status}")
        
    log_event("task_status", f"id={task_id};status={status}")

def delete_task(task_id: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return
        status = row["status"]
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        
    # Revert points if the deleted task was done or failed
    status_points = {"done": 1, "failed": -1, "pending": 0}
    pts = status_points.get(status, 0)
    if pts != 0:
        add_points_transaction(-pts, "task_deleted", f"Deleted task id={task_id} which was {status}")
        
    log_event("task_deleted", f"id={task_id}")

def list_tasks(task_date: str | None = None, query: str = "") -> list[sqlite3.Row]:
    task_date = task_date or today_iso()
    query = query.strip().lower()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE task_date=? ORDER BY priority DESC, id DESC", (task_date,)).fetchall()
    if query:
        rows = [r for r in rows if query in f"{r['title']} {r['category']} {r['notes']}".lower()]
    return rows

def task_stats(task_date: str | None = None) -> dict[str, Any]:
    rows = list_tasks(task_date)
    total = len(rows)
    done = sum(1 for r in rows if r["status"] == "done")
    failed = sum(1 for r in rows if r["status"] == "failed")
    pending = total - done - failed
    completion = round((done / total) * 100, 1) if total else 0.0
    return {"total": total, "done": done, "failed": failed, "pending": pending, "completion": completion}

def add_goal(title: str, details: str = "", target: int = 100) -> int:
    title = title.strip()
    if not title:
        raise ValueError("Goal title cannot be empty")
    target = max(1, min(100, int(target)))
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO goals(title, details, progress, target, status, completed_at, reward_claimed, created_at, updated_at)
               VALUES(?, ?, 0, ?, 'active', NULL, 0, ?, ?)""",
            (title, details.strip(), target, now_iso(), now_iso()),
        )
        conn.commit()
        log_event("goal_added", f"id={cur.lastrowid};title={title}")
        return int(cur.lastrowid)

def edit_goal(goal_id: int, title: str, details: str = "", target: int = 100) -> None:
    title = title.strip()
    if not title:
        raise ValueError("Goal title cannot be empty")
    target = max(1, min(100, int(target)))
    with connect() as conn:
        conn.execute(
            "UPDATE goals SET title=?, details=?, target=?, updated_at=? WHERE id=?",
            (title, details.strip(), target, now_iso(), goal_id),
        )
        conn.commit()
        log_event("goal_edited", f"id={goal_id};title={title}")

def update_goal_progress(goal_id: int, progress: int) -> None:
    progress = max(0, min(100, int(progress)))
    status = "complete" if progress >= 100 else "active"
    completed_at = now_iso() if progress >= 100 else None
    with connect() as conn:
        conn.execute(
            """UPDATE goals SET progress=?, status=?, completed_at=COALESCE(completed_at, ?), updated_at=? WHERE id=?""",
            (progress, status, completed_at, now_iso(), goal_id),
        )
        conn.commit()
        log_event("goal_progress", f"id={goal_id};progress={progress}")

def complete_goal(goal_id: int) -> None:
    update_goal_progress(goal_id, 100)

def claim_goal_reward(goal_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE goals SET reward_claimed=1, updated_at=? WHERE id=?", (now_iso(), goal_id))
        conn.commit()
        log_event("goal_reward_claimed", f"id={goal_id}")

def delete_goal(goal_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
        conn.commit()
        log_event("goal_deleted", f"id={goal_id}")

def list_goals(query: str = "") -> list[sqlite3.Row]:
    query = query.strip().lower()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM goals ORDER BY status ASC, progress DESC, id DESC").fetchall()
    if query:
        rows = [r for r in rows if query in f"{r['title']} {r['details']}".lower()]
    return rows

def goal_stats() -> dict[str, Any]:
    rows = list_goals()
    total = len(rows)
    complete = sum(1 for r in rows if int(r["progress"]) >= 100 or r["status"] == "complete")
    active = total - complete
    return {"total": total, "complete": complete, "active": active}

def add_score_event(day: str, source: str, points: int, note: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO score_events(day, source, points, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (day, source, int(points), note, now_iso()),
        )
        conn.commit()
        return int(cur.lastrowid)

def score_event_total(day: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(points), 0) AS total FROM score_events WHERE day=?", (day,)).fetchone()
    return int(row["total"] if row else 0)

def rebuild_rollup(day: str | None = None) -> dict[str, Any]:
    day = day or today_iso()
    tasks = list_tasks(day)
    total = len(tasks)
    done = sum(1 for r in tasks if r["status"] == "done")
    failed = sum(1 for r in tasks if r["status"] == "failed")
    pending = total - done - failed
    completion = round((done / total) * 100, 1) if total else 0.0
    task_points = done * DAILY_DONE_POINTS + failed * DAILY_FAILED_POINTS
    all_done_bonus = DAILY_ALL_DONE_BONUS if total > 0 and done == total else 0
    goal_bonus = score_event_total(day)
    score_points = task_points + all_done_bonus + goal_bonus
    with connect() as conn:
        conn.execute(
            """INSERT INTO daily_rollups(day, total_tasks, done_tasks, failed_tasks, pending_tasks, completion_pct, task_points, goal_bonus_points, all_done_bonus_awarded, score_points, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET
                   total_tasks=excluded.total_tasks,
                   done_tasks=excluded.done_tasks,
                   failed_tasks=excluded.failed_tasks,
                   pending_tasks=excluded.pending_tasks,
                   completion_pct=excluded.completion_pct,
                   task_points=excluded.task_points,
                   goal_bonus_points=excluded.goal_bonus_points,
                   all_done_bonus_awarded=excluded.all_done_bonus_awarded,
                   score_points=excluded.score_points,
                   updated_at=excluded.updated_at""",
            (day, total, done, failed, pending, completion, task_points, goal_bonus, all_done_bonus, score_points, now_iso()),
        )
        conn.commit()
    return {
        "day": day,
        "total_tasks": total,
        "done_tasks": done,
        "failed_tasks": failed,
        "pending_tasks": pending,
        "completion_pct": completion,
        "task_points": task_points,
        "goal_bonus_points": goal_bonus,
        "all_done_bonus_awarded": all_done_bonus,
        "score_points": score_points,
    }

def monthly_score(prefix: str | None = None) -> int:
    prefix = prefix or month_prefix()
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(score_points), 0) AS total FROM daily_rollups WHERE day LIKE ?", (f"{prefix}%",)).fetchone()
        return int(row["total"] if row else 0)

def monthly_rollups(prefix: str | None = None) -> list[sqlite3.Row]:
    prefix = prefix or month_prefix()
    with connect() as conn:
        return conn.execute("SELECT * FROM daily_rollups WHERE day LIKE ? ORDER BY day ASC", (f"{prefix}%",)).fetchall()

def recent_events(limit: int = 20) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()

def current_launch_count(conn: sqlite3.Connection | None = None) -> int:
    raw = get_setting("launch_count", "0", conn=conn)
    try:
        return int(raw)
    except Exception:
        return 0


# ==========================================
# HABIT TRACKER FUNCTIONS
# ==========================================

def add_habit(name: str, frequency: str = "daily") -> int:
    name = name.strip()
    if not name:
        raise ValueError("Habit name cannot be empty")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO habits(name, frequency, streak_count, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
            (name, frequency, now_iso(), now_iso())
        )
        conn.commit()
        log_event("habit_added", f"id={cur.lastrowid};name={name}")
        return int(cur.lastrowid)

def delete_habit(habit_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM habits WHERE id=?", (habit_id,))
        conn.commit()
        log_event("habit_deleted", f"id={habit_id}")

def list_habits() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM habits ORDER BY id DESC").fetchall()

def get_habit_history(habit_id: int) -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT completed_date FROM habit_completions WHERE habit_id=? ORDER BY completed_date ASC", (habit_id,)).fetchall()
        return [r["completed_date"] for r in rows]

def calculate_habit_streak(habit_id: int) -> int:
    from datetime import date, timedelta
    dates = get_habit_history(habit_id)
    if not dates:
        return 0
    date_set = set(dates)
    today = date.today()
    streak = 0
    curr = today
    if today.isoformat() in date_set:
        while curr.isoformat() in date_set:
            streak += 1
            curr = curr - timedelta(days=1)
    else:
        curr = today - timedelta(days=1)
        while curr.isoformat() in date_set:
            streak += 1
            curr = curr - timedelta(days=1)
    return streak

def complete_habit(habit_id: int, day: str | None = None) -> None:
    day = day or today_iso()
    with connect() as conn:
        row = conn.execute("SELECT id FROM habit_completions WHERE habit_id=? AND completed_date=?", (habit_id, day)).fetchone()
        if row:
            return
        conn.execute("INSERT INTO habit_completions(habit_id, completed_date) VALUES (?, ?)", (habit_id, day))
        conn.commit()
    
    streak = calculate_habit_streak(habit_id)
    with connect() as conn:
        conn.execute("UPDATE habits SET streak_count=?, updated_at=? WHERE id=?", (streak, now_iso(), habit_id))
        conn.commit()
    
    add_points_transaction(1, "habit_completion", f"Completed habit id={habit_id}")
    log_event("habit_completed", f"id={habit_id};day={day};streak={streak}")

def uncomplete_habit(habit_id: int, day: str | None = None) -> None:
    day = day or today_iso()
    with connect() as conn:
        conn.execute("DELETE FROM habit_completions WHERE habit_id=? AND completed_date=?", (habit_id, day))
        conn.commit()
    
    streak = calculate_habit_streak(habit_id)
    with connect() as conn:
        conn.execute("UPDATE habits SET streak_count=?, updated_at=? WHERE id=?", (streak, now_iso(), habit_id))
        conn.commit()
    
    add_points_transaction(-1, "habit_uncompletion", f"Uncompleted habit id={habit_id}")
    log_event("habit_uncompleted", f"id={habit_id};day={day}")


# ==========================================
# MILESTONE GOAL FUNCTIONS
# ==========================================

def add_milestone(goal_id: int, title: str) -> int:
    title = title.strip()
    if not title:
        raise ValueError("Milestone title cannot be empty")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO milestones(goal_id, title, completed, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
            (goal_id, title, now_iso(), now_iso())
        )
        conn.commit()
    update_goal_progress_from_milestones(goal_id)
    log_event("milestone_added", f"goal_id={goal_id};title={title}")
    return int(cur.lastrowid)

def delete_milestone(milestone_id: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT goal_id FROM milestones WHERE id=?", (milestone_id,)).fetchone()
        if not row:
            return
        goal_id = row["goal_id"]
        conn.execute("DELETE FROM milestones WHERE id=?", (milestone_id,))
        conn.commit()
    update_goal_progress_from_milestones(goal_id)
    log_event("milestone_deleted", f"id={milestone_id}")

def toggle_milestone(milestone_id: int, completed: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT goal_id FROM milestones WHERE id=?", (milestone_id,)).fetchone()
        if not row:
            return
        goal_id = row["goal_id"]
        conn.execute("UPDATE milestones SET completed=?, updated_at=? WHERE id=?", (completed, now_iso(), milestone_id))
        conn.commit()
    update_goal_progress_from_milestones(goal_id)
    log_event("milestone_toggled", f"id={milestone_id};completed={completed}")

def list_milestones(goal_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM milestones WHERE goal_id=? ORDER BY id ASC", (goal_id,)).fetchall()

def update_goal_progress_from_milestones(goal_id: int) -> None:
    milestones = list_milestones(goal_id)
    if not milestones:
        return
    total = len(milestones)
    completed = sum(1 for m in milestones if m["completed"] == 1)
    progress = int((completed / total) * 100)
    
    with connect() as conn:
        old_goal = conn.execute("SELECT progress, reward_claimed FROM goals WHERE id=?", (goal_id,)).fetchone()
    
    update_goal_progress(goal_id, progress)
    
    if progress >= 100 and old_goal and old_goal["progress"] < 100 and old_goal["reward_claimed"] == 0:
        add_points_transaction(5, "goal_completion", f"Completed goal id={goal_id}")


# ==========================================
# APP USAGE TRACKER FUNCTIONS
# ==========================================

def log_app_usage(app_name: str, duration_seconds: int, day: str | None = None) -> None:
    day = day or today_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO app_usage(day, app_name, duration_seconds, created_at)
               VALUES(?, ?, ?, ?)
               ON CONFLICT(day, app_name) DO UPDATE SET
                   duration_seconds=duration_seconds + excluded.duration_seconds""",
            (day, app_name, duration_seconds, now_iso())
        )
        conn.commit()

def get_app_usage(day: str | None = None) -> list[sqlite3.Row]:
    day = day or today_iso()
    with connect() as conn:
        return conn.execute("SELECT * FROM app_usage WHERE day=? ORDER BY duration_seconds DESC", (day,)).fetchall()

def get_app_usage_summary(start_day: str, end_day: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT app_name, SUM(duration_seconds) as total_duration FROM app_usage WHERE day BETWEEN ? AND ? GROUP BY app_name ORDER BY total_duration DESC",
            (start_day, end_day)
        ).fetchall()


# ==========================================
# POINTS LEDGER & REWARD STORE FUNCTIONS
# ==========================================

def get_discipline_points_balance() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(points), 0) AS total FROM points_ledger").fetchone()
        return int(row["total"] if row else 0)

def add_points_transaction(points: int, source: str, description: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO points_ledger(points, source, description, created_at) VALUES (?, ?, ?, ?)",
            (int(points), source, description, now_iso())
        )
        conn.commit()

def add_custom_reward(name: str, cost: int) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Reward name cannot be empty")
    cost = max(1, int(cost))
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO custom_rewards(name, cost, created_at) VALUES (?, ?, ?)",
            (name, cost, now_iso())
        )
        conn.commit()
        log_event("custom_reward_added", f"id={cur.lastrowid};name={name};cost={cost}")
        return int(cur.lastrowid)

def delete_custom_reward(reward_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM custom_rewards WHERE id=?", (reward_id,))
        conn.commit()
        log_event("custom_reward_deleted", f"id={reward_id}")

def list_custom_rewards() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM custom_rewards ORDER BY id DESC").fetchall()

def purchase_reward(reward_name: str, cost: int) -> bool:
    balance = get_discipline_points_balance()
    if balance < cost:
        return False
    add_points_transaction(-cost, "reward_purchase", f"Purchased reward: {reward_name}")
    with connect() as conn:
        conn.execute(
            "INSERT INTO reward_purchases(reward_name, points_spent, created_at) VALUES (?, ?, ?)",
            (reward_name, cost, now_iso())
        )
        conn.commit()
    log_event("reward_purchased", f"name={reward_name};cost={cost}")
    return True

def list_purchased_rewards() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM reward_purchases ORDER BY id DESC").fetchall()
