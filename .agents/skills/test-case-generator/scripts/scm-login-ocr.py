#!/usr/bin/env python3
"""SCM 演示系统 OCR 免登脚本。

用途：首次登录或 session 完全失效时，通过 OCR 识别验证码 + HTTP 登录，
      获取认证 Cookie 三元组（cookie_token / UCTOKEN / SESSION），
      供 browser 标签页注入。

执行环境：omp 的 eval（持久 Python kernel）。
依赖：ddddocr、httpx（uv sync 已安装）。

返回的 auth_cookies 列表可直接传给 browser 的 CDP setCookie。
"""
import uuid
import json

import ddddocr
import httpx

# ===================== 配置（按实际项目修改） =====================
BASE_URL = "https://demo19-scm.hoolinks.com"
LOGIN_PAGE = "https://demo19-scm.hoolinks.com/meLogin.do?"
USERNAME = "Hooplus1ce"          # 演示环境凭证
USERPWD = "Ac123456"             # 演示环境凭证
COOKIE_DOMAIN = ".hoolinks.com"
# =================================================================


def get_login_auth():
    """OCR 识别验证码并登录，返回认证 Cookie 列表。

    返回 list[dict]，每项 {name, value}，可直接用于 CDP setCookie。
    """
    ocr = ddddocr.DdddOcr(show_ad=False)
    ocr.set_ranges("0123456789")  # 字符集限定纯数字

    client = httpx.Client(base_url=BASE_URL)
    cookies = {"SESSION": str(uuid.uuid4())}

    # 获取验证码图片
    resp = client.get(
        "/validateCode.json",
        params={"key": "regValidateCode"},
        headers={"Referer": LOGIN_PAGE},
        cookies=cookies,
    )
    vcode = ocr.classification(resp.read())

    # 登录
    data = {"username": USERNAME, "userpwd": USERPWD, "vcode": vcode}
    resp = client.post("/signin.html", data=data, cookies=cookies)

    # 返回 4 个 Cookie: cookie_token, UCTOKEN, SESSION, SYSSOURCE
    auth_cookies = [{"name": k, "value": v} for k, v in resp.cookies.items()]
    return auth_cookies


def get_login_auth_json():
    """同 get_login_auth，但返回 JSON 字符串（便于在 browser 工具中粘贴）。"""
    return json.dumps(get_login_auth(), ensure_ascii=False)


if __name__ == "__main__":
    cookies = get_login_auth()
    print("✅ 登录成功，获取到 %d 个 Cookie：" % len(cookies))
    for c in cookies:
        print("  - %s" % c["name"])
    print()
    print("将以下 JSON 传给 browser 的 CDP setCookie：")
    print(get_login_auth_json())
