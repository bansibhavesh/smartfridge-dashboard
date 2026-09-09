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
import random
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
# ENVIRONMENT DETECTION
# ============================================================

IS_RENDER = os.environ.get("RENDER", "false").lower() == "true"

if IS_RENDER:
    print("🌐 Running on Render.com")
else:
    print("💻 Running locally")

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

if SMARTTHINGS_ENABLED:
    print("✅ SmartThings configuration loaded")
else:
    print("⚠️ SmartThings not configured - token or device ID missing")

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

# Cycle code mapping - Updated with actual codes from your washing machine
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
    """Map cycle codes to readable names."""
    cycle_map = {
        "1B": "Cotton",
        "35": "Synthetics",
        "1D": "Delicates",
        "A0": "Quick Wash",
        "B0": "Rinse + Spin",
        "25": "Spin Only",
        "22": "Baby Care",
        "96": "Wool",
        "20": "Outdoor",
        "65": "Sports Wear",
        "33": "Shirts",
        "23": "Denim",
        "24": "Towels",
        "26": "Bedding",
        "2F": "Drain + Spin",
        "2E": "Rinse + Spin",
        "30": "Silent Wash",
        "2D": "15 Min Quick",
        "36": "Hand Wash",
        "38": "Cloudy Day",
        "37": "Allergy Care",
        "29": "Drum Clean",
        "27": "Blouses",
        "28": "Curtains",
    }
    return cycle_map.get(code, f"Cycle {code}")

# ============================================================
# OPENSKY OAUTH2 TOKEN MANAGER (FIXED FOR RENDER)
# ============================================================

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID")
OPENSKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET")

if OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET:
    print("✅ Loaded OpenSky credentials from environment variables")
    HAS_VALID_CREDENTIALS = True
else:
    # Try loading from file (local development)
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
    print("⚠️ No OpenSky credentials found - using unauthenticated API")
    HAS_VALID_CREDENTIALS = False
else:
    HAS_VALID_CREDENTIALS = True

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
        if not self._has_valid_credentials:
            return None
            
        try:
            print("🔄 Refreshing OpenSky token...")
            
            # Longer timeout for Render
            timeout = 30 if IS_RENDER else 10
            
            r = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": OPENSKY_CLIENT_ID,
                    "client_secret": OPENSKY_CLIENT_SECRET,
                },
                timeout=timeout,
            )
            
            if r.status_code == 401:
                print("❌ Authentication failed! Check your OpenSky credentials.")
                self._has_valid_credentials = False
                return None
                
            r.raise_for_status()
            data = r.json()
            self.token = data["access_token"]
            expires_in = data.get("expires_in", 1800)
            self.expires_at = datetime.now() + timedelta(seconds=expires_in - TOKEN_REFRESH_MARGIN)
            print(f"✅ OpenSky token refreshed successfully")
            return self.token
            
        except requests.exceptions.Timeout:
            print(f"⚠️ OpenSky auth timeout (connection to auth.opensky-network.org took too long)")
            print("   Using unauthenticated API as fallback")
            self._has_valid_credentials = False
            return None
            
        except requests.exceptions.ConnectionError:
            print(f"⚠️ OpenSky auth connection error - server unreachable")
            print("   Using unauthenticated API as fallback")
            self._has_valid_credentials = False
            return None
            
        except Exception as e:
            print(f"❌ Failed to get OpenSky token: {e}")
            self._has_valid_credentials = False
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
# ATC / OPENSKY
# ============================================================

ATC_LAT = 17.3542
ATC_LON = 78.4100
ATC_LAT_RADIUS = 7.0
ATC_LON_RADIUS = 7.0
ATC_MAX_AIRCRAFT = 20

_atc_cache = None
_atc_cache_time = None
_atc_cache_duration = 60
_atc_rate_limit_until = None

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
    """Load tasks from local JSON file."""
    try:
        if LOCAL_TASKS_FILE.exists():
            with open(LOCAL_TASKS_FILE, 'r') as f:
                return json.load(f)
        return {"tasks": [], "last_sync": None}
    except Exception as e:
        print(f"⚠️ Error loading local tasks: {e}")
        return {"tasks": [], "last_sync": None}

def save_local_tasks(tasks):
    """Save tasks to local JSON file."""
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
    """Load calendar events from local JSON file."""
    try:
        if LOCAL_CALENDAR_FILE.exists():
            with open(LOCAL_CALENDAR_FILE, 'r') as f:
                return json.load(f)
        return {"events": [], "last_sync": None}
    except Exception as e:
        print(f"⚠️ Error loading local calendar: {e}")
        return {"events": [], "last_sync": None}

