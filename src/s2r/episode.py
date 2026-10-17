"""M2：bug-fix episode 切分器（确定性规则，无模型）。

规则按序、可回溯（题面 §2 M2）：

1. **起点**：`user_msg` 命中 bug/失败语义词；
2. **失败信号**：其后出现 `tool_result` 失败证据——**数值通道优先**（Codex `exit_code != 0`），
   文本信号其次，Claude 的 `is_error` 是显式标记（K16：`Exit code` 类信号必须取数值再判，标记命中不算失败）；
3. **修复动作**：失败信号之后出现文件写类工具（Claude `Edit`/`Write`/`MultiEdit`；
   Codex `apply_patch` / `file_change`）；
4. **终点**：**同一测试命令**转为通过 → `verified_by_run`；会话结束（或新 bug 报告到来）→ `ended_without_pass`。

判定纪律（三条都是"会打挂测试"的口径）：

* 语义词表、**测试命令标记**、通过标记全部从 `config/scoring.toml` 读（源码零词面，K13/K20）；
* 打分权重与阈值只读配置；打分是确定性规则，不是模型 judgement；
* 丢弃的候选**逐条给出 reason**（`no_failure_evidence` / `no_fix_action` / `docs_only` /
  `below_threshold`）并进 `filtered[]`——"没切出来"与"没看到"必须可区分，不许静默消失。

边界情形（各有断言，见 `tests/test_episode.py`）：一会话多 bug（各成独立 episode、id 不撞）、
一次修复后又失败（追加证据，不新开候选）、失败后未修复（丢 `no_fix_action`）、
用户仅提问（无失败证据 → 丢 `no_failure_evidence`）、只改文档（丢 `docs_only`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import config as config_mod
from . import events

#: 文件写类工具（Claude 侧按工具名；Codex 侧靠 `apply_patch` 与结果侧 `file_change`）
CLAUDE_WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
CODEX_WRITE_TOOLS = ("apply_patch", "file_change")

#: 丢弃原因码（对外可见的枚举）
DROP_NO_FAILURE = "no_failure_evidence"
DROP_NO_FIX = "no_fix_action"
DROP_DOCS_ONLY = "docs_only"
DROP_BELOW_THRESHOLD = "below_threshold"

_ASCII_ONLY = re.compile(r"^[\x20-\x7e]+$")


# --------------------------------------------------------------------------- #
# 判定原语（全部读配置，源码零词面）
# --------------------------------------------------------------------------- #


def matches_bug_term(text: str, terms: Sequence[str]) -> Optional[str]:
    """命中哪个 bug 语义词（返回词本身，便于回执与调试）。

    ASCII 词按**词边界**匹配——否则 `debug` 会命中词表里的 `bug`；中文词按子串匹配
    （中文无词边界形态）。两者都大小写不敏感。
    """
    if not text:
        return None
    lowered = text.lower()
    for term in terms:
        if _ASCII_ONLY.match(term):
            pattern = r"(?<![0-9a-z_])" + re.escape(term.lower()) + r"(?![0-9a-z_])"
            if re.search(pattern, lowered):
                return term
        elif term.lower() in lowered:
            return term
    return None


def failure_evidence(event: events.Event, signals: Sequence[str]) -> Optional[str]:
    """一条事件是否构成失败证据；返回证据类型或 None。

    优先级即判据顺序：**数值通道**（Codex 退出码）→ `is_error`（Claude 显式标记）→ 文本信号。
    数值存在时**只看数值**：`exit_code == 0` 直接判"不是失败"，不去文本里找信号。
    """
    if event.kind != "tool_result":
        return None
    code = event.payload.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return "exit_code" if code != 0 else None
    if event.payload.get("is_error") is True:
        return "is_error"
    text = event.payload.get("text")
    if isinstance(text, str) and text:
        for signal in signals:
            if signal in text:
                return "text_signal:{}".format(signal)
    return None


def normalize_command(command: str) -> str:
    """命令归一化：折叠空白 + 去首尾——"同一测试命令"的比对键，别被空格差异骗过去。"""
    return " ".join(command.split())


def test_command_of(event: events.Event, markers: Sequence[str]) -> Optional[str]:
    """命令文本命中测试标记 → 归一化命令；否则 None。"""
    if event.kind != "tool_call":
        return None
    command = event.payload.get("command")
    if not isinstance(command, str) or not command:
        return None
    lowered = command.lower()
    for marker in markers:
        if marker.lower() in lowered:
            return normalize_command(command)
    return None


def pass_evidence(event: events.Event, signals: Sequence[str], pass_markers: Sequence[str]) -> bool:
    """一条 tool_result 是否表示"测试通过"。

    Codex：`exit_code == 0` 即通过。Claude：没有退出码 → 必须**既无失败信号**、
    **又命中通过标记**（缺标记不算通过——宁可落 `ended_without_pass`，也不虚报 verified）。
    """
    if event.kind != "tool_result":
        return False
    code = event.payload.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code == 0
    text = event.payload.get("text")
    if not isinstance(text, str) or not text:
        return False
    if any(signal in text for signal in signals):
        return False
    return any(marker in text for marker in pass_markers)


def is_write_tool(event: events.Event) -> bool:
    if event.kind not in ("tool_call", "tool_result"):
        return False
    return event.tool in CLAUDE_WRITE_TOOLS or event.tool in CODEX_WRITE_TOOLS


def has_before_image(event: events.Event) -> bool:
    """该事件是否带来"可还原的 before/after"证据（M2 版的 diff 判定）。

    判据（K14 的**非空**版本）：Claude 的 `originalFile`（修改前全文）、`old_string`
    （可定位替换）、**非空** `structuredPatch`（有 hunk）；Codex 的 patch 原文
    （`has_input` + `files`）与 `file_change` 的 `unified_diff`/`content`。

    ⚠️ 为什么必须"非空"：实测新建文件的结果同样带 `structuredPatch: []`（82 条空表），
    只看"键存在"会把**纯新建文件**也算成有 diff 证据，白送 +3 分（本项目合成语料当场抓到这个）。

    这里只回答"**有没有**证据"，"还原得对不对"是 M3 的事。
    """
    payload = event.payload
    if payload.get("has_original_file") or payload.get("has_patch_hunks") or payload.get("has_old_string"):
        return True
    if payload.get("has_input") and payload.get("files"):
        return True
    if event.tool == "file_change":
        files = payload.get("files")
        if isinstance(files, Mapping):
            for entry in files.values():
                if isinstance(entry, Mapping) and (entry.get("has_unified_diff") or entry.get("has_content")):
                    return True
    return False


def is_doc_path(path: str, doc_suffixes: Sequence[str]) -> bool:
    lowered = (path or "").lower()
    return any(lowered.endswith(suffix.lower()) for suffix in doc_suffixes)


# --------------------------------------------------------------------------- #
# 候选与结果
# --------------------------------------------------------------------------- #


@dataclass
class Episode:
    """一个 bug-fix episode（字段即对外契约）。"""

    episode_id: str
    session_id: str
    source: str
    source_file: str
    start_seq: int
    end_seq: int
    start_ts: str
    end_ts: str
    user_report: str
    bug_term: str = events.UNKNOWN
    failure_evidence: List[Dict[str, Any]] = field(default_factory=list)
    edits: List[Dict[str, Any]] = field(default_factory=list)
    test_commands: List[Dict[str, Any]] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    score: int = 0
    score_breakdown: Dict[str, int] = field(default_factory=dict)
    status: str = "ended_without_pass"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "session_id": self.session_id,
            "source": self.source,
            "source_file": self.source_file,
            "start_seq": self.start_seq,
            "end_seq": self.end_seq,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "user_report": self.user_report,
            "bug_term": self.bug_term,
            "failure_evidence": list(self.failure_evidence),
            "edits": list(self.edits),
            "test_commands": list(self.test_commands),
            "files": list(self.files),
            "score": self.score,
            "score_breakdown": dict(self.score_breakdown),
            "status": self.status,
        }


@dataclass
class SplitResult:
    episodes: List[Episode] = field(default_factory=list)
    filtered: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"episodes": [item.to_dict() for item in self.episodes], "filtered": list(self.filtered)}


class _Candidate:
    """进行中的候选：只累积事实，判定在闭合时统一做。"""

    def __init__(self, anchor: events.Event, report: str, bug_term: str, source_file: str, weights: Mapping[str, int], doc_suffixes: Sequence[str]) -> None:
        self.session_id = anchor.session_id
        self.source = anchor.source
        self.source_file = source_file
        self.start_seq = anchor.seq
        self.start_ts = anchor.ts
        self.user_report = report
        self.bug_term = bug_term
        self.weights = dict(weights)
        self.doc_suffixes = list(doc_suffixes)
        self.failure_evidence: List[Dict[str, Any]] = []
        self.edits: List[Dict[str, Any]] = []
        self.edited_files: List[str] = []
        self.test_commands: List[Dict[str, Any]] = []
        self.failing_commands: List[str] = []
        self.call_index: Dict[str, str] = {}
        self.before_image = False
        self.end_seq = anchor.seq
        self.end_ts = anchor.ts
        self.status = "ended_without_pass"

    @property
    def phase(self) -> str:
        if self.edits:
            return "fixed"
        if self.failure_evidence:
            return "failing"
        return "armed"

    def breakdown(self) -> Dict[str, int]:
        parts: Dict[str, int] = {}
        if self.failure_evidence:
            parts["failure_signal"] = self.weights["failure_signal"]
        if self.before_image:
            parts["reconstruction"] = self.weights["reconstruction"]
        if self.test_commands:
            parts["test_command"] = self.weights["test_command"]
        if self.edited_files and any(not is_doc_path(path, self.doc_suffixes) for path in self.edited_files):
            parts["code_change"] = self.weights["code_change"]
        return parts

    def score(self) -> int:
        return sum(self.breakdown().values())

    def drop_reason(self, min_score: int) -> Optional[str]:
        """为什么丢（None = 保留）。判据顺序即优先级，理由要能一眼看懂。"""
        if not self.failure_evidence:
            return DROP_NO_FAILURE
        if not self.edits:
            return DROP_NO_FIX
        if self.edited_files and all(is_doc_path(path, self.doc_suffixes) for path in self.edited_files):
            return DROP_DOCS_ONLY
        if self.score() < min_score:
            return DROP_BELOW_THRESHOLD
        return None


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #


def split(
    events_in: Sequence[events.Event],
    cfg: Mapping[str, Any],
    session_files: Optional[Mapping[str, str]] = None,
) -> SplitResult:
    """把事件流切成 episode 集合（确定性：同一输入 → 同一集合与同一排序）。"""
    vocabulary = config_mod.vocabulary(cfg)
    bug_terms = vocabulary["bug_terms"]
    signals = vocabulary["failure_signals"]
    markers = vocabulary["test_command_markers"]
    pass_markers = vocabulary["test_pass_markers"]
    episode_params = config_mod.episode_params(cfg)
    weights = config_mod.weights(cfg)
    min_score = config_mod.min_score(cfg)
    doc_suffixes = list(episode_params["doc_suffixes"])
    id_format = str(episode_params["id_format"])
    files = dict(session_files or {})

    ordered = sorted(events_in, key=events.event_sort_key)
    result = SplitResult()
    current: Optional[_Candidate] = None
    last_seq: Dict[Tuple[str, str], int] = {}

    def close(candidate: _Candidate, end_seq: int, end_ts: str) -> None:
        candidate.end_seq = max(end_seq, candidate.start_seq)
        if end_ts != events.UNKNOWN:
            candidate.end_ts = end_ts
        reason = candidate.drop_reason(min_score)
        if reason is not None:
            result.filtered.append(
                {
                    "reason": reason,
                    "session_id": candidate.session_id,
                    "source": candidate.source,
                    "source_file": candidate.source_file,
                    "start_seq": candidate.start_seq,
                    "end_seq": candidate.end_seq,
                    "bug_term": candidate.bug_term,
                    "score": candidate.score(),
                }
            )
            return
        first_fix_seq = candidate.edits[0]["seq"]
        result.episodes.append(
            Episode(
                episode_id=id_format.format(session_id=candidate.session_id, first_fix_seq=first_fix_seq),
                session_id=candidate.session_id,
                source=candidate.source,
                source_file=candidate.source_file,
                start_seq=candidate.start_seq,
                end_seq=candidate.end_seq,
                start_ts=candidate.start_ts,
                end_ts=candidate.end_ts,
                user_report=candidate.user_report,
                bug_term=candidate.bug_term,
                failure_evidence=list(candidate.failure_evidence),
                edits=list(candidate.edits),
                test_commands=list(candidate.test_commands),
                files=list(candidate.edited_files),
                score=candidate.score(),
                score_breakdown=candidate.breakdown(),
                status=candidate.status,
            )
        )

    for event in ordered:
        last_seq[(event.source, event.session_id)] = event.seq

        if event.kind == "user_msg":
            text = event.payload.get("text") if isinstance(event.payload.get("text"), str) else ""
            term = matches_bug_term(text, bug_terms)
            if term:
                if current is not None:  # 上一条候选在此闭合（未验证 → ended_without_pass / 丢弃）
                    close(current, event.seq - 1, current.end_ts)
                current = _Candidate(event, text, term, files.get(event.session_id, events.UNKNOWN), weights, doc_suffixes)
            # 非 bug 的 user_msg（普通提问）不影响候选状态
            continue

        if current is None:
            continue

        if event.kind == "tool_call":
            command = test_command_of(event, markers)
            if command is not None:
                current.test_commands.append({"command": command, "seq": event.seq})
                call_id = event.payload.get("call_id")
                if not isinstance(call_id, str) or call_id == events.UNKNOWN:
                    call_id = event.payload.get("tool_use_id")
                if isinstance(call_id, str) and call_id != events.UNKNOWN:
                    current.call_index[call_id] = command
            if is_write_tool(event):
                _record_edit(current, event)
            continue

        if event.kind == "tool_result":
            reason = failure_evidence(event, signals)
            if reason is not None:
                current.failure_evidence.append({"reason": reason, "seq": event.seq})
                current.end_seq = event.seq
                current.end_ts = event.ts if event.ts != events.UNKNOWN else current.end_ts
                paired = _paired_command(event, current)
                if paired is not None and paired not in current.failing_commands:
                    current.failing_commands.append(paired)
                continue
            if is_write_tool(event):
                _record_edit(current, event)
                continue
            paired = _paired_command(event, current)
            if paired is not None and pass_evidence(event, signals, pass_markers):
                current.end_seq = event.seq
                current.end_ts = event.ts if event.ts != events.UNKNOWN else current.end_ts
                verified = (paired in current.failing_commands) if current.failing_commands else bool(current.edits)
                if current.edits and verified:
                    current.status = "verified_by_run"
                    close(current, event.seq, current.end_ts)
                    current = None
            continue

    if current is not None:
        end_seq = last_seq.get((current.source, current.session_id), current.start_seq)
        close(current, end_seq, current.end_ts)

    result.episodes.sort(key=lambda item: (item.source, item.session_id, item.start_seq))
    result.filtered.sort(key=lambda item: (item["source"], item["session_id"], item["start_seq"]))
    return result


def _record_edit(candidate: _Candidate, event: events.Event) -> None:
    candidate.edits.append({"tool": event.tool, "path": event.path, "seq": event.seq})
    if event.path != events.UNKNOWN and event.path not in candidate.edited_files:
        candidate.edited_files.append(event.path)
    if has_before_image(event):
        candidate.before_image = True
    candidate.end_seq = event.seq
    if event.ts != events.UNKNOWN:
        candidate.end_ts = event.ts


def _paired_command(event: events.Event, candidate: _Candidate) -> Optional[str]:
    """结果事件配对到哪条测试命令：靠 `call_id` / `tool_use_id` 配对（**不按时间序猜**）。"""
    payload = event.payload
    for key in ("call_id", "tool_use_id"):
        value = payload.get(key)
        if isinstance(value, str) and value != events.UNKNOWN and value in candidate.call_index:
            return candidate.call_index[value]
    return None


def session_file_map(scan_result: Any) -> Dict[str, str]:
    """会话 id → 源文件路径（M3 用 `read_source_line` 回溯原文时需要）。"""
    mapping: Dict[str, str] = {}
    for file_read in getattr(scan_result, "files", []):
        for event in file_read.events:
            mapping.setdefault(event.session_id, str(file_read.path))
    return mapping
