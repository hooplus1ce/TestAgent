"""filter_area.py tests for SCM filter interactions."""
from unittest.mock import patch


class _FakeTextElement:
    def __init__(self, text):
        self.text = text


class _FakeInput:
    def __init__(self, value=""):
        self._value = value
        self.clicked = False

    def click(self):
        self.clicked = True

    def attr(self, name):
        return self._value if name == "value" else ""


class _FakeDateCell:
    def __init__(self):
        self.click_count = 0

    def click(self):
        self.click_count += 1


class _FakeWait:
    def ele_displayed(self, *args, **kwargs):
        return True


class _FakeCalendar:
    def __init__(self, cell):
        self.cell = cell
        self.wait = _FakeWait()

    def ele(self, locator, timeout=None):
        if locator == "c:.ant-calendar-range-left":
            return self
        if locator == "c:.ant-calendar-year-select":
            return _FakeTextElement("2026年")
        if locator == "c:.ant-calendar-month-select":
            return _FakeTextElement("7月")
        if locator == 'c:td[title="2026/07/08"] .ant-calendar-date':
            return self.cell
        return None


class _FakeRow:
    text = "创建日期 2026-07-02 ~ 2026-07-09"

    def __init__(self):
        self.picker = object()
        self.input = _FakeInput()
        self.inputs = [_FakeInput("2026-07-08"), _FakeInput("2026-07-08")]

    def ele(self, locator, timeout=None):
        if locator == "c:.ant-calendar-picker":
            return self.picker
        if locator == "c:.ant-calendar-picker-input":
            return self.input
        return None

    def eles(self, locator):
        if locator == "c:input.ant-calendar-range-picker-input":
            return self.inputs
        return []


class _FakeFrame:
    def __init__(self, row, calendar):
        self.row = row
        self.calendar = calendar
        self.eles_calls = []
        self.ele_calls = []

    def eles(self, locator, timeout=None):
        self.eles_calls.append(locator)
        if locator == "c:.legions-pro-query-item":
            return [self.row]
        if locator == "c:.legions-pro-quick-filter .ant-row":
            return []
        return []

    def ele(self, locator, timeout=None):
        self.ele_calls.append(locator)
        if locator == "c:.ant-calendar":
            return self.calendar
        return None


def test_select_date_range_finds_query_item_and_calendar_inside_iframe():
    import filter_area

    row = _FakeRow()
    cell = _FakeDateCell()
    frame = _FakeFrame(row, _FakeCalendar(cell))

    with patch.object(filter_area, "expand_filter_area", return_value={"ok": True}), \
         patch.object(filter_area.browser_session, "get_active_frame", return_value=frame), \
         patch.object(filter_area.browser_session, "get_tab", side_effect=AssertionError("calendar must be found in iframe")):
        result = filter_area.select_date_range("创建日期", "2026/07/08", "2026/07/08", tab=object())

    assert result == {"ok": True, "startValue": "2026-07-08", "endValue": "2026-07-08"}
    assert "c:.legions-pro-query-item" in frame.eles_calls
    assert "c:.ant-calendar" in frame.ele_calls
    assert row.input.clicked is True
    assert cell.click_count == 2
