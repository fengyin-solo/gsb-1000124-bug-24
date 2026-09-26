"""检测结果接口：维护检测结果，覆盖录入保存、提交审核、作废结果等动作。

并发/重复提交的约定：
- 所有写接口接受 expected_version（乐观锁），落后时返回 409 与服务端当前内容；
- 接受 request_key（幂等键）：同键重复请求只在首次生效，之后原样回放首次响应，
  异常中断后重试不会再制造一条"未完成的重复提交"。
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.result import FIELDS, BusinessError, ResultService, serialize
from app.store import VersionConflict, store

router = APIRouter(prefix="/api/result", tags=["检测结果"])

service = ResultService()

LIST_FIELDS = FIELDS + ["结果状态"]
STATUSES = ["待录入", "已录入", "待审核", "已发布", "已作废"]


def _conflict(exc: VersionConflict) -> JSONResponse:
    """冲突时不覆盖任何内容，把服务端最新快照交回客户端重新读取。"""
    return JSONResponse(
        status_code=409,
        content={
            "detail": f"记录 {exc.entry_id} 已被其他操作修改，请以最新内容为准后重试",
            "current": serialize(exc.current),
        },
    )


def _serve_write(
    request_key: str | None, produce: Callable[[], ActionResult]
) -> ActionResult | JSONResponse:
    """锁内完成"查幂等台账 -> 执行 -> 记账"，保证重复提交与首次响应完全一致。

    - 业务校验失败（BusinessError）向上抛，由路由返回 ok=false 且不记账：
      调用方补齐内容后用同一键仍可再次提交；
    - 版本冲突（VersionConflict）记账后回放：旧版本重试必然继续冲突；
    - 成功结果记账：网络中断/连点导致的重试只回放，绝不二次生效。
    """
    if not request_key:
        return produce()
    with store.lock("result"):
        replay = store.take_replay("result", request_key)
        if replay is not None:
            if replay["status"] != 200:
                return JSONResponse(status_code=replay["status"], content=replay["body"])
            return ActionResult(**replay["body"])
        try:
            result = produce()
        except VersionConflict as exc:
            store.remember("result", request_key, {
                "status": 409,
                "body": {
                    "detail": f"记录 {exc.entry_id} 已被其他操作修改，请以最新内容为准后重试",
                    "current": serialize(exc.current),
                },
            })
            return _conflict(exc)
        store.remember("result", request_key, {"status": 200, "body": result.model_dump()})
        return result


def _fail(exc: BusinessError) -> ActionResult:
    return ActionResult(ok=False, message=str(exc))


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按结果编号检索"),
    status: str | None = Query(default=None, description="待录入、已录入、待审核、已发布、已作废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按结果编号与状态过滤检测结果列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出检测结果清单：返回当前全量数据，口径与列表/详情完全一致。"""
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
def create_entry(payload: EntryPayload) -> ActionResult | JSONResponse:
    """登记一条检测结果，缺字段时说明原因而不是静默丢弃。"""
    try:
        return _serve_write(payload.request_key, lambda: _do_create(payload))
    except BusinessError as exc:
        return _fail(exc)


def _do_create(payload: EntryPayload) -> ActionResult:
    entry, missing = service.create_entry(payload.values)
    if missing:
        raise BusinessError(f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="检测结果已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def save_entry(entry_id: int, payload: EntryPayload) -> ActionResult | JSONResponse:
    """保存录入内容（检测结果、结果编号、实测值等）。

    values.complete=true 时校验必填并原子推进到"已录入"；否则仅暂存字段。
    保存成功的响应即最新快照，列表据此即时更新，重新打开详情再读也是同一份。
    """
    complete = bool(payload.values.pop("complete", False))
    try:
        return _serve_write(
            payload.request_key,
            lambda: _do_save(entry_id, payload, complete),
        )
    except BusinessError as exc:
        return _fail(exc)
    except VersionConflict as exc:
        return _conflict(exc)


def _do_save(entry_id: int, payload: EntryPayload, complete: bool) -> ActionResult:
    entry = service.save_entry(
        entry_id,
        payload.values,
        expected_version=payload.expected_version,
        complete=complete,
    )
    message = "检测结果已保存并完成录入" if complete else "录入内容已暂存"
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult | JSONResponse:
    """对单条检测结果执行录入结果、提交审核、作废结果；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        return _serve_write(
            payload.request_key,
            lambda: _do_action(entry_id, action, payload.expected_version),
        )
    except BusinessError as exc:
        return _fail(exc)
    except VersionConflict as exc:
        return _conflict(exc)


def _do_action(entry_id: int, action: str, expected_version: int | None) -> ActionResult:
    entry, message = service.run_action(entry_id, action, expected_version=expected_version)
    return ActionResult(ok=True, message=message, entry=entry)
