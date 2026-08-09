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
    LIVE_NOTIFICATIONS, PUSHOVER_USER_KEY, PUSHOVER_API_TOKEN, DAILY_SUMMARY_HOUR_UTC,
    ENV_LABEL,
)


def _env_badge():
    """Part 2a: unmissable environment banner -- amber TEST-Dell vs green LIVE-K1."""
    if ENV_LABEL == "LIVE":
        return ('<span style="background:#12331b;color:#3fb950;border:1px solid #2ea043;border-radius:5px;'
                'padding:2px 10px;font-weight:700;letter-spacing:1px;">LIVE — K1</span>')
    return ('<span style="background:#3a2f00;color:#e0b020;border:1px solid #6b5600;border-radius:5px;'
            'padding:2px 10px;font-weight:700;letter-spacing:1px;">TEST — Dell</span>')

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
            "acct_bal": (st or {}).get("account_balance"),     # Part 3: real Capital.com pot (shared account)
            "acct_type": (st or {}).get("account_type"),
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
    # Part 3: TOTAL POT = the real Capital.com balance. It is ONE shared account, so every system reports
    # the same figure -- take the first non-null. Risk per trade = 2% of it.
    pots = [r["acct_bal"] for r in rows if r.get("acct_bal") is not None]
    total_pot = pots[0] if pots else None
    atypes = [r["acct_type"] for r in rows if r.get("acct_type")]
    account_type = (atypes[0] if atypes else "DEMO")
    risk_pt = round(total_pot * 0.02, 2) if total_pot else None
    portfolio = {
        "deployed": deployed,
        "total_balance": total_bal,
        "total_pot": total_pot,
        "account_type": account_type,
        "risk_per_trade": risk_pt,
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

def _acct_tag(t):
    """Part 3: DEMO (grey) vs LIVE (green) account badge. LIVE is deliberately eye-catching."""
    t = (t or "DEMO").upper()
    if t == "LIVE":
        return ('<span style="background:#12331b;color:#3fb950;border:1px solid #2ea043;'
                'border-radius:4px;padding:2px 9px;font-weight:700;letter-spacing:1px;">LIVE</span>')
    return ('<span style="background:#26262b;color:#8b949e;border:1px solid #444c56;'
            'border-radius:4px;padding:2px 9px;font-weight:700;letter-spacing:1px;">DEMO</span>')

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
        '<td class="num %s">%s</td><td class="%s">%s</td><td class="num">%s</td></tr>'
    ) % (r["emoji"], r["name"], (r["version"] or "--"), status,
         tcls, _money(r["today"], plus=True), posc, pos, locked)

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
    change = portfolio["net_since_golive"]                    # Part 3: net from trade logs since go-live
    pot_line = _money(portfolio["total_pot"])
    risk_line = _money(portfolio["risk_per_trade"])
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
<h1>\U0001F3DB️ ALBIONBASE COMMAND CENTRE &nbsp; {env}</h1>
<div class="sub">Generated {now} UTC &nbsp;|&nbsp; Live system: ACEMAGIC K1 {k1} &nbsp;|&nbsp; Reporter v{ver} (:{port}) &nbsp;|&nbsp; go-live {golive}</div>
{nav}

<div class="card"><h2>Portfolio</h2>
  <div class="grid">
    <div class="kv"><div class="l">TOTAL POT</div><div class="v big">{pot}</div></div>
    <div class="kv"><div class="l">ACCOUNT</div><div class="v">{accttag}</div></div>
    <div class="kv"><div class="l">RISK / TRADE (2%)</div><div class="v">{risk}</div></div>
    <div class="kv"><div class="l">TODAY</div><div class="v {tcls}">{today}</div></div>
    <div class="kv"><div class="l">NET SINCE GO-LIVE</div><div class="v {chcls}">{change}</div></div>
    <div class="kv"><div class="l">SYSTEMS ONLINE</div><div class="v">{online}/{total}</div></div>
  </div>
  <div class="mut" style="margin-top:8px;">TOTAL POT is read live from the Capital.com account (read-only); risk/trade = 2% of it. Per-system paper balances retired — see per-system P&amp;L below.</div>
</div>

