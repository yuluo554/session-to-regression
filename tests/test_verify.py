"""M5 验证器测试：隔离副本 RED/GREEN、四类 unverified、三态不折叠、只写测试目录、确定性。

四条最贵的判据（都是"假门"高发区）：

1. **"不验证不落盘"要可证伪**：unverified / needs_manual 的目录内容必须能被断言为"没有这个文件"；
2. **四类 unverified 原因各有一条**（测试未失败 / 修复后仍失败 / 环境不可用 / 超时）——
   少一类就等于那条路径从没验过；
3. **隔离是真的**：跑一遍验证后，目标仓库的工作区（源码 + 已有文件）逐字节不变；
4. **判据取自口径文件**：命令（含解释器占位）、超时、两个期望值一个都不许在代码里另写一份。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from s2r import config, errors, generate, readers, reconstruct, verify
from s2r.errors import DependencyMissing, InputUnavailable, UsageError

SP_SAMPLE = Path("data/bench/claude/structured-patch-1.jsonl")
AMBIGUOUS_SAMPLE = Path("data/bench/claude/ambiguous-old-string-1.jsonl")
HAPPY_SAMPLE = Path("data/bench/codex/happy-path-1.jsonl")
MB_SAMPLE = Path("data/bench/claude/multi-bug-1.jsonl")

TRUE_BEFORE = "def divide(a, b):\n    return a / b\n"
TRUE_AFTER = "def divide(a, b):\n    return a / b if b else 0\n"

CALC_REL = ("demo", "alpha", "src", "calc.py")
FORMAT_REL = ("demo", "beta", "pkg", "format.py")

#: 一个"自己写的"骨架内容（用于制造 CURATED 的失败形态：修复后仍失败 / 超时）
FAILING_TEST = (
    '"""demo 骨架"""\n'
    "\n"
    "\n"
    "def test_demo():\n"
    "    a = 0\n"
    "    b = 0\n"
    "    result = divide(a, b)\n"
    "    assert result == 999\n"
)
SLEEPING_TEST = (
    '"""demo 骨架"""\n'
    "\n"
    "import time\n"
    "\n"
    "\n"
    "def test_demo():\n"
    "    time.sleep(30)\n"
)


def cfg(repo_root: Path):
    return config.load_config(repo_root / "config" / "scoring.toml")


def limits(repo_root: Path):
    return readers.limits_from_config(cfg(repo_root))


def write_text(path: Path, text: str) -> Path:
    """3.8 通道：`Path.write_text(newline=...)` 是 3.10+ 参数，只能用 `open(..., newline=...)`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return path


def make_repo(tmp_path: Path, files=None) -> Path:
    """造一个"编辑前的工作区"：验证要在这里找到日志里那些文件的相对路径。"""
    root = tmp_path / "target"
    for relative, text in (files or {CALC_REL: TRUE_BEFORE}).items():
        write_text(root.joinpath(*relative), text)
    (root / "demo").mkdir(parents=True, exist_ok=True)
    return root


def read_sample(repo_root: Path, relative: Path):
    return readers.read_file(repo_root / relative, limits(repo_root))


