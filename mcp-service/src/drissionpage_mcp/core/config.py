"""配置外置：从环境变量读取，保留默认值。

环境变量：
  HL_HOST_PREFIX   一键切换环境前缀（推荐），自动推导 HL_URL / HL_BASE_URL /
                   HL_LOGIN_PAGE / HL_COOKIE_DOMAIN / HL_ACCESS_DOMAIN
  HL_URL           SCM Admin URL（逐个覆盖，优先级更高）
  HL_BASE_URL      SCM 站点根 URL（OCR 登录用）
  HL_LOGIN_PAGE    登录页 URL（Referer）
  HL_USERNAME      登录用户名
  HL_USERPWD       登录密码
  HL_COOKIE_DOMAIN    cookie 域
  HL_ACCESS_DOMAIN    CDP 注入 cookie 的域
  HL_TARGET_HINT      connect 时选 tab 的标题提示
  HL_REMOTE_PORT      Chrome 远程调试端口
  HL_SHOT_DIR          screenshot 默认保存目录
  HL_CHROME_PATH       Chrome 浏览器路径（可选，自动探测）
  HL_EDGE_MODE         使用 Edge 浏览器（true/1 启用）
  HL_PROXY             代理地址，格式 'http://user:pass@ip:port'
  HL_HEADLESS          无头模式（true/1 启用）——CI/CD 无图形环境场景，跳过图形环境探测
  HL_REFRESH_NAV_TIMEOUT   会话刷新时进入 SCM 域的导航超时（秒）
  HL_REFRESH_LOAD_TIMEOUT  会话刷新后等待页面加载的超时（秒）
  HL_REFRESH_HTTP_TIMEOUT  OCR 登录 HTTP 请求超时（秒）
"""
import os
from pathlib import Path

from dotenv import load_dotenv