<div class="card"><h2>Systems (live)</h2>
  <table><tr><th>System</th><th>Status</th><th class="num">Today</th><th>Position</th><th class="num">Locked</th></tr>
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
        pot=pot_line, accttag=_acct_tag(portfolio["account_type"]), risk=risk_line,
        change=_money(change, plus=True), chcls=("pos" if (change or 0) >= 0 else "neg"),
        today=_money(portfolio["today"], plus=True), tcls=("pos" if portfolio["today"] >= 0 else "neg"),
        online=portfolio["systems_online"], total=portfolio["systems_total"],
        sys_rows=sys_rows, perf_rows=perf_rows,
        tot_trades=tot_perf["trades"], tot_net=_money(tot_perf["net"], plus=True),
        ncls=("pos" if tot_perf["net"] >= 0 else "neg"), controls=controls, port=PORT,
        nav=_nav("home"), env=_env_badge(),
    )


# ── extended pages (Part 3 pages 2/3/5) + Gaius feed (Part 4) ─────────────────
_CSS = """
body{background:#0d1117;color:#c9d1d9;font-family:'Consolas','Courier New',monospace;margin:0;padding:20px;}
h1{color:#e0b020;font-size:20px;margin:0 0 4px;} .sub{color:#8b949e;font-size:12px;margin-bottom:12px;}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;margin-bottom:16px;}
.card h2{font-size:12px;letter-spacing:1px;color:#8b949e;margin:0 0 10px;text-transform:uppercase;}
table{width:100%;border-collapse:collapse;font-size:13px;} th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #21262d;}
th{color:#8b949e;font-weight:600;font-size:11px;text-transform:uppercase;} .num{text-align:right;font-variant-numeric:tabular-nums;}
.pos{color:#3fb950;} .neg{color:#f85149;} .mut{color:#6e7681;font-size:11px;}
.live{color:#3fb950;font-weight:700;} .off{color:#f85149;font-weight:700;} .cache{color:#d29922;font-weight:700;}
.long{color:#3fb950;font-weight:700;} .short{color:#f85149;font-weight:700;} .flat{color:#8b949e;}
.big{font-size:22px;color:#e6edf3;} .kv{margin-right:26px;display:inline-block;vertical-align:top;} .kv .l{color:#8b949e;font-size:11px;} .kv .v{font-size:15px;}
.nav{margin-bottom:16px;font-size:12px;} .nav a{color:#58a6ff;text-decoration:none;margin-right:14px;} .nav a.on{color:#e0b020;font-weight:700;}
.costs{color:#3fb950;font-weight:700;} pre{white-space:pre-wrap;font-family:inherit;font-size:12px;color:#c9d1d9;margin:0;}
button.copy{background:#21262d;color:#58a6ff;border:1px solid #30363d;border-radius:6px;padding:6px 12px;font-family:inherit;cursor:pointer;margin-top:8px;}
"""

def _cls(v): return "pos" if (v or 0) >= 0 else "neg"
def _slug(inst): return inst["name"].lower()

def _nav(active=""):
    a = ['<a href="/" %s>Command Centre</a>' % ('class="on"' if active == "home" else "")]
    for inst in ALBIONBASE_INSTRUMENTS:
        s = _slug(inst)
        a.append('<a href="/%s" %s>%s %s</a>' % (s, 'class="on"' if active == s else "", inst["emoji"], inst["name"]))
    a.append('<a href="/performance" %s>Performance</a>' % ('class="on"' if active == "perf" else ""))
    a.append('<a href="/archie-brief" %s>Archie Brief</a>' % ('class="on"' if active == "brief" else ""))
    a.append('<a href="/monitor" %s>Monitor</a>' % ('class="on"' if active == "mon" else ""))
    return '<div class="nav">' + " ".join(a) + '</div>'

def _shell(title, body, active=""):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return ("<!doctype html><html><head><meta charset='utf-8'><title>" + title + "</title>"
            "<meta http-equiv='refresh' content='60'><style>" + _CSS + "</style></head><body>"
            "<h1>" + title + " &nbsp; " + _env_badge() + "</h1><div class='sub'>Generated " + now + " UTC | AlbionBase Reporter v" + VERSION + "</div>"
            + _nav(active) + body + "</body></html>")

