"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

为了让"保存后再读"在并发下也保持一致，这里补了三件事：
- 每个模块一把可重入锁，写入（含读-改-写）全部在锁内原子完成；
- 每条记录带 version，乐观锁冲突时由服务层抛出 VersionConflict；
- 幂等台账 store_replay，重复提交同一 request_key 时直接回放首次结果，
  避免异常中断后重试产生"未完成的重复提交"。
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterator

from app.seed import SEED_ROWS

# 各模块"未完结"状态口径：凡是不在终态集合里的记录都算待处理。
# 初始化与每次写入时据此回算 pending，异常中断后不会残留自相矛盾的半成品标记。
PENDING_DONE_STATUSES: dict[str, set[str]] = {
    "result": {"已发布", "已作废"},
}
# 默认按 *_status 字段判断：种子数据里第三个状态即终态。
_DEFAULT_DONE_INDEX = 2


def _is_pending(module: str, status: str, ordered: list[str]) -> bool:
    done = PENDING_DONE_STATUSES.get(module)
    if done is None:
        done = {ordered[min(_DEFAULT_DONE_INDEX, len(ordered) - 1)]} if ordered else set()
    return status not in done


class VersionConflict(Exception):
    """乐观锁冲突：客户端持有的 version 已落后于当前记录。"""

    def __init__(self, entry_id: int, current: dict[str, Any]) -> None:
        super().__init__(f"记录 {entry_id} 已被其他操作修改")
        self.entry_id = entry_id
        self.current = current


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._locks: dict[str, RLock] = {name: RLock() for name in self._tables}
        # request_key -> {"module": str, "response": dict}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._normalize_seed()

    def _normalize_seed(self) -> None:
        """补齐种子记录的版本号，并按状态回算 pending，清除陈旧标记。"""
        from app.seed import STATUS_CHOICES

        for module, rows in self._tables.items():
            ordered = list(STATUS_CHOICES.get(module, ()))
            for row in rows:
                row.setdefault("version", 1)
                status = str(row.get("status") or (ordered[0] if ordered else ""))
                row["status"] = status
                row["pending"] = _is_pending(module, status, ordered)

    @contextmanager
    def lock(self, module: str) -> Iterator[None]:
        """串行化同一模块的读-改-写，避免并发动作把记录改出分叉。"""
        lock = self._locks.setdefault(module, RLock())
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    def check_version(self, entry: dict[str, Any], expected_version: int | None) -> None:
        """校验客户端版本；冲突时把服务端当前内容一并带回，供调用方重新读取。"""
        if expected_version is not None and int(entry.get("version", 1)) != int(expected_version):
            raise VersionConflict(int(entry.get("id", 0)), dict(entry))

    def bump(self, module: str, entry: dict[str, Any]) -> None:
        """一次写入的收尾动作：版本自增并按最新状态回算 pending。"""
        from app.seed import STATUS_CHOICES

        entry["version"] = int(entry.get("version", 1)) + 1
        ordered = list(STATUS_CHOICES.get(module, ()))
        entry["pending"] = _is_pending(module, str(entry.get("status") or ""), ordered)

    def take_replay(self, module: str, request_key: str | None) -> dict[str, Any] | None:
        """命中幂等键时回放首次响应；不同模块即便键相同也互不影响。"""
        if not request_key:
            return None
        record = self._idempotency.get(request_key)
        if record is not None and record["module"] == module:
            return dict(record["response"])
        return None

    def remember(self, module: str, request_key: str | None, response: dict[str, Any]) -> None:
        if request_key:
            self._idempotency[request_key] = {"module": module, "response": dict(response)}

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
