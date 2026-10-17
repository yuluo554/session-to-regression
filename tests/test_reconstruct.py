"""M3 差分还原器：三级降级、置信度、以及"不许猜"的四条纪律。

判据分三层（缺一层就会出现"看起来有产物"的假绿）：

1. **路径优先级**：① `originalFile` + 非空 `structuredPatch` > ② `old_string`/`new_string`
   在基线上定位 > ③ Codex `FileChange.unified_diff` > ③' Codex patch 文本 > ④ 无 before 镜像；
2. **不许猜**：多处命中 → `unresolved`（不任选一处）；hunk 对不上 → `unresolved`；
   没有全文基线 → `partial`（只给片段，`before`/`after` 为 null，**绝不伪造上下文行**）；
3. **来源决定置信度**：日志内全文 = high、磁盘基线 = medium、片段 = low、取不到 = none。

另加一条隐私纪律的断言：**不给 `--repo` 就不读磁盘**——日志里写着某个绝对路径，
不等于工具获准去读它。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s2r import config, readers, reconstruct, serialize

TS = "2026-01-02T03:04:05.000Z"
SESSION = "sess-unit-0001"
CALC = "/demo/alpha/src/calc.py"


def cfg(repo_root: Path):
    return config.load_config(repo_root / "config" / "scoring.toml")


def limits(repo_root: Path):
    return readers.limits_from_config(cfg(repo_root))


def bug_report(cfg_obj) -> str:
    """用户报告文本：语义词从配置取（测试是消费者，不抄词面）。"""
    return "{}, please look".format(config.vocabulary(cfg_obj)["bug_terms"][0])


# --------------------------------------------------------------------------- #
# 合成日志（只造本文件用得上的最小形态）
# --------------------------------------------------------------------------- #


def claude_line(kind: str, **extra):
    record = {
        "cwd": "/demo/alpha",
        "entrypoint": "cli",
        "isSidechain": False,
        "parentUuid": None,
        "sessionId": SESSION,
        "timestamp": TS,
        "type": kind,
        "userType": "external",
        "uuid": "uuid-unit",
        "version": "2.0.0-demo",
    }
    record.update(extra)
    return record


def claude_report(text: str):
    return claude_line("user", message={"role": "user", "content": text})


def claude_bash(tool_use_id: str, command: str, text: str, is_error: bool = True):
    call = claude_line("assistant", message={"role": "assistant", "content": [{"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {"command": command}}]})
    result = claude_line(
        "user",
        message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": [{"type": "text", "text": text}], "is_error": is_error}]},
    )
    return [call, result]


def claude_edit(
    tool_use_id: str,
    old_string: str,
    new_string: str,
    original_file=None,
    patch=None,
    replace_all: bool = False,
    path: str = CALC,
):
    call = claude_line(
        "assistant",
        message={
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Edit", "input": {"file_path": path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all}}],
        },
    )
    meta = {"filePath": path, "structuredPatch": patch if patch is not None else []}
    if original_file is not None:
        meta["originalFile"] = original_file
    result = claude_line(
        "user",
        message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": [{"type": "text", "text": "ok"}], "is_error": False}]},
        toolUseResult=meta,
    )
    return [call, result]


def claude_write(tool_use_id: str, content: str, path: str = "/demo/alpha/src/helper.py"):
    call = claude_line("assistant", message={"role": "assistant", "content": [{"type": "tool_use", "id": tool_use_id, "name": "Write", "input": {"file_path": path, "content": content}}]})
    result = claude_line(
        "user",
        message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": [{"type": "text", "text": "created"}], "is_error": False}]},
        toolUseResult={"type": "create", "filePath": path, "structuredPatch": []},
    )
    return [call, result]


def codex_session_meta():
    return {
        "ordinal": 1,
        "payload": {
            "id": SESSION,
            "session_id": SESSION,
            "cli_version": "0.142.5-demo",
            "cwd": "/demo/alpha",
            "timestamp": TS,
            "model_provider": "demo-provider",
            "originator": "demo_cli",
            "source": "cli",
            "thread_source": "user",
        },
        "timestamp": TS,
        "type": "session_meta",
    }


def codex_apply_patch(patch_text: str, call_id: str = "call-patch"):
    return {
        "ordinal": 2,
        "payload": {"type": "custom_tool_call", "name": "apply_patch", "call_id": call_id, "status": "completed", "input": patch_text},
        "timestamp": TS,
        "type": "response_item",
    }


def codex_file_change(changes):
    return {
        "ordinal": 3,
        "payload": {"type": "item_completed", "item": {"id": "item-unit", "type": "FileChange", "status": "completed", "stdout": "", "changes": changes}},
        "timestamp": TS,
        "type": "event_msg",
    }


def codex_failing_run():
    call = {
        "ordinal": 4,
        "payload": {"type": "function_call", "name": "shell_command", "call_id": "call-run", "arguments": json.dumps({"command": "python -m pytest tests/test_calc.py -q"})},
        "timestamp": TS,
        "type": "response_item",
    }
    output = {
        "ordinal": 5,
        "payload": {"type": "function_call_output", "call_id": "call-run", "output": "Process exited with code 1\nOutput:\n"},
        "timestamp": TS,
        "type": "response_item",
    }
    return [call, output]


def write_log(tmp_path: Path, records, name: str = "session.jsonl") -> Path:
    target = tmp_path / name
    with open(str(target), "w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(serialize.dumps_json_line(record))
    return target


def write_text(path: Path, text: str) -> Path:
    """3.8 通道：`Path.write_text(newline=...)` 是 3.10+ 参数，只能用 `open(..., newline=...)`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return path


