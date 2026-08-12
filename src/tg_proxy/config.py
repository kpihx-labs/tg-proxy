"""
Minimal .env config loader for tg-proxy.

Reads ~/.config/tg-proxy/.env and validates TG_API_ID and TG_API_HASH.
"""

import os
import shutil
import sys
from pathlib import Path

from .exceptions import TgProxyError

CONFIG_DIR = Path.home() / ".config" / "tg-proxy"
ENV_PATH = CONFIG_DIR / ".env"
SESSION_PATH = CONFIG_DIR / "user.session"
DIR_PERMISSIONS = 0o700
FILE_PERMISSIONS = 0o600


def _mask(value: str) -> str:
    """Mask a sensitive value while preserving enough context for diagnostics."""
    if not value:
        return ""
    return f"{value[:4]}…{value[-5:]}" if len(value) > 12 else "…"


def ensure_secure_storage() -> None:
    """Create the config directory and enforce its owner-only mode."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(DIR_PERMISSIONS)


def _permission_record(path: Path, expected: int) -> dict[str, str | None]:
    """Describe one sensitive path without changing its permissions."""
    if not path.exists():
        return {"path": str(path), "mode": None, "status": "absent", "fix": None}
    mode = path.stat().st_mode & 0o777
    return {
        "path": str(path),
        "mode": oct(mode),
        "status": "ok" if mode == expected else "warning",
        "fix": None if mode == expected else f"chmod {expected:o} {path}",
    }


def config_status() -> dict:
    """Report stored credentials, session state and sensitive-path permissions."""
    values = load_env()
    bot_tokens = [
        {"key": key, "present": True, "value": _mask(value)}
        for key, value in sorted(values.items())
        if key.endswith("_TOKEN")
    ]
    return {
        "config": str(ENV_PATH),
        "config_exists": ENV_PATH.exists(),
        "api_id": values.get("TG_API_ID", ""),
        "api_hash": _mask(values.get("TG_API_HASH", "")),
        "bot_tokens": bot_tokens,
        "session_exists": SESSION_PATH.exists(),
        "binary": shutil.which("tg-proxy") or os.path.abspath(sys.argv[0]),
        "permissions": {
            "config_dir": _permission_record(CONFIG_DIR, DIR_PERMISSIONS),
            "config_file": _permission_record(ENV_PATH, FILE_PERMISSIONS),
            "session_file": _permission_record(SESSION_PATH, FILE_PERMISSIONS),
        },
    }


def ensure_env() -> None:
    """Check that ~/.config/tg-proxy/.env exists and has required keys."""
    if not ENV_PATH.exists():
        raise TgProxyError(
            f"Config file not found at {ENV_PATH}. Run 'tg-proxy admin setup' first."
        )
    load_env()
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        raise TgProxyError(
            f"{ENV_PATH} is missing TG_API_ID or TG_API_HASH. "
            "Run 'tg-proxy admin setup' to configure."
        )


def read_env() -> dict[str, str]:
    """Read the managed .env file without changing the process environment."""
    if not ENV_PATH.exists():
        return {}
    result: dict[str, str] = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                result[key] = val
    return result


def load_env() -> dict[str, str]:
    """Load .env file into os.environ and return its parsed values."""
    result = read_env()
    for key, value in result.items():
        os.environ.setdefault(key, value)
    return result


def append_env(key: str, value: str) -> None:
    """Append a key=value line and preserve owner-only configuration modes."""
    ensure_secure_storage()
    with open(ENV_PATH, "a") as f:
        f.write(f"\n{key}={value}\n")
    ENV_PATH.chmod(FILE_PERMISSIONS)


def write_env(values: dict[str, str]) -> None:
    """Rewrite the managed .env file and enforce owner-only permissions."""
    ensure_secure_storage()
    lines = [
        "# tg-proxy configuration — managed by `tg-proxy admin setup`.",
        "# Telegram API credentials and bot tokens are sensitive.",
        "",
    ]
    lines.extend(f"{key}={value}" for key, value in values.items() if value)
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(FILE_PERMISSIONS)


def reset_storage() -> dict[str, bool]:
    """Remove persisted credentials and the Telethon session, retaining the config directory."""
    removed_env = ENV_PATH.exists()
    removed_session = SESSION_PATH.exists()
    if removed_env:
        ENV_PATH.unlink()
    if removed_session:
        SESSION_PATH.unlink()
    ensure_secure_storage()
    return {"config_file_deleted": removed_env, "session_file_deleted": removed_session}


def purge_storage() -> bool:
    """Remove the complete local configuration directory."""
    if not CONFIG_DIR.exists():
        return False
    shutil.rmtree(CONFIG_DIR)
    return True


def get_api_credentials() -> tuple[str, str]:
    """Return (api_id, api_hash) from the environment."""
    api_id = os.environ.get("TG_API_ID", "")
    api_hash = os.environ.get("TG_API_HASH", "")
    return api_id, api_hash
