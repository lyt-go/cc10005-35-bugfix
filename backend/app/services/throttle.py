"""动态限速业务规则：状态流转、面板绑定校验与限速决策。

状态机（只允许按 评审 -> 启用 -> 关闭 推进，禁止跳级）：
    草稿 -> 规则评审中 -> 已启用 -> 已关闭
    已启用（面板归档级联）-> 待复核 -> 已启用（重新绑定新面板版本）
    已关闭 -> 规则评审中（重新走评审，必须引用新面板版本）-> 已启用

绑定口径：
- 每次「启用规则」都必须快照一个已启用监控面板的编号、版本与错误率阈值；
- 面板归档后引用规则进入「待复核」/绑定失效，重新启用必须引用新面板版本；
- 限速决策历史只追加、不改写，历史记录永远保留决策当时的告警面板版本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services import binding
from app.store import store

MODULE = "throttle"
REQUIRED_FIELDS = ["规则编号", "目标域组", "初始速率"]
STATUS_ORDER = ["草稿", "规则评审中", "待复核", "已启用", "已关闭"]
# 动作 -> 允许执行该动作的前置状态集合；关闭状态不在任何启用类动作的前置里
ACTION_GUARDS: dict[str, set[str]] = {
    "提交规则评审": {"草稿", "已关闭"},
    "启用规则": {"规则评审中", "待复核"},
    "关闭规则": {"已启用"},
}


class ThrottleService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.lock():
            rows = [dict(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.lock():
            row = store.find(MODULE, entry_id)
            return dict(row) if row else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with store.transaction():
            rows = store.rows(MODULE)
            code = str(values.get("规则编号")).strip()
            if store.find_by_code(MODULE, "规则编号", code) is not None:
                raise binding.BindingError(f"规则编号 {code} 已存在，不能重复登记")
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = "草稿"
            entry["pending"] = True
            entry["abnormal"] = False
            entry["绑定面板编号"] = None
            entry["绑定面板版本"] = None
            entry["绑定错误率阈值"] = None
            entry["绑定失效"] = False
            rows.append(entry)
            return dict(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        if action not in ACTION_GUARDS:
            return None, f"动作「{action}」不属于动态限速可执行范围"
        with store.transaction():
            try:
                entry = store.find(MODULE, entry_id)
                if entry is None:
                    return None, f"限速规则 {entry_id} 不存在"
                current = entry.get("status")
                if current not in ACTION_GUARDS[action]:
                    return None, self._guard_message(current, action)

                if action == "提交规则评审":
                    entry["status"] = "规则评审中"
                    entry["pending"] = True
                    # 关闭后重新走评审：仅当绑定面板已归档/消失时才把绑定标记失效；
                    # 面板仍有效则保留绑定可追溯，启用时依旧必须显式引用面板版本
                    if current == "已关闭" and entry.get("绑定面板编号"):
                        panel = binding.find_monitor(entry["绑定面板编号"])
                        if panel is None or panel.get("status") == "已归档":
                            entry["绑定失效"] = True
                    return dict(entry), self._review_message(entry)

                if action == "关闭规则":
                    entry["status"] = "已关闭"
                    entry["pending"] = True
                    entry["abnormal"] = False
                    return dict(entry), (
                        f"限速规则 {entry['规则编号']} 已关闭；重新启用须先提交评审并引用新面板版本"
                    )

                # 启用规则：无论来自评审中还是待复核，都必须解析并快照新面板版本
                return self._enable(entry, values)
            except binding.BindingError as exc:
                # 绑定校验失败统一转成可读消息返回；事务上下文负责回滚已做的改动
                return None, str(exc)

    def _enable(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any], str]:
        # resolve_binding 已保证：面板存在、处于已启用、阈值与面板版本一致；
        # 归档面板（旧版本）在此之前就会被拒绝，因此能走到这里的一定是当前有效版本。
        panel_code, version, rate, _panel = binding.resolve_binding(values)
        binding.apply_binding(entry, panel_code, version, rate)
        entry["status"] = "已启用"
        entry["pending"] = False
        entry["abnormal"] = False
        return dict(entry), (
            f"限速规则 {entry['规则编号']} 已启用，绑定监控面板 {panel_code} v{version}，"
            f"错误率阈值快照为 {rate}%"
        )

    def record_decision(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """按当前绑定的面板阈值执行一次限速决策并追加历史。"""
        with store.transaction():
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"限速规则 {entry_id} 不存在"
            if entry.get("status") != "已启用":
                return None, (
                    f"限速规则 {entry.get('规则编号')} 当前为「{entry.get('status')}」，"
                    "只有已启用规则会产生限速决策"
                )
            if entry.get("绑定失效") or entry.get("绑定面板编号") is None:
                return None, "规则未绑定有效监控面板版本，拒绝执行限速决策"
            try:
                observed = float(values.get("实测错误率"))
                current_rate = int(values.get("当前速率"))
            except (TypeError, ValueError):
                return None, "实测错误率与当前速率必须是数值"
            # 边界值不能绕过决策：0、100 的错误率与 0 速率都走正常判定
            if observed < 0 or observed > 100:
                return None, "实测错误率超出边界：仅允许 0~100 之间的百分比"
            if current_rate < 0:
                return None, "当前速率不能为负数"

            threshold = float(entry["绑定错误率阈值"])
            if observed > threshold:
                decision = "减速"
                # 边界 current_rate=0 时 max 兜底为 0，不会产生负速率
                new_rate = max(int(current_rate * 0.8), 0)
            elif observed == threshold:
                # 等于阈值按告警条件处理：维持并标记观察，避免边界值绕过限速
                decision = "维持观察"
                new_rate = current_rate
            else:
                decision = "维持"
                new_rate = current_rate
            record = binding.append_decision(
                rule=entry,
                observed_error_rate=observed,
                decision=decision,
                rate=new_rate,
                decided_at=datetime.now().replace(microsecond=0).isoformat(),
            )
            return record, (
                f"实测错误率 {observed}% 对比面板 {entry['绑定面板编号']} "
                f"v{entry['绑定面板版本']} 阈值 {threshold}%，决策：{decision}，速率 {new_rate}"
            )

    def list_decisions(self, entry_id: int | None = None) -> list[dict[str, Any]]:
        with store.lock():
            rule_code = None
            if entry_id is not None:
                rule = store.find(MODULE, entry_id)
                if rule is None:
                    return []
                rule_code = str(rule.get("规则编号"))
            return binding.list_decisions(rule_code)

    @staticmethod
    def _review_message(entry: dict[str, Any]) -> str:
        if entry.get("绑定失效"):
            return (
                f"限速规则 {entry['规则编号']} 已进入评审，原绑定面板已归档，"
                "启用时必须引用新的监控面板编号与错误率阈值"
            )
        return f"限速规则 {entry['规则编号']} 已提交评审，启用时须显式引用监控面板版本"

    @staticmethod
    def _guard_message(current: str, action: str) -> str:
        allowed = [name for name, guards in ACTION_GUARDS.items() if current in guards]
        if allowed:
            return f"不允许从「{current}」执行「{action}」（禁止跳级），当前仅可执行：{'、'.join(allowed)}"
        return f"「{current}」状态没有可执行动作，不能执行「{action}」"