def _num(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            try: return float(v)
            except (TypeError, ValueError): pass
    return None

def read_trades(inst):
    """Full normalised trade rows (oldest->newest). [] if the CSV is missing/unreadable."""
    out = []
    try:
        with open(log_path(inst), newline="", encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                pnl = _num(r, "pnl_gbp")
                if pnl is None: continue
                out.append({
                    "date": r.get("date", ""), "time": r.get("time", ""), "direction": r.get("direction", ""),
                    "entry": _num(r, "entry_price_usd", "entry_price"), "exit": _num(r, "exit_price_usd", "exit_price"),
                    "points": _num(r, "points_gained"), "pnl": pnl, "reason": r.get("exit_reason", ""),
                    "stake": _num(r, "stake_per_point", "stake"),
                    "balance_after": _num(r, "capital_after_gbp", "capital_after"),
                    "exit_time": r.get("exit_time", "") or (r.get("date", "") + " " + r.get("time", "")),
                })
    except Exception:
        return []
    return out

def _stats(pnls):
    w = [p for p in pnls if p > 0]; l = [p for p in pnls if p <= 0]
    gw, gl = sum(w), abs(sum(l)); dec = len(w) + len(l)
    return dict(n=len(pnls), w=len(w), l=len(l), wr=100 * len(w) / dec if dec else 0.0,
                pf=(gw / gl) if gl else (float("inf") if gw else 0.0), net=sum(pnls),
                avg_w=(gw / len(w)) if w else 0.0, avg_l=(-gl / len(l)) if l else 0.0)

def find_instrument(slug):
    slug = slug.lower()
    for inst in ALBIONBASE_INSTRUMENTS:
        if inst["name"].lower() == slug or inst["name"].lower().startswith(slug) or inst["ticker"].lower() == slug:
            return inst
    return None

def render_instrument_page(inst):
    st, online = fetch_state(inst)
    trades = read_trades(inst)
    s = _stats([t["pnl"] for t in trades])
    pos = (st or {}).get("position") or None
    bal = ((st or {}).get("portfolio") or {}).get("balance")
    start = STARTING_CAPITAL_PER_SYSTEM
    if pos and (st or {}).get("in_trade"):
        d = pos.get("direction"); posc = {"LONG": "long", "SHORT": "short"}.get(d, "flat")
        fl = (st or {}).get("floating_gbp"); lk = (st or {}).get("locked_gbp")
        poscard = ("<div class='kv'><div class='l'>DIRECTION</div><div class='v %s'>%s</div></div>" % (posc, d)
            + "<div class='kv'><div class='l'>ENTRY</div><div class='v'>%s</div></div>" % pos.get("entry")
            + "<div class='kv'><div class='l'>PRICE</div><div class='v'>%s</div></div>" % (st or {}).get("price")
            + "<div class='kv'><div class='l'>STOP</div><div class='v'>%s</div></div>" % pos.get("stop")
            + "<div class='kv'><div class='l'>TARGET</div><div class='v'>%s</div></div>" % pos.get("target")
            + "<div class='kv'><div class='l'>STAKE</div><div class='v'>£%s/pt</div></div>" % pos.get("stake")
            + "<div class='kv'><div class='l'>FLOATING</div><div class='v %s'>%s</div></div>" % (_cls(fl), _money(fl, plus=True))
            + "<div class='kv'><div class='l'>LOCKED</div><div class='v'>%s</div></div>" % (_money(lk, plus=True) if lk else "--"))
    else:
        poscard = "<span class='mut'>No open position (FLAT)</span>" if online else "<span class='off'>OFFLINE</span>"
    best = max((t["pnl"] for t in trades), default=0.0); worst = min((t["pnl"] for t in trades), default=0.0)
    perf = ("<div class='kv'><div class='l'>TRADES</div><div class='v'>%d (%dW/%dL)</div></div>" % (s["n"], s["w"], s["l"])
        + "<div class='kv'><div class='l'>WIN RATE</div><div class='v'>%.1f%%</div></div>" % s["wr"]
        + "<div class='kv'><div class='l'>PF</div><div class='v'>%s</div></div>" % _pf(s["pf"])
        + "<div class='kv'><div class='l'>NET</div><div class='v %s'>%s</div></div>" % (_cls(s["net"]), _money(s["net"], plus=True))
        + "<div class='kv'><div class='l'>AVG W / L</div><div class='v'>%s / %s</div></div>" % (_money(s["avg_w"], plus=True), _money(s["avg_l"]))
        + "<div class='kv'><div class='l'>BEST / WORST</div><div class='v'>%s / %s</div></div>" % (_money(best, plus=True), _money(worst)))
    growth = (bal - start) if bal is not None else None
    gpct = (" (%+.1f%%)" % (100 * growth / start)) if (growth is not None and start) else ""
    comp = ("<div class='kv'><div class='l'>START</div><div class='v'>%s</div></div>" % _money(start)
        + "<div class='kv'><div class='l'>CURRENT</div><div class='v big'>%s</div></div>" % _money(bal)
        + "<div class='kv'><div class='l'>GROWTH</div><div class='v %s'>%s%s</div></div>" % (_cls(growth), _money(growth, plus=True), gpct)
        + "<div class='kv'><div class='l'>CURRENT STAKE</div><div class='v'>£%s/pt</div></div>" % (pos.get("stake") if pos else "--"))
    rrows = ""
    for t in list(reversed(trades))[:10]:
        dc = {"LONG": "long", "SHORT": "short"}.get(t["direction"], "")
        dot = "\U0001F7E2" if t["pnl"] > 0 else "\U0001F534"
        rrows += ("<tr><td>%s</td><td class='%s'>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num %s'>%s</td><td>%s %s</td></tr>"
                  % (t["date"], dc, t["direction"], t["entry"], t["exit"], _cls(t["pnl"]), _money(t["pnl"], plus=True), dot, t["reason"]))
    recent = "<table><tr><th>Date</th><th>Dir</th><th class='num'>Entry</th><th class='num'>Exit</th><th class='num'>P&amp;L</th><th>Exit</th></tr>" + (rrows or "<tr><td colspan=6 class='mut'>no trades yet</td></tr>") + "</table>"
    two = ""
    if inst["name"] == "Gold":
        lk = (st or {}).get("locked_gbp")
        stt = ("ACTIVE -- locked " + _money(lk, plus=True)) if lk else "not engaged (below +30pt)"
        two = "<div class='card'><h2>Two-speed trail (Gold)</h2>Activation +30pt profit &nbsp;|&nbsp; tight trail 10pt &nbsp;|&nbsp; status: <b>%s</b></div>" % stt
    body = ("<div class='card'><h2>Current position</h2>%s</div>" % poscard
        + "<div class='card'><h2>Performance (since go-live)</h2>%s</div>" % perf
        + "<div class='card'><h2>Compounding tracker</h2>%s</div>" % comp
        + two
        + "<div class='card'><h2>Recent trades (last 10)</h2>%s</div>" % recent)
    return _shell("%s %sBASE -- DETAILED REPORT" % (inst["emoji"], inst["name"].upper()), body, _slug(inst))

def render_performance_page():
    from collections import defaultdict
    per = {inst["name"]: read_trades(inst) for inst in ALBIONBASE_INSTRUMENTS}
    all_t = [t for tr in per.values() for t in tr]
    ov = _stats([t["pnl"] for t in all_t])
    deployed = STARTING_CAPITAL_PER_SYSTEM * len(ALBIONBASE_INSTRUMENTS)
    bi = ""
    for inst in ALBIONBASE_INSTRUMENTS:
        s = _stats([t["pnl"] for t in per[inst["name"]]])
        bi += "<tr><td>%s %s</td><td class='num'>%d</td><td class='num'>%.1f%%</td><td class='num'>%s</td><td class='num %s'>%s</td></tr>" % (
            inst["emoji"], inst["name"], s["n"], s["wr"], _pf(s["pf"]), _cls(s["net"]), _money(s["net"], plus=True))
    wk = defaultdict(list); mo = defaultdict(list)
    for t in all_t:
        d = t["date"]
        if len(d) >= 10:
            try:
                y, w, _ = datetime.strptime(d, "%Y-%m-%d").isocalendar(); wk[(y, w)].append(t["pnl"])
            except Exception: pass
            mo[d[:7]].append(t["pnl"])
    wkrows = "".join("<tr><td>%d-W%02d</td><td class='num'>%d</td><td class='num %s'>%s</td></tr>" % (y, w, len(v), _cls(sum(v)), _money(sum(v), plus=True)) for (y, w), v in sorted(wk.items())) or "<tr><td colspan=3 class='mut'>no data</td></tr>"
    morows = "".join("<tr><td>%s</td><td class='num'>%d</td><td class='num %s'>%s</td></tr>" % (k, len(v), _cls(sum(v)), _money(sum(v), plus=True)) for k, v in sorted(mo.items())) or "<tr><td colspan=3 class='mut'>no data</td></tr>"
    merged = sorted(all_t, key=lambda t: t.get("exit_time", "") or t.get("date", ""))
    eq = peak = maxdd = 0.0
    for t in merged:
        eq += t["pnl"]; peak = max(peak, eq); maxdd = max(maxdd, peak - eq)
    comp = ""
    for inst in ALBIONBASE_INSTRUMENTS:
        st, _ = fetch_state(inst); bal = ((st or {}).get("portfolio") or {}).get("balance")
        g = (" %+.1f%%" % (100 * (bal - STARTING_CAPITAL_PER_SYSTEM) / STARTING_CAPITAL_PER_SYSTEM)) if bal is not None else " --"
        comp += "<tr><td>%s %s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>" % (
            inst["emoji"], inst["name"], _money(STARTING_CAPITAL_PER_SYSTEM), _money(bal), g)
    body = ("<div class='card'><h2>Overall (since go-live %s)</h2>"
            "<div class='kv'><div class='l'>TRADES</div><div class='v'>%d</div></div>"
            "<div class='kv'><div class='l'>WIN RATE</div><div class='v'>%.1f%%</div></div>"
            "<div class='kv'><div class='l'>PF</div><div class='v'>%s</div></div>"
            "<div class='kv'><div class='l'>NET</div><div class='v %s'>%s</div></div>"
            "<div class='kv'><div class='l'>ON DEPLOYED</div><div class='v'>%s</div></div>"
            "<div class='kv'><div class='l'>MAX DRAWDOWN</div><div class='v neg'>-%s</div></div>"
            "<div class='kv'><div class='l'>RUNNING COST</div><div class='v costs'>£0.00 ✅</div></div></div>"
            % (GO_LIVE_DATE, ov["n"], ov["wr"], _pf(ov["pf"]), _cls(ov["net"]), _money(ov["net"], plus=True), _money(deployed), _money(maxdd)))
    body += "<div class='card'><h2>By instrument</h2><table><tr><th>System</th><th class='num'>Trades</th><th class='num'>WR</th><th class='num'>PF</th><th class='num'>Net</th></tr>%s</table></div>" % bi
    body += "<div class='card'><h2>By week (ISO)</h2><table><tr><th>Week</th><th class='num'>Trades</th><th class='num'>Net</th></tr>%s</table></div>" % wkrows
    body += "<div class='card'><h2>By month</h2><table><tr><th>Month</th><th class='num'>Trades</th><th class='num'>Net</th></tr>%s</table></div>" % morows
    body += "<div class='card'><h2>Compounding tracker</h2><table><tr><th>System</th><th class='num'>Start</th><th class='num'>Current</th><th class='num'>Growth</th></tr>%s</table></div>" % comp
    return _shell("ALBIONBASE PERFORMANCE TRACKER", body, "perf")

def render_archie_brief():
    rows, portfolio, _ = build_report()
    L = ["ALBIONBASE ARCHIE BRIEF", "Generated: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "",
         "PORTFOLIO",
         "  Balance: %s | Today: %s | Systems: %d/%d live" % (
             _money(portfolio["total_balance"]), _money(portfolio["today"], plus=True),
             portfolio["systems_online"], portfolio["systems_total"]),
         "", "OPEN POSITIONS"]
    op = [r for r in rows if r["in_trade"]]
    if op:
        for r in op:
            L.append("  %s %s %s | floating %s | locked %s" % (
                r["emoji"], r["name"], r["direction"], _money(r["floating"], plus=True),
                _money(r["locked"], plus=True) if r["locked"] else "--"))
    else:
        L.append("  (all flat)")
    L += ["", "PERFORMANCE (since go-live)"]
    for r in rows:
        p = r["perf"]
        if p.get("available"):
            L.append("  %s %-6s %d trades | WR %.0f%% | PF %s | net %s" % (
                r["emoji"], r["name"], p["trades"], p["wr"], _pf(p["pf"]), _money(p["net"], plus=True)))
    L += ["", "ALERTS"]
    alerts = []
    for r in rows:
        if not r["online"] and not r["cached"]: alerts.append("  %s OFFLINE" % r["name"])
        p = r["perf"]
        if p.get("available") and p["trades"] >= 5 and p["pf"] < 1.0:
            alerts.append("  %s PF below 1 (%s)" % (r["name"], _pf(p["pf"])))
    L += alerts or ["  none"]
    text = "\n".join(L)
    body = ("<div class='card'><pre id='brief'>" + text + "</pre>"
            "<button class='copy' onclick=\"navigator.clipboard.writeText(document.getElementById('brief').innerText)\">Copy to clipboard</button></div>")
    return _shell("ALBIONBASE ARCHIE BRIEF", body, "brief")

def gaius_data():
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "instruments": {}, "portfolio": {}}
    all_pnls = []
    for inst in ALBIONBASE_INSTRUMENTS:
        tr = read_trades(inst); s = _stats([t["pnl"] for t in tr]); all_pnls += [t["pnl"] for t in tr]
        out["instruments"][inst["name"]] = {
            "trades": [{"date": t["date"], "direction": t["direction"], "entry": t["entry"], "exit": t["exit"],
                        "points": t["points"], "pnl_gbp": t["pnl"], "exit_reason": t["reason"],
                        "stake": t["stake"], "balance_after": t["balance_after"]} for t in tr],
            "summary": {"trades": s["n"], "wins": s["w"], "losses": s["l"], "win_rate": round(s["wr"] / 100, 3),
                        "profit_factor": (None if s["pf"] == float("inf") else round(s["pf"], 3)),
                        "net_pnl": round(s["net"], 2)}}
    ps = _stats(all_pnls); deployed = STARTING_CAPITAL_PER_SYSTEM * len(ALBIONBASE_INSTRUMENTS)
    out["portfolio"] = {"total_trades": ps["n"], "win_rate": round(ps["wr"] / 100, 3),
                        "profit_factor": (None if ps["pf"] == float("inf") else round(ps["pf"], 3)),
                        "net_pnl": round(ps["net"], 2), "starting_capital": deployed,
                        "current_balance": round(deployed + ps["net"], 2)}
    return out


# ── daily summary Pushover (brief Part 5: ONE 21:00 UTC message across all instruments) ────────
def _pushover_send(title, message):
    if not LIVE_NOTIFICATIONS or not PUSHOVER_USER_KEY or not PUSHOVER_API_TOKEN:
        return False
    try:
        import urllib.parse
        data = urllib.parse.urlencode({"token": PUSHOVER_API_TOKEN, "user": PUSHOVER_USER_KEY,
                                       "title": title, "message": message}).encode()
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=data, timeout=6)
        return True
    except Exception:
        return False

