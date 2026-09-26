"""检测结果业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import threading
from typing import Any

from app.errors import ConflictError
from app.store import store

MODULE = "result"
REQUIRED_FIELDS = ["结果编号", "所属任务", "检测项"]
EDITABLE_FIELDS = REQUIRED_FIELDS + ["实测值", "标准限值", "判定结论", "检测日期"]
STATUS_ORDER = ["待录入", "已录入", "待审核", "已发布", "已作废"]
ACTION_RULES = {"录入结果": "已录入", "提交审核": "待审核", "作废结果": "已作废"}
NEGATIVE_ACTIONS = ["作废结果"]
PENDING_STATUSES = {"待录入", "已录入"}
EDITABLE_STATUSES = {"待录入", "已录入", "待审核"}
FINAL_STATUSES = {"已作废"}


class ResultService:
    def __init__(self) -> None:
        self._requests: dict[str, dict[str, Any]] = {}
        self._request_lock = threading.RLock()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.transaction(MODULE):
            rows = [self._serialize(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("结果编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.transaction(MODULE):
            entry = store.find_copy(MODULE, entry_id)
        return self._serialize(entry) if entry is not None else None

    def create_entry(
        self,
        values: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        request_id = self._normalize_request_id(request_id)
        with self._request_lock:
            if request_id:
                cached = self._replayed_request(request_id)
                if cached is not None:
                    return cached, []

            clean = {field: self._clean_text(values.get(field)) for field in EDITABLE_FIELDS}
            missing = [field for field in REQUIRED_FIELDS if not clean[field]]
            if missing:
                return None, missing

            with store.transaction(MODULE):
                existing = self._find_by_business_key(clean["结果编号"])
                if existing is not None:
                    raise ConflictError(f"结果编号「{clean['结果编号']}」已存在", self._serialize(existing))

                rows = store.rows(MODULE)
                entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
                entry.update({field: clean[field] for field in EDITABLE_FIELDS})
                entry["status"] = STATUS_ORDER[0]
                entry["pending"] = True
                entry["abnormal"] = False
                entry["version"] = 1
                rows.append(entry)
                saved = self._serialize(entry)

            if request_id:
                self._requests[request_id] = store.snapshot(saved)
            return saved, []

    def update_entry(
        self,
        entry_id: int,
        values: dict[str, Any],
        *,
        expected_version: int | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = self._normalize_request_id(request_id)
        with self._request_lock:
            if request_id:
                cached = self._replayed_request(request_id)
                if cached is not None and int(cached.get("id", 0)) == entry_id:
                    return cached

            clean = {field: self._clean_text(values.get(field)) for field in EDITABLE_FIELDS}
            missing = [field for field in REQUIRED_FIELDS if not clean[field]]
            if missing:
                raise ValueError(f"缺少必填字段：{'、'.join(missing)}")
            if expected_version is None:
                raise ConflictError("缺少版本号，请重新读取检测结果后再保存")

            with store.transaction(MODULE):
                entry = store.find(MODULE, entry_id)
                if entry is None:
                    raise KeyError(f"检测结果 {entry_id} 不存在或已归档")
                current_version = int(entry.get("version", 1))
                if current_version != int(expected_version):
                    raise ConflictError("检测结果已被其他人修改，请使用最新内容继续编辑", self._serialize(entry))
                if entry.get("status") not in EDITABLE_STATUSES:
                    raise ConflictError("当前状态不允许修改检测结果", self._serialize(entry))

                duplicate = self._find_by_business_key(clean["结果编号"], exclude_id=entry_id)
                if duplicate is not None:
                    raise ConflictError(f"结果编号「{clean['结果编号']}」已被其他记录使用", self._serialize(duplicate))

                entry.update({field: clean[field] for field in EDITABLE_FIELDS})
                if entry.get("status") == "待录入":
                    entry["status"] = "已录入"
                elif entry.get("status") == "待审核":
                    # 修改待审核内容后回到“已录入”，避免审核依据与详情内容分叉。
                    entry["status"] = "已录入"
                entry["结果状态"] = entry["status"]
                entry["pending"] = entry["status"] in PENDING_STATUSES
                entry["abnormal"] = entry["status"] in FINAL_STATUSES
                entry["version"] = current_version + 1
                saved = self._serialize(entry)

            if request_id:
                self._requests[request_id] = store.snapshot(saved)
            return saved

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        expected_version: int | None = None,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        request_id = self._normalize_request_id(request_id)
        with self._request_lock:
            if request_id:
                cached = self._replayed_request(request_id)
                if cached is not None and int(cached.get("id", 0)) == entry_id:
                    return cached, "检测结果动作已处理"

            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于检测结果可执行范围"
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"

            with store.transaction(MODULE):
                entry = store.find(MODULE, entry_id)
                if entry is None:
                    return None, f"检测结果 {entry_id} 不存在或已归档"

                current_status = str(entry.get("status") or "")
                current_version = int(entry.get("version", 1))
                if current_status == target:
                    saved = self._serialize(entry)
                    if request_id:
                        self._requests[request_id] = store.snapshot(saved)
                    return saved, f"检测结果已{action}"
                if expected_version is not None and current_version != int(expected_version):
                    raise ConflictError("检测结果已被其他人修改，请刷新后继续操作", self._serialize(entry))
                if not self._can_transition(current_status, target):
                    raise ConflictError(f"检测结果当前为「{current_status}」，不能执行「{action}」", self._serialize(entry))

                entry["status"] = target
                entry["结果状态"] = target
                entry["pending"] = target in PENDING_STATUSES
                entry["abnormal"] = action in NEGATIVE_ACTIONS
                entry["version"] = current_version + 1
                saved = self._serialize(entry)

            if request_id:
                self._requests[request_id] = store.snapshot(saved)
            return saved, f"检测结果已{action}"

    @staticmethod
    def _can_transition(current: str, target: str) -> bool:
        if current == "已作废":
            return False
        if target == "已作废":
            return True
        if current == "待录入" and target == "已录入":
            return True
        if current == "已录入" and target == "待审核":
            return True
        return False

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_request_id(request_id: str | None) -> str | None:
        if request_id is None:
            return None
        text = str(request_id).strip()
        return text or None

    def _replayed_request(self, request_id: str) -> dict[str, Any] | None:
        cached = self._requests.get(request_id)
        return store.snapshot(cached) if cached is not None else None

    @staticmethod
    def _find_by_business_key(result_code: str | None, *, exclude_id: int | None = None) -> dict[str, Any] | None:
        if not result_code:
            return None
        for row in store.rows(MODULE):
            if row.get("结果编号") == result_code and (exclude_id is None or int(row.get("id", 0)) != exclude_id):
                return row
        return None

    @staticmethod
    def _serialize(entry: dict[str, Any] | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        data = store.snapshot(entry)
        status = str(data.get("status") or "")
        data["status"] = status
        data["结果状态"] = status
        data.setdefault("version", 1)
        return data
