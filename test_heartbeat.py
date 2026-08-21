"""
test_heartbeat.py -- dead-man's switch safety (brief 21 Aug 2026). Proves the ping can NEVER stall the desk.
The safety properties matter more than the feature working. Run: python test_heartbeat.py
"""
import socket, threading, time
import health_base_reporter as health

PASS = []; FAIL = []
def check(n, c):
    (PASS if c else FAIL).append(n); print(("  PASS " if c else "  FAIL ") + n)

def _closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def _hang_server():
    """Accepts the TCP connection then NEVER responds -- the dangerous case (a hang, not a refusal)."""
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
    def run():
        try:
            conn, _ = srv.accept(); time.sleep(60); conn.close()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
    return port

health.cfg.HEARTBEAT_TIMEOUT_SEC = 2.0
health.cfg.HEARTBEAT_METHOD = "GET"

print("1a -- ping URL UNREACHABLE (connection refused): fast, no raise, desk unaffected")
health.cfg.HEARTBEAT_URL = "http://127.0.0.1:%d/" % _closed_port()
t0 = time.time()
raised = False
try:
    health.heartbeat_once()
except Exception:
    raised = True
dt = time.time() - t0
check("heartbeat_once did NOT raise", not raised)
check("returned fast (<5s), did not hang", dt < 5)
check("recorded a failure (ok False)", health.heartbeat_status()["ok"] is False)
# desk unaffected: collect_health + format still work
_h = health.collect_health(); _t, _m, _p = health.format_health(_h)
check("collect_health/format still work with a failing heartbeat", isinstance(_m, str) and len(_m) > 0)

print("1b -- URL ACCEPTS then NEVER responds (a HANG): bounded by the read timeout, no hang")
health.cfg.HEARTBEAT_URL = "http://127.0.0.1:%d/" % _hang_server()
t0 = time.time()
raised = False
try:
    health.heartbeat_once()
except Exception:
    raised = True
dt = time.time() - t0
check("heartbeat_once did NOT raise on a hang", not raised)
check("bounded by timeout (returned in <%.0fs, not hung)" % (health.cfg.HEARTBEAT_TIMEOUT_SEC + 3), dt < health.cfg.HEARTBEAT_TIMEOUT_SEC + 3)
check("recorded a failure (ok False)", health.heartbeat_status()["ok"] is False)

print("1c -- if the ping is FAILING, the health report SAYS so (never trust a silent alarm)")
# heartbeat is enabled + has never succeeded (only failures above) -> stale
hb = health.heartbeat_status()
check("heartbeat_status: enabled + stale", hb["enabled"] and hb["stale"])
d = health.collect_health()
alert = [a for a in d["alerts"] if a.startswith("HEARTBEAT")]
check("collect_health raises a HEARTBEAT alert", bool(alert))
check("that alert forces HIGH priority", d["priority"] == 1)
_t, _m, _p = health.format_health(d)
check("push message contains a FAILING heartbeat line", "Heartbeat: FAILING" in _m)

# disabled case: blank URL -> no-op, no alert, not in the message
health.cfg.HEARTBEAT_URL = ""
health.heartbeat_once()   # no-op
d2 = health.collect_health()
check("blank HEARTBEAT_URL -> disabled, no heartbeat alert", not any(a.startswith("HEARTBEAT") for a in d2["alerts"]))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILURES:", FAIL); raise SystemExit(1)
print("ALL GREEN")
