from __future__ import annotations

from datetime import datetime, timedelta

from .blocking import apply_daily_youtube_rule, disable_incognito, enable_incognito_for_24h, set_expiry_from_now
from .config import DAILY_UNLOCK_THRESHOLD, GOAL_COMPLETION_BONUS, GOAL_UNLOCK_HOURS
from .db import add_score_event, claim_goal_reward, get_setting, list_goals, log_event, rebuild_rollup, set_setting, task_stats, today_iso

def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None

def _incognito_state() -> dict:
    raw = get_setting("incognito_enabled_until", "")
    until = _parse_dt(raw)
    if until is None:
        return {"active": False, "until": None, "message": "Incognito is locked."}
    if datetime.now() >= until:
        disable_incognito()
        set_setting("incognito_enabled_until", "")
        log_event("incognito_expired", "")
        return {"active": False, "until": None, "message": "Incognito expired and is locked again."}
    return {"active": True, "until": until, "message": f"Incognito unlocked until {until.isoformat(timespec='seconds')}"}

def _grant_due_goal_rewards() -> list[dict]:
    grants = []
    for goal in list_goals():
        complete = int(goal["progress"]) >= 100 or goal["status"] == "complete"
        if complete and int(goal["reward_claimed"]) == 0:
            claim_goal_reward(int(goal["id"]))
            add_score_event(today_iso(), "goal_complete", GOAL_COMPLETION_BONUS, f"id={goal['id']};title={goal['title']}")
            enable_incognito_for_24h()
            until = datetime.now() + timedelta(hours=GOAL_UNLOCK_HOURS)
            previous = _parse_dt(get_setting("incognito_enabled_until", ""))
            if previous and previous > until:
                until = previous
            set_setting("incognito_enabled_until", until.isoformat(timespec="seconds"))
            log_event("goal_reward_granted", f"id={goal['id']};title={goal['title']};until={until.isoformat(timespec='seconds')}")
            grants.append({"goal_id": int(goal["id"]), "title": goal["title"], "until": until.isoformat(timespec="seconds"), "points": GOAL_COMPLETION_BONUS})
    return grants

def enforce_daily_rules() -> dict:
    day = today_iso()
    stats = task_stats(day)
    rollup = rebuild_rollup(day)
    block_ok, block_msg = apply_daily_youtube_rule(stats["completion"])
    
    # Check if unblocked by store purchase
    yt_unblocked_by_purchase = False
    yt_until_raw = get_setting("youtube_unblocked_until", "")
    if yt_until_raw:
        try:
            yt_until = datetime.fromisoformat(yt_until_raw)
            if datetime.now() < yt_until:
                yt_unblocked_by_purchase = True
        except Exception:
            pass
            
    youtube_blocked = (stats["completion"] < DAILY_UNLOCK_THRESHOLD) and not yt_unblocked_by_purchase
    set_setting("youtube_blocked", "1" if youtube_blocked else "0")
    set_setting("youtube_last_state_note", block_msg)
    set_setting("last_sync_day", day)
    return {
        "day": day,
        "completion": stats["completion"],
        "youtube_blocked": youtube_blocked,
        "block_ok": block_ok,
        "block_message": block_msg,
        "rollup": rollup,
    }

def boot_enforcement() -> dict:
    incognito = _incognito_state()
    daily = enforce_daily_rules()
    grants = _grant_due_goal_rewards()
    return {"daily": daily, "incognito": incognito, "goal_grants": grants}

def manual_recalculate() -> dict:
    return boot_enforcement()
