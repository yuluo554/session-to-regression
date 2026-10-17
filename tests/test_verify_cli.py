"""`s2r verify` 的 CLI 契约：退出码 0/1/2/3、产物落点、"不验证不落盘"、报告可机读。

退出码语义（与 README 退出码表同一份口径）：
0 = 至少一条 verified；1 = 无 verified（unverified / needs_manual）；2 = 用法错误或输入不可用；
3 = 缺 pytest（**不得**退化成"零条结果 + exit 0"）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from s2r import cli, errors, generate, verify

SP_EPISODE = "data/bench/claude/structured-patch-1.jsonl#4"
AMBIGUOUS_EPISODE = "data/bench/claude/ambiguous-old-string-1.jsonl#4"
MB_SECOND_EPISODE = "data/bench/claude/multi-bug-1.jsonl#9"

TRUE_BEFORE = "def divide(a, b):\n    return a / b\n"


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return path


def make_repo(tmp_path: Path, files=None) -> Path:
    root = tmp_path / "target"
    for relative, text in (files or {"demo/alpha/src/calc.py": TRUE_BEFORE}).items():
        write_text(root.joinpath(*relative.split("/")), text)
    return root


def digest(root: Path):
    return {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_verify_returns_zero_writes_the_test_and_reports_json(tmp_path, capsys):
    root = make_repo(tmp_path)
    code = cli.main(["verify", SP_EPISODE, "--repo", str(root)])
    captured = capsys.readouterr()
    assert code == errors.EXIT_OK
    assert "verify: claude/sess-demo-0009" in captured.err

    payload = json.loads(captured.out)
    assert payload["schema_version"] == verify.SCHEMA_VERSION
    assert payload["counts"]["verified"] == 1 and payload["counts"]["entries"] == 1
    entry = payload["entries"][0]
    assert entry["episode_id"] == "sess-demo-0009#4"
    assert entry["status"] == verify.STATUS_VERIFIED
    assert entry["test_path"] == "tests/regression/test_sess_demo_0009_4.py"
    assert entry["written"] is True
    assert entry["red"]["exit_code"] == 1 and entry["green"]["exit_code"] == 0
    assert payload["written"] and Path(payload["written"][0]).is_file()
    assert payload["coverage"]["min_count_by_type"], "覆盖度门槛要随报告下发"


def test_verify_without_a_repo_is_a_usage_error(capsys):
    assert cli.main(["verify", SP_EPISODE]) == errors.EXIT_USAGE
    assert "usage_error" in capsys.readouterr().err


def test_verify_reports_unverified_with_exit_1_and_writes_nothing(tmp_path, capsys):
    """没有活动断言的骨架 → 红不成立 → exit 1，且测试目录里一个文件都没有。"""
    root = make_repo(tmp_path, {"demo/beta/pkg/format.py": "def slugify(name):\n    return name\n"})
    before = digest(root)
    code = cli.main(["verify", MB_SECOND_EPISODE, "--repo", str(root)])
    captured = capsys.readouterr()
    assert code == errors.EXIT_CHECK_FAILED
    payload = json.loads(captured.out)
    assert payload["entries"][0]["status"] == verify.STATUS_UNVERIFIED
    assert payload["entries"][0]["reason"] == verify.REASON_RED_NOT_FAILED
    assert payload["counts"]["written"] == 0
    assert digest(root) == before, "未验证通过的项不许改动目标仓库"


def test_verify_reports_needs_manual_with_exit_1(tmp_path, capsys):
    """生成层没产出内容 → needs_manual（连仓库都不需要给）。"""
    code = cli.main(["verify", AMBIGUOUS_EPISODE])
    captured = capsys.readouterr()
    assert code == errors.EXIT_CHECK_FAILED
    payload = json.loads(captured.out)
    entry = payload["entries"][0]
    assert entry["status"] == verify.STATUS_NEEDS_MANUAL
    assert entry["generation_reason"] == "reconstruction_not_usable:unresolved"
    assert entry["red"] is None and entry["green"] is None


def test_verify_unknown_episode_sequence_lists_what_is_available(tmp_path, capsys):
    root = make_repo(tmp_path)
    code = cli.main(["verify", "data/bench/claude/multi-bug-1.jsonl#77", "--repo", str(root)])
    captured = capsys.readouterr()
    assert code == errors.EXIT_USAGE
    assert "input_unavailable" in captured.err
    assert "sess-demo-0003#4" in captured.err and "sess-demo-0003#9" in captured.err


@pytest.mark.parametrize("reference", ["sess-demo-0003", "#4", "claude:sess-demo-0003"])
def test_unparsable_episode_reference_is_a_usage_error(reference, tmp_path, capsys):
    root = make_repo(tmp_path)
    assert cli.main(["verify", reference, "--repo", str(root)]) == errors.EXIT_USAGE
    assert "usage_error" in capsys.readouterr().err


def test_verify_missing_log_is_an_input_error(tmp_path, capsys):
    root = make_repo(tmp_path)
    assert cli.main(["verify", "no-such-log.jsonl#4", "--repo", str(root)]) == errors.EXIT_USAGE
    assert "input_unavailable" in capsys.readouterr().err


def test_verify_missing_pytest_exits_3(tmp_path, capsys, monkeypatch):
    """依赖门：缺 pytest → exit 3（与 selftest 的依赖语义同一判据）。"""
    root = make_repo(tmp_path)
    monkeypatch.setattr(verify, "pytest_available", lambda: False)
    code = cli.main(["verify", SP_EPISODE, "--repo", str(root)])
    captured = capsys.readouterr()
    assert code == errors.EXIT_DEPENDENCY_MISSING
    assert "dependency_missing" in captured.err
    assert captured.out == "", "依赖缺失不许产出半截产物"


def test_verify_writes_the_report_to_out_dir(tmp_path, capsys):
    root = make_repo(tmp_path)
    out_dir = tmp_path / "reports"
    assert cli.main(["verify", SP_EPISODE, "--repo", str(root), "--out-dir", str(out_dir)]) == errors.EXIT_OK
    report = json.loads((out_dir / "verify.json").read_text(encoding="utf-8"))
    payload = json.loads(capsys.readouterr().out)
    assert report == payload, "落盘的报告与 stdout 是同一份（不另立 schema）"


def test_verify_is_no_longer_staged_and_exit_4_is_retired(capsys):
    """退出码 4 整项退役：命令面里不再有"阶段未就绪"这种可能。"""
    assert "verify" in cli.registered_commands()
    assert errors.EXIT_CODES == (errors.EXIT_OK, errors.EXIT_CHECK_FAILED, errors.EXIT_USAGE, errors.EXIT_DEPENDENCY_MISSING)
    assert not hasattr(cli, "STAGED_COMMANDS")
    assert "4" not in "".join(str(code) for code in errors.EXIT_CODES)


def test_verify_reads_the_session_by_id_from_a_log_root(tmp_path, capsys, monkeypatch, repo_root):
    """`<source>:<session_id>#<序号>` 形态（日志根走环境变量，参数面里没有 --claude-root）。"""
    from s2r import paths as paths_mod

    log_root = tmp_path / "projects"
    log_root.mkdir()
    (log_root / "session.jsonl").write_bytes((repo_root / "data" / "bench" / "claude" / "structured-patch-1.jsonl").read_bytes())
    monkeypatch.setenv(paths_mod.ENV_CLAUDE_ROOT, str(log_root))
    root = make_repo(tmp_path)
    code = cli.main(["verify", "claude:sess-demo-0009#4", "--repo", str(root)])
    capsys.readouterr()
    assert code == errors.EXIT_OK
    assert (root / "tests" / "regression" / "test_sess_demo_0009_4.py").is_file()


def test_verify_reuses_the_same_skeleton_as_generate(tmp_path, capsys):
    """验证跑的必须是生成的那份骨架（两边各生成一次 = 验的不是同一个东西）。"""
    root = make_repo(tmp_path)
    assert cli.main(["verify", SP_EPISODE, "--repo", str(root)]) == errors.EXIT_OK
    capsys.readouterr()
    verified = (root / "tests" / "regression" / "test_sess_demo_0009_4.py").read_text(encoding="utf-8")

    out_dir = tmp_path / "generated"
    assert cli.main(["generate", SP_EPISODE, "--out-dir", str(out_dir)]) == errors.EXIT_OK
    generated = (out_dir / "tests" / "regression" / "test_sess_demo_0009_4.py").read_text(encoding="utf-8")
    assert verified == generated
    assert generate.SECTION_MARKER.format("assert") in verified
