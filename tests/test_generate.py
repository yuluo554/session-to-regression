"""M4 生成器测试：约定探测、AST 读签名、候选断言推导、needs_manual 原因、确定性、K11。

三条最贵的判据（都是"假门"高发区）：

1. **模板不是空转**：改一段 diff 描述 → 候选断言必须随之变化。只断言"生成了文件"的测试，
   在模板把 diff 整个忽略掉时照样绿；
2. **宁缺勿造是真的**：还原状态不是 `ok` 时必须**不产文件**，而不是把片段拼成一份骨架；
   符号识别不到时同样不产出——"没生成"要能被断言（少一个文件 ≠ 少一个断言面）；
3. **只写测试目录**（K11）：跑一次生成前后，目标仓库**源码树**逐文件哈希不变。
"""

from __future__ import annotations

import ast
import hashlib
import py_compile
from pathlib import Path

import pytest

from s2r import config, episode as episode_mod, generate, readers, reconstruct
from s2r.errors import InputUnavailable, UsageError

SP_SAMPLE = Path("data/bench/claude/structured-patch-1.jsonl")
AMBIGUOUS_SAMPLE = Path("data/bench/claude/ambiguous-old-string-1.jsonl")
NEW_FILE_SAMPLE = Path("data/bench/claude/new-file-write-1.jsonl")

TRUE_BEFORE = "def divide(a, b):\n    return a / b\n"
TRUE_AFTER = "def divide(a, b):\n    return a / b if b else 0\n"
GUARD_HUNKS = [
    {"old_start": 2, "old_lines": ["return a / b"], "new_lines": ["return a / b if b else 0"], "old_count": 1, "new_count": 1}
]


def cfg(repo_root: Path):
    return config.load_config(repo_root / "config" / "scoring.toml")


