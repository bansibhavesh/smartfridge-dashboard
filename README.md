# 🏠 Smart Fridge Dashboard

A personal smart-home dashboard built for a wall-mounted kitchen display. FastAPI backend + single-file HTML frontend that runs on any device with a browser. Ambient weather-reactive UI, ambient spotlight, animated clock, and hands-off operation — walk by, glance, done.

![Version](https://img.shields.io/badge/version-2.3-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104.1-teal)
![Render](https://img.shields.io/badge/Render-Ready-purple)

---

## ✨ Features

### 🎨 Ambient UI
- **Weather-aware spotlight** — background glow color shifts with weather (rain → blue, storm → violet, heat → amber, cold → cyan, snow → white)
- **Frosted glass cards** — the spotlight bleeds through every card for a lit-from-behind effect
- **Animated clock** — digits slide in when they change, colon blinks on a 2s cycle
- **Premium materials** — layered gradients, soft shadows, noise grain, brand accent dots
- **Dark UI** — designed for low-light kitchen environments

### ⚡ Interactive
- **Confetti burst** on task completion (60 particles if it's the last one)
- **Sound feedback** — `ding.mp3` for tasks, `chime.mp3` for hourly/timer/wind-down
- **Streak counter** — `🔥 N done today` shown in the task card
- **Pull-to-refresh** on touch devices
- **Undo toast** — 5s window to undo task completion
- **Idle dim** after 3 minutes (news + tasks stay bright so they're always readable)
- **Auto-refresh on redeploy** — page reloads itself when the server restarts
- **Auto-fullscreen** on first interaction, persists across reloads

### 🛠️ Functional
- 📋 **Google Tasks** — with local fallback when Google is unavailable
- 📅 **Google Calendar** — with local fallback
- ⏰ **Scheduled tasks** — auto-create tasks (daily 3 PM check-in by default)
- 🛒 **Grocery list** — 11 category tags, quick-add input
- 🌤️ **Weather** — current + 7-day forecast with dual-source fallback (OpenWeatherMap ↔ Open-Meteo)
- 📰 **News** — multi-source aggregation (NewsAPI + RSS) across India, World, Tech, Aviation
- 🎬 **YouTube search** — inline search with embeddable filter
- 📺 **Streaming shortcuts** — Netflix, Prime Video, Hotstar
- 🖥️ **System status** — live CPU, RAM, disk, uptime, IP
- 📟 **Live console** — filterable activity log with client-side injection
- 🧺 **Kitchen timer FAB** — quick countdown with chime on finish
- 🌙 **Hourly chime** — 7 AM to 10 PM, robust against tab throttling
- 🌅 **Morning audio** — plays at 6 AM
- 🌧️ **Rain alert** — spoken notification when rain is likely

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│  FastAPI (app.py)                               │
│  ├── /api/tasks          Google Tasks + fallback│
│  ├── /api/calendar       Google Calendar + fb   │
│  ├── /api/grocery        Local + Upstash KV     │
│  ├── /api/weather        OWM → Open-Meteo       │
│  ├── /api/weather/week   Open-Meteo → OWM → stale
│  ├── /api/news           NewsAPI → RSS → fb     │
│  ├── /api/youtube/search YouTube Data API v3    │
│  ├── /api/health         Deploy signature       │
│  ├── /api/logs           Activity ring buffer   │
│  └── /api/scheduled-*    Task scheduler         │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  Frontend (static/index.html)                   │
│  ├── 6-card grid dashboard                      │
│  ├── Weather-aware ambient spotlight            │
│  ├── Auto-refresh via /api/health polling       │
│  ├── Auto-fullscreen on first interaction       │
│  └── All state in-browser (no build step)       │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  Persistence (cascading fallbacks)              │
│  1. Upstash Redis (if configured)               │
│  2. Local JSON files (data/*.json)              │
│  3. In-memory (session-only)                    │
└─────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Local Development

```bash
git clone https://github.com/yourusername/smartfridge-dashboard.git
cd smartfridge-dashboard

# Create virtual environment
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your API keys (see below)

# Run the server
python app.py
```

Open **http://localhost:8000** — you should see the dashboard.

### Deployment on Render

1. Push this repo to GitHub
2. Create a new **Web Service** on Render
3. Connect the repo
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `python app.py`
6. Add environment variables (below) in the Render dashboard
7. Deploy

The dashboard auto-reloads on every redeploy (via `/api/health`'s `deploy_id`).

---

## 🔑 Environment Variables

Create a `.env` file in the project root:

```bash
# ─── Weather (recommended) ─────────────────────────────
OPENWEATHER_API_KEY=your_openweather_key_here

# ─── News (optional — RSS fallback works without it) ───
NEWS_API_KEY=your_newsapi_key_here

# ─── YouTube Search (required for YouTube feature) ─────
YOUTUBE_API_KEY=your_youtube_data_api_key

# ─── Persistent Storage (optional but recommended) ─────
# Free Redis at https://upstash.com
UPSTASH_REDIS_REST_URL=https://your-db.upstash.io
UPSTASH_REDIS_REST_TOKEN=your_upstash_token

# ─── Google OAuth (for Tasks + Calendar) ───────────────
# Place credentials.json + token.json in the project root instead
# (Generated via Google Cloud Console → OAuth 2.0 Client ID)
```

| Variable | Required? | Purpose |
|---|---|---|
| `OPENWEATHER_API_KEY` | Recommended | Primary weather source + week forecast fallback |
| `NEWS_API_KEY` | Optional | Better news quality; RSS fallback covers without it |
| `YOUTUBE_API_KEY` | Required for YouTube | YouTube search returns 500 without it |
| `UPSTASH_REDIS_REST_URL` | Optional | Persistent storage across redeploys |
| `UPSTASH_REDIS_REST_TOKEN` | Optional | Same as above |

---

## 📁 Project Structure

```
smarthome/
├── app.py                     # FastAPI backend (single file)
├── requirements.txt
├── .env                       # API keys (not committed)
├── credentials.json           # Google OAuth (not committed)
├── token.json                 # Google OAuth token (auto-generated)
│
├── static/
│   ├── index.html             # Full dashboard (HTML + CSS + JS)
│   ├── icon.png               # Favicon
│   ├── chime.mp3              # Hourly/timer/wind-down sound
│   ├── ding.mp3               # Task completion sound
│   └── morning.mp3            # 6 AM audio
│
└── data/                      # Auto-generated (local persistence)
    ├── local_tasks.json
    ├── local_calendar.json
    ├── scheduled_tasks.json
    ├── grocery.json
    ├── system_log.json
    └── data_mode.json
```

---

## ⚙️ Configuration

### Weather location

Weather defaults to **LAT, LONG** (0000, 0000). To change it, edit `app.py`:

```python
WEATHER_LAT = 00000
WEATHER_LON = 00000
```

### Family members

Edit `FAMILY_MEMBERS` in `app.py` to change task assignment options:

```python
FAMILY_MEMBERS = [
    {"id": "son", "name": "son", "emoji": "🧑"},
    {"id": "mother",   "name": "mother",   "emoji": "👩"},
    {"id": "father",    "name": "father",    "emoji": "👨"},
    {"id": "daughther",  "name": "duaghter",  "emoji": "👧"},
]
```

### Idle dim timeout

In `index.html`, search for `IDLE_MS`:

```javascript
const IDLE_MS = 3 * 60 * 1000;  // 3 minutes — change as needed
```

---

## 🎨 Design Notes

### Ambient Spotlight

The background has three large blurred blobs that slowly drift on a 22-second cycle. Their color is driven by the current weather alert:

| Weather | Spot color | RGB |
|---|---|---|
| Clear | Soft blue | `120, 165, 255` |
| Rain | Deep blue | `96, 165, 250` |
| Storm | Violet | `167, 139, 250` |
| Heat | Amber | `251, 146, 60` |
| Cold | Ice cyan | `147, 197, 253` |
| Snow | Icy white | `226, 232, 240` |

Cards are frosted glass (`backdrop-filter: blur(24px)` + translucent background) so the glow bleeds through everything.

### Auto-refresh Flow

```
Browser loads
     │
     ▼
Polls /api/health every 10s
     │
     ▼
Compares response.deploy_id against last known
     │
     ├── Same  → do nothing
     │
     └── Different → show "Server updated" toast → reload
```

`deploy_id` is derived on the server side (see `_compute_deploy_id()` in `app.py`) and changes whenever Render redeploys.

### Fullscreen Behavior

Browsers require a user gesture before entering fullscreen. The dashboard:
- Fires fullscreen on the **first tap/click/keypress** after page load
- Remembers if you were in fullscreen and restores it after reloads
- Stops trying if you press `Esc` (respects the manual exit)

---

## 🐛 Known Issues

- **Audio caching**: handled automatically — the `/api/audio/` endpoint uses `no-cache` + ETag, so replaced MP3s are picked up on the next request without any manual version bump or cache clear.
- **Google OAuth**: the first run requires visiting the server from a browser to complete the OAuth flow (only needed once locally before deploying).
- **Render cold starts**: free-tier services sleep after 15 min of inactivity. First request after sleep takes ~30s.

---

## 📝 Changelog

### v2.3
- Auto-refresh on server redeploy
- Weather-aware ambient spotlight
- Confetti + ding on task completion
- Streak counter, pull-to-refresh, animated clock
- Calendar + news card redesigns
- Premium visual polish (layered glass, premium shadows)

### v2.2
- Week forecast with OWM fallback + stale cache

### v2.1
- Isolated dimming for news + tasks

### v2.0
- Initial multi-card dashboard

---

## 🔧 Development Tips

```markdown
### Replacing audio files

Just drop the new MP3 into `static/` — the server's ETag handling means browsers
will fetch the fresh file automatically. No version bumps needed.
```

### Testing the deploy-ID flow

```powershell
# Simulate a redeploy by touching index.html
(Get-Item static\index.html).LastWriteTime = Get-Date
# Or push a new commit if using RENDER_GIT_COMMIT
```

### Testing the week forecast fallback

Temporarily break the primary source in `_build_week_from_openmeteo()`:

```python
raise Exception("forced test failure")
```

…to confirm OWM fallback kicks in.

---

## 📜 License

Personal project — not licensed for redistribution.
contact at bansibhavesh@hotmail.com for permissons to use

```markdown
Built using DEEPSEEK AI, expect bugs and issues
```


---

*Last updated: 2026-09-18*