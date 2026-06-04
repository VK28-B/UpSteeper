from __future__ import annotations

from pathlib import Path
from datetime import datetime, date, timedelta
import sqlite3

from .config import CHART_PATH
from .db import monthly_rollups, month_prefix, connect, list_goals, list_habits, get_discipline_points_balance

def monthly_dataframe(prefix: str | None = None):
    import pandas as pd
    prefix = prefix or month_prefix()
    rows = monthly_rollups(prefix)
    if not rows:
        cols = ["day", "total_tasks", "done_tasks", "failed_tasks", "pending_tasks", "completion_pct", "task_points", "goal_bonus_points", "all_done_bonus_awarded", "score_points", "updated_at"]
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([dict(r) for r in rows])

def monthly_summary(prefix: str | None = None) -> dict:
    df = monthly_dataframe(prefix)
    if df.empty:
        return {
            "days": 0,
            "score_points": 0,
            "tasks_done": 0,
            "tasks_total": 0,
            "avg_completion": 0.0,
            "perfect_days": 0,
            "goal_completions": 0,
            "goal_bonus_points": 0,
            "current_streak": 0,
        }
    total_tasks = int(df["total_tasks"].sum())
    done_tasks = int(df["done_tasks"].sum())
    score_points = int(df["score_points"].sum())
    avg_completion = float(df["completion_pct"].mean()) if "completion_pct" in df else 0.0
    perfect_days = int((df["completion_pct"] >= 100).sum()) if "completion_pct" in df else 0
    goal_completions = int((df["goal_bonus_points"] > 0).sum()) if "goal_bonus_points" in df else 0
    goal_bonus_points = int(df["goal_bonus_points"].sum()) if "goal_bonus_points" in df else 0
    completion_flags = [1 if float(v) >= 70.0 else 0 for v in df["completion_pct"].tolist()]
    streak = 0
    for flag in reversed(completion_flags):
        if flag:
            streak += 1
        else:
            break
    return {
        "days": int(len(df)),
        "score_points": score_points,
        "tasks_done": done_tasks,
        "tasks_total": total_tasks,
        "avg_completion": round(avg_completion, 1),
        "perfect_days": perfect_days,
        "goal_completions": goal_completions,
        "goal_bonus_points": goal_bonus_points,
        "current_streak": streak,
    }

def scoreboard_text(prefix: str | None = None) -> str:
    s = monthly_summary(prefix)
    return (
        f"Days tracked: {s['days']}\n"
        f"Score points: {s['score_points']}\n"
        f"Tasks done: {s['tasks_done']} / {s['tasks_total']}\n"
        f"Average completion: {s['avg_completion']}%\n"
        f"Perfect days: {s['perfect_days']}\n"
        f"Goal completions: {s['goal_completions']}\n"
        f"Current streak: {s['current_streak']}"
    )

