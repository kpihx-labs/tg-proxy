# Changelog

## 1.2.1 (2026-08-15)

### KπX naming-convention alignment

- **Repo root renamed** `tg_proxy` → `tg-proxy` (kebab-case, matching the KπX `xxx-yyy`
  project-root convention) and moved under `~/KpihX-Labs/proxies/`. The Python package inside
  `src/` remains `tg_proxy` (underscore, as required) and `pyproject.toml` `name = "tg-proxy"`
  was already correct — no code or packaging change needed.
- **Config dir confirmed aligned:** every reference points to `~/.config/tg-proxy/` (hyphen) —
  `src/tg_proxy/config.py` (`CONFIG_DIR`), `src/tg_proxy/client.py` (`TG_DATA_DIR`), docstrings,
  README, AGENTS.md and the tests (which use `tmp_path / "tg-proxy"`). No `~/.tg_proxy` or
  `~/.config/tg_proxy` underscore variant remains.
- **Fixed stale editable install:** the installed `tg-proxy` binary was broken
  (`ModuleNotFoundError: No module named 'tg_proxy'`) because the uv-tool receipt still pointed at
  the pre-move editable path. Reinstalled as a regular install
  (`uv tool uninstall` + `make uv-install`); `tg-proxy --version` now reports `1.2.1` and the
  binary resolves to `~/.local/share/uv/tools/tg-proxy/`.
- **Verified:** `make check` → ruff 0 errors, pyright 0 errors, CLI smoke passed, 28/28 tests green.

## 1.2.0 (2026-08-12)

### Admin lifecycle and local credential hardening

- **Admin parity:** added HITL-protected `tg-proxy admin reset` (removes `.env` and the local
  Telethon session while retaining the secured config directory) and `admin purge` (removes the
  configuration directory and prints the explicit final `uv tool uninstall tg-proxy` step instead
  of uninstalling the running process).
- **Detailed status:** `admin status` now follows the `tick-proxy` diagnostics contract: unmasked
  Telegram API ID, masked API hash and each stored bot token, binary path, session presence, and
  per-path `{path, mode, status, fix}` records for the configuration directory, `.env`, and
  `user.session`. Authorization failures remain a JSON warning and do not hide local diagnostics.
- **Permission invariants:** setup creates the configuration directory at `0700`, writes `.env` at
  `0600`, protects `user.session` at `0600`, and every BotFather token write preserves those modes.
  `.env.example` documents the managed keys and the permission policy.
- **Delivery simplification:** removed the untested container and external CI files, Makefile targets,
  and all project-owned documentation/source references. Releases are now `check → git-push →
  uv-publish` only.

## 1.1.1 (2026-08-09)

### Truth-in-documentation pass: version desync + HITL port docs corrected

Found while `tg-proxy` was being read as the ADN reference for the new `tick-proxy` project
(`$HOME/KpihX-Labs/tick_proxy/`). Both were pre-existing inconsistencies between code and docs.

- **Version desync fixed:** `pyproject.toml` still declared `version = "1.0.0"` while `CHANGELOG.md`
  documented 1.1.0 as released — so the installed binary reported `tg-proxy v1.0.0` and any
  `uv build` / `uv publish` would have shipped the wrong version number. `pyproject.toml` is now the
  truth again (`1.1.1`, this entry). Verified end-to-end: `make uv-install` → `tg-proxy --version`.
- **HITL port documentation corrected (4 files + 1 docstring):** `AGENTS.md`, `CONTRACT.md` (×2),
  `README.md` and the `hitl.py` module docstring all claimed the HITL web UI listens on a **fixed
  port 1143**. The code has always bound an **OS-assigned free port** via `_find_free_port()`
  (`bind(("", 0))`). The docs now describe the real behavior and, more importantly, explain **why**
  a fixed port is deliberately avoided: two concurrent `tg-proxy do` invocations would collide on it
  and the second HITL server would fail to bind. The chosen port is printed with the review URL on
  every invocation, so callers never need to guess it.
- **Workspace debris removed:** stale editor backups `src/tg_proxy/cli.py~` (32 KB, 2026-07-26) and
  `src/tg_proxy/client.py~` (40 KB, 2026-07-26) moved out of the package directory to `/tmp`
  (preserved as `.bak`, never deleted — kernel `.bak` rule). They were gitignored but still sat
  inside `src/tg_proxy/`, polluting the package tree and matching content greps.
- **Command count corrected in `README.md`:** the `do` section header claimed **23 commands** while
  its own table already listed **24** rows and the CLI registers **24** actions
  (`bot-create`, `bot-delete`, `bot-info`, `bot-list`, `bot-photo`, `bot-send`, `bot-send-file`,
  `bot-token`, `chat-delete`, `chat-delete-messages`, `chat-download`, `chat-list`, `chat-move`,
  `chat-read`, `chat-send`, `chat-send-file`, `folder-delete`, `folder-list`, `folder-set`, `raw`,
  `updates`, `webhook-del`, `webhook-get`, `webhook-set`). The undercount came from
  `chat-delete-messages`, whose `@app_do.command(...)` decorator is split across three lines
  (`cli.py:670-672`) and is therefore invisible to single-line greps — exactly the trap that
  produced the stale "23". Header now says 24, matching both the table and the runtime.
- **Verified:** `make check` → 25 tests passed, pyright 0 errors, CLI smoke passed;
  `tg-proxy --version` → `1.1.1` after `make uv-install`; `tg-proxy do --help` lists exactly 24
  actions; `_find_free_port()` returns 5 distinct high ports across successive calls; no `1143`
  reference remains in any source or documentation file.