def digest(root: Path):
    return {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FakeEpisode:
    """只提供验证器需要的字段（不借真 episode 的形态，避免测试与实现一起漂）。"""

    episode_id = "sess-demo-9000#1"
    session_id = "sess-demo-9000"
    source = "claude"
    source_file = "demo-log.jsonl"
    status = "verified_by_run"
    end_ts = "2026-01-02T03:04:05.000000Z"


def curated_plan(before: str, after: str, content: str, relative=CALC_REL) -> verify.Plan:
    """造一件待验证：一个自定义骨架 + 一条自定义还原结果（用于制造指定失败形态）。"""
    file_path = "/" + "/".join(relative)
    skeleton = generate.Skeleton(
        episode_id=FakeEpisode.episode_id,
        slug="sess_demo_9000_1",
        relative_path="tests/regression/test_sess_demo_9000_1.py",
        status=generate.STATUS_GENERATED,
        reason=generate.REASON_NONE,
        assumed_convention=True,
        assumed_parts=list(generate.CONVENTION_PARTS),
        symbols=["divide"],
        candidates=["assert result == 999"],
        files=[file_path],
        content=content,
    )
    item = reconstruct.Reconstruction(
        file=file_path,
        before=before,
        after=after,
        hunks=[{"old_start": 2, "old_lines": ["return a / b"], "new_lines": ["return a / b if b else 0"], "old_count": 1, "new_count": 1}],
        source=reconstruct.SOURCE_CLAUDE_PATCH,
        confidence=reconstruct.CONFIDENCE_HIGH,
        seq=1,
    )
    return verify.Plan(episode=FakeEpisode(), skeleton=skeleton, files=[item])


# --------------------------------------------------------------------------- #
# 口径值 → 命令 / 超时 / 门槛
# --------------------------------------------------------------------------- #


def test_command_comes_from_the_config_with_the_current_interpreter(repo_root):
    loaded = cfg(repo_root)
    params = config.verify_params(loaded)
    command = verify.resolve_command(loaded)
    assert command[0] == sys.executable, "解释器占位必须换成当前解释器（装机后裸 python 未必在 PATH 上）"
    assert command[1:] == [str(item) for item in params["pytest_command"][1:]], "命令其余部分逐字取自口径文件"


def test_config_knows_the_expectation_enum_and_rejects_others(repo_root):
    assert set(verify.EXPECTATION_OUTCOMES) == set(config.VERIFY_EXPECTATIONS), "期望值映射必须与口径文件的枚举一致"
    loaded = cfg(repo_root)
    broken = dict(loaded)
    broken["verify"] = dict(loaded["verify"])
    broken["verify"]["red_expectation"] = "whatever"
    with pytest.raises(errors.ConfigError) as excinfo:
        config.validate_config(broken)
    assert "verify.red_expectation" in str(excinfo.value)


def test_timeout_prefers_the_override_and_rejects_nonsense(repo_root):
    loaded = cfg(repo_root)
    assert verify.timeout_of(loaded) == int(config.verify_params(loaded)["timeout_seconds"])
    assert verify.timeout_of(loaded, 7) == 7
    with pytest.raises(UsageError):
        verify.timeout_of(loaded, 0)


def test_coverage_thresholds_are_dispatched_from_the_bench_manifest(repo_root):
    """覆盖度门槛**自基准 manifest 下发**（不是在本模块里再写一份阈值）。"""
    from s2r import bench

    manifest = bench.load_manifest(repo_root / "data" / "bench")
    thresholds, source = verify.coverage_thresholds(str(repo_root / "data" / "bench"))
    assert thresholds == {key: int(value) for key, value in manifest["coverage"]["min_count_by_type"].items()}
    assert source == "data/bench/manifest.json"
    assert verify.coverage_thresholds(str(repo_root / "data")) == ({}, ""), "定位不到就返回空表 + 空来源（不编造阈值）"


def test_stable_excerpt_drops_header_timing_and_temp_paths():
    """摘录也要能进台账：pytest 的头部/耗时/临时目录不许进 manifest。"""
    text = (
        "============================= test session starts =============================\n"
        "platform win32 -- Python 3.8.8, pytest-7.4.4\n"
        "rootdir: some\\temp\\s2r-verify-abc\n"
        "collected 1 item\n"
        "\n"
        "tests/regression/test_demo.py F                                        [100%]\n"
        "\n"
        "================================== FAILURES ===================================\n"
        ">       result = divide(a, b)\n"
        "E       ZeroDivisionError: division by zero\n"
        "\n"
        "FAILED tests/regression/test_demo.py::test_demo - ZeroDivisionError: division by zero\n"
        "=========================== 1 failed, 1 warning in 0.02s ===========================\n"
    )
    excerpt, truncated = verify.stable_excerpt(text, 800)
    assert "ZeroDivisionError" in excerpt
    assert "0.02s" not in excerpt and "rootdir" not in excerpt and "platform" not in excerpt
    assert truncated is False

    long_excerpt, long_truncated = verify.stable_excerpt("E " + "x" * 100, 10)
    assert long_excerpt == "E " + "x" * 8 and long_truncated is True


# --------------------------------------------------------------------------- #
# 结论：三态 + 只写测试目录
# --------------------------------------------------------------------------- #


def test_verified_episode_gets_written_and_the_target_source_is_untouched(tmp_path, repo_root):
    root = make_repo(tmp_path)
    before = digest(root)

    verdicts = verify.verify_session(read_sample(repo_root, SP_SAMPLE), cfg(repo_root), root)
    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.status == verify.STATUS_VERIFIED
    assert verdict.reason == verify.REASON_NONE
    assert verdict.red.exit_code == 1 and verdict.red.outcome == verify.OUTCOME_FAILED
    assert verdict.green.exit_code == 0 and verdict.green.outcome == verify.OUTCOME_PASSED
    assert "ZeroDivisionError" in verdict.red.excerpt, verdict.red.excerpt
    assert verdict.generated_at == "2026-01-02T03:04:07.007000Z", "生成时间取日志里的 end_ts（禁时钟）"

    written = verify.write_verified(str(root), verdicts)
    assert len(written) == 1
    target = root / "tests" / "regression" / "test_sess_demo_0009_4.py"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == verdict.content

    after = digest(root)
    assert {key: value for key, value in after.items() if not key.startswith("tests/")} == before, "源码树逐字节不变（K11）"
    assert not (root / ".pytest_cache").exists(), "测试跑在隔离副本里，目标仓库不该留下 pytest 缓存"


def test_unverified_episode_is_not_written_into_the_tests_dir(tmp_path, repo_root):
    """没有活动断言的骨架：修复前那次跑"通过" → 红不成立 → unverified，**不落盘**。"""
    root = make_repo(tmp_path, {FORMAT_REL: "def slugify(name):\n    return name\n"})
    file_read = read_sample(repo_root, MB_SAMPLE)
    plans = verify.plans_of_session(file_read, cfg(repo_root), repo=str(root))
    second = [item for item in plans if item.episode_id.endswith("#9")]
    assert second, [item.episode_id for item in plans]

    verdicts = verify.verify_plans(second, cfg(repo_root), str(root))
    assert verdicts[0].status == verify.STATUS_UNVERIFIED
    assert verdicts[0].reason == verify.REASON_RED_NOT_FAILED
    assert verdicts[0].files, "结论里要留下逐文件证据（为什么验不过）"
    assert verify.write_verified(str(root), verdicts) == []
    assert not (root / "tests").exists(), "未通过项一个文件都不许进正式测试目录"


def test_needs_manual_without_content_needs_no_repo_and_is_not_run(tmp_path, repo_root):
    """生成层没产出内容 → 没有可跑的东西：`needs_manual`（两次退出码为 None，**没跑**）。"""
    verdicts = verify.verify_session(read_sample(repo_root, AMBIGUOUS_SAMPLE), cfg(repo_root), repo=None)
    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.status == verify.STATUS_NEEDS_MANUAL
    assert verdict.reason == generate.REASON_RECONSTRUCTION_NOT_USABLE + ":" + reconstruct.STATUS_UNRESOLVED
    assert verdict.red is None and verdict.green is None, "没跑就不许填退出码"
    assert verdict.test_path and not verdict.written


def test_only_verified_entries_are_written_in_a_mixed_batch(tmp_path, repo_root):
    root = make_repo(tmp_path)
    file_read = read_sample(repo_root, MB_SAMPLE)
    plans = verify.plans_of_session(file_read, cfg(repo_root), repo=str(root))
    verdicts = verify.verify_plans(plans, cfg(repo_root), str(root))
    assert [item.status for item in verdicts] == [verify.STATUS_VERIFIED, verify.STATUS_UNVERIFIED]

    written = verify.write_verified(str(root), verdicts)
    assert len(written) == 1
    produced = sorted(str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*.py") if path.parts[len(root.parts)] == "tests")
    assert produced == ["tests/regression/test_sess_demo_0003_4.py"], produced


# --------------------------------------------------------------------------- #
# 四类 unverified 原因各一条
# --------------------------------------------------------------------------- #


def test_red_not_failed_is_reported_when_the_test_passes_before_the_fix(tmp_path, repo_root):
    root = make_repo(tmp_path)
    plan = curated_plan(TRUE_BEFORE, TRUE_AFTER, '"""骨架"""\n\n\ndef test_demo():\n    assert True\n')
    verdict = verify.verify_plans([plan], cfg(repo_root), str(root))[0]
    assert verdict.status == verify.STATUS_UNVERIFIED
    assert verdict.reason == verify.REASON_RED_NOT_FAILED
    assert verdict.red.exit_code == 0 and verdict.green.exit_code == 0


def test_green_not_passed_is_reported_when_the_fix_does_not_help(tmp_path, repo_root):
    """修复前后一模一样 → 红成立、绿不成立（"改了但没修好"必须与"没验证"分得开）。"""
    root = make_repo(tmp_path)
    plan = curated_plan(TRUE_BEFORE, TRUE_BEFORE, FAILING_TEST)
    verdict = verify.verify_plans([plan], cfg(repo_root), str(root))[0]
    assert verdict.status == verify.STATUS_UNVERIFIED
    assert verdict.reason == verify.REASON_GREEN_NOT_PASSED
    assert verdict.red.exit_code == 1 and verdict.green.exit_code == 1


def test_environment_unavailable_when_the_file_is_not_in_the_target(tmp_path, repo_root):
    """日志里的路径在目标仓库里定位不到 → 绝不猜路径硬写（那是在改用户仓库）→ 环境不可用。"""
    root = make_repo(tmp_path, {FORMAT_REL: "def slugify(name):\n    return name\n"})
    plan = curated_plan(TRUE_BEFORE, TRUE_AFTER, FAILING_TEST, relative=("demo", "nowhere", "missing.py"))
    verdict = verify.verify_plans([plan], cfg(repo_root), str(root))[0]
    assert verdict.status == verify.STATUS_UNVERIFIED
    assert verdict.reason == verify.REASON_ENVIRONMENT_UNAVAILABLE
    assert verdict.red is None and verdict.green is None
    assert not (root / "demo" / "nowhere").exists(), "不许替用户新建目录/文件"


def test_timeout_is_reported_as_its_own_reason(tmp_path, repo_root):
    root = make_repo(tmp_path)
    plan = curated_plan(TRUE_BEFORE, TRUE_AFTER, SLEEPING_TEST)
    started = verify.verify_plans([plan], cfg(repo_root), str(root), timeout=1)[0]
    assert started.status == verify.STATUS_UNVERIFIED
    assert started.reason == verify.REASON_TIMEOUT
    assert started.red.outcome == verify.OUTCOME_TIMEOUT
    assert started.red.exit_code is None


def test_every_unverified_reason_is_a_declared_enum_value():
    assert verify.UNVERIFIED_REASONS == (
        verify.REASON_RED_NOT_FAILED,
        verify.REASON_GREEN_NOT_PASSED,
        verify.REASON_ENVIRONMENT_UNAVAILABLE,
        verify.REASON_TIMEOUT,
    )
    assert set(verify.STATUSES) == {verify.STATUS_VERIFIED, verify.STATUS_UNVERIFIED, verify.STATUS_NEEDS_MANUAL}


# --------------------------------------------------------------------------- #
# 依赖门 / 入参
# --------------------------------------------------------------------------- #


def test_missing_pytest_is_a_dependency_error(tmp_path, repo_root, monkeypatch):
    """缺 pytest → `DependencyMissing`（exit 3），**不得**退化成"零条结果 + exit 0"。"""
    root = make_repo(tmp_path)
    monkeypatch.setattr(verify, "pytest_available", lambda: False)
    with pytest.raises(DependencyMissing) as excinfo:
        verify.verify_plans(verify.plans_of_session(read_sample(repo_root, SP_SAMPLE), cfg(repo_root), repo=str(root)), cfg(repo_root), str(root))
    assert excinfo.value.exit_code == errors.EXIT_DEPENDENCY_MISSING


def test_verification_requires_a_repo_only_when_there_is_something_to_run(tmp_path, repo_root):
    plan = curated_plan(TRUE_BEFORE, TRUE_AFTER, FAILING_TEST)
    with pytest.raises(UsageError):
        verify.verify_plans([plan], cfg(repo_root), None)
    with pytest.raises(InputUnavailable):
        verify.verify_plans([plan], cfg(repo_root), str(tmp_path / "nope"))


def test_missing_root_verdicts_never_count_as_verified():
    plan = curated_plan(TRUE_BEFORE, TRUE_AFTER, FAILING_TEST)
    verdicts = verify.unreachable_verdicts([plan])
    assert verdicts[0].status == verify.STATUS_UNVERIFIED
    assert verdicts[0].reason == verify.REASON_ENVIRONMENT_UNAVAILABLE
    assert verify.red_green_facts(verdicts)[0]["status"] == verify.STATUS_UNVERIFIED


# --------------------------------------------------------------------------- #
# 确定性
# --------------------------------------------------------------------------- #


def test_verification_is_byte_identical_across_runs(tmp_path, repo_root):
    """同输入双跑逐字节一致（含日志摘录：摘录进了 manifest 就必须可复现）。"""
    root = make_repo(tmp_path)
    loaded = cfg(repo_root)
    first = [item.to_dict() for item in verify.verify_session(read_sample(repo_root, SP_SAMPLE), loaded, root)]
    second = [item.to_dict() for item in verify.verify_session(read_sample(repo_root, SP_SAMPLE), loaded, root)]
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)


def test_fact_contract_is_locked(tmp_path, repo_root):
    """对账字段集是契约：真值的键 ↔ `Verdict.fact()` 的键逐一对上。"""
    from s2r import bench

    root = make_repo(tmp_path)
    verdict = verify.verify_session(read_sample(repo_root, SP_SAMPLE), cfg(repo_root), root)[0]
    assert set(verdict.fact()) == set(bench.RED_GREEN_FACTS)
    assert "red_green" in bench.CHECKED_FACTS
    assert set(verdict.to_dict()) >= {
        "episode_id", "session_id", "source", "generated_at", "generator_version",
        "status", "reason", "test_path", "written", "files", "red", "green",
    }
