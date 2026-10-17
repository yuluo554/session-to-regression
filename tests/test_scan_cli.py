"""`s2r scan` 的 CLI 契约 + 日志根发现（paths）。

scan 的退出码语义：0 = 读到至少一条事件；1 = 读到文件但**零事件**（显式降级，不伪装成功）；
2 = 输入不可用 / 未知格式。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s2r import cli, events, paths, readers, serialize
from s2r.errors import EXIT_CHECK_FAILED, EXIT_OK, EXIT_USAGE, InputUnavailable

SESSION = "sess-demo-0007"


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


def write_jsonl(path: Path, lines) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        for line in lines:
            stream.write(serialize.dumps_json_line(line))
    return path


def sample_log(tmp_path: Path, name: str = "c.jsonl") -> Path:
    return write_jsonl(
        tmp_path / name,
        [
            claude_line("user", message={"role": "user", "content": "demo report"}),
            claude_line("assistant", message={"role": "assistant", "content": [{"type": "text", "text": "ack"}]}),
        ],
    )


def test_scan_writes_stable_events_and_reports(tmp_path, capsys):
    log = sample_log(tmp_path)
    out = tmp_path / "events.json"
    assert cli.main(["scan", str(log), "--out", str(out)]) == EXIT_OK
    captured = capsys.readouterr()
    assert "2 条事件" in captured.out

    payload = json.loads(serialize.read_text(out))
    assert payload["schema_version"] == readers.SCAN_SCHEMA_VERSION
    assert set(payload) == {"schema_version", "files", "sessions", "events", "episodes", "filtered", "skipped", "failed", "notes"}
    assert payload["schema_version"] == 2
    assert len(payload["events"]) == 2
    assert payload["sessions"][0]["session_id"] == SESSION

    first = serialize.sha256_file(out)
    assert cli.main(["scan", str(log), "--out", str(out)]) == EXIT_OK
    assert serialize.sha256_file(out) == first, "同输入双跑必须逐字节一致（CLI 出口层）"


def test_scan_stdout_is_valid_json(tmp_path, capsys):
    log = sample_log(tmp_path)
    assert cli.main(["scan", str(log)]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["events"]) == 2


def test_scan_zero_events_is_not_a_success(tmp_path, capsys):
    """读到文件但零事件 → exit 1（不得把"没结果"伪装成成功）。

    注意：只有状态行（如 `last-prompt`）的文件连 harness 都判不出来（那是 unsupported_format，exit 2）；
    要造"合法结构但零事件"，得用一条**带 message 的合法行**、其内容块都不产生事件（如 thinking）。
    """
    log = write_jsonl(
        tmp_path / "thinking-only.jsonl",
        [claude_line("assistant", message={"role": "assistant", "content": [{"type": "thinking", "thinking": "demo"}]})],
    )
    assert cli.main(["scan", str(log)]) == EXIT_CHECK_FAILED
    assert json.loads(capsys.readouterr().out)["events"] == []


def test_scan_unknown_format_exits_2(tmp_path, capsys):
    log = write_jsonl(tmp_path / "future.jsonl", [{"future_field": 1, "type": "future"}])
    assert cli.main(["scan", str(log)]) == EXIT_USAGE
    assert "unsupported_format" in capsys.readouterr().err


def test_scan_missing_input_exits_2(tmp_path, capsys):
    assert cli.main(["scan", str(tmp_path / "nope.jsonl")]) == EXIT_USAGE
    assert "input_unavailable" in capsys.readouterr().err


def test_scan_accepts_directories_and_extra_roots(tmp_path, capsys):
    first = tmp_path / "one"
    second = tmp_path / "two"
    sample_log(first, "a.jsonl")
    sample_log(second, "b.jsonl")
    assert cli.main(["scan", str(first), "--claude-root", str(second)]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["files"]) == 2, "显式输入与 --claude-root 都要扫"
    assert len(payload["events"]) == 4


def test_scan_same_log_fed_twice_is_deduplicated(tmp_path, capsys):
    log = sample_log(tmp_path)
    assert cli.main(["scan", str(log), str(log)]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["files"]) == 1, "同一份日志喂两次不得产出两遍事件"


def test_scan_on_frozen_invalid_sample_is_rejected(repo_root, capsys):
    """冻结语料里的非法样本必须被拒绝（基准的误报门也依赖这个行为）。"""
    invalid = sorted((repo_root / "data" / "bench" / "invalid").glob("*.jsonl"))
    assert invalid, "冻结语料应有一个 invalid/ 样本"
    assert cli.main(["scan", str(invalid[0])]) == EXIT_USAGE
    assert "unsupported_format" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 日志根发现
# --------------------------------------------------------------------------- #


def test_discover_root_prefers_explicit(tmp_path):
    explicit = tmp_path / "logs"
    explicit.mkdir()
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    assert paths.discover_root("claude", explicit=str(explicit), env={}, home=tmp_path) == explicit


def test_discover_root_from_env_then_home(tmp_path):
    env_dir = tmp_path / "env-logs"
    env_dir.mkdir()
    assert paths.discover_root("claude", env={paths.ENV_CLAUDE_ROOT: str(env_dir)}, home=tmp_path) == env_dir

    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    assert paths.discover_root("codex", env={}, home=home) == home / ".codex" / "sessions"


def test_discover_root_explicit_must_exist(tmp_path):
    with pytest.raises(InputUnavailable):
        paths.discover_root("claude", explicit=str(tmp_path / "missing"), env={}, home=tmp_path)
    with pytest.raises(InputUnavailable):
        paths.discover_root("claude", env={paths.ENV_CLAUDE_ROOT: str(tmp_path / "missing")}, home=tmp_path)


def test_discover_root_not_found_is_explicit_with_hint(tmp_path):
    with pytest.raises(InputUnavailable) as excinfo:
        paths.discover_root("claude", env={}, home=tmp_path)
    assert "claude-root" in (excinfo.value.hint or "")
    assert excinfo.value.exit_code == EXIT_USAGE


def test_discover_root_rejects_unknown_source(tmp_path):
    with pytest.raises(InputUnavailable):
        paths.discover_root("some-other-agent", env={}, home=tmp_path)


def test_log_root_discovery_is_not_hardcoded(repo_root):
    """路径自适应纪律：不得硬编码用户名/安装形态——实现里必须用 home 展开而非字面量。"""
    source = (repo_root / "src" / "s2r" / "paths.py").read_text(encoding="utf-8")
    assert "Path.home()" in source
    assert ".claude" in source and ".codex" in source
    assert "ASUS" not in source and "Users" not in source, "不得出现本机用户名/路径段"
