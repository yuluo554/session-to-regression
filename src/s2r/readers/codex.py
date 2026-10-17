"""Codex rollout jsonl → 事件流。

本机全量实测（`data/README.md` §六.1–§六.7）：

* 行级键恒为 `ordinal, payload, timestamp, type`；**payload.type 才是事实来源**，按它分流；
* `session_meta.payload` 带 `session_id` / `cli_version`（版本指纹与 harness 版本的实测落点）；
* `apply_patch` 的路径**只在 `input` 的 patch 指令行里**（JSON 侧无独立路径键，R1）；
  指令集实测含 `*** Move to:`（出题期未提，勘误 E3），且路径可为绝对/相对/含中文；
* **命令工具名实测是 `shell_command` / `exec_command`**（题面写的 `exec` 在本机不存在，勘误 E2），
  命令文本键**不统一**：`command`（shell_command）与 `cmd`（exec_command）都要认；
* 失败判定**只在数值上做**：`Exit code: N` 与 `Process exited with code N`，N≠0 才是失败
  （标记字符串在成功时也出现，按字符串判会误报，R3/K16）；
* `item_completed` 的 `FileChange` 是**结构化变更通道**（路径 → `content` 或 `unified_diff`），
  M3 的首选来源；`AgentMessage`/`UserMessage` 与 `message` 是重复通道，只取后者（避免同一句话两次）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from .. import events
from . import build_event, clip

#: 命令类工具（实测集合；`exec` 不是本机工具名）
COMMAND_TOOLS = ("shell_command", "exec_command", "js", "shell")

#: 命令文本在这两个键上（R3：`command` 与 `cmd` 并存）
COMMAND_KEYS = ("command", "cmd")

#: M1 只分流这些 payload.type；其余按 payload_type:* 计注记跳过
HANDLED_PAYLOAD_TYPES = (
    "message",
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "item_completed",
)

_PATCH_DIRECTIVE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to):\s*(.+?)\s*$", re.MULTILINE)
_EXIT_CODE_LINE = re.compile(r"^Exit code:\s*(-?\d+)\s*$", re.MULTILINE)
_PROCESS_EXITED = re.compile(r"Process exited with code\s*(-?\d+)")
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def emit(record: Mapping[str, Any], lineno: int, offset: int, path: Path, state: Any, max_result_chars: int) -> Iterator[events.Event]:
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        state.note("payload_missing")
        return
    ts = record.get("timestamp")

    if record.get("type") == "session_meta":
        session_id = payload.get("session_id") or payload.get("id")
        if isinstance(session_id, str) and session_id:
            state.session_id = session_id
        yield build_event(
            "codex",
            _session_id(state, path),
            lineno,
            offset,
            "meta",
            ts=ts,
            payload={
                "kind": "session_meta",
                # 不复制 cwd 等本机路径信息进事件流（隐私最小化）
                "cli_version": payload.get("cli_version") if isinstance(payload.get("cli_version"), str) else events.UNKNOWN,
                "model_provider": payload.get("model_provider") if isinstance(payload.get("model_provider"), str) else events.UNKNOWN,
                "originator": payload.get("originator") if isinstance(payload.get("originator"), str) else events.UNKNOWN,
            },
        )
        return

    payload_type = payload.get("type")

    if payload_type == "message":
        yield _message(payload, lineno, offset, state, path, ts, max_result_chars)
        return

    if payload_type == "function_call":
        yield _function_call(payload, lineno, offset, state, path, ts, max_result_chars)
        return

    if payload_type == "custom_tool_call":
        yield _custom_tool_call(payload, lineno, offset, state, path, ts)
        return

    if payload_type in ("function_call_output", "custom_tool_call_output"):
        yield _tool_output(payload, lineno, offset, state, path, ts, max_result_chars)
        return

    if payload_type == "item_completed":
        item = payload.get("item")
        if isinstance(item, Mapping) and item.get("type") == "FileChange":
            yield _file_change(item, lineno, offset, state, path, ts)
        elif isinstance(item, Mapping):
            state.note("item_type:{}".format(item.get("type")))
        else:
            state.note("item_missing")
        return

    state.note("payload_type:{}".format(payload_type))


def _session_id(state: Any, path: Path) -> str:
    return state.session_id if state.session_id != events.UNKNOWN else path.stem


def _message(payload: Mapping[str, Any], lineno: int, offset: int, state: Any, path: Path, ts: Any, max_result_chars: int) -> events.Event:
    role = payload.get("role")
    text, truncated = clip(_join_text(payload.get("content")), max_result_chars)
    if role == "developer":
        # 实测 925 条：系统/开发者指令，**不是用户报告**；单列 meta，避免被 M2 当成 bug 报告
        state.note("role:developer")
        kind = "meta"
    elif role == "user":
        kind = "user_msg"
    elif role == "assistant":
        kind = "assistant_msg"
    else:
        state.note("role:{}".format(role))
        kind = "meta"
    return build_event(
        "codex",
        _session_id(state, path),
        lineno,
        offset,
        kind,
        ts=ts,
        payload={"role": role if isinstance(role, str) else events.UNKNOWN, "text": text, "truncated": truncated},
    )


def _function_call(payload: Mapping[str, Any], lineno: int, offset: int, state: Any, path: Path, ts: Any, max_result_chars: int) -> events.Event:
    name = payload.get("name")
    args = _parse_arguments(payload.get("arguments"), state)
    command = None
    for key in COMMAND_KEYS:
        if isinstance(args.get(key), str):
            command = args[key]
            break
    if command is None and isinstance(name, str) and name in COMMAND_TOOLS:
        state.note("command_text_missing:{}".format(name))
    command_text, truncated = clip(command or "", max_result_chars)
    call_id = payload.get("call_id")
    state.call_names[call_id] = name
    payload_out: Dict[str, Any] = {
        "call_id": call_id if isinstance(call_id, str) else events.UNKNOWN,
        "input_keys": sorted(str(key) for key in args),
        "command": command_text,
        "truncated": truncated,
        "has_command": bool(command_text),
    }
    if isinstance(args.get("workdir"), str):
        payload_out["workdir"] = args["workdir"]
    return build_event("codex", _session_id(state, path), lineno, offset, "tool_call", ts=ts, tool=name, payload=payload_out)


def _custom_tool_call(payload: Mapping[str, Any], lineno: int, offset: int, state: Any, path: Path, ts: Any) -> events.Event:
    name = payload.get("name")
    raw = payload.get("input")
    call_id = payload.get("call_id")
    state.call_names[call_id] = name
    files: List[str] = []
    actions: Dict[str, str] = {}
    if isinstance(raw, str) and "*** Begin Patch" in raw:
        files, actions = parse_patch_targets(raw)
        if len(files) > 1:
            state.note("multi_file_patch")
        untracked = len(_PATCH_DIRECTIVE.findall(raw)) - len(files)
        if untracked > 0:
            state.note("patch_directive_path_duplicate")
    payload_out = {
        "call_id": call_id if isinstance(call_id, str) else events.UNKNOWN,
        "input_chars": len(raw) if isinstance(raw, str) else 0,
        "has_input": isinstance(raw, str) and bool(raw),
        "files": files,
        "file_actions": actions,
    }
    target = files[0] if files else None
    return build_event("codex", _session_id(state, path), lineno, offset, "tool_call", ts=ts, tool=name, path=target, payload=payload_out)


def _tool_output(payload: Mapping[str, Any], lineno: int, offset: int, state: Any, path: Path, ts: Any, max_result_chars: int) -> events.Event:
    raw = payload.get("output")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, sort_keys=True) if raw is not None else ""
    clipped, truncated = clip(text, max_result_chars)
    exit_code, exit_source = parse_exit_code(text)
    call_id = payload.get("call_id")
    payload_out = {
        "call_id": call_id if isinstance(call_id, str) else events.UNKNOWN,
        "text": clipped,
        "truncated": truncated,
        "exit_code": exit_code,
        "exit_code_source": exit_source if exit_source else events.UNKNOWN,
    }
    return build_event("codex", _session_id(state, path), lineno, offset, "tool_result", ts=ts, tool=state.call_names.get(call_id), payload=payload_out)


def _file_change(item: Mapping[str, Any], lineno: int, offset: int, state: Any, path: Path, ts: Any) -> events.Event:
    changes = item.get("changes")
    files: Dict[str, Dict[str, Any]] = {}
    if isinstance(changes, Mapping):
        for raw_path, change in changes.items():
            if not isinstance(change, Mapping):
                state.note("file_change_value_not_object")
                continue
            entry: Dict[str, Any] = {"type": change.get("type") if isinstance(change.get("type"), str) else events.UNKNOWN}
            if isinstance(change.get("unified_diff"), str):
                entry["has_unified_diff"] = True
                entry["unified_diff_chars"] = len(change["unified_diff"])
            if isinstance(change.get("content"), str):
                entry["has_content"] = True
                entry["content_chars"] = len(change["content"])
            if isinstance(change.get("move_path"), str):
                entry["move_path"] = change["move_path"]
            files[str(raw_path)] = entry
    else:
        state.note("file_change_changes_not_object")
    state.note("file_change_events")
    first = sorted(files)[0] if files else None
    return build_event(
        "codex",
        _session_id(state, path),
        lineno,
        offset,
        "tool_result",
        ts=ts,
        tool="file_change",
        path=first,
        payload={"files_count": len(files), "files": files, "item_status": item.get("status") if isinstance(item.get("status"), str) else events.UNKNOWN},
    )


def _parse_arguments(raw: Any, state: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            state.note("arguments_unparsed")
            return {}
        if isinstance(parsed, Mapping):
            return parsed
        state.note("arguments_not_object")
        return {}
    state.note("arguments_missing")
    return {}


def _join_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def parse_patch_targets(patch_text: str) -> Tuple[List[str], Dict[str, str]]:
    """解析 patch 指令行里的目标路径（R1 结论：路径只在这里）。

    返回 ``(按出现顺序去重的路径列表, {路径: 动作})``；动作 ∈
    ``add`` / ``update`` / ``delete`` / ``move_to``。**不做路径归一化**（绝对/相对保持原样，
    由 M3 结合仓库根与磁盘状态解析），只记录形态由调用方判断。
    """
    files: List[str] = []
    actions: Dict[str, str] = {}
    action_map = {"Add File": "add", "Update File": "update", "Delete File": "delete", "Move to": "move_to"}
    for directive, raw_path in _PATCH_DIRECTIVE.findall(patch_text):
        target = _strip_quotes(raw_path.strip())
        if not target:
            continue
        if target not in actions:
            files.append(target)
        actions[target] = action_map.get(directive, "unknown")
    return files, actions


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def path_style(target: str) -> str:
    """路径形态：``windows_absolute`` / ``posix_absolute`` / ``relative``。

    为什么要显式判形态：Windows 上 ``Path("/demo/x").is_absolute()`` 为 **False**（无盘符），
    直接用它分派"绝对 vs 相对"会走反分支。
    """
    if _WINDOWS_ABSOLUTE.match(target):
        return "windows_absolute"
    if target.startswith("/"):
        return "posix_absolute"
    return "relative"


def parse_exit_code(text: str) -> Tuple[Optional[int], Optional[str]]:
    """从输出文本里取退出码（R3/K16）：只有数值算数，标记命中不算。

    两种实测形态：``Process exited with code N``（命令类）与行首 ``Exit code: N``（patch 类）。
    """
    match = _PROCESS_EXITED.search(text or "")
    if match:
        return int(match.group(1)), "process_exited"
    match = _EXIT_CODE_LINE.search(text or "")
    if match:
        return int(match.group(1)), "exit_code_line"
    return None, None
