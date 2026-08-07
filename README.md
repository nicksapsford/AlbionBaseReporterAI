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

## Status (7 Aug 2026) -- v1.1.0
**BUILT + verified against the live Dell systems. All reporting pages done.**
`dashboard_base_reporter.py` on :5041 (Flask). Polls `/api/state` (5s timeout, graceful `OFFLINE`/cached
fallback) + reads each trade CSV. Pages/routes:
- `/` -- **Page 1 Command Centre**: portfolio + per-system live status + performance table + nav.
- `/gold /oil /ftse /us500` -- **Page 2 instrument detail**: position, performance, compounding tracker,
  recent 10 trades, two-speed status (Gold). Prefix routing (`/us` works). Add an instrument = one config dict.
- `/performance` -- **Page 3**: overall, by-instrument, by-week (ISO), by-month, max drawdown, compounding.
- `/archie-brief` -- **Page 5**: pasteable brief (portfolio, open positions, performance, alerts) + copy button.
- `/monitor` -- **Page 4**: links the :5015 System Monitor (K1 metrics via Tailscale Phase 2).
- `/api/gaius-data` -- **Part 4**: full per-instrument trades + summaries + portfolio JSON for Gaius/Cody.
- `/api/systems`, `/api/health`.
Verified: all routes HTTP 200, data reads correctly from the 4 live systems.

## Staged (not yet built)
- Shutdown controls -> K1 `/api/shutdown` via Tailscale (rendered disabled until K1 live).
- main/watchdog split (3-process convention); START_ALBIONBASE.bat entry.
- **Compounding position sizing + LIVE_NOTIFICATIONS** are DONE, but live in the *trading* repos, not here.

## Architecture decisions (pending Nick confirmation)
- Per-machine flags (`USE_COMPOUNDING`, `LIVE_NOTIFICATIONS`) via **`.env`**, safe paper defaults.
- K1 runs engine + lightweight state endpoint (reporter polls it) + Tailscale file share for CSVs.
