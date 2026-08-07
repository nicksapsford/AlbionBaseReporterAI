"""
AlbionBase Reporter -- dashboard (Page 1: Command Centre). Port 5041, DELL only.

Reads each instrument's live state over HTTP (/api/state) and its trade CSV for performance stats.
Fully instrument-agnostic -- everything derives from config_base_reporter.ALBIONBASE_INSTRUMENTS.
Graceful degradation: an unreachable system shows OFFLINE (with last-known cached state); a missing
CSV shows '--' performance. Never touches the live systems (read-only + poll).

This is the minimum-viable Page 1 for go-live. Individual instrument pages (/gold ...), /performance,
/archie-brief, /api/gaius-data, and the shutdown controls are staged follow-ups.
"""
import csv
import json
import os
import urllib.request
from datetime import datetime, timezone

from flask import Flask, Response

from config_base_reporter import (
    ALBIONBASE_INSTRUMENTS, PORT, GO_LIVE_DATE, STARTING_CAPITAL_PER_SYSTEM,
    FETCH_TIMEOUT_SEC, state_url, log_path,
)

_VER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
VERSION = open(_VER).read().strip() if os.path.exists(_VER) else "1.0.0"

app = Flask(__name__)
_CACHE = {}   # instrument name -> last-known good /api/state (for OFFLINE fallback)


# ── data acquisition ──────────────────────────────────────────────────────────
def fetch_state(inst):
    """Return (state_dict_or_None, online_bool). Caches last-good for the OFFLINE fallback."""
    try:
        with urllib.request.urlopen(state_url(inst), timeout=FETCH_TIMEOUT_SEC) as r:
            st = json.loads(r.read().decode("utf-8", "replace"))
        _CACHE[inst["name"]] = st
        return st, True
    except Exception:
        return _CACHE.get(inst["name"]), False


def perf_stats(inst):
    """Compute performance from the instrument's trade CSV. Uniform cols: direction, points_gained,
    pnl_gbp, exit_reason. Returns dict (zeros if the log is missing/unreadable)."""
    path = log_path(inst)
    pnls, wpts = [], []
    best, worst = None, None
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                try:
                    p = float(row.get("pnl_gbp"))
                except (TypeError, ValueError):
                    continue
                pnls.append(p)
                if p > 0:
                    try: wpts.append(float(row.get("points_gained")))
                    except (TypeError, ValueError): pass
                best = p if best is None else max(best, p)
                worst = p if worst is None else min(worst, p)
    except FileNotFoundError:
        return {"available": False}
    except Exception:
        return {"available": False}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    dec = len(wins) + len(losses)
    return {
        "available": True,
        "trades": len(pnls), "wins": len(wins), "losses": len(losses),
        "wr": (100 * len(wins) / dec) if dec else 0.0,
        "pf": (gw / gl) if gl else (float("inf") if gw else 0.0),
        "net": sum(pnls),
        "avg_w": (gw / len(wins)) if wins else 0.0,
        "avg_w_pts": (sum(wpts) / len(wpts)) if wpts else 0.0,
        "best": best or 0.0, "worst": worst or 0.0,
    }


def build_report():
    rows, any_online = [], False
    for inst in ALBIONBASE_INSTRUMENTS:
        st, online = fetch_state(inst)
        any_online = any_online or online
        pf = perf_stats(inst)
        pos = (st or {}).get("position") or None
        portfolio = (st or {}).get("portfolio") or {}
        rows.append({
            "name": inst["name"], "emoji": inst["emoji"], "ccy": inst["currency"],
            "online": online, "cached": (st is not None and not online),
            "version": (st or {}).get("version"),
            "balance": portfolio.get("balance"),
            "today": portfolio.get("today_pnl"),
            "in_trade": bool((st or {}).get("in_trade")),
            "direction": (pos or {}).get("direction", "FLAT") if pos else "FLAT",
            "floating": (st or {}).get("floating_gbp"),
            "locked": (st or {}).get("locked_gbp"),
            "perf": pf,
        })
    # portfolio aggregate (only from systems reporting a balance)
    bals = [r["balance"] for r in rows if r["balance"] is not None]
    todays = [r["today"] for r in rows if r["today"] is not None]
    deployed = STARTING_CAPITAL_PER_SYSTEM * len(ALBIONBASE_INSTRUMENTS)
    total_bal = sum(bals) if bals else None
    net_perf = sum(r["perf"]["net"] for r in rows if r["perf"].get("available"))
    portfolio = {
        "deployed": deployed,
        "total_balance": total_bal,
        "today": sum(todays) if todays else 0.0,
        "net_since_golive": net_perf,
        "systems_online": sum(1 for r in rows if r["online"]),
        "systems_total": len(rows),
    }
    return rows, portfolio, any_online


