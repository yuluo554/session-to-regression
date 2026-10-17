"""`s2r generate` 的 CLI 契约：退出码语义、产物落点、证据锚点、双跑一致、K11。

退出码语义（与 README 退出码表同一份口径）：
0 = 至少一份**含候选断言**的骨架；1 = 一份都没有（符号识别不到 / 推断不出 / 还原状态不是 ok）；
2 = 用法错误或输入不可用（认不出的 episode 引用、序号不在会话里、缺落点）。

纪律：`--out-dir` 是**输出根**（目录形态与目标仓库一致），`--repo` 是目标仓库根；
两者都不给就报用法错误——**绝不猜要不要往当前目录写测试文件**（K11 的前置条件）。
"""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

from s2r import cli, config, errors, generate, paths as paths_mod, readers, serialize

CLAUDE_SAMPLE = Path("data/bench/claude/multi-bug-1.jsonl")
AMBIGUOUS_SAMPLE = Path("data/bench/claude/ambiguous-old-string-1.jsonl")
SP_EPISODE = "data/bench/claude/structured-patch-1.jsonl#4"
MB_FIRST_EPISODE = "data/bench/claude/multi-bug-1.jsonl#4"
MB_SECOND_EPISODE = "data/bench/claude/multi-bug-1.jsonl#9"


def read_json(text: str):
    return json.loads(text)


def write_text(path: Path, text: str) -> Path:
    """3.8 通道：`Path.write_text(newline=...)` 是 3.10+ 参数，只能用 `open(..., newline=...)`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return path


def test_generate_is_no_longer_staged(tmp_path, capsys):
    """退役 exit 4：命令面仍注册，但已不在「阶段未就绪」表里，且真能跑出产物。"""
    assert "generate" in cli.registered_commands()
    assert "generate" not in cli.STAGED_COMMANDS
    assert cli.main(["generate", SP_EPISODE, "--out-dir", str(tmp_path)]) == errors.EXIT_OK
    captured = capsys.readouterr()
    assert "stage_not_ready" not in captured.err
    assert "generate: claude/sess-demo-0009" in captured.err


def test_generate_writes_only_the_tests_dir_and_reports_json(tmp_path, capsys):
    code = cli.main(["generate", SP_EPISODE, "--out-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == errors.EXIT_OK

    payload = read_json(captured.out)
    assert set(payload) == {
        "schema_version", "source", "session_id", "source_file", "episode_id", "convention", "skeletons", "written", "counts",
    }
    assert payload["schema_version"] == generate.SCHEMA_VERSION
    assert payload["source"] == "claude"
    assert payload["episode_id"] == "sess-demo-0009#4"
    assert payload["counts"] == {"generated": 1, "needs_manual": 0, "written": 1}
    assert payload["convention"]["assumed"] is True, "没给 --repo 必须显式标出惯例是保守默认"

    skeleton = payload["skeletons"][0]
    assert skeleton["relative_path"] == "tests/regression/test_sess_demo_0009_4.py"
    assert skeleton["status"] == generate.STATUS_GENERATED
    assert skeleton["candidates"] == ["assert result == 0"]

    written = skeleton["relative_path"]
    target_file = tmp_path.joinpath(*written.split("/"))
    assert target_file.is_file(), [str(p) for p in tmp_path.rglob("*")]
    produced = [str(path.relative_to(tmp_path)).replace("\\", "/") for path in tmp_path.rglob("*.py")]
    assert produced == [written], "只许产出这一个文件（且落在测试目录里）"


def test_generated_file_has_the_evidence_anchors_and_compiles(tmp_path, capsys):
    assert cli.main(["generate", SP_EPISODE, "--out-dir", str(tmp_path)]) == errors.EXIT_OK
    capsys.readouterr()
    text = (tmp_path / "tests" / "regression" / "test_sess_demo_0009_4.py").read_text(encoding="utf-8")

    params = config.generate_params(config.load_config(Path("config") / "scoring.toml"))
    assert text.startswith('"""'), "docstring 证据锚点是第一屏"
    assert "sess-demo-0009#4" in text, "会话 id"
    assert "2026-01-02T" in text, "事件时间戳（取自日志，不取时钟）"
    assert "empty denominator" in text, "原始用户报告片段"
    assert "/demo/alpha/src/calc.py" in text, "修复文件清单"
    assert params["candidate_note"] in text, "候选断言必须注明需人工确认"
    for name in params["sections"]:
        assert generate.SECTION_MARKER.format(name) in text
    py_compile.compile(str(tmp_path / "tests" / "regression" / "test_sess_demo_0009_4.py"), doraise=True)


