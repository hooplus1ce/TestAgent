"""配置外置：从环境变量读取，保留默认值。

环境变量：
  HL_SCM_URL          SCM Admin URL
  HL_COOKIE_DOMAIN    cookie 域
  HL_ACCESS_DOMAIN    CDP 注入 cookie 的域
  HL_TARGET_HINT      connect 时选 tab 的标题提示
  HL_REMOTE_PORT      Chrome 远程调试端口
  HL_SHOT_DIR          screenshot 默认保存目录
"""
import os

SCM_ADMIN_URL = os.environ.get("HL_SCM_URL", "https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/")
COOKIE_DOMAIN = os.environ.get("HL_COOKIE_DOMAIN", ".demo19-scm.hoolinks.com")
SCM_ACCESS_DOMAIN = os.environ.get("HL_ACCESS_DOMAIN", ".hoolinks.com")

NEEDED_COOKIES = ["SESSION", "UCTOKEN", "cookie_token"]

DEFAULT_PORT = int(os.environ.get("HL_REMOTE_PORT", "9222"))
DEFAULT_TARGET_HINT = os.environ.get("HL_TARGET_HINT", "诺贝科技")

ACTIVE_FRAME_LOC = 'css:[role="tabpanel"][aria-hidden="false"] iframe'

SHOT_DIR = os.environ.get(
    "HL_SHOT_DIR",
    os.path.join(os.path.expanduser("~"), ".drission-ui-shots"),
)
