"""
test_reporter_readonly.py -- proves the Reporter's vendored Capital.com client (_CapRO) is STRUCTURALLY
read-only: order-placing / order-closing / stop-target-modifying methods are ABSENT, not merely unused, and the
only endpoints it can reach are the session auth + two GETs. Run: python test_reporter_readonly.py
"""
import os
import health_base_reporter as h

PASS = []; FAIL = []
def check(n, c):
    (PASS if c else FAIL).append(n); print(("  PASS " if c else "  FAIL ") + n)

# 1) forbidden capabilities must be ABSENT (not present-but-unused)
FORBIDDEN = ["place_order", "open_position", "close_order", "close_position", "create_position", "delete_position",
             "sell", "buy", "order", "deal", "trade", "modify", "amend", "sync_stop", "set_stop", "set_target",
             "update_position", "delete", "put", "patch"]
cap = h._CapRO("DEMO")
present = [f for f in FORBIDDEN if hasattr(cap, f)]
check("no order-place/close/modify methods exist on the client: %s" % (present or "none"), present == [])

# 2) its public method surface is exactly the three read calls (+ the header helper)
methods = sorted(m for m in dir(cap) if not m.startswith("__") and callable(getattr(cap, m)))
check("public methods == {connect, positions, balance, _h}: %s" % methods,
      set(methods) == {"connect", "positions", "balance", "_h"})

# 3) the ONLY endpoints it can reach are session(POST-auth) + positions(GET) + accounts(GET) -- record every call
calls = []
class _Resp:
    status_code = 200
    headers = {"CST": "c", "X-SECURITY-TOKEN": "s"}
    def raise_for_status(self): pass
    def json(self): return {"positions": [], "accounts": [{"balance": {"balance": 100.0}}]}
class _FakeRq:
    def post(self, url, **k): calls.append(("POST", url)); return _Resp()
    def get(self, url, **k):  calls.append(("GET", url));  return _Resp()
    # NOTE: no put/patch/delete -- and _CapRO never calls them anyway
h._rq = _FakeRq()
os.environ.update(CAPITALCOM_EMAIL="x@y.z", CAPITALCOM_DEMO_KEY="k", CAPITALCOM_DEMO_PASSWORD="p")
c = h._CapRO("DEMO")
c.connect(); c.positions(); c.balance()
eps = [(m, u.split("/api/v1", 1)[-1]) for m, u in calls]
check("endpoints reached == session(POST), positions(GET), accounts(GET): %s" % eps,
      eps == [("POST", "/session"), ("GET", "/positions"), ("GET", "/accounts")])
check("no write verbs used (only POST /session for auth, else GET)",
      all(m == "GET" or (m == "POST" and p == "/session") for m, p in eps))

print("\nWHAT THE CLIENT CAN CALL (its full reach):")
print("  connect()   -> POST %s/session      (authenticate; creates a read session, cannot trade)" % "<base>")
print("  positions() -> GET  %s/positions    (read open positions)" % "<base>")
print("  balance()   -> GET  %s/accounts     (read account balance)" % "<base>")
print("  (no place/close/modify/stop/target capability exists)")

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILURES:", FAIL); raise SystemExit(1)
print("ALL GREEN")