def write_text(path: Path, text: str) -> Path:
    """3.8 通道：`Path.write_text(newline=...)` 是 3.10+ 参数，只能用 `open(..., newline=...)`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    return path


def limits(repo_root: Path):
    return readers.limits_from_config(cfg(repo_root))


def read_sample(repo_root: Path, relative: Path):
    return readers.read_file(repo_root / relative, limits(repo_root))


def hunks_with(new_line: str, old_line: str = "return a / b"):
    return [{"old_start": 2, "old_lines": [old_line], "new_lines": [new_line], "old_count": 1, "new_count": 1}]


def target_for(before: str, hunks):
    tree = generate.parse_before(before)
    assert tree is not None
    lines = generate.changed_lines(before, hunks)
    assert lines, "改动行必须能定位（否则这条用例验的不是符号选择）"
    symbol = generate.pick_symbol(tree, lines)
    assert symbol is not None, "改动行必须落在某个定义里"
    target = generate.target_of(symbol)
    assert target is not None
    return target


# --------------------------------------------------------------------------- #
# 约定探测
# --------------------------------------------------------------------------- #


def test_convention_probe_reads_a_configured_repo(tmp_path, repo_root):
    target = tmp_path / "target"
    (target / "tests").mkdir(parents=True)
    (target / "src" / "pkg").mkdir(parents=True)
    (target / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = \"-q\"\n", encoding="utf-8")
    (target / "tests" / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef demo():\n    return 1\n", encoding="utf-8"
    )

    convention = generate.probe_conventions(str(target), cfg(repo_root))
    assert convention.assumed is False, convention.to_dict()
    assert convention.pytest_config == "pyproject.toml[tool.pytest.ini_options]"
    assert convention.conftest == "tests/conftest.py"
    assert convention.fixture_style == "fixture"
    assert convention.package_root == "src"
    assert convention.tests_dir == config.generate_params(cfg(repo_root))["tests_dir"]


def test_convention_probe_falls_back_to_assumed_defaults(tmp_path, repo_root):
    """探测不到就落保守默认，**并显式标出哪些面没确认**（不许静默用默认值冒充探测结果）。"""
    target = tmp_path / "bare"
    target.mkdir()
    convention = generate.probe_conventions(str(target), cfg(repo_root))
    assert convention.assumed is True
    assert set(convention.assumed_parts) == set(generate.CONVENTION_PARTS)
    assert convention.tests_dir == config.generate_params(cfg(repo_root))["assumed_tests_dir"]

    without_root = generate.probe_conventions(None, cfg(repo_root))
    assert without_root.assumed is True and without_root.assumed_parts


def test_convention_probe_rejects_a_missing_target(tmp_path, repo_root):
    with pytest.raises(InputUnavailable):
        generate.probe_conventions(str(tmp_path / "nope"), cfg(repo_root))


# --------------------------------------------------------------------------- #
# 符号与候选断言（模板不是空转）
# --------------------------------------------------------------------------- #


def test_symbol_selection_prefers_the_innermost_definition(repo_root):
    before = "class Demo:\n    def outer(self, x):\n        return x\n\n    def inner(self, y):\n        return y\n"
    hunks = [{"old_start": 6, "old_lines": ["    return y"], "new_lines": ["    return y or 1"], "old_count": 1, "new_count": 1}]
    target = target_for(before, hunks)
    assert target.qualified == "Demo.inner"
    assert target.args == ["y"], "方法目标必须丢掉首个形参（接收者由 arrange 提供）"
    assert target.bind_as_method is True


def test_candidate_is_derived_from_the_guard_shape(repo_root):
    target = target_for(TRUE_BEFORE, GUARD_HUNKS)
    candidate = generate.candidate_of(GUARD_HUNKS, target, cfg(repo_root))
    assert candidate is not None
    assert candidate.probe == "0", "探测值取 `else` 分支的字面量（同型假值才能把守卫弄假）"
    assert candidate.assert_line == "assert result == 0"
    assert "return a / b" in candidate.evidence


def test_changing_the_diff_changes_the_candidate(repo_root):
    """变异验证（防模板空转）：**只改 diff 描述**，候选断言必须随之变化。"""
    target = target_for(TRUE_BEFORE, GUARD_HUNKS)
    base = generate.candidate_of(GUARD_HUNKS, target, cfg(repo_root))
    mutated = generate.candidate_of(hunks_with("return a / b if b else -1"), target, cfg(repo_root))
    assert base is not None and mutated is not None
    assert base.assert_line != mutated.assert_line, "改了 diff 却不影响输出 = 模板在空转"
    assert mutated.assert_line == "assert result == -1"
    assert base.evidence != mutated.evidence, "证据注释也要跟着 diff 变（它是候选的依据）"


def test_candidate_needs_a_guard_on_a_parameter(repo_root):
    """条件不是裸形参 → 不许猜（改不了守卫的真假，就推不出可证伪的断言）。"""
    target = target_for(TRUE_BEFORE, GUARD_HUNKS)
    assert generate.candidate_of(hunks_with("return a / b if zero else 0"), target, cfg(repo_root)) is None
    assert generate.candidate_of(hunks_with("return a / b if b > 0 else 0"), target, cfg(repo_root)) is None
    assert generate.candidate_of(hunks_with("return a / b if b and a else 0"), target, cfg(repo_root)) is None


def test_candidate_needs_a_literal_fallback(repo_root):
    """`else` 不是字面量（推不出期望值）→ 不推候选（空容器字面量与 None/0 都是合法字面量）。"""
    target = target_for(TRUE_BEFORE, GUARD_HUNKS)
    assert generate.candidate_of(hunks_with("return a / b if b else other"), target, cfg(repo_root)) is None
    empty_list = generate.candidate_of(hunks_with("return [] if b else []"), target, cfg(repo_root))
    assert empty_list is not None and empty_list.assert_line == "assert result == []"


def test_wrapped_return_value_is_not_a_candidate(repo_root):
    """返回值被包了一层调用（`x` → `abs(x)`）→ 推不出能证伪的断言，如实落 needs_manual。"""
    before = "def amount(value):\n    return value\n"
    hunks = [{"old_start": 2, "old_lines": ["return value"], "new_lines": ["return abs(value)"], "old_count": 1, "new_count": 1}]
    target = target_for(before, hunks)
    assert generate.candidate_of(hunks, target, cfg(repo_root)) is None


def test_vararg_signature_has_no_constructible_probe(repo_root):
    """签名含可变参数 → 最小调用构造不出（arity 未知）→ `target_of` 返回 None。"""
    tree = generate.parse_before("def run(*args):\n    return args\n")
    symbols = generate.symbols_of(tree)
    assert generate.target_of(symbols[0]) is None
    tree2 = generate.parse_before("def run(a, **kwargs):\n    return a\n")
    assert generate.target_of(generate.symbols_of(tree2)[0]) is None


def test_changed_lines_refuses_ambiguous_locations(repo_root):
    """旧侧文本在 before 里出现多次 → 定位不唯一 → 不猜（与 M3 的 `unresolved` 同一条纪律）。"""
    before = "def a(v):\n    return v\n\n\ndef b(v):\n    return v\n"
    hunks = [{"old_start": None, "old_lines": ["    return v"], "new_lines": ["    return v + 1"], "old_count": 1, "new_count": 1}]
    assert generate.changed_lines(before, hunks) is None


def test_slug_is_stable_and_safe():
    assert generate.slugify("sess-demo-0003#4") == "sess_demo_0003_4"
    assert generate.slugify("sess-demo-0003#4") == generate.slugify("sess-demo-0003#4")
    assert generate.slugify("会话#1") == "1"
    assert generate.slugify("###") == ""


# --------------------------------------------------------------------------- #
# 骨架内容：三段 + 证据锚点
# --------------------------------------------------------------------------- #


def test_skeleton_has_three_sections_and_evidence_anchors(repo_root):
    file_read = read_sample(repo_root, SP_SAMPLE)
    skeletons = generate.plan_session(file_read, cfg(repo_root))
    assert len(skeletons) == 1
    skeleton = skeletons[0]
    assert skeleton.status == generate.STATUS_GENERATED
    assert skeleton.content, "生成的骨架必须有内容"

    params = config.generate_params(cfg(repo_root))
    for name in params["sections"]:
        assert generate.SECTION_MARKER.format(name) in skeleton.content, name
    assert params["assert_placeholder"] in skeleton.content
    assert params["candidate_note"] in skeleton.content, "候选断言必须注明需人工确认"
    assert "a = 0" in skeleton.content and "b = 0" in skeleton.content, "arrange 要按签名绑最小输入"
    assert "result = divide(a, b)" in skeleton.content, "act 要调用被改动行所属的符号"

    anchors = skeleton.anchors
    assert anchors["session_id"] == "sess-demo-0009"
    assert anchors["start_ts"] != "unknown" and anchors["end_ts"] != "unknown"
    for key in ("session_id", "user_report", "failure_excerpt"):
        assert anchors[key] and anchors[key] in skeleton.content, key
    assert "/demo/alpha/src/calc.py" in skeleton.content
    assert skeleton.candidates == ["assert result == 0"]
    assert skeleton.candidates[0] in skeleton.content


def test_failure_excerpt_comes_from_the_log_line_with_a_signal(repo_root):
    """摘录必须取**带失败信号的那一行**（不是文本首行——Codex 的首行是 "Chunk ID"）。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    skeleton = generate.plan_session(file_read, cfg(repo_root))[0]
    excerpt = skeleton.anchors["failure_excerpt"]
    signals = config.vocabulary(cfg(repo_root))["failure_signals"]
    assert any(signal in excerpt for signal in signals), excerpt
    assert excerpt in skeleton.content


