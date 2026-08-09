"""
AlbionBase Reporter -- instrument-agnostic configuration.

DESIGN PRINCIPLE (brief 7 Aug 2026): adding a future instrument (DAX, NASDAQ, ...) = ONE dict in
ALBIONBASE_INSTRUMENTS below. No other code changes -- every page, P&L calc, Archie brief and Gaius
feed derives from this list.

SEPARATION OF CONCERNS: this reporter runs on the DELL only and never touches the live systems. It
reads each system's live state over HTTP (/api/state) and its trade CSV for performance stats.
  * live_ip -> K1 Tailscale IP in production; defaults to localhost so it reads the DELL systems now.
  * ALBIONBASE_K1_HOST env var repoints ALL instruments to K1 in one place.
"""
import os

# Load this repo's .env so the per-machine settings below actually apply (ALBIONBASE_K1_HOST /
# ALBIONBASE_LOGS_BASE / LIVE_NOTIFICATIONS / Pushover). Without this the reporter ignored .env and
# ALBIONBASE_LOGS_BASE stayed the Dell default -- on the K1 that path is wrong and the CSVs go unread.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

PORT = 5041                 # 5040 is held by a Windows svchost (WSAEACCES on bind) -- confirmed 7 Aug.
GO_LIVE_DATE = "2026-08-17"
STARTING_CAPITAL_PER_SYSTEM = 3000.0     # GBP deployed per system at K1 go-live
POLL_INTERVAL_SEC = 30
FETCH_TIMEOUT_SEC = 5

# Daily-summary Pushover (brief Part 5): the reporter aggregates all instruments into ONE 21:00 UTC
# message. LIVE_NOTIFICATIONS + Pushover creds come from .env (per-machine). On the production Dell
# reporter (pointed at K1) set LIVE_NOTIFICATIONS=True; a pure paper reporter stays False (silent).
LIVE_NOTIFICATIONS     = os.getenv("LIVE_NOTIFICATIONS", "False").strip().lower() in ("1", "true", "yes", "on")
PUSHOVER_USER_KEY      = os.getenv("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN     = os.getenv("PUSHOVER_API_TOKEN", "")
DAILY_SUMMARY_HOUR_UTC = 21

# Environment label (Part 2a): TEST on the Dell (amber), LIVE on the K1 (green). Set ENV_LABEL in .env.
ENV_LABEL = os.getenv("ENV_LABEL", "TEST").strip().upper()

# Poll host (Part 2b): each PC's reporter polls ONLY that PC's own systems -- ALBIONBASE_HOST=localhost
# on both Dell and K1. (Legacy ALBIONBASE_K1_HOST still honoured for back-compat.) Never a hardcoded IP.
_HOST = os.getenv("ALBIONBASE_HOST", os.getenv("ALBIONBASE_K1_HOST", "127.0.0.1"))

# Base folder holding the live systems' logs. Dell (now) = the local repos; K1 = a Tailscale file share.
_LOGS_BASE = os.getenv("ALBIONBASE_LOGS_BASE", r"C:\Users\abc\Desktop\AlbionBase")

ALBIONBASE_INSTRUMENTS = [
    {"name": "Gold",  "ticker": "GOLD",  "port": 5033, "currency": "USD", "emoji": "\U0001F947",
     "live_ip": _HOST, "repo": "GoldBaseAI", "log_csv": "gold_trades.csv"},
    {"name": "Oil",   "ticker": "BRENT", "port": 5035, "currency": "USD", "emoji": "\U0001F6E2️",
     "live_ip": _HOST, "repo": "OilBaseAI",  "log_csv": "oil_trades.csv"},
    {"name": "FTSE",  "ticker": "FTSE",  "port": 5032, "currency": "GBP", "emoji": "\U0001F1EC\U0001F1E7",
     "live_ip": _HOST, "repo": "FTSEBaseAI", "log_csv": "ftse_trades.csv"},
    {"name": "US500", "ticker": "US500", "port": 5034, "currency": "USD", "emoji": "\U0001F1FA\U0001F1F8",
     "live_ip": _HOST, "repo": "USBaseAI",   "log_csv": "us_trades.csv"},
    # ---- Future instruments: add here ONLY (one dict) ----
    # {"name": "DAX", "ticker": "DAX", "port": 5037, "currency": "EUR", "emoji": "\U0001F1E9\U0001F1EA",
    #  "live_ip": _HOST, "repo": "DAXBaseAI", "log_csv": "dax_trades.csv"},
]


def state_url(inst):
    return "http://%s:%d/api/state" % (inst["live_ip"], inst["port"])


def log_path(inst):
    return os.path.join(_LOGS_BASE, inst["repo"], "logs", inst["log_csv"])
