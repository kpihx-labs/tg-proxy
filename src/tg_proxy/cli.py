"""
tg-proxy CLI — Single binary, two namespaces.

Usage:
    tg-proxy admin setup|status
    tg-proxy do <action> [payload] [--output-file/-o] [--format/-f]

All output in JSON (default) or table format.
Admin is ALWAYS JSON. 'do' defaults to JSON, can switch to table.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

TG_PROXY_AUTOSAVE_DIR = Path("/tmp/tg-proxy-autosave")

import typer
from pydantic import ValidationError

from . import __version__
from .client import TgClient
from .config import config_status, ensure_env, purge_storage, reset_storage
from .display import (
    console,
    print_error,
    print_json,
    print_table,
)
from .doc import get_full_help
from .exceptions import TgProxyError
from .logger import setup_logging
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
    OutputMeta,
    RawPayload,
    UpdatesPayload,
    WebhookDelPayload,
    WebhookGetPayload,
    WebhookSetPayload,
)

app = typer.Typer(
    name="tg-proxy",
    help="Telegram administrative proxy — RPC CLI for bot and user management.",
    add_completion=False,
)
app_admin = typer.Typer(help="Admin commands: setup, status, reset, purge.")


app_do = typer.Typer(
    help="RPC actions: bot-list, bot-info, bot-token, bot-create, …",
    add_completion=False,
    add_help_option=False,
)

app.add_typer(app_admin, name="admin")
app.add_typer(app_do, name="do")


# ─── Helpers ───


def run_async(coro):
    return asyncio.run(coro)


def _dump_json_sync(path: Path, data: dict) -> None:
    """Write JSON to a file synchronously (for use with asyncio.to_thread)."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def parse_payload(payload_str: str | None) -> dict:
    """Convert JSON string or file path to dict (ts_proxy style RPC)."""
    if not payload_str:
        return {}
    try:
        return json.loads(payload_str)
    except json.JSONDecodeError:
        path = Path(payload_str)
        if path.exists():
            with open(path) as f:
                return json.load(f)
        raise TgProxyError(f"Invalid JSON or file not found: {payload_str}")


def get_client() -> TgClient:
    ensure_env()
    return TgClient()


def output_result(result: dict, format: str = "json"):
    """Print the result dict (which has meta + data) in requested format."""
    meta = result.get("meta", {})
    data = result.get("data", result)
    if (
        format == "table"
        and isinstance(data, (list, dict))
        and not isinstance(data, str)
    ):
        console.print("[bold blue]Meta:[/]")
        print_table(meta)
        console.print("[bold blue]Data:[/]")
        print_table(data)
    else:
        print_json(data=result)


# ═══════════════════════════════════════════════════
# VERSION CALLBACK
# ═══════════════════════════════════════════════════


def _version_callback(value: bool):
    if value:
        console.print(f"tg-proxy v{__version__}")
        raise typer.Exit()


def _do_help_callback(value: bool = True):
    if value:
        from .client import TgClient
        from .doc import get_compact_help

        console.print(
            "[bold yellow]For detailed information and examples on a specific"
            " action, run:[/bold yellow]"
        )
        console.print("  [bold]tg-proxy do <action> --help[/bold]\n")

        commands = {
            "bot-list": TgClient.bot_list,
            "bot-info": TgClient.bot_info,
            "bot-token": TgClient.bot_token,
            "bot-create": TgClient.bot_create,
            "bot-delete": TgClient.bot_delete,
            "bot-send": TgClient.bot_send,
            "bot-send-file": TgClient.bot_send_file,
            "chat-list": TgClient.chat_list,
            "chat-read": TgClient.chat_read,
            "chat-send": TgClient.chat_send,
            "chat-send-file": TgClient.chat_send_file,
            "chat-download": TgClient.chat_download,
            "chat-delete": TgClient.chat_delete,
            "chat-delete-messages": TgClient.chat_delete_messages,
            "bot-photo": TgClient.bot_photo,
            "raw": TgClient.raw,
            "folder-list": TgClient.folder_list,
            "folder-set": TgClient.folder_set,
            "folder-delete": TgClient.folder_delete,
            "chat-move": TgClient.chat_move,
            "updates": TgClient.updates,
            "webhook-get": TgClient.webhook_get,
            "webhook-set": TgClient.webhook_set,
            "webhook-del": TgClient.webhook_del,
        }
        for name, method in commands.items():
            doc = get_compact_help(method)
            console.print(f"[bold cyan]{name}[/bold cyan]")
            console.print(doc)
            console.print()
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True
    ),
):
    setup_logging()


