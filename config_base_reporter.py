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

# ── Desk health ping (brief 21 Aug 2026) -- "is the DESK alive and in the right state", separate from the
# 21:00 trading summary. Fires REGARDLESS of DEMO/LIVE (its whole point is to shout when the desk is in DEMO),
# so it is gated on its OWN flag, NOT trading_mode. Off by default (silent on a paper/Dell reporter); set
# HEALTH_NOTIFICATIONS=True on the K1 reporter. All times UTC, all thresholds .env-configurable.
HEALTH_NOTIFICATIONS     = os.getenv("HEALTH_NOTIFICATIONS", "False").strip().lower() in ("1", "true", "yes", "on")
HEALTH_MORNING_HOUR_UTC  = int(os.getenv("HEALTH_MORNING_HOUR_UTC", "7"))    # overnight-was-fine push
HEALTH_EVENING_HOUR_UTC  = int(os.getenv("HEALTH_EVENING_HOUR_UTC", "19"))   # before Nick stops for the day
HEALTH_PROCS             = os.getenv("HEALTH_PROCS", "GoldBase=5033,RoundTableBase=5036,Reporter=5041")
HEALTH_KILL_SWITCH_GBP   = float(os.getenv("HEALTH_KILL_SWITCH_GBP", "180"))  # daily-loss kill switch (headroom)
HEALTH_RECENT_BOOT_HOURS = float(os.getenv("HEALTH_RECENT_BOOT_HOURS", "3"))  # uptime under this = flag a reboot
# One health collection makes 3 Capital.com calls (session-auth + /positions + /accounts). /api/health is cached
# for this many seconds so it CANNOT be hammered into multiplying broker calls (endpoint-abuse guard).
HEALTH_CACHE_SEC         = float(os.getenv("HEALTH_CACHE_SEC", "30"))
CAPITALCOM_DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
CAPITALCOM_LIVE_BASE_URL = "https://api-capital.backend-capital.com/api/v1"

# ── Dead-man's switch (brief 21 Aug 2026, Nick-approved). An OUTBOUND-ONLY periodic "still alive" ping so
# SILENCE becomes the alarm -- the health ping dies with K1, this does not (an external service alerts Nick if
# the pings STOP). The ping is BARE: no account data, no balances, no positions, no credentials -- just a GET/
# POST to a secret URL (the URL itself is the only secret, from .env, gitignored). Disabled when HEARTBEAT_URL
# is blank. Runs in its OWN daemon thread, every call wrapped + hard-timeout, so a dead/hanging endpoint can
# NEVER stall the Reporter's scheduler (and the Reporter is a separate process from the engine -- it cannot
# reach the trading loop at all). Expects a healthcheck-style ping URL (e.g. healthchecks.io / Better Stack
# heartbeat / Cronitor / Dead Man's Snitch): a unique URL you GET on a schedule; the service alarms if pings stop.
HEARTBEAT_URL          = os.getenv("HEARTBEAT_URL", "").strip()
HEARTBEAT_INTERVAL_MIN = float(os.getenv("HEARTBEAT_INTERVAL_MIN", "15"))
HEARTBEAT_TIMEOUT_SEC  = float(os.getenv("HEARTBEAT_TIMEOUT_SEC", "8"))
HEARTBEAT_METHOD       = os.getenv("HEARTBEAT_METHOD", "GET").strip().upper()

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
    # OilBase REMOVED 17 Aug 2026 -- SSL/RSI/TMO has no edge on Oil (backtest PF 0.53/0.96); stopped + repo archived.
    # US500/USBase REMOVED 20 Aug 2026 -- the PF 1.49 that justified its live slot was a 1-bar lookahead
    #   artifact; honest re-validation is PF 0.97 net-negative (fails the same standard that demoted Oil).
    #   Stopped on K1 + Dell, repo archived. US500 data collection continues on USBenchmark only.
    # FTSE REMOVED 11 Aug 2026 -- £2/pt on UK100 margin exceeds the £3,000 pot at 2% risk (see changelog).
    # AlbionBase runs 2 instruments: Gold, AUDUSD. Oil + US500 + FTSE repos archived.
    {"name": "AUDUSD", "ticker": "AUDUSD", "port": 5032, "currency": "GBP", "emoji": "\U0001F1E6\U0001F1FA",
     "live_ip": _HOST, "repo": "AUDUSDBaseAI", "log_csv": "audusd_trades.csv"},
    # ---- Future instruments: add here ONLY (one dict) ----
    # {"name": "DAX", "ticker": "DAX", "port": 5037, "currency": "EUR", "emoji": "\U0001F1E9\U0001F1EA",
    #  "live_ip": _HOST, "repo": "DAXBaseAI", "log_csv": "dax_trades.csv"},
]


def state_url(inst):
    return "http://%s:%d/api/state" % (inst["live_ip"], inst["port"])


def log_path(inst):
    return os.path.join(_LOGS_BASE, inst["repo"], "logs", inst["log_csv"])
