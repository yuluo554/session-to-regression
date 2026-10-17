"""M1 读取器单元测试：Claude / Codex 两种形态 → 统一事件流。

判据（都来自 data/README.md §六 的实测结论）：
* **seq = 源文件行号**、`source_offset` = 该行起始字节偏移（可反查原文）；
* 逐字节关键的块（old_string/new_string/content/originalFile/patch 全文）**只留引用**，不复制；
* 失败判定**只在数值上做**（`Exit code N` / `Process exited with code N`，N≠0）；
* 不认识的形态**计注记**（可见），首行不可解析 / 未知 harness / 版本指纹不匹配 → 显式报错。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s2r import config, events, readers, serialize
from s2r.errors import InputUnavailable, UnsupportedFormat
from s2r.readers import claude as claude_reader
from s2r.readers import codex as codex_reader

SESSION = "sess-demo-0009"
VISIBLE = "/demo/alpha/src/calc.py"


def limits(repo_root: Path, **overrides):
    base = dict(readers.limits_from_config(config.load_config(repo_root / "config" / "scoring.toml")))
    base.update(overrides)
    return base


def write_jsonl(path: Path, lines) -> Path:
    """写 jsonl：**一行一对象、LF**（测试夹具也守 jsonl 的行边界契约）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        for line in lines:
            stream.write(serialize.dumps_json_line(line))
    return path


def claude_line(kind: str, **extra):
    record = {
        "cwd": "/demo/alpha",
        "entrypoint": "cli",
        "isSidechain": False,
        "parentUuid": None,
        "sessionId": SESSION,
        "timestamp": "2026-01-02T03:04:05.000Z",
        "type": kind,
        "userType": "external",
        "uuid": "uuid-demo",
        "version": "2.0.0-demo",
    }
    record.update(extra)
    return record


def codex_line(payload, line_type="response_item"):
    return {"ordinal": 1, "payload": payload, "timestamp": "2026-01-02T03:04:05.000Z", "type": line_type}


def codex_meta():
    return codex_line(
        {
            "id": SESSION,
            "session_id": SESSION,
            "cli_version": "0.142.5-demo",
            "cwd": "/demo/alpha",
            "model_provider": "demo",
            "originator": "demo_cli",
            "source": "cli",
            "thread_source": "user",
            "timestamp": "2026-01-02T03:04:05.000Z",
        },
        "session_meta",
    )


# --------------------------------------------------------------------------- #
# Claude
# --------------------------------------------------------------------------- #


def test_claude_user_text_string_becomes_user_msg(tmp_path, repo_root):
    path = write_jsonl(tmp_path / "c.jsonl", [claude_line("user", message={"role": "user", "content": "demo bug report"})])
    result = readers.read_file(path, limits(repo_root))
    assert result.source == "claude"
    assert result.format_version == "cc-jsonl-1"
    assert result.harness_version == "2.0.0-demo"
    kinds = [event.kind for event in result.events]
    assert kinds == ["user_msg"]
    assert result.events[0].payload["text"] == "demo bug report"


