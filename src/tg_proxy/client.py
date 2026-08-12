"""
TgClient — Centralized Telegram client for tg-proxy.

Combines Telethon (user + MTProto) and Bot API (httpx) into one interface.
All methods return dicts suitable for JSON serialization.
"""

import asyncio
import os
from pathlib import Path

import httpx
from telethon import TelegramClient, errors, functions
from telethon.tl.types import (
    DialogFilterDefault,
    PeerChannel,
    PeerChat,
    PeerUser,
    TextWithEntities,
)

from .config import (
    ENV_PATH,
    FILE_PERMISSIONS,
    SESSION_PATH,
    append_env,
    config_status,
    ensure_secure_storage,
    get_api_credentials,
    read_env,
    write_env,
)
from .exceptions import TgProxyError
from .hitl import require_approval
from .models import (
    BotCreatePayload,
    BotDeletePayload,
    BotInfoPayload,
    BotListPayload,
    BotPhotoPayload,
    BotSendFilePayload,
    BotSendPayload,
    BotTokenPayload,
    ChatDeleteMessagesPayload,
    ChatDeletePayload,
    ChatDownloadPayload,
    ChatListPayload,
    ChatMovePayload,
    ChatReadPayload,
    ChatSendFilePayload,
    ChatSendPayload,
    FolderDeletePayload,
    FolderListPayload,
    FolderSetPayload,
    RawPayload,
    UpdatesPayload,
    WebhookDelPayload,
    WebhookGetPayload,
    WebhookSetPayload,
)

TG_DATA_DIR = Path.home() / ".config" / "tg-proxy"

BOTFATHER_ID = 93372553
BF_NOTE = (
    "You MUST check BotFather chat even if there is no error:\n"
    '  tg-proxy do chat-read \'{"chat":BOTFATHER_ID,"limit":5}\'\n'
    "to ensure the process was successful ! if error discuss directly with bot father !"
)


def _peer_id(peer) -> int | None:
    """Extract a stable peer ID from Telethon Peer* types for folder matching."""
    if isinstance(peer, PeerUser):
        return peer.user_id
    if isinstance(peer, PeerChat):
        return peer.chat_id
    if isinstance(peer, PeerChannel):
        return peer.channel_id
    if hasattr(peer, "user_id"):
        return peer.user_id
    if hasattr(peer, "chat_id"):
        return peer.chat_id
    if hasattr(peer, "channel_id"):
        return peer.channel_id
    if hasattr(peer, "id"):
        return peer.id
    return None


def _title_str(val) -> str:
    """Convert a DialogFilter title to a plain string (handles TextWithEntities)."""
    if val is None:
        return ""
    if hasattr(val, "text"):
        return val.text
    return str(val)


def _read_token_from_env(key: str) -> str:
    """Read a single token key from the .env file synchronously."""
    env_path = TG_DATA_DIR / ".env"
    if not env_path.exists():
        return ""
    with open(env_path) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                if k == key:
                    return v
    return ""