def save_local_calendar(events):
    """Save calendar events to local JSON file."""
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
    """Load scheduled tasks from JSON file."""
    try:
        if SCHEDULED_TASKS_FILE.exists():
            with open(SCHEDULED_TASKS_FILE, 'r') as f:
                return json.load(f)
        return {"tasks": []}
    except Exception as e:
        print(f"⚠️ Error loading scheduled tasks: {e}")
        return {"tasks": []}

def save_scheduled_tasks(tasks):
    """Save scheduled tasks to JSON file."""
    try:
        with open(SCHEDULED_TASKS_FILE, 'w') as f:
            json.dump({"tasks": tasks}, f, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ Error saving scheduled tasks: {e}")
        return False

def initialize_default_scheduled_task():
    """Add the default 3 PM task if it doesn't exist."""
    scheduled_tasks = load_scheduled_tasks()
    tasks = scheduled_tasks.get("tasks", [])
    
    # Check if 3 PM task already exists
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

# ============================================================
# SCHEDULER
# ============================================================

_scheduler_thread = None
_scheduler_running = False
_scheduler_lock = threading.Lock()

def create_task_via_api(title, member_id="bhavesh"):
    """Create a task via the API."""
    try:
        # Try Google API first
        service = get_google_tasks_service()
        task_list = get_default_task_list(service)
        
        # Get member name
        member = get_member(member_id)
        member_name = member["name"] if member else "Unassigned"
        
        created = service.tasks().insert(
            tasklist=task_list["id"],
            body={"title": f"[{member_name}] {title}"},
        ).execute()
        
        parsed = parse_task(created)
        
        # Also save to local
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        local_tasks.append(parsed)
        save_local_tasks(local_tasks)
        
        print(f"✅ Scheduled task created: {title}")
        return {"success": True, "task": parsed}
        
    except Exception as e:
        print(f"⚠️ Scheduled task creation failed (Google): {e}")
        
        # Fallback to local
        try:
            member = get_member(member_id)
            local_data = load_local_tasks()
            local_tasks = local_data.get("tasks", [])
            
            new_task = {
                "id": f"local_{datetime.now(timezone.utc).timestamp()}",
                "title": title,
                "member": member_id,
                "member_name": member["name"] if member else member_id,
                "member_emoji": member["emoji"] if member else "👤",
                "task_list": "Local Tasks",
                "local": True
            }
            local_tasks.append(new_task)
            save_local_tasks(local_tasks)
            
            print(f"✅ Scheduled task saved locally: {title}")
            return {"success": True, "task": new_task, "fallback": True}
        except Exception as e2:
            print(f"❌ Failed to create scheduled task: {e2}")
            return {"success": False, "error": str(e2)}

def run_scheduled_tasks():
    """Check and run scheduled tasks."""
    try:
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        now = datetime.now()
        
        for task in tasks:
            if not task.get("enabled", True):
                continue
            
            # Check if it's time to run
            if now.hour == task["hour"] and now.minute == task["minute"]:
                # Check if already ran today
                last_run = task.get("last_run")
                if last_run:
                    last_run_date = datetime.fromisoformat(last_run).date()
                    if last_run_date == now.date():
                        continue  # Already ran today
                
                # Create the task
                title = task["title"]
                if task.get("label") and task.get("label") != "auto":
                    title = f"[{task['label']}] {title}"
                
                result = create_task_via_api(title, task.get("member", "bhavesh"))
                
                if result["success"]:
                    # Update last_run
                    scheduled_tasks = load_scheduled_tasks()
                    tasks = scheduled_tasks.get("tasks", [])
                    for t in tasks:
                        if t["id"] == task["id"]:
                            t["last_run"] = now.isoformat()
                            break
                    save_scheduled_tasks(tasks)
                    
    except Exception as e:
        print(f"⚠️ Scheduler error: {e}")

def scheduler_loop():
    """Main scheduler loop."""
    global _scheduler_running
    print("🔄 Scheduler started")
    
    while _scheduler_running:
        try:
            run_scheduled_tasks()
        except Exception as e:
            print(f"⚠️ Scheduler loop error: {e}")
        
        # Sleep for 30 seconds before checking again
        for _ in range(30):
            if not _scheduler_running:
                break
            time.sleep(1)

def start_scheduler():
    """Start the scheduler in a background thread."""
    global _scheduler_thread, _scheduler_running
    
    with _scheduler_lock:
        if _scheduler_running:
            print("ℹ️ Scheduler already running")
            return
        
        _scheduler_running = True
        _scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        _scheduler_thread.start()
        print("✅ Scheduler started")

def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler_running, _scheduler_thread
    
    with _scheduler_lock:
        if not _scheduler_running:
            return
        
        _scheduler_running = False
        if _scheduler_thread:
            _scheduler_thread.join(timeout=2)
        print("✅ Scheduler stopped")

def restart_scheduler():
    """Restart the scheduler."""
    stop_scheduler()
    start_scheduler()

# Start scheduler on startup
start_scheduler()
initialize_default_scheduled_task()

# ============================================================
# GOOGLE AUTH
# ============================================================

def get_google_credentials():
    """Get Google credentials from environment variables or local files."""
    creds = None
    
    # Try environment variables first (for Render)
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    token_json = os.getenv("GOOGLE_TOKEN_JSON")
    
    if creds_json and token_json:
        try:
            print("🔑 Loading Google credentials from environment variables...")
            creds_dict = json.loads(creds_json)
            token_dict = json.loads(token_json)
            
            # Create credentials from stored info
            creds = Credentials(
                token=token_dict.get("token"),
                refresh_token=token_dict.get("refresh_token"),
                token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=creds_dict.get("installed", {}).get("client_id") or creds_dict.get("web", {}).get("client_id"),
                client_secret=creds_dict.get("installed", {}).get("client_secret") or creds_dict.get("web", {}).get("client_secret"),
                scopes=SCOPES
            )
            
            # Refresh if expired
            if creds.expired and creds.refresh_token:
                print("🔄 Refreshing Google token...")
                creds.refresh(Request())
            
            print("✅ Google credentials loaded from environment")
            return creds
        except Exception as e:
            print(f"⚠️ Failed to load Google credentials from env: {e}")
    
    # Fallback to local files (for local development)
    if TOKEN_FILE.exists():
        try:
            print("📁 Loading Google credentials from local files...")
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            if creds and creds.valid:
                print("✅ Google credentials loaded from local files")
                return creds
        except Exception as e:
            print(f"⚠️ Failed to load from token.json: {e}")
            creds = None
    
    # If no credentials, try OAuth flow (local only)
    if not creds or not creds.valid:
        if not CREDENTIALS_FILE.exists():
            print("⚠️ credentials.json not found. Using local storage only.")
            return None
        try:
            print("🔐 Starting OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            print("✅ OAuth flow completed successfully")
            return creds
        except Exception as e:
            print(f"⚠️ OAuth flow failed: {e}")
            return None
    
    return creds

def get_google_tasks_service():
    try:
        return build("tasks", "v1", credentials=get_google_credentials())
    except Exception as e:
        print(f"⚠️ Google Tasks service unavailable: {e}")
        return None

def get_google_calendar_service():
    try:
        return build("calendar", "v3", credentials=get_google_credentials())
    except Exception as e:
        print(f"⚠️ Google Calendar service unavailable: {e}")
        return None

# ============================================================
# TASK HELPERS
# ============================================================

def get_default_task_list(service):
    if not service:
        return None
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
        print(f"⚠️ System status error: {e}")
        return {"status": "error", "error": str(e)}

@app.get("/api/system/status")
def system_status():
    return get_system_status()

# ============================================================
# SMARTTHINGS - WASHING MACHINE STATUS
# ============================================================

def get_washer_status_from_api():
    """Fetch washing machine status from SmartThings API."""
    if not SMARTTHINGS_ENABLED:
        print("⚠️ SmartThings not configured - token or device ID missing")
        return None
    
    try:
        headers = {
            "Authorization": f"Bearer {SMARTTHINGS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Get device status
        url = f"https://api.smartthings.com/v1/devices/{SMARTTHINGS_DEVICE_ID}/status"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"⚠️ SmartThings API error: {response.status_code}")
            if response.status_code == 401:
                print("   Invalid token - please check SMARTTHINGS_TOKEN")
            return None
        
        data = response.json()
        status = parse_washer_status(data)
        return status
        
    except requests.exceptions.Timeout:
        print("⚠️ SmartThings API timeout")
        return None
    except Exception as e:
        print(f"⚠️ SmartThings error: {e}")
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
        
        # Determine cycle name - FIXED: removed duplicate emoji
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
        print(f"⚠️ Error parsing washer status: {e}")
        import traceback
        traceback.print_exc()
        return None

@app.get("/api/smartthings/washer")
def get_washer_status():
    """Get washing machine status from SmartThings."""
    global _smartthings_cache, _smartthings_cache_time
    
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
                print(f"🧺 Returning cached washer status ({elapsed:.1f}s old)")
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
            return {
                "success": False,
                "enabled": True,
                "error": "Failed to get washer status"
            }
            
    except Exception as e:
        print(f"❌ Washer status error: {e}")
        return {
            "success": False,
            "enabled": True,
            "error": str(e)
        }

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
        
        # Get device info
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
    """Discovery page to help find the dashboard."""
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
        # Try Google API first
        service = get_google_tasks_service()
        if not service:
            raise Exception("Google Tasks service unavailable")
            
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
        
        # Sync to local as backup
        save_local_tasks(all_tasks)
        
        return {
            "tasks": all_tasks,
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        print(f"⚠️ Google Tasks error: {e}, using local fallback")
        
        # Fallback to local storage
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
        
        # Try Google API first
        service = get_google_tasks_service()
        if service:
            task_list = get_default_task_list(service)
            if task_list:
                created = service.tasks().insert(
                    tasklist=task_list["id"],
                    body={"title": make_task_title(task.title, task.member)},
                ).execute()
                
                parsed = parse_task(created)
                
                # Also save to local
                local_data = load_local_tasks()
                local_tasks = local_data.get("tasks", [])
                local_tasks.append(parsed)
                save_local_tasks(local_tasks)
                
                return {
                    "success": True, 
                    "task": parsed,
                    "source": "google",
                    "fallback": False
                }
        
        # Fallback to local storage
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
        
        return {
            "success": True,
            "task": new_task,
            "source": "local",
            "fallback": True,
            "fallback_reason": "Google service unavailable"
        }
        
    except Exception as e:
        print(f"⚠️ Task create error: {e}, using local fallback")
        
        # Fallback to local storage
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
        # Try Google API first
        service = get_google_tasks_service()
        if service:
            task_list = get_default_task_list(service)
            if task_list:
                result = service.tasks().patch(
                    tasklist=task_list["id"],
                    task=task_id,
                    body={"status": "completed"},
                ).execute()
                
                # Remove from local if it exists
                local_data = load_local_tasks()
                local_tasks = local_data.get("tasks", [])
                local_tasks = [t for t in local_tasks if t.get("id") != task_id]
                save_local_tasks(local_tasks)
                
                return {
                    "success": True, 
                    "task": result["id"],
                    "source": "google",
                    "fallback": False
                }
        
        # Fallback: remove from local
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        local_tasks = [t for t in local_tasks if t.get("id") != task_id]
        save_local_tasks(local_tasks)
        
        return {
            "success": True,
            "task": task_id,
            "source": "local",
            "fallback": True,
            "fallback_reason": "Google service unavailable"
        }
        
    except Exception as e:
        print(f"⚠️ Task complete error: {e}")
        
        # Fallback: remove from local
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        local_tasks = [t for t in local_tasks if t.get("id") != task_id]
        save_local_tasks(local_tasks)
        
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
        # Try Google API first
        service = get_google_calendar_service()
        if not service:
            raise Exception("Google Calendar service unavailable")
            
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
        
        # Sync to local as backup
        save_local_calendar(events)
        
        return {
            "events": events,
            "source": "google",
            "fallback": False
        }
        
    except Exception as e:
        print(f"⚠️ Google Calendar error: {e}, using local fallback")
        
        # Fallback to local storage
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
        
        # Try Google API first
        service = get_google_calendar_service()
        if service:
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
            
            # Also save to local
            local_data = load_local_calendar()
            local_events = local_data.get("events", [])
            local_events.append(event_data)
            save_local_calendar(local_events)
            
            return {
                "success": True,
                "event": event_data,
                "source": "google",
                "fallback": False
            }
        
        # Fallback to local storage
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
        
        return {
            "success": True,
            "event": new_event,
            "source": "local",
            "fallback": True,
            "fallback_reason": "Google service unavailable"
        }
        
    except Exception as e:
        print(f"⚠️ Calendar create error: {e}")
        
        # Fallback to local storage
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
        # Try Google API first
        service = get_google_calendar_service()
        if service:
            try:
                event = service.events().get(calendarId='primary', eventId=event_id).execute()
                print(f"✅ Found event: '{event.get('summary', 'Untitled')}' (ID: {event_id})")
                service.events().delete(calendarId='primary', eventId=event_id).execute()
                print(f"✅ Event deleted successfully: {event_id}")
                
                # Also remove from local
                local_data = load_local_calendar()
                local_events = local_data.get("events", [])
                local_events = [e for e in local_events if e.get("id") != event_id]
                save_local_calendar(local_events)
                
                return {
                    "success": True, 
                    "message": "Event deleted successfully",
                    "event_id": event_id,
                    "source": "google",
                    "fallback": False
                }
            except Exception as e:
                print(f"⚠️ Event not found in Google: {e}")
                # Check if it's a local event
                local_data = load_local_calendar()
                local_events = local_data.get("events", [])
                local_event = next((e for e in local_events if e.get("id") == event_id), None)
                if local_event:
                    local_events = [e for e in local_events if e.get("id") != event_id]
                    save_local_calendar(local_events)
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
        
        # Try local deletion as fallback
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
        
    except Exception as e:
        print(f"❌ Delete error: {e}")
        # Try local deletion as fallback
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
# SYNC LOCAL DATA TO GOOGLE (When API comes back online)
# ============================================================

@app.post("/api/sync/tasks")
def sync_tasks_to_google():
    """Sync local tasks to Google when it comes back online."""
    try:
        # Check if Google is available
        service = get_google_tasks_service()
        if not service:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Google Tasks not available"}
            )
        
        service.tasklists().list(maxResults=1).execute()
        
        # Load local tasks
        local_data = load_local_tasks()
        local_tasks = local_data.get("tasks", [])
        
        if not local_tasks:
            return {"success": True, "synced": 0, "message": "No local tasks to sync"}
        
        # Get default task list
        task_list = get_default_task_list(service)
        if not task_list:
            return {"success": False, "error": "No task list found"}
        
        synced = 0
        failed = 0
        
        for task in local_tasks:
            if task.get("local") and not task.get("synced_to_google"):
                try:
                    # Create the task in Google
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
                    print(f"⚠️ Failed to sync task: {e}")
                    failed += 1
        
        # Save updated local tasks
        save_local_tasks(local_tasks)
        
        return {
            "success": True,
            "synced": synced,
            "failed": failed,
            "total": len(local_tasks)
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/sync/calendar")
def sync_calendar_to_google():
    """Sync local calendar events to Google when it comes back online."""
    try:
        # Check if Google is available
        service = get_google_calendar_service()
        if not service:
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Google Calendar not available"}
            )
        
        service.events().list(calendarId="primary", maxResults=1).execute()
        
        # Load local events
        local_data = load_local_calendar()
        local_events = local_data.get("events", [])
        
        if not local_events:
            return {"success": True, "synced": 0, "message": "No local events to sync"}
        
        synced = 0
        failed = 0
        
        for event in local_events:
            if event.get("local") and not event.get("synced_to_google"):
                try:
                    # Create the event in Google
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
                    print(f"⚠️ Failed to sync event: {e}")
                    failed += 1
        
        # Save updated local events
        save_local_calendar(local_events)
        
        return {
            "success": True,
            "synced": synced,
            "failed": failed,
            "total": len(local_events)
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

# ============================================================
# API STATUS ENDPOINT - Check if Google APIs are working
# ============================================================

@app.get("/api/status/google")
def check_google_status():
    """Check if Google APIs are available."""
    results = {
        "tasks": {"available": False, "error": None},
        "calendar": {"available": False, "error": None},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Check Tasks
    try:
        service = get_google_tasks_service()
        if service:
            service.tasklists().list(maxResults=1).execute()
            results["tasks"]["available"] = True
    except Exception as e:
        results["tasks"]["error"] = str(e)
    
    # Check Calendar
    try:
        service = get_google_calendar_service()
        if service:
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
    """Get all scheduled tasks."""
    return {"tasks": load_scheduled_tasks().get("tasks", [])}

@app.post("/api/scheduled-tasks")
def create_scheduled_task(task: ScheduledTaskCreate):
    """Create a new scheduled task."""
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
        
        # Restart scheduler to pick up new task
        restart_scheduler()
        
        return {"success": True, "task": new_task}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.put("/api/scheduled-tasks/{task_id}")
def update_scheduled_task(task_id: str, task: ScheduledTaskCreate):
    """Update a scheduled task."""
    try:
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        
        for t in tasks:
            if t["id"] == task_id:
                t["title"] = task.title.strip()
                t["hour"] = task.hour
                t["minute"] = task.minute
                t["member"] = task.member
                t["enabled"] = task.enabled
                t["label"] = task.label if task.label != "auto" else f"Daily at {task.hour:02d}:{task.minute:02d}"
                save_scheduled_tasks(tasks)
                restart_scheduler()
                return {"success": True, "task": t}
        
        raise Exception("Task not found")
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.delete("/api/scheduled-tasks/{task_id}")
def delete_scheduled_task(task_id: str):
    """Delete a scheduled task."""
    try:
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        tasks = [t for t in tasks if t["id"] != task_id]
        save_scheduled_tasks(tasks)
        restart_scheduler()
        return {"success": True}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/scheduled-tasks/{task_id}/toggle")
def toggle_scheduled_task(task_id: str):
    """Enable/disable a scheduled task."""
    try:
        scheduled_tasks = load_scheduled_tasks()
        tasks = scheduled_tasks.get("tasks", [])
        
        for t in tasks:
            if t["id"] == task_id:
                t["enabled"] = not t.get("enabled", True)
                save_scheduled_tasks(tasks)
                restart_scheduler()
                return {"success": True, "enabled": t["enabled"]}
        
        raise Exception("Task not found")
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/scheduler/status")
def get_scheduler_status():
    """Get scheduler status."""
    scheduled_tasks = load_scheduled_tasks()
    tasks = scheduled_tasks.get("tasks", [])
    
    # Calculate next runs
    now = datetime.now()
    next_runs = []
    
    for task in tasks:
        if not task.get("enabled", True):
            continue
        # Calculate next run time
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
    """Remove HTML tags from text."""
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode HTML entities
    text = html.unescape(text)
    # Remove extra whitespace
    text = ' '.join(text.split())
    return text

def fetch_news_from_rss(feed_url, max_items=3):
    """Fetch news from an RSS feed."""
    try:
        feed = feedparser.parse(feed_url)
        articles = []
        
        for entry in feed.entries[:max_items]:
            try:
                # Get title
                title = entry.get('title', '')
                title = clean_html_text(title)
                if not title:
                    continue
                
                # Get description/summary
                description = entry.get('description') or entry.get('summary') or ''
                description = clean_html_text(description)
                if len(description) > 200:
                    description = description[:197] + '...'
                
                # Get source name
                source = entry.get('source', {}).get('title', '')
                if not source:
                    source = entry.get('author', '')
                if not source:
                    # Try to extract from URL
                    url = entry.get('link', '')
                    if 'timesofindia' in url:
                        source = 'Times of India'
                    elif 'thehindu' in url:
                        source = 'The Hindu'
                    elif 'indianexpress' in url:
                        source = 'Indian Express'
                    elif 'ndtv' in url:
                        source = 'NDTV'
                    elif 'bbc' in url:
                        source = 'BBC News'
                    elif 'nytimes' in url:
                        source = 'NY Times'
                    elif 'reuters' in url:
                        source = 'Reuters'
                    elif 'aljazeera' in url:
                        source = 'Al Jazeera'
                    elif 'techcrunch' in url:
                        source = 'TechCrunch'
                    elif 'wired' in url:
                        source = 'Wired'
                    elif 'theverge' in url:
                        source = 'The Verge'
                    elif 'arstechnica' in url:
                        source = 'Ars Technica'
                    elif 'aviationweek' in url:
                        source = 'Aviation Week'
                    elif 'flightglobal' in url:
                        source = 'FlightGlobal'
                    elif 'ainonline' in url:
                        source = 'AIN Online'
                    elif 'simpleflying' in url:
                        source = 'Simple Flying'
                    else:
                        source = 'News'
                
                # Get published date
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
                
                # Get URL
                url = entry.get('link', '#')
                
                articles.append({
                    'title': title,
                    'description': description or f"Read more at {source}",
                    'source': {'name': source},
                    'url': url,
                    'published': published
                })
            except Exception as e:
                print(f"⚠️ Error parsing RSS entry: {e}")
                continue
        
        return articles
    except Exception as e:
        print(f"⚠️ RSS fetch error for {feed_url}: {e}")
        return []

def fetch_news_from_newsapi(category_config):
    """Fetch news from NewsAPI."""
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
        print(f"⚠️ NewsAPI exception: {e}")
        return None

def fetch_news_from_multiple_sources(category, category_config):
    """Fetch news from multiple sources for a category."""
    all_articles = []
    sources_used = []
    
    # Try NewsAPI first
    if NEWS_API_ENABLED:
        try:
            articles = fetch_news_from_newsapi(category_config)
            if articles:
                all_articles.extend(articles)
                sources_used.append("NewsAPI")
                print(f"✅ NewsAPI returned {len(articles)} articles for {category}")
        except Exception as e:
            print(f"⚠️ NewsAPI error for {category}: {e}")
    
    # If NewsAPI didn't return enough articles, try RSS feeds
    if len(all_articles) < 3:
        feeds = RSS_FEEDS.get(category, [])
        for feed_url in feeds:
            try:
                articles = fetch_news_from_rss(feed_url, 3)
                if articles:
                    all_articles.extend(articles)
                    sources_used.append(feed_url.split('/')[2] if '//' in feed_url else 'RSS')
                    print(f"✅ RSS returned {len(articles)} articles for {category}")
                    if len(all_articles) >= 3:
                        break
            except Exception as e:
                print(f"⚠️ RSS error for {category}: {e}")
                continue
    
    # Remove duplicates based on title
    seen_titles = set()
    unique_articles = []
    for article in all_articles:
        title_lower = article['title'].lower()
        if title_lower not in seen_titles:
            seen_titles.add(title_lower)
            unique_articles.append(article)
    
    # Limit to 3 articles per category
    unique_articles = unique_articles[:3]
    
    if unique_articles:
        print(f"✅ {category}: {len(unique_articles)} articles from {len(sources_used)} sources")
        return unique_articles
    else:
        return None

def get_fallback_news(error_message=None):
    """Return fallback news articles if all APIs fail."""
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
    """Get news from multiple sources with fallback."""
    global _news_cache, _news_cache_time
    
    try:
        # Check cache
        if _news_cache and _news_cache_time:
            elapsed = (datetime.now(timezone.utc) - _news_cache_time).total_seconds()
            if elapsed < _news_cache_duration:
                print(f"📰 Returning cached news ({elapsed:.1f}s old)")
                return _news_cache

        results = {}
        success_count = 0
        
        for category, config in NEWS_CATEGORIES.items():
            print(f"📰 Fetching {category} news from multiple sources...")
            
            articles = fetch_news_from_multiple_sources(category, config)
            
            if articles:
                results[category] = articles
                success_count += 1
            else:
                fallback = get_fallback_news()
                results[category] = fallback["articles"].get(category, [])
                print(f"⚠️ Using fallback for {category}")
        
        has_results = any(len(articles) > 0 for articles in results.values())
        if not has_results:
            print("⚠️ No results from any source, using fallback")
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
        
        print(f"✅ News fetched: {success_count}/{len(NEWS_CATEGORIES)} categories updated")
        return _news_cache
        
    except Exception as e:
        print(f"❌ News error: {e}")
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
            print(f"⚠️ OpenWeatherMap error: {current_response.status_code}")
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
        print(f"⚠️ OpenWeatherMap exception: {e}")
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
                print(f"🌤️ Returning cached weather ({elapsed:.1f}s old)")
                return _weather_cache
        
        weather_data = get_weather_from_openweather()
        
        if not weather_data:
            print("⚠️ OpenWeatherMap failed, using fallback")
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
        print(f"❌ Weather error: {e}")
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
        print(f"❌ Open-Meteo fallback error: {e}")
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
# ATC
# ============================================================

def calculate_distance_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c

def format_altitude(meters):
    if meters is None:
        return None
    feet = meters * 3.28084
    return round(feet)

def format_speed(meters_per_second):
    if meters_per_second is None:
        return None
    knots = meters_per_second * 1.943844
    return round(knots)

def format_heading(degrees):
    if degrees is None:
        return None
    return round(degrees)

@app.get("/api/atc")
def get_atc():
    global _atc_cache, _atc_cache_time, _atc_rate_limit_until
    
    try:
        if _atc_rate_limit_until:
            remaining = (_atc_rate_limit_until - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                print(f"⏳ Rate limit cooldown: {remaining:.0f}s remaining")
                if _atc_cache:
                    cached_response = _atc_cache.copy()
                    cached_response["_stale"] = True
                    cached_response["_stale_reason"] = f"Rate limit cooldown ({remaining:.0f}s)"
                    cached_response["_cached"] = True
                    return cached_response
        
        if _atc_cache and _atc_cache_time:
            elapsed = (datetime.now(timezone.utc) - _atc_cache_time).total_seconds()
            if elapsed < _atc_cache_duration:
                print(f"📦 Returning cached ATC data ({elapsed:.1f}s old)")
                cached_response = _atc_cache.copy()
                cached_response["_cached"] = True
                cached_response["_cache_age"] = round(elapsed, 1)
                return cached_response

        lamin = ATC_LAT - ATC_LAT_RADIUS
        lamax = ATC_LAT + ATC_LAT_RADIUS
        lomin = ATC_LON - ATC_LON_RADIUS
        lomax = ATC_LON + ATC_LON_RADIUS

        headers = tokens.headers()
        if headers:
            print("🔑 Using OpenSky OAuth2 authentication")
        else:
            print("🌐 Using unauthenticated OpenSky API")

        response = requests.get(
            "https://opensky-network.org/api/states/all",
            params={"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax},
            headers=headers,
            timeout=15,
        )

        if response.status_code == 429:
            print("🚫 OpenSky rate limit reached - waiting 2 minutes")
            _atc_rate_limit_until = datetime.now(timezone.utc) + timedelta(seconds=120)
            if _atc_cache:
                stale_response = _atc_cache.copy()
                stale_response["_stale"] = True
                stale_response["_stale_reason"] = "Rate limit reached. Data may be stale."
                return stale_response
            return JSONResponse(
                status_code=200,
                content={
                    "success": False,
                    "aircraft": [],
                    "count": 0,
                    "_rate_limited": True
                }
            )

        _atc_rate_limit_until = None

        if not response.ok:
            raise Exception(f"OpenSky returned HTTP {response.status_code}")

        data = response.json()
        states = data.get('states')
        
        if states is None or len(states) == 0:
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "aircraft": [],
                    "count": 0,
                    "total_available": 0,
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "message": "No aircraft in your area right now"
                }
            )
        
        aircraft = []
        for state in states:
            if not state:
                continue
            try:
                callsign_raw = state[1] if len(state) > 1 else None
                callsign = callsign_raw.strip().upper() if callsign_raw else "N/A"
                longitude = state[5] if len(state) > 5 and state[5] is not None else None
                latitude = state[6] if len(state) > 6 and state[6] is not None else None
                altitude = state[7] if len(state) > 7 and state[7] is not None else None
                velocity = state[9] if len(state) > 9 and state[9] is not None else None
                heading = state[10] if len(state) > 10 and state[10] is not None else None

                if latitude is None or longitude is None:
                    continue

                distance = calculate_distance_km(ATC_LAT, ATC_LON, latitude, longitude)
                if distance > 300:
                    continue

                aircraft.append({
                    "callsign": callsign,
                    "latitude": latitude,
                    "longitude": longitude,
                    "distance_km": round(distance, 1),
                    "altitude_ft": format_altitude(altitude),
                    "speed_kt": format_speed(velocity),
                    "heading": format_heading(heading),
                })
            except Exception as e:
                print(f"⚠️ Error processing state: {e}")
                continue

        aircraft.sort(key=lambda plane: plane["distance_km"])
        aircraft = aircraft[:ATC_MAX_AIRCRAFT]

        result = {
            "success": True,
            "aircraft": aircraft,
            "count": len(aircraft),
            "total_available": len(states),
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        
        _atc_cache = result
        _atc_cache_time = datetime.now(timezone.utc)
        return result

    except Exception as e:
        print(f"❌ ATC error: {e}")
        if _atc_cache:
            stale_response = _atc_cache.copy()
            stale_response["_stale"] = True
            stale_response["_stale_reason"] = str(e)
            return stale_response
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "aircraft": [],
                "count": 0,
                "total_available": 0,
                "updated": datetime.now(timezone.utc).isoformat()
            }
        )

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
        print(f"Error loading grocery: {e}")
        return []

def save_grocery_to_file(items):
    try:
        with open(_grocery_file, 'w') as f:
            json.dump({"items": items, "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving grocery: {e}")
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
        print("YouTube search error:", e)
        return JSONResponse(status_code=500, content={"error": str(e)})

# ============================================================
# STARTUP - REGISTER mDNS (Optional - will fallback gracefully)
# ============================================================

mdns_zeroconf = None

def register_mdns():
    global mdns_zeroconf
    
    if not MDNS_AVAILABLE:
        print("⚠️ Zeroconf not available - skipping mDNS registration")
        return None
    
    try:
        from zeroconf import ServiceInfo, Zeroconf
        import socket
        
        print("📡 Registering mDNS with Bonjour...")
        zeroconf = Zeroconf()
        
        # Get primary IP address
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            print(f"   Primary IP: {local_ip}")
        except Exception as e:
            print(f"   ⚠️ Could not determine IP: {e}")
            local_ip = "127.0.0.1"
        
        # Register service
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
            return zeroconf
        except Exception as e:
            print(f"⚠️ Failed to register mDNS: {e}")
            print("   💡 You can still access the dashboard via IP address")
            return None
        
    except Exception as e:
        print(f"❌ Failed to register mDNS: {e}")
        print("   💡 Try using IP address instead")
        return None

# Try to register mDNS, but don't worry if it fails
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

atexit.register(cleanup_mdns)

# ============================================================
# SERVE CUSTOM AUDIO FILES
# ============================================================

@app.get("/api/audio/{filename}")
def get_audio(filename: str):
    """Serve custom audio files."""
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
# MAIN - Compatible with both local and Render
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*50)
    print("🏠 SMART FRIDGE DASHBOARD")
    print("="*50)
    
    # Check if running on Render
    is_render = os.environ.get("RENDER", "false").lower() == "true"
    
    if is_render:
        print("\n📱 Deployed on Render")
        print(f"   Service URL: https://{os.environ.get('RENDER_SERVICE_NAME', 'smartfridge-dashboard')}.onrender.com")
    else:
        # Get and display IP addresses for local development
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
    
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)