# ═══════════════════════════════════════════════════
# ADMIN COMMANDS
# ═══════════════════════════════════════════════════


@app_admin.command("setup")
def admin_setup():
    """Authenticate and create ~/.config/tg-proxy/.env — HITL web form."""
    api_id = typer.prompt("TG_API_ID")
    api_hash = typer.prompt("TG_API_HASH", hide_input=True)
    phone = typer.prompt("Phone (e.g. +336XXXXXXXX)")

    async def _run():
        return await _do_admin_setup(api_id, api_hash, phone)

    result = run_async(_run())
    print_json(data=result)


async def _do_admin_setup(api_id: str, api_hash: str, phone: str) -> dict:
    """HITL-protected admin setup. Uses TgClient.__new__ to bypass ensure_env()."""
    client = TgClient.__new__(TgClient)
    client.api_id = int(api_id)
    client.api_hash = api_hash
    client._client = None
    result = await client.admin_setup(
        {
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
        }
    )
    await client.close()
    return result


@app_admin.command("status")
def admin_status():
    """Report Telegram identity, stored token presence and sensitive-path permissions."""
    result = config_status()
    try:
        client = get_client()
        result.update(run_async(client.admin_status()))
        _close_client(client)
        status = "ok"
    except (TgProxyError, OSError, ValueError) as exc:
        result["authorization"] = {"status": "unavailable", "reason": str(exc)}
        status = "warning"
    print_json(
        data={
            "meta": {"status": status, "comment": "", "edited": False},
            "data": result,
        }
    )


@app_admin.command("reset")
def admin_reset():
    """Delete stored credentials and the local Telegram session after HITL approval."""

    async def _run():
        from .hitl import request_approval

        response = await request_approval(
            "admin reset",
            {
                "action": "delete_credentials_and_session",
                "config_file": str(config_status()["config"]),
                "session_file": str(
                    config_status()["permissions"]["session_file"]["path"]
                ),
                "confirm": "Yes, delete credentials and the Telegram session",
            },
        )
        if response.status == "rejected":
            return {
                "meta": {
                    "status": "rejected",
                    "comment": response.comment,
                    "edited": response.edited,
                },
                "data": None,
            }
        return {
            "meta": {
                "status": response.status,
                "comment": response.comment,
                "edited": response.edited,
            },
            "data": {"status": "reset", **reset_storage()},
        }

    print_json(data=run_async(_run()))


@app_admin.command("purge")
def admin_purge():
    """Remove local configuration after HITL approval; print final tool-uninstall command."""

    async def _run():
        from .hitl import request_approval

        response = await request_approval(
            "admin purge",
            {
                "action": "delete_config_and_uninstall",
                "config_dir": config_status()["permissions"]["config_dir"]["path"],
                "uninstalled_tool": "tg-proxy",
                "confirm": "Yes, delete configuration and uninstall the CLI",
            },
        )
        if response.status == "rejected":
            return {
                "meta": {
                    "status": "rejected",
                    "comment": response.comment,
                    "edited": response.edited,
                },
                "data": None,
            }
        return {
            "meta": {
                "status": response.status,
                "comment": response.comment,
                "edited": response.edited,
            },
            "data": {
                "status": "purged",
                "config_dir_deleted": purge_storage(),
                "uninstalled": False,
                "note": "Configuration removed. To fully uninstall the CLI, run: uv tool uninstall tg-proxy",
            },
        }

    print_json(data=run_async(_run()))


# ═══════════════════════════════════════════════════
# DO COMMANDS — RPC STYLE
# ═══════════════════════════════════════════════════

# --- Meta options constants ---

OUTPUT_FILE_OPT = typer.Option(
    None, "--output-file", "-o", help="Write output to file (required for bot-token)."
)
FORMAT_OPT = typer.Option(
    "json", "--format", "-f", help="Output format: json (default) or table."
)


