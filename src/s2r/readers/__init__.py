"""M1 双 harness 读取器：jsonl → 统一事件流（IR 见 `s2r.events`）。

三条设计决定（都来自本机全量实测，结论见 `data/README.md` §六）：

1. **按记录形态分流，不按目录名**：Claude 看顶层 `type`，Codex 看 `payload.type`——
   真实用户的目录布局会漂，记录形态才是稳定事实；
2. **事件流不复制大文本**：`originalFile` / `structuredPatch` / `apply_patch` 全文 /
   `FileChange.unified_diff` 这些**逐字节关键**的块只在事件里留引用（`seq` = 源行号 +
   `source_offset`），由 M3 用 `read_source_line()` 取回原文——避免截断毁掉 before 镜像；
   判定要用的**小字段**（命令文本、file_path、tool_result 文本）内联并按口径截断（显式标 `truncated`）；
3. **不认识的记录不静默丢弃**：按 `code` 聚合成 ParseNote 计数（`line_type:*` / `payload_type:*` /
   `malformed_json` / `invalid_utf8` …），首行不是合法 JSON 或整文件零事件时**显式报错**。
"""

from __future__ import annotations

import json
from collections import Counter
from itertools import chain
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from .. import config as config_mod
from .. import events, serialize
from ..errors import InputUnavailable, UnsupportedFormat

JSONL_SUFFIX = ".jsonl"

