"""config.py 测试：环境变量读取与默认值。"""
import importlib
import os


def test_defaults():
    import config
    assert config.SCM_ADMIN_URL.startswith("https://")
    assert config.COOKIE_DOMAIN.startswith(".")
    assert config.SCM_ACCESS_DOMAIN.startswith(".")
    assert config.NEEDED_COOKIES == ["SESSION", "UCTOKEN", "cookie_token"]
    assert config.DEFAULT_PORT == 9222
    assert config.DEFAULT_TARGET_HINT == "诺贝科技"
    assert "drission-ui-shots" in config.SHOT_DIR


def test_env_override(monkeypatch):
    monkeypatch.setenv("HL_SCM_URL", "https://preprod.example.com/")
    monkeypatch.setenv("HL_COOKIE_DOMAIN", ".preprod.example.com")
    monkeypatch.setenv("HL_REMOTE_PORT", "9333")
    monkeypatch.setenv("HL_TARGET_HINT", "某其他系统")
    import config
    importlib.reload(config)
    assert config.SCM_ADMIN_URL == "https://preprod.example.com/"
    assert config.COOKIE_DOMAIN == ".preprod.example.com"
    assert config.DEFAULT_PORT == 9333
    assert config.DEFAULT_TARGET_HINT == "某其他系统"
    # 还原默认值，避免污染后续测试
    for k in ["HL_SCM_URL", "HL_COOKIE_DOMAIN", "HL_REMOTE_PORT", "HL_TARGET_HINT"]:
        monkeypatch.delenv(k, raising=False)
    importlib.reload(config)
