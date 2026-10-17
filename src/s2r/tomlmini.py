"""严格 TOML 子集读取器（纯标准库，零第三方依赖）。

为什么自研而不是引 ``tomli``：本题定位是「纯本地零依赖 CLI」，Python 3.8 的 stdlib 里
没有 ``tomllib``（3.11+ 才有）；引第三方会让「零依赖可离线复现」的承诺变成一个附条件承诺。
代价必须如实声明：**只支持下面的子集**，超出子集一律显式报错（带行号），绝不静默忽略。

支持的语法：

* 注释 ``#`` 到行尾；空行；
* 表头 ``[table]`` / ``[table.sub]``（不支持数组表 ``[[x]]``）；
* 键值 ``key = value``，键可为裸键（``[A-Za-z0-9_-]+``）或引号键；
* 值：字符串（单/双引号，支持 ``\\n \\t \\" \\\\`` 转义）、整数、浮点、布尔、单行数组、
  单行内联表 ``{k = v, ...}``；数组允许尾随逗号，元素类型必须一致（内联表/数组可作元素）。

不支持（显式报错，不猜测）：数组表 ``[[x]]``、多行字符串 ``\"\"\"``、点号键 ``a.b = 1``、
多行数组、多行内联表、日期时间字面量。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .errors import ConfigError

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_KEY_TOKEN = re.compile(r"""\s*(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*""")
_INT = re.compile(r"^[+-]?[0-9]+$")
_FLOAT = re.compile(r"^[+-]?[0-9]+\.[0-9]+$")


def loads(text: str, origin: str = "<string>") -> Dict[str, Any]:
    """把 TOML 子集文本解析成嵌套 dict。任何不合法形态都抛 ``ConfigError``（带行号）。"""
    root: Dict[str, Any] = {}
    current: Dict[str, Any] = root
    current_path: Tuple[str, ...] = ()
    seen_tables: Dict[Tuple[str, ...], Dict[str, Any]] = {(): root}
    lines = text.split("\n")
    index = 0

    while index < len(lines):
        lineno = index + 1
        line = _strip_comment(lines[index]).strip()
        index += 1
        if not line:
            continue

        if line.startswith("[["):
            raise _err(origin, lineno, "不支持数组表 [[...]]")

        if line.startswith("["):
            if not line.endswith("]"):
                raise _err(origin, lineno, "表头缺少收尾的 ]（表头不支持跨行）")
            path = _parse_table_path(line[1:-1], origin, lineno)
            if path in seen_tables:
                raise _err(origin, lineno, "表重复定义: {}".format(".".join(path)))
            table = root
            for depth in range(len(path)):
                prefix = path[: depth + 1]
                child = table.get(prefix[-1])
                if child is None:
                    child = {}
                    table[prefix[-1]] = child
                elif not isinstance(child, dict):
                    raise _err(origin, lineno, "键与表冲突: {}".format(".".join(prefix)))
                table = child
            seen_tables[path] = table
            current, current_path = table, path
            continue

        if "=" not in line:
            raise _err(origin, lineno, "既不是表头也不是 key = value")
        raw_key, raw_value = line.split("=", 1)
        key = _parse_key(raw_key, origin, lineno)
        value_text, index = _collect_value(raw_value, lines, index, origin, lineno)
        if key in current:
            raise _err(origin, lineno, "键重复定义: {}".format(".".join(current_path + (key,))))
        current[key] = _parse_value(value_text, origin, lineno)

    return root


def _collect_value(first: str, lines: List[str], index: int, origin: str, lineno: int) -> Tuple[str, int]:
    """收集一个可能跨行的值（数组/内联表可跨行；字符串不可跨行）。返回 (值文本, 新行游标)。"""
    parts = [first.strip()]
    while True:
        joined = " ".join(part for part in parts if part)
        depth, quote = _bracket_state(joined, origin, lineno)
        if quote is not None:
            raise _err(origin, lineno, "字符串缺少收尾引号（不支持多行字符串）")
        if depth == 0:
            return joined, index
        if index >= len(lines):
            raise _err(origin, lineno, "值未收尾（缺少 ] 或 }）")
        nxt = _strip_comment(lines[index]).strip()
        index += 1
        if nxt:
            parts.append(nxt)


def _bracket_state(text: str, origin: str, lineno: int) -> Tuple[int, Any]:
    """返回 (括号深度, 未闭合引号)。深度为负 = 多余的收尾括号，直接报错。"""
    depth = 0
    quote = None
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if quote:
            if ch == "\\" and quote == '"':
                escaped = True
                continue
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            continue
        if ch in "[{":
            depth += 1
            continue
        if ch in "]}":
            depth -= 1
            if depth < 0:
                raise _err(origin, lineno, "多余的收尾括号: {!r}".format(ch))
    return depth, quote


def _strip_comment(line: str) -> str:
    """去掉注释；引号内的 ``#`` 不算注释。"""
    out: List[str] = []
    quote = None
    escaped = False
    for ch in line:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\" and quote == '"':
            out.append(ch)
            escaped = True
            continue
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _parse_table_path(raw: str, origin: str, lineno: int) -> Tuple[str, ...]:
    parts: List[str] = []
    pos = 0
    while pos < len(raw):
        match = _KEY_TOKEN.match(raw, pos)
        if not match or match.end() == pos:
            raise _err(origin, lineno, "表名不合法: {!r}".format(raw))
        quoted_double, quoted_single, bare = match.group(1), match.group(2), match.group(3)
        parts.append(quoted_double if quoted_double is not None else quoted_single if quoted_single is not None else bare)
        pos = match.end()
        if pos < len(raw):
            if raw[pos] != ".":
                raise _err(origin, lineno, "表名分隔符应为 . : {!r}".format(raw))
            pos += 1
    if not parts:
        raise _err(origin, lineno, "空表名")
    return tuple(parts)


def _parse_key(raw: str, origin: str, lineno: int) -> str:
    text = raw.strip()
    if not text:
        raise _err(origin, lineno, "缺键名")
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return _unescape(text[1:-1], origin, lineno)
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1]
    if not _BARE_KEY.match(text):
        raise _err(origin, lineno, "键名不合法（点号键不支持）: {!r}".format(text))
    return text


