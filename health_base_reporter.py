"""
health_base_reporter.py -- desk-health snapshot for the Reporter (brief 21 Aug 2026).

Answers "is the DESK alive and in the right state" -- the two blind spots the Capital.com app can't cover:
  1. after a reboot RoundTableBase forces DEMO (Rule 13): the desk looks healthy while NOT trading real money;
  2. a dead machine and a quiet machine look identical.

SELF-CONTAINED: vendors a tiny READ-ONLY Capital.com client (no camelot_engine dependency -- K1 currently runs
the pre-engine GoldBase). BROKER-DIRECT for positions/agreement (Rule 12: never trust the engine's in_trade /
state file for the ground truth). EVERY collector is wrapped so a failure returns a safe default and NEVER
raises -- monitoring must not become a liability.
"""
import csv as _csv
import glob as _glob
import json as _json
import os
import subprocess
import socket
import ctypes
from datetime import datetime, timezone

import config_base_reporter as cfg

try:
    import requests as _rq
except Exception:
    _rq = None

_UTC = timezone.utc
def _now(): return datetime.now(_UTC)
def _iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None
_BROKER_LAST_OK = {"ts": None}   # persists across calls within the process


# ── dead-man's switch: outbound "still alive" heartbeat (NO account data). Never raises; hard-bounded. ─────────
_HB = {"last_attempt": None, "last_ok": None, "last_error": None, "ok": None}

def heartbeat_once():
    """One outbound ping to HEARTBEAT_URL. BARE signal -- no body, no account data, no creds leave the process.
    Hard timeout so a hanging endpoint cannot block beyond it. Swallows EVERYTHING -- can never raise."""
    url = cfg.HEARTBEAT_URL
    if not url:
        return
    _HB["last_attempt"] = _iso(_now())
    try:
        if _rq is None:
            raise RuntimeError("requests unavailable")
        # NO payload -> nothing about the account/positions/balance ever leaves. timeout = (connect, read).
        to = (float(cfg.HEARTBEAT_TIMEOUT_SEC), float(cfg.HEARTBEAT_TIMEOUT_SEC))
        r = (_rq.post(url, timeout=to) if cfg.HEARTBEAT_METHOD == "POST" else _rq.get(url, timeout=to))
        r.raise_for_status()
        _HB["ok"] = True; _HB["last_ok"] = _iso(_now()); _HB["last_error"] = None
    except Exception as exc:
        _HB["ok"] = False; _HB["last_error"] = str(exc)[:140]

