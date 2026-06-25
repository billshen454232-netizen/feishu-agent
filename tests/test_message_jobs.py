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
    )

    assert created is True
    assert job.message_id == "om_1"
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