## 1.1.0 (2026-07-26)

### Production hardening: type_hints, autosave refactor, dead code removal

- **`raw` command — type_hints now functional:** Wire `payload.type_hints` into mtproto handler (client.py:1648-1668) — wraps string params in Telethon TLObject types before passing to request constructor. Double-try pattern handles both value-arg types (`InputUser(id=123)`) and marker types (`InputPrivacyKeyStatusTimestamp()`).
- **Autosave dir extracted to constant:** `TG_PROXY_AUTOSAVE_DIR` at top of cli.py — both usages (execute + _write_and_display) now reference the constant.
- **Autosave naming `last_` removed:** Files now `{action}_{timestamp}.json` instead of `last_{action}.json` (both paths).
- **Dead code purged:** `autosave_output()` removed from display.py — was defined but never called, used old `last_` format.
- **Verified in production:** `help.getNearestDc` ✅ + `account.getPrivacy` with `InputPrivacyKeyStatusTimestamp` ✅ — 2 real tmux+HITL executions, both passed.
- **Cleaner imports:** `from datetime import datetime` moved to global imports (top of cli.py).

## 1.0.0 (2026-07-25)

### Major refactoring — complete rewrite as tg-proxy

- **Architecture:** Single binary with `do` (RPC) + `admin` namespaces (inspired by ts_proxy)
- **Config:** Single `.env` at `~/.config/tg-proxy/.env` — no more `config.yaml`, no more per-bot tokens
- **HITL:** 100% web UI for 7 methods: admin-setup, bot-token, bot-create, bot-delete, bot-send, bot-send-file, chat-send-file, **folder-delete**
- **Bot discovery:** `getAdminedBots` — list ALL owned bots without any token
- **RPC:** Pure JSON-RPC style with payload as inline JSON or file path
- **Output:** Unified `meta + data` format — JSON default, table via `--format/-f`
- **Doc system:** `doc.py` with structured docstrings + Pydantic schema injection into `--help`
- **Maximum privacy:** `bot-create` auto-disables groups and inline mode
- **All 22 commands docstrings have Parameters sections**
- **`folder-list` docstring now has Parameters** (was missing)

### Session 2026-07-26 — 15+ bugs fixed, 22 commands

#### New commands
- **`chat-delete`** — delete entire conversation via `client.delete_dialog()`
- **`chat-delete-messages`** — delete specific messages via `client.delete_messages()`
- **`bot-photo`** — download profile photo from any bot/user via Telethon
- **`folder-list`** — list Telegram chat folders with chats
- **`folder-set`** — create/update folders (UPSERT)
- **`folder-delete`** — delete folder by title (HITL)
- **`chat-move`** — move chat between folders

#### Enriched features
- **`chat-list` now shows `folders`** — cross-references Telegram dialog filters
- **`bot-info` now shows `photo_info`** — photo_id, dc_id, has_video, size

#### BotFather protocol
- **BF_NOTE** — mandatory note in ALL BotFather methods, ALL output paths (success, error, exception)
- **bot-delete fix:** `"Yes, delete it"` → `"Yes, I am totally sure."` — BotFather requires exact text
- **BotFather rate limit** — detected and handled gracefully ("too many attempts")
- **`/setuserpic` flow proven** — S25 bot got Ubuntu's profile photo via BotFather conversation

#### Folder management patterns
- `_title_str()` helper for `TextWithEntities` conversion
- `_peer_id()` helper for peer ID extraction
- `DialogFilterDefault` checks (system filters without `.id`)
- `DialogFilters.filters` property (5 occurrences)

#### Quality
- **All `except Exception:` → specific exceptions** — ZERO `# noqa`
- **6 pyright errors silenced with precise `# type: ignore[code]`** — only Telethon stub false positives
- **Makefile `||` removed** — real pyright errors now block pre-commit hook
- **`make check`:** 0 ruff, 0 pyright errors, 25/25 tests
- **git pre-commit hook installed** — runs `make check`
- **IDEAS.md created** — `do raw` generic gateway concept

#### Repositories renamed
- GitHub: `KpihX/tg` → `KpihX/tg-proxy`
- GitLab: `kpihx/tg` → `kpihx/tg-proxy`

### Major refactoring — complete rewrite as tg-proxy

- **Architecture:** Single binary with `do` (RPC) + `admin` namespaces (inspired by ts_proxy)
- **Config:** Single `.env` at `~/.config/tg-proxy/.env` — no more `config.yaml`, no more per-bot tokens
- **HITL:** 100% web UI for sensitive operations (bot-token, bot-create, bot-delete, bot-send, admin-setup)
- **Bot discovery:** `getAdminedBots` — list ALL owned bots without any token
- **RPC:** Pure JSON-RPC style with payload as inline JSON or file path
- **Output:** Unified `meta + data` format — JSON default, table via `--format/-f`
- **Doc system:** `doc.py` with structured docstrings + Pydantic schema injection into `--help`
- **Maximum privacy:** `bot-create` auto-disables groups and inline mode

### Removed / discontinued

- All `TELEGRAM_*_TOKEN` env vars — tokens retrieved from BotFather on demand (HITL)
- `config.yaml`, `config.py` — replaced by minimal `.env` loader
- Separate `cli.py` → unified single-app CLI
- `cmd-get/set`, `status`, `me`, `chat-info`, `chat-admins`, `admin contacts`
- Agent-specific files: COPILOT.md, GEMINI.md, CLAUDE.md, VIBE.md
- `.agent/` subfolder — AGENTS.md lives directly in the project root

### Old code

Previous `tg` (v0.3.0, uv-tool) source is preserved at `old/src/`.
