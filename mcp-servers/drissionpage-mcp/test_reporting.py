"""Markdown reporting and baseline comparison for test execution results."""
from __future__ import annotations

import json
import math
from collections import Counter
from statistics import mean


_COVERED_STATES = {"已验证", "已覆盖", "verified", "covered", "passed", "pass", "true"}


def _number(value):
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _coverage_metric(key: str, value: dict) -> dict | None:
    covered = next((_number(value.get(name)) for name in
                    ("covered", "covered_count", "verified", "verified_count", "passed")
                    if _number(value.get(name)) is not None), None)
    total = next((_number(value.get(name)) for name in ("total", "total_count", "count")
                  if _number(value.get(name)) is not None), None)
    rate = next((_number(value.get(name)) for name in
                ("rate", "coverage_rate", "percentage", "percent")
                if _number(value.get(name)) is not None), None)
    if covered is not None and total is not None:
        rate = covered / total * 100 if total else 0.0
    elif rate is not None and rate <= 1:
        rate *= 100
    if covered is None and total is None and rate is None:
        return None
    return {
        "key": str(key),
        "label": str(value.get("label") or value.get("name") or key),
        "covered": covered,
        "total": total,
        "rate": rate,
    }


def _summary_rows(summary) -> list[dict]:
    if isinstance(summary, list):
        rows = []
        for index, item in enumerate(summary, start=1):
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("dimension") or item.get("name") or "coverage_%d" % index
            metric = _coverage_metric(str(key), item)
            if metric:
                rows.append(metric)
        return rows
    if not isinstance(summary, dict):
        return []
    rows = []
    for key, value in summary.items():
        if isinstance(value, dict):
            metric = _coverage_metric(str(key), value)
            if metric:
                rows.append(metric)
    if rows:
        return rows
    own_metric = _coverage_metric("coverage", summary)
    return [own_metric] if own_metric else []