def _load_env_file(path: Path) -> bool:
    """Fill missing process variables from the optional workspace .env."""
    return load_dotenv(dotenv_path=path, override=False)


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SERVICE_ROOT = PACKAGE_ROOT.parents[1]
DEFAULT_WORKSPACE_ROOT = SERVICE_ROOT.parent
ENV_FILE = Path(
    os.environ.get(
        "DRISSIONPAGE_MCP_ENV_FILE",
        str(DEFAULT_WORKSPACE_ROOT / ".env"),
    )
).resolve()
_load_env_file(ENV_FILE)
WORKSPACE_ROOT = Path(
    os.environ.get("DRISSIONPAGE_MCP_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT))
).resolve()
PROJECT_ROOT = str(WORKSPACE_ROOT)
CONFIG_DIR = Path(
    os.environ.get(
        "DRISSIONPAGE_MCP_CONFIG_DIR",
        SERVICE_ROOT / "configs",
    )
).resolve()
DP_CONFIG_PATH = CONFIG_DIR / "dp_configs.ini"

# ---- 目标环境：通过 HL_HOST_PREFIX 一键派生，也支持逐个覆盖 ----
# 优先 HL_HOST_PREFIX 批量推导，逐个环境变量（HL_URL / HL_BASE_URL 等）可覆盖。
# 调用 set_target_env(prefix) 可运行时切换，无需重启 MCP 服务。
_HOST_PREFIX = os.environ.get("HL_HOST_PREFIX", "").strip()

def _derive_all(prefix: str) -> dict:
    """从 host 前缀推导 5 个关联配置。"""
    base = f"https://{prefix}.hoolinks.com"
    return {
        "HL_URL": f"{base}/scm-static/scm-admin/scm-admin/#/",
        "HL_BASE_URL": base,
        "HL_LOGIN_PAGE": f"{base}/meLogin.do",
        "HL_COOKIE_DOMAIN": f".{prefix}.hoolinks.com",
        "HL_ACCESS_DOMAIN": f".{prefix}.hoolinks.com",
    }

def _apply_prefix(prefix: str) -> None:
    """将派生值写入 os.environ（不覆盖已有显式环境变量）。"""
    for key, value in _derive_all(prefix).items():
        os.environ.setdefault(key, value)

if _HOST_PREFIX:
    _apply_prefix(_HOST_PREFIX)

# ---- 逐个变量取值（已被上面 setdefault 或显式 env 填充） ----
SCM_ADMIN_URL = os.environ.get("HL_URL", "")
COOKIE_DOMAIN = os.environ.get("HL_COOKIE_DOMAIN", "")
SCM_ACCESS_DOMAIN = os.environ.get("HL_ACCESS_DOMAIN", "")

# OCR 登录端点与凭据（demo 环境；凭据优先走环境变量）
SCM_BASE_URL = os.environ.get("HL_BASE_URL", "")
SCM_LOGIN_PAGE = os.environ.get("HL_LOGIN_PAGE", "")
SCM_USERNAME = os.environ.get("HL_USERNAME", "")
SCM_USERPWD = os.environ.get("HL_USERPWD", "")


def reload_target_config():
    """运行时重新读取 os.environ 中的目标地址配置（set_target_env 切换环境后调用）。"""
    global SCM_ADMIN_URL, COOKIE_DOMAIN, SCM_ACCESS_DOMAIN
    global SCM_BASE_URL, SCM_LOGIN_PAGE
    SCM_ADMIN_URL = os.environ.get("HL_URL", "")
    COOKIE_DOMAIN = os.environ.get("HL_COOKIE_DOMAIN", "")
    SCM_ACCESS_DOMAIN = os.environ.get("HL_ACCESS_DOMAIN", "")
    SCM_BASE_URL = os.environ.get("HL_BASE_URL", "")
    SCM_LOGIN_PAGE = os.environ.get("HL_LOGIN_PAGE", "")


def set_target_prefix(prefix: str):
    """切换目标环境前缀，写入 os.environ 并刷新模块级常量。"""
    os.environ["HL_HOST_PREFIX"] = prefix
    for key, value in _derive_all(prefix).items():
        os.environ[key] = value
    reload_target_config()

NEEDED_COOKIES = ["SESSION", "UCTOKEN", "cookie_token"]

DEFAULT_PORT = int(os.environ.get("HL_REMOTE_PORT", "9222"))
DEFAULT_TARGET_HINT = os.environ.get("HL_TARGET_HINT", "诺贝科技")


SHOT_DIR = os.environ.get(
    "HL_SHOT_DIR",
    str(SERVICE_ROOT / "resources"),
)

# ChromiumOptions 配置（4.2 新增）
CHROME_PATH = os.environ.get("HL_CHROME_PATH", "")
EDGE_MODE = os.environ.get("HL_EDGE_MODE", "").lower() in ("true", "1", "yes")
PROXY = os.environ.get("HL_PROXY", "")
DISABLE_PDF_PREVIEW = os.environ.get("HL_DISABLE_PDF_PREVIEW", "").lower() in ("true", "1", "yes")
REMOVE_TEST_TYPE = os.environ.get("HL_REMOVE_TEST_TYPE", "").lower() in ("true", "1", "yes")

# 无头模式：CI/CD 等无图形环境场景。启用时跳过 Linux 图形环境探测，并加 --no-sandbox。
HEADLESS = os.environ.get("HL_HEADLESS", "").lower() in ("true", "1", "yes")

# 会话刷新必须短超时、少重试，避免 MCP 单次工具调用被页面加载等待拖到外层超时。
REFRESH_NAV_TIMEOUT = float(os.environ.get("HL_REFRESH_NAV_TIMEOUT", "10"))
REFRESH_LOAD_TIMEOUT = float(os.environ.get("HL_REFRESH_LOAD_TIMEOUT", "15"))
REFRESH_HTTP_TIMEOUT = float(os.environ.get("HL_REFRESH_HTTP_TIMEOUT", "15"))


def make_chromium_options():
    """创建 ChromiumOptions（4.2 增强配置）。

    仅用于「启动新浏览器实例」的场景（如自启 Chrome）。connect() 走的是接管已运行
    Chrome 的路径（Chromium(port)），不会调用本函数，故 HL_CHROME_PATH / HL_EDGE_MODE /
    HL_PROXY / HL_DISABLE_PDF_PREVIEW / HL_REMOVE_TEST_TYPE 在接管模式下不生效——
    仅当未来新增 launch 工具时才生效。
    """
    from DrissionPage import ChromiumOptions
    co = ChromiumOptions()
    if CHROME_PATH:
        co.set_browser_path(CHROME_PATH)
    elif EDGE_MODE:
        co.set_browser_path(edge=True)
    if PROXY:
        co.set_proxy(PROXY)
    if DISABLE_PDF_PREVIEW:
        co.disable_pdf_preview()
    if REMOVE_TEST_TYPE:
        co.remove_test_type()
    co.auto_port()
    return co
