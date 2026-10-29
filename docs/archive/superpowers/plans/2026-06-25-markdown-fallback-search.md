# Markdown Fallback Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Markdown full-text fallback search when structured indexes miss, so unindexed knowledge-base files can still be found.

**Architecture:** Keep the current structured index search as the first and fastest layer. Only when index candidates are empty, optionally scan configured Markdown directories under the vault, score files with the existing `_score()` rules, and return matching files as normal `KnowledgeSource` entries.

**Tech Stack:** Python 3.11+, standard library `pathlib`, JSON config, pytest.

## Global Constraints

- Do not add database, FTS5, vector search, or AI ingestion in this phase.
- Preserve current indexed search behavior when indexes match.
- Markdown fallback only runs after index search returns no candidates above `min_score`.
- Markdown fallback must stay inside `vault_path` and only read `.md` / `.txt` files.
- Default fallback directories are exactly `articles`, `concepts`, and `qa`.
- Default `scan_markdown_when_index_miss` is `false` for backward compatibility unless explicitly configured.
- Default `markdown_fallback_max_files` is `1000`.
- Default `markdown_fallback_max_file_chars` is `20000`.
- Use existing `min_score`, `max_results`, and `max_context_chars` behavior.
- Continue using TDD: write failing tests before production changes.
- Do not commit unless the user explicitly asks.

---

## File Structure

- Modify `src/config.py`
  - Add Markdown fallback configuration fields to `KnowledgeBaseConfig` and parse them from `knowledge_base` config.
- Modify `src/knowledge_base.py`
  - Add constructor options for fallback scanning.
  - Add `_search_markdown_files()` and helper methods for safe directory scanning.
  - Invoke Markdown fallback only when indexed search has no eligible candidates.
- Modify `src/app.py`
  - Pass parsed Markdown fallback config into `DualChainVaultKnowledgeBase`.
- Modify `tests/test_config.py`
  - Add tests for fallback config defaults and custom values.
- Modify `tests/test_knowledge_base.py`
  - Add tests for unindexed Markdown fallback, disabled fallback, max file limits, and preserving index priority.
- Modify `config/config.example.json`
  - Add sample fallback config.
- Modify `config/config.json`
  - Add local development fallback config using the same field names.
- Modify `README.md`
  - Document the fallback scanning behavior and config fields.

---

### Task 1: Add Markdown Fallback Configuration

**Files:**
- Modify: `src/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `KnowledgeBaseConfig.scan_markdown_when_index_miss: bool`
- Produces: `KnowledgeBaseConfig.markdown_fallback_dirs: list[str]`
- Produces: `KnowledgeBaseConfig.markdown_fallback_max_files: int`
- Produces: `KnowledgeBaseConfig.markdown_fallback_max_file_chars: int`
- Consumed by later tasks: `src.app.create_app()` passes these values to `DualChainVaultKnowledgeBase`.

- [ ] **Step 1: Write failing default config test**

Append this test to `tests/test_config.py`:

```python
def test_markdown_fallback_config_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.knowledge_base.scan_markdown_when_index_miss is False
    assert config.knowledge_base.markdown_fallback_dirs == ["articles", "concepts", "qa"]
    assert config.knowledge_base.markdown_fallback_max_files == 1000
    assert config.knowledge_base.markdown_fallback_max_file_chars == 20000
