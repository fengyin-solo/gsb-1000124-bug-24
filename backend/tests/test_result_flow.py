"""检测结果模块的端到端校验：保存/重读一致性、防重复提交、乐观锁与状态机。

直接以 TestClient 打接口，每个用例重置内存仓库，保证互相独立。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.seed import SEED_ROWS
from app.store import store


@pytest.fixture()
def client() -> TestClient:
    """每个用例前重置内存仓库（含幂等台账与版本号），用例间互不影响。"""
    fresh = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    store._tables = fresh  # type: ignore[attr-defined]
    store._idempotency = {}  # type: ignore[attr-defined]
    store._normalize_seed()  # type: ignore[attr-defined]
    from app.main import app
    return TestClient(app, raise_server_exceptions=True)


# ---- 读取一致性 -------------------------------------------------------------

def test_list_and_detail_share_same_serialized_fact(client: TestClient) -> None:
    listing = client.get("/api/result").json()["items"]
    assert listing  # 种子里有数据
    for row in listing:
        detail = client.get(f"/api/result/{row['id']}").json()
        assert detail == row
        assert detail["status"] == detail["结果状态"], "状态键与中文展示列必须同义"


def test_list_status_filter_matches_detail(client: TestClient) -> None:
    for status in ["待录入", "已录入", "待审核"]:
        items = client.get("/api/result", params={"status": status}).json()["items"]
        assert items, f"种子数据中应存在 {status} 的记录"
        for row in items:
            detail = client.get(f"/api/result/{row['id']}").json()
            assert detail["status"] == status


# ---- 保存后再读 -------------------------------------------------------------

def test_save_then_reload_persists_new_content_and_version(client: TestClient) -> None:
    detail = client.get("/api/result/1").json()
    assert detail["实测值"] != "0.42 mg/L"
    resp = client.put("/api/result/1", json={
        "values": {"实测值": "0.42 mg/L", "判定结论": "合格"},
        "expected_version": detail["version"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    saved = body["entry"]
    assert saved["version"] == detail["version"] + 1
    assert saved["实测值"] == "0.42 mg/L"
    # 重新进入（重新发起 GET）读到的必须是保存后的内容，而不是旧值。
    reread = client.get("/api/result/1").json()
    assert reread == saved
    listed = [r for r in client.get("/api/result").json()["items"] if r["id"] == 1][0]
    assert listed == saved, "列表与详情必须是同一事实"


def test_complete_entry_moves_status_atomically_and_both_views_reflect(client: TestClient) -> None:
    detail = client.get("/api/result/1").json()
    assert detail["status"] == "待录入"
    body = client.put("/api/result/1", json={
        "values": {"实测值": "1.0", "complete": True},
        "expected_version": detail["version"],
    }).json()
    assert body["ok"] is True
    assert body["entry"]["status"] == "已录入"
    assert body["entry"]["结果状态"] == "已录入"
    listed = [r for r in client.get("/api/result").json()["items"] if r["id"] == 1][0]
    assert listed["status"] == "已录入"
    assert listed["version"] == body["entry"]["version"]


def test_complete_requires_measured_value(client: TestClient) -> None:
    detail = client.get("/api/result/1").json()
    body = client.put("/api/result/1", json={
        "values": {"实测值": "", "complete": True},
        "expected_version": detail["version"],
    }).json()
    assert body["ok"] is False
    assert "实测值" in body["message"]
    # 校验失败不能把状态/版本改出去
    again = client.get("/api/result/1").json()
    assert again["status"] == "待录入"
    assert again["version"] == detail["version"]


# ---- 重复提交（幂等） --------------------------------------------------------

def test_duplicate_submit_with_same_key_applies_once(client: TestClient) -> None:
    detail = client.get("/api/result/2").json()
    payload = {
        "values": {"action": "提交审核"},
        "expected_version": detail["version"],
        "request_key": "key-submit-once",
    }
    first = client.post("/api/result/2/actions", json=payload).json()
    second = client.post("/api/result/2/actions", json=payload).json()
    third = client.post("/api/result/2/actions", json=payload).json()
    assert first["ok"] is True
    assert second == first, "重复提交必须回放首次响应"
    assert third == first
    final = client.get("/api/result/2").json()
    assert final["status"] == "待审核"
    # 只发生了一次状态推进：2 -> 3
    assert final["version"] == detail["version"] + 1


def test_retried_save_after_interruption_does_not_fork(client: TestClient) -> None:
    """异常中断（客户端没收到响应）后重试，同一 request_key 只生效一次。"""
    detail = client.get("/api/result/1").json()
    payload = {
        "values": {"实测值": "中断前已写入", "complete": True},
        "expected_version": detail["version"],
        "request_key": "key-interrupted",
    }
    first = client.put("/api/result/1", json=payload).json()
    retry = client.put("/api/result/1", json=payload).json()  # 版本已落后也应回放
    assert retry == first
    assert client.get("/api/result/1").json()["version"] == detail["version"] + 1


def test_business_failure_does_not_consume_idempotency_key(client: TestClient) -> None:
    detail = client.get("/api/result/1").json()
    bad = client.put("/api/result/1", json={
        "values": {"实测值": "", "complete": True},
        "expected_version": detail["version"],
        "request_key": "key-reusable",
    }).json()
    assert bad["ok"] is False
    good = client.put("/api/result/1", json={
        "values": {"实测值": "补齐了", "complete": True},
        "expected_version": detail["version"],
        "request_key": "key-reusable",
    }).json()
    assert good["ok"] is True
    assert good["entry"]["status"] == "已录入"


# ---- 并发修改（乐观锁） ------------------------------------------------------

def test_stale_version_gets_409_with_current_snapshot(client: TestClient) -> None:
    detail = client.get("/api/result/2").json()
    # 另一个会话先推进了状态
    client.post("/api/result/2/actions", json={
        "values": {"action": "提交审核"},
        "expected_version": detail["version"],
    })
    # 当前会话拿着旧版本继续保存 -> 409，且不能把两份内容改分叉
    resp = client.put("/api/result/2", json={
        "values": {"实测值": "旧会话的覆盖"},
        "expected_version": detail["version"],
    })
    assert resp.status_code == 409
    current = resp.json()["current"]
    assert current["version"] == detail["version"] + 1
    assert current["status"] == "待审核"
    # 服务端事实未被旧内容污染
    server = client.get("/api/result/2").json()
    assert server == current


def test_continue_after_conflict_with_refresh_converges(client: TestClient) -> None:
    """冲突后按 409 携带的最新版本重新保存，最终只保留一条事实。"""
    detail = client.get("/api/result/2").json()
    client.post("/api/result/2/actions", json={
        "values": {"action": "提交审核"},
        "expected_version": detail["version"],
    })
    conflict = client.put("/api/result/2", json={
        "values": {"实测值": "试图覆盖"},
        "expected_version": detail["version"],
    })
    latest = conflict.json()["current"]
    # 作废结果允许从待审核执行：用新版本继续操作
    body = client.post("/api/result/2/actions", json={
        "values": {"action": "作废结果"},
        "expected_version": latest["version"],
    }).json()
    assert body["ok"] is True
    assert body["entry"]["status"] == "已作废"
    assert body["entry"]["abnormal"] is True
    listed = [r for r in client.get("/api/result").json()["items"] if r["id"] == 2][0]
    assert listed == body["entry"], "冲突后继续操作，列表与详情仍须收敛到同一版本"


def test_illegal_transition_is_rejected(client: TestClient) -> None:
    # id=1 待录入，不能直接提交审核（必须先录入）
    detail = client.get("/api/result/1").json()
    body = client.post("/api/result/1/actions", json={
        "values": {"action": "提交审核"},
        "expected_version": detail["version"],
    }).json()
    assert body["ok"] is False
    assert client.get("/api/result/1").json()["version"] == detail["version"]


def test_action_idempotent_when_already_in_target_status(client: TestClient) -> None:
    detail = client.get("/api/result/2").json()  # 已录入
    body = client.post("/api/result/2/actions", json={
        "values": {"action": "录入结果"},
        "expected_version": detail["version"],
    }).json()
    assert body["ok"] is True
    assert "无需重复" in body["message"]
    assert client.get("/api/result/2").json()["version"] == detail["version"]


# ---- 半成品/终态保护 ---------------------------------------------------------

def test_voided_entry_cannot_be_edited(client: TestClient) -> None:
    detail = client.get("/api/result/1").json()
    client.post("/api/result/1/actions", json={
        "values": {"action": "作废结果"},
        "expected_version": detail["version"],
    })
    after = client.get("/api/result/1").json()
    resp = client.put("/api/result/1", json={
        "values": {"实测值": "作废后偷改"},
        "expected_version": after["version"],
    }).json()
    assert resp["ok"] is False
    assert client.get("/api/result/1").json()["实测值"] != "作废后偷改"


def test_pending_flag_recomputed_from_status(client: TestClient) -> None:
    detail = client.get("/api/result/2").json()
    body = client.post("/api/result/2/actions", json={
        "values": {"action": "提交审核"},
        "expected_version": detail["version"],
    }).json()
    # 待审核仍属于未完结
    assert body["entry"]["pending"] is True
    overview = client.get("/api/overview").json()
    result_card = [m for m in overview["modules"] if m["name"] == "result"][0]
    assert result_card["pending"] == 3  # 待录入/已录入/待审核 各一，终态才完结


def test_create_entry_persists_and_is_immediately_consistent(client: TestClient) -> None:
    body = client.post("/api/result", json={
        "values": {"结果编号": "RESU-NEW", "所属任务": "TASK-X", "检测项": "pH"},
        "request_key": "key-create",
    }).json()
    assert body["ok"] is True
    new_id = body["entry"]["id"]
    detail = client.get(f"/api/result/{new_id}").json()
    assert detail == body["entry"]
    listed = [r for r in client.get("/api/result").json()["items"] if r["id"] == new_id][0]
    assert listed == body["entry"]
    # 创建同样幂等
    again = client.post("/api/result", json={
        "values": {"结果编号": "RESU-NEW", "所属任务": "TASK-X", "检测项": "pH"},
        "request_key": "key-create",
    }).json()
    assert again["entry"]["id"] == new_id
    assert client.get("/api/result").json()["total"] == 4
