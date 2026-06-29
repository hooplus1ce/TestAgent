"""vtable.py 测试：_js_args 参数序列化（纯逻辑，不依赖浏览器）。"""
import json


def test_js_args_int():
    import vtable
    s = vtable._js_args(1, 2)
    assert s == "[1, 2]"


def test_js_args_string_and_bool():
    import vtable
    s = vtable._js_args("制令单号", True)
    assert json.loads(s) == ["制令单号", True]


def test_js_args_empty():
    import vtable
    assert vtable._js_args() == "[]"


def test_js_args_negative_and_float():
    import vtable
    s = vtable._js_args(-1, 3.14)
    assert json.loads(s) == [-1, 3.14]


def test_js_args_special_chars():
    import vtable
    s = vtable._js_args("含'引号\"和\\斜杠")
    # 确保序列化后是合法 JSON
    parsed = json.loads(s)
    assert parsed == ["含'引号\"和\\斜杠"]
