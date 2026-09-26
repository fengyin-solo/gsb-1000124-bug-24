"""检测结果接口：维护检测结果，覆盖录入结果、提交审核、作废结果等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.errors import ConflictError
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.result import ResultService

router = APIRouter(prefix="/api/result", tags=["检测结果"])

service = ResultService()

LIST_FIELDS = ["结果编号", "所属任务", "检测项", "实测值", "标准限值", "判定结论", "检测日期", "结果状态"]
STATUSES = ["待录入", "已录入", "待审核", "已发布", "已作废"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按结果编号检索"),
    status: str | None = Query(default=None, description="待录入、已录入、待审核、已发布、已作废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按结果编号与状态过滤检测结果列表；没有数据时返回空页，不报错。"""
    if page < 1:
        raise HTTPException(status_code=400, detail="页码不能小于 1")
    if size < 1 or size > 200:
        raise HTTPException(status_code=400, detail="每页数量需在 1 到 200 之间")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出检测结果清单：返回当前全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "result", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条检测结果明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检测结果 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条检测结果，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values, request_id=payload.request_id)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="检测结果已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """保存检测结果；只有保存成功后，前端才应以返回的明细作为最新事实。"""
    try:
        entry = service.update_entry(
            entry_id,
            payload.values,
            expected_version=payload.expected_version,
            request_id=payload.request_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ActionResult(ok=True, message="检测结果已保存", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检测结果执行录入结果、提交审核、作废结果；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    if payload.expected_version is None:
        raise ConflictError("缺少版本号，请重新读取检测结果后再操作")
    entry, message = service.run_action(
        entry_id,
        action,
        expected_version=payload.expected_version,
        request_id=payload.request_id,
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
