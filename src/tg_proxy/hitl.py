"""
Human-in-the-Loop web UI for tg-proxy.

Adapted from ts_proxy. Launches a local web server on an OS-assigned free port
(see `_find_free_port()`) that shows the action payload for review. The user can
edit the payload, add a comment, then approve or reject. The chosen port is printed
with the review URL on every invocation, so it is never guessed by the caller.

A fixed port is deliberately NOT used: two concurrent `tg-proxy do` invocations
would collide on it, and the second HITL server would fail to bind. Binding to
port 0 lets the kernel hand out a guaranteed-free port instead.

100% Web UI — no TUI fallback. If no browser, the URL is printed for SSH/GUI access.
"""

import asyncio
import json
import logging
import socket
import threading
import uuid
from collections.abc import Callable, Coroutine
from functools import wraps
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

HITL_TIMEOUT: int | None = 300  # seconds (None = no timeout)


def _find_free_port() -> int:
    """Bind to port 0 to get a random free port from the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


TEMPLATE_PATH = Path(__file__).parent / "templates" / "hitl.html"

HITL_REQUIRED_OPERATIONS = [
    "admin_setup",
    "admin_reset",
    "admin_purge",
    "bot_token",
    "bot_create",
    "bot_delete",
    "bot_send",
    "bot_send_file",
]


class HITLResponse:
    def __init__(
        self, status: str, payload: Any = None, comment: str = "", edited: bool = False
    ):
        self.status = status
        self.payload = payload
        self.comment = comment
        self.edited = edited


class HITLServer(BaseHTTPRequestHandler):
    active_requests: ClassVar[dict[str, dict[str, Any]]] = {}

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/review"):
            query = self.path.split("?")[-1]
            req_id = query.split("id=")[-1] if "id=" in query else ""
            if req_id not in self.active_requests:
                self.send_error(404, "Review request not found.")
                return
            req = self.active_requests[req_id]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(self._render(req_id, req).encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/submit":
            length = int(self.headers.get("Content-Length", 0))
            post = json.loads(self.rfile.read(length).decode("utf-8"))
            req_id = post.get("id", "")
            if req_id in self.active_requests:
                req = self.active_requests[req_id]
                status = post.get("status", "rejected")
                comment = post.get("comment", "")
                edited = post.get("edited", False)
                payload_raw = post.get("payload")
                if isinstance(payload_raw, str):
                    try:
                        payload = json.loads(payload_raw)
                    except (json.JSONDecodeError, ValueError):
                        payload = payload_raw
                else:
                    payload = (
                        payload_raw if payload_raw is not None else req.get("payload")
                    )
                req["result"] = HITLResponse(status, payload, comment, edited)
                loop = req["loop"]
                loop.call_soon_threadsafe(req["event"].set)
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            else:
                self.send_error(404)

    def _render(self, req_id: str, req: dict) -> str:
        try:
            payload_display = json.dumps(req["payload"], indent=2)
            payload_safe = (
                json.dumps(req["payload"]).replace("\\", "\\\\").replace("'", "\\'")
            )
        except (TypeError, ValueError):
            safe = str(req.get("payload", {}))
            payload_display = safe
            payload_safe = safe[:100]
        try:
            html = TEMPLATE_PATH.read_text()
        except FileNotFoundError:
            return f"<html><body><h2>Template not found: {TEMPLATE_PATH}</h2></body></html>"
        html = html.replace("{{FUNC_NAME}}", req.get("func_name", "unknown"))
        html = html.replace("{{PAYLOAD_JSON}}", payload_display)
        html = html.replace("{{PAYLOAD_JSON_SAFE}}", payload_safe)
        html = html.replace("{{REQUEST_ID}}", req_id)
        return html


def require_approval():
    """Decorator that wraps a function with HITL web UI approval."""

    def decorator(func: Callable[..., Coroutine[Any, Any, Any]]):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            payload_dict = kwargs.get("payload") or (args[0] if args else None)
            if payload_dict and hasattr(payload_dict, "model_dump"):
                payload_dict = payload_dict.model_dump()
            rationale = kwargs.get("rationale", "")
            response = await request_approval(
                func.__name__, payload_dict, rationale=rationale
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
            payload = response.payload or payload_dict
            # Re-validate payload dict back into the correct Pydantic model
            model_candidate = args[1] if len(args) > 1 else (args[0] if args else None)
            if (
                payload
                and isinstance(payload, dict)
                and model_candidate is not None
                and hasattr(model_candidate, "model_validate")
            ):
                try:
                    kwargs["payload"] = model_candidate.model_validate(payload)
                except (ValueError, TypeError) as exc:
                    logger.warning("Failed to validate payload model: %s", exc)
                else:
                    payload = kwargs["payload"]
            result = await func(
                self, payload, **{k: v for k, v in kwargs.items() if k != "payload"}
            )
            if isinstance(result, dict):
                result = {
                    "meta": {
                        "status": response.status,
                        "comment": response.comment or "",
                        "edited": response.edited,
                    },
                    "data": result,
                }
            return result

        return wrapper

    return decorator


async def request_approval(
    func_name: str, payload: Any, rationale: str = ""
) -> HITLResponse:
    """Launch HITL web UI and wait for user approval."""
    req_id = str(uuid.uuid4())
    event = asyncio.Event()
    loop = asyncio.get_running_loop()

    req_context = {
        "func_name": func_name,
        "payload": payload,
        "rationale": rationale,
        "event": event,
        "loop": loop,
        "result": None,
    }
    HITLServer.active_requests[req_id] = req_context

    port = _find_free_port()
    server = HTTPServer(("127.0.0.1", port), HITLServer)
    url = f"http://127.0.0.1:{port}/review?id={req_id}"
    print("\n🚀 [HITL] ACTION REVIEW REQUIRED")
    print(f"🔗 {url}")
    print(f"📝 Action: {func_name}")
    print("If the browser doesn't open, connect from a machine with GUI:")
    print(f"   ssh -L {port}:localhost:{port} your-host")

    import webbrowser

    try:
        webbrowser.open(url)
    except OSError:
        logger.warning("Failed to open browser for HITL URL: %s", url)

    def serve():
        while not event.is_set():
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    try:
        await asyncio.wait_for(event.wait(), timeout=HITL_TIMEOUT)
    except TimeoutError:
        logger.warning("HITL timeout expired for %s (id=%s)", func_name, req_id)
        response = HITLResponse(
            status="rejected",
            payload=None,
            comment="HITL timeout expired (no response received)",
            edited=False,
        )
        del HITLServer.active_requests[req_id]
        server.server_close()
        return response

    response = req_context["result"]
    del HITLServer.active_requests[req_id]
    server.server_close()
    return response
