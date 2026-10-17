"""`s2r reconstruct` 的 CLI 契约：退出码语义、产物 schema、双跑一致、会话引用解析。

退出码语义（与 README 退出码表同一份口径）：
0 = 至少一个文件给出**完整** before/after；1 = 一个都没有（片段/无法定位/无 before 镜像）；
2 = 用法错误或输入不可用。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s2r import cli, errors, readers, reconstruct, serialize

CLAUDE_SAMPLE = Path("data/bench/claude/multi-bug-1.jsonl")
CODEX_SAMPLE = Path("data/bench/codex/happy-path-1.jsonl")
CODEX_REPO = Path("data/bench/repo/happy-path-1")
NEW_FILE_SAMPLE = Path("data/bench/claude/new-file-write-1.jsonl")
AMBIGUOUS_SAMPLE = Path("data/bench/claude/ambiguous-old-string-1.jsonl")


def read_json(path: Path):
    return json.loads(serialize.read_text(path))


def test_reconstruct_is_no_longer_staged(capsys):
    """退役 exit 4：命令面仍注册、真能跑出产物，且命令面里不再有"阶段未就绪"这种可能。"""
    assert "reconstruct" in cli.registered_commands()
    assert not hasattr(cli, "STAGED_COMMANDS"), "M5 起 exit 4 整项退役（`plan/04` §1.2 四处同步）"
    assert cli.main(["reconstruct", str(CLAUDE_SAMPLE)]) == errors.EXIT_OK
    assert "stage_not_ready" not in capsys.readouterr().err


def test_reconstruct_writes_stable_output_and_reports(repo_root, tmp_path, capsys):
    out = tmp_path / "rec.json"
    assert cli.main(["reconstruct", str(CLAUDE_SAMPLE), "--out", str(out)]) == errors.EXIT_OK
    captured = capsys.readouterr()
    assert "2 个文件" in captured.out
    assert "完整 2" in captured.out

    payload = read_json(out)
    assert set(payload) == {"schema_version", "source", "session_id", "source_file", "files", "episodes", "counts"}
    assert payload["schema_version"] == reconstruct.SCHEMA_VERSION
    assert payload["source"] == "claude"
    assert payload["counts"][reconstruct.STATUS_OK] == 2
    assert [item["file"] for item in payload["files"]] == ["/demo/alpha/src/calc.py", "/demo/beta/pkg/format.py"]
    assert payload["episodes"] and all(item["status"] == reconstruct.STATUS_OK for item in payload["episodes"])

    first = serialize.sha256_file(out)
    assert cli.main(["reconstruct", str(CLAUDE_SAMPLE), "--out", str(out)]) == errors.EXIT_OK
    capsys.readouterr()
    assert serialize.sha256_file(out) == first, "同输入双跑必须逐字节一致（CLI 出口层）"


def test_reconstruct_stdout_is_valid_json(capsys):
    assert cli.main(["reconstruct", str(CLAUDE_SAMPLE)]) == errors.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"][0]["before"].startswith("def divide")
    assert payload["files"][0]["after"].endswith("if b else 0\n")


def test_reconstruct_reports_a_fragment_only_session_as_not_ok(capsys):
    """一个完整 before/after 都没有 → exit 1（显式降级，不伪装成功）。"""
    assert cli.main(["reconstruct", str(NEW_FILE_SAMPLE)]) == errors.EXIT_CHECK_FAILED
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"][reconstruct.STATUS_NO_BEFORE] == 1
    assert payload["counts"][reconstruct.STATUS_OK] == 0
    assert payload["files"][0]["before"] is None


def test_reconstruct_surfaces_unresolved_without_picking_a_side(capsys):
    """多处命中：CLI 出口如实报 `unresolved`，且不产出 before/after。"""
    assert cli.main(["reconstruct", str(AMBIGUOUS_SAMPLE)]) == errors.EXIT_CHECK_FAILED
    payload = json.loads(capsys.readouterr().out)
    item = payload["files"][0]
    assert item["status"] == reconstruct.STATUS_UNRESOLVED
    assert item["reason"] == reconstruct.REASON_AMBIGUOUS
    assert item["before"] is None and item["after"] is None


def test_repo_flag_enables_disk_alignment(capsys):
    """`--repo` 是磁盘对齐的显式开关：给了才读工作区，且 Codex 侧因此拿到全文 before/after。"""
    assert cli.main(["reconstruct", str(CODEX_SAMPLE)]) == errors.EXIT_CHECK_FAILED
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"][0]["status"] == reconstruct.STATUS_PARTIAL

    assert cli.main(["reconstruct", str(CODEX_SAMPLE), "--repo", str(CODEX_REPO)]) == errors.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    item = payload["files"][0]
    assert item["status"] == reconstruct.STATUS_OK
    assert item["source"] == reconstruct.SOURCE_CODEX_DIFF
    assert item["before"] == "def format_amount(value):\n    return value\n"


def test_session_ref_is_resolvable_from_a_log_root(tmp_path, repo_root, capsys):
    """`<source>:<session_id>`：只探测每份文件的前若干行即可定位（不扫全量内容）。"""
    session_id = "sess-demo-0003"
    log_root = tmp_path / "projects"
    log_root.mkdir()
    (log_root / "session.jsonl").write_bytes(CLAUDE_SAMPLE.read_bytes())
    assert cli.main(["reconstruct", "claude:" + session_id, "--claude-root", str(log_root)]) == errors.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == session_id

    assert cli.main(["reconstruct", "claude:no-such-session", "--claude-root", str(log_root)]) == errors.EXIT_USAGE
    assert "没找到会话" in capsys.readouterr().err


@pytest.mark.parametrize("reference", ["sess-demo-0003", "nope:sess-demo-0003", "claude:"])
def test_unresolvable_session_reference_is_a_usage_error(reference, capsys):
    assert cli.main(["reconstruct", reference]) == errors.EXIT_USAGE
    assert "input_unavailable" in capsys.readouterr().err


def test_missing_log_file_is_a_usage_error(tmp_path, capsys):
    assert cli.main(["reconstruct", str(tmp_path / "nope.jsonl")]) == errors.EXIT_USAGE
    assert "input_unavailable" in capsys.readouterr().err


def test_invalid_corpus_sample_is_rejected_by_the_reader(repo_root, capsys):
    """非法样本必须报 `unsupported_format`（还原器不替读取器"宽松"）。"""
    invalid = sorted((repo_root / "data" / "bench" / "invalid").glob("*.jsonl"))
    assert invalid
    assert cli.main(["reconstruct", str(invalid[0])]) == errors.EXIT_USAGE
    assert "unsupported_format" in capsys.readouterr().err


def test_reconstruct_does_not_modify_the_worktree(repo_root, capsys):
    """K11：还原只**读**磁盘基线——不许改动用户工作区。"""
    targets = sorted((repo_root / CODEX_REPO).rglob("*.py"))
    assert targets
    before = {str(path): serialize.sha256_file(path) for path in targets}
    assert cli.main(["reconstruct", str(CODEX_SAMPLE), "--repo", str(CODEX_REPO)]) == errors.EXIT_OK
    capsys.readouterr()
    after = {str(path): serialize.sha256_file(path) for path in targets}
    assert before == after


def test_reconstruct_reader_limits_come_from_config(repo_root):
    """口径单点：行长上限等数值只从配置文件取（这里顺手锁住读取面用对了口径）。"""
    from s2r import config

    loaded = config.load_config(repo_root / "config" / "scoring.toml")
    limits = readers.limits_from_config(loaded)
    assert limits["max_line_bytes"] == config.episode_params(loaded)["max_line_bytes"]