class TgClient:
    """Centralized Telegram client — Telethon + MTProto + Bot API."""

    def __init__(self):
        api_id_str, api_hash = get_api_credentials()
        if not api_id_str or not api_hash:
            raise TgProxyError("TG_API_ID and TG_API_HASH not configured.")
        self.api_id = int(api_id_str)
        self.api_hash = api_hash
        self._client: TelegramClient | None = None

    async def _telethon(self) -> TelegramClient:
        """Lazy-init Telethon client (session stored in TG_DATA_DIR)."""
        if self._client is None:
            session_path = str(TG_DATA_DIR / "user.session")
            self._client = TelegramClient(  # type: ignore[reportGeneralTypeIssues]
                session_path, self.api_id, self.api_hash
            )
            await self._client.start()  # type: ignore[reportGeneralTypeIssues]
        return self._client

    async def close(self):
        if self._client:
            await self._client.disconnect()  # type: ignore[reportGeneralTypeIssues]
            self._client = None

    # ─── Bot API helpers (token from BotFather on demand) ───

    async def _bot_token(self, bot_username: str) -> str:
        """Get a bot token via BotFather Telethon interaction."""
        client = await self._telethon()
        await client.send_message(BOTFATHER_ID, "/token")
        await asyncio.sleep(2)
        await client.send_message(BOTFATHER_ID, f"@{bot_username}")
        await asyncio.sleep(2)
        async for msg in client.iter_messages(BOTFATHER_ID, limit=5):
            if msg.message and ("token" in msg.message.lower() or ":" in msg.message):
                lines = msg.message.strip().split("\n")
                for line in lines:
                    if ":" in line and len(line) > 40:
                        return line.strip().split()[-1].strip()
        raise TgProxyError(
            f"Could not retrieve token for @{bot_username} from BotFather."
        )

    async def _bot_api(
        self, bot_username: str, method: str, data: dict | None = None
    ) -> dict:
        """Call Bot API using token from .env (set via bot-token HITL earlier)."""
        key = bot_username.lstrip("@").upper() + "_TOKEN"
        token = os.environ.get(key, "")
        if not token:
            token = await asyncio.to_thread(_read_token_from_env, key)
            if token:
                os.environ[key] = token
        if not token:
            raise TgProxyError(
                f"Token for @{bot_username} not found in env or .env. "
                f'Run \'tg-proxy do bot-token {{"bots":["@{bot_username}"]}}\' first.'
            )
        url = f"https://api.telegram.org/bot{token}/{method}"
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(url, json=data) if data else await http.get(url)
            if resp.status_code != 200:
                raise TgProxyError(f"Bot API {method}: {resp.text}")
            return resp.json()

    # ─── admin setup (web form via hitl) ───

    @require_approval()
    async def admin_setup(self, payload: dict) -> dict:
        """
        Initialize the Telethon session and create the .env configuration file.

        Connects to Telegram with the provided API credentials and phone number,
        sends an OTP code, and writes the verified credentials to
        ~/.config/tg-proxy/.env. Requires Human-in-the-Loop approval.

        Parameters:
            - api_id (str): Telegram API ID from my.telegram.org.
            - api_hash (str): Telegram API hash.
            - phone (str): Phone number with country code (e.g., +336XXXXXXXX).

        Examples:
            - Run setup (interactive — credentials entered via web form):
                `tg-proxy admin setup`
                → {"meta":{"status":"approved","comment":"","edited":false},
                   "data":{"id":1234567890,"username":"YourUser","first_name":"YourName"}}
            - Setup with a different phone:
                `tg-proxy admin setup`
                (enter +336XXXXXXXX in the phone prompt)
            - Setup from environment variables (non-interactive):
                `export TG_API_ID=12345 && export TG_API_HASH=abc && tg-proxy admin setup`
        """
        api_id = int(payload["api_id"])
        api_hash = payload["api_hash"]
        phone = payload["phone"]
        ensure_secure_storage()
        client = TelegramClient(  # type: ignore[reportGeneralTypeIssues]
            str(SESSION_PATH), api_id, api_hash
        )
        await client.start(phone=phone)  # type: ignore[reportGeneralTypeIssues]
        me = await client.get_me()
        await client.disconnect()  # type: ignore[reportGeneralTypeIssues]
        SESSION_PATH.chmod(FILE_PERMISSIONS)
        await asyncio.to_thread(
            write_env,
            {
                **{
                    key: value
                    for key, value in read_env().items()
                    if key.endswith("_TOKEN")
                },
                "TG_API_ID": str(api_id),
                "TG_API_HASH": api_hash,
            },
        )
        return {
            "id": me.id,
            "username": me.username or "",
            "first_name": me.first_name or "",
            **config_status(),
        }

    # ─── admin status ───

    async def admin_status(self) -> dict:
        """
        Return the current authenticated user's identity.

        Fetches the Telegram user profile of the authenticated session,
        including ID, username, display name, phone, premium status, and
        whether the account is a bot.

        Parameters:
            - None

        Examples:
            - Show your identity:
                `tg-proxy admin status`
                → {"id":1234567890,"username":"YourUser","first_name":"YourName","phone":"+336XXXXXXXX","premium":false}
            - Pipe through jq for specific fields:
                `tg-proxy admin status | jq '.data.username'`
                → (filtered via jq)
            - Show identity with table format:
                `tg-proxy admin status -f table`
                → (table with key-value columns)
        """
        c = await self._telethon()
        me = await c.get_me()
        return {
            "id": me.id,
            "username": me.username or "",
            "first_name": me.first_name or "",
            "last_name": me.last_name or "",
            "phone": me.phone or "",
            "premium": getattr(me, "premium", False),
            "bot": getattr(me, "bot", False),
        }

    # ─── do bot-list ───

    async def bot_list(self, payload: BotListPayload | None = None) -> list[dict]:
        """
        List all bots owned by the user.

        Retrieves the full list of bots owned by the authenticated Telegram user
        using the MTProto getAdminedBots API. No bot tokens are required.

        Parameters:
            - None

        Examples:
            - List all bots:
                `tg-proxy do bot-list`
                → [{"id":8557838158,"username":"kpihx_s25_bot","first_name":"S25","bot_info_version":1,"photo":false}]
            - List with table format:
                `tg-proxy do bot-list -f table`
                → (table output)
            - List and save to file:
                `tg-proxy do bot-list -o /tmp/bots.json`
                → Written to /tmp/bots.json
        """
        c = await self._telethon()
        result = await c(functions.bots.GetAdminedBotsRequest())
        bots = []
        for bot in result:
            bots.append(
                {
                    "id": bot.id,
                    "username": bot.username or "",
                    "first_name": bot.first_name or "",
                    "photo": getattr(bot, "photo", None) is not None,
                    "bot_info_version": getattr(bot, "bot_info_version", 0),
                    "bot_can_edit": getattr(bot, "bot_can_edit", False),
                }
            )
        return sorted(bots, key=lambda b: b["username"])

    # ─── do bot-info ───

    async def bot_info(self, payload: BotInfoPayload) -> list[dict]:
        """
        Get detailed information for one or more bots.

        Returns full metadata (ID, username, photo status, bot_info_version,
        bot_can_edit) for each requested bot username. Supports multiple bots
        in a single call.

        Parameters:
            - bots (list[str]): List of bot @usernames or IDs to query.

        Examples:
            - Get info for one bot:
                `tg-proxy do bot-info '{"bots":["@kpihx_s25_bot"]}'`
                → [{"id":8557838158,"username":"kpihx_s25_bot","first_name":"S25","photo":false,"bot_info_version":1}]
            - Get info for multiple bots:
                `tg-proxy do bot-info '{"bots":["@s25","@ubuntu","@pve"]}'`
                → [{"id":8557838158,...},{"id":8948919586,...}]
            - Get info from a file payload:
                `tg-proxy do bot-info ./bots.json`
                → [{"id":8557838158,"username":"kpihx_s25_bot","first_name":"S25","photo":false,"bot_info_version":1}]
        """
        c = await self._telethon()
        result = await c(functions.bots.GetAdminedBotsRequest())
        bots_map = {b.username: b for b in result if b.username}
        out = []
        for username in payload.bots:
            clean = username.lstrip("@")
            bot = bots_map.get(clean)
            if bot:
                # Fetch profile photo info
                photo_info = {}
                try:
                    photos = await c.get_profile_photos(bot)
                    if photos:
                        p = photos[0]
                        photo_info = {
                            "has_photo": True,
                            "photo_id": p.id,
                            "dc_id": p.dc_id,
                            "has_video": getattr(p, "video", False),
                            "size": getattr(p.sizes[-1], "size", 0) if p.sizes else 0,
                        }
                except (TypeError, ValueError, OSError):
                    photo_info = {"has_photo": False}

                # Fetch bot info (about, description) via GetBotInfoRequest
                about = ""
                description = ""
                try:
                    binfo = await c(
                        functions.bots.GetBotInfoRequest(bot=bot, lang_code="")
                    )
                    about = getattr(binfo, "about", "") or ""
                    description = getattr(binfo, "description", "") or ""
                except (errors.RPCError, ValueError, TypeError, OSError) as exc:
                    about = f"<error: {exc}>"
                    description = f"<error: {exc}>"

                out.append(
                    {
                        "id": bot.id,
                        "username": clean,
                        "first_name": bot.first_name or "",
                        "photo": getattr(bot, "photo", None) is not None,
                        "photo_info": photo_info,
                        "bot_info_version": getattr(bot, "bot_info_version", 0),
                        "bot_can_edit": getattr(bot, "bot_can_edit", False),
                        "about": about,
                        "description": description,
                    }
                )
            else:
                out.append({"username": clean, "error": "Bot not found"})
        return out

    # ─── do bot-photo ───

    async def bot_photo(self, payload: BotPhotoPayload) -> list[dict]:
        """
        Download profile photo(s) from one or more bots/users.

        Uses Telethon's download_profile_photo to save the profile photo
        to a local directory. Returns the file path for each downloaded photo.

        Parameters:
            - bots (list[str]): @usernames or IDs.
            - out (str): Output directory (default: /tmp/tg-bot-photos).

        Examples:
            - Download a single bot photo:
                `tg-proxy do bot-photo '{"bots":["@k_ubuntu_bot"]}'`
                → [{"username":"k_ubuntu_bot","downloaded":true,"path":"/tmp/tg-bot-photos/k_ubuntu_bot.jpg"}]
            - Download multiple:
                `tg-proxy do bot-photo '{"bots":["@bot1","@bot2"],"out":"/tmp/photos"}'`
            - Bot has no photo:
                `tg-proxy do bot-photo '{"bots":["@kpihx_s25_bot"]}'`
                → [{"username":"kpihx_s25_bot","downloaded":false,"error":"No profile photo"}]
        """
        c = await self._telethon()
        out = Path(payload.out)
        out.mkdir(parents=True, exist_ok=True)
        results = []
        for username in payload.bots:
            clean = username.lstrip("@")
            try:
                entity = await c.get_input_entity(username)
                ext = Path("profile.jpg")
                file_path = out / f"{clean}{ext.suffix}"
                downloaded_path = await c.download_profile_photo(
                    entity, file=str(file_path)
                )
                if downloaded_path:
                    results.append(
                        {
                            "username": clean,
                            "downloaded": True,
                            "path": str(downloaded_path),
                            "size": Path(downloaded_path).stat().st_size,
                        }
                    )
                else:
                    results.append(
                        {
                            "username": clean,
                            "downloaded": False,
                            "error": "No profile photo",
                        }
                    )
            except (ValueError, TypeError, OSError) as e:
                results.append(
                    {
                        "username": clean,
                        "downloaded": False,
                        "error": str(e),
                    }
                )
        return results

    # ─── do bot-token (HITL, writes to .env) ───

    @require_approval()
    async def bot_token(self, payload: BotTokenPayload) -> dict:
        """
        Retrieve bot token(s) from BotFather.

        Interacts with BotFather to retrieve the API token for each requested bot.
        Tokens are appended to ~/.config/tg-proxy/.env in BOT_USERNAME_UPPER=token format.
        Requires Human-in-the-Loop approval.

        Parameters:
            - bots (list[str]): List of bot @usernames to get tokens for.

        Examples:
            - Get token for a single bot:
                `tg-proxy do bot-token '{"bots":["@kpihx_s25_bot"]}'`
                → {"appended_to":"~/.config/tg-proxy/.env","bots":[{"username":"kpihx_s25_bot","key":"KPIHX_S25_BOT"}]}
            - Get tokens for multiple bots:
                `tg-proxy do bot-token '{"bots":["@s25","@ubuntu","@pve"]}'`
                → {"appended_to":"~/.config/tg-proxy/.env","bots":[{"username":"kpihx_s25_bot","key":"S25"},{"username":"k_ubuntu_bot","key":"UBUNTU"}]}
            - Get token from a file payload:
                `tg-proxy do bot-token ./bots_to_tokenize.json`
                → {"appended_to":"~/.config/tg-proxy/.env","bots":[{"username":"bot1","key":"BOT1"}]}
            - Note: bot-token does NOT accept --output-file or --format.
        """
        c = await self._telethon()
        written = []
        for username in payload.bots:
            clean = username.lstrip("@")
            await client_send(c, BOTFATHER_ID, "/token")
            await asyncio.sleep(2)
            await client_send(c, BOTFATHER_ID, f"@{clean}")
            await asyncio.sleep(2)
            token = ""
            async for msg in c.iter_messages(BOTFATHER_ID, limit=5):
                if msg.message:
                    import re

                    m = re.search(r"\b(\d+:[a-zA-Z0-9_-]{35,})\b", msg.message)
                    if m:
                        token = m.group(1)
                        break
            if token:
                key = clean.upper().replace("@", "") + "_TOKEN"

                await asyncio.to_thread(append_env, key, token)
                written.append({"username": clean, "key": key})
        return {"appended_to": str(ENV_PATH), "bots": written, "note": BF_NOTE}

    # ─── do bot-create (HITL, max privacy) ───

    async def _bf_response(self, c) -> str:
        """Read the latest BotFather response message text."""
        msgs = await c.get_messages(BOTFATHER_ID, limit=1)
        if msgs:
            return msgs[0].text or ""
        return ""

    @require_approval()
    async def bot_create(self, payload: BotCreatePayload) -> list[dict]:
        """
        Create one or more bots via BotFather with full error handling.

        ⚠️ IMPORTANT: BotFather REQUIRES the username to end with "bot" (e.g. "my_test_bot").
        If the username does not end with "bot", BotFather will reject it with an error.
        Always append "bot" to the username or BotFather will fail the creation.

        Automates the BotFather /newbot flow for each bot. After successful
        creation, disables group joining (/setjoingroups Disable) and inline
        mode (/setinline Disable) for maximum privacy.
        Each step reads BotFather's response and handles errors properly.

        Parameters:
            - bots (list[BotCreateItem]): List of bots to create, each with name and username.
              Username MUST end with "bot" (e.g. "my_test_bot") — BotFather enforces this.

        Examples:
            - Create a single bot:
                `tg-proxy do bot-create '{"bots":[{"name":"MyBot","username":"my_test_bot"}]}'`
                → [{"username":"my_test_bot","name":"MyBot","status":"created"}]
            - Create with a taken username (error):
                `tg-proxy do bot-create '{"bots":[{"name":"MyBot","username":"taken_bot"}]}'`
                → {"username":"taken_bot","name":"MyBot","status":"error","error":"Username already taken"}
            - Create from a file payload:
                `tg-proxy do bot-create ./new_bots.json`
                → [{"username":"my_test_bot","name":"MyBot","status":"created"}]
        """
        c = await self._telethon()
        results = []

        async def _send_and_check(
            msg: str, ok_keywords: list[str], err_keywords: list[str] | None = None
        ) -> str | None:
            """Send message to BotFather, wait, read response, check for errors.
            Returns the response text if OK, raises TgProxyError if error keyword found."""
            await c.send_message(BOTFATHER_ID, msg)
            await asyncio.sleep(3)
            resp = await self._bf_response(c)
            if err_keywords:
                for kw in err_keywords:
                    if kw.lower() in resp.lower():
                        raise TgProxyError(kw)
            if ok_keywords:
                for kw in ok_keywords:
                    if kw.lower() in resp.lower():
                        return resp
            return resp

        for idx, item in enumerate(payload.bots):
            # Sleep between bot creations to avoid BotFather rate limit
            if idx > 0:
                await asyncio.sleep(5)
            name = item.name
            uname = item.username.lstrip("@")
            bot_status = {"username": uname, "name": name, "status": "error"}

            try:
                # Step 1: /newbot
                resp1 = await _send_and_check(
                    "/newbot",
                    ok_keywords=["choose a name", "alright"],
                    err_keywords=[],
                )
                # Check for BotFather rate limit
                if resp1 and "too many attempts" in resp1.lower():
                    bot_status["error"] = "BotFather rate limit reached"
                    bot_status["note"] = (
                        BF_NOTE
                        + "\nBotFather rate limit triggered. Wait and try again later."
                    )
                    results.append(bot_status)
                    break

                # Step 2: send bot name
                await _send_and_check(
                    name,
                    ok_keywords=["choose a username", "good"],
                    err_keywords=[],
                )

                # Step 3: send username
                resp3 = await _send_and_check(
                    uname,
                    ok_keywords=["congratulations", "done!", "use this token"],
                    err_keywords=["already taken", "sorry"],
                )

                # If "already taken" appeared in response, return specific error
                if resp3 and (
                    "already taken" in resp3.lower() or "sorry" in resp3.lower()
                ):
                    bot_status["error"] = "BotFather rejected the username"
                    bot_status["note"] = (
                        BF_NOTE
                        + '\nIf the error persists, reply to BotFather manually:\n  tg-proxy do chat-read \'{"chat":BOTFATHER_ID,"limit":10}\''
                    )
                    results.append(bot_status)
                    continue

                # Step 4: disable groups
                await _send_and_check(
                    "/setjoingroups",
                    ok_keywords=[],
                    err_keywords=[],
                )
                await _send_and_check(
                    f"@{uname}",
                    ok_keywords=[],
                    err_keywords=["invalid bot selected"],
                )
                await _send_and_check(
                    "Disable",
                    ok_keywords=[],
                    err_keywords=[],
                )

                # Step 5: disable inline
                await _send_and_check(
                    "/setinline",
                    ok_keywords=[],
                    err_keywords=[],
                )
                await _send_and_check(
                    f"@{uname}",
                    ok_keywords=[],
                    err_keywords=["invalid bot selected"],
                )
                await _send_and_check(
                    "Disable",
                    ok_keywords=[],
                    err_keywords=[],
                )

                bot_status["status"] = "created"
                bot_status["note"] = BF_NOTE
                results.append(bot_status)

            except TgProxyError as e:
                bot_status["error"] = str(e)
                bot_status["note"] = (
                    BF_NOTE
                    + '\nIf the error persists, use chat mode:\n  tg-proxy do chat-send \'{"to":BOTFATHER_ID,"message":"/command"}\'\n  tg-proxy do chat-read \'{"chat":BOTFATHER_ID,"limit":10}\''
                )
                results.append(bot_status)
        return results

    # ─── do bot-delete (HITL) ───

    @require_approval()
    async def bot_delete(self, payload: BotDeletePayload) -> dict:
        """
        Delete one or more bots via BotFather.

        Automates the BotFather /deletebot flow for each requested bot.
        Requires Human-in-the-Loop approval. The action is irreversible.

        Parameters:
            - bots (list[str]): List of bot @usernames or IDs to delete.

        Examples:
            - Delete a single bot:
                `tg-proxy do bot-delete '{"bots":["@kpihx_old_bot"]}'`
                → {"deleted":["kpihx_old_bot"]}
            - Delete multiple bots:
                `tg-proxy do bot-delete '{"bots":["@dead_bot1","@dead_bot2"]}'`
                → {"deleted":["dead_bot1","dead_bot2"]}
            - Delete from a file payload:
                `tg-proxy do bot-delete ./bots_to_delete.json`
                → {"deleted":["kpihx_old_bot"]}
        """
        c = await self._telethon()
        deleted = []
        for username in payload.bots:
            clean = username.lstrip("@")
            try:
                await client_send(c, BOTFATHER_ID, "/deletebot")
                await asyncio.sleep(2)
                await client_send(c, BOTFATHER_ID, f"@{clean}")
                await asyncio.sleep(3)
                await client_send(c, BOTFATHER_ID, "Yes, I am totally sure.")
                await asyncio.sleep(2)
                deleted.append(clean)
            except (TypeError, ValueError, OSError, AttributeError):
                deleted.append(
                    {"username": clean, "error": "BotFather did not confirm deletion"}
                )
        return {"deleted": deleted, "note": BF_NOTE}

    # ─── do bot-send (HITL) ───

    @require_approval()
    async def bot_send(self, payload: BotSendPayload) -> dict:
        """
        Send a message as a bot.

        Sends a text message to your own Telegram account (the configured user).
        The message is sent using the Bot API with the bot's token retrieved from
        BotFather. Requires Human-in-the-Loop approval with the option to edit
        the message before sending.

        Parameters:
            - bot (str): Bot @username or ID to send the message AS.
            - message (str): Message text (supports HTML/Markdown).
            - parse_mode (str | None): "HTML", "Markdown", or null for plain.

        Examples:
            - Send a simple message:
                `tg-proxy do bot-send '{"bot":"@kpihx_s25_bot","message":"Hello!"}'`
                → {"message_id":42}
            - Send HTML-formatted message:
                `tg-proxy do bot-send '{"bot":"@ubuntu","message":"<b>Report</b> ready"}'`
                → {"message_id":43}
            - Send from a file payload:
                `tg-proxy do bot-send ./message.json`
                → {"message_id":44}
        """
        result = await self._bot_api(
            payload.bot.lstrip("@"),
            "sendMessage",
            {
                "chat_id": (await self._get_self_id()),
                "text": payload.message,
                "parse_mode": payload.parse_mode or "HTML",
            },
        )
        return {"message_id": result.get("result", {}).get("message_id")}

    # ─── do bot-send-file (HITL) ───

    @require_approval()
    async def bot_send_file(self, payload: BotSendFilePayload) -> dict:
        """
        Send a message with one or more files as a bot.

        Sends a caption plus one or multiple files to your own Telegram account
        using the Bot API. The bot token is retrieved from BotFather.
        Requires Human-in-the-Loop approval.

        Parameters:
            - bot (str): Bot @username or ID to send the message AS.
            - message (str): Message caption.
            - files (list[str]): List of file paths to attach.

        Examples:
            - Send a single file with caption:
                `tg-proxy do bot-send-file '{"bot":"@s25","message":"Here","files":["/tmp/doc.pdf"]}'`
                → {"message_id":42,"files_sent":1}
            - Send multiple files:
                `tg-proxy do bot-send-file '{"bot":"@ubuntu","message":"Reports","files":["/tmp/a.pdf","/tmp/b.pdf"]}'`
                → {"message_id":43,"files_sent":2}
            - Send from a file payload:
                `tg-proxy do bot-send-file ./payload.json`
                → {"message_id":42,"files_sent":1}
        """
        # Get token from .env (not BotFather)
        key = payload.bot.lstrip("@").upper() + "_TOKEN"
        token = os.environ.get(key, "") or _read_token_from_env(key)
        chat_id = await self._get_self_id()
        media = []
        for fpath in payload.files:
            file_data = await asyncio.to_thread(Path(fpath).read_bytes)
            media.append(("document", (Path(fpath).name, file_data)))
        if not media:
            raise TgProxyError("At least one file is required.")
        async with httpx.AsyncClient(timeout=60) as http:
            files_data: list = media
            send_data: dict = {"chat_id": chat_id, "caption": payload.message}
            if len(media) == 1:
                resp = await http.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data=send_data,
                    files=files_data,
                )
            else:
                resp = await http.post(
                    f"https://api.telegram.org/bot{token}/sendMediaGroup",
                    data=send_data,
                    files=media if len(media) == 1 else None,
                )
            if resp.status_code != 200:
                raise TgProxyError(f"sendMediaGroup: {resp.text}")
            return {
                "message_id": resp.json().get("result", {}).get("message_id"),
                "files_sent": len(media),
            }

    # ─── do chat-list ───

    async def chat_list(self, payload: ChatListPayload) -> list[dict]:
        """
        List your conversations.

        Retrieves all dialogs (chats) for the authenticated user, including
        private chats, groups, and channels. Can be filtered by type.

        Parameters:
            - type (str | None): Filter by type: "user", "group", or "channel".
            - limit (int): Maximum number of conversations to return (default: 30).

        Examples:
            - List all conversations:
                `tg-proxy do chat-list`
                → [{"id":BOTFATHER_ID,"name":"BotFather","type":"user","unread":9},...]
            - List only private chats:
                `tg-proxy do chat-list '{"type":"user"}'`
                → [{"id":...}] (filtered user chats only)
            - List with table format:
                `tg-proxy do chat-list -f table`
                → (table format)
        """
        c = await self._telethon()
        dialogs = await c.get_dialogs(limit=payload.limit)
        results = []

        # Build folder/filter index: peer_id → [folder_name, ...]
        peer_to_folders: dict[int, list[str]] = {}
        try:
            filters_resp = await c(functions.messages.GetDialogFiltersRequest())
            filters = filters_resp.filters if filters_resp else []
            for flt in filters:
                folder_name = _title_str(getattr(flt, "title", None))
                if not folder_name:
                    continue
                for rule in getattr(flt, "include_peers", []) or []:
                    peer_id = _peer_id(rule)
                    if peer_id:
                        peer_to_folders.setdefault(peer_id, []).append(folder_name)
        except (TypeError, ValueError, OSError, AttributeError):
            pass

        for d in dialogs:
            entity = d.entity
            if payload.type and payload.type != str(type(entity).__name__).lower():
                continue
            peer_id = getattr(entity, "id", 0)
            folders = peer_to_folders.get(peer_id, [])
            results.append(
                {
                    "id": entity.id,
                    "name": d.name or "",
                    "username": getattr(entity, "username", "") or "",
                    "type": type(entity).__name__,
                    "unread": d.unread_count,
                    "last_message": d.message.text[:80]
                    if d.message and d.message.text
                    else "",
                    "date": str(d.date),
                    "folders": folders,
                }
            )
        return results

    # ─── do chat-read ───

    async def chat_read(self, payload: ChatReadPayload) -> list[dict]:
        """
        Read messages from a chat.

        Retrieves recent messages from a specific chat, group, or channel.
        Supports optional search filtering and custom limits.
        File attachments are listed with metadata (name, size, mime type).

        Parameters:
            - chat (str | int): Chat ID, @username, or phone number.
            - limit (int): Maximum messages to retrieve (default: 20).
            - search (str | None): Optional text to search for.

        Examples:
            - Read recent messages from BotFather:
                `tg-proxy do chat-read '{"chat":BOTFATHER_ID,"limit":5}'`
                → [{"id":9095,"date":"2026-07-23","from_id":BOTFATHER_ID,"text":"Use this token:..."}]
            - Search for token messages:
                `tg-proxy do chat-read '{"chat":BOTFATHER_ID,"search":"token"}'`
                → [{"id":9095,"text":"Here is your token:"}] (searched)
            - Read from a file payload:
                `tg-proxy do chat-read ./read_query.json`
                → [{"id":9095,"date":"2026-07-23","from_id":BOTFATHER_ID,"text":"Use this token:..."}]
        """
        c = await self._telethon()
        entity = await c.get_input_entity(payload.chat)
        messages = await c.get_messages(
            entity, limit=payload.limit, search=payload.search
        )
        results = []
        for msg in messages:  # type: ignore[reportOptionalIterable, reportGeneralTypeIssues]
            entry = {
                "id": msg.id,
                "date": str(msg.date),
                "from_id": msg.sender_id,
                "text": msg.text or "",
            }
            if msg.file:
                entry["file"] = {
                    "name": msg.file.name or "",
                    "size": msg.file.size or 0,
                    "mime": msg.file.mime_type or "",
                }
            results.append(entry)
        return results

    # ─── do chat-send ───

    @require_approval()
    async def chat_send(self, payload: ChatSendPayload) -> dict:
        """
        Send a message as yourself to anyone.

        Sends a text message to any Telegram entity (user, bot, group, channel)
        using the authenticated user's account via Telethon.

        Parameters:
            - to (str | int): Recipient: chat ID, @username, or phone number.
            - message (str): Message text to send.

        Examples:
            - Send to a user:
                `tg-proxy do chat-send '{"to":"@YourUser","message":"Hello"}'`
                → {"message_id":50,"chat":"@YourUser"}
            - Send to BotFather:
                `tg-proxy do chat-send '{"to":"@BotFather","message":"/start"}'`
                → {"message_id":51,"chat":"@BotFather"}
            - Send to a chat by ID:
                `tg-proxy do chat-send '{"to":BOTFATHER_ID,"message":"Hi"}'`
                → {"message_id":52,"chat":"BOTFATHER_ID"}
        """
        c = await self._telethon()
        entity = await c.get_input_entity(payload.to)
        msg = await c.send_message(entity, payload.message)
        return {"message_id": msg.id, "chat": str(payload.to)}

    # ─── do chat-send-file ───

    @require_approval()
    async def chat_send_file(self, payload: ChatSendFilePayload) -> dict:
        """
        Send a message with files as yourself to anyone.

        Sends a caption plus one or more files to any Telegram entity using
        the authenticated user's account. The files must exist on the local
        filesystem.

        Parameters:
            - to (str | int): Recipient: chat ID, @username, or phone number.
            - message (str): Message caption.
            - files (list[str]): List of local file paths to send.

        Examples:
            - Send a file with caption:
                `tg-proxy do chat-send-file '{"to":"@YourUser","message":"Photo","files":["/tmp/img.jpg"]}'`
                → {"message_id":52,"files_sent":1}
            - Send multiple files:
                `tg-proxy do chat-send-file '{"to":"@YourUser","message":"Docs","files":["/tmp/a.pdf","/tmp/b.pdf"]}'`
                → {"message_id":53,"files_sent":2}
            - Send from a file payload:
                `tg-proxy do chat-send-file ./payload.json`
                → {"message_id":52,"files_sent":1}
        """
        c = await self._telethon()
        entity = await c.get_input_entity(payload.to)
        file_objs = []
        for fpath in payload.files:
            p = Path(fpath)
            if p.exists():
                file_objs.append(str(p.resolve()))
        if not file_objs:
            raise TgProxyError("No valid files provided.")
        msg = await c.send_file(entity, file_objs, caption=payload.message or "")
        return {
            "message_id": msg[0].id if isinstance(msg, list) else msg.id,
            "files_sent": len(file_objs),
        }

    # ─── do chat-download ───

    async def chat_download(self, payload: ChatDownloadPayload) -> dict:
        """
        Download media files from a chat by message ID(s).

        Downloads one or more media files (photos, documents, videos) from
        specific messages in a chat to a local output directory.
        Each message must contain a file attachment.

        Parameters:
            - chat (str | int): Chat ID, @username, or phone number.
            - message_ids (list[int]): Message IDs to download media from.
            - out (str): Local output directory path (default: /tmp/tg-proxy-downloads).

        Examples:
            - Download a single media:
                `tg-proxy do chat-download '{"chat":"@chat","message_ids":[42],"out":"/tmp/dl"}'`
                → {"downloaded":[{"message_id":42,"name":"photo.jpg","path":"/tmp/dl/photo.jpg","size":12345}]}
            - Download multiple files:
                `tg-proxy do chat-download '{"chat":BOTFATHER_ID,"message_ids":[42,43],"out":"/tmp"}'`
                → {"downloaded":[{"message_id":42,"name":"a.jpg","path":"/tmp/a.jpg","size":100},{"message_id":43,"name":"b.jpg"}]}
            - Download from a file payload:
                `tg-proxy do chat-download ./download.json`
                → {"downloaded":[{"message_id":42,"name":"photo.jpg","path":"/tmp/tg-proxy-downloads/photo.jpg","size":12345}]}
        """
        c = await self._telethon()
        entity = await c.get_input_entity(payload.chat)
        out_dir = Path(payload.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        downloaded = []
        for mid in payload.message_ids:
            msg = await c.get_messages(entity, ids=mid)
            if msg and msg.file:
                path = await msg.download_media(file=str(out_dir))
                downloaded.append(
                    {
                        "message_id": mid,
                        "name": msg.file.name or Path(path).name,
                        "path": str(path),
                        "size": msg.file.size or 0,
                    }
                )
        return {"downloaded": downloaded}

    # ─── do chat-delete ───

    async def chat_delete(self, payload: ChatDeletePayload) -> dict:
        """
        Delete an entire chat conversation.

        Permanently deletes the dialog and all its messages.
        Uses Telethon's delete_dialog which deletes the conversation
        from your account. For private chats, revoke=True also deletes
        for the other side.

        Parameters:
            - chat (str | int): Chat ID, @username, or phone number.
            - revoke (bool): Delete for both sides (default: True).

        Examples:
            - Delete a chat:
                `tg-proxy do chat-delete '{"chat":"@spam_chat"}'`
                → {"deleted": "@spam_chat", "revoke": true}
            - Delete without revoke:
                `tg-proxy do chat-delete '{"chat":123456789,"revoke":false}'`
                → {"deleted": 123456789, "revoke": false}
        """
        c = await self._telethon()
        entity = await c.get_input_entity(payload.chat)
        await c.delete_dialog(entity, revoke=payload.revoke)
        return {"deleted": payload.chat, "revoke": payload.revoke}

    # ─── do chat-delete-messages ───

    async def chat_delete_messages(self, payload: ChatDeleteMessagesPayload) -> dict:
        """
        Delete specific messages from a chat.

        Removes one or more messages by their IDs from a chat.
        Uses Telethon's delete_messages for precise message deletion.

        Parameters:
            - chat (str | int): Chat ID, @username, or phone number.
            - message_ids (list[int]): Message IDs to delete.
            - revoke (bool): Delete for both sides (default: True).

        Examples:
            - Delete a single message:
                `tg-proxy do chat-delete-messages '{"chat":"@chat","message_ids":[42]}'`
                → {"chat":"@chat","deleted_count":1,"revoke":true}
            - Delete multiple messages:
                `tg-proxy do chat-delete-messages '{"chat":BOTFATHER_ID,"message_ids":[42,43,44]}'`
                → {"chat":BOTFATHER_ID,"deleted_count":3,"revoke":true}
        """
        c = await self._telethon()
        entity = await c.get_input_entity(payload.chat)
        result = await c.delete_messages(
            entity, payload.message_ids, revoke=payload.revoke
        )
        count = getattr(result, "messages", None)
        if count is None and isinstance(result, (list, tuple)):
            count = len(result)
        elif isinstance(result, int):
            count = result
        else:
            count = len(payload.message_ids)
        return {
            "chat": payload.chat,
            "deleted_count": count,
            "message_ids": payload.message_ids,
            "revoke": payload.revoke,
        }

    # ─── do folder-list ───

    async def folder_list(self, payload: FolderListPayload) -> list[dict]:
        """
        List all Telegram chat folders (filters) with their chats.

        Retrieves all dialog filters configured for this account,
        resolves each included peer to a display name/username.

        Parameters:
            - payload (FolderListPayload): No parameters needed. Call with no arguments.

        Examples:
            - List all folders:
                `tg-proxy do folder-list`
                → [{"id":1,"title":"KpihX-Labs","chats":["Ivann","BotFather"],"icon":"💻"}]
        """
        c = await self._telethon()
        filters_resp = await c(functions.messages.GetDialogFiltersRequest())
        filters = filters_resp.filters if filters_resp else []
        # Build a peer_id → display name map from all dialogs
        dialogs = await c.get_dialogs(limit=200)
        peer_name_map: dict[int, str] = {}
        for d in dialogs:
            eid = getattr(d.entity, "id", 0)
            display = d.name or getattr(d.entity, "username", "") or str(eid)
            peer_name_map[eid] = display

        result = []
        for flt in filters:
            if isinstance(flt, DialogFilterDefault):
                continue
            title_raw = getattr(flt, "title", None)
            title = (
                title_raw.text
                if title_raw is not None and hasattr(title_raw, "text")
                else str(title_raw)
                if title_raw is not None
                else ""
            )
            icon = getattr(flt, "emoticon", None) or ""
            chats = []
            for peer in getattr(flt, "include_peers", []) or []:
                pid = _peer_id(peer)
                if pid:
                    chats.append(peer_name_map.get(pid, str(pid)))
            result.append(
                {
                    "id": flt.id,
                    "title": title,
                    "chats": chats,
                    "icon": icon,
                }
            )
        return result

    # ─── do folder-set (UPSERT) ───

    async def folder_set(self, payload: FolderSetPayload) -> dict:
        """
        Create or update a Telegram chat folder (UPSERT).

        If a folder with the given title exists → updates it.
        If not → creates a new folder with the next available ID.
        Resolves @usernames and IDs to Telegram peers automatically.

        Parameters:
            - title (str): Folder title.
            - chats (list[str]): @usernames or IDs to include.
            - icon (str | None): Folder icon emoji (e.g. 💻).

        Examples:
            - Create a folder:
                `tg-proxy do folder-set '{"title":"Work","chats":["@bot1"],"icon":"💼"}'`
                → {"title":"Work","chat_count":1,"action":"created"}
            - Update existing:
                `tg-proxy do folder-set '{"title":"Work","chats":["@bot1","@bot2"]}'`
                → {"title":"Work","chat_count":2,"action":"updated"}
        """
        from telethon.tl.types import DialogFilter, DialogFilterDefault

        c = await self._telethon()
        all_filters_resp = await c(functions.messages.GetDialogFiltersRequest())
        all_filters = all_filters_resp.filters if all_filters_resp else []

        # Find existing folder or prepare new ID
        existing = None
        max_id = 0
        for flt in all_filters:
            if isinstance(flt, DialogFilterDefault):
                continue
            max_id = max(max_id, flt.id)
            flt_title = _title_str(getattr(flt, "title", None))
            if flt_title == payload.title:
                existing = flt

        folder_id = existing.id if existing else (max_id + 1)

        # Resolve chat identifiers to InputPeer objects
        include_peers = []
        for chat_str in payload.chats:
            try:
                entity = await c.get_input_entity(chat_str)
                include_peers.append(entity)
            except (ValueError, TypeError):
                pass

        emoticon = payload.icon or ""

        new_filter = DialogFilter(
            id=folder_id,
            title=TextWithEntities(text=payload.title, entities=[]),
            emoticon=emoticon,
            pinned_peers=[],
            include_peers=include_peers,
            exclude_peers=[],
        )

        await c(
            functions.messages.UpdateDialogFilterRequest(
                id=folder_id,
                filter=new_filter,
            )
        )

        action = "updated" if existing else "created"
        return {
            "title": payload.title,
            "chat_count": len(include_peers),
            "id": folder_id,
            "action": action,
        }

    # ─── do folder-delete (HITL) ───

    @require_approval()
    async def folder_delete(self, payload: FolderDeletePayload) -> dict:
        """
        Delete a Telegram chat folder by title.

        Requires Human-in-the-Loop approval. The action is irreversible.

        Parameters:
            - title (str): Folder title to delete.

        Examples:
            - Delete a folder:
                `tg-proxy do folder-delete '{"title":"Work"}'`
                → {"title":"Work","action":"deleted"}
        """
        c = await self._telethon()
        all_filters_resp = await c(functions.messages.GetDialogFiltersRequest())
        all_filters = all_filters_resp.filters if all_filters_resp else []

        target = None
        for flt in all_filters:
            if isinstance(flt, DialogFilterDefault):
                continue
            if _title_str(getattr(flt, "title", None)) == payload.title:
                target = flt
                break

        if not target:
            return {"title": payload.title, "action": "not_found"}

        await c(
            functions.messages.UpdateDialogFilterRequest(
                id=target.id,
                filter=None,
            )
        )
        return {"title": payload.title, "action": "deleted"}

    # ─── do chat-move ───

    async def chat_move(self, payload: ChatMovePayload) -> dict:
        """
        Move a chat from its current folder(s) to a target folder.

        Removes the chat from all source folders and adds it to the
        target folder. Creates the target folder if it doesn't exist.

        Parameters:
            - chat (str): Chat @username or ID.
            - to (str): Target folder title.

        Examples:
            - Move a chat:
                `tg-proxy do chat-move '{"chat":"@bot1","to":"Work"}'`
                → {"chat":"@bot1","folders":["Work"],"removed_from":["OldFolder"]}
        """
        from telethon.tl.types import DialogFilter, DialogFilterDefault

        c = await self._telethon()
        entity = await c.get_input_entity(payload.chat)
        peer_id = (
            getattr(entity, "user_id", None)
            or getattr(entity, "chat_id", None)
            or getattr(entity, "channel_id", 0)
        )

        all_filters_resp = await c(functions.messages.GetDialogFiltersRequest())
        all_filters = all_filters_resp.filters if all_filters_resp else []
        removed_from = []
        target_filter = None

        for flt in all_filters:
            if isinstance(flt, DialogFilterDefault):
                continue
            title_raw = getattr(flt, "title", None)
            title = _title_str(title_raw)
            inc = list(getattr(flt, "include_peers", []) or [])

            # Check if this filter contains our chat
            before = len(inc)
            inc = [p for p in inc if _peer_id(p) != peer_id]
            after = len(inc)
            if before != after:
                removed_from.append(title)
                # Update filter without this chat
                updated = DialogFilter(
                    id=flt.id,
                    title=TextWithEntities(text=getattr(flt, "title", ""), entities=[]),
                    emoticon=getattr(flt, "emoticon", None) or "",
                    pinned_peers=list(getattr(flt, "pinned_peers", []) or []),
                    include_peers=inc,
                    exclude_peers=list(getattr(flt, "exclude_peers", []) or []),
                )
                await c(
                    functions.messages.UpdateDialogFilterRequest(
                        id=flt.id,
                        filter=updated,
                    )
                )

            if title == payload.to:
                target_filter = flt

        # Add to target folder
        if target_filter:
            inc = list(getattr(target_filter, "include_peers", []) or [])
            if entity not in inc:
                inc.append(entity)
            updated = DialogFilter(
                id=target_filter.id,
                title=TextWithEntities(
                    text=getattr(target_filter, "title", ""), entities=[]
                ),
                emoticon=getattr(target_filter, "emoticon", None) or "",
                pinned_peers=list(getattr(target_filter, "pinned_peers", []) or []),
                include_peers=inc,
                exclude_peers=list(getattr(target_filter, "exclude_peers", []) or []),
            )
            await c(
                functions.messages.UpdateDialogFilterRequest(
                    id=target_filter.id,
                    filter=updated,
                )
            )
        else:
            # Create target folder
            new_id = max((f.id for f in all_filters), default=0) + 1
            new_filter = DialogFilter(
                id=new_id,
                title=TextWithEntities(text=payload.to, entities=[]),
                emoticon="",
                pinned_peers=[],
                include_peers=[entity],
                exclude_peers=[],
            )
            await c(
                functions.messages.UpdateDialogFilterRequest(
                    id=new_id,
                    filter=new_filter,
                )
            )

        return {
            "chat": payload.chat,
            "folders": [payload.to],
            "removed_from": removed_from,
        }

    # ─── do updates ───

    async def updates(self, payload: UpdatesPayload) -> list[dict]:
        """
        Read a bot's received messages (inbox).

        Retrieves the most recent messages sent TO a bot by other users.
        Uses the Bot API getUpdates method with the bot's token (fetched
        from BotFather on demand).

        Parameters:
            - bot (str): Bot @username or ID to read messages for.
            - limit (int): Maximum updates to retrieve (default: 10).

        Examples:
            - Read recent inbox messages:
                `tg-proxy do updates '{"bot":"@kpihx_general_capture_bot","limit":5}'`
                → [{"update_id":100,"message_id":5,"date":1728000000,"from":"User","text":"Hello"}]
            - Read with table output:
                `tg-proxy do updates '{"bot":"@ubuntu","limit":10}' -f table`
                → [{"update_id":100,"message_id":5,"date":1728000000,"from":"User","text":"Hello"}]
            - Read from a file payload:
                `tg-proxy do updates ./updates_query.json`
                → [{"update_id":100,"message_id":5,"date":1728000000,"from":"User","text":"Hello"}]
        """
        result = await self._bot_api(
            payload.bot.lstrip("@"),
            "getUpdates",
            {
                "limit": payload.limit,
                "allowed_updates": ["message"],
            },
        )
        updates = result.get("result", [])
        out = []
        for u in updates:
            msg = u.get("message", {})
            out.append(
                {
                    "update_id": u["update_id"],
                    "message_id": msg.get("message_id"),
                    "date": msg.get("date"),
                    "from": msg.get("from", {}).get("username", ""),
                    "text": msg.get("text", ""),
                }
            )
        return out

    # ─── do webhook-get ───

    async def webhook_get(self, payload: WebhookGetPayload) -> dict:
        """
        Get webhook configuration for a bot.

        Retrieves the current webhook URL, pending update count, maximum
        connections, and any last error message for the specified bot.
        Uses the Bot API getWebhookInfo method.

        Parameters:
            - bot (str): Bot @username or ID.

        Examples:
            - Get webhook info:
                `tg-proxy do webhook-get '{"bot":"@kpihx_general_capture_bot"}'`
                → {"url":"https://example.com/webhook","pending":0,"max_connections":40,"last_error":""}
            - Save webhook info to file:
                `tg-proxy do webhook-get '{"bot":"@ubuntu"}' -o /tmp/webhook.json`
                → {"url":"https://example.com/webhook","pending":0,"max_connections":40,"last_error":""}
            - Get with table output:
                `tg-proxy do webhook-get '{"bot":"@pve"}' -f table`
                → {"url":"https://example.com/webhook","pending":0,"max_connections":40,"last_error":""}
        """
        result = await self._bot_api(payload.bot.lstrip("@"), "getWebhookInfo")
        info = result.get("result", {})
        return {
            "url": info.get("url", ""),
            "pending": info.get("pending_update_count", 0),
            "max_connections": info.get("max_connections", 40),
            "last_error": info.get("last_error_message", ""),
        }

    # ─── do webhook-set ───

    async def webhook_set(self, payload: WebhookSetPayload) -> dict:
        """
        Set webhook URL for a bot.

        Configures the bot to send all updates to the specified HTTPS endpoint.
        ⚠️ Important: you MUST filter by from.id in your webhook handler to
        prevent unauthorized access. Any user who sends a command to the bot
        will trigger the webhook otherwise.

        Parameters:
            - bot (str): Bot @username or ID to configure.
            - url (str): Webhook URL (must be HTTPS).

        Examples:
            - Set webhook for an MCP bot:
                `tg-proxy do webhook-set '{"bot":"@kpihx_general_capture_bot","url":"https://n8n.kpihx-labs.com/webhook"}'`
                → {"url":"https://n8n.kpihx-labs.com/webhook","configured":true}
            - Set webhook from a file payload:
                `tg-proxy do webhook-set ./webhook_config.json`
                → {"url":"https://n8n.kpihx-labs.com/webhook","configured":true}
            - Save result to file:
                `tg-proxy do webhook-set '{"bot":"@bot","url":"..."}' -o /tmp/result.json`
                → {"url":"...","configured":true}
        """
        result = await self._bot_api(
            payload.bot.lstrip("@"),
            "setWebhook",
            {
                "url": payload.url,
            },
        )
        return {"url": payload.url, "configured": result.get("ok", False)}

    # ─── do webhook-del ───

    async def webhook_del(self, payload: WebhookDelPayload) -> dict:
        """
        Delete webhook for a bot.

        Removes the webhook configuration and switches the bot back to polling
        mode. Optionally drops pending updates that accumulated while the
        webhook was active.

        Parameters:
            - bot (str): Bot @username or ID.
            - drop_pending (bool): Whether to drop pending updates (default: false).

        Examples:
            - Simple webhook deletion:
                `tg-proxy do webhook-del '{"bot":"@kpihx_general_capture_bot"}'`
                → {"detail":"Webhook deleted","ok":true}
            - Delete and drop pending updates:
                `tg-proxy do webhook-del '{"bot":"@old_bot","drop_pending":true}'`
                → {"detail":"Webhook deleted","ok":true}
            - Delete from a file payload:
                `tg-proxy do webhook-del ./webhook_delete.json`
                → {"detail":"Webhook deleted","ok":true}
        """
        if payload.drop_pending:
            result = await self._bot_api(
                payload.bot.lstrip("@"),
                "deleteWebhook",
                {
                    "drop_pending_updates": True,
                },
            )
        else:
            result = await self._bot_api(
                payload.bot.lstrip("@"),
                "setWebhook",
                {
                    "url": "",
                },
            )
        return {"detail": "Webhook deleted", "ok": result.get("ok", False)}

    # ─── do raw (generic Telegram gateway) ───

    @require_approval()
    async def raw(self, payload: RawPayload) -> dict:
        """
        Execute ANY Telegram operation via raw method call.

        Generic gateway covering all three Telegram interaction protocols.
        Requires Human-in-the-Loop approval. The raw response from Telegram
        is returned as-is — no tg-proxy wrapping.

        ── Protocols ────────────────────────────────────────────────────────────

        1. MTProto (telethon.tl.functions.*) — "mtproto"
           Uses Telethon's full MTProto API. Can call ANY function from
           Telegram's Type Language schema, exposing the full MTProto API.
           Methods follow the pattern: `service.methodName`.
           The method name is dynamically resolved to the corresponding
           Telethon Request class at runtime.
           Reference: https://docs.telethon.dev/en/stable/modules/client.html
                     https://core.telegram.org/schema
           Examples of what you can do:
           • messages.sendMessage — send a message (like our `chat-send`)
           • messages.getMessages — read messages (like our `chat-read`)
           • channels.joinChannel — join a channel
           • users.getFullUser — get user details (like our `bot-info`)
           • bots.getBotInfo — get bot info
           • account.updateProfile — update your own profile
           • messages.getDialogs — list conversations (like our `chat-list`)
           Needs: method name in dotted notation, params matching the TL schema.

        2. Bot HTTP API — "botapi"
           Calls the official Telegram Bot API via HTTP POST.
           All methods from https://core.telegram.org/bots/api are available.
           Requires a bot token (stored in .env) — the `bot` field specifies
           which bot to use (e.g. "@my_bot").
           Examples of what you can do:
           • sendMessage — send a message as a bot (like our `bot-send`)
           • sendPhoto — send a photo as a bot (like our `bot-send-file`)
           • getUpdates — read bot's inbox (like our `updates`)
           • setMyDescription — set bot description (like BotFather /setdescription)
           • setMyName — change bot name (like BotFather /setname)
           • getWebhookInfo — get webhook config (like our `webhook-get`)
           • setWebhook — set webhook URL (like our `webhook-set`)
           • deleteWebhook — delete webhook (like our `webhook-del`)
           Needs: method name as in Bot API docs, JSON params, bot @username.

        3. BotFather Conversation — "bf"
           Sends any text command directly to BotFather and returns his response.
           This is the same conversation protocol used by `bot-create`, `bot-delete`,
           `bot-token`, and `/setuserpic`.
           References: https://t.me/botfather
           Examples of what you can do:
           • /mybots — list all your bots (like our `bot-list`)
           • /setname — change a bot's name
           • /setdescription — change a bot's description
           • /setabouttext — change a bot's about section
           • /setuserpic — change a bot's profile photo
           • /setcommands — change a bot's command list
           • /setjoingroups — toggle group joining
           • /setinline — toggle inline mode
           Needs: any BotFather command as the method, no params.

        Parameters:
            - method (str): Method name.
                mtproto: 'messages.sendMessage', 'channels.joinChannel', etc.
                botapi:  'sendMessage', 'getMe', 'setMyDescription', etc.
                bf:      '/mybots', '/setname', '/setdescription', etc.
            - params (dict): Parameters for the method.
            - protocol (str): 'mtproto', 'botapi', or 'bf' (default: mtproto).
            - bot (str | None): Bot @username (required for botapi).

        Examples:
            ── MTProto (3 examples) ──

            - Send a message as yourself (same as `chat-send`):
                `tg-proxy do raw '{"method":"messages.sendMessage","params":{"peer":"@user","message":"Hi!"},"protocol":"mtproto"}'`
                → {"protocol":"mtproto","method":"messages.sendMessage","result":"..."}

            - Get your own user info (same as `admin status`):
                `tg-proxy do raw '{"method":"users.getFullUser","params":{"id":"me"},"protocol":"mtproto"}'`
                → {"protocol":"mtproto","method":"users.getFullUser","result":"..."}

            - List recent messages from BotFather (same as `chat-read`):
                `tg-proxy do raw '{"method":"messages.getHistory","params":{"peer":93372553,"limit":5},"protocol":"mtproto"}'`
                → {"protocol":"mtproto","method":"messages.getHistory","result":"..."}

            ── Bot API (3 examples) ──

            - Send a message as a bot (same as `bot-send`):
                `tg-proxy do raw '{"method":"sendMessage","params":{"chat_id":93372553,"text":"Hello"},"protocol":"botapi","bot":"@my_bot"}'`
                → {"protocol":"botapi","method":"sendMessage","result":{"ok":true,...}}

            - Get bot webhook info (same as `webhook-get`):
                `tg-proxy do raw '{"method":"getWebhookInfo","params":{},"protocol":"botapi","bot":"@my_bot"}'`
                → {"protocol":"botapi","method":"getWebhookInfo","result":{"ok":true,...}}

            - Set bot description (same as BotFather `/setdescription`):
                `tg-proxy do raw '{"method":"setMyDescription","params":{"description":"My awesome bot"},"protocol":"botapi","bot":"@my_bot"}'`
                → {"protocol":"botapi","method":"setMyDescription","result":{"ok":true,...}}

            ── BotFather (3 examples) ──

            - List all your bots (same as `bot-list`):
                `tg-proxy do raw '{"method":"/mybots","protocol":"bf"}'`
                → {"protocol":"bf","command":"/mybots","response":"..."}

            - Change a bot's name:
                `tg-proxy do raw '{"method":"/setname","protocol":"bf"}'`
                → {"protocol":"bf","command":"/setname","response":"Choose a bot to change name."}

            - Get a bot's API token (same as `bot-token`):
                `tg-proxy do raw '{"method":"/token","protocol":"bf"}'`
                → {"protocol":"bf","command":"/token","response":"Choose a bot to get token."}
        """
        c = await self._telethon()

        if payload.protocol == "mtproto":
            # Dynamic MTProto method resolution
            import importlib

            parts = payload.method.split(".")
            module_path = "telethon.tl.functions." + ".".join(parts[:-1])
            raw_name = parts[-1]
            # Convert to PascalCase: getPrivacy → GetPrivacy
            if raw_name and raw_name[0].islower():
                pascal = raw_name[0].upper() + raw_name[1:]
            else:
                pascal = raw_name
            class_name = pascal if pascal.endswith("Request") else pascal + "Request"
            try:
                module = importlib.import_module(module_path)
                request_class = getattr(module, class_name)
            except (ImportError, AttributeError) as e:
                return {"error": f"Unknown method '{payload.method}': {e}"}
            # Apply type_hints: wrap string params in Telethon TLObject types
            if payload.type_hints:
                import importlib as _il

                _tl_types = _il.import_module("telethon.tl.types")
                converted = dict(payload.params)
                for pname, tname in payload.type_hints.items():
                    if pname in converted:
                        try:
                            tl_class = getattr(_tl_types, tname)
                            # Some TLObjects take no args (e.g. InputPrivacyKeyStatus),
                            # others take the value as positional arg
                            try:
                                converted[pname] = tl_class(converted[pname])
                            except TypeError:
                                converted[pname] = tl_class()
                        except AttributeError as _e:
                            return {
                                "error": f"Unknown TLObject type '{tname}' for '{pname}'"
                            }
                params_for_request = converted
            else:
                params_for_request = payload.params
            request_obj = request_class(**params_for_request)  # type: ignore[reportCallIssue]
            result = await c(request_obj)
            return {
                "protocol": "mtproto",
                "method": payload.method,
                "result": str(result),
            }

        elif payload.protocol == "botapi":
            # Bot API HTTP call
            if not payload.bot:
                return {"error": "bot parameter is required for botapi protocol"}

            token_key = f"{payload.bot.lstrip('@').upper()}_TOKEN"
            token = _read_token_from_env(token_key)
            if not token:
                return {"error": f"Token for {payload.bot} not found"}
            import httpx

            url = f"https://api.telegram.org/bot{token}/{payload.method.lstrip('/')}"
            if payload.upload_files:
                from pathlib import Path as P

                files = []
                for fp in payload.upload_files:
                    fpath = P(fp)
                    if not fpath.exists():
                        return {"error": f"File not found: {fp}"}
                    files.append(
                        (
                            "document",
                            (
                                fpath.name,
                                fpath.read_bytes(),
                                "application/octet-stream",
                            ),
                        )
                    )
                data = {}
                for k, v in (payload.params or {}).items():
                    import json as _json

                    data[k] = _json.dumps(v) if isinstance(v, (list, dict)) else str(v)
                async with httpx.AsyncClient(timeout=60) as hc:
                    resp = await hc.post(url, data=data, files=files)
                return {
                    "protocol": "botapi",
                    "method": payload.method,
                    "result": resp.json(),
                }
            else:
                async with httpx.AsyncClient() as hc:
                    resp = await hc.post(url, json=payload.params or {})
                return {
                    "protocol": "botapi",
                    "method": payload.method,
                    "result": resp.json(),
                }

        elif payload.protocol == "bf":
            # BotFather conversation
            if payload.steps:
                await c.send_message(BOTFATHER_ID, payload.method)
                await asyncio.sleep(2)
                for step in payload.steps:
                    if step == "__photo__":
                        if not payload.photo:
                            return {"error": "__photo__ step requires 'photo' field"}
                        await c.send_file(BOTFATHER_ID, payload.photo)
                    else:
                        await c.send_message(BOTFATHER_ID, step)
                    await asyncio.sleep(2)
                from typing import cast

                final = cast(list, await c.get_messages(BOTFATHER_ID, limit=1))
                final_text = final[0].text if final else "(no response)"
                return {
                    "protocol": "bf",
                    "command": payload.method,
                    "steps": payload.steps,
                    "final_response": final_text,
                }
            else:
                await c.send_message(BOTFATHER_ID, payload.method)
                await asyncio.sleep(2)
                from typing import cast

                bf_msgs = cast(list, await c.get_messages(BOTFATHER_ID, limit=1))
                response_text = bf_msgs[0].text if bf_msgs else "(no response)"
                return {
                    "protocol": "bf",
                    "command": payload.method,
                    "response": response_text,
                }

        else:
            return {
                "error": f"Unknown protocol '{payload.protocol}'. Use 'mtproto', 'botapi', or 'bf'."
            }

    # ─── Internal helpers ───

    async def _get_self_id(self) -> int:
        c = await self._telethon()
        me = await c.get_me()
        return me.id


async def client_send(client, entity, message: str):
    """Helper to send a message via Telethon."""
    await client.send_message(entity, message)