def _make_rpc(action_func, PayloadClass, hitl: bool = False):
    """Factory for RPC do commands (inspired by ts_proxy autosave pattern)."""

    async def execute(payload_raw: str | None, output_file: str | None, fmt: str):
        # Check required .env for all do commands (bot-token skips check)
        if action_func.__name__ not in ("_do_bot_token",):
            ensure_env()

        # Parse payload
        params = parse_payload(payload_raw) if payload_raw else {}

        # If HITL is needed, the function handles it via decorator
        try:
            if PayloadClass and params:
                validated = PayloadClass(**params)
            else:
                validated = None
        except (TgProxyError, ValidationError) as e:
            print_error(f"Validation error: {e}")
            sys.exit(1)

        client = get_client()
        try:
            if validated:
                result = await action_func(client, validated)
            else:
                result = await action_func(client)
        except (TgProxyError, ValidationError) as e:
            print_error(str(e))
            sys.exit(1)
        finally:
            await client.close()

        # Autosave to /tmp
        TG_PROXY_AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
        autosave_path = (
            TG_PROXY_AUTOSAVE_DIR
            / f"{action_func.__name__.replace('_', '-')}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
        )
        await asyncio.to_thread(_dump_json_sync, autosave_path, result)

        # Handle output file
        if output_file:
            out_path = Path(output_file)
            await asyncio.to_thread(out_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(_dump_json_sync, out_path, result)
            console.print(f"[dim]📄 Written to: {out_path}[/dim]")
        else:
            console.print(f"[dim]💾 Autosave: {autosave_path}[/dim]")

        # Display
        print_json(data=result)

    return execute


@app_do.callback(invoke_without_command=True)
def do_main(
    ctx: typer.Context,
    show_help: bool = typer.Option(
        False, "--help", "-h", help="Show help.", hidden=True
    ),
):
    if show_help or ctx.invoked_subcommand is None:
        _do_help_callback(True)


@app_do.command("bot-list", help=get_full_help(TgClient.bot_list))
def do_bot_list(
    payload: str | None = typer.Argument(
        None, help="JSON payload or file path (optional)."
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """List ALL owned bots (getAdminedBots)."""
    run_async(_do_bot_list_inner(payload, output_file, fmt))


async def _do_bot_list_inner(payload: str | None, output_file: str | None, fmt: str):
    ensure_env()
    client = get_client()
    try:
        data = await client.bot_list(
            BotListPayload(**parse_payload(payload) if payload else {})
        )
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        await client.close()
    _write_and_display(result, output_file, fmt, "bot-list")


@app_do.command("bot-info", help=get_full_help(TgClient.bot_info))
def do_bot_info(
    payload: str = typer.Argument(
        ..., help='JSON: {"bots":["@bot1","@bot2"]} or file path.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Get details for one or MORE bots."""
    run_async(_do_bot_info_inner(payload, output_file, fmt))


async def _do_bot_info_inner(payload: str, output_file: str | None, fmt: str):
    ensure_env()
    params = parse_payload(payload)
    validated = BotInfoPayload(**params)
    client = get_client()
    try:
        data = await client.bot_info(validated)
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        await client.close()
    _write_and_display(result, output_file, fmt, "bot-info")


@app_do.command("bot-token", help=get_full_help(TgClient.bot_token))
def do_bot_token(
    payload: str = typer.Argument(..., help='JSON: {"bots":["@bot1"]} or file path.'),
):
    """⭐ Get bot token(s) — appends to ~/.config/tg-proxy/.env (NO --output-file, NO --format).

    This is the ONLY do command that does NOT accept --output-file or --format.
    It always writes (appends) to ~/.config/tg-proxy/.env in BOT_USERNAME_UPPER=token format.
    HITL required.
    """
    params = parse_payload(payload)
    validated = BotTokenPayload(**params)
    client = get_client()
    try:
        data = run_async(client.bot_token(validated))
        result = {
            "meta": OutputMeta(
                status="approved", comment="", edited=False
            ).model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    print_json(data=result)


@app_do.command("bot-create", help=get_full_help(TgClient.bot_create))
def do_bot_create(
    payload: str = typer.Argument(
        ..., help='JSON: {"bots":[{"name":"X","username":"x"}]} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Create ONE or MORE bots (HITL, max privacy — no groups, no inline)."""
    params = parse_payload(payload)
    validated = BotCreatePayload(**params)
    client = get_client()
    try:
        data = run_async(client.bot_create(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="", edited=False).model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "bot-create")


@app_do.command("bot-delete", help=get_full_help(TgClient.bot_delete))
def do_bot_delete(
    payload: str = typer.Argument(..., help='JSON: {"bots":["@bot1"]} or file path.'),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Delete ONE or MORE bots (HITL)."""
    params = parse_payload(payload)
    validated = BotDeletePayload(**params)
    client = get_client()
    try:
        data = run_async(client.bot_delete(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="", edited=False).model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "bot-delete")


@app_do.command("bot-send", help=get_full_help(TgClient.bot_send))
def do_bot_send(
    payload: str = typer.Argument(
        ..., help='JSON: {"bot":"@bot","message":"Hi"} or file path.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Send a message AS a bot (to ME, HITL with editing)."""
    params = parse_payload(payload)
    validated = BotSendPayload(**params)
    client = get_client()
    try:
        data = run_async(client.bot_send(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="", edited=False).model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "bot-send")


@app_do.command("bot-send-file", help=get_full_help(TgClient.bot_send_file))
def do_bot_send_file(
    payload: str = typer.Argument(
        ..., help='JSON: {"bot":"@bot","message":"","files":["/a.pdf"]} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Send a message + list of files AS a bot (to ME, HITL)."""
    params = parse_payload(payload)
    validated = BotSendFilePayload(**params)
    client = get_client()
    try:
        data = run_async(client.bot_send_file(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="", edited=False).model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "bot-send-file")


@app_do.command("chat-list", help=get_full_help(TgClient.chat_list))
def do_chat_list(
    payload: str | None = typer.Argument(
        None, help='JSON: {"type":"user","limit":30} or file. Optional.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """List your conversations."""
    params = parse_payload(payload) if payload else {}
    validated = ChatListPayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_list(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-list")


@app_do.command("chat-read", help=get_full_help(TgClient.chat_read))
def do_chat_read(
    payload: str = typer.Argument(
        ..., help='JSON: {"chat":93372553,"limit":5} or file path.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Read messages from a chat."""
    params = parse_payload(payload)
    validated = ChatReadPayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_read(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-read")


@app_do.command("chat-send", help=get_full_help(TgClient.chat_send))
def do_chat_send(
    payload: str = typer.Argument(
        ..., help='JSON: {"to":"@YourUser","message":"Hi"} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Send a message as YOU to anyone (contact, bot, BotFather)."""
    params = parse_payload(payload)
    validated = ChatSendPayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_send(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-send")


@app_do.command("chat-send-file", help=get_full_help(TgClient.chat_send_file))
def do_chat_send_file(
    payload: str = typer.Argument(
        ..., help='JSON: {"to":"@YourUser","message":"","files":["/a.pdf"]} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Send a message + files as YOU to anyone."""
    params = parse_payload(payload)
    validated = ChatSendFilePayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_send_file(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-send-file")


@app_do.command("chat-download", help=get_full_help(TgClient.chat_download))
def do_chat_download(
    payload: str = typer.Argument(
        ..., help='JSON: {"chat":"@x","message_ids":[42],"out":"/tmp"} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Download media files from a chat by message_id(s)."""
    params = parse_payload(payload)
    validated = ChatDownloadPayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_download(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-download")


@app_do.command("chat-delete", help=get_full_help(TgClient.chat_delete))
def do_chat_delete(
    payload: str = typer.Argument(
        ..., help='JSON: {"chat":"@chat","revoke":true} or file path.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Delete an entire chat conversation."""
    params = parse_payload(payload)
    validated = ChatDeletePayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_delete(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-delete")


@app_do.command(
    "chat-delete-messages", help=get_full_help(TgClient.chat_delete_messages)
)
def do_chat_delete_messages(
    payload: str = typer.Argument(
        ..., help='JSON: {"chat":"@chat","message_ids":[42,43]} or file path.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Delete specific messages from a chat."""
    params = parse_payload(payload)
    validated = ChatDeleteMessagesPayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_delete_messages(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-delete-messages")


@app_do.command("bot-photo", help=get_full_help(TgClient.bot_photo))
def do_bot_photo(
    payload: str = typer.Argument(
        ..., help='JSON: {"bots":["@bot1","@bot2"],"out":"/tmp/photos"} or file path.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Download profile photo(s) from bots/users."""
    params = parse_payload(payload)
    validated = BotPhotoPayload(**params)
    client = get_client()
    try:
        data = run_async(client.bot_photo(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "bot-photo")


@app_do.command("folder-list", help=get_full_help(TgClient.folder_list))
def do_folder_list(
    payload: str | None = typer.Argument(
        None, help="JSON payload or file path (optional)."
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """List all Telegram chat folders with their chats."""
    params = parse_payload(payload) if payload else {}
    validated = FolderListPayload(**params)
    client = get_client()
    try:
        data = run_async(client.folder_list(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "folder-list")


@app_do.command("folder-set", help=get_full_help(TgClient.folder_set))
def do_folder_set(
    payload: str = typer.Argument(
        ..., help='JSON: {"title":"X","chats":["@bot1"],"icon":"💼"} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Create or update a chat folder (UPSERT)."""
    params = parse_payload(payload)
    validated = FolderSetPayload(**params)
    client = get_client()
    try:
        data = run_async(client.folder_set(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "folder-set")


@app_do.command("folder-delete", help=get_full_help(TgClient.folder_delete))
def do_folder_delete(
    payload: str = typer.Argument(..., help='JSON: {"title":"Work"} or file path.'),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Delete a chat folder by title."""
    params = parse_payload(payload)
    validated = FolderDeletePayload(**params)
    client = get_client()
    try:
        data = run_async(client.folder_delete(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "folder-delete")


@app_do.command("chat-move", help=get_full_help(TgClient.chat_move))
def do_chat_move(
    payload: str = typer.Argument(
        ..., help='JSON: {"chat":"@bot1","to":"Work"} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Move a chat between folders."""
    params = parse_payload(payload)
    validated = ChatMovePayload(**params)
    client = get_client()
    try:
        data = run_async(client.chat_move(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "chat-move")


@app_do.command("updates", help=get_full_help(TgClient.updates))
def do_updates(
    payload: str = typer.Argument(
        ..., help='JSON: {"bot":"@bot","limit":10} or file path.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Read bot's received messages (inbox)."""
    params = parse_payload(payload)
    validated = UpdatesPayload(**params)
    client = get_client()
    try:
        data = run_async(client.updates(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "updates")


@app_do.command("webhook-get", help=get_full_help(TgClient.webhook_get))
def do_webhook_get(
    payload: str = typer.Argument(..., help='JSON: {"bot":"@bot"} or file path.'),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Show webhook configuration for a bot."""
    params = parse_payload(payload)
    validated = WebhookGetPayload(**params)
    client = get_client()
    try:
        data = run_async(client.webhook_get(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "webhook-get")


@app_do.command("webhook-set", help=get_full_help(TgClient.webhook_set))
def do_webhook_set(
    payload: str = typer.Argument(
        ..., help='JSON: {"bot":"@bot","url":"https://..."} or file.'
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Set webhook URL for a bot.

    ⚠️ IMPORTANT: Filter by from.id in your webhook handler to prevent unauthorized access.
    """
    params = parse_payload(payload)
    validated = WebhookSetPayload(**params)
    client = get_client()
    try:
        data = run_async(client.webhook_set(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "webhook-set")


@app_do.command("webhook-del", help=get_full_help(TgClient.webhook_del))
def do_webhook_del(
    payload: str = typer.Argument(..., help='JSON: {"bot":"@bot"} or file path.'),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Delete webhook for a bot."""
    params = parse_payload(payload)
    validated = WebhookDelPayload(**params)
    client = get_client()
    try:
        data = run_async(client.webhook_del(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "webhook-del")


# ═══════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════


def _write_and_display(
    result: dict, output_file: str | None, fmt: str, action_name: str
):
    """Centralized output handling (ts_proxy autosave pattern)."""
    # Flatten double-wrapping: HITL decorator adds meta+data, CLI re-wraps → use decorator's meta
    if isinstance(result, dict):
        inner = result.get("data")
        if isinstance(inner, dict) and "meta" in inner and "data" in inner:
            result = inner
    TG_PROXY_AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    autosave_path = (
        TG_PROXY_AUTOSAVE_DIR
        / f"{action_name}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(autosave_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    if output_file:
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        console.print(f"[dim]📄 Written to: {out_path}[/dim]")
    else:
        console.print(f"[dim]💾 Autosave: {autosave_path}[/dim]")
    output_result(result, format=fmt)


@app_do.command("raw", help=get_full_help(TgClient.raw))
def do_raw(
    payload: str = typer.Argument(
        ...,
        help='JSON: {"method":"...","params":{...},"protocol":"mtproto|botapi|bf","bot":"@bot"} or file.',
    ),
    output_file: str | None = OUTPUT_FILE_OPT,
    fmt: str = FORMAT_OPT,
):
    """Execute ANY Telegram operation via raw method call (HITL)."""
    params = parse_payload(payload)
    validated = RawPayload(**params)
    client = get_client()
    try:
        data = run_async(client.raw(validated))
        result = {
            "meta": OutputMeta(status="ok", comment="").model_dump(),
            "data": data,
        }
    except (TgProxyError, ValidationError) as e:
        print_error(str(e))
        sys.exit(1)
    finally:
        run_async(client.close())
    _write_and_display(result, output_file, fmt, "raw")


def _close_client(client: TgClient):
    try:
        run_async(client.close())
    except (TgProxyError, ValidationError):
        pass


# ═══════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    app()
