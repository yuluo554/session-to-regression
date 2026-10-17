"""确定性写盘单点：所有产物（事件流 / manifest / 基准真值）都必须经这里落盘。

为什么单点：多个子命令各写各的 ``open() + json.dump()`` 迟早漂移（一个带 BOM、一个排序不同、
一个 CRLF）——「同输入双跑逐字节一致」的承诺就会在某个子命令上悄悄失效。本模块把序列化参数
冻结成契约：

* ``json.dumps(sort_keys=True, ensure_ascii=False, indent=2)``——中文不转 ``\\uXXXX``（人读产物），
  键序与平台无关；
* LF 行尾 + 结尾换行（``newline="\\n"`` 显式写出，Windows 上也不生成 CRLF）；
* 原子写（临时文件 + ``os.replace``），中断不会留下半个产物；
* 不写时钟、不写随机值（时间类字段一律由调用方按需显式传入纯值）。

Python 3.8 兼容：``Path.write_text(newline=...)`` 是 3.10+ 参数，这里用 ``open(..., newline=...)``。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Union

JSON_INDENT = 2
JSON_KWARGS = {"sort_keys": True, "ensure_ascii": False, "indent": JSON_INDENT}
TRAILING_NEWLINE = "\n"


def dumps_json(obj: Any) -> str:
    """确定性 JSON 文本（LF + 结尾换行）。"""
    text = json.dumps(obj, **JSON_KWARGS)
    if not text.endswith("\n"):
        text += TRAILING_NEWLINE
    return text.replace("\r\n", "\n")


def write_text_atomic(path: Union[str, Path], text: str) -> Path:
    """原子写文本（UTF-8、LF）。返回目标路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=".s2r-", suffix=".tmp", dir=str(target.parent))
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        with io.open(str(tmp), "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text.replace("\r\n", "\n"))
        os.replace(str(tmp), str(target))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target


def write_json_atomic(path: Union[str, Path], obj: Any) -> Path:
    return write_text_atomic(path, dumps_json(obj))


def read_text(path: Union[str, Path]) -> str:
    """读文本并按面值返回（不自动改行尾）——供逐字节对账使用。"""
    with io.open(str(path), "r", encoding="utf-8", newline="") as stream:
        return stream.read()


def read_text_normalized(path: Union[str, Path]) -> str:
    """读文本并把 CRLF 归一成 LF——只在需要跨平台比较内容时使用。"""
    return read_text(path).replace("\r\n", "\n")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Union[str, Path]) -> str:
    digest = hashlib.sha256()
    with io.open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def tree_digest(root: Union[str, Path], relative_paths: Any) -> str:
    """逐文件 sha256 聚合的目录摘要（与 mtime 无关）：``<sha256>  <相对路径>`` 逐行再取哈希。"""
    base = Path(root)
    lines = []
    for rel in sorted(str(item).replace("\\", "/") for item in relative_paths):
        lines.append("{}  {}".format(sha256_file(base / rel), rel))
    return sha256_text("\n".join(lines) + "\n")
