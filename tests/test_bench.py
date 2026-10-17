"""M1 基准测试：参照生成器（benchdata）+ 对账器（bench）。

三条最贵的判据（方法论里踩过的坑）：

1. **两层确定性**：冻结产物自身形态正确 **且** 重跑生成器逐字节一致（只断言前者是假门——
   生成器被改坏时读盘断言照样绿）；
2. **真值只存输入与期望**：真值里出现 `observed_*` 即红（否则基准退化成"比对两份预先算好的答案"）；
3. **探活必须能红**：往对账路径注入已知缺陷 → 基准必须报未达标；探活自己"直接记通过"就是假门。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from s2r import bench, benchdata, config, readers, serialize
from s2r.errors import InputUnavailable

SKIP_KEYS = ("observed", "measured", "actual_")


def limits(repo_root: Path):
    return readers.limits_from_config(config.load_config(repo_root / "config" / "scoring.toml"))


def cfg(repo_root: Path):
    return config.load_config(repo_root / "config" / "scoring.toml")


# --------------------------------------------------------------------------- #
# 生成器：两层确定性 + 真值形态 + 词表来源
# --------------------------------------------------------------------------- #


def test_regeneration_is_byte_identical_to_frozen_corpus(tmp_path, repo_root):
    """第二层：真跑一遍生成器 → 与冻结语料逐字节比对（含 manifest 与真值）。"""
    frozen = repo_root / "data" / "bench"
    assert frozen.is_dir(), "冻结语料必须在仓（否则基准没有可复现的输入）"
    benchdata.write_corpus(tmp_path, cfg(repo_root))
    assert benchdata.diff_trees(frozen, tmp_path) == []


def test_two_generations_are_byte_identical(tmp_path, repo_root):
    """同一次运行内两次生成也必须一致（第一层：产物确定性）。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    benchdata.write_corpus(first, cfg(repo_root))
    benchdata.write_corpus(second, cfg(repo_root))
    assert benchdata.diff_trees(first, second) == []


def test_rng_is_actually_consumed(tmp_path, repo_root):
    """RNG 实例化了却零调用 → "确定性生成"的变异验证永远变不红（假确定性）。"""
    stats = benchdata.write_corpus(tmp_path, cfg(repo_root))
    assert stats["draws"] > 0, "RNG 必须被真消费（用于多套预置方案选型）"
    manifest = json.loads(serialize.read_text(tmp_path / benchdata.MANIFEST_NAME))
    assert manifest["rng_draws"] == stats["draws"]


def test_truth_files_contain_only_inputs_and_expectations(tmp_path, repo_root):
    """K9：真值里不得出现观测类字段（冻结"观测"会让基准在引擎全坏时照样全绿）。"""
    benchdata.write_corpus(tmp_path, cfg(repo_root))
    checked = 0
    for truth in sorted((tmp_path / "truth").glob("*.truth.json")):
        data = json.loads(serialize.read_text(truth))
        checked += 1
        assert set(data) >= {"sample", "input_file", "expect", "expect_episodes"}
        for key in _all_keys(data):
            assert not any(marker in key for marker in SKIP_KEYS), "真值出现观测类字段: {} ({})".format(key, truth.name)
    assert checked >= 5, "扫描面过小"


