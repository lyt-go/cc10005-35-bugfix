"""监控面板与动态限速规则的绑定域。

职责边界：
- 限速规则启用/重新启用时，解析并校验它引用的监控面板版本，快照该版本的错误率阈值；
- 监控面板归档时，在同一事务内级联更新所有引用规则（已启用 -> 待复核，绑定标记失效）；
- 已归档面板保留最后告警窗口与通知组，只是停止采样、不再产生新告警。

所有函数都假定运行在 ``store.transaction()`` 与 store 锁内（可重入锁允许嵌套调用）。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MONITOR_MODULE = "monitor"
THROTTLE_MODULE = "throttle"
DECISION_MODULE = "throttle_decision"

MONITOR_CODE_FIELD = "面板编号"
THROTTLE_CODE_FIELD = "规则编号"

# 面板归档后，限速规则引用该面板时统一进入的复核状态
REVIEW_STATUS = "待复核"


class BindingError(ValueError):
    """绑定校验失败：消息会直接返回给前端，文案需可读。"""


def find_monitor(panel_code: str) -> dict[str, Any] | None:
    return store.find_by_code(MONITOR_MODULE, MONITOR_CODE_FIELD, panel_code)


def find_rule(rule_code: str) -> dict[str, Any] | None:
    return store.find_by_code(THROTTLE_MODULE, THROTTLE_CODE_FIELD, rule_code)


def parse_error_rate(values: dict[str, Any]) -> float | None:
    """解析错误率阈值；空值视为未提供，非法格式抛出可读错误。"""
    raw = values.get("错误率阈值")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        rate = float(raw)
    except (TypeError, ValueError) as exc:
        raise BindingError("错误率阈值必须是 0~100 之间的数值（百分比）") from exc
    # 边界值不能绕过限速决策：0 与 100 都是合法边界，超出范围才拒绝
    if rate < 0 or rate > 100:
        raise BindingError("错误率阈值超出边界：仅允许 0~100 之间的百分比")
    return rate


def resolve_binding(values: dict[str, Any]) -> tuple[str, int, float, dict[str, Any]]:
    """启用规则时解析绑定的新面板版本。

    要求：
    - 必须显式引用一个存在的监控面板编号；
    - 面板必须处于「已启用」（归档/评审中的面板不能作为新绑定版本）；
    - 错误率阈值以面板当前版本为准，请求里可显式携带但必须与面板一致，
      避免告警条件和实际速率不一致。
    """
    panel_code = str(values.get("绑定面板编号") or "").strip()
    if not panel_code:
        raise BindingError("启用限速规则必须引用监控面板编号")
    panel = find_monitor(panel_code)
    if panel is None:
        raise BindingError(f"监控面板 {panel_code} 不存在")
    if panel.get("status") == "已归档":
        raise BindingError(
            f"监控面板 {panel_code} 已归档，不能作为新绑定版本，请改用新面板编号"
        )
    if panel.get("status") != "已启用":
        raise BindingError(
            f"监控面板 {panel_code} 当前为「{panel.get('status')}」，仅已启用面板可被限速规则引用"
        )
    version = int(panel.get("面板版本", 1))
    panel_rate = panel.get("错误率阈值")
    if panel_rate is None:
        raise BindingError(f"监控面板 {panel_code} 缺少错误率阈值，无法快照限速依据")
    requested_rate = parse_error_rate(values)
    if requested_rate is not None and abs(requested_rate - float(panel_rate)) > 1e-9:
        raise BindingError(
            f"提交的错误率阈值 {requested_rate} 与面板 {panel_code} v{version} 的阈值 "
            f"{panel_rate} 不一致，限速依据必须与告警条件保持同一版本"
        )
    return panel_code, version, float(panel_rate), panel


def apply_binding(rule: dict[str, Any], panel_code: str, version: int, rate: float) -> None:
    """把面板版本快照写到规则上；历史决策记录不受影响。"""
    rule["绑定面板编号"] = panel_code
    rule["绑定面板版本"] = version
    rule["绑定错误率阈值"] = rate
    rule["绑定失效"] = False


def cascade_archive(panel: dict[str, Any]) -> list[dict[str, Any]]:
    """归档面板的级联动作，返回被联动更新的规则列表。

    - 面板停止采样、不再产生新告警，但保留最后告警窗口与通知组；
    - 引用该面板的规则：已启用 -> 待复核；其余状态保持原状态，仅标记绑定失效；
      待复核/已关闭规则重新启用前必须引用新面板版本。
    """
    panel_code = panel.get(MONITOR_CODE_FIELD)
    affected: list[dict[str, Any]] = []
    for rule in store.rows(THROTTLE_MODULE):
        if rule.get("绑定面板编号") != panel_code:
            continue
        rule["绑定失效"] = True
        if rule.get("status") == "已启用":
            rule["status"] = REVIEW_STATUS
            rule["pending"] = True
            rule["abnormal"] = True
        affected.append(rule)
    return affected


def append_decision(
    *,
    rule: dict[str, Any],
    observed_error_rate: float,
    decision: str,
    rate: int,
    decided_at: str,
) -> dict[str, Any]:
    """追加一条不可变限速决策历史；始终快照决策时的面板版本与阈值。"""
    rows = store.rows(DECISION_MODULE)
    entry = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "规则编号": rule.get(THROTTLE_CODE_FIELD),
        "面板编号": rule.get("绑定面板编号"),
        "面板版本": rule.get("绑定面板版本"),
        "错误率阈值": rule.get("绑定错误率阈值"),
        "实测错误率": observed_error_rate,
        "决策结果": decision,
        "决策速率": rate,
        "决策时间": decided_at,
    }
    rows.append(entry)
    return entry


def list_decisions(rule_code: str | None = None) -> list[dict[str, Any]]:
    rows = store.rows(DECISION_MODULE)
    if rule_code:
        rows = [row for row in rows if row.get(THROTTLE_CODE_FIELD) == rule_code]
    # 新记录在后，展示时倒序更符合「最近决策在前」
    return list(reversed(rows))
