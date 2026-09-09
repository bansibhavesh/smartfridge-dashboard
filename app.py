#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import re
import os
import math
import json
import time
import socket
import platform
import html
import threading
import logging
import sys
from datetime import datetime, timezone, timedelta

import requests
import psutil
import feedparser

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ============================================================
# mDNS / ZEROCONF
# ============================================================

try:
    from zeroconf import ServiceInfo, Zeroconf
    MDNS_AVAILABLE = True
    print("✅ Zeroconf available for mDNS")
except ImportError:
    MDNS_AVAILABLE = False
    print("⚠️ Zeroconf not installed. Install with: pip install zeroconf")

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
HOSTNAME = "smartfridge.local"

SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
]

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

# ============================================================
# APP
# ============================================================

app = FastAPI()

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    print("✅ Loaded environment variables from .env file")
except ImportError:
    print("ℹ️ python-dotenv not installed - using system environment variables")

# ============================================================
# LOGGING SETUP (Render-friendly)
# ============================================================

# Configure logging for Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # This goes to Render console
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# SYSTEM ACTIVITY LOG (Dashboard Logs)
# ============================================================

LOG_FILE = BASE_DIR / "system_log.json"
MAX_LOG_ENTRIES = 500  # Keep last 500 entries

def load_logs():
    """Load logs from file."""
    try:
        if LOG_FILE.exists():
            with open(LOG_FILE, 'r') as f:
                return json.load(f)
        return {"logs": []}
    except Exception as e:
        logger.error(f"Error loading logs: {e}")
        return {"logs": []}

def save_logs(log_data):
    """Save logs to file."""
    try:
        with open(LOG_FILE, 'w') as f:
            json.dump(log_data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving logs: {e}")
        return False

def add_log_entry(event_type, message, details=None):
    """Add a new log entry and output to console."""
    log_data = load_logs()
    logs = log_data.get("logs", [])
    
    entry = {
        "id": f"log_{datetime.now(timezone.utc).timestamp()}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "message": message,
        "details": details or {}
    }
    
    logs.insert(0, entry)
    
    # Limit log size
    if len(logs) > MAX_LOG_ENTRIES:
        logs = logs[:MAX_LOG_ENTRIES]
    
    log_data["logs"] = logs
    save_logs(log_data)
    
    # Also log to console (for Render)
    emoji_map = {
        "info": "ℹ️",
        "success": "✅",
        "warning": "⚠️",
        "error": "❌",
        "system": "🖥️",
        "task": "📋",
        "washer": "🧺",
        "habit": "🎯",
        "note": "📝"
    }
    emoji = emoji_map.get(event_type, "📌")
    logger.info(f"{emoji} {message}")
    
    return entry

def log_info(message, details=None):
    add_log_entry("info", message, details)

def log_success(message, details=None):
    add_log_entry("success", message, details)

def log_warning(message, details=None):
    add_log_entry("warning", message, details)

def log_error(message, details=None):
    add_log_entry("error", message, details)

def log_system(message, details=None):
    add_log_entry("system", message, details)

def log_task(message, details=None):
    add_log_entry("task", message, details)

def log_washer(message, details=None):
    add_log_entry("washer", message, details)

# ============================================================
# NEWS API CONFIG
# ============================================================

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
NEWS_API_ENABLED = bool(NEWS_API_KEY)

NEWS_CATEGORIES = {
    "india": {
        "query": "India",
        "label": "🇮🇳 INDIA",
        "country": "in",
        "category": "general"
    },
    "world": {
        "query": "world",
        "label": "🌍 WORLD",
        "country": "",
        "category": "general"
    },
    "technology": {
        "query": "technology",
        "label": "💻 TECHNOLOGY",
        "country": "",
        "category": "technology"
    },
    "aviation": {
        "query": "aviation",
        "label": "✈️ AVIATION",
        "country": "",
        "category": "science"
    }
}

# Fallback RSS feeds (free, no API key needed)
RSS_FEEDS = {
    "india": [
        "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "https://www.thehindu.com/news/national/?service=rss",
        "https://indianexpress.com/feed/",
        "https://www.ndtv.com/news/india/rss",
    ],
    "world": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.feedburner.com/Reuters/worldNews",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "technology": [
        "https://feeds.feedburner.com/TechCrunch",
        "https://www.wired.com/feed/rss",
        "https://feeds.feedburner.com/venturebeat/SZYF",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.arstechnica.com/arstechnica/index",
    ],
    "aviation": [
        "https://aviationweek.com/rss.xml",
        "https://www.flightglobal.com/rss",
        "https://www.ainonline.com/rss",
        "https://simpleflying.com/feed/",
        "https://aerospace.einnews.com/rss/",
    ]
}

_news_cache = None
_news_cache_time = None
_news_cache_duration = 300  # 5 minutes
_fallback_news_cache = None

# ============================================================
# OPENWEATHERMAP API CONFIG
# ============================================================

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
WEATHER_LAT = 17.3542
WEATHER_LON = 78.4100

_weather_cache = None
_weather_cache_time = None
_weather_cache_duration = 600

# ============================================================
# SMARTTHINGS API CONFIG
# ============================================================

SMARTTHINGS_TOKEN = os.getenv("SMARTTHINGS_TOKEN", "")
SMARTTHINGS_DEVICE_ID = os.getenv("SMARTTHINGS_DEVICE_ID", "")
SMARTTHINGS_ENABLED = bool(SMARTTHINGS_TOKEN and SMARTTHINGS_DEVICE_ID)

# ============================================================
# SMARTTHINGS ERROR IGNORING CONFIG
# ============================================================

# Set this to True to ignore SmartThings errors in logs
IGNORE_SMARTTHINGS_ERRORS = True

# Maximum number of consecutive SmartThings errors before suppressing logs
SMARTTHINGS_ERROR_THRESHOLD = 5

# Track SmartThings errors
_smartthings_error_count = 0
_smartthings_last_error_time = None
_smartthings_error_suppressed = False

if SMARTTHINGS_ENABLED:
    print("✅ SmartThings configuration loaded")
    log_system("SmartThings configured", {"device_id": SMARTTHINGS_DEVICE_ID})
else:
    print("⚠️ SmartThings not configured - token or device ID missing")
    log_warning("SmartThings not configured")

# Cache for SmartThings status
_smartthings_cache = None
_smartthings_cache_time = None
_smartthings_cache_duration = 30  # 30 seconds

# Washing machine status codes mapping
WASHER_STATUS_MAP = {
    "completed": {
        "status": "completed",
        "display": "✅ Done",
        "icon": "🧺",
        "color": "#4ade80",
        "description": "Wash cycle complete"
    },
    "running": {
        "status": "running",
        "display": "🔄 Running",
        "icon": "⏳",
        "color": "#fbbf24",
        "description": "Wash in progress"
    },
    "paused": {
        "status": "paused",
        "display": "⏸️ Paused",
        "icon": "⏸️",
        "color": "#fbbf24",
        "description": "Wash paused"
    },
    "error": {
        "status": "error",
        "display": "⚠️ Error",
        "icon": "⚠️",
        "color": "#f87171",
        "description": "Error detected"
    },
    "idle": {
        "status": "idle",
        "display": "💤 Idle",
        "icon": "🧺",
        "color": "#6b7280",
        "description": "Ready for next cycle"
    },
    "unknown": {
        "status": "unknown",
        "display": "❓ Unknown",
        "icon": "❓",
        "color": "#6b7280",
        "description": "Status unknown"
    }
}

# Cycle code mapping
CYCLE_CODES = {
    # Regular cycles
    "1B": {"name": "Cotton", "icon": "👕"},
    "35": {"name": "Synthetics", "icon": "🧵"},
    "1D": {"name": "Delicates", "icon": "🌸"},
    "A0": {"name": "Quick Wash", "icon": "⚡"},
    "B0": {"name": "Rinse + Spin", "icon": "💧"},
    "25": {"name": "Spin Only", "icon": "🌀"},
    "22": {"name": "Baby Care", "icon": "👶"},
    "96": {"name": "Wool", "icon": "🐑"},
    "20": {"name": "Outdoor", "icon": "🏔️"},
    "65": {"name": "Sports Wear", "icon": "🏃"},
    "33": {"name": "Shirts", "icon": "👔"},
    "23": {"name": "Denim", "icon": "👖"},
    "24": {"name": "Towels", "icon": "🧣"},
    "26": {"name": "Bedding", "icon": "🛏️"},
    "2F": {"name": "Drain + Spin", "icon": "💧"},
    "2E": {"name": "Rinse + Spin", "icon": "💧"},
    "30": {"name": "Silent Wash", "icon": "🤫"},
    "2D": {"name": "15 Min Quick", "icon": "⚡"},
    "36": {"name": "Hand Wash", "icon": "✋"},
    "38": {"name": "Cloudy Day", "icon": "☁️"},
    "37": {"name": "Allergy Care", "icon": "🤧"},
    "29": {"name": "Drum Clean", "icon": "🧹"},
    "27": {"name": "Blouses", "icon": "👚"},
    "28": {"name": "Curtains", "icon": "🪟"},
    
    # Special cycles
    "UC": {"name": "Drum Clean", "icon": "🧹"},
    "DC": {"name": "Drum Clean", "icon": "🧹"},
    "SC": {"name": "Self Clean", "icon": "🧼"},
    "TB": {"name": "Tub Clean", "icon": "🧽"},
}