def _all_keys(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            for item in _all_keys(value, prefix):
                yield item
    elif isinstance(node, list):
        for value in node:
            for item in _all_keys(value, prefix):
                yield item


def test_every_configured_failure_signal_appears_in_the_corpus(tmp_path, repo_root):
    """语料里的失败输出由**配置词表**拼装（源码零词面）——所以词表加词必须让语料重生成。

    这条同时是"词表单一来源"的自证：换词/加词后不重生成 → 本测试红。
    """
    benchdata.write_corpus(tmp_path, cfg(repo_root))
    corpus = "".join(serialize.read_text(path) for path in sorted(tmp_path.rglob("*.jsonl")))
    signals = config.vocabulary(cfg(repo_root))["failure_signals"]
    assert signals, "配置里失败信号表为空"
    missing = [signal for signal in signals if signal not in corpus]
    assert not missing, "语料缺少配置里的失败信号: {}".format(missing)


def test_every_configured_bug_term_appears_in_the_corpus(tmp_path, repo_root):
    benchdata.write_corpus(tmp_path, cfg(repo_root))
    corpus = "".join(serialize.read_text(path) for path in sorted(tmp_path.rglob("*.jsonl")))
    terms = config.vocabulary(cfg(repo_root))["bug_terms"]
    used = [term for term in terms if term in corpus]
    assert used, "语料里一个 bug 语义词都没有（扫描面为空）"


def test_manifest_declares_coverage_thresholds_and_pending_facts(tmp_path, repo_root):
    benchdata.write_corpus(tmp_path, cfg(repo_root))
    manifest = json.loads(serialize.read_text(tmp_path / benchdata.MANIFEST_NAME))
    for episode_type, minimum in manifest["coverage"]["min_count_by_type"].items():
        observed = manifest["coverage"]["observed_count_by_type"].get(episode_type, 0)
        assert observed >= minimum, "{}: 生成量 {} < 门槛 {}".format(episode_type, observed, minimum)
    assert "episodes" not in manifest["pending_facts"], "M2 起 episode 已真对账，不该再挂在 pending 里"
    assert "before_after" not in manifest["pending_facts"], "M3 起 before/after 已真对账，pending 里不许再有它"
    assert "generated_tests" not in manifest["pending_facts"], "M4 起生成内容已真对账，pending 里不许再有它"
    assert manifest["pending_facts"] == {}, "M5 起没有未对账的面：空表是断言（不许留挡箭牌）"
    assert "episode_count" in manifest["checked_facts"], "episode 事实必须进对账面"
    assert "reconstructions" in manifest["checked_facts"], "M3 还原事实必须进对账面"
    assert "generated" in manifest["checked_facts"], "M4 生成事实必须进对账面"
    assert "red_green" in manifest["checked_facts"], "M5 验证事实必须进对账面"
    assert manifest["thresholds"]["max_false_positive"] == 0
    # 磁盘基线夹具也是冻结物（Codex 侧的全文 before 只能来自这里）
    assert manifest["repo_fixtures"], "磁盘基线夹具必须在台账里"
    for fixture in manifest["repo_fixtures"]:
        path = tmp_path / fixture["file"]
        assert path.is_file(), fixture
        assert serialize.sha256_bytes(path.read_bytes()) == fixture["sha256"], fixture


def test_privacy_self_check_rejects_a_real_looking_path(tmp_path, repo_root):
    """变异验证：往语料里塞一条白名单外的绝对路径 → 生成必须失败（隐私自检不是摆设）。"""
    leaked = "B" + chr(58) + chr(92) + "work" + chr(92) + "secret.py"
    records = [{"type": "user", "message": {"role": "user", "content": "see " + leaked}}]
    with pytest.raises(SystemExit) as excinfo:
        benchdata.assert_privacy(records, "mutation-sample")
    assert "隐私自检失败" in str(excinfo.value)


def test_refusing_to_overwrite_frozen_corpus_without_force(tmp_path, repo_root):
    """破坏性动作纪律：冻结语料默认不覆盖，要显式 --force。"""
    benchdata.write_corpus(tmp_path, cfg(repo_root))
    with pytest.raises(SystemExit) as excinfo:
        benchdata.write_corpus(tmp_path, cfg(repo_root))
    assert "force" in str(excinfo.value)
    benchdata.write_corpus(tmp_path, cfg(repo_root), force=True)  # 显式开关放行


# --------------------------------------------------------------------------- #
# 对账器：双边门 + 探活
# --------------------------------------------------------------------------- #


def test_bench_reports_ok_on_frozen_corpus(repo_root):
    report = bench.run(repo_root / "data" / "bench", limits(repo_root), cfg(repo_root))
    assert report["status"] == "ok"
    gates = report["gates"]
    assert gates["detection"]["matched"] == gates["detection"]["checked"] > 0
    assert gates["detection"]["rate"] >= gates["detection"]["min_rate"]
    assert gates["false_positive"]["count"] == 0
    assert gates["ledger"]["ok"] and gates["coverage"]["ok"] and gates["files_present"]["ok"]
    assert report["pending_facts"] == {}, "M5 起没有未对账的面（空表也要在报告里显式可见）"
    assert set(report["checked_facts"]) >= {"episode_count", "reconstructions", "generated", "red_green"}

@pytest.mark.parametrize("probe", bench.PROBES)
def test_probes_are_detected(probe, repo_root):
    """探活：注入已知缺陷 → 必须报未达标。四个探活里任何一个变成"没检出"就是基准失灵。"""
    report = bench.run(repo_root / "data" / "bench", limits(repo_root), cfg(repo_root), probe=probe)
    assert report["probe_detected"] is True
    assert report["status"] == "probe_detected"


def test_unknown_probe_name_is_an_input_error(repo_root):
    with pytest.raises(InputUnavailable):
        bench.run(repo_root / "data" / "bench", limits(repo_root), cfg(repo_root), probe="no-such-probe")


def test_tampered_corpus_is_caught_by_the_ledger_gate(tmp_path, repo_root):
    """冻结物被改过 → 台账门必须红（sha256 是"字节即契约"的可执行形态）。"""
    copy = tmp_path / "bench"
    shutil.copytree(str(repo_root / "data" / "bench"), str(copy))
    target = sorted((copy / "claude").glob("*.jsonl"))[0]
    target.write_bytes(target.read_bytes().replace(b"demo", b"DEMO"))
    report = bench.run(copy, limits(repo_root), cfg(repo_root))
    assert report["status"] != "ok"
    assert report["gates"]["ledger"]["ok"] is False
    assert report["gates"]["ledger"]["mismatches"]


def test_missing_sample_file_is_caught(tmp_path, repo_root):
    copy = tmp_path / "bench"
    shutil.copytree(str(repo_root / "data" / "bench"), str(copy))
    sorted((copy / "codex").glob("*.jsonl"))[0].unlink()
    report = bench.run(copy, limits(repo_root), cfg(repo_root))
    assert report["status"] != "ok"
    assert report["gates"]["files_present"]["ok"] is False


def test_only_filter_restricts_the_checked_set(repo_root):
    report = bench.run(repo_root / "data" / "bench", limits(repo_root), cfg(repo_root), only=["happy_path"])
    assert report["gates"]["detection"]["checked"] == 1
    assert report["status"] == "ok"


def test_missing_manifest_is_an_input_error(tmp_path, repo_root):
    with pytest.raises(InputUnavailable) as excinfo:
        bench.run(tmp_path, limits(repo_root), cfg(repo_root))
    assert "manifest" in str(excinfo.value)


def test_summary_lines_expose_failures_and_no_pending_left(repo_root):
    """摘要必须把失败样本摆出来，且 M5 之后**不该再有任何**未对账的面。"""
    report = bench.run(repo_root / "data" / "bench", limits(repo_root), cfg(repo_root), only=["unsupported_format"])
    lines = bench.summary_lines(report)
    assert not any("pending" in line for line in lines), "M5 起 pending 为空，摘要里不该出现它"
    assert not report["pending_facts"]
    assert "red_green" in report["checked_facts"], "M5 的验证事实要在对账面清单里可见"


def test_generated_mismatch_is_attributed_to_the_sample(tmp_path, repo_root):
    """生成层对账必须点名到 `generated.<键>`——只断言"某样本 fail"会被别的判据蹭走（M2 的教训）。

    变异：真值声明"这个样本不该有候选断言"，而对账实际有 → 必须报 `generated.candidate_count`。
    """
    copy = tmp_path / "bench"
    shutil.copytree(str(repo_root / "data" / "bench"), str(copy))
    truth = copy / "truth" / "structured-patch-1.truth.json"
    data = json.loads(serialize.read_text(truth))
    data["expect_generated"]["candidate_count"] = 0
    _write_json(truth, data)

    report = bench.run(copy, limits(repo_root), cfg(repo_root), only=["structured_patch"])
    entry = [item for item in report["samples"] if item["sample"] == "structured-patch-1"][0]
    assert entry["status"] == "fail", entry
    assert any("generated.candidate_count" in item for item in entry["mismatches"]), entry["mismatches"]
    assert report["status"] != "ok"
    assert any("generated.candidate_count" in line for line in bench.summary_lines(report))


def test_declared_anchor_must_really_appear_in_the_skeleton(tmp_path, repo_root):
    """锚点判据不是摆设：真值声明一个**不在骨架里**的片段 → 对账必须红。"""
    copy = tmp_path / "bench"
    shutil.copytree(str(repo_root / "data" / "bench"), str(copy))
    truth = copy / "truth" / "structured-patch-1.truth.json"
    data = json.loads(serialize.read_text(truth))
    data["expect_generated"]["anchor_fragments"] = ["这个片段不在骨架里", "sess-demo-0009"]
    _write_json(truth, data)

    report = bench.run(copy, limits(repo_root), cfg(repo_root), only=["structured_patch"])
    entry = [item for item in report["samples"] if item["sample"] == "structured-patch-1"][0]
    assert entry["status"] == "fail", entry
    assert any("generated.anchor_fragments" in item for item in entry["mismatches"]), entry["mismatches"]


def _write_json(path: Path, data) -> None:
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialize.dumps_json(data))