def test_planning_is_byte_identical_across_runs(repo_root):
    """同一 episode → 逐字节相同骨架（K5：无时钟、无随机；中间层就断言）。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    first = generate.plan_session(file_read, cfg(repo_root))[0]
    second = generate.plan_session(read_sample(repo_root, SP_SAMPLE), cfg(repo_root))[0]
    assert first.content == second.content
    assert first.slug == second.slug
    assert first.relative_path == second.relative_path


def test_report_is_kept_verbatim_even_with_quotes_and_backslashes(repo_root):
    """证据锚点不许被转义层吃掉：含引号/反斜杠/换行的报告原文要**逐字**出现在生成物里，且仍可编译。"""
    backslash = chr(92)
    report = 'demo-user said: "it breaks" and C:' + backslash + "work" + backslash + "app.py\nsecond line"
    skeleton = _skeleton_with_report(repo_root, report)
    assert report in skeleton.content, "报告原文必须逐字出现（转义会改字节，锚点就查不到了）"
    ast.parse(skeleton.content)


def test_report_with_conflicting_quotes_falls_back_to_comments(repo_root):
    """两种三引号都出现在原文里 → 整块退化成注释，锚点仍然逐字在文件里（且仍可编译）。"""
    report = 'bad """ and \'\'\' together'
    skeleton = _skeleton_with_report(repo_root, report)
    assert report in skeleton.content
    ast.parse(skeleton.content)


