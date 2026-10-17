"""M2 episode 切分器：判定原语 + 状态机 + 边界情形 + 确定性 + "口径只读配置"。

判据全部来自题面 §2 M2 与 `data/README.md` §六 的实测结论；四类边界情形各至少一条断言。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

import pytest

from s2r import config, episode, events

SESSION = "sess-demo-0001"
VISIBLE = "/demo/alpha/src/calc.py"


def cfg(repo_root):
    return config.load_config(repo_root / "config" / "scoring.toml")


#: 语义词表在配置里（测试**不复制词面**，运行时取）——K13/K20
def vocab(repo_root):
    return config.vocabulary(cfg(repo_root))


def mk(seq: int, kind: str, *, tool: str = events.UNKNOWN, path: str = events.UNKNOWN, payload: Dict[str, Any] = None, session: str = SESSION, source: str = "claude") -> events.Event:
    return events.Event(
        source=source,
        session_id=session,
        seq=seq,
        kind=kind,
        ts="2026-01-02T03:04:05.{:06d}Z".format(seq),
        tool=tool,
        path=path,
        payload=dict(payload or {}),
    )


def bug_text(repo_root, extra: str = "divide() returns wrong value") -> str:
    """用配置里的语义词构造用户报告（源码/测试零词面）。"""
    term = vocab(repo_root)["bug_terms"][0]
    return "用户说 {}：{}".format(term, extra)


def fail_text(repo_root, exit_code=None) -> Dict[str, Any]:
    signals = vocab(repo_root)["failure_signals"]
    if exit_code is not None:
        return {"exit_code": exit_code, "exit_code_source": "process_exited", "text": "Output:"}
    return {"text": "\n".join(signals) + "\nrest of output"}


def pass_text(repo_root, exit_code=None) -> Dict[str, Any]:
    marker = vocab(repo_root)["test_pass_markers"][0]
    if exit_code is not None:
        return {"exit_code": exit_code, "exit_code_source": "process_exited", "text": "Output:"}
    return {"text": "1 {} in 0.02s".format(marker)}


def make_test_command(repo_root) -> str:
    return "{} tests/test_calc.py -q".format(vocab(repo_root)["test_command_markers"][0])


# --------------------------------------------------------------------------- #
# 判定原语
# --------------------------------------------------------------------------- #


def test_bug_term_matching_uses_word_boundaries_for_ascii(repo_root):
    """`debug` 不得命中词表里的 `bug`——ASCII 词按词边界匹配。"""
    terms = vocab(repo_root)["bug_terms"]
    assert episode.matches_bug_term("this is a bug in divide()", terms) is not None
    assert episode.matches_bug_term("debugging the exporter", terms) is None
    assert episode.matches_bug_term("BUG: value is wrong", terms) is not None, "大小写不敏感"
    assert episode.matches_bug_term("everything is fine", terms) is None


def test_failure_evidence_prefers_the_numeric_channel(repo_root):
    signals = vocab(repo_root)["failure_signals"]
    ok = mk(1, "tool_result", payload=pass_text(repo_root, exit_code=0))
    bad = mk(2, "tool_result", payload=fail_text(repo_root, exit_code=1))
    assert episode.failure_evidence(ok, signals) is None, "exit_code == 0 不许因文本里有信号词就判失败"
    assert episode.failure_evidence(bad, signals) == "exit_code"

    claude_fail = mk(3, "tool_result", payload=fail_text(repo_root))
    assert episode.failure_evidence(claude_fail, signals).startswith("text_signal:")

    explicit = mk(4, "tool_result", payload={"is_error": True, "text": "boom"})
    assert episode.failure_evidence(explicit, signals) == "is_error"

    call = mk(5, "tool_call", tool="Bash", payload={"command": "x"})
    assert episode.failure_evidence(call, signals) is None, "只有 tool_result 才算证据"


def test_pass_evidence_needs_a_marker_on_the_claude_side(repo_root):
    signals = vocab(repo_root)["failure_signals"]
    markers = vocab(repo_root)["test_pass_markers"]
    assert episode.pass_evidence(mk(1, "tool_result", payload=pass_text(repo_root, exit_code=0)), signals, markers)
    assert not episode.pass_evidence(mk(2, "tool_result", payload=pass_text(repo_root, exit_code=1)), signals, markers)
    assert episode.pass_evidence(mk(3, "tool_result", payload=pass_text(repo_root)), signals, markers)
    assert not episode.pass_evidence(mk(4, "tool_result", payload={"text": "no marker here"}), signals, markers)
    mixed = {"text": "1 {} but AssertionError in teardown".format(markers[0])}
    assert not episode.pass_evidence(mk(5, "tool_result", payload=mixed), signals, markers), "同现失败信号时不算通过"


def test_command_normalization_collapses_whitespace(repo_root):
    assert episode.normalize_command("python   -m   pytest  -q ") == "python -m pytest -q"


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #


def claude_chain(repo_root, session: str = SESSION, base: int = 0, pass_after_fix: bool = True, command: str = None) -> List[events.Event]:
    cmd = command or make_test_command(repo_root)
    seq = base
    out = [
        mk(seq + 1, "user_msg", payload={"text": bug_text(repo_root)}, session=session),
        mk(seq + 2, "tool_call", tool="Bash", payload={"command": cmd, "tool_use_id": "t-run-{}".format(base)}, session=session),
        mk(seq + 3, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="t-run-{}".format(base)), session=session),
        mk(seq + 4, "tool_call", tool="Edit", path=VISIBLE, payload={"file_path": VISIBLE, "has_old_string": True}, session=session),
        mk(seq + 5, "tool_result", tool="Edit", path=VISIBLE, payload={"file_path": VISIBLE, "has_original_file": True, "has_patch_hunks": True, "tool_use_id": "t-edit-{}".format(base)}, session=session),
    ]
    if pass_after_fix:
        out.extend(
            [
                mk(seq + 6, "tool_call", tool="Bash", payload={"command": cmd, "tool_use_id": "t-run2-{}".format(base)}, session=session),
                mk(seq + 7, "tool_result", tool="Bash", payload=dict(pass_text(repo_root), tool_use_id="t-run2-{}".format(base)), session=session),
            ]
        )
    return out


def test_happy_path_verified_by_run(repo_root):
    result = episode.split(claude_chain(repo_root), cfg(repo_root), {SESSION: "demo.jsonl"})
    assert len(result.episodes) == 1 and not result.filtered
    item = result.episodes[0]
    assert item.status == "verified_by_run"
    assert item.start_seq == 1 and item.end_seq == 7
    assert item.episode_id == "{}{}".format(SESSION, "#4"), "id_format = 会话 id + 首次修复事件序号"
    assert item.files == [VISIBLE]
    assert item.score == sum(item.score_breakdown.values()) > 0
    assert set(item.score_breakdown) == {"failure_signal", "reconstruction", "test_command", "code_change"}


def test_ended_without_pass_when_no_pass_observed(repo_root):
    result = episode.split(claude_chain(repo_root, pass_after_fix=False), cfg(repo_root), {SESSION: "demo.jsonl"})
    assert len(result.episodes) == 1
    assert result.episodes[0].status == "ended_without_pass"


def test_multi_bug_in_one_session_yields_two_episodes_with_distinct_ids(repo_root):
    chain_a = claude_chain(repo_root, session="sess-demo-0002", base=0, pass_after_fix=False)
    chain_b = claude_chain(repo_root, session="sess-demo-0002", base=10, pass_after_fix=True, command=make_test_command(repo_root) + " -k slug")
    result = episode.split(chain_a + chain_b, cfg(repo_root), {"sess-demo-0002": "demo.jsonl"})
    assert len(result.episodes) == 2
    ids = [item.episode_id for item in result.episodes]
    assert len(set(ids)) == 2, "episode_id 必须互不撞"
    assert [item.status for item in result.episodes] == ["ended_without_pass", "verified_by_run"]
    assert result.episodes[0].start_seq < result.episodes[1].start_seq


def test_pass_before_failure_does_not_close_the_episode(repo_root):
    """先成功后失败：早先的通过不闭合候选；失败后仍需修复 + 通过才算 verified。"""
    cmd = make_test_command(repo_root)
    evs = [
        mk(1, "tool_call", tool="Bash", payload={"command": cmd, "tool_use_id": "early"}),
        mk(2, "tool_result", tool="Bash", payload=dict(pass_text(repo_root), tool_use_id="early")),
        mk(3, "user_msg", payload={"text": bug_text(repo_root)}),
        mk(4, "tool_call", tool="Bash", payload={"command": cmd, "tool_use_id": "fail"}),
        mk(5, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="fail")),
        mk(6, "tool_call", tool="Edit", path=VISIBLE, payload={"file_path": VISIBLE, "has_old_string": True}),
        mk(7, "tool_call", tool="Bash", payload={"command": cmd, "tool_use_id": "retry"}),
        mk(8, "tool_result", tool="Bash", payload=dict(pass_text(repo_root), tool_use_id="retry")),
    ]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert len(result.episodes) == 1
    assert result.episodes[0].status == "verified_by_run"
    assert result.episodes[0].start_seq == 3
    assert result.episodes[0].edits[0]["seq"] == 6


def test_different_test_command_passing_does_not_verify(repo_root):
    """题面：终点是"**同一**测试命令转为通过"——换了命令不算 verified（宁可落 ended_without_pass）。"""
    evs = claude_chain(repo_root, pass_after_fix=False)
    other = make_test_command(repo_root) + " -k other_suite"
    evs.extend(
        [
            mk(8, "tool_call", tool="Bash", payload={"command": other, "tool_use_id": "other"}),
            mk(9, "tool_result", tool="Bash", payload=dict(pass_text(repo_root), tool_use_id="other")),
        ]
    )
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert len(result.episodes) == 1
    assert result.episodes[0].status == "ended_without_pass"


def test_codex_chain_with_exit_codes(repo_root):
    cmd = make_test_command(repo_root)
    evs = [
        mk(1, "user_msg", payload={"text": bug_text(repo_root)}, source="codex"),
        mk(2, "tool_call", tool="shell_command", payload={"command": cmd, "call_id": "call-1"}, source="codex"),
        mk(3, "tool_result", tool="shell_command", payload=dict(fail_text(repo_root, exit_code=1), call_id="call-1"), source="codex"),
        mk(4, "tool_call", tool="apply_patch", path=VISIBLE, payload={"call_id": "call-2", "files": [VISIBLE], "has_input": True}, source="codex"),
        mk(5, "tool_result", tool="apply_patch", payload={"call_id": "call-2", "exit_code": 0}, source="codex"),
        mk(6, "tool_call", tool="shell_command", payload={"command": cmd, "call_id": "call-3"}, source="codex"),
        mk(7, "tool_result", tool="shell_command", payload=dict(pass_text(repo_root, exit_code=0), call_id="call-3"), source="codex"),
    ]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo-rollout.jsonl"})
    assert len(result.episodes) == 1
    assert result.episodes[0].source == "codex"
    assert result.episodes[0].status == "verified_by_run"
    assert result.episodes[0].source_file == "demo-rollout.jsonl"


# --------------------------------------------------------------------------- #
# 边界：四种丢弃理由
# --------------------------------------------------------------------------- #


def test_question_only_is_not_an_episode(repo_root):
    evs = [
        mk(1, "user_msg", payload={"text": "帮我解释一下这个函数怎么用"}),
        mk(2, "assistant_msg", payload={"text": "这个函数……"}),
    ]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert result.episodes == []
    assert result.filtered == [], "普通提问连候选都不该起（不是「丢了」）"


def test_bug_without_failure_evidence_is_filtered(repo_root):
    evs = [mk(1, "user_msg", payload={"text": bug_text(repo_root)}), mk(2, "assistant_msg", payload={"text": "让我看看"})]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert result.episodes == []
    assert [item["reason"] for item in result.filtered] == [episode.DROP_NO_FAILURE]


def test_failure_without_fix_is_filtered(repo_root):
    evs = [
        mk(1, "user_msg", payload={"text": bug_text(repo_root)}),
        mk(2, "tool_call", tool="Bash", payload={"command": make_test_command(repo_root), "tool_use_id": "r"}),
        mk(3, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="r")),
    ]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert result.episodes == []
    assert [item["reason"] for item in result.filtered] == [episode.DROP_NO_FIX]


def test_docs_only_fix_is_filtered_even_with_high_score(repo_root):
    """只改文档：即使分数够（失败信号 + 测试命令）也丢弃——理由必须显式。"""
    doc = "/demo/alpha/docs/notes.md"
    evs = [
        mk(1, "user_msg", payload={"text": bug_text(repo_root)}),
        mk(2, "tool_call", tool="Bash", payload={"command": make_test_command(repo_root), "tool_use_id": "r"}),
        mk(3, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="r")),
        mk(4, "tool_call", tool="Edit", path=doc, payload={"file_path": doc, "has_old_string": True}),
        mk(5, "tool_result", tool="Edit", path=doc, payload={"file_path": doc, "has_original_file": True, "has_patch_hunks": True}),
    ]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert result.episodes == []
    assert [item["reason"] for item in result.filtered] == [episode.DROP_DOCS_ONLY]
    assert result.filtered[0]["score"] > 0, "分数照算（可见），只是规则上丢弃"


def test_new_file_without_before_image_is_kept_but_scores_no_diff(repo_root):
    """纯新建文件：保留为 episode（M3 要如实记 no_before_image），但拿不到 diff 权重。"""
    new_file = "/demo/alpha/src/helper.py"
    evs = [
        mk(1, "user_msg", payload={"text": bug_text(repo_root)}),
        mk(2, "tool_call", tool="Bash", payload={"command": make_test_command(repo_root), "tool_use_id": "r"}),
        mk(3, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="r")),
        mk(4, "tool_call", tool="Write", path=new_file, payload={"file_path": new_file, "has_content": True}),
        mk(5, "tool_result", tool="Write", path=new_file, payload={"file_path": new_file, "has_structured_patch": True, "has_patch_hunks": False, "has_original_file": False}),
    ]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert len(result.episodes) == 1
    assert "reconstruction" not in result.episodes[0].score_breakdown, "空 structuredPatch 不是 diff 证据"


def test_below_threshold_candidate_is_filtered_with_reason(repo_root):
    """只有失败信号、没有 diff/测试命令/代码改动 → 低于阈值丢弃（reason 可见）。"""
    evs = [
        mk(1, "user_msg", payload={"text": bug_text(repo_root)}),
        mk(2, "tool_call", tool="Bash", payload={"command": "echo hello", "tool_use_id": "r"}),
        mk(3, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="r")),
        mk(4, "tool_call", tool="Edit", path="/demo/alpha/src/calc.py", payload={"file_path": "/demo/alpha/src/calc.py"}),
    ]
    result = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"})
    assert result.episodes == []
    assert [item["reason"] for item in result.filtered] == [episode.DROP_BELOW_THRESHOLD]
    assert result.filtered[0]["score"] == config.weights(cfg(repo_root))["failure_signal"] + config.weights(cfg(repo_root))["code_change"]


# --------------------------------------------------------------------------- #
# 确定性 + 口径只读配置
# --------------------------------------------------------------------------- #


def test_split_is_deterministic_and_order_insensitive(repo_root):
    evs = claude_chain(repo_root)
    first = episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"}).to_dict()
    second = episode.split(list(reversed(evs)), cfg(repo_root), {SESSION: "demo.jsonl"}).to_dict()
    assert first == second, "输入顺序不影响结果（内部按 (source, session_id, seq) 排序）"


def test_scoring_weights_are_read_from_config_not_hardcoded(repo_root):
    """把权重全改成 0 → 同一输入落到 below_threshold（证明打分只读配置）。"""
    mutated = copy.deepcopy(cfg(repo_root))
    for key in mutated["scoring"]["weights"]:
        mutated["scoring"]["weights"][key] = 0
    result = episode.split(claude_chain(repo_root), mutated, {SESSION: "demo.jsonl"})
    assert result.episodes == []
    assert [item["reason"] for item in result.filtered] == [episode.DROP_BELOW_THRESHOLD]


def test_threshold_zero_keeps_a_weak_candidate(repo_root):
    """阈值改成 0 → 弱候选被保留（阈值同样只读配置）。"""
    mutated = copy.deepcopy(cfg(repo_root))
    mutated["scoring"]["threshold"]["min_score"] = 0
    evs = [
        mk(1, "user_msg", payload={"text": bug_text(repo_root)}),
        mk(2, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="r")),
        mk(3, "tool_call", tool="Edit", path=VISIBLE, payload={"file_path": VISIBLE}),
    ]
    assert episode.split(evs, cfg(repo_root), {SESSION: "demo.jsonl"}).episodes == []
    assert len(episode.split(evs, mutated, {SESSION: "demo.jsonl"}).episodes) == 1


def test_episode_id_format_comes_from_config(repo_root):
    mutated = copy.deepcopy(cfg(repo_root))
    mutated["episode"]["id_format"] = "EP::{session_id}::{first_fix_seq}"
    result = episode.split(claude_chain(repo_root), mutated, {SESSION: "demo.jsonl"})
    assert result.episodes[0].episode_id == "EP::{}::4".format(SESSION)


def test_doc_suffixes_come_from_config(repo_root):
    """把 .md 从"文档后缀"里拿掉 → 只改 .md 的那个候选不再按 docs_only 丢。"""
    doc = "/demo/alpha/docs/notes.md"
    evs = [
        mk(1, "user_msg", payload={"text": bug_text(repo_root)}),
        mk(2, "tool_call", tool="Bash", payload={"command": make_test_command(repo_root), "tool_use_id": "r"}),
        mk(3, "tool_result", tool="Bash", payload=dict(fail_text(repo_root), tool_use_id="r")),
        mk(4, "tool_call", tool="Edit", path=doc, payload={"file_path": doc, "has_old_string": True}),
        mk(5, "tool_result", tool="Edit", path=doc, payload={"file_path": doc, "has_original_file": True, "has_patch_hunks": True}),
    ]
    assert episode.split(evs, cfg(repo_root), {}).filtered[0]["reason"] == episode.DROP_DOCS_ONLY
    mutated = copy.deepcopy(cfg(repo_root))
    mutated["episode"]["doc_suffixes"] = [".rst"]
    assert episode.split(evs, mutated, {}).episodes, "改成不认 .md 后它就成了代码改动"


def test_session_file_map_links_sessions_to_sources(tmp_path, repo_root):
    from s2r import readers, serialize

    path = tmp_path / "s.jsonl"
    line = {
        "cwd": "/demo",
        "entrypoint": "cli",
        "isSidechain": False,
        "sessionId": SESSION,
        "timestamp": "2026-01-02T03:04:05.000Z",
        "type": "user",
        "userType": "external",
        "uuid": "u",
        "version": "2.0.0-demo",
        "message": {"role": "user", "content": bug_text(repo_root)},
    }
    with open(str(path), "w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialize.dumps_json_line(line))
    scan = readers.scan([str(path)], readers.limits_from_config(cfg(repo_root)), cfg=cfg(repo_root))
    assert episode.session_file_map(scan)[SESSION] == str(path)
    assert scan.to_dict()["schema_version"] == readers.SCAN_SCHEMA_VERSION
