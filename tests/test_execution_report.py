from src.execution_report import (
    ExecutionReportStore,
    compute_error_signature,
    extract_key_error_lines,
    is_read_only_result,
    normalize_error_text,
)
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult


def _failure(args, log, reason="脚本退出码非 0（exit code 255）", path="/home/serverGeneralScript/exc_server_merge.sh"):
    return MuliuExecutionResult(
        "合服",
        [MuliuStepResult(1, "合服", path, list(args), False, log)],
        failed_step_number=1,
        failure_reason=reason,
    )


SSH_LOG = (
    "start merge {sid}\n"
    "{ts} ssh: connect to host {ip} port 22: Connection refused\n"
    "exit code 255"
)


def test_same_error_on_other_server_and_time_has_same_fingerprint():
    a = compute_error_signature(_failure(["2213", "2018", "17"], SSH_LOG.format(sid="2213", ts="2026-11-02 11:40:01", ip="10.1.2.3")))
    b = compute_error_signature(_failure(["3001", "2018", "18"], SSH_LOG.format(sid="3001", ts="2026-11-03 09:12:45", ip="10.9.8.7")))

    assert a.fingerprint == b.fingerprint
    assert a.failure_category == "exit_code=255"
    assert a.script_path == "/home/serverGeneralScript/exc_server_merge.sh"
    assert all("2213" not in line and "10.1.2.3" not in line for line in a.key_error_lines)


def test_different_error_or_script_has_different_fingerprint():
    base = compute_error_signature(_failure(["2213"], SSH_LOG.format(sid="2213", ts="2026-11-02 11:40:01", ip="10.1.2.3")))
    other_error = compute_error_signature(_failure(["2213"], "配置生成失败：缺少 combine_demo_new.lua", reason="未检测到合服完成文本"))
    other_script = compute_error_signature(
        _failure(["2213"], SSH_LOG.format(sid="2213", ts="2026-11-02 11:40:01", ip="10.1.2.3"), path="/home/serverGeneralScript/start.sh")
    )

    assert len({base.fingerprint, other_error.fingerprint, other_script.fingerprint}) == 3


def test_key_lines_prefer_keyword_lines_then_fall_back_to_tail():
    keyword = extract_key_error_lines("a\nb\nERROR: disk full\nc\nd")
    tail = extract_key_error_lines("l1\nl2\nl3\nl4\nl5")

    assert keyword == ("ERROR: disk full",)
    assert tail == ("l3", "l4", "l5")
    assert extract_key_error_lines("") == ()


def test_normalization_keeps_exit_codes_and_names_but_drops_volatile_values():
    text = normalize_error_text(
        "2026-11-02 11:40:01 exc_server_merge.sh exit code 255 pid 123456 uuid 123e4567-e89b-12d3-a456-426614174000 /tmp/x.lua",
        variable_values=["2213"],
    )

    assert "exc_server_merge.sh" in text
    assert "exit code 255" in text
    assert "123456" not in text and "/tmp/x.lua" not in text and "123e4567" not in text
    assert "<TIME>" in text


def test_is_read_only_requires_every_step_to_be_read():
    class Contract:
        def __init__(self, path, risk):
            self.path, self.risk = path, risk

        def matches(self, step):
            return step.path == self.path

    class Contracts:
        contracts = [Contract("/r.sh", "read"), Contract("/w.sh", "write")]

    read = MuliuExecutionResult("q", [MuliuStepResult(1, "q", "/r.sh", [], True, "")])
    mixed = MuliuExecutionResult("q", [MuliuStepResult(1, "q", "/r.sh", [], True, ""), MuliuStepResult(2, "w", "/w.sh", [], True, "")])
    unregistered = MuliuExecutionResult("q", [MuliuStepResult(1, "q", "/x.sh", [], True, "")])

    assert is_read_only_result(read, Contracts()) is True
    assert is_read_only_result(mixed, Contracts()) is False
    assert is_read_only_result(unregistered, Contracts()) is False
    assert is_read_only_result(read, None) is False


