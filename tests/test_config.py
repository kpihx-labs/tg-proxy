"""Tests for tg_proxy.config secure local storage helpers."""

from tg_proxy import config


def test_write_env_enforces_owner_only_permissions(tmp_path, monkeypatch):
    config_dir = tmp_path / "tg-proxy"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "ENV_PATH", config_dir / ".env")
    monkeypatch.setattr(config, "SESSION_PATH", config_dir / "user.session")

    config.write_env({"TG_API_ID": "123", "BOT_TOKEN": "secret-value"})

    assert (config_dir.stat().st_mode & 0o777) == config.DIR_PERMISSIONS
    assert (config.ENV_PATH.stat().st_mode & 0o777) == config.FILE_PERMISSIONS
    assert config.read_env()["BOT_TOKEN"] == "secret-value"


def test_status_masks_bot_tokens_and_reports_permissions(tmp_path, monkeypatch):
    config_dir = tmp_path / "tg-proxy"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "ENV_PATH", config_dir / ".env")
    monkeypatch.setattr(config, "SESSION_PATH", config_dir / "user.session")
    config.write_env({"TG_API_ID": "123", "MY_BOT_TOKEN": "1234-very-sensitive-token"})
    config.SESSION_PATH.write_text("session")
    config.SESSION_PATH.chmod(config.FILE_PERMISSIONS)

    status = config.config_status()

    assert status["bot_tokens"] == [
        {"key": "MY_BOT_TOKEN", "present": True, "value": "1234…token"}
    ]
    assert status["api_id"] == "123"
    assert all(item["status"] == "ok" for item in status["permissions"].values())


def test_reset_storage_removes_credentials_and_session(tmp_path, monkeypatch):
    config_dir = tmp_path / "tg-proxy"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "ENV_PATH", config_dir / ".env")
    monkeypatch.setattr(config, "SESSION_PATH", config_dir / "user.session")
    config.write_env({"TG_API_ID": "123"})
    config.SESSION_PATH.write_text("session")

    result = config.reset_storage()

    assert result == {"config_file_deleted": True, "session_file_deleted": True}
    assert config.CONFIG_DIR.exists()
    assert not config.ENV_PATH.exists()
    assert not config.SESSION_PATH.exists()