def test_generated_candidate_is_red_before_and_green_after(tmp_path, capsys):
    """候选断言的**语义**判据：同一个骨架在修复前失败、修复后通过。

    这是"模板不是空转"的最强反证——断言若只是装饰，就不可能对修复前后做出不同反应；
    同时也是 M5 RED/GREEN 的前哨（证明这条路走得通），运行在隔离临时目录内（不碰用户工作区）。
    """
    repo = tmp_path / "repo"
    module = repo / "demo" / "alpha" / "src"
    module.mkdir(parents=True)
    calc = module / "calc.py"
    write_text(calc, "def divide(a, b):\n    return a / b\n")

    assert cli.main(["generate", SP_EPISODE, "--repo", str(repo)]) == errors.EXIT_OK
    capsys.readouterr()

    red = _run_pytest(repo)
    assert red.returncode != 0, "修复前必须是红的：\n{}".format(red.stdout)

    write_text(calc, "def divide(a, b):\n    return a / b if b else 0\n")
    green = _run_pytest(repo)
    assert green.returncode == 0, "修复后必须转绿：\n{}".format(green.stdout)


def _run_pytest(repo: Path):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/regression", "-q"],
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )


def test_generate_is_byte_identical_on_a_second_run(tmp_path, capsys):
    """同 episode 双跑 → 逐字节一致（CLI 出口层也断言，别只在中间层断）。"""
    first_dir = tmp_path / "one"
    second_dir = tmp_path / "two"
    assert cli.main(["generate", SP_EPISODE, "--out-dir", str(first_dir)]) == errors.EXIT_OK
    first_out = capsys.readouterr()
    assert cli.main(["generate", SP_EPISODE, "--out-dir", str(second_dir)]) == errors.EXIT_OK
    second_out = capsys.readouterr()

    # 机读产物里带绝对落点（落点本来就不同），比对前先把落点规范化掉——比的是**骨架内容**
    first_payload = read_json(first_out.out)
    second_payload = read_json(second_out.out)
    for payload, directory in ((first_payload, first_dir), (second_payload, second_dir)):
        payload["written"] = [item.replace(str(directory), "%OUT%") for item in payload["written"]]
    assert first_payload == second_payload
    left = first_dir / "tests" / "regression" / "test_sess_demo_0009_4.py"
    right = second_dir / "tests" / "regression" / "test_sess_demo_0009_4.py"
    assert left.read_bytes() == right.read_bytes(), "同 episode 双跑必须逐字节一致"


def test_generate_with_repo_writes_into_the_repo_tests_dir(tmp_path, capsys):
    """`--repo`：生成物进目标仓库的 `[generate].tests_dir`，并**不动源码**（K11）。"""
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    source = target / "src" / "calc.py"
    with open(str(source), "w", encoding="utf-8", newline="\n") as stream:
        stream.write("def divide(a, b):\n    return a / b\n")
    before = _digest(target)

    assert cli.main(["generate", SP_EPISODE, "--repo", str(target)]) == errors.EXIT_OK
    capsys.readouterr()
    produced = target / "tests" / "regression" / "test_sess_demo_0009_4.py"
    assert produced.is_file()
    assert {key: value for key, value in _digest(target).items() if not key.startswith("tests/regression")} == before


def test_generate_reports_needs_manual_with_a_reason(tmp_path, capsys):
    """还原状态不是 ok → exit 1、不产文件，但**逐 episode 的结论与原因必须可见**。"""
    sample = Path("data/bench/claude/ambiguous-old-string-1.jsonl")
    code = cli.main(["generate", str(sample) + "#4", "--out-dir", str(tmp_path)])
    payload = read_json(capsys.readouterr().out)
    assert code == errors.EXIT_CHECK_FAILED
    assert payload["counts"] == {"generated": 0, "needs_manual": 1, "written": 0}
    skeleton = payload["skeletons"][0]
    assert skeleton["status"] == generate.STATUS_NEEDS_MANUAL
    assert skeleton["reason"] == "reconstruction_not_usable:unresolved"
    assert skeleton["content"] == "" and skeleton["written"] is False
    assert not list(tmp_path.rglob("*.py")), "不生成文件就是真的一个都不许有"


