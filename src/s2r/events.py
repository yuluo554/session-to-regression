"""统一事件流 IR（M1 输出契约，下游一律只吃这个，不吃原始日志行）。

IR 的三条硬纪律：

1. **``seq`` = 源文件行号（1-based）**，不是紧凑数组下标——行号可由日志独立复算，下标会随
   解析过滤漂移；排序键 ``(source, session_id, seq)`` 因此可从输入复算；
2. **全程不读时钟**：``ts`` 只取自日志自身字段，取不到落 ``unknown``；
3. **不认识的字段落 ``unknown``**、不认识的格式显式报 ``unsupported_format`` 并列出缺失键，
   绝不静默产出空事件流。

``FORMAT_VERSION_FINGERPRINTS`` 目前是空的：按数据台账纪律（R1–R3 未闭环），本文件
不宣称任何版本指纹键名；M1 全量聚合实测后在此登记。空表期间的语义是**显式报错**，
不是「宽松解析」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from . import serialize
from .errors import UnsupportedFormat

#: 事件种类（对外契约枚举；出现集外值即报错）
KINDS: Tuple[str, ...] = ("user_msg", "assistant_msg", "tool_call", "tool_result", "meta")

#: 来源 harness（读日志的判别结果）
SOURCES: Tuple[str, ...] = ("claude", "codex")

#: 未知值统一哨兵：字段拿不到就落它，不猜测补全
UNKNOWN = "unknown"

#: 对外字段集（Event 序列化后的键集合契约；顺序不承诺，键集合由测试锁死）
EVENT_FIELDS: Tuple[str, ...] = (
    "source",
    "session_id",
    "seq",
    "source_offset",
    "ts",
    "kind",
    "tool",
    "path",
    "payload",
)

#: 首行形态判别（按**文件形态**而非目录名）：实测两 harness 的顶层容器键不同。
HARNESS_FINGERPRINTS: Dict[str, Tuple[str, ...]] = {
    "claude": ("message",),
    "codex": ("payload",),
}

#: 版本指纹表：``{source: {版本号: (必需顶层键, ...)}}``。M1 闭环 R1–R3 后登记；
#: 为空表示「本版本尚未宣称支持该 harness 的任何格式版本」→ 一律显式报错。
FORMAT_VERSION_FINGERPRINTS: Dict[str, Dict[str, FrozenSet[str]]] = {
    "claude": {},
    "codex": {},
}


@dataclass(frozen=True)
class Event:
    """归一事件流的一条记录。"""

    source: str
    session_id: str
    seq: int
    kind: str
    ts: str = UNKNOWN
    tool: str = UNKNOWN
    path: str = UNKNOWN
    source_offset: int = 0
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise UnsupportedFormat("未知来源 harness: {!r}".format(self.source), missing_keys=("source",))
        if self.kind not in KINDS:
            raise UnsupportedFormat("未知事件种类: {!r}".format(self.kind), missing_keys=("kind",))
        if not isinstance(self.seq, int) or isinstance(self.seq, bool) or self.seq < 1:
            raise UnsupportedFormat("seq 必须是源文件行号（≥1 的整数），实际 {!r}".format(self.seq), missing_keys=("seq",))
        if not isinstance(self.session_id, str) or not self.session_id:
            raise UnsupportedFormat("session_id 不得为空", missing_keys=("session_id",))
        if self.tool == "" or self.path == "":
            raise UnsupportedFormat("tool/path 取不到时落 {}，不得写空串".format(UNKNOWN))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "session_id": self.session_id,
            "seq": self.seq,
            "source_offset": self.source_offset,
            "ts": self.ts,
            "kind": self.kind,
            "tool": self.tool,
            "path": self.path,
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        return serialize.dumps_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Event":
        missing = tuple(name for name in EVENT_FIELDS if name not in data)
        if missing:
            raise UnsupportedFormat("事件缺字段", missing_keys=missing)
        return cls(
            source=data["source"],
            session_id=data["session_id"],
            seq=data["seq"],
            kind=data["kind"],
            ts=data["ts"],
            tool=data["tool"],
            path=data["path"],
            source_offset=data["source_offset"],
            payload=data["payload"],
        )

    @classmethod
    def from_json(cls, text: str) -> "Event":
        import json

        return cls.from_dict(json.loads(text))


def event_sort_key(event: Event) -> Tuple[str, str, int]:
    """规范序：``(source, session_id, seq)``——三个分量都能从输入独立复算。"""
    return (event.source, event.session_id, event.seq)


def detect_harness(first_record: Mapping[str, Any]) -> str:
    """按首行形态判别来源 harness；未知形态显式报错（不静默归到默认解析器）。"""
    if not isinstance(first_record, Mapping):
        raise UnsupportedFormat("首行不是 JSON 对象", missing_keys=("message", "payload"))
    matched: List[str] = []
    for source, required in sorted(HARNESS_FINGERPRINTS.items()):
        if all(key in first_record for key in required):
            matched.append(source)
    if len(matched) == 1:
        return matched[0]
    keys = tuple(sorted(str(key) for key in first_record.keys()))
    if not matched:
        raise UnsupportedFormat(
            "无法判别日志来源：首行不含任何已知 harness 的判别键",
            missing_keys=keys,
            hint="已知判别键见 events.HARNESS_FINGERPRINTS",
        )
    raise UnsupportedFormat("首行同时命中多个 harness: {}".format(", ".join(matched)), missing_keys=keys)


def detect_format_version(source: str, first_record: Mapping[str, Any]) -> str:
    """按版本指纹识别日志格式版本；登记表为空或键缺失即报 ``unsupported_format``。"""
    table = FORMAT_VERSION_FINGERPRINTS.get(source, {})
    if not table:
        raise UnsupportedFormat(
            "{} 的格式版本指纹尚未登记（M1 闭环 R1–R3 后登记）".format(source),
            missing_keys=tuple(sorted(str(key) for key in first_record.keys())),
        )
    for version, required in sorted(table.items()):
        if all(key in first_record for key in required):
            return version
    present = tuple(sorted(str(key) for key in first_record.keys()))
    raise UnsupportedFormat(
        "{} 的日志格式版本不认识".format(source),
        missing_keys=tuple(sorted(set().union(*table.values()) - set(present))),
    )
