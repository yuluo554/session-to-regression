"""编码守卫：所有会打印的可执行入口（CLI 与 tools/ 下每个脚本）都必须先调它。

为什么需要：本机中文控制台是 cp936、CI 管道常见 cp1252——同一份含中文的输出，在
其中一个上会 ``UnicodeEncodeError``。守卫按流自身的 ``encoding`` 探针决定是否
reconfigure 成 UTF-8，中文控制台（cp936/gbk/cp950 等）保持不动以免打乱用户终端。

配套（三层独立加固，缺一层就有盲区）：① 本模块（救用户终端）；② 子进程显式
``encoding=``（救一切环境，无环境变量可替代）；③ CI ``env: PYTHONUTF8=1``（救 CI 全部步骤）。
"""

from __future__ import annotations

import sys
from typing import Optional

#: 探针命中的编码不再动（UTF-8 已就绪；中日韩本地编码保持用户习惯）
_KEEP = (
    "utf8",
    "utf-8",
    "cp65001",
    "cp936",
    "gbk",
    "gb2312",
    "gb18030",
    "cp950",
    "big5",
)


def _normalize(encoding: Optional[str]) -> str:
    return (encoding or "").lower().replace("_", "-")


def ensure_utf8_stdio(stdout=None, stderr=None) -> None:
    """把 stdout/stderr 收敛到 UTF-8（除非它们已经是 UTF-8 或 CJK 本地编码）。"""
    import io

    for stream in (stdout if stdout is not None else sys.stdout, stderr if stderr is not None else sys.stderr):
        if stream is None:
            continue
        if _normalize(getattr(stream, "encoding", None)) in _KEEP:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError, io.UnsupportedOperation):
            # 流不支持重配置（如已被替换的 StringIO）时静默跳过：守卫是加固，不是功能前提。
            continue
