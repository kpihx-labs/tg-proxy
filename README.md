# tg-proxy

Telegram administrative proxy — RPC CLI for bot and user management.

## Architecture

Single binary with two namespaces:

```bash
tg-proxy admin setup|status          # Admin operations (always JSON)
tg-proxy do <action> [payload|file]  # RPC actions (JSON default, table via --format)
```

### `tg-proxy admin`

| Command | Description |
|---------|-------------|
| `setup` | First-time auth via HITL web form (creates `~/.config/tg-proxy/.env`) |
| `status` | Your Telegram identity as JSON |

### `tg-proxy do` (RPC) — 24 commands

| Action | Description | HITL | Enriched |
|--------|-------------|:----:|:--------:|
| `bot-list` | List ALL owned bots (getAdminedBots) | ❌ | — |
| `bot-info` | Details for one or more bots | ❌ | **`photo_info`** ✅ |
| `bot-token` | Get token(s) — appends to `.env` | ✅ | — |
| `bot-create` | Create one or more bots (max privacy) | ✅ | — |
| `bot-delete` | Delete one or more bots | ✅ | — |
| `bot-send` | Send message AS a bot (to me) | ✅ | — |
| `bot-send-file` | Send message + files AS a bot | ✅ | — |
| **`bot-photo`** | **Download profile photo from any bot/user** | ❌ | — |
| `chat-list` | List conversations | ❌ | **`folders`** ✅ |
| `chat-read` | Read messages from a chat | ❌ | — |
| `chat-send` | Send message as you to anyone | ✅ | — |
| `chat-send-file` | Send message + files as you | ✅ | — |
| `chat-download` | Download media files by message_id(s) | ❌ | — |
| **`chat-delete`** | **Delete entire conversation** | ❌ | — |
| **`chat-delete-messages`** | **Delete specific messages** | ❌ | — |
| **`folder-list`** | **List Telegram chat folders with chats** | ❌ | — |
| **`folder-set`** | **Create/update folder (UPSERT)** | ❌ | — |
| **`folder-delete`** | **Delete folder by title** | ✅ | — |
| **`chat-move`** | **Move chat between folders** | ❌ | — |
| `updates` | Read bot's inbox | ❌ | — |
| `webhook-get` | Show webhook configuration | ❌ | — |
| `webhook-set` | Set webhook URL | ❌ | — |
| `webhook-del` | Delete webhook | ❌ | — |
| **`raw`** | **Generic Telegram gateway (mtproto/botapi/bf)** | ✅ | **`type_hints`** ✅ |

**`raw`** — execute ANY Telegram API call via one of three protocols (`"mtproto"`, `"botapi"`, `"bf"`). Supports `type_hints` to map string params to Telethon TLObject types for typed MTProto calls. Every execution autosaves to `/tmp/tg-proxy-autosave/{action}_{timestamp}.json`.

### Enriched features

- **`bot-info`** now includes **`photo_info`** (has_photo, photo_id, dc_id, has_video, size) — fetched via Telethon's `get_profile_photos()`
- **`chat-list`** now includes **`folders`** — cross-references Telegram dialog filters (`GetDialogFiltersRequest`) to show which folder each chat belongs to

### BotFather operations

