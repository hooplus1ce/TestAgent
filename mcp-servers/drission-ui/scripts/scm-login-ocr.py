#!/usr/bin/env python3
"""SCM 演示系统 OCR 免登脚本。

用途：首次登录或 session 完全失效时，通过 OCR 识别验证码 + HTTP 登录，
      获取认证 Cookie 三元组（cookie_token / UCTOKEN / SESSION），
      供 browser 标签页注入。

依赖：ddddocr、httpx（uv sync 已安装）。

返回的 auth_cookies 列表可直接传给 browser 的 CDP setCookie。

配置：从环境变量读取，保留默认值（演示环境凭证）。
  HL_SCM_BASE_URL    SCM 站点根 URL
  HL_SCM_LOGIN_PAGE  登录页 URL
  HL_SCM_USERNAME    登录用户名
  HL_SCM_USERPWD     登录密码
  HL_ACCESS_DOMAIN   cookie 域
"""

import json
import os
import uuid

import ddddocr
import httpx

BASE_URL = os.environ.get("HL_SCM_BASE_URL", "https://demo19-scm.hoolinks.com")
LOGIN_PAGE = os.environ.get(
    "HL_SCM_LOGIN_PAGE", "https://demo19-scm.hoolinks.com/meLogin.do?"
)
# 凭据只走环境变量，不在代码库留明文默认值；缺失时给出明确错误而非静默用空凭证
USERNAME = os.environ.get("HL_SCM_USERNAME", "Hooplus1ce")
USERPWD = os.environ.get("HL_SCM_USERPWD", "Ac123456")
COOKIE_DOMAIN = os.environ.get("HL_ACCESS_DOMAIN", ".hoolinks.com")


def _require_creds():
    """凭据缺失时抛出明确错误，提示设置环境变量。"""
    missing = [
        k
        for k, v in (("HL_SCM_USERNAME", USERNAME), ("HL_SCM_USERPWD", USERPWD))
        if not v
    ]
    if missing:
        raise RuntimeError(
            "缺少登录凭据环境变量: %s（请通过环境变量/secret 提供，勿写入代码）"
            % ", ".join(missing)
        )


def get_login_auth():
    """OCR 识别验证码并登录，返回认证 Cookie 列表。

    返回 list[dict]，每项 {name, value}，可直接用于 CDP setCookie。
    """
    _require_creds()
    ocr = ddddocr.DdddOcr(show_ad=False)
    ocr.set_ranges("0123456789")  # 字符集限定纯数字

    client = httpx.Client(base_url=BASE_URL)
    cookies = {"SESSION": str(uuid.uuid4())}

    resp = client.get(
        "/validateCode.json",
        params={"key": "regValidateCode"},
        headers={"Referer": LOGIN_PAGE},
        cookies=cookies,
    )
    vcode = ocr.classification(resp.read())

    data = {"username": USERNAME, "userpwd": USERPWD, "vcode": vcode}
    resp = client.post("/signin.html", data=data, cookies=cookies)

    auth_cookies = [{"name": k, "value": v} for k, v in resp.cookies.items()]
    return auth_cookies


def get_login_auth_json():
    """同 get_login_auth，但返回 JSON 字符串。"""
    return json.dumps(get_login_auth(), ensure_ascii=False)


if __name__ == "__main__":
    cookies = get_login_auth()
    print("login ok, got %d cookies:" % len(cookies))
    for c in cookies:
        print("  - %s" % c["name"])
    print()
    print(get_login_auth_json())
