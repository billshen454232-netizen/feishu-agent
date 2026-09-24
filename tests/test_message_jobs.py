import sqlite3

from src.message_jobs import MessageJobStore


def test_create_if_new_persists_queued_job(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()

    job, created = store.create_if_new(
        message_id="om_1",
        event_id="evt_1",
        chat_id="oc_1",
        user_id="ou_1",
        text="你好",
        now=1000,
        chat_type="group",
    )

    assert created is True
    assert job.message_id == "om_1"
    assert job.chat_type == "group"
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.created_at == 1000
    assert store.get("om_1") == job


def test_create_if_new_returns_existing_job_for_duplicate_message_id(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    first, created_first = store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "first", now=1000)

    second, created_second = store.create_if_new("om_1", "evt_2", "oc_1", "ou_1", "duplicate", now=1005)

    assert created_first is True
    assert created_second is False
    assert second == first
    assert store.get("om_1").text == "first"


def test_state_transitions_update_status_attempts_and_error(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "你好", now=1000)

    store.mark_processing("om_1", now=1001)
    processing = store.get("om_1")
    assert processing.status == "processing"
    assert processing.updated_at == 1001

    store.mark_failed("om_1", "AI timeout", now=1002)
    failed = store.get("om_1")
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.last_error == "AI timeout"
    assert failed.updated_at == 1002

    store.mark_done("om_1", now=1003)
    done = store.get("om_1")
    assert done.status == "done"
    assert done.updated_at == 1003


def test_submission_unknown_state_is_not_recoverable_or_expired(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("submitted", "evt", "oc", "ou", "启动 5000 服", now=100)
    store.mark_submission_in_progress("submitted", now=101)
    store.mark_submitted_result_unknown("submitted", "Muliu 已接受但尚未取得终态", now=102)

    recoverable = store.list_recoverable_unfinished(now=10000, recovery_window_seconds=1)
    expired = store.expire_stale_unfinished(now=10000, recovery_window_seconds=1)

    assert recoverable == []
    assert expired == []
    assert store.get("submitted").status == "submitted_result_unknown"


def test_restart_converts_in_progress_submission_to_result_unknown(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("submitted", "evt", "oc", "ou", "启动 5000 服", now=100)
    store.mark_submission_in_progress("submitted", now=101)

    store.expire_stale_unfinished(now=102, recovery_window_seconds=600)

    job = store.get("submitted")
    assert job.status == "submitted_result_unknown"
    assert "机器人重启时无法确认" in job.last_error


def test_recovery_lists_recent_unfinished_and_expires_old_unfinished(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("recent", "evt_recent", "oc_1", "ou_1", "recent text", now=950)
    store.create_if_new("old", "evt_old", "oc_1", "ou_1", "old text", now=300)
    store.mark_processing("old", now=301)
    store.create_if_new("done", "evt_done", "oc_1", "ou_1", "done text", now=900)
    store.mark_done("done", now=901)

    recoverable = store.list_recoverable_unfinished(now=1000, recovery_window_seconds=600)
    expired = store.expire_stale_unfinished(now=1000, recovery_window_seconds=600)

    assert [job.message_id for job in recoverable] == ["recent"]
    assert [job.message_id for job in expired] == ["old"]
    assert store.get("old").status == "expired"
    assert store.get("done").status == "done"


def test_text_confirmation_cannot_claim_card_pending_plan(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt", "oc_group", "ou_requester", "关闭 5000 服", now=1000)
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json='{"kind":"operation","summary":"关闭 5000 服","steps":[]}',
        confirmation_message_id="om_card",
        confirmation_token="ABC123",
        confirmation_method="card",
        now=1001,
    )

    claimed = store.claim_confirmation("oc_group", "ABC123", 600, now=1002)

    assert claimed is None
    assert store.get("om_request").status == "awaiting_confirmation"


def test_card_confirmation_requires_original_requester_and_confirmation_card(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt", "oc_group", "ou_requester", "关闭 5000 服", now=1000)
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json='{"kind":"operation","summary":"关闭 5000 服","steps":[]}',
        confirmation_message_id="om_card",
        confirmation_token="ABC123",
        now=1001,
    )

    wrong_user = store.claim_card_confirmation(
        "om_request", "om_card", "oc_group", "ou_other", "ABC123", 600, now=1002
    )
    wrong_card = store.claim_card_confirmation(
        "om_request", "om_other_card", "oc_group", "ou_requester", "ABC123", 600, now=1002
    )
    claimed = store.claim_card_confirmation(
        "om_request", "om_card", "oc_group", "ou_requester", "ABC123", 600, now=1002
    )

    assert wrong_user is None
    assert wrong_card is None
    assert claimed is not None
    assert store.get("om_request").status == "confirmed"


def test_card_cancel_is_atomic_and_prevents_later_confirmation(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt", "oc_group", "ou_requester", "关闭 5000 服", now=1000)
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json='{"kind":"operation","summary":"关闭 5000 服","steps":[]}',
        confirmation_message_id="om_card",
        confirmation_token="ABC123",
        now=1001,
    )

    cancelled = store.cancel_card_confirmation(
        "om_request", "om_card", "oc_group", "ou_requester", "ABC123", 600, now=1002
    )
    confirmed_after_cancel = store.claim_card_confirmation(
        "om_request", "om_card", "oc_group", "ou_requester", "ABC123", 600, now=1003
    )

    assert cancelled is not None
    assert store.get("om_request").status == "cancelled"
    assert confirmed_after_cancel is None


def test_initialize_migrates_legacy_database_with_empty_chat_type(tmp_path):
    database_path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE message_jobs (
                message_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_error TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO message_jobs (
                message_id, event_id, chat_id, user_id, text, status,
                attempts, created_at, updated_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy", "evt_legacy", "oc_legacy", "ou_legacy", "旧任务", "queued", 0, 1000, 1000, ""),
        )

    store = MessageJobStore(database_path)
    store.initialize()

    job = store.get("legacy")
    assert job is not None
    assert job.chat_type == ""