- **`bot-create`** automates `/newbot` + privacy settings
- **`bot-delete`** sends exact confirmation text `"Yes, I am totally sure."`
- **`bot-token`** retrieves and stores tokens in `.env`
- **BF_NOTE** in ALL BotFather methods (success AND error paths)
- Rate limit detection with graceful error handling
- `/setuserpic` flow proven (S25 got Ubuntu's photo via BotFather)
- 13/13 bot tokens now in `.env`

## Protocol Categories & `do raw` Equivalent

Every non-`raw` `do` command can be expressed as a `do raw` call.
This section groups commands by the three Telegram protocols.

### MTProto (TL Functions via Telethon)

Protocol value: `"mtproto"` — calls `telethon.tl.functions.*`
Reference: https://docs.telethon.dev/en/stable/modules/client.html

| `do` command | `do raw` equivalent |
|-------------|---------------------|
| `bot-list` | `do raw '{"protocol":"mtproto","method":"bots.getAdminedBots"}'` |
| `bot-info` | `do raw '{"protocol":"mtproto","method":"bots.getBotInfo","params":{"bot":"@bot"}}'` |
| `chat-list` | `do raw '{"protocol":"mtproto","method":"messages.getDialogs","params":{"limit":30}}'` |
| `chat-read` | `do raw '{"protocol":"mtproto","method":"messages.getHistory","params":{"peer":93372553,"limit":5}}'` |
| `chat-send` | `do raw '{"protocol":"mtproto","method":"messages.sendMessage","params":{"peer":"@user","message":"Hi"}}'` |
| `chat-download` | `do raw '{"protocol":"mtproto","method":"messages.getMessages","params":{"id":[42]}}'` |
| `chat-delete` | `do raw '{"protocol":"mtproto","method":"messages.deleteHistory","params":{"peer":"@chat","revoke":true}}'` |
| `chat-delete-messages` | `do raw '{"protocol":"mtproto","method":"messages.deleteMessages","params":{"id":[42,43]}}'` |
| `folder-list` | `do raw '{"protocol":"mtproto","method":"messages.getDialogFilters"}'` |
| `folder-set` | `do raw '{"protocol":"mtproto","method":"messages.updateDialogFilter","params":{...}}'` |
| `folder-delete` | `do raw '{"protocol":"mtproto","method":"messages.updateDialogFilter","params":{"id":12}}'` |
| `chat-move` | *(combination of getDialogFilters + updateDialogFilter)* |
| `admin status` | `do raw '{"protocol":"mtproto","method":"users.getFullUser","params":{"id":"me"}}'` |
| `bot-photo` | `do raw '{"protocol":"mtproto","method":"photos.getUserPhotos","params":{"user_id":"@bot"}}'` |

### Bot HTTP API

Protocol value: `"botapi"` — calls `POST https://api.telegram.org/bot{token}/{method}`
Reference: https://core.telegram.org/bots/api

| `do` command | `do raw` equivalent |
|-------------|---------------------|
| `bot-send` | `do raw '{"protocol":"botapi","bot":"@my_bot","method":"sendMessage","params":{"chat_id":93372553,"text":"Hi"}}'` |
| `bot-send-file` | `do raw '{"protocol":"botapi","bot":"@my_bot","method":"sendDocument","params":{"chat_id":93372553,"document":"/path"}}'` |
| `updates` | `do raw '{"protocol":"botapi","bot":"@my_bot","method":"getUpdates","params":{}}'` |
| `webhook-get` | `do raw '{"protocol":"botapi","bot":"@my_bot","method":"getWebhookInfo","params":{}}'` |
| `webhook-set` | `do raw '{"protocol":"botapi","bot":"@my_bot","method":"setWebhook","params":{"url":"https://..."}}'` |
| `webhook-del` | `do raw '{"protocol":"botapi","bot":"@my_bot","method":"deleteWebhook","params":{}}'` |

### BotFather Conversation

Protocol value: `"bf"` — sends text to BotFather (BOTFATHER_ID = 93372553)
Reference: https://t.me/botfather

| `do` command | `do raw` equivalent |
|-------------|---------------------|
| `bot-create` | `do raw '{"protocol":"bf","method":"/newbot"}'` *(multi-step)* |
| `bot-delete` | `do raw '{"protocol":"bf","method":"/deletebot"}'` *(multi-step)* |
| `bot-token` | `do raw '{"protocol":"bf","method":"/token"}'` *(multi-step)* |
| `bot-photo` (via BF) | `do raw '{"protocol":"bf","method":"/setuserpic"}'` *(multi-step)* |

## Config

Single `.env` at `~/.config/tg-proxy/.env`:

```env
TG_API_ID=32750118
TG_API_HASH=df796e5e2c4f045ae51eba5de68335f7
```

Created by `tg-proxy admin setup` (HITL web form).

## Security

The configuration directory and its files contain sensitive API credentials and a Telethon session file. Protect them after `tg-proxy admin setup`:

```bash
chmod 700 ~/.config/tg-proxy
chmod 600 ~/.config/tg-proxy/.env ~/.config/tg-proxy/user.session
```

On first-time setup, verify the permissions:

```bash
ls -la ~/.config/tg-proxy/
# drwx------  2 user user   4096 ...
# -rw-------  1 user user    ... .env
# -rw-------  1 user user    ... user.session
```

## HITL

Human-in-the-Loop via local web UI on an OS-assigned free port (the port is printed with the review URL — it is never fixed, so two concurrent `do` invocations cannot collide on it). Sensitive operations open a browser page showing the payload for review, editing, and approval/rejection.

## Output format

Every response has a `meta` section:

```json
{
  "meta": {
    "status": "ok",
    "comment": "optional user comment",
    "edited": false
  },
  "data": { ... }
}
```

Use `--format table` or `-f table` for table output.

## Install

### uv tool

```bash
uv tool install .
```

### Development

```bash
uv tool install --editable .
```

### Docker (not yet tested)

⚠️ The Docker build and runtime have not been end-to-end tested yet. Use `uv tool install` for production.

```bash
make docker-build
docker run --rm kpihx/tg-proxy --help
```

## Development

```bash
make check        # ruff + py_compile + pyright + pytest
make uv-link      # editable install
make git-install-hooks  # pre-commit hook
```

See `Makefile` for full target list.
