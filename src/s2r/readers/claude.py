"""Claude Code jsonl → 事件流。

本机全量实测（`data/README.md` §六.1/§六.5/§六.8）：

* 顶层 `type` 有 15 种，**只有 `user` / `assistant` 承载事件**，其余按 `line_type:*` 计注记跳过；
* `message.content` 有两种载体：**字符串**（用户文本，实测 200 条）与**块列表**
  （`tool_use` / `tool_result` / `text` / `thinking` / `image`）——两种都要认；
* `toolUseResult` 是**行级**字段：决定 before 镜像是否可得，判据是"`structuredPatch` /
  `originalFile` **键存在**"，**不是** `type == "update"`（全量实测 update 仅 4 条、带
  `structuredPatch` 的却有 305 条——出题期判据勘误 E1）；
* `MultiEdit` 本机全量 **0 次**（R2）：登记为已知工具名占位，出现即计 `multiedit_observed` 注记，
  **不猜测 `edits[]` 字段形态**。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence, Tuple

from .. import events
from . import build_event, clip

#: 判定要用的小字段：内联进事件（按口径截断）
INLINE_INPUT_KEYS = ("command", "file_path", "pattern", "path", "url", "prompt", "description", "glob", "notebook_path")

#: 逐字节关键的字段：只记"存在性/规模"，原文由 M3 用 seq 回溯（避免截断毁掉 before 镜像）
REFERENCED_INPUT_KEYS = ("old_string", "new_string", "content")

WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")

TEXT_BLOCK_KINDS = {"text", "thinking"}

#: 承载事件的顶层行类型（只有这两种进事件流）
CONVERSATION_LINE_TYPES = ("user", "assistant")

#: 实测的非会话状态行类型（`data/README.md` §六.1 全量枚举）——
#: 一个文件里**全部**行都是这些类型且没有任何 `message` 时，它是 harness 状态文件而不是会话日志。
STATE_LINE_TYPES = frozenset(
    {
        "last-prompt",
        "ai-title",
        "mode",
        "permission-mode",
        "queue-operation",
        "file-history-snapshot",
        "file-history-delta",
        "atis-latch",
        "started",
        "result",
        "cost-state",
        "system",
        "attachment",
    }
)


def is_state_only(records: Sequence[Mapping[str, Any]]) -> bool:
    """探测窗口内是否"全是状态行"（没有任何会话事件行）→ 这不是会话日志。

    判据 = ① 窗口内没有任何记录带 `message`；② 出现过的顶层 `type` 全在实测状态类型集合内。
    两条都不满足时**不**下这个结论——那样会退化成"我不认识就说是状态文件"，
    真正的格式漂移必须继续走 `unsupported_format`（严格报错）。
    """
    if not records:
        return False
    observed = set()
    for record in records:
        if "message" in record:
            return False
        kind = record.get("type")
        if not isinstance(kind, str):
            return False
        observed.add(kind)
    return bool(observed) and observed <= STATE_LINE_TYPES


def emit(record: Mapping[str, Any], lineno: int, offset: int, path: Path, state: Any, max_result_chars: int) -> Iterator[events.Event]:
    line_type = record.get("type")
    if line_type not in ("user", "assistant"):
        state.note("line_type:{}".format(line_type))
        return

    message = record.get("message")
    if not isinstance(message, Mapping):
        state.note("message_missing")
        return

    session_id = _session_id(record, path, state)
    ts = record.get("timestamp")
    content = message.get("content")

    if isinstance(content, str):
        text, truncated = clip(content, max_result_chars)
        if not text.strip():
            state.note("empty_text_message")
            return
        yield build_event(
            "claude",
            session_id,
            lineno,
            offset,
            "user_msg" if line_type == "user" else "assistant_msg",
            ts=ts,
            payload={"text": text, "truncated": truncated, "role": message.get("role")},
        )
        return

    if not isinstance(content, list):
        state.note("content_type:{}".format(type(content).__name__))
        return

    result_meta = record.get("toolUseResult")
    result_meta = result_meta if isinstance(result_meta, Mapping) else {}

    for block in content:
        if not isinstance(block, Mapping):
            state.note("block_not_object")
            continue
        block_type = block.get("type")
        if block_type == "tool_use":
            event = _tool_call(block, lineno, offset, session_id, ts, state)
            if event is not None:
                yield event
        elif block_type == "tool_result":
            yield _tool_result(block, result_meta, lineno, offset, session_id, ts, state, max_result_chars)
        elif block_type == "text":
            text, truncated = clip(_join_text_blocks([block]), max_result_chars)
            if not text.strip():
                state.note("empty_text_block")
                continue
            yield build_event(
                "claude",
                session_id,
                lineno,
                offset,
                "user_msg" if line_type == "user" else "assistant_msg",
                ts=ts,
                payload={"text": text, "truncated": truncated, "role": message.get("role")},
            )
        else:
            state.note("block_type:{}".format(block_type))


def _session_id(record: Mapping[str, Any], path: Path, state: Any) -> str:
    session_id = record.get("sessionId") or record.get("session_id")
    if isinstance(session_id, str) and session_id:
        state.session_id = session_id
    return state.session_id if state.session_id != events.UNKNOWN else path.stem


def _tool_call(block: Mapping[str, Any], lineno: int, offset: int, session_id: str, ts: Any, state: Any) -> Optional[events.Event]:
    name = block.get("name")
    tool_input = block.get("input")
    tool_input = tool_input if isinstance(tool_input, Mapping) else {}
    payload: dict = {
        "tool_use_id": block.get("id") if isinstance(block.get("id"), str) else events.UNKNOWN,
        "input_keys": sorted(str(key) for key in tool_input),
    }
    for key in INLINE_INPUT_KEYS:
        if key in tool_input:
            value = tool_input[key]
            payload[key] = value if isinstance(value, (str, int, float, bool)) else events.UNKNOWN
    for key in REFERENCED_INPUT_KEYS:
        if key in tool_input:
            value = tool_input[key]
            payload["has_{}".format(key)] = True
            payload["{}_chars".format(key)] = len(value) if isinstance(value, str) else 0
    if "replace_all" in tool_input:
        payload["replace_all"] = bool(tool_input["replace_all"])

    if isinstance(name, str) and name in WRITE_TOOLS:
        state.note("write_tool:{}".format(name))
    if name == "MultiEdit":  # 本机 0 次（R2）：占位登记 + 显式注记，不猜 edits[] 形态
        state.note("multiedit_observed")
        payload["edits_count"] = len(tool_input.get("edits") or []) if isinstance(tool_input.get("edits"), list) else 0

    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    call_id = block.get("id")
    state.call_names[call_id] = name
    return build_event("claude", session_id, lineno, offset, "tool_call", ts=ts, tool=name, path=file_path, payload=payload)


def _tool_result(
    block: Mapping[str, Any],
    result_meta: Mapping[str, Any],
    lineno: int,
    offset: int,
    session_id: str,
    ts: Any,
    state: Any,
    max_result_chars: int,
) -> events.Event:
    text, truncated = clip(_flatten_result_content(block.get("content")), max_result_chars)
    has_patch = "structuredPatch" in result_meta
    hunks = result_meta.get("structuredPatch")
    file_path = result_meta.get("filePath")
    payload = {
        "tool_use_id": block.get("tool_use_id") if isinstance(block.get("tool_use_id"), str) else events.UNKNOWN,
        "is_error": bool(block.get("is_error")),
        "text": text,
        "truncated": truncated,
        "file_path": file_path if isinstance(file_path, str) and file_path else events.UNKNOWN,
        # 判据按"键存在"（E1/K14），不是 type 字段值；但"存在"还不够——
        # 实测：新建文件的结果也带 `structuredPatch: []`（空表，82 条），空表**不含 diff**，
        # 所以另给一个"有 hunk"的标记，下游用哪个必须自己说清楚。
        "has_structured_patch": has_patch,
        "has_patch_hunks": bool(hunks),
        "structured_patch_hunks": len(hunks) if isinstance(hunks, list) else 0,
        "has_original_file": "originalFile" in result_meta,
        "result_type": result_meta.get("type") if isinstance(result_meta.get("type"), str) else events.UNKNOWN,
    }
    return build_event("claude", session_id, lineno, offset, "tool_result", ts=ts, tool=state.call_names.get(block.get("tool_use_id")), path=file_path, payload=payload)


def _flatten_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _join_text_blocks(content)
    return ""


def _join_text_blocks(blocks: Sequence[Any]) -> str:
    parts = []
    for block in blocks:
        if isinstance(block, Mapping) and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


#: 供 M3 使用的 patch 文本回溯常量（本读取器只做引用，不解析 diff 语义）
PATCH_REFERENCE_FIELDS = ("originalFile", "structuredPatch", "old_string", "new_string", "content")