def _skeleton_with_report(repo_root: Path, report: str) -> generate.Skeleton:
    """用一份"最小会话"驱动完整生成路径（只替换报告原文，其它都是真路径）。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    loaded = cfg(repo_root)
    episode = episode_stub(file_read, loaded, user_report=report)
    files = ok_files_of(file_read, loaded, episode.files)
    skeleton = generate.plan_episode(episode, files, file_read, loaded, generate.probe_conventions(None, loaded))
    return skeleton


def episode_stub(file_read, loaded, user_report=None):
    """复用真 episode（别在测试里另写一套 episode 形状），可选只换报告原文。"""
    session_id = generate._session_id_of(file_read)
    split = episode_mod.split(file_read.events, loaded, session_files={session_id: str(file_read.path)})
    assert split.episodes, "样本必须能切出 episode"
    entity = split.episodes[0]
    if user_report is not None:
        entity.user_report = user_report
    return entity


def ok_files_of(file_read, loaded, episode_files):
    reconstruction = reconstruct.reconstruct_session(file_read, loaded)
    wanted = set(episode_files)
    return [item for item in reconstruction.files if item.status == reconstruct.STATUS_OK and item.file in wanted]


# --------------------------------------------------------------------------- #
# needs_manual：原因必须具体，且"不生成文件"是一条断言
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "relative, status, reason",
    [
        (AMBIGUOUS_SAMPLE, reconstruct.STATUS_UNRESOLVED, "reconstruction_not_usable:unresolved"),
        (NEW_FILE_SAMPLE, reconstruct.STATUS_NO_BEFORE, "reconstruction_not_usable:no_before_image"),
    ],
)
def test_unusable_reconstruction_never_reaches_the_generation_path(relative, status, reason, repo_root):
    file_read = read_sample(repo_root, relative)
    skeletons = generate.plan_session(file_read, cfg(repo_root))
    assert len(skeletons) == 1, "每个 episode 都要有结论（不许因为不好生成就整条消失）"
    skeleton = skeletons[0]
    assert skeleton.status == generate.STATUS_NEEDS_MANUAL
    assert skeleton.reason == reason
    assert skeleton.content == "", "还原状态不是 ok → 宁缺勿造，不产出文件"
    assert skeleton.written is False


def test_non_python_change_is_not_generated(repo_root):
    """真实会话里改到 .md / .css 一类的修复不少（实测抽验：非 Python 是 `before_unparsable` 的全部来源）
    ——首期只生成 Python 测试，所以要有一个**说得出原因**的出口，而不是笼统的"解析不了"。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    loaded = cfg(repo_root)
    episode = episode_stub(file_read, loaded)
    item = _reconstruction_of("/demo/alpha/docs/notes.md", "run it\n", "run it with --demo\n", [{"old_start": 1, "old_lines": ["run it"], "new_lines": ["run it with --demo"], "old_count": 1, "new_count": 1}])
    episode.files = ["/demo/alpha/docs/notes.md"]
    skeleton = generate.plan_episode(episode, [item], file_read, loaded, generate.probe_conventions(None, loaded))
    assert skeleton.reason == generate.REASON_NOT_PYTHON
    assert skeleton.content == "" and skeleton.written is False