# ── rendering ─────────────────────────────────────────────────────────────────
def _money(v, plus=False):
    if v is None: return "--"
    return ("+" if (plus and v >= 0) else "") + "£{:,.2f}".format(v)

def _pf(v):
    if v is None: return "--"
    return "inf" if v == float("inf") else "%.2f" % v

def _sys_row_html(r):
    if not r["online"] and not r["cached"]:
        status = '<span class="off">OFFLINE</span>'
    elif r["cached"]:
        status = '<span class="cache">OFFLINE (cached)</span>'
    else:
        status = '<span class="live">LIVE \U0001F7E2</span>'
    pos = r["direction"] if r["in_trade"] else "FLAT"
    posc = {"LONG": "long", "SHORT": "short"}.get(pos, "flat")
    locked = _money(r["locked"], plus=True) + " locked" if r["locked"] else "--"
    tcls = "pos" if (r["today"] or 0) >= 0 else "neg"
    return (
        '<tr><td>%s %s <span class="mut">%s</span></td><td>%s</td>'
        '<td class="num">%s</td><td class="num %s">%s</td><td class="%s">%s</td><td class="num">%s</td></tr>'
    ) % (r["emoji"], r["name"], (r["version"] or "--"), status,
         _money(r["balance"]), tcls, _money(r["today"], plus=True), posc, pos, locked)

def _perf_row_html(r):
    p = r["perf"]
    if not p.get("available"):
        return '<tr><td>%s %s</td><td colspan="5" class="mut">trade log not accessible</td></tr>' % (r["emoji"], r["name"])
    ncls = "pos" if p["net"] >= 0 else "neg"
    return ('<tr><td>%s %s</td><td class="num">%d</td><td class="num">%.1f%%</td>'
            '<td class="num">%s</td><td class="num">%s</td><td class="num %s">%s</td></tr>') % (
        r["emoji"], r["name"], p["trades"], p["wr"], _pf(p["pf"]),
        _money(p["avg_w"], plus=True), ncls, _money(p["net"], plus=True))

