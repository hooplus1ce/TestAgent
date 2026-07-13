"""config.py 测试：环境变量读取与默认值。"""
import importlib
import os


def test_defaults():
    from drissionpage_mcp.core import config
    assert config.SCM_ADMIN_URL.startswith("https://")
    assert config.COOKIE_DOMAIN.startswith(".")
    assert config.SCM_ACCESS_DOMAIN.startswith(".")
    assert config.NEEDED_COOKIES == ["SESSION", "UCTOKEN", "cookie_token"]
    assert config.DEFAULT_PORT == 9222
    assert config.DEFAULT_TARGET_HINT == "诺贝科技"
    assert os.path.basename(config.SHOT_DIR) == "resources"
    assert hasattr(config, "CHROME_PATH")
    assert hasattr(config, "EDGE_MODE")
    assert hasattr(config, "PROXY")
    assert hasattr(config, "DISABLE_PDF_PREVIEW")
    assert hasattr(config, "REMOVE_TEST_TYPE")
    assert config.REFRESH_NAV_TIMEOUT == 10
    assert config.REFRESH_LOAD_TIMEOUT == 15
    assert config.REFRESH_HTTP_TIMEOUT == 15
    assert callable(config.make_chromium_options)


def test_env_override(monkeypatch):
    monkeypatch.setenv("HL_SCM_URL", "https://preprod.example.com/")
    monkeypatch.setenv("HL_COOKIE_DOMAIN", ".preprod.example.com")
    monkeypatch.setenv("HL_REMOTE_PORT", "9333")
    monkeypatch.setenv("HL_TARGET_HINT", "某其他系统")
    monkeypatch.setenv("HL_REFRESH_NAV_TIMEOUT", "3.5")
    monkeypatch.setenv("HL_REFRESH_LOAD_TIMEOUT", "4.5")
    monkeypatch.setenv("HL_REFRESH_HTTP_TIMEOUT", "5.5")
    from drissionpage_mcp.core import config
    importlib.reload(config)
    assert config.SCM_ADMIN_URL == "https://preprod.example.com/"
    assert config.COOKIE_DOMAIN == ".preprod.example.com"
    assert config.DEFAULT_PORT == 9333
    assert config.DEFAULT_TARGET_HINT == "某其他系统"
    assert config.REFRESH_NAV_TIMEOUT == 3.5
    assert config.REFRESH_LOAD_TIMEOUT == 4.5
    assert config.REFRESH_HTTP_TIMEOUT == 5.5
    # 还原默认值，避免污染后续测试
    for k in [
        "HL_SCM_URL",
        "HL_COOKIE_DOMAIN",
        "HL_REMOTE_PORT",
        "HL_TARGET_HINT",
        "HL_REFRESH_NAV_TIMEOUT",
        "HL_REFRESH_LOAD_TIMEOUT",
        "HL_REFRESH_HTTP_TIMEOUT",
    ]:
        monkeypatch.delenv(k, raising=False)
    importlib.reload(config)


def test_workspace_env_file_fills_missing_values_without_overriding_process_env(
    monkeypatch, tmp_path,
):
    from drissionpage_mcp.core import config

    assert config._load_env_file(tmp_path / "missing.env") is False

    missing_name = "DRISSIONPAGE_MCP_DOTENV_MISSING_TEST"
    existing_name = "DRISSIONPAGE_MCP_DOTENV_EXISTING_TEST"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"{missing_name}=from-file\n{existing_name}=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(missing_name, raising=False)
    monkeypatch.setenv(existing_name, "from-process")

    assert config._load_env_file(env_file) is True
    assert os.environ[missing_name] == "from-file"
    assert os.environ[existing_name] == "from-process"


def test_workspace_env_file_path_can_be_overridden(monkeypatch, tmp_path):
    from drissionpage_mcp.core import config

    env_file = tmp_path / "custom.env"
    variable = "DRISSIONPAGE_MCP_DOTENV_PATH_TEST"
    env_file.write_text(f"{variable}=loaded\n", encoding="utf-8")
    monkeypatch.setenv("DRISSIONPAGE_MCP_ENV_FILE", str(env_file))
    monkeypatch.delenv(variable, raising=False)

    importlib.reload(config)

    assert config.ENV_FILE == env_file.resolve()
    assert os.environ[variable] == "loaded"

    monkeypatch.delenv("DRISSIONPAGE_MCP_ENV_FILE", raising=False)
    monkeypatch.delenv(variable, raising=False)
    importlib.reload(config)