def test_symbol_not_identified_produces_no_file(repo_root):
    """改动行落在模块级语句上（不属于任何定义）→ 识别不到符号 → 不生成文件。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    loaded = cfg(repo_root)
    episode = episode_stub(file_read, loaded, user_report="demo report")
    before = "LIMIT = 1\n\n\ndef other(x):\n    return x\n"
    item = _reconstruction_of(episode.files[0], before, "LIMIT = 2\n\n\ndef other(x):\n    return x\n", [{"old_start": 1, "old_lines": ["LIMIT = 1"], "new_lines": ["LIMIT = 2"], "old_count": 1, "new_count": 1}])
    skeleton = generate.plan_episode(episode, [item], file_read, loaded, generate.probe_conventions(None, loaded))
    assert skeleton.reason == generate.REASON_SYMBOL_NOT_IDENTIFIED
    assert skeleton.content == ""


def test_before_unparsable_produces_no_file(repo_root):
    file_read = read_sample(repo_root, SP_SAMPLE)
    loaded = cfg(repo_root)
    episode = episode_stub(file_read, loaded, user_report="demo report")
    item = _reconstruction_of(episode.files[0], "def broken(:\n", "def broken(:\n", [{"old_start": 1, "old_lines": ["def broken(:"], "new_lines": ["def broken():"], "old_count": 1, "new_count": 1}])
    skeleton = generate.plan_episode(episode, [item], file_read, loaded, generate.probe_conventions(None, loaded))
    assert skeleton.reason == generate.REASON_BEFORE_UNPARSABLE
    assert skeleton.content == ""


def test_changed_lines_unknown_produces_no_file(repo_root):
    """hunk 定位不出来（旧侧文本在 before 里找不到）→ 不猜，如实记原因。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    loaded = cfg(repo_root)
    episode = episode_stub(file_read, loaded, user_report="demo report")
    item = _reconstruction_of(episode.files[0], TRUE_BEFORE, TRUE_AFTER, [{"old_start": None, "old_lines": ["return a * b"], "new_lines": ["return a * b if b else 0"], "old_count": 1, "new_count": 1}])
    skeleton = generate.plan_episode(episode, [item], file_read, loaded, generate.probe_conventions(None, loaded))
    assert skeleton.reason == generate.REASON_CHANGED_LINES_UNKNOWN
    assert skeleton.content == ""