def map_cycle_code(code):
    cycle_map = {
        "1B": "Cotton", "35": "Synthetics", "1D": "Delicates",
        "A0": "Quick Wash", "B0": "Rinse + Spin", "25": "Spin Only",
        "22": "Baby Care", "96": "Wool", "20": "Outdoor",
        "65": "Sports Wear", "33": "Shirts", "23": "Denim",
        "24": "Towels", "26": "Bedding", "2F": "Drain + Spin",
        "2E": "Rinse + Spin", "30": "Silent Wash", "2D": "15 Min Quick",
        "36": "Hand Wash", "38": "Cloudy Day", "37": "Allergy Care",
        "29": "Drum Clean", "27": "Blouses", "28": "Curtains",
    }
    return cycle_map.get(code, f"Cycle {code}")

# ============================================================
# OPENSKY OAUTH2 TOKEN MANAGER
# ============================================================

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID")
OPENSKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET")

if OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET:
    print("✅ Loaded OpenSky credentials from environment variables")

if not OPENSKY_CLIENT_ID or not OPENSKY_CLIENT_SECRET:
    OPENSKY_CREDENTIALS_FILE = BASE_DIR / "opensky_credentials.json"
    if OPENSKY_CREDENTIALS_FILE.exists():
        try:
            with open(OPENSKY_CREDENTIALS_FILE, 'r') as f:
                creds_data = json.load(f)
                possible_id_keys = ["client_id", "id", "CLIENT_ID", "clientId", "ClientId", "username"]
                possible_secret_keys = ["client_secret", "secret", "CLIENT_SECRET", "clientSecret", "ClientSecret", "password"]
                
                for key in possible_id_keys:
                    if key in creds_data and creds_data[key]:
                        OPENSKY_CLIENT_ID = creds_data[key]
                        print(f"✅ Found client_id using key: '{key}'")
                        break
                
                for key in possible_secret_keys:
                    if key in creds_data and creds_data[key]:
                        OPENSKY_CLIENT_SECRET = creds_data[key]
                        print(f"✅ Found client_secret using key: '{key}'")
                        break
        except Exception as e:
            print(f"❌ Error loading OpenSky credentials JSON: {e}")

if not OPENSKY_CLIENT_ID or not OPENSKY_CLIENT_SECRET:
    print("⚠️ Trying hardcoded credentials (for testing)...")
    OPENSKY_CLIENT_ID = "bansi-api-client"
    OPENSKY_CLIENT_SECRET = "fkUpnnDfn2yAYkSgWIO5FvmBsxeIQlBT"
    print("✅ Using hardcoded credentials")

HAS_VALID_CREDENTIALS = bool(OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET)
TOKEN_REFRESH_MARGIN = 30

class TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = None
        self._has_valid_credentials = HAS_VALID_CREDENTIALS

    def get_token(self):
        if not self._has_valid_credentials:
            return None
        if self.token and self.expires_at and datetime.now() < self.expires_at:
            return self.token
        return self._refresh()

    def _refresh(self):
        try:
            print("🔄 Refreshing OpenSky token...")
            r = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": OPENSKY_CLIENT_ID,
                    "client_secret": OPENSKY_CLIENT_SECRET,
                },
                timeout=10,
            )
            if r.status_code == 401:
                print("❌ Authentication failed!")
                self._has_valid_credentials = False
                return None
            r.raise_for_status()
            data = r.json()
            self.token = data["access_token"]
            expires_in = data.get("expires_in", 1800)
            self.expires_at = datetime.now() + timedelta(seconds=expires_in - TOKEN_REFRESH_MARGIN)
            print(f"✅ OpenSky token refreshed")
            return self.token
        except Exception as e:
            print(f"❌ Failed to get OpenSky token: {e}")
            return None

    def headers(self):
        token = self.get_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

tokens = TokenManager()

# ============================================================
# YOUTUBE API KEY
# ============================================================

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# ============================================================
# FAMILY
# ============================================================

FAMILY_MEMBERS = [
    {"id": "bhavesh", "name": "Bhavesh", "emoji": "🧑"},
    {"id": "sowji", "name": "sowji", "emoji": "👩"},
    {"id": "arun", "name": "arun", "emoji": "👨"},
    {"id": "thaswi", "name": "thaswi", "emoji": "👧"},
]

# ============================================================
# MODELS
# ============================================================

class TaskCreate(BaseModel):
    title: str
    member: str

class EventCreate(BaseModel):
    title: str
    date: str
    start_time: str
    end_time: str

class ScheduledTaskCreate(BaseModel):
    title: str
    hour: int
    minute: int
    member: str
    enabled: bool = True
    label: str = "auto"

# ============================================================
# LOCAL FALLBACK STORAGE FOR TASKS & CALENDAR
# ============================================================

LOCAL_TASKS_FILE = BASE_DIR / "local_tasks.json"
LOCAL_CALENDAR_FILE = BASE_DIR / "local_calendar.json"

def load_local_tasks():
    try:
        if LOCAL_TASKS_FILE.exists():
            with open(LOCAL_TASKS_FILE, 'r') as f:
                return json.load(f)
        return {"tasks": [], "last_sync": None}
    except Exception as e:
        print(f"⚠️ Error loading local tasks: {e}")
        return {"tasks": [], "last_sync": None}

