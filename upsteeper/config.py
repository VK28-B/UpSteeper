from __future__ import annotations

from pathlib import Path
import platform

APP_NAME = "UpSteeper"
APP_SUBTITLE = "Earned access. Daily discipline. Monthly mastery."
VERSION = "3.1.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
GENERATED_DIR = BASE_DIR / "generated"
LOGS_DIR = BASE_DIR / "logs"

DB_PATH = DATA_DIR / "upsteeper.db"
LOG_PATH = LOGS_DIR / "upsteeper.log"
CHART_PATH = GENERATED_DIR / "monthly_chart.png"
LOGO_PATH = ASSETS_DIR / "logo.png"
LOGO_ICON_PATH = ASSETS_DIR / "logo.ico"
BACKDROP_PATH = ASSETS_DIR / "backdrop.png"
GRID_PATH = ASSETS_DIR / "grid.png"

IS_WINDOWS = platform.system().lower() == "windows"

BLOCKED_SITES = ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"]
DAILY_UNLOCK_THRESHOLD = 70.0
DAILY_DONE_POINTS = 1
DAILY_FAILED_POINTS = -1
DAILY_ALL_DONE_BONUS = 1
GOAL_COMPLETION_BONUS = 5
GOAL_UNLOCK_HOURS = 24
HABIT_COMPLETION_POINTS = 1

# List of apps and websites blocked during Emergency Lock Mode
EMERGENCY_BLOCKED_SITES = [
    "facebook.com", "instagram.com", "twitter.com", "x.com", "reddit.com", 
    "netflix.com", "twitch.tv", "discord.com", "steamcommunity.com", "tiktok.com"
]
EMERGENCY_BLOCKED_APPS = [
    "discord.exe", "steam.exe", "epicgameslauncher.exe", "spotify.exe", 
    "riotclientservices.exe", "leagueoflegends.exe", "valorant.exe", "gta5.exe"
]

BG = "#081018"
PANEL = "#0d1621"
PANEL_2 = "#121d2a"
PANEL_3 = "#172636"
TEXT = "#eaf4ff"
MUTED = "#8ca1b9"
ACCENT = "#18d6ff"
ACCENT_2 = "#9e4dff"
ACCENT_3 = "#28e39c"
WARNING = "#f3bc45"
DANGER = "#ff627a"
SUCCESS = "#2fdf9d"
BORDER = "#213043"
CARD_BORDER = "#294055"
INPUT_BG = "#0a1118"
HOVER = "#1b2837"

CARD_PAD_X = 16
CARD_PAD_Y = 14
ANIM_STEP_MS = 16