def heartbeat_status():
    st = {"enabled": bool(cfg.HEARTBEAT_URL), "last_attempt": _HB["last_attempt"], "last_ok": _HB["last_ok"],
          "last_error": _HB["last_error"], "ok": _HB["ok"], "stale": False}
    if st["enabled"]:                                  # stale = enabled but no success within 2x the interval
        if not st["last_ok"]:
            st["stale"] = True
        else:
            try:
                last = datetime.strptime(st["last_ok"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC)
                st["stale"] = (_now() - last).total_seconds() > 2 * cfg.HEARTBEAT_INTERVAL_MIN * 60
            except Exception:
                st["stale"] = True
    return st

def heartbeat_loop():
    """OWN daemon thread. Each iteration guarded so it never dies and never raises into anything else."""
    import time
    while True:
        try:
            heartbeat_once()
        except Exception:
            pass
        try:
            time.sleep(max(60.0, float(cfg.HEARTBEAT_INTERVAL_MIN) * 60.0))
        except Exception:
            time.sleep(900.0)


# ── tiny READ-ONLY Capital.com client (auth + positions + balance) ───────────────────────────────
class _CapRO:
    def __init__(self, mode):
        acct = "LIVE" if str(mode).upper() == "LIVE" else "DEMO"
        self.base = cfg.CAPITALCOM_LIVE_BASE_URL if acct == "LIVE" else cfg.CAPITALCOM_DEMO_BASE_URL
        self.email = os.getenv("CAPITALCOM_EMAIL", "").strip()
        self.key = os.getenv("CAPITALCOM_%s_KEY" % acct, "").strip()
        self.pwd = os.getenv("CAPITALCOM_%s_PASSWORD" % acct, "").strip()
        self.cst = self.sec = None

    def connect(self):
        if _rq is None or not (self.email and self.key and self.pwd):
            return False
        r = _rq.post(self.base + "/session",
                     headers={"X-CAP-API-KEY": self.key, "Content-Type": "application/json"},
                     json={"identifier": self.email, "password": self.pwd, "encryptedPassword": False}, timeout=8)
        r.raise_for_status()
        self.cst = r.headers.get("CST"); self.sec = r.headers.get("X-SECURITY-TOKEN")
        return bool(self.cst and self.sec)

    def _h(self):
        return {"X-CAP-API-KEY": self.key, "CST": self.cst, "X-SECURITY-TOKEN": self.sec}

    def positions(self):
        r = _rq.get(self.base + "/positions", headers=self._h(), timeout=8); r.raise_for_status()
        return r.json().get("positions", [])

    def balance(self):
        r = _rq.get(self.base + "/accounts", headers=self._h(), timeout=8); r.raise_for_status()
        for a in r.json().get("accounts", []):
            b = (a.get("balance") or {}).get("balance")
            if b is not None:
                return float(b)
        return None


# ── individual collectors (each: never raises) ───────────────────────────────────────────────────
def _mode():
    try:
        import trading_mode
        return trading_mode.read_mode()
    except Exception:
        return "UNKNOWN"

def _uptime_seconds():
    try:
        return int(ctypes.windll.kernel32.GetTickCount64() // 1000)   # ms since boot -> s (Windows)
    except Exception:
        return None

def _human_dur(sec):
    if sec is None:
        return "unknown"
    d, r = divmod(int(sec), 86400); h, r = divmod(r, 3600); m, _ = divmod(r, 60)
    return ("%dd %dh %dm" % (d, h, m)) if d else (("%dh %dm" % (h, m)) if h else "%dm" % m)

def _port_up(port):
    try:
        s = socket.socket(); s.settimeout(1.0)
        ok = s.connect_ex(("127.0.0.1", int(port))) == 0
        s.close(); return ok
    except Exception:
        return False

def _processes():
    out = {}
    for tok in cfg.HEALTH_PROCS.split(","):
        tok = tok.strip()
        if not tok or "=" not in tok:
            continue
        name, port = tok.split("=", 1)
        out[name.strip()] = _port_up(port.strip())
    return out

def _gold_state():
    """Engine's own view (for the AGREEMENT cross-check only -- never the ground truth)."""
    try:
        gold = next((i for i in cfg.ALBIONBASE_INSTRUMENTS if i["ticker"] == "GOLD"), None)
        if not gold or _rq is None:
            return {}
        r = _rq.get(cfg.state_url(gold), timeout=cfg.FETCH_TIMEOUT_SEC); r.raise_for_status()
        return r.json()
    except Exception:
        return {}

def _pos_report(positions):
    rep = []
    for p in positions or []:
        pos = p.get("position") or {}; mkt = p.get("market") or {}
        rep.append({
            "instrument": mkt.get("instrumentName") or mkt.get("epic"),
            "epic": mkt.get("epic"),
            "direction": pos.get("direction"),
            "entry": pos.get("level"),
            "stop": pos.get("stopLevel"),
            "target": pos.get("profitLevel", pos.get("limitLevel")),
            "deal_id": pos.get("dealId"),
        })
    return rep

def _broker(mode):
    """d/e: broker reachable + last-ok + open positions + balance (BROKER-DIRECT). Returns a dict; never raises."""
    b = {"reachable": False, "last_ok": None, "down_for": None, "positions": None, "balance": None, "note": None}
    try:
        c = _CapRO(mode)
        if not c.connect():
            b["note"] = "no creds / connect failed"
        else:
            b["reachable"] = True
            _BROKER_LAST_OK["ts"] = _now()
            b["positions"] = _pos_report(c.positions())
            b["balance"] = c.balance()
    except Exception as exc:
        b["note"] = str(exc)[:120]
    b["last_ok"] = _iso(_BROKER_LAST_OK["ts"])
    if not b["reachable"] and _BROKER_LAST_OK["ts"]:
        b["down_for"] = _human_dur((_now() - _BROKER_LAST_OK["ts"]).total_seconds())
    return b

def _agreement(broker, gold_state):
    """f: does the engine's in_trade match the broker's actual position? (19 Aug phantom failure mode.)"""
    try:
        if not broker.get("reachable") or broker.get("positions") is None:
            return {"checked": False, "disagree": False, "detail": "broker unreachable -- cannot compare"}
        broker_gold = any((p.get("epic") or "").upper() == "GOLD" for p in broker["positions"])
        engine_in_trade = bool(gold_state.get("in_trade")) if gold_state else None
        if engine_in_trade is None:
            return {"checked": False, "disagree": False, "detail": "engine state unavailable"}
        disagree = (broker_gold != engine_in_trade)
        detail = ("engine says %s, broker says %s" %
                  ("IN TRADE" if engine_in_trade else "FLAT", "HAS POSITION" if broker_gold else "FLAT"))
        return {"checked": True, "disagree": disagree, "detail": ("DISAGREE -- " + detail) if disagree else ("agree (" + detail + ")")}
    except Exception as exc:
        return {"checked": False, "disagree": False, "detail": "compare error: %s" % str(exc)[:80]}

def _today_pnl():
    """g: sum today's realised P&L across instrument trade CSVs (date column, UTC)."""
    total = 0.0; found = False
    today = _now().strftime("%Y-%m-%d")
    for inst in cfg.ALBIONBASE_INSTRUMENTS:
        try:
            p = cfg.log_path(inst)
            if not os.path.exists(p):
                continue
            with open(p, newline="", encoding="utf-8", errors="replace") as f:
                for row in _csv.DictReader(f):
                    if (row.get("date") or "").strip() == today:
                        try:
                            total += float(row.get("pnl_gbp")); found = True
                        except (TypeError, ValueError):
                            pass
        except Exception:
            pass
    return round(total, 2) if found else 0.0

def _reconcile(broker):
    """h: light order-audit-vs-broker cross-check. Compares the NET open count our order_audit.csv implies
    (ACCEPTED OPENs minus ACCEPTED/ALREADY_CLOSED CLOSEs over the WHOLE log -- robust to a position held for
    days, which a fixed time window would false-flag) against the broker's actual GOLD position count.
      net_audit > broker  -> AUDIT_ONLY (we recorded an open the broker doesn't have)
      net_audit < broker  -> BROKER_ONLY (a broker position we never recorded)
    Read-only; never raises. NOTE: reads the audit log of the configured GoldBase repo -- accurate on K1 (which
    runs that repo); on Dell the running demo is GoldBaseAI-engine, so a mismatch here is a path artifact."""
    try:
        if not broker.get("reachable") or broker.get("positions") is None:
            return {"available": False, "count": 0, "detail": "broker unreachable"}
        gold = next((i for i in cfg.ALBIONBASE_INSTRUMENTS if i["ticker"] == "GOLD"), None)
        audit = os.path.join(cfg._LOGS_BASE, gold["repo"], "logs", "order_audit.csv") if gold else None
        if not (audit and os.path.exists(audit)):
            return {"available": False, "count": 0, "detail": "order_audit.csv not reachable"}
        opens = closes = rows = 0
        with open(audit, newline="", encoding="utf-8", errors="replace") as f:
            for r in _csv.DictReader(f):
                if r.get("epic") != "GOLD":
                    continue
                rows += 1
                if r.get("action") == "OPEN" and r.get("outcome") == "ACCEPTED":
                    opens += 1
                elif r.get("action") == "CLOSE" and r.get("outcome") in ("ACCEPTED", "ALREADY_CLOSED"):
                    closes += 1
        if rows == 0:
            return {"available": False, "count": 0, "detail": "no GOLD audit rows -- cannot cross-check"}
        net_open_audit = max(0, opens - closes)
        broker_gold = sum(1 for p in broker["positions"] if (p.get("epic") or "").upper() == "GOLD")
        mm = []
        if net_open_audit > broker_gold:
            mm.append("AUDIT_ONLY: audit implies %d open GOLD vs %d at broker" % (net_open_audit, broker_gold))
        elif net_open_audit < broker_gold:
            mm.append("BROKER_ONLY: %d broker GOLD position(s) vs %d recorded open" % (broker_gold, net_open_audit))
        return {"available": True, "count": len(mm), "detail": "; ".join(mm) if mm else "clean (audit net-open matches broker)"}
    except Exception as exc:
        return {"available": False, "count": 0, "detail": "reconcile error: %s" % str(exc)[:80]}

def _code_fingerprint(gold_state):
    """i: consumer commit hash (FINGERPRINT) + engine version/commit if the engine is installed."""
    out = {"consumer_commit": None, "engine_version": None, "engine_commit": None}
    try:
        gold = next((i for i in cfg.ALBIONBASE_INSTRUMENTS if i["ticker"] == "GOLD"), None)
        repo = os.path.join(cfg._LOGS_BASE, gold["repo"]) if gold else None
        if repo and os.path.isdir(os.path.join(repo, ".git")):
            out["consumer_commit"] = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                                                    capture_output=True, text=True, timeout=8).stdout.strip()[:40] or None
    except Exception:
        pass
    try:
        import camelot_engine as _ce
        out["engine_version"] = getattr(_ce, "__version__", None)
        sp = os.path.dirname(os.path.dirname(_ce.__file__))
        du = _glob.glob(os.path.join(sp, "camelot_engine-*.dist-info", "direct_url.json"))
        if du:
            out["engine_commit"] = (_json.load(open(du[0])).get("vcs_info") or {}).get("commit_id")
    except Exception:
        out["engine_version"] = out["engine_version"] or "n/a (pre-engine)"
    if not out["engine_version"] and gold_state:
        out["engine_version"] = gold_state.get("version")
    return out

def _reboot_pending():
    """j: Windows update / servicing reboot pending?"""
    try:
        import winreg
        for hive, path in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")):
            try:
                winreg.CloseKey(winreg.OpenKey(hive, path)); return True
            except FileNotFoundError:
                continue
        return False
    except Exception:
        return None   # unknown


# ── assemble ─────────────────────────────────────────────────────────────────────────────────────
def collect_health():
    """Full health snapshot. HIGH-severity alerts (force priority 1): DEMO-on-live, missing process, broker
    unreachable, engine/broker disagreement, reconcile mismatch. Never raises."""
    h = {"ts_utc": _iso(_now()), "env_label": cfg.ENV_LABEL, "alerts": [], "info": []}
    try:
        mode = _mode(); h["mode"] = mode
        gold_state = _gold_state()

        # b uptime
        up = _uptime_seconds(); h["uptime_seconds"] = up; h["uptime"] = _human_dur(up)
        if up is not None and up < cfg.HEALTH_RECENT_BOOT_HOURS * 3600:
            h["info"].append("Recent reboot (uptime %s) -- was it planned?" % h["uptime"])

        # a mode (HIGH if DEMO on a live machine)
        if mode != "LIVE" and cfg.ENV_LABEL == "LIVE":
            h["alerts"].append("MODE: desk is in %s on a LIVE machine -- NOT trading real money" % mode)

        # c processes
        procs = _processes(); h["processes"] = procs
        missing = [n for n, u in procs.items() if not u]
        if missing:
            h["alerts"].append("PROCESS: missing -- " + ", ".join(missing))

        # d/e broker + positions
        broker = _broker(mode); h["broker"] = broker
        if not broker["reachable"]:
            h["alerts"].append("BROKER: Capital.com unreachable" + (" for %s" % broker["down_for"] if broker["down_for"] else ""))

        # f agreement
        agree = _agreement(broker, gold_state); h["agreement"] = agree
        if agree.get("disagree"):
            h["alerts"].append("AGREEMENT: engine and broker DISAGREE -- " + agree["detail"])

        # g money
        bal = broker.get("balance"); today = _today_pnl()
        headroom = round(cfg.HEALTH_KILL_SWITCH_GBP + min(0.0, today), 2)
        h["money"] = {"balance": bal, "today_pnl": today, "kill_switch_gbp": cfg.HEALTH_KILL_SWITCH_GBP, "headroom": headroom}

        # h reconcile
        rec = _reconcile(broker); h["reconcile"] = rec
        if rec.get("count"):
            h["alerts"].append("RECONCILE: " + rec["detail"])

        # i code
        h["code"] = _code_fingerprint(gold_state)

        # j windows
        pend = _reboot_pending(); h["windows_reboot_pending"] = pend
        if pend:
            h["info"].append("Windows restart pending")

        # dead-man's switch status (1c: if the alarm itself is failing, SAY so -- never trust a silent alarm)
        hb = heartbeat_status(); h["heartbeat"] = hb
        if hb["enabled"] and hb["stale"]:
            h["alerts"].append("HEARTBEAT: dead-man's switch FAILING (last ok %s) -- the ALARM ITSELF is broken"
                               % (hb["last_ok"] or "never"))
    except Exception as exc:
        h["error"] = "collect_health error: %s" % str(exc)[:150]
    h["priority"] = 1 if h["alerts"] else 0    # HIGH iff a HIGH-severity alert fired
    return h


def format_health(h):
    """Phone-friendly Pushover (title + message + priority). Most alarming first."""
    mode = h.get("mode", "?"); pri = h.get("priority", 0)
    flag = "⚠️" if pri == 1 else "✅"        # warning / check
    title = "AlbionBase Health [%s] %s" % (mode, flag)
    lines = []
    for a in h.get("alerts", []):                          # HIGH lines first
        lines.append("⚠️ " + a)
    lines.append("Mode: %s" % mode)
    lines.append("Uptime: %s" % h.get("uptime", "?"))
    procs = h.get("processes", {})
    lines.append("Procs: " + " ".join(("%s%s" % (n, "✓" if u else "✗")) for n, u in procs.items()))
    b = h.get("broker", {})
    lines.append("Broker: " + ("reachable (last ok %s)" % b.get("last_ok") if b.get("reachable")
                               else "UNREACHABLE" + (" %s" % b.get("down_for") if b.get("down_for") else "")))
    posrep = b.get("positions")
    if posrep:
        for p in posrep:
            lines.append("Pos: %s %s @%s stop %s tp %s" % (p.get("instrument"), p.get("direction"),
                                                           p.get("entry"), p.get("stop"), p.get("target")))
    elif posrep == []:
        lines.append("Pos: flat (broker-direct)")
    ag = h.get("agreement", {})
    lines.append("Agreement: " + (ag.get("detail") or "n/a"))
    m = h.get("money", {})
    lines.append("Money: bal %s | today %s | kill-switch headroom £%s" % (
        ("£%.2f" % m["balance"]) if m.get("balance") is not None else "n/a",
        ("%+.2f" % m.get("today_pnl", 0.0)), m.get("headroom")))
    rec = h.get("reconcile", {})
    lines.append("Reconcile: " + (rec.get("detail") or "n/a"))
    c = h.get("code", {})
    cc = (c.get("consumer_commit") or "?")[:7]
    eng = c.get("engine_version") or "?"
    ecc = (" (%s)" % c.get("engine_commit")[:7]) if c.get("engine_commit") else ""
    lines.append("Code: GoldBase %s · engine %s%s" % (cc, eng, ecc))
    if h.get("windows_reboot_pending"):
        lines.append("Windows: RESTART PENDING")
    hb = h.get("heartbeat", {})
    if hb.get("enabled"):
        lines.append("Heartbeat: " + ("ok (last %s)" % hb.get("last_ok") if not hb.get("stale")
                                      else "FAILING (last ok %s)" % (hb.get("last_ok") or "never")))
    return title, "\n".join(lines), pri