def read_session(tmp_path: Path, records, repo_root: Path, name: str = "session.jsonl"):
    path = write_log(tmp_path, records, name)
    return readers.read_file(path, limits(repo_root))


def run(tmp_path, repo_root, records, repo=None, name: str = "session.jsonl"):
    file_read = read_session(tmp_path, records, repo_root, name)
    return reconstruct.reconstruct_session(file_read, cfg(repo_root), repo=repo)


def only(tmp_path, repo_root, records, repo=None, name: str = "session.jsonl"):
    result = run(tmp_path, repo_root, records, repo=repo, name=name)
    assert len(result.files) == 1, [item.to_dict() for item in result.files]
    return result.files[0]


# --------------------------------------------------------------------------- #
# ① originalFile + 非空 structuredPatch
# --------------------------------------------------------------------------- #

BEFORE = "def divide(a, b):\n    return a / b\n"
AFTER = "def divide(a, b):\n    return a / b if b else 0\n"
HUNK = [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": [" def divide(a, b):", "-    return a / b", "+    return a / b if b else 0"]}]


def test_structured_patch_is_the_first_priority(tmp_path, repo_root):
    """① 与 ② 都成立时走 ①：patch 带行号，可唯一定位；② 单独看本来会 unresolve。

    构造：`originalFile` 里 `old_string` 出现 **2** 次（② 必 `unresolved`），而结构化补丁
    明确指向第 1 行那处 → 结果必须是 ①（`ok` + `claude_structured_patch`）。
    这条就是"优先级"的可证伪形态：把优先级写反 → 测试当场红。
    """
    original = "def divide(a, b):\n    return a / b\n\n\ndef scale(a, b):\n    return a / b\n"
    item = only(
        tmp_path,
        repo_root,
        [claude_report(bug_report(cfg(repo_root)))] + claude_edit("toolu-1", "    return a / b", "    return a / b if b else 0", original_file=original, patch=HUNK),
    )
    assert item.status == reconstruct.STATUS_OK
    assert item.source == reconstruct.SOURCE_CLAUDE_PATCH
    assert item.confidence == reconstruct.CONFIDENCE_HIGH
    assert item.before == original
    assert item.after == "def divide(a, b):\n    return a / b if b else 0\n\n\ndef scale(a, b):\n    return a / b\n"
    assert item.evidence["aligned_by"] == reconstruct.ALIGN_LINE_NUMBER


def test_structured_patch_is_not_trusted_when_the_hunk_does_not_match(tmp_path, repo_root):
    """hunk 的旧侧在原文件里找不到 → `unresolved`（不硬套、不任选）。"""
    patch = [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": [" def divide(a, b):", "-    return a * b", "+    return a / b if b else 0"]}]
    item = only(
        tmp_path,
        repo_root,
        [claude_report(bug_report(cfg(repo_root)))] + claude_edit("toolu-1", "    return a / b", "    return a / b if b else 0", original_file=BEFORE, patch=patch),
    )
    assert item.status == reconstruct.STATUS_UNRESOLVED
    assert item.reason == reconstruct.REASON_HUNK_MISMATCH
    assert item.before is None and item.after is None


def test_empty_structured_patch_is_not_a_diff(tmp_path, repo_root):
    """K14 的非空版本：空 `structuredPatch` 不算 diff，判定落到 ② 路径。"""
    item = only(
        tmp_path,
        repo_root,
        [claude_report(bug_report(cfg(repo_root)))] + claude_edit("toolu-1", "    return a / b", "    return a / b if b else 0", original_file=BEFORE, patch=[]),
    )
    assert item.source == reconstruct.SOURCE_CLAUDE_OLD_STRING
    assert item.status == reconstruct.STATUS_OK
    assert (item.before, item.after) == (BEFORE, AFTER)


# --------------------------------------------------------------------------- #
# ② old_string / new_string 定位
# --------------------------------------------------------------------------- #


def test_ambiguous_old_string_is_unresolved(tmp_path, repo_root):
    """多处命中且无法唯一定位 → `unresolved`，**不得任选一处**。"""
    original = "def a(value):\n    return value\n\n\ndef b(value):\n    return value\n"
    item = only(
        tmp_path,
        repo_root,
        [claude_report(bug_report(cfg(repo_root)))] + claude_edit("toolu-1", "    return value", "    return round(value, 2)", original_file=original),
    )
    assert item.status == reconstruct.STATUS_UNRESOLVED
    assert item.reason == reconstruct.REASON_AMBIGUOUS
    assert item.confidence == reconstruct.CONFIDENCE_NONE
    assert item.evidence["occurrences"] == 2
    assert item.before is None and item.after is None


def test_replace_all_resolves_every_match(tmp_path, repo_root):
    """`replace_all: true` 时多处命中是**明确**语义（全替换），不构成歧义。"""
    original = "def a(value):\n    return value\n\n\ndef b(value):\n    return value\n"
    item = only(
        tmp_path,
        repo_root,
        [claude_report(bug_report(cfg(repo_root)))] + claude_edit("toolu-1", "    return value", "    return round(value, 2)", original_file=original, replace_all=True),
    )
    assert item.status == reconstruct.STATUS_OK
    assert item.after == "def a(value):\n    return round(value, 2)\n\n\ndef b(value):\n    return round(value, 2)\n"
    assert len(item.hunks) == 2, "全替换应逐处给出 hunk（下游据此定位被改的符号）"
    assert item.evidence["occurrences"] == 2


def test_old_string_missing_from_the_base_is_unresolved(tmp_path, repo_root):
    """基线里根本没有这段文本 → `unresolved`（对不上就是不知道，不是"换一处试试"）。"""
    item = only(
        tmp_path,
        repo_root,
        [claude_report(bug_report(cfg(repo_root)))] + claude_edit("toolu-1", "    return missing", "    return other", original_file=BEFORE),
    )
    assert item.status == reconstruct.STATUS_UNRESOLVED
    assert item.reason == reconstruct.REASON_NOT_FOUND


def test_disk_base_is_used_only_with_an_explicit_repo(tmp_path, repo_root, monkeypatch):
    """隐私纪律：不给 `--repo` 就不读磁盘——日志里写着绝对路径不等于获准去读它。

    测试怎么才**可证伪**：把"相对 CWD 就是那个路径"的基线摆好，再 monkeypatch.chdir 过去——
    于是"不给 --repo 就用 CWD 兜底"这种实现会当场读到它并变绿，本断言随红。
    （只断言"文件不存在所以读不到"是假门：实现根本没去读也照样过。）
    """
    monkeypatch.chdir(tmp_path)
    write_text(tmp_path / "demo" / "alpha" / "src" / "calc.py", BEFORE)
    records = [claude_report(bug_report(cfg(repo_root)))] + claude_edit("toolu-1", "    return a / b", "    return a / b if b else 0", original_file=None)

    without = only(tmp_path, repo_root, records, repo=None, name="a.jsonl")
    assert without.status == reconstruct.STATUS_NO_BEFORE
    assert without.evidence["confidence_base"] == reconstruct.BASE_NONE

    with_repo = only(tmp_path, repo_root, records, repo=str(tmp_path), name="b.jsonl")
    assert with_repo.status == reconstruct.STATUS_OK
    assert with_repo.source == reconstruct.SOURCE_CLAUDE_OLD_STRING
    assert with_repo.confidence == reconstruct.CONFIDENCE_MEDIUM
    assert (with_repo.before, with_repo.after) == (BEFORE, AFTER)


def test_same_session_write_content_is_a_before_base(tmp_path, repo_root):
    """同会话更早的 `Write.content` 也能当基线（新建之后又被 Edit 的文件）。"""
    content = "def helper(value):\n    return value\n"
    records = (
        [claude_report(bug_report(cfg(repo_root)))]
        + claude_write("toolu-w", content)
        + claude_edit("toolu-e", "    return value", "    return value.strip()", original_file=None, path="/demo/alpha/src/helper.py")
    )
    result = run(tmp_path, repo_root, records)
    edited = [item for item in result.files if item.file == "/demo/alpha/src/helper.py"][-1]
    assert edited.status == reconstruct.STATUS_OK
    assert edited.before == content
    assert edited.confidence == reconstruct.CONFIDENCE_HIGH, "同会话 Write 属日志内全文基线"
    assert edited.evidence["base_detail"] == "session_write"


def test_create_write_reports_no_before_image(tmp_path, repo_root):
    """纯新建：before 不存在（不是"还没算出来"）→ `no_before_image`，after 取自日志 content。"""
    content = "def helper(value):\n    return value\n"
    item = only(tmp_path, repo_root, [claude_report(bug_report(cfg(repo_root)))] + claude_write("toolu-w", content))
    assert item.status == reconstruct.STATUS_NO_BEFORE
    assert item.source == reconstruct.SOURCE_CLAUDE_WRITE
    assert item.confidence == reconstruct.CONFIDENCE_NONE
    assert item.before is None
    assert item.after == content, "after 是日志里的事实，不该因为缺 before 就丢掉"


# --------------------------------------------------------------------------- #
# ③ Codex：结构化通道优先，patch 文本兜底
# --------------------------------------------------------------------------- #

CODEX_BASE = "def first(items):\n    return items[0]\n"
CODEX_AFTER = "def first(items):\n    return items[0] if items else None\n"
CODEX_PATCH = "*** Begin Patch\n*** Update File: " + CALC + "\n@@\n-    return items[0]\n+    return items[0] if items else None\n*** End Patch"
CODEX_DIFF = "@@ -2 +2 @@\n-    return items[0]\n+    return items[0] if items else None\n"


def repo_with(tmp_path: Path, content: str) -> Path:
    root = tmp_path / "worktree"
    target = root / "demo" / "alpha" / "src" / "calc.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, content)
    return root


def test_codex_unified_diff_is_preferred_over_patch_text(tmp_path, repo_root):
    """同一文件两条通道都在 → 结构化通道胜出，并把"还有哪些通道"记进证据。"""
    records = [codex_session_meta(), codex_apply_patch(CODEX_PATCH), codex_file_change({CALC: {"type": "update", "unified_diff": CODEX_DIFF}})]
    item = only(tmp_path, repo_root, records, repo=str(repo_with(tmp_path, CODEX_BASE)))
    assert item.source == reconstruct.SOURCE_CODEX_DIFF
    assert item.status == reconstruct.STATUS_OK
    assert (item.before, item.after) == (CODEX_BASE, CODEX_AFTER)
    assert item.confidence == reconstruct.CONFIDENCE_MEDIUM, "磁盘基线属工作区状态，可能是后续改动过的"
    assert item.evidence["channels"] == ["apply_patch", "file_change"]


def test_codex_patch_text_uses_the_line_number_when_the_diff_has_one(tmp_path, repo_root):
    """patch 文本不带行号 → 只能按内容唯一定位（`aligned_by` 如实记录凭什么认为在这个位置）。"""
    item = only(tmp_path, repo_root, [codex_session_meta(), codex_apply_patch(CODEX_PATCH)], repo=str(repo_with(tmp_path, CODEX_BASE)))
    assert item.source == reconstruct.SOURCE_CODEX_PATCH
    assert item.status == reconstruct.STATUS_OK
    assert item.evidence["aligned_by"] == reconstruct.ALIGN_CONTENT_SEARCH
    assert (item.before, item.after) == (CODEX_BASE, CODEX_AFTER)


def test_codex_without_a_base_is_partial_and_does_not_fabricate_context(tmp_path, repo_root):
    """没有全文基线 → `partial`：只给日志里真实出现的行，`before`/`after` 为 null。"""
    item = only(tmp_path, repo_root, [codex_session_meta(), codex_apply_patch(CODEX_PATCH)], repo=None)
    assert item.status == reconstruct.STATUS_PARTIAL
    assert item.reason == reconstruct.REASON_BASE_MISSING
    assert item.confidence == reconstruct.CONFIDENCE_LOW
    assert item.before is None and item.after is None
    assert len(item.hunks) == 1
    assert item.hunks[0]["old_lines"] == ["    return items[0]"], "片段只能是日志里出现过的行"
    assert item.hunks[0]["old_start"] is None, "patch 文本没有行号，不许编一个"


def test_codex_drifted_base_degrades_to_partial(tmp_path, repo_root):
    """磁盘基线已被改动（hunk 对不上）→ 如实降级，**不硬套**。"""
    drifted = "def first(items):\n    return items[-1]\n"
    item = only(tmp_path, repo_root, [codex_session_meta(), codex_apply_patch(CODEX_PATCH)], repo=str(repo_with(tmp_path, drifted)))
    assert item.status == reconstruct.STATUS_PARTIAL
    assert item.reason == reconstruct.REASON_BASE_MISMATCH
    assert item.before is None and item.after is None
    assert item.evidence["mismatch"] == reconstruct.REASON_HUNK_MISMATCH


def test_codex_unique_content_search_still_reconstructs(tmp_path, repo_root):
    """基线整体漂移但目标片段**唯一**可定位 → 仍然还原（唯一 = 不是猜）。"""
    shifted = "# demo header\n\ndef first(items):\n    return items[0]\n"
    item = only(tmp_path, repo_root, [codex_session_meta(), codex_apply_patch(CODEX_PATCH)], repo=str(repo_with(tmp_path, shifted)))
    assert item.status == reconstruct.STATUS_OK
    assert item.before == shifted
    assert item.after == "# demo header\n\ndef first(items):\n    return items[0] if items else None\n"
    assert item.evidence["aligned_by"] == reconstruct.ALIGN_CONTENT_SEARCH


def test_codex_ambiguous_hunk_is_not_placed(tmp_path, repo_root):
    """片段在基线里出现多次 → `partial` + `ambiguous_match`（不任选一处）。"""
    repeated = "def first(items):\n    return items[0]\n\n\ndef last(items):\n    return items[0]\n"
    item = only(tmp_path, repo_root, [codex_session_meta(), codex_apply_patch(CODEX_PATCH)], repo=str(repo_with(tmp_path, repeated)))
    assert item.status == reconstruct.STATUS_PARTIAL
    assert item.reason == reconstruct.REASON_AMBIGUOUS
    assert item.before is None and item.after is None


def test_codex_add_file_has_no_before_image(tmp_path, repo_root):
    """`*** Add File:`：before 不存在（新建），after 由正文行还原（结尾换行按行语义补）。"""
    patch = "*** Begin Patch\n*** Add File: /demo/alpha/src/helper.py\n+def helper(value):\n+    return value\n*** End Patch"
    item = only(tmp_path, repo_root, [codex_session_meta(), codex_apply_patch(patch)])
    assert item.status == reconstruct.STATUS_NO_BEFORE
    assert item.source == reconstruct.SOURCE_CODEX_PATCH
    assert item.before is None
    assert item.after == "def helper(value):\n    return value\n"
    assert item.evidence["action"] == "add"


def test_codex_file_change_content_is_a_new_file(tmp_path, repo_root):
    """结果侧 `content` 通道（实测 1274 条）同样是"新增文件"语义。"""
    records = [codex_session_meta(), codex_file_change({"/demo/alpha/src/helper.py": {"type": "add", "content": "def helper(value):\n    return value\n"}})]
    item = only(tmp_path, repo_root, records)
    assert item.status == reconstruct.STATUS_NO_BEFORE
    assert item.source == reconstruct.SOURCE_CODEX_CONTENT
    assert item.after == "def helper(value):\n    return value\n"


# --------------------------------------------------------------------------- #
# episode 级结论 + 确定性
# --------------------------------------------------------------------------- #


def test_episode_is_marked_unverified_without_a_before_image(tmp_path, repo_root):
    """`no_before_image` 必须浮到 episode 层（该文件不进生成路径）。"""
    records = (
        [claude_report(bug_report(cfg(repo_root)))]
        + claude_bash("toolu-run", "python -m pytest tests/test_format.py -q", "ImportError: cannot import name helper")
        + claude_write("toolu-w", "def helper(value):\n    return value\n")
    )
    result = run(tmp_path, repo_root, records)
    assert result.episodes, "会话里应切出 episode"
    assert all(item["status"] == "unverified" for item in result.episodes)
    assert all(item["reason"] == reconstruct.REASON_NO_BEFORE for item in result.episodes)


def test_reconstruction_is_byte_identical_across_runs(tmp_path, repo_root):
    """同一日志 + 同一磁盘状态 → 同一 diff（K5：双跑逐字节一致）。"""
    records = [codex_session_meta(), codex_apply_patch(CODEX_PATCH)]
    repo = str(repo_with(tmp_path, CODEX_BASE))
    path = write_log(tmp_path, records)
    limits_obj = limits(repo_root)
    cfg_obj = cfg(repo_root)
    first = reconstruct.reconstruct_session(readers.read_file(path, limits_obj), cfg_obj, repo=repo).to_json()
    second = reconstruct.reconstruct_session(readers.read_file(path, limits_obj), cfg_obj, repo=repo).to_json()
    assert first == second
    assert reconstruct.SCHEMA_VERSION == json.loads(first)["schema_version"]


def test_unknown_values_in_the_contract_are_rejected():
    """枚举越界即报错（别让拼错的状态/来源静默流过对账面）。"""
    from s2r.errors import InputUnavailable

    with pytest.raises(InputUnavailable):
        reconstruct.Reconstruction(file="x", before="a", after="b", hunks=[], source=reconstruct.SOURCE_NONE, confidence=reconstruct.CONFIDENCE_NONE, status="looks_fine")
    with pytest.raises(InputUnavailable):
        reconstruct.Reconstruction(file="x", before="a", after="b", hunks=[], source="made_up", confidence=reconstruct.CONFIDENCE_NONE)
    with pytest.raises(InputUnavailable):
        reconstruct.Reconstruction(file="x", before=None, after="b", hunks=[], source=reconstruct.SOURCE_NONE, confidence=reconstruct.CONFIDENCE_NONE, status=reconstruct.STATUS_OK)


def test_session_reference_resolution(tmp_path, repo_root):
    """会话引用：文件路径 / `<source>:<session_id>` / 认不出来的形态（显式报错，不猜）。"""
    from s2r.errors import InputUnavailable

    path = write_log(tmp_path, [claude_report("hello")])
    assert reconstruct.resolve_session_file(str(path), limits(repo_root)) == path
    found = reconstruct.resolve_session_file("claude:" + SESSION, limits(repo_root), {"claude": str(tmp_path)})
    assert found == path
    with pytest.raises(InputUnavailable):
        reconstruct.resolve_session_file("sess-demo-0001", limits(repo_root))
    with pytest.raises(InputUnavailable):
        reconstruct.resolve_session_file("claude:" + SESSION, limits(repo_root), {"claude": str(tmp_path / "nope")})