def test_each_episode_of_a_session_gets_its_own_verdict(tmp_path, capsys):
    """一会话多 episode：逐个生成，互不牵连（一个推不出来不影响另一个产出）。"""
    assert cli.main(["generate", MB_FIRST_EPISODE, "--out-dir", str(tmp_path / "a")]) == errors.EXIT_OK
    first = read_json(capsys.readouterr().out)
    assert first["episode_id"] == "sess-demo-0003#4"
    assert first["counts"]["generated"] == 1

    assert cli.main(["generate", MB_SECOND_EPISODE, "--out-dir", str(tmp_path / "b")]) == errors.EXIT_CHECK_FAILED
    second = read_json(capsys.readouterr().out)
    assert second["episode_id"] == "sess-demo-0003#9"
    assert second["skeletons"][0]["reason"] == generate.REASON_ASSERT_NOT_DERIVABLE
    assert (tmp_path / "b" / "tests" / "regression" / "test_sess_demo_0003_9.py").is_file(), "推不出候选断言也要落骨架（只是标 needs_manual）"


def test_generate_without_a_target_is_a_usage_error(capsys):
    """`--repo` / `--out-dir` 都不给 → exit 2（绝不猜"要不要写进当前目录"）。"""
    assert cli.main(["generate", SP_EPISODE]) == errors.EXIT_USAGE
    captured = capsys.readouterr()
    assert "usage_error" in captured.err
    assert "--out-dir" in captured.err


def test_unknown_episode_sequence_lists_what_is_available(capsys):
    code = cli.main(["generate", "data/bench/claude/multi-bug-1.jsonl#77", "--out-dir", "unused"])
    captured = capsys.readouterr()
    assert code == errors.EXIT_USAGE
    assert "input_unavailable" in captured.err
    assert "sess-demo-0003#4" in captured.err and "sess-demo-0003#9" in captured.err, "要把可用序号列出来"


@pytest.mark.parametrize("reference", ["sess-demo-0003", "#4", "claude:sess-demo-0003"])
def test_unparsable_episode_reference_is_a_usage_error(reference, capsys):
    assert cli.main(["generate", reference, "--out-dir", "unused"]) == errors.EXIT_USAGE
    assert "usage_error" in capsys.readouterr().err


def test_missing_log_file_is_a_usage_error(capsys):
    assert cli.main(["generate", "no-such-log.jsonl#4", "--out-dir", "unused"]) == errors.EXIT_USAGE
    assert "input_unavailable" in capsys.readouterr().err


def test_invalid_corpus_sample_is_rejected_by_the_reader(repo_root, capsys):
    """非法样本必须报 `unsupported_format`（生成器不替读取器"宽松"）。"""
    invalid = sorted((repo_root / "data" / "bench" / "invalid").glob("*.jsonl"))
    assert invalid
    assert cli.main(["generate", str(invalid[0]) + "#1", "--out-dir", "unused"]) == errors.EXIT_USAGE
    assert "unsupported_format" in capsys.readouterr().err


def test_generate_reads_the_session_by_id_from_a_log_root(tmp_path, capsys, monkeypatch):
    """`<source>:<session_id>#<序号>`：与 reconstruct 同形的会话引用（只探测前若干行）。

    `generate` 的参数面里没有 `--claude-root`（K12 冻结），所以日志根覆盖走**环境变量**
    ——这也是 README 里公开的覆盖方式之一。
    """
    log_root = tmp_path / "projects"
    log_root.mkdir()
    (log_root / "session.jsonl").write_bytes(CLAUDE_SAMPLE.read_bytes())
    monkeypatch.setenv(paths_mod.ENV_CLAUDE_ROOT, str(log_root))
    code = cli.main(["generate", "claude:sess-demo-0003#4", "--out-dir", str(tmp_path / "out")])
    capsys.readouterr()
    assert code == errors.EXIT_OK
    assert (tmp_path / "out" / "tests" / "regression" / "test_sess_demo_0003_4.py").is_file()


def test_generate_keeps_the_report_verbatim(repo_root, capsys, tmp_path):
    """报告原文逐字进 docstring（含引号也不许被转义吃掉）——锚点可回溯是硬要求。"""
    loaded = config.load_config(repo_root / "config" / "scoring.toml")
    file_read = readers.read_file(
        repo_root / "data" / "bench" / "claude" / "structured-patch-1.jsonl", readers.limits_from_config(loaded)
    )
    report = file_read.events[0].payload["text"]
    assert cli.main(["generate", SP_EPISODE, "--out-dir", str(tmp_path)]) == errors.EXIT_OK
    capsys.readouterr()
    text = (tmp_path / "tests" / "regression" / "test_sess_demo_0009_4.py").read_text(encoding="utf-8")
    assert report in text, report


def _digest(root: Path):
    return {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