def test_reconstruction_mismatch_is_attributed_to_the_sample(tmp_path, repo_root):
    """还原对账必须点名到**具体文件的具体字段**（否则"两边不等"说不出坏在哪）。

    用"把 before 改成 after"这个最典型的还原错误做变异：改真值 → 对账必须红，
    且差异里要出现 `reconstructions[...].before`（不是一句笼统的"不一致"）。
    """
    copy = tmp_path / "bench"
    shutil.copytree(str(repo_root / "data" / "bench"), str(copy))
    truth = copy / "truth" / "structured-patch-1.truth.json"
    data = json.loads(serialize.read_text(truth))
    data["expect_reconstructions"][0]["before"] = "def divide(a, b):\n    return a / b if b else 0\n"
    with open(str(truth), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialize.dumps_json(data))

    report = bench.run(copy, limits(repo_root), cfg(repo_root), only=["structured_patch"])
    entry = [item for item in report["samples"] if item["sample"] == "structured-patch-1"][0]
    assert entry["status"] == "fail", entry
    assert any("reconstructions[" in item and ".before" in item for item in entry["mismatches"]), entry["mismatches"]
    assert report["status"] != "ok"

    lines = bench.summary_lines(report)
    assert any("reconstructions[" in line for line in lines), lines


def test_corpus_exercises_every_degradation_path(repo_root):
    """语料必须**真的走到**每一条降级路径（M2 的教训：样本没走到 = 路径没验过）。

    三条降级路径 + 一个第三态各有具名样本：① 结构化补丁、② 子串定位、③ Codex 的两条通道，
    以及 `unresolved` / `partial` / `no_before_image` 三个"不许猜"的出口。
    """
    from s2r import reconstruct

    manifest = bench.load_manifest(repo_root / "data" / "bench")
    statuses = set()
    sources = set()
    for sample in manifest["samples"]:
        truth = json.loads(serialize.read_text(repo_root / "data" / "bench" / sample["truth"]))
        for item in truth.get("expect_reconstructions") or []:
            statuses.add(item["status"])
            sources.add(item["source"])
    assert statuses == set(reconstruct.STATUSES), "还原状态的覆盖不完整: {}".format(sorted(set(reconstruct.STATUSES) - statuses))
    for source in (reconstruct.SOURCE_CLAUDE_PATCH, reconstruct.SOURCE_CLAUDE_OLD_STRING, reconstruct.SOURCE_CODEX_DIFF, reconstruct.SOURCE_CODEX_PATCH):
        assert source in sources, "降级路径缺样本: {}".format(source)


