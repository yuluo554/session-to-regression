"""M3 差分还原器：从**日志证据**重建文件的 before/after（logs-only，不依赖 git）。

为什么必须先做这一步：真实的改文件会话里只有 7/37 在 git 仓库内（`data/README.md` §2.3），
所以"修改前的内容"必须从日志里恢复，不能指望 `git show`。

三级降级（逐级降级，**每级标 `source` 与 `confidence`**，多文件 → 列表、逐文件独立置信度）：

1. **Claude `originalFile` + 非空 `structuredPatch`**（修改前全文 + 带 hunk 的补丁）——
   判据是"**键存在且非空**"（K14：实测新建文件的结果也带 `structuredPatch: []`，空表不含 diff）；
2. **Claude `old_string`/`new_string` 在 before 基线上定位替换**——基线按
   `originalFile` → 磁盘（显式 `--repo`）→ 同会话更早的 `Write.content` 的优先级取第一个可用的；
3. **Codex**：首选 `FileChange.changes[*].unified_diff`（结构化、带路径、带行号），
   `*** Begin Patch` 文本解析**降为兜底**（同一文件两条通道都在时，结构化通道胜出）；
4. 都不可得 → 该文件**不进生成路径**（`no_before_image`，绝不伪造 before）。

四条判定纪律（都会被打挂测试）：

* **多处命中且无法唯一定位 → `unresolved`，不任选一处**（`replace_all: true` 时全部替换）；
* **绝不伪造上下文行**：只报日志里真实出现的行；拿不到全文基线时落 `partial`（片段级证据 +
  `before`/`after` 为 null），而不是把片段拼成一份"看起来像全文"的东西；
* **磁盘对齐是显式开关**（`--repo`）：不给就不碰用户工作区；给了也只**读**，且对不上就如实降级
  （`--repo` 的语义是"该编辑发生前的工作区状态"，工具不做时序推断）；
* **同一日志 + 同一磁盘状态 → 同一 diff**（无时钟、无随机、无网络；输出走 `serialize` 单点）。

`confidence` 由**before 镜像的来源**决定（不由结果好坏决定）：日志内全文 = `high`、
磁盘工作区 = `medium`、只有片段 = `low`、取不到 = `none`。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import config as config_mod
from . import episode as episode_mod
from . import events, readers, serialize
from .errors import InputUnavailable

#: 还原产物 schema 版本（对外契约；字段集变化即升版）
SCHEMA_VERSION = 1

# ---- 状态（第三态显式可见，不得折叠成"通过"）------------------------------ #
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_UNRESOLVED = "unresolved"
STATUS_NO_BEFORE = "no_before_image"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_UNRESOLVED, STATUS_NO_BEFORE)

#: 可用产物状态：只有这两种能进生成路径（`partial` 只给片段，`M4` 自行决定是否用）
USABLE_STATUSES = (STATUS_OK, STATUS_PARTIAL)

# ---- 证据通道（source）----------------------------------------------------- #
SOURCE_CLAUDE_PATCH = "claude_structured_patch"
SOURCE_CLAUDE_OLD_STRING = "claude_old_string"
SOURCE_CLAUDE_WRITE = "claude_write_content"
SOURCE_CODEX_DIFF = "codex_unified_diff"
SOURCE_CODEX_CONTENT = "codex_file_change_content"
SOURCE_CODEX_PATCH = "codex_patch_text"
SOURCE_NONE = "none"
SOURCES = (
    SOURCE_CLAUDE_PATCH,
    SOURCE_CLAUDE_OLD_STRING,
    SOURCE_CLAUDE_WRITE,
    SOURCE_CODEX_DIFF,
    SOURCE_CODEX_CONTENT,
    SOURCE_CODEX_PATCH,
    SOURCE_NONE,
)

# ---- 置信度 ---------------------------------------------------------------- #
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"
CONFIDENCES = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW, CONFIDENCE_NONE)

#: before 镜像的来源（决定 confidence，不是"结果好不好"）
BASE_LOG_TEXT = "log_text"      # 日志内全文（Claude originalFile / 同会话 Write.content）
BASE_WORKTREE = "worktree"      # 磁盘工作区（可能被后续改动）
BASE_FRAGMENT = "fragment"      # 只有片段（hunk 行），没有全文基线
BASE_NONE = "none"

CONFIDENCE_BY_BASE = {
    BASE_LOG_TEXT: CONFIDENCE_HIGH,
    BASE_WORKTREE: CONFIDENCE_MEDIUM,
    BASE_FRAGMENT: CONFIDENCE_LOW,
    BASE_NONE: CONFIDENCE_NONE,
}

#: 定位方式（写进 evidence，便于回溯"凭什么认为它在这个位置"）
ALIGN_LINE_NUMBER = "line_number"
ALIGN_CONTENT_SEARCH = "content_search"

# ---- 原因码（unresolved / partial / no_before_image 都要说清为什么）--------- #
REASON_NONE = ""
REASON_NO_BEFORE = "no_before_image"
REASON_ADD_NO_BEFORE = "new_file_in_log"
REASON_AMBIGUOUS = "ambiguous_match"
REASON_HUNK_MISMATCH = "hunk_mismatch"
REASON_NOT_FOUND = "old_string_not_found"
REASON_BASE_MISSING = "base_missing"
REASON_BASE_MISMATCH = "base_mismatch"

#: Codex 结果侧结构化通道里的变更类型（实测：`{type, content}` / `{type, unified_diff, move_path}`）
CHANGE_ADD = "add"


# --------------------------------------------------------------------------- #
# 对外产物
# --------------------------------------------------------------------------- #


@dataclass
class Reconstruction:
    """一个文件的一次还原结果（多文件 → 列表，逐文件独立置信度）。"""

    file: str
    before: Optional[str]
    after: Optional[str]
    hunks: List[Dict[str, Any]]
    source: str
    confidence: str
    status: str = STATUS_OK
    reason: str = REASON_NONE
    tool: str = events.UNKNOWN
    seq: int = 0
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise InputUnavailable("未知还原状态: {!r}".format(self.status))
        if self.source not in SOURCES:
            raise InputUnavailable("未知还原来源: {!r}".format(self.source))
        if self.confidence not in CONFIDENCES:
            raise InputUnavailable("未知置信度: {!r}".format(self.confidence))
        if self.status == STATUS_OK and (self.before is None or self.after is None):
            raise InputUnavailable("status=ok 必须同时给出 before 与 after")

    @property
    def usable(self) -> bool:
        return self.status in USABLE_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "before": self.before,
            "after": self.after,
            "hunks": [dict(item) for item in self.hunks],
            "hunk_count": len(self.hunks),
            "source": self.source,
            "confidence": self.confidence,
            "status": self.status,
            "reason": self.reason,
            "tool": self.tool,
            "seq": self.seq,
            "evidence": dict(self.evidence),
        }

    def fact(self) -> Dict[str, Any]:
        """对账用的事实子集（真值只声明这几项；顺序不承诺，键集合由测试锁死）。"""
        return {
            "file": self.file,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "before": self.before,
            "after": self.after,
            "hunk_count": len(self.hunks),
        }


@dataclass
class SessionReconstruction:
    """一个会话的还原产物：逐文件结果 + 逐 episode 的可用性结论。"""

    source: str
    session_id: str
    source_file: str
    files: List[Reconstruction] = field(default_factory=list)
    episodes: List[Dict[str, Any]] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        counter = Counter(item.status for item in self.files)
        return {status: counter.get(status, 0) for status in STATUSES}

    @property
    def ok_files(self) -> List[Reconstruction]:
        return [item for item in self.files if item.status == STATUS_OK]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "session_id": self.session_id,
            "source_file": self.source_file,
            "files": [item.to_dict() for item in self.files],
            "episodes": [dict(item) for item in self.episodes],
            "counts": self.counts(),
        }

    def to_json(self) -> str:
        return serialize.dumps_json(self.to_dict())


# --------------------------------------------------------------------------- #
# 行级工具（保留"结尾换行有没有"这件事，别在归一化里丢掉事实）
# --------------------------------------------------------------------------- #


def split_lines(text: str) -> Tuple[List[str], bool]:
    """文本 → (行列表, 结尾是否有换行)。CRLF 归一成 LF——两种换行不承诺逐字节相等。"""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    trailing = False
    if lines and lines[-1] == "":
        lines.pop()
        trailing = True
    return lines, trailing


def join_lines(lines: Sequence[str], trailing: bool) -> str:
    return "\n".join(lines) + ("\n" if trailing else "")


def count_occurrences(text: str, needle: str) -> int:
    if not needle:
        return 0
    return text.count(needle)


def hunk_of(old_lines: Sequence[str], new_lines: Sequence[str], old_start: Optional[int] = None) -> Dict[str, Any]:
    """归一化的 hunk（两 harness 共用同一形状，便于 M4 消费与跨源比对）。"""
    return {
        "old_start": old_start,
        "old_lines": list(old_lines),
        "new_lines": list(new_lines),
        "old_count": len(old_lines),
        "new_count": len(new_lines),
    }


def _strip_prefix(line: str) -> Tuple[str, str]:
    """diff 行 → (前缀, 内容)；前缀 ∈ {' ', '-', '+'}，认不出的一律当上下文（不猜）。"""
    if line[:1] in (" ", "-", "+"):
        return line[0], line[1:]
    return " ", line


def normalize_diff_lines(lines: Sequence[str]) -> Tuple[List[str], List[str]]:
    """带前缀的 diff 行 → (旧侧行, 新侧行)。`\\ No newline at end of file` 标记跳过。"""
    old_side: List[str] = []
    new_side: List[str] = []
    for raw in lines:
        if not isinstance(raw, str):
            continue
        if raw.startswith("\\"):
            continue
        prefix, content = _strip_prefix(raw)
        if prefix in (" ", "-"):
            old_side.append(content)
        if prefix in (" ", "+"):
            new_side.append(content)
    return old_side, new_side


def text_lines(text: str) -> List[str]:
    """diff / patch 文本 → 行列表：去掉**结尾那一个**换行，别把它变成一个假的空行。

    实测教训：`text.split("\\n")` 会把结尾换行变成末尾的 `""`，而 `""` 在 diff 里是
    "空上下文行"，于是每个 hunk 的旧侧都多出一条不存在的空行 → 定位必然失败（首版实测）。
    """
    body = text.replace("\r\n", "\n")
    if body.endswith("\n"):
        body = body[:-1]
    return body.split("\n") if body else []


def parse_unified_diff(text: str) -> List[Dict[str, Any]]:
    """标准 unified diff → hunks（`@@ -a,b +c,d @@` 的行号 a 进 `old_start`）。"""
    hunks: List[Dict[str, Any]] = []
    current: Optional[List[str]] = None
    old_start: Optional[int] = None
    for line in text_lines(text):
        if line.startswith("@@"):
            if current is not None:
                hunks.append(_finish_hunk(current, old_start))
            current = []
            old_start = _parse_hunk_start(line)
            continue
        if current is None:
            continue
        current.append(line)
    if current is not None:
        hunks.append(_finish_hunk(current, old_start))
    return hunks


def _finish_hunk(lines: Sequence[str], old_start: Optional[int]) -> Dict[str, Any]:
    old_side, new_side = normalize_diff_lines(lines)
    return hunk_of(old_side, new_side, old_start)


def _parse_hunk_start(header: str) -> Optional[int]:
    """从 `@@ -a,b +c,d @@` 取 a（缺省 b 视为 1）。取不到落 None——不猜行号。"""
    body = header[2:].strip()
    token = body.split(" ")[0] if body else ""
    if not token.startswith("-"):
        return None
    span = token[1:].split(",")[0]
    try:
        return int(span)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# `apply_patch` 文本解析（M1 的指令表复用，避免两处各写一份指令名）
# --------------------------------------------------------------------------- #


def parse_patch_blocks(text: str) -> List[Dict[str, Any]]:
    """`*** Begin Patch` 文本 → 每文件一块 ``{file, action, moved_to, hunks}``（按出现顺序）。

    只认实测指令集（含 `*** Move to:`，K17）；一个块里的 Move to 记进 `moved_to`，
    **不新建一个"文件"**（移动的源文件才有内容行）。
    """
    from .readers import codex as codex_reader

    blocks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    lines: List[str] = []
    old_start: Optional[int] = None
    in_hunk = False

    def flush_hunk() -> None:
        nonlocal lines, old_start, in_hunk
        if current is not None and lines:
            current["hunks"].append(_finish_hunk(lines, old_start))
        lines, old_start, in_hunk = [], None, False

    def flush_block() -> None:
        nonlocal current
        flush_hunk()
        if current is not None and current["file"]:
            blocks.append(current)
        current = None

    for raw in text_lines(text):
        probe = raw.rstrip()
        if probe.startswith("*** End Patch") or probe.startswith("*** Begin Patch"):
            flush_block()
            continue
        if probe.startswith("*** "):
            files, actions = codex_reader.parse_patch_targets(probe + "\n")
            if files:
                target = files[0]
                action = actions.get(target, "unknown")
                if action == "move_to" and current is not None:
                    current["moved_to"] = target
                    continue
                flush_block()
                current = {"file": target, "action": action, "moved_to": None, "hunks": []}
                in_hunk = action == CHANGE_ADD  # Add File 的正文块不带 `@@` 头
                continue
            continue
        if current is None:
            continue
        if probe.startswith("@@"):
            flush_hunk()
            old_start = _parse_hunk_start(probe)
            in_hunk = True
            continue
        if not in_hunk:
            # 指令行与正文之间的行（实测不存在）：不猜它是不是上下文
            continue
        if raw == "":
            lines.append(" ")  # 空上下文行：前缀 ' ' + 空内容
            continue
        if raw[:1] in (" ", "-", "+", "\\"):
            lines.append(raw)
    flush_block()
    return blocks


# --------------------------------------------------------------------------- #
# hunk 定位与套用
# --------------------------------------------------------------------------- #


def apply_hunks(base_lines: Sequence[str], hunks: Sequence[Mapping[str, Any]]) -> Tuple[Optional[List[str]], str, List[Dict[str, Any]]]:
    """把 hunks 套到基线行上：**先定位再套用**，定位不唯一就拒绝（不任选一处）。

    返回 ``(新行列表 or None, aligned_by, 逐 hunk 证据)``；失败时第一项为 None，
    第二项是原因码（`hunk_mismatch` / `ambiguous_match`）。
    """
    placements: List[Tuple[int, int, List[str], str]] = []
    evidence: List[Dict[str, Any]] = []
    for index, hunk in enumerate(hunks):
        old_side = list(hunk.get("old_lines") or [])
        new_side = list(hunk.get("new_lines") or [])
        old_start = hunk.get("old_start")
        taken = [(item[0], item[1]) for item in placements]
        found: Optional[Tuple[int, str]] = None
        if isinstance(old_start, int) and old_start >= 1:
            offset = old_start - 1
            if _fits(base_lines, offset, old_side) and not _overlaps(taken, offset, len(old_side)):
                found = (offset, ALIGN_LINE_NUMBER)
        if found is None:
            candidates = [
                offset
                for offset in range(0, len(base_lines) - len(old_side) + 1)
                if _fits(base_lines, offset, old_side) and not _overlaps(taken, offset, len(old_side))
            ]
            if len(candidates) == 1:
                found = (candidates[0], ALIGN_CONTENT_SEARCH)
            elif not candidates:
                evidence.append({"hunk": index, "placed": False, "reason": REASON_HUNK_MISMATCH})
                return None, REASON_HUNK_MISMATCH, evidence
            else:
                evidence.append({"hunk": index, "placed": False, "reason": REASON_AMBIGUOUS, "candidates": len(candidates)})
                return None, REASON_AMBIGUOUS, evidence
        placements.append((found[0], len(old_side), new_side, found[1]))
        evidence.append({"hunk": index, "placed": True, "old_start": found[0] + 1, "aligned_by": found[1]})

    result = list(base_lines)
    for offset, length, new_side, _aligned in sorted(placements, key=lambda item: item[0], reverse=True):
        result[offset : offset + length] = new_side
    aligned_by = ALIGN_LINE_NUMBER if all(item[3] == ALIGN_LINE_NUMBER for item in placements) else ALIGN_CONTENT_SEARCH
    return result, aligned_by, evidence


def _fits(base_lines: Sequence[str], offset: int, old_side: Sequence[str]) -> bool:
    if offset < 0 or offset + len(old_side) > len(base_lines):
        return False
    return list(base_lines[offset : offset + len(old_side)]) == list(old_side)


def _overlaps(taken: Sequence[Tuple[int, int]], offset: int, length: int) -> bool:
    end = offset + max(length, 1)
    for start, other_length in taken:
        other_end = start + max(other_length, 1)
        if start < end and offset < other_end:
            return True
    return False


# --------------------------------------------------------------------------- #
# 磁盘基线（显式开关；只读）
# --------------------------------------------------------------------------- #


def _is_file(path: Path) -> bool:
    """`Path.is_file()` 在 3.8 上遇到非法文件名（Windows 的 `claude:` 这种）会抛 `OSError`，
    3.12 只返回 False——统一成"不是文件"，别让平台/版本差异变成崩溃。"""
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def disk_candidates(raw_path: str, repo: Path) -> List[Path]:
    """候选磁盘路径（按优先级）：仓库内相对/`/` 开头的 POSIX 路径，以及确实位于仓库内的绝对路径。

    **不给 `repo` 就不碰用户工作区**——日志里写着某个绝对路径不等于工具获准去读它。
    """
    if not raw_path or raw_path == events.UNKNOWN:
        return []
    root = Path(repo)
    out: List[Path] = []
    posix = raw_path.replace("\\", "/")
    if posix.startswith("/"):
        out.append(root.joinpath(*[part for part in posix.split("/") if part]))
    else:
        out.append(root.joinpath(*[part for part in posix.split("/") if part]))
    try:
        absolute = Path(raw_path)
    except (OSError, ValueError):
        absolute = None
    if absolute is not None and absolute.is_absolute():
        try:
            resolved = absolute.resolve()
            base = root.resolve()
            if resolved == base or base in resolved.parents:
                out.append(absolute)
        except OSError:
            pass
    unique: List[Path] = []
    for item in out:
        if item not in unique:
            unique.append(item)
    return unique


def read_disk_text(raw_path: str, repo: Optional[str]) -> Tuple[Optional[Path], Optional[str]]:
    """读磁盘基线（只读、CRLF 归一）；找不到返回 ``(None, None)``。"""
    if repo is None:
        return None, None
    for candidate in disk_candidates(raw_path, Path(repo)):
        if not _is_file(candidate):
            continue
        try:
            return candidate, serialize.read_text_normalized(str(candidate))
        except (OSError, UnicodeDecodeError):
            continue
    return None, None


# --------------------------------------------------------------------------- #
# 原始行回溯（事件流不复制大文本，见 M1 的三条设计决定）
# --------------------------------------------------------------------------- #


class _SourceCache:
    """按 `seq` 取回原始源行；同一行只读一次（大文本不进事件流）。"""

    def __init__(self) -> None:
        self._records: Dict[Tuple[str, int], Mapping[str, Any]] = {}

    def record(self, path: Any, seq: Any) -> Mapping[str, Any]:
        key = (str(path), int(seq))
        if key not in self._records:
            self._records[key] = readers.read_source_line(Path(key[0]), key[1])
        return self._records[key]


def _claude_tool_input(record: Mapping[str, Any], tool_use_id: Any) -> Mapping[str, Any]:
    """从 tool_call 源行取回该 `tool_use` 的 input（id 匹配；只有一块时才回落到它）。"""
    message = record.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, list):
        return {}
    blocks = [block for block in content if isinstance(block, Mapping) and block.get("type") == "tool_use"]
    for block in blocks:
        if block.get("id") == tool_use_id and isinstance(block.get("input"), Mapping):
            return dict(block["input"])
    if len(blocks) == 1 and isinstance(blocks[0].get("input"), Mapping):
        return dict(blocks[0]["input"])
    return {}


def _claude_result_meta(record: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = record.get("toolUseResult")
    return meta if isinstance(meta, Mapping) else {}


def _codex_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, Mapping) else {}


# --------------------------------------------------------------------------- #
# 逐事件还原
# --------------------------------------------------------------------------- #


def _result(status: str, source: str, reason: str, base: str, **kwargs: Any) -> Reconstruction:
    """收口构造：confidence 由 base 推导，避免"结果好看就标高置信度"的自由发挥。"""
    evidence = dict(kwargs.pop("evidence", {}) or {})
    evidence.setdefault("confidence_base", base)
    return Reconstruction(
        source=source,
        confidence=CONFIDENCE_BY_BASE.get(base, CONFIDENCE_NONE),
        status=status,
        reason=reason,
        evidence=evidence,
        **kwargs
    )


def _claude_reconstruction(
    call_event: events.Event,
    result_event: Optional[events.Event],
    call_input: Mapping[str, Any],
    result_meta: Mapping[str, Any],
    session_write: Optional[str],
    repo: Optional[str],
) -> Reconstruction:
    file_path = call_event.path
    tool = call_event.tool if call_event.tool != events.UNKNOWN else "Edit"
    before_full = result_meta.get("originalFile")
    before_full = before_full if isinstance(before_full, str) else None
    patch = result_meta.get("structuredPatch")
    patch = patch if isinstance(patch, list) else []

    # ---- ① originalFile + 非空 structuredPatch（K14：空表不算 diff）---------- #
    if before_full is not None and patch:
        hunks = [h for h in (_claude_hunk(item) for item in patch if isinstance(item, Mapping)) if h is not None]
        if hunks:
            base_lines, trailing = split_lines(before_full)
            after_lines, aligned, detail = apply_hunks(base_lines, hunks)
            if after_lines is not None:
                return _result(
                    STATUS_OK,
                    SOURCE_CLAUDE_PATCH,
                    REASON_NONE,
                    BASE_LOG_TEXT,
                    file=file_path,
                    before=before_full,
                    after=join_lines(after_lines, trailing),
                    hunks=hunks,
                    tool=tool,
                    seq=call_event.seq,
                    evidence={"aligned_by": aligned, "hunks": detail, "base_detail": "originalFile"},
                )
            return _result(
                STATUS_UNRESOLVED,
                SOURCE_CLAUDE_PATCH,
                aligned,
                BASE_LOG_TEXT,
                file=file_path,
                before=None,
                after=None,
                hunks=hunks,
                tool=tool,
                seq=call_event.seq,
                evidence={"hunks": detail, "base_detail": "originalFile"},
            )
        # patch 非空但没有可解析的 hunk（形态不认识）：如实降级，不硬套
        return _result(
            STATUS_PARTIAL,
            SOURCE_CLAUDE_PATCH,
            REASON_HUNK_MISMATCH,
            BASE_FRAGMENT,
            file=file_path,
            before=None,
            after=None,
            hunks=[],
            tool=tool,
            seq=call_event.seq,
            evidence={"base_detail": "originalFile", "patch_hunks": len(patch)},
        )

    # ---- ② old_string / new_string 在 before 基线上定位替换 ------------------ #
    old_string = call_input.get("old_string")
    new_string = call_input.get("new_string")
    if isinstance(old_string, str) and old_string and isinstance(new_string, str):
        disk_path, disk_text = read_disk_text(file_path, repo)
        bases: List[Tuple[str, str, Optional[str]]] = []
        if before_full:
            bases.append((BASE_LOG_TEXT, "originalFile", before_full))
        if isinstance(disk_text, str) and disk_text:
            bases.append((BASE_WORKTREE, str(disk_path), disk_text))
        if isinstance(session_write, str) and session_write:
            bases.append((BASE_LOG_TEXT, "session_write", session_write))
        if not bases:
            return _result(
                STATUS_NO_BEFORE,
                SOURCE_CLAUDE_OLD_STRING,
                REASON_NO_BEFORE,
                BASE_NONE,
                file=file_path,
                before=None,
                after=None,
                hunks=[],
                tool=tool,
                seq=call_event.seq,
                evidence={"old_string_chars": len(old_string)},
            )
        base_kind, base_detail, base_text = bases[0]
        replace_all = bool(call_input.get("replace_all"))
        hits = count_occurrences(base_text, old_string)
        if hits == 0:
            return _result(
                STATUS_UNRESOLVED,
                SOURCE_CLAUDE_OLD_STRING,
                REASON_NOT_FOUND,
                BASE_NONE,
                file=file_path,
                before=None,
                after=None,
                hunks=[],
                tool=tool,
                seq=call_event.seq,
                evidence={"base_kind": base_kind, "base_detail": base_detail, "occurrences": 0},
            )
        if hits > 1 and not replace_all:
            return _result(
                STATUS_UNRESOLVED,
                SOURCE_CLAUDE_OLD_STRING,
                REASON_AMBIGUOUS,
                BASE_NONE,
                file=file_path,
                before=None,
                after=None,
                hunks=[],
                tool=tool,
                seq=call_event.seq,
                evidence={"base_kind": base_kind, "base_detail": base_detail, "occurrences": hits},
            )
        hunks = _substring_hunks(base_text, old_string, new_string, replace_all)
        after = base_text.replace(old_string, new_string) if replace_all else base_text.replace(old_string, new_string, 1)
        return _result(
            STATUS_OK,
            SOURCE_CLAUDE_OLD_STRING,
            REASON_NONE,
            base_kind,
            file=file_path,
            before=base_text,
            after=after,
            hunks=hunks,
            tool=tool,
            seq=call_event.seq,
            evidence={"base_detail": base_detail, "occurrences": hits, "aligned_by": ALIGN_CONTENT_SEARCH if hits == 1 else "replace_all"},
        )

    # ---- ④ 都不可得：Write 只给得出 after，before 不存在（新建文件）--------- #
    content = call_input.get("content")
    if tool == "Write" and isinstance(content, str) and content:
        return _result(
            STATUS_NO_BEFORE,
            SOURCE_CLAUDE_WRITE,
            REASON_ADD_NO_BEFORE,
            BASE_NONE,
            file=file_path,
            before=None,
            after=content,
            hunks=[],
            tool=tool,
            seq=call_event.seq,
            evidence={"base_detail": "write_content"},
        )
    return _result(
        STATUS_NO_BEFORE,
        SOURCE_NONE,
        REASON_NO_BEFORE,
        BASE_NONE,
        file=file_path,
        before=None,
        after=None,
        hunks=[],
        tool=tool,
        seq=call_event.seq,
        evidence={},
    )


def _claude_hunk(raw: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return None
    old_side, new_side = normalize_diff_lines([item for item in lines if isinstance(item, str)])
    old_start = raw.get("oldStart")
    old_start = old_start if isinstance(old_start, int) and not isinstance(old_start, bool) else None
    return hunk_of(old_side, new_side, old_start)


def _substring_hunks(base_text: str, old_string: str, new_string: str, replace_all: bool) -> List[Dict[str, Any]]:
    """把"子串替换"表达成 hunk（带命中行号）——下游（M4）据此定位被改的符号。"""
    hunks: List[Dict[str, Any]] = []
    cursor = 0
    while True:
        position = base_text.find(old_string, cursor)
        if position < 0:
            break
        line_number = base_text.count("\n", 0, position) + 1
        hunks.append(hunk_of(_lines_of(old_string), _lines_of(new_string), line_number))
        cursor = position + len(old_string)
        if not replace_all:
            break
    return hunks


def _lines_of(text: str) -> List[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


# --------------------------------------------------------------------------- #
# Codex：两条通道（结构化优先，patch 文本兜底）
# --------------------------------------------------------------------------- #


def _codex_evidence(file_read: Any, cache: _SourceCache) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """收集 Codex 侧写文件证据：``{文件: {"file_change": ..., "apply_patch": ...}}`` + 首次出现顺序。

    两条通道**不承诺配对**（`FileChange` 没有 call_id，按时间序配对属猜测，M1 已定）；
    因此这里只按文件聚合，优先级在还原时判定。
    """
    channels: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for event in file_read.events:
        if event.tool == "apply_patch" and event.payload.get("has_input"):
            record = cache.record(file_read.path, event.seq)
            raw = _codex_payload(record).get("input")
            if not isinstance(raw, str):
                continue
            for block in parse_patch_blocks(raw):
                target = block["file"]
                if target not in channels:
                    channels[target] = {}
                    order.append(target)
                channels[target].setdefault("apply_patch", {"block": block, "seq": event.seq})
        elif event.tool == "file_change":
            record = cache.record(file_read.path, event.seq)
            item = _codex_payload(record).get("item")
            changes = item.get("changes") if isinstance(item, Mapping) else None
            if not isinstance(changes, Mapping):
                continue
            for raw_path, change in changes.items():
                if not isinstance(change, Mapping):
                    continue
                target = str(raw_path)
                if target not in channels:
                    channels[target] = {}
                    order.append(target)
                channels[target].setdefault("file_change", {"change": dict(change), "seq": event.seq})
    return channels, order


def _codex_reconstruction(file_path: str, channel: str, payload_item: Mapping[str, Any], repo: Optional[str]) -> Reconstruction:
    seq = payload_item.get("seq") or 0
    channels = payload_item.get("channels") or []
    if channel == "file_change":
        change = payload_item["change"]
        unified = change.get("unified_diff")
        content = change.get("content")
        if isinstance(unified, str) and unified:
            hunks = parse_unified_diff(unified)
            return _codex_from_hunks(file_path, SOURCE_CODEX_DIFF, hunks, seq, repo, {"channels": channels, "base_detail": "file_change.unified_diff"})
        if isinstance(content, str) and content:
            return _result(
                STATUS_NO_BEFORE,
                SOURCE_CODEX_CONTENT,
                REASON_ADD_NO_BEFORE,
                BASE_NONE,
                file=file_path,
                before=None,
                after=content,
                hunks=[],
                tool="file_change",
                seq=seq,
                evidence={"channels": channels, "base_detail": "file_change.content"},
            )
        return _result(
            STATUS_NO_BEFORE,
            SOURCE_CODEX_CONTENT,
            REASON_NO_BEFORE,
            BASE_NONE,
            file=file_path,
            before=None,
            after=None,
            hunks=[],
            tool="file_change",
            seq=seq,
            evidence={"channels": channels, "change_type": change.get("type")},
        )

    block = payload_item["block"]
    hunks = list(block["hunks"])
    evidence_extra = {"channels": channels, "base_detail": "apply_patch", "action": block.get("action")}
    if block.get("moved_to"):
        evidence_extra["moved_to"] = block["moved_to"]
    if block.get("action") == CHANGE_ADD or not hunks:
        after_lines: List[str] = []
        for hunk in hunks:
            after_lines.extend(hunk["new_lines"])
        after = join_lines(after_lines, True) if after_lines else None
        return _result(
            STATUS_NO_BEFORE,
            SOURCE_CODEX_PATCH,
            REASON_ADD_NO_BEFORE if after is not None else REASON_NO_BEFORE,
            BASE_NONE,
            file=file_path,
            before=None,
            after=after,
            hunks=hunks,
            tool="apply_patch",
            seq=seq,
            evidence=evidence_extra,
        )
    return _codex_from_hunks(file_path, SOURCE_CODEX_PATCH, hunks, seq, repo, evidence_extra)


def _codex_from_hunks(file_path: str, source: str, hunks: List[Dict[str, Any]], seq: Any, repo: Optional[str], evidence_extra: Mapping[str, Any]) -> Reconstruction:
    """Codex 侧：日志里**没有 before 全文**（实测只有 diff/片段），全文基线只能来自磁盘。"""
    disk_path, disk_text = read_disk_text(file_path, repo)
    if isinstance(disk_text, str) and disk_text:
        base_lines, trailing = split_lines(disk_text)
        after_lines, aligned, detail = apply_hunks(base_lines, hunks)
        if after_lines is not None:
            evidence = dict(evidence_extra)
            evidence.update({"aligned_by": aligned, "hunks": detail, "base_detail": "worktree", "base_path": str(disk_path)})
            return _result(
                STATUS_OK,
                source,
                REASON_NONE,
                BASE_WORKTREE,
                file=file_path,
                before=disk_text,
                after=join_lines(after_lines, trailing),
                hunks=hunks,
                tool="apply_patch" if source == SOURCE_CODEX_PATCH else "file_change",
                seq=seq,
                evidence=evidence,
            )
        if aligned in (REASON_HUNK_MISMATCH, REASON_AMBIGUOUS):
            evidence = dict(evidence_extra)
            evidence.update({"aligned_by": REASON_NONE, "hunks": detail, "base_detail": "worktree", "base_path": str(disk_path), "mismatch": aligned})
            return _result(
                STATUS_PARTIAL,
                source,
                REASON_BASE_MISMATCH if aligned == REASON_HUNK_MISMATCH else REASON_AMBIGUOUS,
                BASE_FRAGMENT,
                file=file_path,
                before=None,
                after=None,
                hunks=hunks,
                tool="apply_patch" if source == SOURCE_CODEX_PATCH else "file_change",
                seq=seq,
                evidence=evidence,
            )
    reason = REASON_BASE_MISSING if repo is None or disk_text is None else REASON_BASE_MISMATCH
    evidence = dict(evidence_extra)
    evidence.update({"base_detail": "worktree" if disk_text is not None else "none", "mismatch": reason})
    return _result(
        STATUS_PARTIAL,
        source,
        reason,
        BASE_FRAGMENT,
        file=file_path,
        before=None,
        after=None,
        hunks=hunks,
        tool="apply_patch" if source == SOURCE_CODEX_PATCH else "file_change",
        seq=seq,
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# 会话级入口
# --------------------------------------------------------------------------- #


def reconstruct_session(file_read: Any, cfg: Mapping[str, Any], repo: Optional[str] = None) -> SessionReconstruction:
    """还原一个会话里所有的文件改动（逐文件独立置信度），并给出逐 episode 的可用性结论。"""
    cache = _SourceCache()
    if file_read.source == "claude":
        files = _claude_files(file_read, cache, repo)
    else:
        files = _codex_files(file_read, cache, repo)

    session_id = events.UNKNOWN
    for event in file_read.events:
        if event.session_id != events.UNKNOWN:
            session_id = event.session_id
            break
    result = SessionReconstruction(
        source=file_read.source,
        session_id=session_id,
        source_file=str(file_read.path),
        files=files,
    )
    result.episodes = _episode_verdicts(file_read, files, cfg, session_id)
    return result


def _claude_files(file_read: Any, cache: _SourceCache, repo: Optional[str]) -> List[Reconstruction]:
    """Claude：以**调用侧**（tool_use）为锚，结果侧（toolUseResult）做 before 镜像。

    以调用侧为锚的原因：每个写文件调用都该有一条还原结论——包括"结果侧没有任何 before 证据"
    的纯新建文件（它必须显式落 `no_before_image`，而不是静默消失）。
    """
    calls = [item for item in file_read.events if item.kind == "tool_call" and item.tool in episode_mod.CLAUDE_WRITE_TOOLS]
    results: Dict[Any, events.Event] = {}
    for item in file_read.events:
        if item.kind != "tool_result":
            continue
        key = item.payload.get("tool_use_id")
        if isinstance(key, str):
            results.setdefault(key, item)
    writes: Dict[str, List[int]] = {}
    for item in calls:
        if item.tool == "Write" and item.path != events.UNKNOWN:
            writes.setdefault(item.path, []).append(item.seq)

    out: List[Reconstruction] = []
    for call in calls:
        call_id = call.payload.get("tool_use_id")
        result_event = results.get(call_id) if isinstance(call_id, str) else None
        call_line = cache.record(file_read.path, call.seq)
        call_input = _claude_tool_input(call_line, call_id)
        result_meta: Mapping[str, Any] = {}
        if result_event is not None:
            result_meta = _claude_result_meta(cache.record(file_read.path, result_event.seq))
        session_write = None
        if call.tool != "Write" and call.path != events.UNKNOWN:
            session_write = _latest_session_write(file_read, cache, writes.get(call.path, []), call.seq)
        out.append(
            _claude_reconstruction(
                call_event=_with_path(call, result_event, result_meta),
                result_event=result_event,
                call_input=call_input,
                result_meta=result_meta,
                session_write=session_write,
                repo=repo,
            )
        )
    return out


def _with_path(call: events.Event, result_event: Optional[events.Event], result_meta: Mapping[str, Any]) -> events.Event:
    """文件路径以调用侧为准，取不到时用结果侧的 `filePath`（两处实测同源）。"""
    if call.path != events.UNKNOWN:
        return call
    fallback = result_meta.get("filePath") or (result_event.path if result_event is not None else None)
    if not isinstance(fallback, str) or not fallback:
        return call
    return events.Event(
        source=call.source,
        session_id=call.session_id,
        seq=call.seq,
        source_offset=call.source_offset,
        ts=call.ts,
        kind=call.kind,
        tool=call.tool,
        path=fallback,
        payload=call.payload,
    )


def _latest_session_write(file_read: Any, cache: _SourceCache, write_seqs: Sequence[int], before_seq: int) -> Optional[str]:
    """同会话里更早的 `Write.content`（新建之后又被 Edit 的文件，其 before 基线在这里）。"""
    candidates = [seq for seq in write_seqs if seq < before_seq]
    if not candidates:
        return None
    record = cache.record(file_read.path, max(candidates))
    content = None
    message = record.get("message")
    content_blocks = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content_blocks, list):
        for block in content_blocks:
            if isinstance(block, Mapping) and block.get("type") == "tool_use" and block.get("name") == "Write":
                raw = block.get("input")
                if isinstance(raw, Mapping) and isinstance(raw.get("content"), str):
                    content = raw["content"]
    return content


def _codex_files(file_read: Any, cache: _SourceCache, repo: Optional[str]) -> List[Reconstruction]:
    channels, order = _codex_evidence(file_read, cache)
    out: List[Reconstruction] = []
    for file_path in order:
        available = channels[file_path]
        # 首选结构化通道（FileChange.unified_diff/content），patch 文本降为兜底；
        # 同一文件两条通道都在时记录 channels，便于回溯"当时还有哪些证据"。
        present = sorted(available)
        if "file_change" in available:
            item = dict(available["file_change"])
            item["channels"] = present
            out.append(_codex_reconstruction(file_path, "file_change", item, repo))
        else:
            item = dict(available["apply_patch"])
            item["channels"] = present
            out.append(_codex_reconstruction(file_path, "apply_patch", item, repo))
    return out


def _episode_verdicts(file_read: Any, files: Sequence[Reconstruction], cfg: Mapping[str, Any], session_id: str) -> List[Dict[str, Any]]:
    """episode 级结论：任一文件无 before 镜像 → `unverified` + `no_before_image`（不进生成路径）。"""
    split = episode_mod.split(file_read.events, cfg, session_files={session_id: str(file_read.path)})
    by_file: Dict[str, List[Reconstruction]] = {}
    for item in files:
        by_file.setdefault(item.file, []).append(item)
    episodes: List[Dict[str, Any]] = []
    for episode in split.episodes:
        related: List[Reconstruction] = []
        for path in episode.files:
            related.extend(by_file.get(path, []))
        if not related:
            status, reason = STATUS_UNRESOLVED, REASON_NO_BEFORE
        elif any(item.status == STATUS_NO_BEFORE for item in related):
            status, reason = "unverified", REASON_NO_BEFORE
        elif any(item.status == STATUS_UNRESOLVED for item in related):
            status, reason = STATUS_UNRESOLVED, REASON_AMBIGUOUS
        elif any(item.status == STATUS_PARTIAL for item in related):
            status, reason = STATUS_PARTIAL, REASON_BASE_MISSING
        else:
            status, reason = STATUS_OK, REASON_NONE
        episodes.append(
            {
                "episode_id": episode.episode_id,
                "status": status,
                "reason": reason,
                "files": [
                    {"file": item.file, "status": item.status, "confidence": item.confidence, "source": item.source}
                    for item in related
                ],
            }
        )
    return episodes


# --------------------------------------------------------------------------- #
# 会话引用解析（`source:session_id` 或日志文件路径）
# --------------------------------------------------------------------------- #


def looks_like_path(ref: str) -> bool:
    """`claude:<id>` 与 `C:<...>` 形态要分得开——Windows 盘符不是来源前缀。"""
    if not ref:
        return False
    head, separator, tail = ref.partition(":")
    if separator and head in events.SOURCES:
        return False
    return True


def resolve_session_file(ref: str, limits: Mapping[str, Any], roots: Optional[Mapping[str, str]] = None) -> Path:
    """把会话引用解析成日志文件路径；解析不了一律显式报错（不猜、不返回空会话）。"""
    from . import paths as paths_mod

    overrides = dict(roots or {})
    path = Path(ref)
    if _is_file(path):
        return path
    if looks_like_path(ref) and not (":" in ref):
        raise InputUnavailable(
            "会话引用认不出来: {}".format(ref),
            hint="写成 <source>:<session_id>（source ∈ {}）或直接给 jsonl 日志路径".format("/".join(events.SOURCES)),
        )
    head, separator, session_id = ref.partition(":")
    if not separator or head not in events.SOURCES or not session_id:
        raise InputUnavailable(
            "会话引用认不出来: {}".format(ref),
            hint="写成 <source>:<session_id>（source ∈ {}）或直接给 jsonl 日志路径".format("/".join(events.SOURCES)),
        )
    root = paths_mod.discover_root(head, explicit=overrides.get(head))
    found = _find_session_in_root(head, session_id, root, limits)
    return found


def _find_session_in_root(source: str, session_id: str, root: Path, limits: Mapping[str, Any]) -> Path:
    """在日志根内按会话 id 找文件：只读每份文件的前若干行（**不扫全量内容**）。"""
    notes: Counter = Counter()
    max_lines = int(limits["version_probe_lines"])
    try:
        candidates = readers.collect_jsonl([str(root)])
    except InputUnavailable:
        candidates = []
    for candidate in candidates:
        try:
            for lineno, _offset, record in readers.read_records(candidate, int(limits["max_line_bytes"]), notes):
                if lineno > max_lines:
                    break
                if _record_session_id(source, record) == session_id:
                    return candidate
        except Exception:  # 单件读不了不影响"按 id 找文件"这件事（批量隔离，K21）
            continue
    raise InputUnavailable(
        "在 {} 日志根下没找到会话 {}（只探测了每份文件的前 {} 行）".format(source, session_id, max_lines),
        hint="也可以直接把 jsonl 日志路径传给本命令",
    )


def _record_session_id(source: str, record: Mapping[str, Any]) -> Optional[str]:
    if source == "claude":
        value = record.get("sessionId") or record.get("session_id")
    else:
        payload = _codex_payload(record)
        value = payload.get("session_id") or payload.get("id")
    return value if isinstance(value, str) and value else None


def load_session(path: Path, limits: Mapping[str, Any]) -> Any:
    return readers.read_file(path, limits)