def render_page(rows, portfolio, any_online):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    k1 = ('<span class="live">ONLINE \U0001F7E2</span>' if any_online
          else '<span class="off">K1 OFFLINE \U0001F534</span>')
    tot_perf = {"trades": sum(r["perf"].get("trades", 0) for r in rows if r["perf"].get("available")),
                "net": portfolio["net_since_golive"]}
    sys_rows = "".join(_sys_row_html(r) for r in rows)
    perf_rows = "".join(_perf_row_html(r) for r in rows)
    bal_line = _money(portfolio["total_balance"])
    change = (portfolio["total_balance"] - portfolio["deployed"]) if portfolio["total_balance"] is not None else None
    controls = "".join('<button class="ctl" disabled>%s %s off</button>' % (r["emoji"], r["name"]) for r in rows)
    return """<!doctype html><html><head><meta charset="utf-8"><title>AlbionBase Command Centre</title>
<meta http-equiv="refresh" content="30">
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:'Consolas','Courier New',monospace;margin:0;padding:20px;}}
h1{{color:#e0b020;font-size:20px;margin:0 0 4px;}} .sub{{color:#8b949e;font-size:12px;margin-bottom:16px;}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin-bottom:16px;}}
.card h2{{font-size:12px;letter-spacing:1px;color:#8b949e;margin:0 0 10px;text-transform:uppercase;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}} th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #21262d;}}
th{{color:#8b949e;font-weight:600;font-size:11px;text-transform:uppercase;}} .num{{text-align:right;font-variant-numeric:tabular-nums;}}
.pos{{color:#3fb950;}} .neg{{color:#f85149;}} .mut{{color:#6e7681;font-size:11px;}}
.live{{color:#3fb950;font-weight:700;}} .off{{color:#f85149;font-weight:700;}} .cache{{color:#d29922;font-weight:700;}}
.long{{color:#3fb950;font-weight:700;}} .short{{color:#f85149;font-weight:700;}} .flat{{color:#8b949e;}}
.big{{font-size:24px;color:#e6edf3;}} .ctl{{background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:6px;padding:8px 12px;margin:4px 6px 0 0;font-family:inherit;cursor:not-allowed;}}
.grid{{display:flex;gap:24px;flex-wrap:wrap;}} .kv{{margin-right:24px;}} .kv .l{{color:#8b949e;font-size:11px;}} .kv .v{{font-size:16px;}}
.costs{{color:#3fb950;font-weight:700;}}
</style></head><body>
<h1>\U0001F3DB️ ALBIONBASE COMMAND CENTRE</h1>
<div class="sub">Generated {now} UTC &nbsp;|&nbsp; Live system: ACEMAGIC K1 {k1} &nbsp;|&nbsp; Reporter v{ver} (:{port}) &nbsp;|&nbsp; go-live {golive}</div>

<div class="card"><h2>Portfolio</h2>
  <div class="grid">
    <div class="kv"><div class="l">DEPLOYED</div><div class="v">{deployed}</div></div>
    <div class="kv"><div class="l">CURRENT BALANCE</div><div class="v big">{bal}</div></div>
    <div class="kv"><div class="l">SINCE GO-LIVE</div><div class="v {chcls}">{change}</div></div>
    <div class="kv"><div class="l">TODAY</div><div class="v {tcls}">{today}</div></div>
    <div class="kv"><div class="l">SYSTEMS ONLINE</div><div class="v">{online}/{total}</div></div>
  </div></div>

<div class="card"><h2>Systems (live)</h2>
  <table><tr><th>System</th><th>Status</th><th class="num">Balance</th><th class="num">Today</th><th>Position</th><th class="num">Locked</th></tr>
  {sys_rows}</table></div>

<div class="card"><h2>Performance (from trade logs)</h2>
  <table><tr><th>System</th><th class="num">Trades</th><th class="num">WR</th><th class="num">PF</th><th class="num">Avg W</th><th class="num">Net</th></tr>
  {perf_rows}
  <tr style="border-top:2px solid #30363d;"><td><b>TOTAL</b></td><td class="num"><b>{tot_trades}</b></td><td class="num">--</td><td class="num">--</td><td class="num">--</td><td class="num {ncls}"><b>{tot_net}</b></td></tr>
  </table></div>

<div class="card"><h2>Running costs</h2><span class="costs">£0.00/day ✅</span> &nbsp;<span class="mut">(pure-Lancelot, no API cost)</span></div>

<div class="card"><h2>Controls</h2>
  <button class="ctl" disabled>\U0001F534 SHUTDOWN ALL -- MAINTENANCE</button><br>{controls}
  <div class="mut" style="margin-top:8px;">Controls wire to K1 /api/shutdown via Tailscale -- staged follow-up (disabled until K1 live).</div></div>
</body></html>""".format(
        now=now, k1=k1, ver=VERSION, golive=GO_LIVE_DATE,
        deployed=_money(portfolio["deployed"]), bal=bal_line,
        change=_money(change, plus=True), chcls=("pos" if (change or 0) >= 0 else "neg"),
        today=_money(portfolio["today"], plus=True), tcls=("pos" if portfolio["today"] >= 0 else "neg"),
        online=portfolio["systems_online"], total=portfolio["systems_total"],
        sys_rows=sys_rows, perf_rows=perf_rows,
        tot_trades=tot_perf["trades"], tot_net=_money(tot_perf["net"], plus=True),
        ncls=("pos" if tot_perf["net"] >= 0 else "neg"), controls=controls, port=PORT,
    )


# ── routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    rows, portfolio, any_online = build_report()
    return Response(render_page(rows, portfolio, any_online), mimetype="text/html")

@app.route("/api/systems")
def api_systems():
    rows, portfolio, any_online = build_report()
    return Response(json.dumps({"systems": rows, "portfolio": portfolio, "k1_online": any_online},
                               default=str), mimetype="application/json")

@app.route("/api/health")
def api_health():
    return Response(json.dumps({"status": "ok", "system": "AlbionBaseReporter",
                                "version": VERSION, "port": PORT}), mimetype="application/json")


if __name__ == "__main__":
    print("AlbionBase Reporter v%s -- Command Centre on http://localhost:%d" % (VERSION, PORT))
    app.run(host="0.0.0.0", port=PORT, threaded=True)
