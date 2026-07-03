"""配置外置：从环境变量读取，保留默认值。

环境变量：
  HL_SCM_URL          SCM Admin URL
  HL_SCM_BASE_URL      SCM 站点根 URL（OCR 登录用）
  HL_SCM_LOGIN_PAGE    登录页 URL（Referer）
  HL_SCM_USERNAME      登录用户名
  HL_SCM_USERPWD       登录密码
  HL_COOKIE_DOMAIN    cookie 域
  HL_ACCESS_DOMAIN    CDP 注入 cookie 的域
  HL_TARGET_HINT      connect 时选 tab 的标题提示
  HL_REMOTE_PORT      Chrome 远程调试端口
  HL_SHOT_DIR          screenshot 默认保存目录
  HL_CHROME_PATH       Chrome 浏览器路径（可选，自动探测）
  HL_EDGE_MODE         使用 Edge 浏览器（true/1 启用）
  HL_PROXY             代理地址，格式 'http://user:pass@ip:port'
  HL_HEADLESS          无头模式（true/1 启用）——CI/CD 无图形环境场景，跳过图形环境探测
"""
import os

SCM_ADMIN_URL = os.environ.get("HL_SCM_URL", "https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/")
COOKIE_DOMAIN = os.environ.get("HL_COOKIE_DOMAIN", ".demo19-scm.hoolinks.com")
SCM_ACCESS_DOMAIN = os.environ.get("HL_ACCESS_DOMAIN", ".hoolinks.com")

# OCR 登录端点与凭据（demo 环境；凭据优先走环境变量）
SCM_BASE_URL = os.environ.get("HL_SCM_BASE_URL", "https://demo19-scm.hoolinks.com")
SCM_LOGIN_PAGE = os.environ.get("HL_SCM_LOGIN_PAGE", "https://demo19-scm.hoolinks.com/meLogin.do?")
SCM_USERNAME = os.environ.get("HL_SCM_USERNAME", "Hooplus1ce")
SCM_USERPWD = os.environ.get("HL_SCM_USERPWD", "Ac123456")

NEEDED_COOKIES = ["SESSION", "UCTOKEN", "cookie_token"]

DEFAULT_PORT = int(os.environ.get("HL_REMOTE_PORT", "9222"))
DEFAULT_TARGET_HINT = os.environ.get("HL_TARGET_HINT", "诺贝科技")

ACTIVE_FRAME_LOC = 'c:[role="tabpanel"][aria-hidden="false"] iframe'

SHOT_DIR = os.environ.get(
    "HL_SHOT_DIR",
    os.path.join(os.path.expanduser("~"), ".drission-ui-shots"),
)

# ChromiumOptions 配置（4.2 新增）
CHROME_PATH = os.environ.get("HL_CHROME_PATH", "")
EDGE_MODE = os.environ.get("HL_EDGE_MODE", "").lower() in ("true", "1", "yes")
PROXY = os.environ.get("HL_PROXY", "")
DISABLE_PDF_PREVIEW = os.environ.get("HL_DISABLE_PDF_PREVIEW", "").lower() in ("true", "1", "yes")
REMOVE_TEST_TYPE = os.environ.get("HL_REMOVE_TEST_TYPE", "").lower() in ("true", "1", "yes")

# 无头模式：CI/CD 等无图形环境场景。启用时跳过 Linux 图形环境探测，并加 --no-sandbox。
HEADLESS = os.environ.get("HL_HEADLESS", "").lower() in ("true", "1", "yes")


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
