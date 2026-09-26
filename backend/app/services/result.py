"""检测结果业务规则：录入保存、状态流转、字段校验与筛选口径都收在这里。

设计要点（对应"保存/重读/并发"三类问题）：
- serialize 是列表与详情唯一的出参口径，两份数据永远是同一条记录的同一快照；
- save / run_action / create 全部在 store 锁内完成读-改-写，并带 version 乐观锁，
  冲突时抛 VersionConflict，由路由层返回 409 + 服务端当前内容；
- 每次写入后统一 bump：版本自增、按状态回算 pending，中断重试也不留半成品；
- 幂等 request_key 由路由层在锁内处理，重复提交直接回放首次结果。
"""
from __future__ import annotations

from typing import Any

from app.store import VersionConflict, store

MODULE = "result"

# 详情/列表共用的业务字段（顺序即列顺序），保存时只允许改这些字段。
FIELDS = ["结果编号", "所属任务", "检测项", "实测值", "标准限值", "判定结论", "检测日期"]
# 结果状态用英文键 status 驱动，同时镜像到中文展示字段，二者绝不再各说各话。
DISPLAY_STATUS_FIELD = "结果状态"

REQUIRED_CREATE_FIELDS = ["结果编号", "所属任务", "检测项"]
# "录入结果"动作要求实测值齐备；结果编号在登记阶段已校验。
REQUIRED_ENTER_FIELDS = ["所属任务", "检测项", "实测值"]

STATUS_ORDER = ["待录入", "已录入", "待审核", "已发布", "已作废"]
# 允许的状态流转：非法跳转会被拦下，避免并发/重复点击把状态改乱。
ACTION_RULES: dict[str, dict[str, Any]] = {
    "录入结果": {"target": "已录入", "allow_from": {"待录入"}},
    "提交审核": {"target": "待审核", "allow_from": {"已录入"}},
    "作废结果": {"target": "已作废", "allow_from": {"待录入", "已录入", "待审核"}},
}
NEGATIVE_ACTIONS = ["作废结果"]


class BusinessError(Exception):
    """业务校验未通过（字段缺失、非法状态流转等），HTTP 层按 200/ok=false 返回。"""


def serialize(entry: dict[str, Any]) -> dict[str, Any]:
    """列表与详情的唯一出参构造：同一份事实，任何入口看到的字段都一致。"""
    status = str(entry.get("status") or STATUS_ORDER[0])
    item: dict[str, Any] = {"id": int(entry.get("id", 0)), "version": int(entry.get("version", 1))}
    for field in FIELDS:
        item[field] = entry.get(field)
    item["status"] = status
    item[DISPLAY_STATUS_FIELD] = status
    item["pending"] = bool(entry.get("pending"))
    item["abnormal"] = bool(entry.get("abnormal"))
    return item


class ResultService:
    # ---- 查询 ----------------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.lock(MODULE):
            rows = list(store.rows(MODULE))
            if keyword:
                rows = [row for row in rows if keyword in str(row.get("结果编号", ""))]
            if status:
                # 修正：旧实现读不存在的 "status" 键以外的口径会与详情不一致，
                # 这里与 serialize 一样以 status 为准。
                rows = [row for row in rows if str(row.get("status") or "") == status]
            total = len(rows)
            start = max(page - 1, 0) * size
            return [serialize(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.lock(MODULE):
            entry = store.find(MODULE, entry_id)
            return serialize(entry) if entry is not None else None

    # ---- 写入（均为锁内原子操作） ---------------------------------------------

    def create_entry(
        self,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        missing = [
            field for field in REQUIRED_CREATE_FIELDS if not str(values.get(field) or "").strip()
        ]
        if missing:
            return {}, missing
        with store.lock(MODULE):
            entry: dict[str, Any] = {"id": store.next_id(MODULE)}
            for field in FIELDS:
                if values.get(field) is not None:
                    entry[field] = values.get(field)
            entry["status"] = STATUS_ORDER[0]
            entry["abnormal"] = False
            entry["version"] = 0
            store.rows(MODULE).append(entry)
            store.bump(MODULE, entry)
            return serialize(entry), []

    def save_entry(
        self,
        entry_id: int,
        values: dict[str, Any],
        *,
        expected_version: int | None,
        complete: bool = False,
    ) -> dict[str, Any]:
        """保存录入内容。

        complete=True 表示"保存并完成录入"：校验实测值等必填项并原子推进到已录入；
        complete=False 只是暂存字段，状态保持不变（待录入仍是待录入）。
        """
        with store.lock(MODULE):
            entry = store.find(MODULE, entry_id)
            if entry is None:
                raise BusinessError(f"检测结果 {entry_id} 不存在或已归档")
            store.check_version(entry, expected_version)

            status = str(entry.get("status") or STATUS_ORDER[0])
            if status == "已作废":
                raise BusinessError("结果已作废，不能再修改录入内容")
            if status in {"待审核", "已发布"}:
                raise BusinessError(f"结果当前为「{status}」，录入内容已锁定，请先退回或作废")

            for field in FIELDS:
                if field in values:
                    entry[field] = values[field]

            if complete:
                missing = [
                    field
                    for field in REQUIRED_ENTER_FIELDS
                    if not str(entry.get(field) or "").strip()
                ]
                if missing:
                    raise BusinessError(f"完成录入还缺少：{'、'.join(missing)}")
                if status == "待录入":
                    entry["status"] = "已录入"

            store.bump(MODULE, entry)
            return serialize(entry)

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        expected_version: int | None,
    ) -> tuple[dict[str, Any], str]:
        with store.lock(MODULE):
            entry = store.find(MODULE, entry_id)
            if entry is None:
                raise BusinessError(f"检测结果 {entry_id} 不存在或已归档")
            store.check_version(entry, expected_version)

            rule = ACTION_RULES.get(action)
            if rule is None:
                raise BusinessError(f"动作「{action}」不属于检测结果可执行范围")

            status = str(entry.get("status") or STATUS_ORDER[0])
            target = str(rule["target"])
            if status == target:
                # 幂等：已经处于目标态时不重复推进、不额外 bump，直接回放当前事实。
                return serialize(entry), f"检测结果已是「{target}」，无需重复{action}"
            if status not in rule["allow_from"]:
                raise BusinessError(f"当前状态「{status}」不允许执行「{action}」")

            entry["status"] = target
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            store.bump(MODULE, entry)
            return serialize(entry), f"检测结果已{action}"
