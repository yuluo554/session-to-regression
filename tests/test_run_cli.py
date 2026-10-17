"""`s2r run` 的 CLI 契约：全链路一条命令 → verified 测试 + manifest 证据链。

判据重点（每条都对应题面 §四的一个评分项）：

* **只写 verified**：目标仓库的测试目录内容 ↔ manifest 里 `status == verified` 的条目**一一对应**；
* **manifest 溯源**：每条都带会话 id / 生成时间 / 逐文件 hunk 摘要 / RED-GREEN 退出码与原因（含截断标记）；
* **退出码四语义**：0 = 至少一条 verified、1 = 无 verified、2 = 用法/输入不可用、3 = 缺 pytest；
* **批内隔离可见**：读不了/非会话日志的文件进 `failed[]`/`skipped[]`（不许静默吞掉，K21）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from s2r import cli, errors, verify

CLAUDE_ROOT = "data/bench/claude"
CODEX_ROOT = "data/bench/codex"
SEED = Path("data/bench/repo/multi-bug-1")


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return path


def seed_target(tmp_path: Path, name: str = "target") -> Path:
    """目标仓库 = 语料里那份"编辑前的工作区"的拷贝（真实仓库不能当落点：生成物会写进它）。"""
    source = Path(__file__).resolve().parents[1] / SEED
    root = tmp_path / name
    for path in sorted(source.rglob("*")):
        if path.is_file():
            write_text(root / path.relative_to(source), path.read_text(encoding="utf-8"))
    return root


def run_argv(target: Path, out_dir=None):
    argv = ["run", "--target", str(target), "--claude-root", CLAUDE_ROOT, "--codex-root", CODEX_ROOT]
    if out_dir is not None:
        argv += ["--out-dir", str(out_dir)]
    return argv


def digest(root: Path):
    return {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_run_writes_only_verified_tests_and_a_manifest(tmp_path, capsys):
    target = seed_target(tmp_path)
    before = digest(target)

    code = cli.main(run_argv(target))
    captured = capsys.readouterr()
    assert code == errors.EXIT_OK
    assert "run: 读入" in captured.err

    payload = json.loads(captured.out)
    manifest = json.loads((target / "regression" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == payload, "stdout 与 regression/manifest.json 是同一份内容"

    verified = [entry for entry in manifest["entries"] if entry["status"] == verify.STATUS_VERIFIED]
    assert verified, manifest["counts"]
    produced = sorted(
        str(path.relative_to(target)).replace("\\", "/") for path in (target / "tests").rglob("*.py")
    )
    assert produced == sorted(entry["test_path"] for entry in verified), "测试目录内容必须与 verified 条目一一对应"
    assert manifest["counts"]["written"] == len(verified)

    entry = [item for item in verified if item["episode_id"] == "sess-demo-0009#4"][0]
    assert entry["session_id"] == "sess-demo-0009"
    assert entry["files"] and entry["files"][0]["hunks"], "逐文件 hunk 摘要要进 manifest（证据链）"
    assert entry["red"]["exit_code"] == 1 and entry["green"]["exit_code"] == 0
    assert isinstance(entry["red"]["truncated"], bool)
    assert entry["generated_at"].startswith("2026-01-02T"), "生成时间取会话日志里的时间戳（禁时钟）"

    after = digest(target)
    unchanged = {key: value for key, value in after.items() if not key.startswith(("tests/", "regression/"))}
    assert unchanged == {key: value for key, value in before.items() if not key.startswith(("tests/", "regression/"))}


def test_run_with_an_empty_target_verifies_nothing(tmp_path, capsys):
    """空仓库：文件都定位不到 → 逐条如实 unverified/needs_manual，manifest 照写，但一个测试都不落。"""
    target = tmp_path / "empty"
    target.mkdir()
    code = cli.main(run_argv(target))
    captured = capsys.readouterr()
    assert code == errors.EXIT_CHECK_FAILED
    payload = json.loads(captured.out)
    assert payload["counts"]["verified"] == 0
    assert payload["counts"]["entries"] > 0, "有 episode 就都要有结论（不许静默消失）"
    assert not (target / "tests").exists()
    assert (target / "regression" / "manifest.json").is_file()
    statuses = {entry["status"] for entry in payload["entries"]}
    assert statuses <= {verify.STATUS_UNVERIFIED, verify.STATUS_NEEDS_MANUAL}


def test_run_without_any_usable_log_root_is_an_input_error(tmp_path, capsys):
    target = tmp_path / "target"
    target.mkdir()
    code = cli.main(["run", "--target", str(target), "--claude-root", str(tmp_path / "nope-a"), "--codex-root", str(tmp_path / "nope-b")])
    captured = capsys.readouterr()
    assert code == errors.EXIT_USAGE
    assert "input_unavailable" in captured.err
    assert (errors.EXIT_DEPENDENCY_MISSING == 3)  # 与"缺依赖"区分开：这里不是依赖问题


def test_run_missing_pytest_exits_3(tmp_path, capsys, monkeypatch):
    target = seed_target(tmp_path)
    monkeypatch.setattr(verify, "pytest_available", lambda: False)
    code = cli.main(run_argv(target))
    captured = capsys.readouterr()
    assert code == errors.EXIT_DEPENDENCY_MISSING
    assert "dependency_missing" in captured.err
    assert captured.out == ""
    assert not (target / "regression").exists(), "依赖缺失不许留下半截产物"


def test_run_needs_a_target_directory(tmp_path, capsys):
    code = cli.main(["run", "--target", str(tmp_path / "nope"), "--claude-root", CLAUDE_ROOT, "--codex-root", CODEX_ROOT])
    assert code == errors.EXIT_USAGE
    assert "input_unavailable" in capsys.readouterr().err


def test_run_without_target_is_a_usage_error(capsys):
    assert cli.main(["run"]) == errors.EXIT_USAGE
    assert "usage" in capsys.readouterr().err


def test_run_shows_batch_isolation_of_unreadable_files(tmp_path, capsys):
    """读不进/非会话日志的文件进 failed[]/skipped[]（K21：批量按件隔离且显式可见）。"""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    (log_root / "bad.jsonl").write_bytes(b'{"mystery": true, "type": "future-line"}\n')
    bad_claude = tmp_path / "bad-claude"
    bad_claude.mkdir()
    (bad_claude / "broken.jsonl").write_bytes(b"not-json-at-all\n")
    target = seed_target(tmp_path)

    code = cli.main(["run", "--target", str(target), "--claude-root", str(bad_claude), "--codex-root", str(log_root)])
    payload = json.loads(capsys.readouterr().out)
    assert code == errors.EXIT_CHECK_FAILED
    assert payload["failed"], "读不了的文件必须显式进 failed[]"
    assert any("unsupported_format" in str(item.get("code")) for item in payload["failed"])


def test_run_is_byte_identical_on_a_second_run(tmp_path, capsys):
    """同输入双跑 → manifest 逐字节一致（只有目标路径这类外部输入不同，比对前把落点规范化掉）。"""
    first_target = seed_target(tmp_path, "one")
    second_target = seed_target(tmp_path, "two")
    assert cli.main(run_argv(first_target)) == errors.EXIT_OK
    first = json.loads(capsys.readouterr().out)
    assert cli.main(run_argv(second_target)) == errors.EXIT_OK
    second = json.loads(capsys.readouterr().out)

    first["target"] = "%TARGET%"
    second["target"] = "%TARGET%"
    assert first == second
    assert _normalized_manifest(first_target) == _normalized_manifest(second_target)


def _normalized_manifest(root: Path) -> str:
    """按 **JSON 里的转义形态**替换落点（`\\` 在 json 文本里是双写，直接替换原始路径对不上）。"""
    text = (root / "regression" / "manifest.json").read_text(encoding="utf-8")
    return text.replace(json.dumps(str(root))[1:-1], "%TARGET%")


def test_run_writes_the_report_to_out_dir(tmp_path, capsys):
    target = seed_target(tmp_path)
    out_dir = tmp_path / "reports"
    assert cli.main(run_argv(target, out_dir)) == errors.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    report = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    assert report == payload