def test_claude_meta_first_line_still_detected(tmp_path, repo_root):
    """实测：Claude 文件首行常是 UI 状态行（只有 sessionId/type）——探测窗口必须容得下。"""
    path = write_jsonl(
        tmp_path / "c.jsonl",
        [
            {"type": "last-prompt", "lastPrompt": "demo", "leafUuid": "uuid-demo", "sessionId": SESSION},
            claude_line("user", message={"role": "user", "content": "demo bug report"}),
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    assert result.source == "claude"
    assert result.notes["line_type:last-prompt"] == 1
    assert [event.kind for event in result.events] == ["user_msg"]


def test_claude_tool_call_keeps_references_not_big_text(tmp_path, repo_root):
    path = write_jsonl(
        tmp_path / "c.jsonl",
        [
            claude_line(
                "assistant",
                message={
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu-1", "name": "Edit", "input": {"file_path": VISIBLE, "old_string": "old body", "new_string": "new body", "replace_all": False}}],
                },
            )
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    event = result.events[0]
    assert (event.kind, event.tool, event.path) == ("tool_call", "Edit", VISIBLE)
    payload = event.payload
    assert payload["has_old_string"] is True and payload["has_new_string"] is True
    assert "old_string" not in payload, "逐字节关键的块只留引用（原文由 seq 回溯）"
    assert payload["old_string_chars"] == len("old body")
    assert payload["replace_all"] is False


def test_claude_tool_result_flags_before_image_by_key_presence(tmp_path, repo_root):
    """E1/K14：判据是"键存在"，不是 type == 'update'（全量实测 update 只有 4 条）。"""
    path = write_jsonl(
        tmp_path / "c.jsonl",
        [
            claude_line(
                "user",
                message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu-1", "content": [{"type": "text", "text": "updated"}], "is_error": False}]},
                toolUseResult={
                    "filePath": VISIBLE,
                    "originalFile": "def divide(a, b):\n    return a / b\n",
                    "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": ["-x", "+y"]}],
                },
            )
        ],
    )
    event = readers.read_file(path, limits(repo_root)).events[0]
    assert event.kind == "tool_result"
    assert event.payload["has_original_file"] is True
    assert event.payload["structured_patch_hunks"] == 1
    assert event.payload["file_path"] == VISIBLE
    assert event.payload["result_type"] == events.UNKNOWN, "type 键缺失时落 unknown，不猜"


def test_claude_tool_result_without_before_image_is_explicit(tmp_path, repo_root):
    path = write_jsonl(
        tmp_path / "c.jsonl",
        [
            claude_line(
                "user",
                message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu-2", "content": "created"}]},
                toolUseResult={"type": "create", "filePath": VISIBLE, "structuredPatch": []},
            )
        ],
    )
    event = readers.read_file(path, limits(repo_root)).events[0]
    assert event.payload["has_original_file"] is False
    assert event.payload["structured_patch_hunks"] == 0


def test_claude_records_thinking_and_multiedit_as_notes(tmp_path, repo_root):
    """R2：MultiEdit 本机 0 次 → 占位登记 + 注记，**不猜** edits[] 形态。"""
    path = write_jsonl(
        tmp_path / "c.jsonl",
        [
            claude_line("assistant", message={"role": "assistant", "content": [{"type": "thinking", "thinking": "demo"}]}),
            claude_line(
                "assistant",
                message={"role": "assistant", "content": [{"type": "tool_use", "id": "toolu-3", "name": "MultiEdit", "input": {"file_path": VISIBLE, "edits": [{"old_string": "a", "new_string": "b"}]}}]},
            ),
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    assert result.notes["block_type:thinking"] == 1
    assert result.notes["multiedit_observed"] == 1
    event = [item for item in result.events if item.tool == "MultiEdit"][0]
    assert event.payload["edits_count"] == 1
    assert "edits" in event.payload["input_keys"], "键名可以记（结构性事实）"
    assert "edits" not in event.payload, "但**不复制**未实测过的字段内容"
    assert "has_old_string" not in event.payload, "顶层没有 old_string 就不该出现该标记（不猜）"


def test_seq_is_source_line_number_and_offset_round_trips(tmp_path, repo_root):
    """seq = 源行号；用 read_source_line(seq) 必须取回**那一行**的原始对象。"""
    lines = [
        claude_line("user", message={"role": "user", "content": "first"}),
        {"type": "last-prompt", "lastPrompt": "demo", "leafUuid": "x", "sessionId": SESSION},
        claude_line("user", message={"role": "user", "content": "third"}),
    ]
    path = write_jsonl(tmp_path / "c.jsonl", lines)
    result = readers.read_file(path, limits(repo_root))
    assert [event.seq for event in result.events] == [1, 3]
    raw = path.read_bytes()
    offsets = []
    cursor = 0
    for line in raw.split(b"\n"):
        offsets.append(cursor)
        cursor += len(line) + 1
    for event in result.events:
        assert event.source_offset == offsets[event.seq - 1]
        assert readers.read_source_line(path, event.seq)["message"]["content"] == ("first" if event.seq == 1 else "third")


def test_claude_parse_notes_make_skips_visible(tmp_path, repo_root):
    path = write_jsonl(
        tmp_path / "c.jsonl",
        [
            claude_line("user", message={"role": "user", "content": "report"}),
            {"type": "mode", "mode": "demo", "sessionId": SESSION},
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    assert result.notes["line_type:mode"] == 1


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #


def test_codex_session_meta_and_roles(tmp_path, repo_root):
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [
            codex_meta(),
            codex_line({"type": "message", "id": "m1", "role": "user", "content": [{"type": "text", "text": "demo report"}]}),
            codex_line({"type": "message", "id": "m2", "role": "developer", "content": [{"type": "text", "text": "demo instructions"}]}),
            codex_line({"type": "message", "id": "m3", "role": "assistant", "content": [{"type": "text", "text": "ack"}]}),
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    assert result.format_version == "codex-rollout-1"
    assert result.harness_version == "0.142.5-demo"
    kinds = [event.kind for event in result.events]
    assert kinds == ["meta", "user_msg", "meta", "assistant_msg"], "developer 角色是 meta（不得当成用户报 bug）"
    assert result.events[0].payload["cli_version"] == "0.142.5-demo"
    assert result.notes["role:developer"] == 1


@pytest.mark.parametrize("key", ["command", "cmd"])
def test_codex_command_text_from_both_key_names(tmp_path, repo_root, key):
    """R3：命令文本键不统一（shell_command 用 command，exec_command 用 cmd），两个都要认。"""
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [
            codex_meta(),
            codex_line({"type": "function_call", "name": "shell_command", "call_id": "call-1", "arguments": json.dumps({key: "python -m pytest -q", "workdir": "/demo/alpha"})}),
        ],
    )
    event = [item for item in readers.read_file(path, limits(repo_root)).events if item.kind == "tool_call"][0]
    assert event.payload["command"] == "python -m pytest -q"
    assert event.payload["workdir"] == "/demo/alpha"


def test_codex_unparsable_arguments_are_noted(tmp_path, repo_root):
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [
            codex_meta(),
            codex_line({"type": "function_call", "name": "shell_command", "call_id": "call-1", "arguments": "not json"}),
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    assert result.notes["arguments_unparsed"] == 1
    event = [item for item in result.events if item.kind == "tool_call"][0]
    assert event.payload["has_command"] is False


@pytest.mark.parametrize(
    "output, expected, source",
    [
        ("Chunk ID: ab12cd\nWall time: 0.1 seconds\nProcess exited with code 0\nOutput:\nok\n", 0, "process_exited"),
        ("Chunk ID: ab12cd\nWall time: 0.1 seconds\nProcess exited with code 1\nOutput:\nfail\n", 1, "process_exited"),
        ("Exit code: 2\nWall time: 0 seconds\nOutput:\nboom\n", 2, "exit_code_line"),
        ("Chunk ID: ab12cd\nWall time: 0.1 seconds\nOutput:\nno code here\n", None, events.UNKNOWN),
    ],
)
def test_codex_exit_code_is_numeric_only(output, expected, source, tmp_path, repo_root):
    """K16：标记字符串命中不算失败——成功输出同样含 "Process exited with code"，必须取数值。"""
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [codex_meta(), codex_line({"type": "function_call_output", "call_id": "call-1", "output": output})],
    )
    event = [item for item in readers.read_file(path, limits(repo_root)).events if item.kind == "tool_result"][0]
    assert event.payload["exit_code"] == expected
    assert event.payload["exit_code_source"] == source


def test_codex_patch_paths_come_from_the_patch_text(tmp_path, repo_root):
    """R1：路径只在 patch 指令行里（JSON 侧无独立路径键）；含实测的 Move to 形态。"""
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: /demo/beta/pkg/format.py",
            "@@",
            "-old",
            "+new",
            "*** Add File: /demo/beta/pkg/new_file.py",
            "+content",
            "*** Move to: /demo/beta/pkg/moved.py",
            "*** End Patch",
        ]
    )
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [
            codex_meta(),
            codex_line({"type": "custom_tool_call", "name": "apply_patch", "call_id": "call-p", "status": "completed", "input": patch}),
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    event = [item for item in result.events if item.kind == "tool_call"][0]
    assert event.payload["files"] == ["/demo/beta/pkg/format.py", "/demo/beta/pkg/new_file.py", "/demo/beta/pkg/moved.py"]
    assert event.payload["file_actions"]["/demo/beta/pkg/moved.py"] == "move_to"
    assert event.payload["file_actions"]["/demo/beta/pkg/format.py"] == "update"
    assert event.payload["has_input"] is True and event.payload["input_chars"] > 0
    assert result.notes["multi_file_patch"] == 1


def test_codex_patch_output_pairs_back_to_the_tool_by_call_id(tmp_path, repo_root):
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [
            codex_meta(),
            codex_line({"type": "custom_tool_call", "name": "apply_patch", "call_id": "call-p", "input": "*** Begin Patch\n*** Update File: /demo/beta/pkg/format.py\n@@\n-a\n+b\n*** End Patch"}),
            codex_line({"type": "custom_tool_call_output", "call_id": "call-p", "output": "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM /demo/beta/pkg/format.py\n"}),
        ],
    )
    event = [item for item in readers.read_file(path, limits(repo_root)).events if item.kind == "tool_result"][0]
    assert event.tool == "apply_patch", "结果侧靠 call_id 配对（不是按时间序猜）"
    assert event.payload["exit_code"] == 0


def test_codex_file_change_channel_carries_structured_diff(tmp_path, repo_root):
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [
            codex_meta(),
            codex_line(
                {
                    "type": "item_completed",
                    "thread_id": "t",
                    "turn_id": "u",
                    "completed_at_ms": 1,
                    "item": {
                        "id": "i1",
                        "type": "FileChange",
                        "status": "completed",
                        "stdout": "",
                        "changes": {
                            "/demo/beta/pkg/format.py": {"type": "update", "unified_diff": "@@ -1 +1 @@\n-a\n+b\n"},
                            "/demo/beta/pkg/new_file.py": {"type": "add", "content": "x\n"},
                        },
                    },
                },
                "event_msg",
            )
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    event = [item for item in result.events if item.tool == "file_change"][0]
    assert event.kind == "tool_result"
    assert event.payload["files_count"] == 2
    assert event.payload["files"]["/demo/beta/pkg/format.py"]["unified_diff_chars"] > 0
    assert event.payload["files"]["/demo/beta/pkg/new_file.py"]["has_content"] is True
    assert result.notes["file_change_events"] == 1


def test_codex_duplicate_item_channels_are_noted_not_double_counted(tmp_path, repo_root):
    """item_completed 的 AgentMessage/UserMessage 与 message 是重复通道 → 只取后者。"""
    path = write_jsonl(
        tmp_path / "x.jsonl",
        [
            codex_meta(),
            codex_line({"type": "item_completed", "thread_id": "t", "turn_id": "u", "completed_at_ms": 1, "item": {"id": "i", "type": "AgentMessage", "content": [{"type": "text", "text": "dup"}]}}, "event_msg"),
        ],
    )
    result = readers.read_file(path, limits(repo_root))
    assert [event.kind for event in result.events] == ["meta"]
    assert result.notes["item_type:AgentMessage"] == 1


def test_path_style_distinguishes_windows_and_posix(tmp_path):
    """Windows 上 `Path("/demo/x").is_absolute()` 为 False——判形态不能靠 is_absolute()。"""
    win = "B" + chr(58) + chr(92) + "demo" + chr(92) + "x.py"
    assert codex_reader.path_style(win) == "windows_absolute"
    assert codex_reader.path_style("/demo/alpha/src/calc.py") == "posix_absolute"
    assert codex_reader.path_style("src/pkg/calc.py") == "relative"


# --------------------------------------------------------------------------- #
# 失败形态与安全阀（显式，不静默）
# --------------------------------------------------------------------------- #


def test_first_line_broken_reports_unsupported_format(tmp_path, repo_root):
    path = tmp_path / "broken.jsonl"
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write("not-json\n")
    with pytest.raises(UnsupportedFormat) as excinfo:
        readers.read_file(path, limits(repo_root))
    assert excinfo.value.code == "unsupported_format"


def test_later_broken_lines_are_noted_and_skipped(tmp_path, repo_root):
    path = tmp_path / "partial.jsonl"
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialize.dumps_json_line(claude_line("user", message={"role": "user", "content": "report"})))
        stream.write("not-json\n")
        stream.write(serialize.dumps_json_line(claude_line("assistant", message={"role": "assistant", "content": [{"type": "text", "text": "ack"}]})))
    result = readers.read_file(path, limits(repo_root))
    assert [event.kind for event in result.events] == ["user_msg", "assistant_msg"]
    assert result.notes["malformed_json"] == 1


def test_unknown_harness_reports_missing_keys(tmp_path, repo_root):
    path = write_jsonl(tmp_path / "future.jsonl", [{"future_field": 1, "type": "future"}])
    with pytest.raises(UnsupportedFormat) as excinfo:
        readers.read_file(path, limits(repo_root))
    assert excinfo.value.missing_keys


def test_version_fingerprint_mismatch_is_explicit(tmp_path, repo_root):
    """有 message（harness 可判）但缺版本指纹必需键 → 必须报错，不得"宽松解析"。"""
    path = write_jsonl(tmp_path / "c.jsonl", [{"type": "user", "message": {"role": "user", "content": "x"}, "sessionId": SESSION}])
    with pytest.raises(UnsupportedFormat) as excinfo:
        readers.read_file(path, limits(repo_root))
    assert "claude" in str(excinfo.value)
    assert excinfo.value.missing_keys


def test_line_too_long_is_an_input_error(tmp_path, repo_root):
    path = write_jsonl(tmp_path / "big.jsonl", [claude_line("user", message={"role": "user", "content": "x" * 200})])
    with pytest.raises(InputUnavailable) as excinfo:
        readers.read_file(path, limits(repo_root, max_line_bytes=64))
    assert "行超长" in str(excinfo.value)


def test_event_ceiling_is_an_input_error(tmp_path, repo_root):
    lines = [claude_line("user", message={"role": "user", "content": "report {}".format(index)}) for index in range(5)]
    path = write_jsonl(tmp_path / "many.jsonl", lines)
    with pytest.raises(InputUnavailable) as excinfo:
        readers.read_file(path, limits(repo_root, max_events_per_session=2))
    assert "事件数超过上限" in str(excinfo.value)


def test_scan_is_byte_stable_across_runs(tmp_path, repo_root):
    """K5：同输入双跑逐字节一致（中间层与 CLI 出口两层，这里是中间层 + dump）。"""
    path = write_jsonl(
        tmp_path / "c.jsonl",
        [
            claude_line("user", message={"role": "user", "content": "report"}),
            claude_line("assistant", message={"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python -m pytest -q"}}]}),
        ],
    )
    first = readers.dump(readers.read_file(path, limits(repo_root)))
    second = readers.dump(readers.read_file(path, limits(repo_root)))
    assert first == second
    assert first.endswith("\n") and "\r" not in first