```

- [ ] **Step 2: Write failing custom config test**

Append this test to `tests/test_config.py`:

```python
def test_markdown_fallback_config_custom_values(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "knowledge_base": {
            "scan_markdown_when_index_miss": True,
            "markdown_fallback_dirs": ["articles", "runbooks"],
            "markdown_fallback_max_files": 50,
            "markdown_fallback_max_file_chars": 4096
        },
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.knowledge_base.scan_markdown_when_index_miss is True
    assert config.knowledge_base.markdown_fallback_dirs == ["articles", "runbooks"]
    assert config.knowledge_base.markdown_fallback_max_files == 50
    assert config.knowledge_base.markdown_fallback_max_file_chars == 4096
```

- [ ] **Step 3: Run config tests and confirm red**

Run:

```bash
python -m pytest tests/test_config.py -q
```

Expected: fails with `AttributeError` for missing Markdown fallback config fields.

- [ ] **Step 4: Implement config dataclass fields**

In `src/config.py`, update `KnowledgeBaseConfig`:

```python
@dataclass(frozen=True)
class KnowledgeBaseConfig:
    enabled: bool = False
    type: str = "dual_chain_vault"
    vault_path: str = ""
    min_score: int = 3
    max_results: int = 5
    max_context_chars: int = 12000
    fallback_when_miss: str = "answer_with_notice"
    scan_markdown_when_index_miss: bool = False
    markdown_fallback_dirs: list[str] | None = None
    markdown_fallback_max_files: int = 1000
    markdown_fallback_max_file_chars: int = 20000
```

Then add this helper function above `load_config()`:

```python
def _list_str(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return default
    return [str(item) for item in value if isinstance(item, str) and item]
```

Then update `KnowledgeBaseConfig(...)` construction inside `load_config()`:

```python
knowledge_base=KnowledgeBaseConfig(
    enabled=bool(knowledge_base_raw.get("enabled", False)) if isinstance(knowledge_base_raw, dict) else False,
    type=str(knowledge_base_raw.get("type", "dual_chain_vault")) if isinstance(knowledge_base_raw, dict) else "dual_chain_vault",
    vault_path=str(knowledge_base_raw.get("vault_path", "")) if isinstance(knowledge_base_raw, dict) else "",
    min_score=int(knowledge_base_raw.get("min_score", 3)) if isinstance(knowledge_base_raw, dict) else 3,
    max_results=int(knowledge_base_raw.get("max_results", 5)) if isinstance(knowledge_base_raw, dict) else 5,
    max_context_chars=int(knowledge_base_raw.get("max_context_chars", 12000)) if isinstance(knowledge_base_raw, dict) else 12000,
    fallback_when_miss=str(knowledge_base_raw.get("fallback_when_miss", "answer_with_notice")) if isinstance(knowledge_base_raw, dict) else "answer_with_notice",
    scan_markdown_when_index_miss=bool(knowledge_base_raw.get("scan_markdown_when_index_miss", False)) if isinstance(knowledge_base_raw, dict) else False,
    markdown_fallback_dirs=_list_str(knowledge_base_raw.get("markdown_fallback_dirs"), ["articles", "concepts", "qa"]) if isinstance(knowledge_base_raw, dict) else ["articles", "concepts", "qa"],
    markdown_fallback_max_files=int(knowledge_base_raw.get("markdown_fallback_max_files", 1000)) if isinstance(knowledge_base_raw, dict) else 1000,
    markdown_fallback_max_file_chars=int(knowledge_base_raw.get("markdown_fallback_max_file_chars", 20000)) if isinstance(knowledge_base_raw, dict) else 20000,
),
```

- [ ] **Step 5: Run config tests and confirm green**

Run:

```bash
python -m pytest tests/test_config.py -q
```

Expected: all config tests pass.

---

### Task 2: Add Markdown Fallback Search to Knowledge Base

**Files:**
- Modify: `src/knowledge_base.py`
- Modify: `tests/test_knowledge_base.py`

**Interfaces:**
- Consumes: new constructor args from Task 1.
- Produces: `DualChainVaultKnowledgeBase(..., scan_markdown_when_index_miss=False, markdown_fallback_dirs=None, markdown_fallback_max_files=1000, markdown_fallback_max_file_chars=20000)`.
- Produces: private method `_search_markdown_files(query: str) -> list[KnowledgeSource]`.
- Produces: fallback source kind `markdown`.

- [ ] **Step 1: Add unindexed Markdown test helper content**

In `tests/test_knowledge_base.py`, add an unindexed document to `make_vault()` after existing file writes:

```python
    (vault / "articles" / "unindexed.md").write_text(
        "# 未索引部署规范\n正文：蓝绿部署需要先验证新版本健康检查，再切换流量。",
        encoding="utf-8",
    )
```

Do not add this document to `indexes/articles.json`.

- [ ] **Step 2: Write failing fallback hit test**

Append this test to `tests/test_knowledge_base.py`:

```python
def test_search_scans_markdown_when_index_misses(tmp_path):
    kb = DualChainVaultKnowledgeBase(
        vault_path=make_vault(tmp_path),
        min_score=3,
        max_results=3,
        max_context_chars=2000,
        scan_markdown_when_index_miss=True,
        markdown_fallback_dirs=["articles", "concepts", "qa"],
        markdown_fallback_max_files=1000,
        markdown_fallback_max_file_chars=20000,
    )

    result = kb.search("蓝绿部署健康检查")

    assert result.hit is True
    assert result.sources[0].kind == "markdown"
    assert result.sources[0].path == "articles/unindexed.md"
    assert "蓝绿部署需要先验证新版本健康检查" in result.context
```

- [ ] **Step 3: Write failing disabled fallback test**

Append this test to `tests/test_knowledge_base.py`:

```python
def test_search_does_not_scan_markdown_when_fallback_disabled(tmp_path):
    kb = DualChainVaultKnowledgeBase(
        vault_path=make_vault(tmp_path),
        min_score=3,
        max_results=3,
        max_context_chars=2000,
        scan_markdown_when_index_miss=False,
    )

    result = kb.search("蓝绿部署健康检查")

    assert result.hit is False
    assert result.sources == []
```

- [ ] **Step 4: Write failing max files limit test**

Append this test to `tests/test_knowledge_base.py`:

```python
def test_markdown_fallback_respects_max_files(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "articles" / "aaa_first.md").write_text("# 无关文档\n正文：这里没有目标词。", encoding="utf-8")
    (vault / "articles" / "zzz_late.md").write_text("# 限制测试\n正文：限流熔断降级策略。", encoding="utf-8")

    kb = DualChainVaultKnowledgeBase(
        vault_path=vault,
        min_score=3,
        max_results=3,
        max_context_chars=2000,
        scan_markdown_when_index_miss=True,
        markdown_fallback_dirs=["articles"],
        markdown_fallback_max_files=1,
        markdown_fallback_max_file_chars=20000,
    )

    result = kb.search("限流熔断降级")

    assert result.hit is False
```

- [ ] **Step 5: Write indexed search priority regression test**

Append this test to `tests/test_knowledge_base.py`:

```python
def test_markdown_fallback_does_not_run_when_index_hits(tmp_path):
    kb = DualChainVaultKnowledgeBase(
        vault_path=make_vault(tmp_path),
        min_score=3,
        max_results=3,
        max_context_chars=2000,
        scan_markdown_when_index_miss=True,
    )

    result = kb.search("双链增强怎么工作")

    assert result.hit is True
    assert result.sources[0].path == "qa/双链问答.md"
    assert "articles/unindexed.md" not in [source.path for source in result.sources]
```

- [ ] **Step 6: Run knowledge-base tests and confirm red**

Run:

```bash
python -m pytest tests/test_knowledge_base.py -q
```

Expected: fails because `DualChainVaultKnowledgeBase.__init__()` does not accept fallback constructor args.

- [ ] **Step 7: Extend constructor fields**

In `src/knowledge_base.py`, update constructor signature and body:

```python
def __init__(
    self,
    vault_path: str | Path,
    min_score: int = 3,
    max_results: int = 5,
    max_context_chars: int = 12000,
    scan_markdown_when_index_miss: bool = False,
    markdown_fallback_dirs: list[str] | None = None,
    markdown_fallback_max_files: int = 1000,
    markdown_fallback_max_file_chars: int = 20000,
) -> None:
    self.vault_path = Path(vault_path)
    self.min_score = min_score
    self.max_results = max_results
    self.max_context_chars = max_context_chars
    self.scan_markdown_when_index_miss = scan_markdown_when_index_miss
    self.markdown_fallback_dirs = markdown_fallback_dirs or ["articles", "concepts", "qa"]
    self.markdown_fallback_max_files = markdown_fallback_max_files
    self.markdown_fallback_max_file_chars = markdown_fallback_max_file_chars
    self.articles = self._load_json("indexes/articles.json")
    self.concepts = self._load_json("indexes/concepts.json")
    self.qa = self._load_json("indexes/qa.json")
    self._articles_by_id = {str(item.get("id")): item for item in self.articles}
```

- [ ] **Step 8: Split indexed candidate search from final filtering**

In `src/knowledge_base.py`, update the first half of `search()` to this exact structure:

```python
        candidates: list[KnowledgeSource] = []
        candidates.extend(self._search_qa(query))
        candidates.extend(self._search_concepts(query))
        candidates.extend(self._search_articles(query))
        candidates = self._filter_candidates(candidates)
        if not candidates and self.scan_markdown_when_index_miss:
            candidates = self._filter_candidates(self._search_markdown_files(query))
        if not candidates:
            return KnowledgeSearchResult(False, "", [])
```

Add this helper below `_search_articles()`:

```python
    def _filter_candidates(self, candidates: list[KnowledgeSource]) -> list[KnowledgeSource]:
        candidates = self._dedupe_sources(sorted(candidates, key=lambda source: source.score, reverse=True))
        return [source for source in candidates if source.score >= self.min_score][: self.max_results]
```

Remove the old inline filtering lines from `search()`:

```python
        candidates = self._dedupe_sources(sorted(candidates, key=lambda source: source.score, reverse=True))
        candidates = [source for source in candidates if source.score >= self.min_score][: self.max_results]
```

- [ ] **Step 9: Implement Markdown fallback scanning**

Add this method below `_search_articles()`:

```python
    def _search_markdown_files(self, query: str) -> list[KnowledgeSource]:
        results: list[KnowledgeSource] = []
        scanned = 0
        vault_root = self.vault_path.resolve()
        for fallback_dir in self.markdown_fallback_dirs:
            directory = (self.vault_path / fallback_dir).resolve()
            if vault_root not in directory.parents and directory != vault_root:
                continue
            if not directory.exists() or not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if scanned >= self.markdown_fallback_max_files:
                    return results
                if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
                    continue
                resolved_path = path.resolve()
                if vault_root not in resolved_path.parents:
                    continue
                scanned += 1
                relative_path = resolved_path.relative_to(vault_root).as_posix()
                text = path.read_text(encoding="utf-8", errors="replace")[: self.markdown_fallback_max_file_chars]
                score = self._score(query, text)
                if score > 0:
                    title = self._extract_title(text) or path.stem
                    results.append(KnowledgeSource("markdown", relative_path, title, score))
        return results
```

Add this helper near `_read_text()`:

```python
    @staticmethod
    def _extract_title(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""
```

- [ ] **Step 10: Run knowledge-base tests and confirm green**

Run:

```bash
python -m pytest tests/test_knowledge_base.py -q
```

Expected: all knowledge-base tests pass.

---

### Task 3: Wire Markdown Fallback Config into App

**Files:**
- Modify: `src/app.py`
- Test: existing app/config tests

**Interfaces:**
- Consumes: `config.knowledge_base.scan_markdown_when_index_miss`
- Consumes: `config.knowledge_base.markdown_fallback_dirs`
- Consumes: `config.knowledge_base.markdown_fallback_max_files`
- Consumes: `config.knowledge_base.markdown_fallback_max_file_chars`
- Produces: app-created `DualChainVaultKnowledgeBase` with fallback options applied.

- [ ] **Step 1: Update app wiring**

In `src/app.py`, update `DualChainVaultKnowledgeBase(...)` construction:

```python
        knowledge_base = DualChainVaultKnowledgeBase(
            vault_path=config.knowledge_base.vault_path,
            min_score=config.knowledge_base.min_score,
            max_results=config.knowledge_base.max_results,
            max_context_chars=config.knowledge_base.max_context_chars,
            scan_markdown_when_index_miss=config.knowledge_base.scan_markdown_when_index_miss,
            markdown_fallback_dirs=config.knowledge_base.markdown_fallback_dirs,
            markdown_fallback_max_files=config.knowledge_base.markdown_fallback_max_files,
            markdown_fallback_max_file_chars=config.knowledge_base.markdown_fallback_max_file_chars,
        )
```

- [ ] **Step 2: Run app/config tests**

Run:

```bash
python -m pytest tests/test_app.py tests/test_config.py -q
```

Expected: all tests pass.

---

### Task 4: Update Config Files and README

**Files:**
- Modify: `config/config.example.json`
- Modify: `config/config.json`
- Modify: `README.md`

**Interfaces:**
- Produces: documented configuration fields for Markdown fallback.
- Does not change runtime behavior beyond enabling the configured fallback in local config.

- [ ] **Step 1: Update `config/config.example.json`**

Inside the `knowledge_base` object, after `fallback_when_miss`, add a comma and these fields:

```json
"fallback_when_miss": "local_only",
"scan_markdown_when_index_miss": true,
"markdown_fallback_dirs": ["articles", "concepts", "qa"],
"markdown_fallback_max_files": 1000,
"markdown_fallback_max_file_chars": 20000
```

Ensure the JSON remains valid.

- [ ] **Step 2: Update `config/config.json`**

Inside the `knowledge_base` object, add:

```json
"scan_markdown_when_index_miss": true,
"markdown_fallback_dirs": ["articles", "concepts", "qa"],
"markdown_fallback_max_files": 1000,
"markdown_fallback_max_file_chars": 20000
```

Keep the user's current `fallback_when_miss` value unchanged.

- [ ] **Step 3: Update README configuration example**

In `README.md`, update the knowledge-base config example to include:

```json
"fallback_when_miss": "local_only",
"scan_markdown_when_index_miss": true,
"markdown_fallback_dirs": ["articles", "concepts", "qa"],
"markdown_fallback_max_files": 1000,
"markdown_fallback_max_file_chars": 20000
```

- [ ] **Step 4: Add README behavior note**

In the `Obsidian dual-chain vault 知识库` section, add this paragraph:

```markdown
如果 `scan_markdown_when_index_miss` 为 `true`，后端会在结构化索引没有命中时，扫描 `markdown_fallback_dirs` 中的 `.md` / `.txt` 文件作为兜底。该功能用于补漏未进入索引的文档；如果文件数量较多，应观察日志中的 `knowledge_ms`，必要时升级为 SQLite FTS5 全文索引。
```

- [ ] **Step 5: Run JSON/config smoke tests**

Run:

```bash
python -m pytest tests/test_config.py tests/test_app.py -q
```

Expected: all tests pass.

---

### Task 5: Full Verification and Runtime Check

**Files:**
- No required source file changes.

**Interfaces:**
- Consumes all prior tasks.
- Produces verified Markdown fallback search.

- [ ] **Step 1: Run full test suite**

Run:

```bash
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Restart local server**

Find current process using port 8000:

```bash
python - <<'PY'
import subprocess
out = subprocess.check_output(['netstat', '-ano'], text=True, errors='replace')
for line in out.splitlines():
    if ':8000' in line and 'LISTENING' in line:
        print(line)
PY
```

Stop the printed PID with:

```bash
taskkill /PID <PID> /F
```

Start the server:

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 3: Verify health**

Run:

```bash
python - <<'PY'
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())
PY
```

Expected:

```json
{"status":"ok"}
```

- [ ] **Step 4: Runtime verify with an unindexed Markdown document**

Create or identify a Markdown file under the vault, for example:

```text
D:/MySecondBrain/vault/articles/未索引测试.md
```

Containing a unique phrase:

```markdown
# 未索引测试

凤凰灰度发布策略需要先验证影子环境，再切换正式流量。
```

Do not add it to `indexes/articles.json`.

Send this message to the bot:

```text
凤凰灰度发布策略是什么？
```

Expected:

- Bot replies with the local knowledge prefix.
- Reply includes the source path for the unindexed Markdown file.
- Logs show `local_knowledge_hit=True` and `knowledge_ms=...`.

- [ ] **Step 5: Runtime verify miss still works**

Send this message:

```text
一个本地知识库肯定没有的随机问题：fallback-miss-24680 是什么
```

Expected:

- Bot replies with the local-only miss message.
- Logs show `local_knowledge_hit=False fallback=local_only`.
- `ai_ms=0`.

- [ ] **Step 6: Report changed files and verification evidence**

Report:

```text
Changed files:
- src/config.py
- src/knowledge_base.py
- src/app.py
- tests/test_config.py
- tests/test_knowledge_base.py
- config/config.example.json
- config/config.json
- README.md

Verification:
- python -m pytest -q → <N> passed
- /health → {"status":"ok"}
- Runtime fallback query → source path observed
- Runtime miss query → local_only miss observed
```

Do not commit unless the user explicitly asks.

---

## Self-Review Notes

- Spec coverage: The plan implements Stage 1 from `plan.md`: indexed search first, Markdown fallback only after index miss, source paths preserved, local_only behavior preserved, and knowledge timing logs remain useful.
- Placeholder scan: No intentional TBD/TODO placeholders remain. The only `<PID>` token appears in a runtime instruction where the operator must substitute the actual process id printed by the previous command.
- Type consistency: Config fields match across `KnowledgeBaseConfig`, `create_app()`, and `DualChainVaultKnowledgeBase.__init__()`.
- Scope control: This plan does not implement SQLite FTS5, AI ingestion, vector search, or server migration; those remain later phases.