def build_daily_summary():
    rows, portfolio, _ = build_report()
    lines = ["%s %-5s %s" % (r["emoji"], r["name"], _money(r["today"], plus=True)) for r in rows]
    lines.append("TODAY:    " + _money(portfolio["today"], plus=True))
    lines.append("TOTAL POT: %s (%s)" % (_money(portfolio["total_pot"]), portfolio["account_type"]))
    return "AlbionBase Daily -- " + _money(portfolio["today"], plus=True), "\n".join(lines)

def _daily_summary_scheduler():
    import time
    last = None
    while True:
        try:
            now = datetime.now(timezone.utc); key = now.strftime("%Y-%m-%d")
            if now.hour == DAILY_SUMMARY_HOUR_UTC and last != key:
                t, m = build_daily_summary(); _pushover_send(t, m); last = key
        except Exception:
            pass
        time.sleep(30)


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

@app.route("/performance")
def performance():
    return Response(render_performance_page(), mimetype="text/html")

@app.route("/archie-brief")
def archie_brief():
    return Response(render_archie_brief(), mimetype="text/html")

@app.route("/monitor")
def monitor():
    body = ("<div class='card'><h2>System Monitor</h2>"
            "Hardware/process monitor runs on <b>port 5015</b> (AlbionMonitorAI). "
            "<a href='http://localhost:5015' style='color:#58a6ff'>Open System Monitor &rarr;</a>"
            "<div class='mut' style='margin-top:8px;'>K1 hardware metrics + per-system heartbeats activate "
            "once Tailscale Phase 2 is configured. Dell metrics available now via :5015.</div></div>")
    return Response(_shell("SYSTEM MONITOR", body, "mon"), mimetype="text/html")

@app.route("/api/gaius-data")
def api_gaius():
    return Response(json.dumps(gaius_data(), default=str, indent=2), mimetype="application/json")

@app.route("/api/daily-summary")
def api_daily_summary():
    t, m = build_daily_summary()
    return Response(json.dumps({"title": t, "message": m}), mimetype="application/json")

@app.route("/<slug>")
def instrument_page(slug):
    inst = find_instrument(slug)
    if not inst:
        return Response("Unknown instrument: " + slug, status=404, mimetype="text/plain")
    return Response(render_instrument_page(inst), mimetype="text/html")


if __name__ == "__main__":
    import threading
    threading.Thread(target=_daily_summary_scheduler, daemon=True).start()   # 21:00 UTC daily Pushover
    print("AlbionBase Reporter v%s -- Command Centre on http://localhost:%d" % (VERSION, PORT))
    app.run(host="0.0.0.0", port=PORT, threaded=True)
