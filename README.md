# AlbionBaseReporterAI (port 5041)

The **reporting + notification layer** for the AlbionBase live desk (ACEMAGIC K1 go-live 17 Aug 2026).
Runs on the **Dell only** — reads the live systems read-only over HTTP (`/api/state`) + their trade CSVs.
**Never touches the live systems.** Pure-Lancelot desk → £0/day running cost.

_Port 5041 (5040 is held by a Windows svchost — WSAEACCES, confirmed 7 Aug 2026)._

## Design principles
1. **Separation of concerns** — K1 trades + writes data only; all reporting runs on the Dell.
2. **Instrument-agnostic** — adding a future instrument (DAX, NASDAQ, …) = **one dict** in
   `config_base_reporter.ALBIONBASE_INSTRUMENTS`. No other code changes.
3. **Live notifications only** — Percival fires for real-money K1 trades; Dell paper is silent.

## Config (`config_base_reporter.py`)
- `ALBIONBASE_INSTRUMENTS` — the single source of truth (name/ticker/port/currency/emoji/live_ip/repo/log_csv).
- `ALBIONBASE_K1_HOST` env var repoints every instrument to the K1 Tailscale IP in one place
  (defaults to `127.0.0.1`, so the reporter reads the **Dell** systems until K1/Tailscale is up).
- `ALBIONBASE_LOGS_BASE` — where the trade CSVs live (Dell repos now; Tailscale share on K1).

## Status (7 Aug 2026)
**BUILT + verified against the live Dell systems (Page 1).**
- `dashboard_base_reporter.py` — Page 1 Command Centre on :5041. Polls `/api/state` (HTTP, 5s timeout,
  graceful `OFFLINE`/cached fallback) + reads each trade CSV for WR/PF/net. Routes: `/`, `/api/systems`,
  `/api/health`. Auto-refresh 30s.
- Verified: 4/4 systems online, portfolio + per-system live status + performance table render correctly.

## Staged (not yet built)
- Individual instrument pages `/gold /oil /ftse /us` (Page 2), `/performance` (Page 3), `/monitor`
  (Page 4, links :5015), `/archie-brief` (Page 5), `/api/gaius-data` (Part 4).
- Shutdown controls → K1 `/api/shutdown` via Tailscale (rendered disabled until K1 live).
- main/watchdog split (3-process convention); START_ALBIONBASE.bat entry.
- **Compounding position sizing + LIVE_NOTIFICATIONS** live in the *trading* repos, not here.

## Architecture decisions (pending Nick confirmation)
- Per-machine flags (`USE_COMPOUNDING`, `LIVE_NOTIFICATIONS`) via **`.env`**, safe paper defaults.
- K1 runs engine + lightweight state endpoint (reporter polls it) + Tailscale file share for CSVs.