def _matrix_rows(matrix: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for row in matrix:
        if not isinstance(row, dict):
            continue
        key = str(row.get("dimension") or row.get("coverage_type") or row.get("type") or "scenarios")
        label = str(row.get("dimension_label") or (
            "已验证覆盖场景" if key == "scenarios" else key
        ))
        group = groups.setdefault(key, {"key": key, "label": label, "covered": 0.0, "total": 0.0})
        group["total"] += 1
        status = str(row.get("status", "")).strip().lower()
        if row.get("covered") is True or status in {item.lower() for item in _COVERED_STATES}:
            group["covered"] += 1
    for group in groups.values():
        group["rate"] = group["covered"] / group["total"] * 100 if group["total"] else 0.0
    return list(groups.values())


def _coverage_rows(payload) -> list[dict]:
    """Normalize authoritative coverage summaries, with legacy matrix fallback."""
    if isinstance(payload, list):
        return _matrix_rows(payload)
    if not isinstance(payload, dict):
        return []
    if payload.get("coverage_summary") is not None:
        return _summary_rows(payload.get("coverage_summary"))
    if payload.get("coverage") is not None:
        nested = _coverage_rows(payload.get("coverage"))
        if nested:
            return nested
    if isinstance(payload.get("coverage_matrix"), list):
        return _matrix_rows(payload["coverage_matrix"])
    return []


def _coverage_changed(before: dict, after: dict) -> bool:
    for key in ("covered", "total", "rate"):
        old, new = before.get(key), after.get(key)
        if old is None and new is None:
            continue
        if old is None or new is None or abs(float(old) - float(new)) > 0.001:
            return True
    return False


def compare_regression(current: dict, baseline: dict | None) -> dict:
    """Compare case membership, status, material timing, and coverage with a baseline."""
    if not baseline:
        return {"ok": True, "baseline_available": False, "changes": []}
    current_results = {
        item.get("case_id"): item for item in current.get("results", [])
        if isinstance(item, dict) and item.get("case_id") is not None
    }
    previous_results = {
        item.get("case_id"): item for item in baseline.get("results", [])
        if isinstance(item, dict) and item.get("case_id") is not None
    }
    changes = []
    for case_id, item in current_results.items():
        old = previous_results.get(case_id)
        if old is None:
            changes.append({
                "case_id": case_id, "before": None,
                "after": item.get("status"), "kind": "added",
            })
            continue
        if old.get("status") != item.get("status"):
            changes.append({
                "case_id": case_id, "before": old.get("status"),
                "after": item.get("status"), "kind": "status",
            })
        old_ms, new_ms = _number(old.get("elapsed_ms")), _number(item.get("elapsed_ms"))
        if old_ms is not None and new_ms is not None and old_ms > 0:
            increase = (new_ms - old_ms) / old_ms
            if increase >= 0.2:
                changes.append({
                    "case_id": case_id, "before_ms": old_ms, "after_ms": new_ms,
                    "change_percent": round(increase * 100, 2), "kind": "performance_regression",
                })
            elif increase <= -0.2:
                changes.append({
                    "case_id": case_id, "before_ms": old_ms, "after_ms": new_ms,
                    "change_percent": round(increase * 100, 2), "kind": "performance_improvement",
                })
    for case_id, old in previous_results.items():
        if case_id not in current_results:
            changes.append({
                "case_id": case_id, "before": old.get("status"),
                "after": None, "kind": "removed",
            })

    current_coverage = {row["key"]: row for row in _coverage_rows(current)}
    previous_coverage = {row["key"]: row for row in _coverage_rows(baseline)}
    for key in sorted(set(current_coverage) | set(previous_coverage)):
        old, new = previous_coverage.get(key), current_coverage.get(key)
        if old is None or new is None or _coverage_changed(old, new):
            changes.append({
                "kind": "coverage", "dimension": key,
                "before": old, "after": new,
            })

    summary = {kind: 0 for kind in (
        "added", "removed", "status", "performance_regression",
        "performance_improvement", "coverage",
    )}
    for change in changes:
        summary[change["kind"]] = summary.get(change["kind"], 0) + 1
    return {"ok": True, "baseline_available": True, "changes": changes, "summary": summary}


def _percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _display(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _cell(value) -> str:
    return _display(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _step_label(step) -> str:
    if not isinstance(step, dict):
        return _cell(step)
    phase = step.get("phase", "")
    index = step.get("index", step.get("phase_index", ""))
    action = step.get("action", "")
    return "%s#%s %s" % (phase, index, action)


def _screenshot(ref: dict):
    if not isinstance(ref, dict):
        return None
    return ref.get("screenshot") or (ref.get("artifacts") or {}).get("screenshot")


def _markdown_target(value) -> str:
    target = str(value or "").replace("\\", "/")
    if len(target) >= 3 and target[1:3] == ":/":
        target = "/" + target
    return "<%s>" % target if any(char.isspace() for char in target) else target


def _evidence_text(item: dict, max_refs: int = 3) -> str:
    refs = []
    for ref in item.get("evidence_refs", []):
        if not isinstance(ref, dict):
            continue
        label = "%s#%s" % (ref.get("flow_id", ""), ref.get("sequence", ""))
        screenshot = _screenshot(ref)
        refs.append("[%s](%s)" % (label, _markdown_target(screenshot)) if screenshot else label)
    if len(refs) > max_refs:
        kept = [refs[0], refs[len(refs) // 2], refs[-1]]
        return "; ".join(kept) + "（其余 %d 条见执行 JSON）" % (len(refs) - len(kept))
    return "; ".join(refs) or "-"


def _defect_evidence(defect: dict) -> str:
    refs = defect.get("evidence_refs") or []
    return _evidence_text({"evidence_refs": refs})


def _coverage_value(row: dict) -> str:
    covered, total = row.get("covered"), row.get("total")
    if covered is None or total is None:
        return "-"
    return "%g / %g" % (covered, total)


def _coverage_rate(row: dict | None) -> str:
    if not row or row.get("rate") is None:
        return "-"
    return "%.1f%%" % row["rate"]


def _coverage_matrix(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    matrix = payload.get("coverage_matrix")
    return [item for item in matrix if isinstance(item, dict)] if isinstance(matrix, list) else []


def _regression_values(change: dict) -> tuple[str, str, str]:
    kind = change.get("kind", "")
    if kind.startswith("performance_"):
        return change.get("case_id", ""), "%g ms" % change.get("before_ms", 0), "%g ms" % change.get("after_ms", 0)
    if kind == "coverage":
        return change.get("dimension", ""), _coverage_rate(change.get("before")), _coverage_rate(change.get("after"))
    return change.get("case_id", ""), _display(change.get("before")), _display(change.get("after"))


def render_markdown(execution: dict, coverage_matrix: list[dict] | dict | None = None,
                    regression: dict | None = None) -> str:
    """Render execution, authoritative coverage, defects, evidence, and regression."""
    results = [item for item in execution.get("results", []) if isinstance(item, dict)]
    counts = {state: sum(1 for item in results if item.get("status") == state)
              for state in ("passed", "failed", "xfailed", "skipped")}
    total = len(results)
    executed = counts["passed"] + counts["failed"] + counts["xfailed"]
    business_results = counts["passed"] + counts["failed"]
    unknown = total - executed - counts["skipped"]
    case_elapsed = [_number(item.get("elapsed_ms")) for item in results]
    case_elapsed = [value for value in case_elapsed if value is not None]
    step_timings = []
    for item in results:
        for step in item.get("steps", []):
            if not isinstance(step, dict):
                continue
            elapsed = _number(step.get("elapsed_ms"))
            if elapsed is not None:
                step_timings.append((elapsed, item.get("case_id", ""), step))
    step_values = [item[0] for item in step_timings]
    coverage_rows = _coverage_rows(coverage_matrix)
    screenshot_count = sum(
        1 for item in results for ref in item.get("evidence_refs", [])
        if isinstance(ref, dict) and _screenshot(ref)
    )

    lines = [
        "# 自动化测试报告", "", "## 执行环境", "",
        "| 项目 | 内容 |", "|---|---|",
        "| 模块 | %s |" % _cell((execution.get("module_info") or {}).get("module_name", "-")),
        "| 开始时间 | %s |" % _cell(execution.get("started_at", "-")),
        "| 结束时间 | %s |" % _cell(execution.get("finished_at", "-")),
        "| 会话检查 | %s |" % ("通过" if (execution.get("ready_gate") or {}).get("ok") else "未记录"),
        "| 活动页面 | %s |" % _cell((((execution.get("ready_gate") or {}).get("frame") or {}).get("url", "-"))),
        "| 补充执行文件 | %d |" % len(execution.get("supplemental_execution_files", [])),
        "", "## 执行摘要", "",
        "| 指标 | 数值 |", "|---|---:|",
        "| 计划用例 | %d |" % total,
        "| 已执行 | %d |" % executed,
        "| 通过 | %d |" % counts["passed"],
        "| 失败 | %d |" % counts["failed"],
        "| 已知缺陷复现 | %d |" % counts["xfailed"],
        "| 跳过 | %d |" % counts["skipped"],
        "| 未知状态 | %d |" % unknown,
        "| 业务通过率 | %.1f%% |" % ((counts["passed"] / business_results * 100) if business_results else 0),
        "| 执行完成率 | %.1f%% |" % ((executed / total * 100) if total else 0),
        "| 平均用例耗时 | %.2f ms |" % (mean(case_elapsed) if case_elapsed else 0),
        "| 执行期截图证据 | %d |" % screenshot_count,
        "", "## 覆盖率", "",
    ]
    if coverage_rows:
        lines.extend(["| 覆盖维度 | 已覆盖 / 总数 | 覆盖率 |", "|---|---:|---:|"])
        for row in coverage_rows:
            lines.append("| %s | %s | %s |" % (
                _cell(row.get("label")), _coverage_value(row), _coverage_rate(row),
            ))
    else:
        lines.append("- 未提供可验证的覆盖统计。")

    matrix = _coverage_matrix(coverage_matrix)
    gaps = [item for item in matrix if item.get("status") != "已验证"]
    lines.extend(["", "### 覆盖缺口", ""])
    if gaps:
        grouped = Counter((item.get("status", "待验证"), item.get("risk") or "未分类") for item in gaps)
        lines.extend(["| 状态 | 风险维度 | 场景数 |", "|---|---|---:|"])
        for (status, risk), count in sorted(grouped.items(), key=lambda item: (-item[1], str(item[0]))):
            lines.append("| %s | %s | %d |" % (_cell(status), _cell(risk), count))
        lines.extend(["", "优先缺口：", ""])
        for item in gaps[:10]:
            lines.append("- [%s] %s / %s：%s" % (
                _cell(item.get("status", "待验证")), _cell(item.get("function", "")),
                _cell(item.get("risk", "")), _cell(item.get("scenario", "")),
            ))
    else:
        lines.append("- 当前覆盖矩阵无未验证场景。")

    lines.extend([
        "", "## 用例结果", "",
        "| 用例编号 | 用例标题 | 结果 | 耗时 | 失败类型 | 原因 | 执行证据 |",
        "|---|---|---|---:|---|---|---|",
    ])
    for item in results:
        elapsed = item.get("elapsed_ms")
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            _cell(item.get("case_id", "")), _cell(item.get("case_title", "")),
            _cell(item.get("status", "")),
            ("%s ms" % elapsed) if elapsed is not None else "-",
            _cell(item.get("failure_type", "-")), _cell(item.get("reason", "")),
            _evidence_text(item),
        ))

    lines.extend(["", "## 缺陷记录", ""])
    defects = []
    for item in results:
        if item.get("status") == "failed":
            defects.append((item, item))
        for cleanup_failure in item.get("cleanup_failures", []):
            if isinstance(cleanup_failure, dict):
                defects.append((item, cleanup_failure))
    if defects:
        lines.extend([
            "| 用例编号 | 类型 | 失败步骤 | 期望 | 实际 | 原因 | 执行证据 |",
            "|---|---|---|---|---|---|---|",
        ])
        for item, defect in defects:
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                _cell(item.get("case_id", "")), _cell(defect.get("failure_type", "execution")),
                _cell(_step_label(defect.get("failure_step"))), _cell(defect.get("expected")),
                _cell(defect.get("actual")), _cell(defect.get("reason", "")),
                _evidence_text(item),
            ))
    else:
        lines.append("- 未发现执行失败。")

    lines.extend(["", "## 已知缺陷", ""])
    known_defects = [item for item in execution.get("known_defects", []) if isinstance(item, dict)]
    if known_defects:
        lines.extend([
            "| 缺陷编号 | 严重级别 | 状态 | 标题 | 期望 | 实际 | 证据 |",
            "|---|---|---|---|---|---|---|",
        ])
        for defect in known_defects:
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                _cell(defect.get("defect_id", "")), _cell(defect.get("severity", "")),
                _cell(defect.get("status", "open")), _cell(defect.get("title", "")),
                _cell(defect.get("expected", "")), _cell(defect.get("actual", "")),
                _defect_evidence(defect),
            ))
    else:
        lines.append("- 无已登记缺陷。")

    lines.extend(["", "## 性能数据", ""])
    if step_values:
        lines.extend([
            "| 指标 | 数值 |", "|---|---:|",
            "| 步骤样本量 | %d |" % len(step_values),
            "| P50 步骤耗时 | %.2f ms |" % _percentile(step_values, 0.50),
            "| P95 步骤耗时 | %.2f ms |" % _percentile(step_values, 0.95),
            "| 最大步骤耗时 | %.2f ms |" % max(step_values),
            "", "### 最慢步骤", "",
            "| 用例编号 | 步骤 | 耗时 |", "|---|---|---:|",
        ])
        for elapsed, case_id, step in sorted(step_timings, reverse=True, key=lambda value: value[0])[:5]:
            lines.append("| %s | %s | %.2f ms |" % (
                _cell(case_id), _cell(_step_label(step)), elapsed,
            ))
    else:
        lines.append("- 无执行步骤耗时数据。")

    lines.extend(["", "## 回归比较", ""])
    if regression and regression.get("baseline_available"):
        changes = regression.get("changes", [])
        if changes:
            lines.extend(["| 变化类型 | 对象 | 基线 | 当前 |", "|---|---|---|---|"])
            for change in changes:
                subject, before, after = _regression_values(change)
                lines.append("| %s | %s | %s | %s |" % (
                    _cell(change.get("kind", "")), _cell(subject), _cell(before), _cell(after),
                ))
        else:
            lines.append("- 与基线相比未检测到用例、状态、性能或覆盖变化。")
    else:
        lines.append("- 未提供历史基线，本次结果已作为候选基线。")
    return "\n".join(lines) + "\n"