#: 单文件读取结果
class FileRead:
    """一个 jsonl 文件的读取结果（事件 + 注记 + 识别到的 harness/版本）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.source = events.UNKNOWN
        self.format_version = events.UNKNOWN
        self.harness_version = events.UNKNOWN
        self.events: List[events.Event] = []
        self.notes: Counter = Counter()
        #: 会话内共享状态：会话 id（逐行/逐文件补齐）与 call_id → 工具名映射（结果侧配对用）
        self.session_id = events.UNKNOWN
        self.call_names: Dict[Any, Any] = {}

    def note(self, code: str) -> None:
        self.notes[code] += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "source": self.source,
            "format_version": self.format_version,
            "harness_version": self.harness_version,
            "event_count": len(self.events),
            "notes": [{"code": code, "count": count} for code, count in sorted(self.notes.items())],
        }


class ScanResult:
    """一次 scan 的全部产物（事件流 + 逐文件信息 + 全局注记）。"""

    def __init__(self) -> None:
        self.files: List[FileRead] = []
        self.notes: Counter = Counter()

    @property
    def events(self) -> List[events.Event]:
        merged: List[events.Event] = []
        for item in self.files:
            merged.extend(item.events)
        return merged

    def to_dict(self) -> Dict[str, Any]:
        merged = sorted(self.events, key=events.event_sort_key)
        return {
            "schema_version": events.EVENT_SCHEMA_VERSION,
            "files": [item.to_dict() for item in self.files],
            "sessions": _session_summaries(merged),
            "events": [event.to_dict() for event in merged],
            "notes": [{"code": code, "count": count} for code, count in sorted(self.notes.items())],
        }


def _session_summaries(merged: Sequence[events.Event]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[events.Event]] = {}
    for event in merged:
        grouped.setdefault((event.source, event.session_id), []).append(event)
    summaries = []
    for (source, session_id), items in sorted(grouped.items()):
        kinds: Counter = Counter(event.kind for event in items)
        summaries.append(
            {
                "source": source,
                "session_id": session_id,
                "event_count": len(items),
                "first_seq": min(event.seq for event in items),
                "last_seq": max(event.seq for event in items),
                "kinds": {kind: kinds[kind] for kind in sorted(kinds)},
            }
        )
    return summaries


# --------------------------------------------------------------------------- #
# 底层：逐行读取
# --------------------------------------------------------------------------- #


def iter_raw_lines(path: Path, max_line_bytes: int) -> Iterator[Tuple[int, int, bytes]]:
    """逐行产出 ``(行号, 行起始字节偏移, 原始行)``；超长行显式报错（不静默截断）。"""
    offset = 0
    with path.open("rb") as stream:
        for lineno, raw in enumerate(stream, start=1):
            size = len(raw)
            if size > max_line_bytes:
                raise InputUnavailable(
                    "行超长（{} 字节 > 上限 {}）: {}:{}".format(size, max_line_bytes, path, lineno),
                    hint="口径文件 episode.max_line_bytes 可调；调大前请确认内存可承受",
                )
            yield lineno, offset, raw
            offset += size


def read_records(path: Path, max_line_bytes: int, notes: Counter) -> Iterator[Tuple[int, int, Mapping[str, Any]]]:
    """产出合法 JSON 对象记录；坏行按 code 计注记并跳过（**首行坏即报错**）。"""
    seen_any = False
    for lineno, offset, raw in iter_raw_lines(path, max_line_bytes):
        if not raw.strip():
            notes["blank_line"] += 1
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            if not seen_any:
                raise UnsupportedFormat("首行不是合法 UTF-8: {}:{}".format(path, lineno))
            notes["invalid_utf8"] += 1
            continue
        try:
            record = json.loads(text)
        except ValueError:
            if not seen_any:
                raise UnsupportedFormat(
                    "首行不是合法 JSON（无法判别 harness）: {}:{}".format(path, lineno),
                    hint="本工具读的是 jsonl 会话日志；--claude-root/--codex-root 是否指到了对的目录？",
                )
            notes["malformed_json"] += 1
            continue
        if not isinstance(record, Mapping):
            notes["non_object_record"] += 1
            continue
        seen_any = True
        yield lineno, offset, record


def build_event(source: str, session_id: str, seq: int, offset: int, kind: str, ts: Any = None, tool: Any = None, path: Any = None, payload: Optional[Mapping[str, Any]] = None) -> events.Event:
    """统一的 Event 构造函数：取不到的值一律落 UNKNOWN 哨兵（不写空串、不猜）。"""
    return events.Event(
        source=source,
        session_id=session_id or events.UNKNOWN,
        seq=seq,
        source_offset=offset,
        ts=ts if isinstance(ts, str) and ts else events.UNKNOWN,
        kind=kind,
        tool=tool if isinstance(tool, str) and tool else events.UNKNOWN,
        path=path if isinstance(path, str) and path else events.UNKNOWN,
        payload=dict(payload or {}),
    )


def clip(text: Any, limit: int) -> Tuple[str, bool]:
    """按上限截断文本并**返回截断标记**（截断必须显式，见 K6）。"""
    if not isinstance(text, str):
        return "", False
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def read_source_line(path: Path, seq: int) -> Mapping[str, Any]:
    """按行号取回原始记录（M3 用它取 `originalFile` / patch 全文等逐字节关键的块）。"""
    if seq < 1:
        raise InputUnavailable("seq 必须是 ≥1 的源行号: {}".format(seq))
    for lineno, _offset, raw in iter_raw_lines(path, max_line_bytes=_NO_LINE_LIMIT):
        if lineno == seq:
            try:
                record = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise InputUnavailable("第 {} 行不是合法 JSON: {}: {}".format(seq, path, exc))
            if not isinstance(record, Mapping):
                raise InputUnavailable("第 {} 行不是 JSON 对象: {}".format(seq, path))
            return record
    raise InputUnavailable("源文件没有第 {} 行: {}".format(seq, path))


#: ``read_source_line`` 不做行长限制（原文可能很长，M3 需要完整字节）
_NO_LINE_LIMIT = float("inf")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def collect_jsonl(inputs: Sequence[str], roots: Sequence[Optional[str]] = ()) -> List[Path]:
    """把显式输入与（可选的）日志根展开成**稳定排序**的 jsonl 文件清单。

    * 显式输入可以是文件或目录（目录递归取 ``*.jsonl``）；
    * 显式输入不存在 → exit 2（不静默跳过）；
    * 清单去重并排序——同一份日志被喂两次不会产生两遍事件。
    """
    found: List[Path] = []
    for raw in list(inputs) + [item for item in roots if item]:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(item for item in path.rglob("*" + JSONL_SUFFIX) if item.is_file()))
        elif path.is_file():
            found.append(path)
        else:
            raise InputUnavailable("输入不存在: {}".format(path))
    unique: Dict[str, Path] = {}
    for path in found:
        unique[str(path.resolve())] = path
    result = [unique[key] for key in sorted(unique)]
    if not result:
        raise InputUnavailable("没有找到任何 jsonl 会话文件（输入为空目录？）")
    return result


def read_file(path: Path, limits: Mapping[str, Any]) -> FileRead:
    """读一个 jsonl 文件：先攒探测窗口判 harness 与版本指纹，再逐条归一事件流。"""
    from . import claude as claude_reader
    from . import codex as codex_reader

    result = FileRead(path)
    max_line_bytes = int(limits["max_line_bytes"])
    probe_lines = int(limits["version_probe_lines"])
    max_result_chars = int(limits["max_result_chars"])
    max_events = int(limits["max_events_per_session"])

    records = read_records(path, max_line_bytes, result.notes)
    probe = []
    for item in records:
        probe.append(item)
        if len(probe) >= probe_lines:
            break
    if not probe:
        raise UnsupportedFormat("文件没有任何合法 JSON 行: {}".format(path))

    probe_records = [item[2] for item in probe]
    result.source = events.detect_harness(probe_records)
    result.format_version = events.detect_format_version(result.source, _version_views(result.source, probe_records))
    result.harness_version = _harness_version(result.source, probe_records)

    emit = claude_reader.emit if result.source == "claude" else codex_reader.emit
    for lineno, offset, record in chain(probe, records):
        for event in emit(record, lineno, offset, path, result, max_result_chars):
            result.events.append(event)
            if len(result.events) > max_events:
                raise InputUnavailable(
                    "会话事件数超过上限 {}（{}）".format(max_events, path),
                    hint="口径文件 episode.max_events_per_session；超限不静默截断",
                )
    return result


def _version_views(source: str, records: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """版本指纹看的是哪个记录形态：Claude = 顶层行；Codex = session_meta 的 payload。"""
    if source == "claude":
        return list(records)
    views = []
    for record in records:
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), Mapping):
            views.append(record["payload"])
    return views


def _harness_version(source: str, records: Sequence[Mapping[str, Any]]) -> str:
    """记录实测到的 harness 版本字符串（信息性，不参与判定；取不到落 unknown）。"""
    if source == "claude":
        for record in records:
            version = record.get("version")
            if isinstance(version, str) and version:
                return version
        return events.UNKNOWN
    for record in records:
        payload = record.get("payload")
        if record.get("type") == "session_meta" and isinstance(payload, Mapping):
            version = payload.get("cli_version")
            if isinstance(version, str) and version:
                return version
    return events.UNKNOWN


def scan(inputs: Sequence[str], limits: Mapping[str, Any], roots: Sequence[Optional[str]] = ()) -> ScanResult:
    """扫描一批输入，返回合并后的 ScanResult（跨文件注记也已聚合）。"""
    result = ScanResult()
    for path in collect_jsonl(inputs, roots):
        file_read = read_file(path, limits)
        result.files.append(file_read)
        for code, count in file_read.notes.items():
            result.notes[code] += count
    return result


def limits_from_config(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    return config_mod.episode_params(cfg)


def dump(result: ScanResult) -> str:
    """确定性字节（K6）：同一输入 → 同一输出。"""
    return serialize.dumps_json(result.to_dict())
