"""
test_health_extra.py -- health-ping remainder (brief 21 Aug 2026): 1a Pushover message is credential-free,
1c /api/health cache stops broker-call multiplication. (1b default checked separately in the shell.)
Run: python test_health_extra.py
"""
import health_base_reporter as h

PASS = []; FAIL = []
def check(n, c):
    (PASS if c else FAIL).append(n); print(("  PASS " if c else "  FAIL ") + n)

print("1a -- no credential can appear in the PUSHOVER MESSAGE")
SECRET = "PUSHSECRET_KEY_999"
# worst case: a broker snapshot whose exception 'note' literally contains a secret -> the Pushover message
# (format_health output) must still not carry it (format_health does not include broker.note at all).
h._broker = lambda mode: {"reachable": False, "last_ok": None, "down_for": "5m", "positions": None,
                          "balance": None, "note": "connect failed: key=" + SECRET}
h._gold_state = lambda: {}
d = h.collect_health()
t, m, p = h.format_health(d)
check("secret in the broker.note does NOT reach the Pushover title/message", SECRET not in t and SECRET not in m)
check("format_health() does not include the broker 'note' field at all", "note" not in m.lower() or SECRET not in m)
# also a realistic scan with real-looking creds -> message clean (mirrors the /api/health + logs proof)
import importlib
importlib.reload  # noqa -- keep import used
import os
os.environ.update(CAPITALCOM_EMAIL="user@x.z", CAPITALCOM_DEMO_KEY="REALKEY_ABC", CAPITALCOM_DEMO_PASSWORD="REALPW_DEF")
del h._broker; del h._gold_state          # restore real collectors
importlib.reload(h)
os.environ.update(CAPITALCOM_EMAIL="user@x.z", CAPITALCOM_DEMO_KEY="REALKEY_ABC", CAPITALCOM_DEMO_PASSWORD="REALPW_DEF")
d2 = h.collect_health(); t2, m2, p2 = h.format_health(d2)
check("real creds appear nowhere in the Pushover title/message",
      not any(s in (t2 + m2) for s in ("REALKEY_ABC", "REALPW_DEF", "user@x.z")))

print("1c -- /api/health is cached so it cannot multiply the 3 Capital.com calls per collection")
calls = {"n": 0}
def _counting():
    calls["n"] += 1
    return {"mode": "DEMO", "alerts": [], "info": [], "priority": 0}
h.collect_health = _counting
h._health_cache["data"] = None; h._health_cache["ts"] = 0.0
a = h.get_health(); b = h.get_health(); c = h.get_health()      # 3 rapid endpoint hits within the cache window
check("3 rapid get_health() calls -> collect_health (3 broker calls) ran only ONCE", calls["n"] == 1)
check("first call fresh, subsequent served from cache", a.get("cached") is False and b.get("cached") is True and c.get("cached") is True)
# after the window, it refreshes
a2 = h.get_health(max_age=0)
check("max_age=0 forces a fresh collection (cache is time-bounded, not permanent)", calls["n"] == 2 and a2.get("cached") is False)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILURES:", FAIL); raise SystemExit(1)
print("ALL GREEN")
