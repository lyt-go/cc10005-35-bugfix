"""动态限速接口：维护限速规则，覆盖提交规则评审、启用规则、关闭规则等动作。

规则只能按 评审 -> 启用 -> 关闭 推进，关闭后重新启用必须重新评审并引用新面板版本；
限速决策走独立接口，决策历史只追加不改写，保留决策当时的告警面板版本。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.binding import BindingError
from app.services.throttle import ThrottleService

router = APIRouter(prefix="/api/throttle", tags=["动态限速"])

service = ThrottleService()

LIST_FIELDS = ["规则编号", "目标域组", "初始速率", "最大速率", "加速因子", "减速因子", "错误率阈值", "降速恢复间隔"]
STATUSES = ["草稿", "规则评审中", "待复核", "已启用", "已关闭"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、规则评审中、待复核、已启用、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤动态限速列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if page < 1:
        raise HTTPException(status_code=400, detail="页码必须从 1 开始")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条限速规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"限速规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条限速规则，缺字段时说明原因而不是静默丢弃。"""
    try:
        entry, missing = service.create_entry(payload.values)
    except BindingError as exc:
        return ActionResult(ok=False, message=str(exc))
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="限速规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条限速规则执行提交规则评审、启用规则、关闭规则；跳级动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, payload.values)
    except BindingError as exc:
        return ActionResult(ok=False, message=str(exc))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/decisions", response_model=ActionResult)
def record_decision(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按规则当前绑定的面板阈值执行一次限速决策，历史只追加、保留原告警版本。"""
    try:
        record, message = service.record_decision(entry_id, payload.values)
    except BindingError as exc:
        return ActionResult(ok=False, message=str(exc))
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/{entry_id}/decisions")
def list_decisions(entry_id: int) -> dict[str, Any]:
    """读取单条规则的限速决策历史（最近决策在前）。"""
    items = service.list_decisions(entry_id)
    return {"module": "throttle_decision", "total": len(items), "items": items}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出动态限速清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "throttle", "total": total, "items": items}