def save_local_tasks(tasks):
    try:
        with open(LOCAL_TASKS_FILE, 'w') as f:
            json.dump({
                "tasks": tasks,
                "last_sync": datetime.now(timezone.utc).isoformat()
            }, f, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ Error saving local tasks: {e}")
        return False

def load_local_calendar():
    try:
        if LOCAL_CALENDAR_FILE.exists():
            with open(LOCAL_CALENDAR_FILE, 'r') as f:
                return json.load(f)
        return {"events": [], "last_sync": None}
    except Exception as e:
        print(f"⚠️ Error loading local calendar: {e}")
        return {"events": [], "last_sync": None}

def save_local_calendar(events):
    try:
        with open(LOCAL_CALENDAR_FILE, 'w') as f:
            json.dump({
                "events": events,
                "last_sync": datetime.now(timezone.utc).isoformat()
            }, f, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ Error saving local calendar: {e}")
        return False

# ============================================================
# SCHEDULED TASKS
# ============================================================

SCHEDULED_TASKS_FILE = BASE_DIR / "scheduled_tasks.json"

def load_scheduled_tasks():
    try:
        if SCHEDULED_TASKS_FILE.exists():
            with open(SCHEDULED_TASKS_FILE, 'r') as f:
                return json.load(f)
        return {"tasks": []}
    except Exception as e:
        print(f"⚠️ Error loading scheduled tasks: {e}")
        return {"tasks": []}

def save_scheduled_tasks(tasks):
    try:
        with open(SCHEDULED_TASKS_FILE, 'w') as f:
            json.dump({"tasks": tasks}, f, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ Error saving scheduled tasks: {e}")
        return False

def initialize_default_scheduled_task():
    scheduled_tasks = load_scheduled_tasks()
    tasks = scheduled_tasks.get("tasks", [])
    
    default_exists = any(
        task.get("hour") == 15 and 
        task.get("minute") == 0 and 
        task.get("title") == "Daily Check-in" 
        for task in tasks
    )
    
    if not default_exists:
        new_task = {
            "id": f"sched_default_{datetime.now(timezone.utc).timestamp()}",
            "title": "Daily Check-in",
            "hour": 15,
            "minute": 0,
            "member": "bhavesh",
            "enabled": True,
            "label": "Daily Reminder",
            "created": datetime.now(timezone.utc).isoformat(),
            "last_run": None
        }
        tasks.append(new_task)
        save_scheduled_tasks(tasks)
        print("✅ Default 3 PM scheduled task created: 'Daily Check-in'")
        log_system("Default scheduled task created", {"task": "Daily Check-in", "time": "15:00"})

# ============================================================
# SCHEDULER
# ============================================================

_scheduler_thread = None
_scheduler_running = False
_scheduler_lock = threading.Lock()

def create_task_via_api(title, member_id="bhavesh"):
    try:
        service = get_google_tasks_service()
        task_list = get_default_task_list(service)
        
        member = get_member(member_id)
        member_name = member["name"] if member else "Unassigned"
        
        created = service.tasks().insert(
            tasklist=task_list["id"],
            body={"title": f"[{member_name}] {title}"},
        ).execute()
        
        parsed = parse_task(created)
        
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        local_tasks.append(parsed)
        save_local_tasks(local_tasks)
        
        log_task(f"Scheduled task created: {title}", {"member": member_name})
        return {"success": True, "task": parsed}
        
    except Exception as e:
        log_error(f"Scheduled task creation failed: {e}")
        return {"success": False, "error": str(e)}

def run_scheduled_tasks():
    try:
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        now = datetime.now()
        
        for task in tasks:
            if not task.get("enabled", True):
                continue
            
            if now.hour == task["hour"] and now.minute == task["minute"]:
                last_run = task.get("last_run")
                if last_run:
                    last_run_date = datetime.fromisoformat(last_run).date()
                    if last_run_date == now.date():
                        continue
                
                title = task["title"]
                if task.get("label") and task.get("label") != "auto":
                    title = f"[{task['label']}] {title}"
                
                result = create_task_via_api(title, task.get("member", "bhavesh"))
                
                if result["success"]:
                    scheduled_tasks = load_scheduled_tasks()
                    tasks = scheduled_tasks.get("tasks", [])
                    for t in tasks:
                        if t["id"] == task["id"]:
                            t["last_run"] = now.isoformat()
                            break
                    save_scheduled_tasks(tasks)
                    log_success(f"Scheduled task executed: {title}")
                    
    except Exception as e:
        log_error(f"Scheduler error: {e}")

def scheduler_loop():
    global _scheduler_running
    print("🔄 Scheduler started")
    log_system("Scheduler started")
    
    while _scheduler_running:
        try:
            run_scheduled_tasks()
        except Exception as e:
            log_error(f"Scheduler loop error: {e}")
        
        for _ in range(30):
            if not _scheduler_running:
                break
            time.sleep(1)

def start_scheduler():
    global _scheduler_thread, _scheduler_running
    
    with _scheduler_lock:
        if _scheduler_running:
            return
        
        _scheduler_running = True
        _scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        _scheduler_thread.start()
        print("✅ Scheduler started")

def stop_scheduler():
    global _scheduler_running, _scheduler_thread
    
    with _scheduler_lock:
        if not _scheduler_running:
            return
        
        _scheduler_running = False
        if _scheduler_thread:
            _scheduler_thread.join(timeout=2)
        log_system("Scheduler stopped")

def restart_scheduler():
    stop_scheduler()
    start_scheduler()

# Start scheduler on startup
start_scheduler()
initialize_default_scheduled_task()

# ============================================================
# GOOGLE AUTH
# ============================================================

def get_google_credentials():
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        if not CREDENTIALS_FILE.exists():
            raise Exception("credentials.json not found.")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds

def get_google_tasks_service():
    return build("tasks", "v1", credentials=get_google_credentials())

def get_google_calendar_service():
    return build("calendar", "v3", credentials=get_google_credentials())

# ============================================================
# TASK HELPERS
# ============================================================

def get_default_task_list(service):
    result = service.tasklists().list(maxResults=100).execute()
    lists = result.get("items", [])
    if not lists:
        raise Exception("No Google Task lists found.")
    for task_list in lists:
        if task_list.get("title") == "My Tasks":
            return task_list
    return lists[0]

def get_member(member_id):
    for member in FAMILY_MEMBERS:
        if member["id"] == member_id:
            return member
    return None

def make_task_title(title, member_id):
    member = get_member(member_id)
    if not member:
        raise ValueError("Invalid family member.")
    return f"[{member['name']}] {title.strip()}"

def parse_task(task):
    raw_title = task.get("title", "Untitled task")
    match = re.match(r"^\[(.*?)\]\s*(.*)$", raw_title)
    member = None
    if match:
        member_name = match.group(1)
        clean_title = match.group(2)
        for family_member in FAMILY_MEMBERS:
            if family_member["name"].lower() == member_name.lower():
                member = family_member
                break
    else:
        clean_title = raw_title
    if not member:
        member = {"id": "unknown", "name": "Unassigned", "emoji": "👤"}
    return {
        "id": task["id"],
        "title": clean_title,
        "member": member["id"],
        "member_name": member["name"],
        "member_emoji": member["emoji"],
    }

# ============================================================
# SYSTEM STATUS
# ============================================================

def get_system_status():
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_gb = memory.used / (1024**3)
        memory_total_gb = memory.total / (1024**3)
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_used_gb = disk.used / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        
        hostname = socket.gethostname()
        ip_address = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_address = s.getsockname()[0]
            s.close()
        except:
            ip_address = "Unknown"
        
        uptime_seconds = time.time() - psutil.boot_time()
        uptime = str(timedelta(seconds=int(uptime_seconds)))
        
        return {
            "status": "online",
            "hostname": hostname,
            "ip": ip_address,
            "cpu_usage": round(cpu_percent, 1),
            "memory_usage": round(memory_percent, 1),
            "memory_used_gb": round(memory_used_gb, 2),
            "memory_total_gb": round(memory_total_gb, 2),
            "disk_usage": round(disk_percent, 1),
            "disk_used_gb": round(disk_used_gb, 2),
            "disk_total_gb": round(disk_total_gb, 2),
            "uptime": uptime,
            "platform": platform.system(),
            "platform_release": platform.release(),
        }
    except Exception as e:
        log_error(f"System status error: {e}")
        return {"status": "error", "error": str(e)}

@app.get("/api/system/status")
def system_status():
    return get_system_status()

# ============================================================
# SMARTTHINGS - WASHING MACHINE STATUS
# ============================================================

def get_washer_status_from_api():
    """Fetch washing machine status from SmartThings API."""
    global _smartthings_error_count, _smartthings_last_error_time, _smartthings_error_suppressed
    
    if not SMARTTHINGS_ENABLED:
        return None
    
    try:
        headers = {
            "Authorization": f"Bearer {SMARTTHINGS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        url = f"https://api.smartthings.com/v1/devices/{SMARTTHINGS_DEVICE_ID}/status"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            # Track errors
            _smartthings_error_count += 1
            _smartthings_last_error_time = datetime.now(timezone.utc)
            
            # Check if we should log this error
            if _smartthings_error_count <= SMARTTHINGS_ERROR_THRESHOLD:
                log_warning(f"SmartThings API error: {response.status_code}")
            elif _smartthings_error_count == SMARTTHINGS_ERROR_THRESHOLD + 1:
                log_warning("SmartThings API errors suppressed (will retry silently)")
                _smartthings_error_suppressed = True
            
            return None
        
        # Reset error count on success
        if _smartthings_error_suppressed:
            log_success("SmartThings API recovered")
            _smartthings_error_suppressed = False
        _smartthings_error_count = 0
        
        data = response.json()
        status = parse_washer_status(data)
        
        if status:
            log_washer(f"Status updated: {status['status']} - {status['cycle']}", {
                "status": status['status'],
                "cycle": status['cycle'],
                "progress": status['progress']
            })
        
        return status
        
    except requests.exceptions.Timeout:
        _smartthings_error_count += 1
        _smartthings_last_error_time = datetime.now(timezone.utc)
        
        if _smartthings_error_count <= SMARTTHINGS_ERROR_THRESHOLD:
            log_warning("SmartThings API timeout")
        elif _smartthings_error_count == SMARTTHINGS_ERROR_THRESHOLD + 1:
            log_warning("SmartThings API errors suppressed (will retry silently)")
            _smartthings_error_suppressed = True
        
        return None
    except Exception as e:
        _smartthings_error_count += 1
        _smartthings_last_error_time = datetime.now(timezone.utc)
        
        if _smartthings_error_count <= SMARTTHINGS_ERROR_THRESHOLD:
            log_error(f"SmartThings error: {e}")
        elif _smartthings_error_count == SMARTTHINGS_ERROR_THRESHOLD + 1:
            log_warning("SmartThings API errors suppressed (will retry silently)")
            _smartthings_error_suppressed = True
        
        return None

def parse_washer_status(data):
    """Parse the SmartThings API response for washing machine status."""
    try:
        # Get the components/status
        components = data.get("components", {})
        
        # Check all components for capabilities
        capabilities = {}
        
        # First check "main" component
        main = components.get("main", {})
        if "capabilities" in main:
            capabilities = main.get("capabilities", {})
        
        # If no capabilities in main, try to find them elsewhere
        if not capabilities:
            for comp_name, comp_data in components.items():
                if isinstance(comp_data, dict) and "capabilities" in comp_data:
                    capabilities = comp_data.get("capabilities", {})
                    if capabilities:
                        break
        
        # IMPORTANT: Also check for top-level capabilities that might not be in "main"
        if not capabilities.get("samsungce.washerOperatingState"):
            for comp_name, comp_data in components.items():
                if isinstance(comp_data, dict):
                    if "samsungce.washerOperatingState" in comp_data:
                        for key, value in comp_data.items():
                            if key not in capabilities:
                                capabilities[key] = value
        
        # If still no capabilities, try using the raw data directly
        if not capabilities:
            for key, value in data.items():
                if key == "samsungce.washerOperatingState" or "washerOperatingState" in key:
                    capabilities[key] = value
        
        # Get washer operating state - try multiple locations
        washer_ops = {}
        
        if "samsungce.washerOperatingState" in capabilities:
            washer_ops = capabilities.get("samsungce.washerOperatingState", {})
        else:
            for key, value in data.items():
                if "washerOperatingState" in key:
                    washer_ops = value
                    break
        
        if not washer_ops:
            for comp_name, comp_data in components.items():
                if isinstance(comp_data, dict):
                    if "samsungce.washerOperatingState" in comp_data:
                        washer_ops = comp_data.get("samsungce.washerOperatingState", {})
                        break
        
        # Get the key status values
        operating_state = washer_ops.get("operatingState", {}).get("value", "unknown")
        washer_job_state = washer_ops.get("washerJobState", {}).get("value", "unknown")
        washer_job_phase = washer_ops.get("washerJobPhase", {}).get("value", "unknown")
        progress = washer_ops.get("progress", {}).get("value", 0)
        remaining_time = washer_ops.get("remainingTime", {}).get("value", 0)
        remaining_time_str = washer_ops.get("remainingTimeStr", {}).get("value", "")
        
        # Get switch state - try multiple locations
        switch_state = "unknown"
        if "switch" in capabilities:
            switch_state = capabilities.get("switch", {}).get("switch", {}).get("value", "unknown")
        elif "samsungce.switch" in capabilities:
            switch_state = capabilities.get("samsungce.switch", {}).get("switch", {}).get("value", "unknown")
        else:
            for comp_name, comp_data in components.items():
                if isinstance(comp_data, dict):
                    if "switch" in comp_data:
                        switch_state = comp_data.get("switch", {}).get("switch", {}).get("value", "unknown")
                        break
                    if "samsungce.switch" in comp_data:
                        switch_state = comp_data.get("samsungce.switch", {}).get("switch", {}).get("value", "unknown")
                        break
        
        # Get cycle info - try multiple locations
        course = "unknown"
        if "custom.supportedOptions" in capabilities:
            course = capabilities.get("custom.supportedOptions", {}).get("course", {}).get("value", "unknown")
        else:
            for comp_name, comp_data in components.items():
                if isinstance(comp_data, dict):
                    if "custom.supportedOptions" in comp_data:
                        course = comp_data.get("custom.supportedOptions", {}).get("course", {}).get("value", "unknown")
                        break
        
        # Determine the status
        status_key = "idle"
        
        # Check if running
        is_running = False
        
        if operating_state == "running":
            is_running = True
        elif switch_state == "on":
            is_running = True
        elif washer_job_state in ["running", "drumCleaning", "wash", "rinse", "spin", "washing", "drying"]:
            is_running = True
        elif washer_job_phase in ["running", "drumCleaning", "wash", "rinse", "spin", "washing", "drying"]:
            is_running = True
        
        # Check if completed
        is_completed = False
        if washer_job_state == "finished" or washer_job_phase == "finished":
            is_completed = True
        elif progress == 100:
            is_completed = True
        
        # Determine final status
        if is_completed:
            status_key = "completed"
        elif is_running:
            status_key = "running"
        elif operating_state == "paused":
            status_key = "paused"
        else:
            status_key = "idle"
        
        # Special case for Drum Clean
        if washer_job_state == "drumCleaning" or washer_job_phase == "drumCleaning":
            status_key = "running"
        
        # Get status info
        status_info = WASHER_STATUS_MAP.get(status_key, WASHER_STATUS_MAP["unknown"])
        
        # Determine cycle name
        cycle_name = "Unknown"
        cycle_icon = "🔄"
        
        # Check if it's Drum Clean
        if washer_job_state == "drumCleaning" or washer_job_phase == "drumCleaning":
            cycle_name = "Drum Clean"
            cycle_icon = "🧹"
        elif course and course != "unknown":
            cycle_info = CYCLE_CODES.get(course, {"name": f"Cycle {course}", "icon": "🔄"})
            cycle_name = cycle_info["name"]
            cycle_icon = cycle_info["icon"]
        
        # If still unknown, try to determine from job state
        if cycle_name == "Unknown" and washer_job_state not in ["unknown", "finished", "none"]:
            cycle_name = f"{washer_job_state.capitalize()}"
            cycle_icon = "⚙️"
        
        # Build result
        result = {
            "status": status_info["status"],
            "display": status_info["display"],
            "icon": status_info["icon"],
            "color": status_info["color"],
            "description": status_info["description"],
            "cycle": cycle_name,
            "cycle_icon": cycle_icon,
            "cycle_code": course,
            "progress": progress,
            "remaining_time": remaining_time,
            "remaining_time_str": remaining_time_str,
            "operating_state": operating_state,
            "job_state": washer_job_state,
            "job_phase": washer_job_phase,
            "switch_state": switch_state,
            "raw_data": data
        }
        
        return result
        
    except Exception as e:
        log_error(f"Error parsing washer status: {e}")
        return None

@app.get("/api/smartthings/washer")
def get_washer_status():
    """Get washing machine status from SmartThings."""
    global _smartthings_cache, _smartthings_cache_time, _smartthings_error_suppressed
    
    if not SMARTTHINGS_ENABLED:
        return {
            "success": False,
            "enabled": False,
            "error": "SmartThings not configured"
        }
    
    try:
        # Check cache
        if _smartthings_cache and _smartthings_cache_time:
            elapsed = (datetime.now(timezone.utc) - _smartthings_cache_time).total_seconds()
            if elapsed < _smartthings_cache_duration:
                return {
                    "success": True,
                    "enabled": True,
                    "status": _smartthings_cache,
                    "cached": True,
                    "cache_age": round(elapsed, 1)
                }
        
        status = get_washer_status_from_api()
        
        if status:
            _smartthings_cache = status
            _smartthings_cache_time = datetime.now(timezone.utc)
            return {
                "success": True,
                "enabled": True,
                "status": status,
                "cached": False
            }
        else:
            # Return cached status if available, even if stale
            if _smartthings_cache:
                return {
                    "success": True,
                    "enabled": True,
                    "status": _smartthings_cache,
                    "cached": True,
                    "stale": True,
                    "cache_age": round((datetime.now(timezone.utc) - _smartthings_cache_time).total_seconds(), 1) if _smartthings_cache_time else 0
                }
            
            return {
                "success": False,
                "enabled": True,
                "error": "Failed to get washer status",
                "suppressed": _smartthings_error_suppressed
            }
            
    except Exception as e:
        # Don't log if errors are being suppressed
        if not _smartthings_error_suppressed:
            log_error(f"Washer status error: {e}")
        return {
            "success": False,
            "enabled": True,
            "error": str(e),
            "suppressed": _smartthings_error_suppressed
        }

@app.post("/api/smartthings/reset-errors")
def reset_smartthings_errors():
    """Reset SmartThings error counter and suppression."""
    global _smartthings_error_count, _smartthings_error_suppressed
    _smartthings_error_count = 0
    _smartthings_error_suppressed = False
    log_system("SmartThings error counter reset")
    return {"success": True}

@app.get("/api/smartthings/device-info")
def get_smartthings_device_info():
    """Get basic device info from SmartThings."""
    if not SMARTTHINGS_ENABLED:
        return {
            "success": False,
            "enabled": False,
            "error": "SmartThings not configured"
        }
    
    try:
        headers = {
            "Authorization": f"Bearer {SMARTTHINGS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        url = f"https://api.smartthings.com/v1/devices/{SMARTTHINGS_DEVICE_ID}"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return {
                "success": False,
                "error": f"API error: {response.status_code}"
            }
        
        data = response.json()
        return {
            "success": True,
            "device": data
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/api/smartthings/debug")
def debug_smartthings():
    """Debug endpoint to see raw API response."""
    if not SMARTTHINGS_ENABLED:
        return {"error": "Not configured"}
    
    try:
        headers = {"Authorization": f"Bearer {SMARTTHINGS_TOKEN}"}
        url = f"https://api.smartthings.com/v1/devices/{SMARTTHINGS_DEVICE_ID}/status"
        response = requests.get(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# LOGS ENDPOINTS
# ============================================================

@app.get("/api/logs")
def get_logs(limit: int = 100, type_filter: str = None):
    """Get system logs."""
    log_data = load_logs()
    logs = log_data.get("logs", [])
    
    if type_filter:
        logs = [log for log in logs if log.get("type") == type_filter]
    
    return {
        "logs": logs[:limit],
        "total": len(log_data.get("logs", [])),
        "filtered_count": len(logs)
    }

@app.delete("/api/logs")
def clear_logs():
    """Clear all logs."""
    save_logs({"logs": []})
    log_system("Logs cleared")
    return {"success": True}

@app.get("/api/logs/stats")
def get_log_stats():
    """Get log statistics."""
    log_data = load_logs()
    logs = log_data.get("logs", [])
    
    stats = {
        "total": len(logs),
        "by_type": {},
        "last_24h": 0
    }
    
    now = datetime.now(timezone.utc)
    for log in logs:
        log_type = log.get("type", "unknown")
        stats["by_type"][log_type] = stats["by_type"].get(log_type, 0) + 1
        
        try:
            log_time = datetime.fromisoformat(log.get("timestamp", ""))
            diff = now - log_time
            if diff.days < 1:
                stats["last_24h"] += 1
        except:
            pass
    
    return stats

# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")

# ============================================================
# DISCOVERY PAGE
# ============================================================

@app.get("/discover")
def discover_page():
    return FileResponse(BASE_DIR / "static" / "discover.html")

# ============================================================
# FAMILY
# ============================================================

@app.get("/api/family")
def get_family():
    return {"members": FAMILY_MEMBERS}

# ============================================================
# TASKS - WITH FALLBACK
# ============================================================

@app.get("/api/tasks")
def get_tasks():
    try:
        service = get_google_tasks_service()
        task_lists = service.tasklists().list(maxResults=100).execute()
        all_tasks = []
        for task_list in task_lists.get("items", []):
            tasks = service.tasks().list(
                tasklist=task_list["id"],
                showCompleted=False,
                showHidden=False,
                maxResults=100,
            ).execute()
            for task in tasks.get("items", []):
                parsed = parse_task(task)
                parsed["task_list"] = task_list.get("title", "Tasks")
                all_tasks.append(parsed)
        
        save_local_tasks(all_tasks)
        return {
            "tasks": all_tasks,
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        log_warning(f"Google Tasks error: {e}, using local fallback")
        local_data = load_local_tasks()
        return {
            "tasks": local_data.get("tasks", []),
            "source": "local",
            "fallback": True,
            "fallback_reason": str(e)
        }

@app.post("/api/tasks")
def create_task(task: TaskCreate):
    try:
        if not task.title.strip():
            raise Exception("Task cannot be empty.")
        member = get_member(task.member)
        if not member:
            raise Exception("Invalid family member.")
        
        service = get_google_tasks_service()
        task_list = get_default_task_list(service)
        created = service.tasks().insert(
            tasklist=task_list["id"],
            body={"title": make_task_title(task.title, task.member)},
        ).execute()
        
        parsed = parse_task(created)
        
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        local_tasks.append(parsed)
        save_local_tasks(local_tasks)
        
        log_task(f"Task created: {task.title}", {"member": task.member, "source": "google"})
        
        return {
            "success": True, 
            "task": parsed,
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        log_warning(f"Google Tasks create error: {e}, using local fallback")
        
        member = get_member(task.member)
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        
        new_task = {
            "id": f"local_{datetime.now(timezone.utc).timestamp()}",
            "title": task.title.strip(),
            "member": task.member,
            "member_name": member["name"] if member else task.member,
            "member_emoji": member["emoji"] if member else "👤",
            "task_list": "Local Tasks",
            "local": True
        }
        local_tasks.append(new_task)
        save_local_tasks(local_tasks)
        
        log_task(f"Task created locally: {task.title}", {"member": task.member, "source": "local"})
        
        return {
            "success": True,
            "task": new_task,
            "source": "local",
            "fallback": True,
            "fallback_reason": str(e)
        }

@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: str):
    try:
        service = get_google_tasks_service()
        task_list = get_default_task_list(service)
        result = service.tasks().patch(
            tasklist=task_list["id"],
            task=task_id,
            body={"status": "completed"},
        ).execute()
        
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        local_tasks = [t for t in local_tasks if t.get("id") != task_id]
        save_local_tasks(local_tasks)
        
        log_task(f"Task completed: {task_id}", {"source": "google"})
        
        return {
            "success": True, 
            "task": result["id"],
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        log_warning(f"Google Tasks complete error: {e}, using local fallback")
        
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        local_tasks = [t for t in local_tasks if t.get("id") != task_id]
        save_local_tasks(local_tasks)
        
        log_task(f"Task completed locally: {task_id}", {"source": "local"})
        
        return {
            "success": True,
            "task": task_id,
            "source": "local",
            "fallback": True,
            "fallback_reason": str(e)
        }

# ============================================================
# CALENDAR - WITH FALLBACK
# ============================================================

@app.get("/api/calendar")
def get_calendar():
    try:
        service = get_google_calendar_service()
        now = datetime.now(timezone.utc).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=now,
            maxResults=100,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = []
        for event in result.get("items", []):
            start = event.get("start", {})
            end = event.get("end", {})
            start_value = start.get("dateTime") or start.get("date")
            end_value = end.get("dateTime") or end.get("date")
            events.append({
                "id": event.get("id"),
                "title": event.get("summary", "Untitled"),
                "start": start_value,
                "end": end_value,
                "all_day": "date" in start,
            })
        
        save_local_calendar(events)
        return {
            "events": events,
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        log_warning(f"Google Calendar error: {e}, using local fallback")
        local_data = load_local_calendar()
        return {
            "events": local_data.get("events", []),
            "source": "local",
            "fallback": True,
            "fallback_reason": str(e)
        }

@app.post("/api/calendar")
def create_calendar_event(event: EventCreate):
    try:
        if not event.title.strip():
            raise Exception("Event title cannot be empty.")
        
        service = get_google_calendar_service()
        start_datetime = f"{event.date}T{event.start_time}:00"
        end_datetime = f"{event.date}T{event.end_time}:00"
        body = {
            "summary": event.title.strip(),
            "start": {"dateTime": start_datetime, "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_datetime, "timeZone": "Asia/Kolkata"},
        }
        created = service.events().insert(calendarId="primary", body=body).execute()
        
        event_data = {
            "id": created.get("id"),
            "title": created.get("summary"),
            "start": created["start"].get("dateTime"),
            "end": created["end"].get("dateTime"),
            "all_day": False,
        }
        
        local_data = load_local_calendar()
        local_events = local_data.get("events", [])
        local_events.append(event_data)
        save_local_calendar(local_events)
        
        log_info(f"Calendar event created: {event.title}", {"date": event.date, "time": f"{event.start_time}-{event.end_time}"})
        
        return {
            "success": True,
            "event": event_data,
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        log_warning(f"Google Calendar create error: {e}, using local fallback")
        
        start_datetime = f"{event.date}T{event.start_time}:00"
        end_datetime = f"{event.date}T{event.end_time}:00"
        
        local_data = load_local_calendar()
        local_events = local_data.get("events", [])
        
        new_event = {
            "id": f"local_{datetime.now(timezone.utc).timestamp()}",
            "title": event.title.strip(),
            "start": start_datetime,
            "end": end_datetime,
            "all_day": False,
            "local": True
        }
        local_events.append(new_event)
        save_local_calendar(local_events)
        
        log_info(f"Calendar event created locally: {event.title}", {"date": event.date, "source": "local"})
        
        return {
            "success": True,
            "event": new_event,
            "source": "local",
            "fallback": True,
            "fallback_reason": str(e)
        }

@app.delete("/api/calendar/{event_id}")
def delete_calendar_event(event_id: str):
    try:
        service = get_google_calendar_service()
        try:
            event = service.events().get(calendarId='primary', eventId=event_id).execute()
        except Exception as e:
            local_data = load_local_calendar()
            local_events = local_data.get("events", [])
            local_event = next((e for e in local_events if e.get("id") == event_id), None)
            if local_event:
                local_events = [e for e in local_events if e.get("id") != event_id]
                save_local_calendar(local_events)
                log_info(f"Local event deleted: {event_id}")
                return {
                    "success": True, 
                    "message": "Local event deleted successfully",
                    "event_id": event_id,
                    "source": "local",
                    "fallback": True
                }
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "Event not found", "event_id": event_id}
            )
        
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        
        local_data = load_local_calendar()
        local_events = local_data.get("events", [])
        local_events = [e for e in local_events if e.get("id") != event_id]
        save_local_calendar(local_events)
        
        log_info(f"Calendar event deleted: {event_id}")
        
        return {
            "success": True, 
            "message": "Event deleted successfully",
            "event_id": event_id,
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        log_error(f"Delete error: {e}")
        local_data = load_local_calendar()
        local_events = local_data.get("events", [])
        local_events = [e for e in local_events if e.get("id") != event_id]
        save_local_calendar(local_events)
        return {
            "success": True,
            "message": "Event deleted from local storage",
            "event_id": event_id,
            "source": "local",
            "fallback": True
        }

# ============================================================
# SYNC LOCAL DATA TO GOOGLE
# ============================================================

@app.post("/api/sync/tasks")
def sync_tasks_to_google():
    try:
        try:
            service = get_google_tasks_service()
            service.tasklists().list(maxResults=1).execute()
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Google Tasks not available", "details": str(e)}
            )
        
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        
        if not local_tasks:
            return {"success": True, "synced": 0, "message": "No local tasks to sync"}
        
        task_list = get_default_task_list(service)
        
        synced = 0
        failed = 0
        
        for task in local_tasks:
            if task.get("local") and not task.get("synced_to_google"):
                try:
                    member = get_member(task.get("member", "unknown"))
                    member_name = member["name"] if member else "Unassigned"
                    
                    created = service.tasks().insert(
                        tasklist=task_list["id"],
                        body={"title": f"[{member_name}] {task['title']}"},
                    ).execute()
                    
                    task["synced_to_google"] = True
                    task["google_id"] = created.get("id")
                    synced += 1
                except Exception as e:
                    failed += 1
        
        save_local_tasks(local_tasks)
        log_success(f"Synced {synced} local tasks to Google")
        
        return {
            "success": True,
            "synced": synced,
            "failed": failed,
            "total": len(local_tasks)
        }
        
    except Exception as e:
        log_error(f"Sync tasks error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/sync/calendar")
def sync_calendar_to_google():
    try:
        try:
            service = get_google_calendar_service()
            service.events().list(calendarId="primary", maxResults=1).execute()
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Google Calendar not available", "details": str(e)}
            )
        
        local_data = load_local_calendar()
        local_events = local_data.get("events", [])
        
        if not local_events:
            return {"success": True, "synced": 0, "message": "No local events to sync"}
        
        synced = 0
        failed = 0
        
        for event in local_events:
            if event.get("local") and not event.get("synced_to_google"):
                try:
                    body = {
                        "summary": event["title"],
                        "start": {"dateTime": event["start"], "timeZone": "Asia/Kolkata"},
                        "end": {"dateTime": event["end"], "timeZone": "Asia/Kolkata"},
                    }
                    created = service.events().insert(calendarId="primary", body=body).execute()
                    
                    event["synced_to_google"] = True
                    event["google_id"] = created.get("id")
                    synced += 1
                except Exception as e:
                    failed += 1
        
        save_local_calendar(local_events)
        log_success(f"Synced {synced} local events to Google")
        
        return {
            "success": True,
            "synced": synced,
            "failed": failed,
            "total": len(local_events)
        }
        
    except Exception as e:
        log_error(f"Sync calendar error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

# ============================================================
# API STATUS ENDPOINT
# ============================================================

@app.get("/api/status/google")
def check_google_status():
    results = {
        "tasks": {"available": False, "error": None},
        "calendar": {"available": False, "error": None},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    try:
        service = get_google_tasks_service()
        service.tasklists().list(maxResults=1).execute()
        results["tasks"]["available"] = True
    except Exception as e:
        results["tasks"]["error"] = str(e)
    
    try:
        service = get_google_calendar_service()
        service.events().list(calendarId="primary", maxResults=1).execute()
        results["calendar"]["available"] = True
    except Exception as e:
        results["calendar"]["error"] = str(e)
    
    return results

# ============================================================
# SCHEDULED TASKS ENDPOINTS
# ============================================================

@app.get("/api/scheduled-tasks")
def get_scheduled_tasks():
    return {"tasks": load_scheduled_tasks().get("tasks", [])}

@app.post("/api/scheduled-tasks")
def create_scheduled_task(task: ScheduledTaskCreate):
    try:
        if not task.title.strip():
            raise Exception("Task title cannot be empty.")
        
        if task.hour < 0 or task.hour > 23 or task.minute < 0 or task.minute > 59:
            raise Exception("Invalid time format.")
        
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        
        new_task = {
            "id": f"sched_{datetime.now(timezone.utc).timestamp()}",
            "title": task.title.strip(),
            "hour": task.hour,
            "minute": task.minute,
            "member": task.member,
            "enabled": task.enabled,
            "label": task.label if task.label != "auto" else f"Daily at {task.hour:02d}:{task.minute:02d}",
            "created": datetime.now(timezone.utc).isoformat(),
            "last_run": None
        }
        tasks.append(new_task)
        save_scheduled_tasks(tasks)
        
        restart_scheduler()
        log_task(f"Scheduled task created: {task.title}", {"time": f"{task.hour:02d}:{task.minute:02d}"})
        
        return {"success": True, "task": new_task}
    except Exception as e:
        log_error(f"Create scheduled task error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.delete("/api/scheduled-tasks/{task_id}")
def delete_scheduled_task(task_id: str):
    try:
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        tasks = [t for t in tasks if t["id"] != task_id]
        save_scheduled_tasks(tasks)
        restart_scheduler()
        log_task(f"Scheduled task deleted: {task_id}")
        return {"success": True}
    except Exception as e:
        log_error(f"Delete scheduled task error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/scheduled-tasks/{task_id}/toggle")
def toggle_scheduled_task(task_id: str):
    try:
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        
        for t in tasks:
            if t["id"] == task_id:
                t["enabled"] = not t.get("enabled", True)
                save_scheduled_tasks(tasks)
                restart_scheduler()
                log_task(f"Scheduled task toggled: {task_id} - {'enabled' if t['enabled'] else 'disabled'}")
                return {"success": True, "enabled": t["enabled"]}
        
        raise Exception("Task not found")
    except Exception as e:
        log_error(f"Toggle scheduled task error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/scheduler/status")
def get_scheduler_status():
    scheduled_tasks = load_scheduled_tasks()
    tasks = scheduled_tasks.get("tasks", [])
    
    now = datetime.now()
    next_runs = []
    
    for task in tasks:
        if not task.get("enabled", True):
            continue
        run_time = datetime(now.year, now.month, now.day, task["hour"], task["minute"])
        if run_time <= now:
            run_time = run_time + timedelta(days=1)
        next_runs.append({
            "id": task["id"],
            "title": task["title"],
            "next_run": run_time.isoformat(),
            "label": task.get("label", "auto")
        })
    
    return {
        "running": _scheduler_running,
        "total_tasks": len(tasks),
        "enabled_tasks": sum(1 for t in tasks if t.get("enabled", True)),
        "next_runs": sorted(next_runs, key=lambda x: x["next_run"])[:5]
    }

# ============================================================
# NEWS - MULTIPLE SOURCES
# ============================================================

def clean_html_text(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = ' '.join(text.split())
    return text

def fetch_news_from_rss(feed_url, max_items=3):
    try:
        feed = feedparser.parse(feed_url)
        articles = []
        
        if not feed.entries:
            return []
        
        for entry in feed.entries[:max_items]:
            try:
                title = entry.get('title', '')
                if title:
                    title = clean_html_text(title)
                    title = ' '.join(title.split())
                    if len(title) > 100:
                        title = title[:97] + '...'
                else:
                    continue
                
                if len(title) < 3 or title.startswith('[') and title.endswith(']'):
                    continue
                
                description = entry.get('description') or entry.get('summary') or ''
                if description:
                    description = clean_html_text(description)
                    description = html.unescape(description)
                    description = ' '.join(description.split())
                    if len(description) > 200:
                        description = description[:197] + '...'
                else:
                    description = "Read more at the source"
                
                source = ''
                if 'source' in entry and hasattr(entry.source, 'title'):
                    source = entry.source.title
                if not source and 'author' in entry:
                    source = entry.author
                if not source:
                    url = entry.get('link', '')
                    if 'timesofindia' in url:
                        source = 'Times of India'
                    elif 'thehindu' in url:
                        source = 'The Hindu'
                    elif 'bbc' in url:
                        source = 'BBC News'
                    else:
                        source = 'News'
                
                published = entry.get('published', '')
                if not published:
                    published = entry.get('updated', '')
                if published:
                    try:
                        from dateutil import parser
                        pub_date = parser.parse(published)
                        published = pub_date.strftime('%b %d, %Y')
                    except:
                        published = 'Recent'
                else:
                    published = 'Recent'
                
                url = entry.get('link', '#')
                
                articles.append({
                    'title': title,
                    'description': description,
                    'source': {'name': source},
                    'url': url,
                    'published': published
                })
            except Exception as e:
                continue
        
        return articles
    except Exception as e:
        return []

def fetch_news_from_newsapi(category_config):
    if not NEWS_API_ENABLED:
        return None
    
    try:
        params = {
            "apiKey": NEWS_API_KEY,
            "pageSize": 5,
            "language": "en"
        }
        if category_config.get("country"):
            params["country"] = category_config["country"]
        else:
            params["category"] = category_config.get("category", "general")
        
        response = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params=params,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok" and data.get("articles"):
                articles = []
                for article in data["articles"][:3]:
                    if article.get("title") and article["title"] != "[Removed]":
                        published = "Recent"
                        if article.get("publishedAt"):
                            try:
                                pub_date = datetime.fromisoformat(article["publishedAt"].replace('Z', '+00:00'))
                                published = pub_date.strftime('%b %d, %Y')
                            except:
                                pass
                        articles.append({
                            "title": article["title"],
                            "description": article.get("description") or f"Read more at {article.get('source', {}).get('name', 'NewsAPI')}",
                            "source": {"name": article.get("source", {}).get("name", "News")},
                            "url": article.get("url", "#"),
                            "published": published
                        })
                return articles
        return None
    except Exception as e:
        return None

def fetch_news_from_multiple_sources(category, category_config):
    all_articles = []
    
    if NEWS_API_ENABLED:
        try:
            articles = fetch_news_from_newsapi(category_config)
            if articles:
                all_articles.extend(articles)
        except Exception as e:
            pass
    
    if len(all_articles) < 3:
        feeds = RSS_FEEDS.get(category, [])
        for feed_url in feeds:
            try:
                articles = fetch_news_from_rss(feed_url, 3)
                if articles:
                    all_articles.extend(articles)
                    if len(all_articles) >= 3:
                        break
            except Exception as e:
                continue
    
    seen_titles = set()
    unique_articles = []
    for article in all_articles:
        title_lower = article['title'].lower()
        if title_lower not in seen_titles:
            seen_titles.add(title_lower)
            unique_articles.append(article)
    
    unique_articles = unique_articles[:3]
    
    if unique_articles:
        return unique_articles
    else:
        return None

def get_fallback_news(error_message=None):
    global _fallback_news_cache
    
    if _fallback_news_cache:
        return _fallback_news_cache
    
    fallback_articles = {
        "india": [
            {"title": "India's economy shows robust growth", "description": "Latest economic indicators show strong performance across all sectors.", "source": {"name": "Economic Times"}, "url": "https://economictimes.indiatimes.com", "published": "Today"},
            {"title": "New infrastructure projects approved across India", "description": "Government announces major infrastructure development projects.", "source": {"name": "Times of India"}, "url": "https://timesofindia.indiatimes.com", "published": "Today"},
            {"title": "India's space program achieves new milestone", "description": "ISRO successfully launches next-generation satellite.", "source": {"name": "The Hindu"}, "url": "https://www.thehindu.com", "published": "Today"}
        ],
        "world": [
            {"title": "Global climate summit reaches historic agreement", "description": "World leaders commit to ambitious emissions reduction targets.", "source": {"name": "BBC News"}, "url": "https://www.bbc.com/news", "published": "Today"},
            {"title": "International trade talks progress on key issues", "description": "Major economies reach consensus on trade regulations.", "source": {"name": "Reuters"}, "url": "https://www.reuters.com", "published": "Today"},
            {"title": "Global technology companies announce new initiatives", "description": "Tech giants collaborate on sustainability and innovation.", "source": {"name": "CNN"}, "url": "https://www.cnn.com", "published": "Today"}
        ],
        "technology": [
            {"title": "AI breakthrough in healthcare diagnostics", "description": "New AI system achieves 99% accuracy in detecting diseases.", "source": {"name": "TechCrunch"}, "url": "https://techcrunch.com", "published": "Today"},
            {"title": "Quantum computing advances with new processor", "description": "Researchers develop more stable quantum processor.", "source": {"name": "Wired"}, "url": "https://www.wired.com", "published": "Today"},
            {"title": "5G network expansion accelerates globally", "description": "Major telecom providers announce 5G coverage expansion.", "source": {"name": "The Verge"}, "url": "https://www.theverge.com", "published": "Today"}
        ],
        "aviation": [
            {"title": "Airbus announces new fuel-efficient aircraft", "description": "Next-generation aircraft promises 20% fuel savings.", "source": {"name": "FlightGlobal"}, "url": "https://www.flightglobal.com", "published": "Today"},
            {"title": "Sustainable aviation fuel production increases", "description": "Major airlines commit to using sustainable fuel.", "source": {"name": "Aviation Weekly"}, "url": "https://aviationweek.com", "published": "Today"},
            {"title": "New international airport opens in Asia", "description": "State-of-the-art facility to boost regional connectivity.", "source": {"name": "Airport World"}, "url": "https://www.airport-world.com", "published": "Today"}
        ]
    }
    
    result = {
        "success": True,
        "articles": fallback_articles,
        "updated": datetime.now(timezone.utc).isoformat(),
        "source": "Fallback News",
        "categories": list(fallback_articles.keys())
    }
    
    if error_message:
        result["error"] = error_message
    
    _fallback_news_cache = result
    return result

@app.get("/api/news")
def get_news():
    global _news_cache, _news_cache_time
    
    try:
        if _news_cache and _news_cache_time:
            elapsed = (datetime.now(timezone.utc) - _news_cache_time).total_seconds()
            if elapsed < _news_cache_duration:
                return _news_cache

        results = {}
        success_count = 0
        
        for category, config in NEWS_CATEGORIES.items():
            articles = fetch_news_from_multiple_sources(category, config)
            
            if articles:
                results[category] = articles
                success_count += 1
            else:
                fallback = get_fallback_news()
                results[category] = fallback["articles"].get(category, [])
        
        has_results = any(len(articles) > 0 for articles in results.values())
        if not has_results:
            return get_fallback_news("No news available")
        
        source_info = "Multiple Sources"
        if NEWS_API_ENABLED:
            source_info += " (NewsAPI + RSS)"
        else:
            source_info += " (RSS Feeds)"
        
        _news_cache = {
            "success": True,
            "articles": results,
            "updated": datetime.now(timezone.utc).isoformat(),
            "source": source_info,
            "categories": list(results.keys())
        }
        _news_cache_time = datetime.now(timezone.utc)
        
        return _news_cache
        
    except Exception as e:
        log_error(f"News error: {e}")
        return get_fallback_news(str(e))

# ============================================================
# WEATHER
# ============================================================

def get_weather_from_openweather():
    if not OPENWEATHER_API_KEY:
        return None
    
    try:
        current_url = "https://api.openweathermap.org/data/2.5/weather"
        current_params = {
            "lat": WEATHER_LAT,
            "lon": WEATHER_LON,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric"
        }
        current_response = requests.get(current_url, params=current_params, timeout=10)
        
        if current_response.status_code != 200:
            return None
        
        current_data = current_response.json()
        
        forecast_url = "https://api.openweathermap.org/data/2.5/forecast"
        forecast_params = {
            "lat": WEATHER_LAT,
            "lon": WEATHER_LON,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric"
        }
        forecast_response = requests.get(forecast_url, params=forecast_params, timeout=10)
        
        rain_probability = 0
        if forecast_response.status_code == 200:
            forecast_data = forecast_response.json()
            if forecast_data.get("list") and len(forecast_data["list"]) > 0:
                for item in forecast_data["list"][:6]:
                    if item.get("pop", 0) > rain_probability:
                        rain_probability = item.get("pop", 0) * 100
        
        weather_code = current_data.get("weather", [{}])[0].get("id", 800)
        is_day = True
        try:
            sunrise = current_data.get("sys", {}).get("sunrise", 0)
            sunset = current_data.get("sys", {}).get("sunset", 0)
            if sunrise and sunset:
                current_time = datetime.now().timestamp()
                is_day = sunrise <= current_time <= sunset
        except:
            pass
        
        return {
            "temperature": round(current_data.get("main", {}).get("temp", 0)),
            "feels_like": round(current_data.get("main", {}).get("feels_like", 0)),
            "humidity": round(current_data.get("main", {}).get("humidity", 0)),
            "wind_speed": round(current_data.get("wind", {}).get("speed", 0)),
            "weather_code": weather_code,
            "weather_text": current_data.get("weather", [{}])[0].get("description", "Unknown"),
            "rain_probability": round(rain_probability),
            "is_day": is_day,
            "cloud_cover": current_data.get("clouds", {}).get("all", 0),
            "pressure": current_data.get("main", {}).get("pressure", 0),
        }
        
    except Exception as e:
        return None

def weather_icon_owm(code, is_day=True):
    if code >= 200 and code < 300:
        return "⛈️"
    elif code >= 300 and code < 400:
        return "🌦️"
    elif code >= 500 and code < 600:
        return "🌧️"
    elif code >= 600 and code < 700:
        return "❄️"
    elif code >= 700 and code < 800:
        return "🌫️"
    elif code == 800:
        return "☀️" if is_day else "🌙"
    elif code == 801:
        return "🌤️" if is_day else "🌙"
    elif code == 802:
        return "⛅"
    elif code == 803 or code == 804:
        return "☁️"
    else:
        return "🌤️"

@app.get("/api/weather")
def get_weather():
    global _weather_cache, _weather_cache_time
    
    try:
        if _weather_cache and _weather_cache_time:
            elapsed = (datetime.now(timezone.utc) - _weather_cache_time).total_seconds()
            if elapsed < _weather_cache_duration:
                return _weather_cache
        
        weather_data = get_weather_from_openweather()
        
        if not weather_data:
            return get_weather_from_openmeteo()
        
        result = {
            "success": True,
            "temperature": weather_data["temperature"],
            "feels_like": weather_data["feels_like"],
            "humidity": weather_data["humidity"],
            "wind_speed": weather_data["wind_speed"],
            "weather_code": weather_data["weather_code"],
            "weather_text": weather_data["weather_text"],
            "rain_probability": weather_data["rain_probability"],
            "is_day": weather_data["is_day"],
            "cloud_cover": weather_data["cloud_cover"],
            "pressure": weather_data["pressure"],
            "icon": weather_icon_owm(weather_data["weather_code"], weather_data["is_day"]),
            "updated": datetime.now(timezone.utc).isoformat(),
            "source": "OpenWeatherMap"
        }
        
        _weather_cache = result
        _weather_cache_time = datetime.now(timezone.utc)
        return result
        
    except Exception as e:
        return get_weather_from_openmeteo()

def get_weather_from_openmeteo():
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={WEATHER_LAT}"
            f"&longitude={WEATHER_LON}"
            "&current=temperature_2m,"
            "relative_humidity_2m,"
            "apparent_temperature,"
            "weather_code,"
            "wind_speed_10m,"
            "cloud_cover"
            "&hourly=precipitation_probability"
            "&forecast_days=1"
            "&timezone=auto"
        )
        response = requests.get(url, timeout=10)
        if not response.ok:
            raise Exception("Open-Meteo unavailable")
        data = response.json()
        current = data.get("current", {})
        
        weather_code = current.get("weather_code", 1)
        is_day = True
        hour = datetime.now().hour
        if hour < 6 or hour > 18:
            is_day = False
        
        rain_probability = 0
        if data.get("hourly") and data["hourly"].get("precipitation_probability"):
            current_hour = datetime.now().hour
            if current_hour < len(data["hourly"]["precipitation_probability"]):
                rain_probability = data["hourly"]["precipitation_probability"][current_hour]
        
        return {
            "success": True,
            "temperature": round(current.get("temperature_2m", 0)),
            "feels_like": round(current.get("apparent_temperature", 0)),
            "humidity": round(current.get("relative_humidity_2m", 0)),
            "wind_speed": round(current.get("wind_speed_10m", 0)),
            "weather_code": weather_code,
            "weather_text": "Unknown",
            "rain_probability": rain_probability,
            "is_day": is_day,
            "cloud_cover": current.get("cloud_cover", 0),
            "pressure": 0,
            "icon": weather_icon_owm(weather_code, is_day),
            "updated": datetime.now(timezone.utc).isoformat(),
            "source": "Open-Meteo (Fallback)"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "temperature": 0,
            "feels_like": 0,
            "humidity": 0,
            "wind_speed": 0,
            "weather_code": 800,
            "weather_text": "Weather unavailable",
            "rain_probability": 0,
            "is_day": True,
            "cloud_cover": 0,
            "pressure": 0,
            "icon": "🌤️",
            "updated": datetime.now(timezone.utc).isoformat(),
            "source": "Error"
        }

# ============================================================
# GROCERY LIST
# ============================================================

_grocery_file = BASE_DIR / "grocery.json"

def load_grocery_from_file():
    try:
        if _grocery_file.exists():
            with open(_grocery_file, 'r') as f:
                data = json.load(f)
                return data.get("items", [])
        return []
    except Exception as e:
        return []

def save_grocery_to_file(items):
    try:
        with open(_grocery_file, 'w') as f:
            json.dump({"items": items, "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
        return True
    except Exception as e:
        return False

grocery_items = load_grocery_from_file()

@app.get("/api/grocery")
def get_grocery():
    return {"items": grocery_items}

@app.post("/api/grocery")
def create_grocery_item(request: dict):
    try:
        title = request.get("title", "").strip()
        category = request.get("category", "other")
        if not title:
            raise Exception("Item cannot be empty")
        new_item = {
            "id": f"grocery_{datetime.now(timezone.utc).timestamp()}",
            "title": title,
            "category": category,
            "completed": False,
            "created": datetime.now(timezone.utc).isoformat()
        }
        grocery_items.append(new_item)
        save_grocery_to_file(grocery_items)
        return {"success": True, "item": new_item}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/grocery/{item_id}/complete")
def complete_grocery_item(item_id: str):
    try:
        for item in grocery_items:
            if item["id"] == item_id:
                item["completed"] = True
                save_grocery_to_file(grocery_items)
                return {"success": True, "item": item}
        raise Exception("Item not found")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/grocery/{item_id}/uncomplete")
def uncomplete_grocery_item(item_id: str):
    try:
        for item in grocery_items:
            if item["id"] == item_id:
                item["completed"] = False
                save_grocery_to_file(grocery_items)
                return {"success": True, "item": item}
        raise Exception("Item not found")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================================
# YOUTUBE SEARCH
# ============================================================

@app.get("/api/youtube/search")
def youtube_search(q: str):
    try:
        query = q.strip()
        if not query:
            return {"results": []}
        if not YOUTUBE_API_KEY:
            raise Exception("YouTube API key is not configured.")
        
        search_response = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "videoCategoryId": "10",
                "maxResults": 15,
                "key": YOUTUBE_API_KEY,
            },
            timeout=10,
        )
        if not search_response.ok:
            error_msg = None
            try:
                error_data = search_response.json()
                error_msg = error_data.get("error", {}).get("message")
            except:
                pass
            raise Exception(error_msg or f"YouTube search failed ({search_response.status_code})")
        
        search_data = search_response.json()
        video_ids = []
        snippets = {}
        for item in search_data.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue
            video_ids.append(video_id)
            snippets[video_id] = item.get("snippet", {})
        
        if not video_ids:
            return {"results": []}
        
        videos_response = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,status",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY,
            },
            timeout=10,
        )
        if not videos_response.ok:
            error_msg = None
            try:
                error_data = videos_response.json()
                error_msg = error_data.get("error", {}).get("message")
            except:
                pass
            raise Exception(error_msg or f"YouTube video lookup failed ({videos_response.status_code})")
        
        videos_data = videos_response.json()
        results = []
        for video in videos_data.get("items", []):
            video_id = video.get("id")
            status = video.get("status", {})
            if not status.get("embeddable", False):
                continue
            snippet = video.get("snippet") or snippets.get(video_id, {})
            thumbnails = snippet.get("thumbnails", {})
            thumbnail = thumbnails.get("medium", {}).get("url") or thumbnails.get("default", {}).get("url")
            results.append({
                "id": video_id,
                "title": snippet.get("title", "Unknown title"),
                "channel": snippet.get("channelTitle", "Unknown channel"),
                "thumbnail": thumbnail,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "embeddable": True,
            })
        return {"results": results}
    except Exception as e:
        log_error(f"YouTube search error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================================
# STARTUP - REGISTER mDNS
# ============================================================

mdns_zeroconf = None

def register_mdns():
    global mdns_zeroconf
    
    if not MDNS_AVAILABLE:
        return None
    
    try:
        from zeroconf import ServiceInfo, Zeroconf
        import socket
        
        print("📡 Registering mDNS with Bonjour...")
        zeroconf = Zeroconf()
        
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            print(f"   Primary IP: {local_ip}")
        except Exception as e:
            local_ip = "127.0.0.1"
        
        try:
            service_info = ServiceInfo(
                "_http._tcp.local.",
                "Smart Fridge._http._tcp.local.",
                addresses=[socket.inet_aton(local_ip)],
                port=8000,
                properties={
                    "path": "/",
                    "name": "Smart Fridge Dashboard",
                    "version": "1.0"
                },
            )
            zeroconf.register_service(service_info)
            print(f"✅ mDNS registered: http://{local_ip}:8000")
            mdns_zeroconf = zeroconf
            log_system(f"mDNS registered at {local_ip}:8000")
            return zeroconf
        except Exception as e:
            print(f"⚠️ Failed to register mDNS: {e}")
            return None
        
    except Exception as e:
        print(f"❌ Failed to register mDNS: {e}")
        return None

try:
    mdns_zeroconf = register_mdns()
except Exception as e:
    print(f"⚠️ mDNS registration skipped: {e}")

# ============================================================
# SHUTDOWN - Clean up mDNS
# ============================================================

import atexit

def cleanup_mdns():
    global mdns_zeroconf
    if mdns_zeroconf:
        try:
            mdns_zeroconf.close()
            print("✅ mDNS unregistered")
        except Exception as e:
            print(f"⚠️ Error unregistering mDNS: {e}")
    
    log_system("Smart Fridge Dashboard stopped")

atexit.register(cleanup_mdns)

# ============================================================
# SERVE CUSTOM AUDIO FILES
# ============================================================

@app.get("/api/audio/{filename}")
def get_audio(filename: str):
    audio_path = BASE_DIR / "static" / filename
    if audio_path.exists():
        return FileResponse(
            audio_path,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "public, max-age=86400",
            }
        )
    return JSONResponse(
        status_code=404,
        content={"error": "Audio file not found"}
    )

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*50)
    print("🏠 SMART FRIDGE DASHBOARD")
    print("="*50)
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"\n📱 Access the dashboard at:")
        print(f"   → http://{local_ip}:8000")
        print(f"   → http://localhost:8000")
    except:
        print("\n📱 Access the dashboard at: http://localhost:8000")
    
    print("\n" + "="*50)
    print("🚀 Starting server...\n")
    
    log_system(f"Server started on port 8000", {"host": "0.0.0.0", "port": 8000})
    
    uvicorn.run(app, host="0.0.0.0", port=8000)