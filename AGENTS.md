# tg-proxy — Agent Context

## Overview

tg-proxy is a Telegram administrative proxy — a single binary with two namespaces:
- `tg-proxy do <action> [payload]` — RPC operations (flat, pure JSON-RPC)
- `tg-proxy admin setup|status|reset|purge` — Admin operations (always JSON)

## Key Files

| File | Role |
|------|------|
| `src/tg_proxy/cli.py` | Typer CLI entry point (do + admin) |
| `src/tg_proxy/client.py` | TgClient — Telethon + MTProto + Bot API |
| `src/tg_proxy/models.py` | Pydantic RPC payloads |
| `src/tg_proxy/config.py` | .env loader (~/.config/tg-proxy/.env) |
| `src/tg_proxy/display.py` | Rich output helpers, table formatting |
| `src/tg_proxy/logger.py` | stderr logger for terminal and journald capture |
| `src/tg_proxy/exceptions.py` | Base exception class |
| `src/tg_proxy/hitl.py` | HITL web UI |
| `src/tg_proxy/doc.py` | Dynamic --help injection |
| `pyproject.toml` | Single entry point: tg-proxy = tg_proxy.cli:app |
| `Makefile` | check, uv-install, git-push, release... |
| `CONTRACT.md` | Architecture contract |

## Config

`~/.config/tg-proxy/.env` — contains `TG_API_ID`, `TG_API_HASH`, and BotFather-retrieved
`*_TOKEN` entries. `tg-proxy admin setup` creates the directory at `0700` and the file at `0600`;
all token writes preserve that policy. `admin status` shows the API ID, masked secret/token
inventory plus actual directory, file and session modes with a corrective command when a mode is
wrong.

No config.yaml or cache; bot tokens are stored only in the protected local `.env`.

## Bot Discovery

`bots.getAdminedBots` (MTProto) lists ALL owned bots without any token.

## RPC Pattern

After `do`, ONE flat action followed by payload (inline JSON or file path):

```bash
tg-proxy do bot-list
tg-proxy do bot-info '{"bots":["@bot1","@bot2"]}'
tg-proxy do bot-send '{"bot":"@bot","message":"Hello"}'
```

Meta options (only for do): `--output-file/-o <path>`, `--format/-f json|table`, `--help/-h`.

## HITL

100% Web UI on an OS-assigned free port (bind to port 0 — printed with the review URL, never fixed, so concurrent `do` invocations cannot collide). Required for: admin setup/reset/purge, bot-token, bot-create, bot-delete, bot-send, bot-send-file.

## Output

Always `{"meta": {"status": "...", "comment": "", "edited": false}, "data": {...}}`.