def _parse_value(raw: str, origin: str, lineno: int) -> Any:
    if not raw:
        raise _err(origin, lineno, "缺值")
    if raw.startswith('"""') or raw.startswith("'''"):
        raise _err(origin, lineno, "不支持多行字符串")
    if raw.startswith("{"):
        return _parse_inline_table(raw, origin, lineno)
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.startswith("["):
        return _parse_array(raw, origin, lineno)
    if raw.startswith('"') or raw.startswith("'"):
        return _parse_string(raw, origin, lineno)
    if _INT.match(raw):
        return int(raw)
    if _FLOAT.match(raw):
        return float(raw)
    raise _err(origin, lineno, "值形态不支持: {!r}".format(raw))


def _parse_string(raw: str, origin: str, lineno: int) -> str:
    quote = raw[0]
    end = _find_string_end(raw, quote)
    if end is None:
        raise _err(origin, lineno, "字符串缺少收尾引号: {!r}".format(raw))
    if end != len(raw) - 1:
        raise _err(origin, lineno, "字符串后有意外内容: {!r}".format(raw[end + 1 :]))
    body = raw[1:end]
    if quote == "'":
        return body
    return _unescape(body, origin, lineno)


def _find_string_end(text: str, quote: str) -> Optional[int]:
    """返回与 ``text[0]`` 配对的那个引号下标；找不到返回 None。"""
    index = 1
    while index < len(text):
        ch = text[index]
        if ch == "\\" and quote == '"':
            index += 2
            continue
        if ch == quote:
            return index
        index += 1
    return None


def _unescape(body: str, origin: str, lineno: int) -> str:
    out: List[str] = []
    index = 0
    mapping = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}
    while index < len(body):
        ch = body[index]
        if ch != "\\":
            out.append(ch)
            index += 1
            continue
        if index + 1 >= len(body):
            raise _err(origin, lineno, "转义符后缺字符")
        nxt = body[index + 1]
        if nxt not in mapping:
            raise _err(origin, lineno, "不支持的转义: \\{}".format(nxt))
        out.append(mapping[nxt])
        index += 2
    return "".join(out)


def _parse_array(raw: str, origin: str, lineno: int) -> List[Any]:
    end = _find_top_level_close(raw, "]")
    if end is None:
        raise _err(origin, lineno, "数组缺少收尾的 ]（不支持多行数组）")
    if end != len(raw) - 1:
        raise _err(origin, lineno, "数组 ] 后有多余内容: {!r}".format(raw[end + 1 :]))
    body = raw[1:end].strip()
    if not body:
        return []
    items: List[Any] = []
    kind = None
    for chunk in _split_top_level(body, origin, lineno):
        value = _parse_value(chunk.strip(), origin, lineno)
        if kind is None:
            kind = type(value).__name__
        elif type(value).__name__ != kind:
            raise _err(origin, lineno, "数组元素类型不一致: {} 与 {}".format(kind, type(value).__name__))
        items.append(value)
    return items


def _parse_inline_table(raw: str, origin: str, lineno: int) -> Dict[str, Any]:
    end = _find_top_level_close(raw, "}")
    if end is None:
        raise _err(origin, lineno, "内联表缺少收尾的 }（不支持多行内联表）")
    if end != len(raw) - 1:
        raise _err(origin, lineno, "内联表 }} 后有多余内容: {!r}".format(raw[end + 1 :]))
    body = raw[1:end].strip()
    table: Dict[str, Any] = {}
    if not body:
        return table
    for chunk in _split_top_level(body, origin, lineno):
        if "=" not in chunk:
            raise _err(origin, lineno, "内联表元素应为 key = value: {!r}".format(chunk.strip()))
        raw_key, raw_value = chunk.split("=", 1)
        key = _parse_key(raw_key, origin, lineno)
        if key in table:
            raise _err(origin, lineno, "内联表键重复: {}".format(key))
        table[key] = _parse_value(raw_value.strip(), origin, lineno)
    return table


def _find_top_level_close(text: str, closer: str) -> Optional[int]:
    """返回与 ``text[0]`` 配对的顶层收尾括号下标；不平衡/找不到返回 None。"""
    if not text or text[0] not in "[{":
        return None
    depth = 0
    quote = None
    escaped = False
    for index, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote:
            if ch == "\\" and quote == '"':
                escaped = True
                continue
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            continue
        if ch in "[{":
            depth += 1
            continue
        if ch in "]}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                return index if ch == closer else None
    return None


def _split_top_level(body: str, origin: str, lineno: int) -> List[str]:
    """按顶层逗号切分（引号内与括号内的逗号不切）。"""
    chunks: List[str] = []
    buf: List[str] = []
    quote = None
    depth = 0
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch in "[{":
            depth += 1
            buf.append(ch)
            continue
        if ch in "]}":
            depth -= 1
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            chunks.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if quote:
        raise _err(origin, lineno, "字符串缺少收尾引号")
    tail = "".join(buf)
    if tail.strip():
        chunks.append(tail)
    if any(not chunk.strip() for chunk in chunks):
        raise _err(origin, lineno, "有连续逗号或多处逗号位置非法")
    return chunks


def _err(origin: str, lineno: int, message: str) -> ConfigError:
    return ConfigError("{}:{}: {}".format(origin, lineno, message))
