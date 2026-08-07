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

PORT = 5041                 # 5040 is held by a Windows svchost (WSAEACCES on bind) -- confirmed 7 Aug.
GO_LIVE_DATE = "2026-08-17"
STARTING_CAPITAL_PER_SYSTEM = 3000.0     # GBP deployed per system at K1 go-live
POLL_INTERVAL_SEC = 30
FETCH_TIMEOUT_SEC = 5

# Poll host. Set ALBIONBASE_K1_HOST to the K1 Tailscale IP (100.x.x.x) in production. Until K1/Tailscale
# is up it defaults to localhost, so the reporter reads the DELL paper systems as a live stand-in.
_HOST = os.getenv("ALBIONBASE_K1_HOST", "127.0.0.1")

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