def test_assert_not_derivable_writes_a_placeholder_skeleton(repo_root):
    """推不出候选断言时**仍然产出骨架**（有 arrange/act/三段与锚点），但标 needs_manual。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    loaded = cfg(repo_root)
    episode = episode_stub(file_read, loaded, user_report="demo report")
    after = "def divide(a, b):\n    return round(a / b, 2)\n"
    item = _reconstruction_of(episode.files[0], TRUE_BEFORE, after, [{"old_start": 2, "old_lines": ["return a / b"], "new_lines": ["    return round(a / b, 2)"], "old_count": 1, "new_count": 1}])
    skeleton = generate.plan_episode(episode, [item], file_read, loaded, generate.probe_conventions(None, loaded))
    assert skeleton.status == generate.STATUS_NEEDS_MANUAL
    assert skeleton.reason == generate.REASON_ASSERT_NOT_DERIVABLE
    assert skeleton.content
    assert skeleton.candidates == []
    params = config.generate_params(cfg(repo_root))
    assert params["assert_placeholder"] in skeleton.content
    assert "assert " not in skeleton.content.split(params["assert_placeholder"])[1], "推不出来就不许写活动断言"


def test_probe_not_derivable_writes_a_placeholder_skeleton(repo_root):
    file_read = read_sample(repo_root, SP_SAMPLE)
    loaded = cfg(repo_root)
    episode = episode_stub(file_read, loaded, user_report="demo report")
    before = "def run(*args):\n    return args\n"
    after = "def run(*args):\n    return tuple(args)\n"
    item = _reconstruction_of(episode.files[0], before, after, [{"old_start": 2, "old_lines": ["return args"], "new_lines": ["    return tuple(args)"], "old_count": 1, "new_count": 1}])
    skeleton = generate.plan_episode(episode, [item], file_read, loaded, generate.probe_conventions(None, loaded))
    assert skeleton.status == generate.STATUS_NEEDS_MANUAL
    assert skeleton.reason == generate.REASON_PROBE_NOT_DERIVABLE
    params = config.generate_params(cfg(repo_root))
    assert params["arrange_placeholder"] in skeleton.content
    assert params["act_placeholder"] in skeleton.content


def _reconstruction_of(file: str, before: str, after: str, hunks) -> reconstruct.Reconstruction:
    return reconstruct.Reconstruction(
        file=file,
        before=before,
        after=after,
        hunks=list(hunks),
        source=reconstruct.SOURCE_CLAUDE_OLD_STRING,
        confidence=reconstruct.CONFIDENCE_HIGH,
        status=reconstruct.STATUS_OK,
    )


# --------------------------------------------------------------------------- #
# 写盘：只写测试目录（K11）
# --------------------------------------------------------------------------- #


def test_write_skeletons_only_touches_the_tests_dir(tmp_path, repo_root):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    source = write_text(target / "src" / "calc.py", TRUE_BEFORE)
    before_digest = _tree_digest(target)

    file_read = read_sample(repo_root, SP_SAMPLE)
    skeletons = generate.plan_session(file_read, cfg(repo_root), repo=str(target))
    written = generate.write_skeletons(str(target), skeletons, cfg(repo_root))

    params = config.generate_params(cfg(repo_root))
    assert written and all(params["tests_dir"] in path.replace("\\", "/") for path in written)
    for path in written:
        assert Path(path).is_file()
    unchanged = {key: value for key, value in _tree_digest(target).items() if not key.startswith(params["tests_dir"])}
    assert unchanged == before_digest, "生成只许写测试目录，源码树一个字节都不许动（K11）"


def test_write_requires_an_explicit_target(tmp_path, repo_root):
    file_read = read_sample(repo_root, SP_SAMPLE)
    skeletons = generate.plan_session(file_read, cfg(repo_root))
    with pytest.raises(UsageError):
        generate.write_skeletons(None, skeletons, cfg(repo_root))
    with pytest.raises(InputUnavailable):
        generate.write_skeletons(str(tmp_path / "nope"), skeletons, cfg(repo_root))


def test_written_skeleton_compiles_and_declares_the_episode(tmp_path, repo_root):
    file_read = read_sample(repo_root, SP_SAMPLE)
    skeletons = generate.plan_session(file_read, cfg(repo_root))
    written = generate.write_skeletons(str(tmp_path), skeletons, cfg(repo_root))
    assert written
    for path in written:
        py_compile.compile(path, doraise=True)
        text = Path(path).read_text(encoding="utf-8")
        assert not text.startswith("\ufeff"), "不许带 BOM"
        assert "\r" not in text, "不许有 CR（生成物也走 serialize 单点）"
        assert text.endswith("\n")


def _tree_digest(root: Path):
    return {
        str(path.relative_to(root)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------- #
# 事实面
# --------------------------------------------------------------------------- #


def test_summarize_reports_only_the_declared_anchor_fragments(repo_root):
    """锚点是"**声明过的**片段是否真的在骨架里"——没声明的片段不进事实（别把事实做成转储）。"""
    file_read = read_sample(repo_root, SP_SAMPLE)
    skeletons = generate.plan_session(file_read, cfg(repo_root))
    summary = generate.summarize(skeletons, anchors=["sess-demo-0009", "不存在的片段"])
    assert summary["anchor_fragments"] == ["sess-demo-0009"]
    assert summary["generated_count"] == 1 and summary["written_count"] == 1
    assert summary["statuses"] == [generate.STATUS_GENERATED]
    assert summary["sections"] == sorted(config.generate_params(cfg(repo_root))["sections"])
    assert summary["assumed_convention"] is True, "没有 --repo 时应显式标出惯例是保守默认"


def test_empty_session_yields_no_skeleton(repo_root):
    """没有 episode → 空集（空集也是断言：不许从"没切出来的东西"里生成测试）。"""
    file_read = read_sample(repo_root, Path("data/bench/claude/no-failure-signal-1.jsonl"))
    skeletons = generate.plan_session(file_read, cfg(repo_root))
    assert skeletons == []
    summary = generate.summarize(skeletons)
    assert summary["generated_count"] == 0 and summary["statuses"] == []


def test_parse_episode_ref_requires_both_halves():
    assert generate.parse_episode_ref("data/bench/x.jsonl#4") == ("data/bench/x.jsonl", "4")
    assert generate.parse_episode_ref("claude:sess-demo-0003#9") == ("claude:sess-demo-0003", "9")
    for bad in ("sess-demo-0003", "#4", "sess-demo-0003#", ""):
        with pytest.raises(UsageError):
            generate.parse_episode_ref(bad)
