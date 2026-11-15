"""采集监控业务规则：状态流转、字段校验与筛选口径都收在这里。

状态机（只允许相邻推进，不允许跳级）：
    草稿 -> 面板评审中 -> 已启用 -> 已归档

归档语义（与动态限速规则在同一事务/同一提交内完成）：
- 面板停止采样、不再产生新告警；
- 保留最后告警窗口与通知组，供追溯；
- 引用该面板的已启用限速规则级联进入「待复核」，其余规则标记绑定失效；
- 归档是终态：重复归档只返回首次结果，不允许第二个归档提交成功。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services import binding
from app.store import store

MODULE = "monitor"
REQUIRED_FIELDS = ["面板编号", "监控指标集", "采样间隔秒"]
STATUS_ORDER = ["草稿", "面板评审中", "已启用", "已归档"]
# 动作 -> 唯一合法的前置状态；不在表里的动作一律拒绝
ACTION_GUARDS: dict[str, str] = {
    "提交面板评审": "草稿",
    "启用面板": "面板评审中",
    "归档面板": "已启用",
}
ARCHIVED = STATUS_ORDER[-1]


class MonitorService:
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
            rows = [row for row in rows if keyword in str(row.get("面板编号", ""))]
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
            code = str(values.get("面板编号")).strip()
            if store.find_by_code(MODULE, "面板编号", code) is not None:
                raise binding.BindingError(f"面板编号 {code} 已存在，不能重复登记")
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            # 登记时补齐面板版本与错误率阈值，作为后续限速绑定的快照来源
            rate = binding.parse_error_rate(values)
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            entry["面板版本"] = 1
            entry["错误率阈值"] = rate
            entry["最后告警窗口"] = None
            entry["最后通知组"] = values.get("通知接收组")
            entry["采样中"] = False
            rows.append(entry)
            return dict(entry), []

    def run_action(
        self, entry_id: int, action: str
    ) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        """执行动作，返回 (面板, 消息, 被级联更新的限速规则)。"""
        if action not in ACTION_GUARDS:
            return None, f"动作「{action}」不属于采集监控可执行范围", []
        with store.transaction():
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"监控面板 {entry_id} 不存在", []
            current = entry.get("status")
            if current == ARCHIVED:
                # 终态幂等：重复归档/任何后续动作都不能产生第二个成功提交
                return None, f"监控面板 {entry.get('面板编号')} 已归档，终态不可再执行动作", []
            expected = ACTION_GUARDS[action]
            if current != expected:
                target_status = next(
                    (name for name, guard in ACTION_GUARDS.items() if guard == current), None
                )
                hint = f"当前状态为「{current}」，仅可执行「{target_status}」" if target_status else ""
                return None, f"不允许从「{current}」执行「{action}」（禁止跳级）。{hint}".rstrip("。 "), []

            if action == "归档面板":
                return self._archive(entry)
            if action == "启用面板":
                entry["status"] = "已启用"
                entry["pending"] = False
                entry["采样中"] = True
                return dict(entry), f"监控面板 {entry['面板编号']} 已启用，恢复采样", []
            # 提交面板评审
            entry["status"] = "面板评审中"
            entry["pending"] = True
            entry["采样中"] = False
            return dict(entry), f"监控面板 {entry['面板编号']} 已提交评审", []

    def _archive(
        self, entry: dict[str, Any]
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        """归档 + 告警停止 + 规则状态更新在同一事务内提交。"""
        panel_code = entry["面板编号"]
        # 停止采样：归档面板不再产生新告警
        entry["采样中"] = False
        entry["status"] = ARCHIVED
        entry["pending"] = False
        # 保留最后告警窗口与通知组：仅在尚无历史窗口时落一个占位，绝不清空
        if not entry.get("最后告警窗口"):
            entry["最后告警窗口"] = self._last_window_placeholder()
        if not entry.get("最后通知组"):
            entry["最后通知组"] = entry.get("通知接收组")
        # 同一提交：级联更新所有引用该面板的限速规则
        affected = binding.cascade_archive(entry)
        rule_codes = "、".join(str(rule.get("规则编号")) for rule in affected) or "无"
        message = (
            f"监控面板 {panel_code} 已归档：采样与新告警已停止，最后告警窗口与通知组已保留；"
            f"引用它的限速规则（{rule_codes}）已同步进入待复核或标记绑定失效"
        )
        return dict(entry), message, [dict(rule) for rule in affected]

    @staticmethod
    def _last_window_placeholder() -> str:
        now = datetime.now().replace(microsecond=0).isoformat()
        return f"{now}/{now}"
