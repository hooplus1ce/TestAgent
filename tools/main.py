# ty:ignore type
import uuid
import httpx
import ddddocr
from DrissionPage.common import tree
from DrissionPage import Chromium, ChromiumOptions
from DrissionPage._functions.elements import ChromiumElementsList
from DrissionPage.items import ChromiumFrame, ChromiumTab, ChromiumElement
from rich import print
import re
import time
from test import get_iframe_floats


def get_login_auth():
    # 1. 全局初始化 OCR 实例（切记不要放进循环或频繁调用的函数内部）
    ocr = ddddocr.DdddOcr(show_ad=False)
    # 2. 【关键】限定字符集为纯数字，排除字母干扰，大幅提升准确率
    ocr.set_ranges("0123456789")
    # 备选写法（使用内置索引 0 代表纯数字）：ocr.set_ranges(0)
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Referer": "https://demo19-scm.hoolinks.com/meLogin.do?",
        "Sec-Fetch-Dest": "image",
        "Origin": "https://demo19-scm.hoolinks.com",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    client = httpx.Client(base_url="https://demo19-scm.hoolinks.com")
    cookies = {"SESSION": str(uuid.uuid4())}
    params = {"key": "regValidateCode"}
    response = client.get(
        "/validateCode.json", headers=headers, cookies=cookies, params=params
    )
    img_data = response.read()

    # 5. 执行识别
    result = ocr.classification(img_data)
    print(f"纯数字识别结果: {result}")

    headers["Accept"] = "*/*"
    data = {"username": "nb_jb", "userpwd": "Ac123456", "vcode": result}
    response = client.post("/signin.html", headers=headers, cookies=cookies, data=data)
    print(response.json())
    client.close()
    return [{"name": k, "value": v} for k, v in response.cookies.items()]


def set_tab_cookies(tab: ChromiumTab):
    """
    设置浏览器的Cookies
    :param tab: ChromiumTab 实例
    """
    cookies = get_login_auth()
    tab.get(
        "https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/",
    )
    tab.set.cookies(cookies)
    tab.refresh()
    return tab


def expand_operation(iframe: ChromiumFrame) -> None:
    """
    展开操作，点击内联模式按钮

    参数：
    - iframe: 传入的 iframe 对象
    """
    if iframe.wait.eles_loaded(
        "t:i@@class=anticon anticon-bars", timeout=3, raise_err=False
    ):
        bars_button = iframe.ele("t:i@@class=anticon anticon-bars").parent(
            "t:button", index=1
        )

    while True:
        iframe.actions.move_to(bars_button, duration=0.3).click().wait(0.2).move_to(
            "t:li@@text()=内联模式", duration=0.3
        ).click()
        if "selected" in iframe.ele("t:li@@text()=内联模式").attr("class"):
            break

    if iframe.wait.eles_loaded("t:button@@tx():展开", timeout=0.5, raise_err=False):
        iframe.ele("t:button@@tx():展开", timeout=1).wait(0.2).click()
    else:
        ...
    iframe.ele("t:button@@tx():重置", timeout=3).wait(0.2).click()


def click_search_button(iframe: ChromiumFrame) -> None:
    """
    点击搜索按钮
    :param iframe: ChromiumFrame 实例
    """
    if iframe.wait.eles_loaded(
        "t:i@@class=anticon anticon-search", timeout=3, raise_err=False
    ):
        search_btn = iframe.ele('xpath://i[contains(@class,"anticon-search")]')
        iframe.actions.move_to(search_btn, duration=0).click()
    else:
        print("未成功点击查询按钮")


def get_active_iframe(tab: ChromiumTab) -> ChromiumFrame | None:
    """
    获取当前激活的 iframe
    :param tab: ChromiumTab 实例
    :return: ChromiumFrame 实例
    """
    is_tabpanel_displayed = tab.wait.eles_loaded(
        "t:div@@role=tabpanel@@aria-hidden=false", timeout=5, raise_err=False
    )
    if not is_tabpanel_displayed:
        return None
    active_tabpanel = tab.ele("t:div@@role=tabpanel@@aria-hidden=false", timeout=3)
    print("tabpanel已加载")

    iframe = active_tabpanel.get_frame("t:iframe", timeout=3)
    return iframe


def get_active_tab() -> ChromiumTab:
    print("正在进行浏览器初始化...")
    co = ChromiumOptions(ini_path="./configs/dp_configs.ini")
    browser = Chromium(addr_or_opts=co)
    print("浏览器初始化完成！")
    return browser.latest_tab


def main():
    tab = get_active_tab()

    # tab = set_tab_cookies(tab)
    # go_to_tab(tab, "托工缴回明细表")
    # tab.set.window.full()

    iframe = get_active_iframe(tab)
    iframe.set.show_trail(True)
    tab.set.show_trail(True)

    # ret = get_iframe_floats(only_visible=True, iframe_active=iframe)
    # print(ret)

    # tree(iframe.ele("t:div@@class:ant-modal-wrap"), text=True, show_css=True)
    # iframe.actions.move_to(
    #     (
    #         ret["floats"][-1]["buttons"][0]["rect"]["x"],
    #         ret["floats"][-1]["buttons"][0]["rect"]["y"],
    #     ),
    #     duration=2,
    # ).wait(1).hold().click()
    # iframe.actions.move_to((334.0, 314), duration=2).wait(0.15).click(times=1)
    try:
        t = iframe.wait.ele_displayed(
            "c:.ant-select-dropdown:not(.ant-select-dropdown-hidden)",
            timeout=2,
            raise_err=True,
        )
        print(t)
    except Exception as e:
        print(e)
    tab.set.show_trail(False)
    iframe.set.show_trail(False)
    exit()

    expand_operation(iframe)
    querry_filter_date(iframe, "销货时间", "2026-06-01~2026-06-15")
    click_search_button(iframe)
    is_loading_hidden = is_loading_complete(iframe)
    if is_loading_hidden:
        vtable = get_loaded_vtable(vtable, iframe)
        load_vtable_data(vtable, is_loading_hidden)
        vtable.drag_cells(1, 1, 10, 10)

    else:
        print.warning("表格数据加载超时，请检查网络连接或表格数据量太大")

    tab.set.window.max()


if __name__ == "__main__":
    main()
