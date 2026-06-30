# ty:ignore type
import random
import uuid

import ddddocr
import httpx
from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.common import tree
from DrissionPage.items import ChromiumElement, ChromiumFrame, ChromiumTab
from loguru import logger
from rich import print

from fast_vtable_helper import FastVTableHelper


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
    data = {"username": "Hooplus1ce", "userpwd": "Ac123456", "vcode": result}
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
        "t:i@@class=anticon anticon-bars", timeout=5, raise_err=False
    ):
        bars_button = iframe.ele("xpath://i[@class='anticon anticon-bars']").parent(
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


def filter_operation(
    iframe: ChromiumFrame, filter_map: dict, filter_list: list
) -> None:
    """
    筛选操作
    :param iframe: ChromiumFrame 实例
    :param filter_map: 筛选条件字典
    :param filter_list: 筛选条件列表
    """
    for filter_name, filter_code, filter_type in filter_list:
        filter_row: ChromiumElement = iframe.ele(
            f'xpath://div[contains(@class,"ant-select-selection-selected-value")'
            f' and text()="{filter_name}"]'
            '/ancestor::div[contains(@class,"ant-row")][1]'
        )
        operator_select: ChromiumElement = filter_row.ele(
            'xpath:.//div[contains(@class,"ant-col-8")][2]'
            '//div[contains(@class,"ant-select-selection")]',
            timeout=10,
        )
        if filter_type == "enum":
            operator_select.click(by_js=True)
            iframe.actions.move_to(
                f'xpath:.//li[text()="{random.choice(list(filter_map[filter_name].keys()))}"]',
                duration=0,
            ).wait(0.2).click()


def get_active_iframe(tab: ChromiumTab) -> ChromiumFrame:
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
    logger.info("tabpanel已加载")

    iframe = active_tabpanel.get_frame("t:iframe", timeout=3)
    return iframe


def get_loaded_vtable(
    vtable: FastVTableHelper, iframe: ChromiumFrame
) -> FastVTableHelper:
    """
    获取加载完成的 vtable 实例
    :param iframe: ChromiumFrame 实例
    :return: FastVTableHelper 实例
    """

    if not iframe:
        logger.warning("iframe 加载失败")
        return None

    logger.info("VTable正在加载中...")
    is_vtable_displayed = iframe.wait.eles_loaded(
        ".vtable", timeout=20, raise_err=False
    )

    if is_vtable_displayed:
        logger.info("VTable已成功加载！")
        result = vtable.vtable_connection(iframe)
        logger.info("VTable已挂载进【window._vtable】中") if result else logger.info(
            "VTable挂载失败，请检查"
        )
        return vtable
    else:
        logger.warning("未找到 vtable 元素")
        return None


def load_vtable_data(vtable: FastVTableHelper, is_loading_hidden: bool) -> bool:
    """
    获取加载完成的 vtable 数据
    :param iframe: ChromiumFrame 实例
    :return: ChromiumFrame 实例
    """

    if is_loading_hidden:
        ret = vtable.get_vtable_overview()
        logger.info(
            f"VTable 极速连接成功！当前表格行数: {ret['rowCount']}, 列数: {ret['colCount']}, 数据条数: {ret['dataCount']}"
        )
        return True
    else:
        logger.warning("表格数据加载超时，请检查网络连接或表格数据量太大")
        return False


def is_loading_complete(iframe: ChromiumFrame) -> bool:
    """
    判断加载是否完成
    :param iframe: ChromiumFrame 实例
    :return: bool
    """
    iframe.wait.eles_loaded(
        "xpath://div[@class='page-content']//div[contains(@class, 'vtable-loading')]",
        timeout=3,
        raise_err=False,
    )

    is_loading_hidden = iframe.wait.ele_deleted(
        "xpath://div[@class='page-content']//div[contains(@class, 'vtable-loading')]",
        timeout=20,
        raise_err=False,
    )
    return is_loading_hidden


def get_active_tab() -> ChromiumTab:
    logger.info("正在进行浏览器初始化...")
    co = ChromiumOptions(ini_path="./dp_configs.ini")
    browser = Chromium(addr_or_opts=co)
    logger.info("浏览器初始化完成！")
    return browser.latest_tab


def go_to_tab(tab: ChromiumTab, menu_name: str) -> None:
    """
    切换标签页
    :param tab: ChromiumTab 实例
    :param menu_name: 标签页名称
    """
    tab.wait.doc_loaded(timeout=30, raise_err=False)
    tab.set.show_trail()
    if tab.wait.ele_displayed(
        f"t:span@@class=ant-dropdown-trigger@@text()={menu_name}",
        timeout=2,
        raise_err=False,
    ):
        close_button: ChromiumElement = tab.ele(
            f"t:span@@class=ant-dropdown-trigger@@text()={menu_name}"
        ).next(".$anticon-close", index=1)
        tab.actions.move_to(close_button, duration=0).wait(0.2).click(times=2)

    menu_pos: ChromiumElement = tab.ele("t:div@@class=ant-layout-header").ele(
        "t:div@@class=ant-select-selection__rendered"
    )
    tab.actions.move_to(menu_pos.east(index=1), duration=0).click().wait(0.2).click(
        menu_pos
    ).type(menu_name, 0.2).click(f"t:li@@text()={menu_name}")
    tab.set.show_trail(False)


def querry_filter_date(iframe: ChromiumFrame, filter_name: str, date_str: str):
    """
    筛选日期
    :param iframe: ChromiumFrame 实例
    :param filter_name: 筛选条件名称
    :param date_str: 日期字符串,格式:YYYY-MM-DD~YYYY-MM-DD
    """
    start_date, end_date = date_str.split("~")
    if iframe.wait.ele_displayed(
        f"t:div@@class=ant-col-8@@tx():{filter_name}", timeout=3, raise_err=False
    ):
        date_input_ele = iframe.ele(f"t:div@@class=ant-col-8@@tx():{filter_name}").next(
            index=2
        )
        iframe.actions.move_to(date_input_ele, duration=0).wait(0.1).click()

        if iframe.wait.eles_loaded(
            "t:div@@class=ant-calendar-panel", timeout=3, raise_err=False
        ):
            calendar_panel: ChromiumElement = iframe.ele(
                "t:div@@class=ant-calendar-panel"
            )
            calendar_panel.ele(
                "t:input@@placeholder=结束日期", timeout=20
            ).click().wait(0.2).input(end_date, clear=True).wait(0.2)
            calendar_panel.ele(
                "t:input@@placeholder=开始日期", timeout=20
            ).click().wait(0.2).input(start_date, clear=True).wait(0.2)
        else:
            print("未找到日历面板")
    else:
        print("未找到筛选条件行")


def main():
    tab = get_active_tab()
    vtable = FastVTableHelper()

    # tab = set_tab_cookies(tab)
    # go_to_tab(tab, "销货统计表")
    # tab.set.window.full()
    iframe = get_active_iframe(tab)
    iframe.set.show_trail(True)
    tree(iframe.ele(".:legions-pro-quick-filter"))
    # iframe.actions.move_to((522, 539.5), duration=1).click().wait(0.15).click()

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
        logger.warning("表格数据加载超时，请检查网络连接或表格数据量太大")

    tab.set.window.max()


if __name__ == "__main__":
    main()