def test_reconstruction_fact_contract_is_locked():
    """对账字段集是契约：真值声明哪些键、`Reconstruction.fact()` 就产出哪些键。"""
    from s2r import reconstruct

    fact = reconstruct.Reconstruction(
        file="/demo/a.py", before="a\n", after="b\n", hunks=[], source=reconstruct.SOURCE_NONE, confidence=reconstruct.CONFIDENCE_NONE
    ).fact()
    assert set(fact) == set(bench.RECONSTRUCTION_FACTS)
    assert "reconstructions" in bench.CHECKED_FACTS
    assert set(bench.PROBES) >= {"swap-before-after"}


def test_episode_mismatch_is_attributed_to_the_sample(tmp_path, repo_root):
    """逐样本点名必须指到**具体字段**，而且要确认这条判据真的在跑。

    反面教材（首版）：只断言"摘要里出现样本名"——台账门也会打一行含样本名的失败，
    于是断言被**另一条判据**满足，把"episode 对账"整段删掉后测试照样绿（张冠李戴）。
    正解 = 断言只锚 episode 判据：样本条目的 mismatches 里必须出现 `episode_count`。
    """
    copy = tmp_path / "bench"
    shutil.copytree(str(repo_root / "data" / "bench"), str(copy))
    truth = copy / "truth" / "happy-path-1.truth.json"
    truth.write_bytes(truth.read_bytes().replace(b'"expect_episodes": 1', b'"expect_episodes": 3'))

    report = bench.run(copy, limits(repo_root), cfg(repo_root), only=["happy_path"])
    entry = [item for item in report["samples"] if item["sample"] == "happy-path-1"][0]
    assert entry["status"] == "fail", entry
    assert any("episode_count" in item for item in entry["mismatches"]), entry["mismatches"]
    assert report["status"] != "ok"

    lines = bench.summary_lines(report)
    assert any(("happy-path-1" in line) and ("episode_count" in line) for line in lines), lines