def export_month_csv(out_path: Path | None = None, prefix: str | None = None) -> Path:
    import pandas as pd
    out_path = out_path or Path.cwd() / f"upsteeper_{(prefix or month_prefix())}.csv"
    df = monthly_dataframe(prefix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return out_path

def build_monthly_chart(prefix: str | None = None, out_path: Path | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    
    prefix = prefix or month_prefix()
    out_path = out_path or CHART_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = monthly_dataframe(prefix)
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(6.8, 3.6), dpi=170)
    ax = fig.add_subplot(111)
    fig.patch.set_facecolor("#081018")
    ax.set_facecolor("#121d2a")
    if df.empty:
        ax.text(0.5, 0.5, "No data yet", ha="center", va="center", color="#8ca1b9", fontsize=14)
        ax.set_axis_off()
    else:
        x = list(range(len(df)))
        ax.plot(x, df["completion_pct"], linewidth=2.8, marker="o", color="#18d6ff", label="Completion %")
        ax.bar(x, df["score_points"], color="#9e4dff", alpha=0.22, label="Score points")
        ax.set_xticks(x)
        ax.set_xticklabels([d[5:] for d in df["day"].tolist()], fontsize=8)
        ax.grid(alpha=0.18)
        ax.legend(loc="upper left", frameon=False)
        ax.tick_params(colors="#8ca1b9")
        for spine in ax.spines.values():
            spine.set_color("#294055")
        ax.set_title(f"{prefix} performance", color="#eaf4ff")
    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_path


# ==========================================
# AI COACH ENGINE
# ==========================================

def get_ai_coach_insights() -> dict:
    """Analyze database data to generate statistical and behavioral coaching insights."""
    import pandas as pd
    
    insights = {
        "discipline_score": 50,
        "rank": "Novice",
        "level": 1,
        "xp_current": 0,
        "xp_needed": 100,
        "tips": [],
        "strengths": [],
        "weaknesses": []
    }
    
    # Calculate XP and Level based on total discipline points
    points = get_discipline_points_balance()
    if points < 0:
        points = 0
        
    level = points // 100 + 1
    xp_current = points % 100
    insights["level"] = level
    insights["xp_current"] = xp_current
    insights["xp_needed"] = 100
    
    # Ranks mapping
    ranks = {
        1: "Novice", 2: "Beginner", 3: "Apprentice", 4: "Disciple", 
        5: "Practitioner", 6: "Initiate", 7: "Warrior", 8: "Adept", 
        9: "Master", 10: "Grandmaster", 11: "Sage", 12: "Champion"
    }
    insights["rank"] = ranks.get(level, "Champion" if level >= 12 else "Novice")
    
    # A. Analyze Task Completion Trends (Weekly Pattern)
    with connect() as conn:
        rollups = conn.execute("SELECT day, completion_pct, total_tasks, done_tasks FROM daily_rollups").fetchall()
    
    if rollups:
        df_roll = pd.DataFrame([dict(r) for r in rollups])
        df_roll["day"] = pd.to_datetime(df_roll["day"])
        df_roll["day_name"] = df_roll["day"].dt.day_name()
        
        # Calculate overall discipline score (average completion percentage of past 30 days)
        recent_df = df_roll.sort_values("day").tail(30)
        avg_comp = recent_df["completion_pct"].mean()
        insights["discipline_score"] = int(round(avg_comp)) if not pd.isna(avg_comp) else 50
        
        # Group by day name
        by_day = df_roll.groupby("day_name")["completion_pct"].mean()
        
        if len(by_day) >= 3:
            worst_day = by_day.idxmin()
            worst_val = by_day.min()
            best_day = by_day.idxmax()
            best_val = by_day.max()
            
            insights["weaknesses"].append(
                f"Your task completion is lowest on {worst_day}s (avg: {int(worst_val)}%)."
            )
            insights["strengths"].append(
                f"You perform best on {best_day}s, reaching an average of {int(best_val)}% completion."
            )
            
            # Tips
            insights["tips"].append(
                f"Designate {worst_day}s for lighter tasks, or schedule a critical focus session early on {worst_day} morning."
            )
    else:
        insights["tips"].append("Complete more daily tasks so I can analyze your weekly patterns.")
        
    # B. Analyze App Usage & Distractions
    with connect() as conn:
        usage = conn.execute("SELECT app_name, SUM(duration_seconds) as total_sec FROM app_usage GROUP BY app_name").fetchall()
        
    if usage:
        df_use = pd.DataFrame([dict(u) for u in usage])
        df_use["total_hours"] = df_use["total_sec"] / 3600.0
        
        # Identify top distraction apps
        distractions = ["YouTube", "Steam", "Discord", "Netflix", "Twitch", "Social Media"]
        df_dist = df_use[df_use["app_name"].isin(distractions)].sort_values("total_sec", ascending=False)
        
        if not df_dist.empty:
            top_dist = df_dist.iloc[0]
            insights["weaknesses"].append(
                f"Your top distraction is {top_dist['app_name']} ({top_dist['total_hours']:.1f} hours logged)."
            )
            insights["tips"].append(
                f"Consider using the Emergency Lock Mode to block {top_dist['app_name']} when you need to write code or read."
            )
            
        # Identify productive apps
        productive = ["VS Code", "Browser"]
        df_prod = df_use[df_use["app_name"].isin(productive)].sort_values("total_sec", ascending=False)
        if not df_prod.empty:
            top_prod = df_prod.iloc[0]
            insights["strengths"].append(
                f"You spent {top_prod['total_hours']:.1f} hours working in {top_prod['app_name']}."
            )
    else:
        insights["tips"].append("App tracking is running in the background. Keep working to build usage insights.")
        
    # C. Habit Streaks
    habits = list_habits()
    if habits:
        top_streak_habit = max(habits, key=lambda h: h["streak_count"])
        if top_streak_habit["streak_count"] > 0:
            insights["strengths"].append(
                f"Strong streak! You completed '{top_streak_habit['name']}' for {top_streak_habit['streak_count']} consecutive sessions."
            )
        else:
            insights["tips"].append("Complete your habits daily to start building consistency streaks!")
    else:
        insights["tips"].append("Add habits in the Habit Tracker tab to begin building automatic behaviors.")

    # D. Default Coach Tip
    if not insights["tips"] or len(insights["tips"]) < 3:
        insights["tips"].append("Remember: Action precedes motivation. Start with a 5-minute task if you feel stuck.")
        insights["tips"].append("Unlock rewards responsibly in the Reward Store to maintain a healthy effort-reward cycle.")

    return insights
