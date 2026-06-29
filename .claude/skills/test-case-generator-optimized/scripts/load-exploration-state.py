#!/usr/bin/env python3
"""断点续传：读写探索进度文件。

在 eval kernel 中执行。Phase 3 每完成一个区域调用 save_state() 追加；
重新启动技能时调用 load_state() 读取上次进度并向用户汇报。
进度文件结构见 assets/exploration-state.schema.json。
"""
import json
import os
from datetime import datetime

DEFAULT_STATE_PATH = os.path.join(".", ".exploration-state.json")


def load_state(path: str = DEFAULT_STATE_PATH):
    """读取进度文件。返回 dict；文件不存在返回 None。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(module: str, covered_areas: list, pending_areas: list,
               domain: str = "", vtable_scanned: bool = False,
               derived_count: int = 0, path: str = DEFAULT_STATE_PATH):
    """覆盖写入进度文件。建议每次只 append 一个 area 到 covered_areas 后调用。"""
    state = {
        "module": module,
        "domain": domain,
        "vtable_columns_scanned": vtable_scanned,
        "covered_areas": covered_areas,
        "pending_areas": pending_areas,
        "derived_cases_count": derived_count,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


if __name__ == "__main__":
    # 用法演示
    state = load_state()
    if state is None:
        print("（无历史进度）首次启动，建议从 Phase 1 开始。")
    else:
        print(f"模块: {state['module']}")
        print(f"已覆盖: {state['covered_areas']}")
        print(f"待探索: {state['pending_areas']}")
        print(f"最后更新: {state['last_updated']}")