# --------------------------------------------------------------------------- #
# M5：验证层事实（RED/GREEN）
# --------------------------------------------------------------------------- #


def test_red_green_fact_contract_is_locked():
    """对账字段集是契约：真值声明哪些键、`Verdict.fact()` 就产出哪些键。"""
    from s2r import verify

    verdict = verify.Verdict(
        episode_id="sess-demo-1#1", session_id="sess-demo-1", source="claude", source_file="x.jsonl",
        generated_at="t", episode_status="verified_by_run", generation_status="generated",
        generation_reason="", assumed_convention=True, assumed_parts=[], test_path="tests/x.py",
        status=verify.STATUS_VERIFIED, reason=verify.REASON_NONE,
    )
    assert set(verdict.fact()) == set(bench.RED_GREEN_FACTS)
    assert "red_green" in bench.CHECKED_FACTS
    assert "fold-unverified" in bench.PROBES


def test_corpus_really_exercises_red_green(repo_root):
    """语料必须**真的跑到**验证层：不能只有"声明"没有"实跑"。

    判据：真值里既有 verified（两次跑真的一红一绿），也有 unverified + 未失败（"没有活动断言"那条路径），
    还有 needs_manual（生成层没产出内容 → 两次退出码为 None）。三者缺一，"三态不折叠"就只是口号。
    """
    manifest = bench.load_manifest(repo_root / "data" / "bench")
    statuses = set()
    reasons = set()
    red_exits = set()
    green_exits = set()
    for sample in manifest["samples"]:
        truth = json.loads(serialize.read_text(repo_root / "data" / "bench" / sample["truth"]))
        for item in truth.get("expect_red_green") or []:
            statuses.add(item["status"])
            if item["reason"]:
                reasons.add(item["reason"])
            red_exits.add(item["red_exit"])
            green_exits.add(item["green_exit"])
    assert statuses == {"verified", "unverified", "needs_manual"}, statuses
    assert "red_not_failed" in reasons, "语料里要有『测试没失败』这条路径（needs_manual 骨架不许被当成通过）"
    assert 1 in red_exits, "必须有一条真的一红（RED 退出码 1）"
    assert 0 in green_exits, "必须有一条真的一绿（GREEN 退出码 0）"
    assert None in red_exits, "没有内容可跑的条目两次退出码必须是 None（没跑就是没跑）"


def test_red_green_mismatch_is_attributed_to_the_episode(tmp_path, repo_root):
    """验证层对账必须点名到 `red_green[<episode>].<键>`（否则"没验过"与"验过"分不清）。"""
    copy = tmp_path / "bench"
    shutil.copytree(str(repo_root / "data" / "bench"), str(copy))
    truth = copy / "truth" / "structured-patch-1.truth.json"
    data = json.loads(serialize.read_text(truth))
    data["expect_red_green"][0]["status"] = "unverified"
    _write_json(truth, data)

    report = bench.run(copy, limits(repo_root), cfg(repo_root), only=["structured_patch"])
    entry = [item for item in report["samples"] if item["sample"] == "structured-patch-1"][0]
    assert entry["status"] == "fail", entry
    assert any("red_green[sess-demo-0009#4].status" in item for item in entry["mismatches"]), entry["mismatches"]
    assert report["status"] != "ok"
    assert any("red_green[sess-demo-0009#4].status" in line for line in bench.summary_lines(report))