def test_store_candidate_review_lifecycle(tmp_path):
    store = ExecutionReportStore(tmp_path / "db.sqlite3")
    store.initialize()
    signature = compute_error_signature(_failure(["2213"], "ERROR x"))

    pattern = store.add_candidate(signature, "诊断A", "om_1", "cn 2213", now=100)
    again = store.add_candidate(signature, "诊断B", "om_2", "cn 3001", now=200)
    assert again.pattern_id == pattern.pattern_id and again.diagnosis == "诊断A"

    hit = store.record_pattern_hit(pattern.pattern_id, "om_3", "overseas 3001", now=300)
    assert hit.hit_count == 1 and hit.last_seen_at == 300
    assert hit.seen_scopes == ("cn 2213", "overseas 3001")
    assert hit.sample_message_ids == ("om_1", "om_3")

    assert store.set_pattern_status([pattern.pattern_id], "verified", now=400) == 1
    assert [p.pattern_id for p in store.list_patterns("verified")] == [pattern.pattern_id]
    assert store.get_pattern(pattern.pattern_id).approved_at == 400
    store.set_pattern_status([pattern.pattern_id], "candidate")
    assert store.get_pattern(pattern.pattern_id).approved_at is None
    assert store.update_diagnosis(pattern.pattern_id, "改过的诊断")
    assert store.find_pattern(signature.fingerprint).diagnosis == "改过的诊断"
    assert store.count_patterns() == {"candidate": 1, "verified": 0}

    assert store.delete_patterns([pattern.pattern_id]) == 1
    assert store.find_pattern(signature.fingerprint) is None


def test_store_read_success_mode_defaults_to_ai(tmp_path):
    store = ExecutionReportStore(tmp_path / "db.sqlite3")
    store.initialize()

    assert store.read_success_mode() == "ai"
    store.set_read_success_mode("template")
    assert store.read_success_mode() == "template"


def test_store_recovery_gives_up_after_max_attempts_and_prunes(tmp_path):
    store = ExecutionReportStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue_interpretation("om_1", "failure", {"summary": "s", "step_results": []}, now=0)
    for _ in range(3):
        store.claim_interpretation("om_1")
        store.recover_interpretations()

    assert store.recover_interpretations() == []
    assert store.get_interpretation("om_1").status == "failed"

    store.record_artifact("om_old", "failed", "template", "x", now=0)
    store.record_artifact("om_new", "failed", "template", "y", now=100 * 86400)
    assert store.prune_artifacts(90, now=100 * 86400) == 1
    assert [a["message_id"] for a in store.list_artifacts(["om_old", "om_new"])] == ["om_new"]


def test_store_masks_secrets_in_persisted_payload(tmp_path):
    store = ExecutionReportStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.enqueue_interpretation(
        "om_1",
        "failure",
        {"summary": "s", "failure_reason": "password=hunter2", "step_results": [{"log": "Authorization: Bearer tok123", "description": "d"}]},
    )

    payload = store.get_interpretation("om_1").payload_json
    assert "hunter2" not in payload and "tok123" not in payload


def test_initialize_migrates_old_pattern_table_without_outcome(tmp_path):
    import sqlite3

    db = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE execution_error_patterns (pattern_id INTEGER PRIMARY KEY AUTOINCREMENT, script_path TEXT NOT NULL, "
        "error_fingerprint TEXT NOT NULL UNIQUE, failure_category TEXT NOT NULL, normalized_reason TEXT NOT NULL, "
        "key_error_lines TEXT NOT NULL, diagnosis TEXT NOT NULL, status TEXT NOT NULL, seen_scopes_json TEXT NOT NULL DEFAULT '[]', "
        "sample_message_ids TEXT NOT NULL DEFAULT '[]', first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, "
        "hit_count INTEGER NOT NULL DEFAULT 0, approved_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO execution_error_patterns (script_path, error_fingerprint, failure_category, normalized_reason, "
        "key_error_lines, diagnosis, status, first_seen_at, last_seen_at) VALUES ('/a.sh','fp','x','r','[]','d','candidate',1,1)"
    )
    conn.commit()
    conn.close()

    store = ExecutionReportStore(db)
    store.initialize()

    assert store.find_pattern("fp").outcome == "failure"
