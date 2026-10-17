"""日志根发现：路径一律自适应，**不得硬编码用户名或安装形态**。

顺序（前一个命中即用）：显式参数 → 环境变量 → 本机常见位置（`Path.home()` 展开）。
**找不到就显式报错（exit 2）**——绝不"返回空事件流"：那会把"没有数据"伪装成"数据里没有修复"。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from .errors import InputUnavailable

ENV_CLAUDE_ROOT = "S2R_CLAUDE_ROOT"
ENV_CODEX_ROOT = "S2R_CODEX_ROOT"

#: 常见位置（相对 HOME 展开），按顺序探测；不写死任何用户名
CLAUDE_RELATIVE_CANDIDATES = (( ".claude", "projects"),)
CODEX_RELATIVE_CANDIDATES = (( ".codex", "sessions"),)

#: 环境变量覆盖用的键（供 CLI 与文档引用，避免两处各写一份字符串）
ENV_KEYS: Dict[str, str] = {"claude": ENV_CLAUDE_ROOT, "codex": ENV_CODEX_ROOT}


def _candidates(source: str, home: Path):
    table = {"claude": CLAUDE_RELATIVE_CANDIDATES, "codex": CODEX_RELATIVE_CANDIDATES}
    for parts in table[source]:
        yield home.joinpath(*parts)


def discover_root(source: str, explicit: Optional[str] = None, env: Optional[Dict[str, str]] = None, home: Optional[Path] = None) -> Path:
    """定位某个 harness 的日志根目录；找不到即 ``InputUnavailable``。"""
    if source not in ENV_KEYS:
        raise InputUnavailable("未知日志来源: {!r}".format(source))
    environ = os.environ if env is None else env

    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise InputUnavailable("{} 日志根不存在或不是目录: {}".format(source, path))
        return path

    from_env = environ.get(ENV_KEYS[source])
    if from_env:
        path = Path(from_env)
        if not path.is_dir():
            raise InputUnavailable("{} 指向的日志根不存在: {}".format(ENV_KEYS[source], path))
        return path

    base = Path(home) if home is not None else Path.home()
    for candidate in _candidates(source, base):
        if candidate.is_dir():
            return candidate

    raise InputUnavailable(
        "未能自动发现 {} 会话日志根（已探测 HOME 下的常见位置）".format(source),
        hint="显式传 --{}-root DIR，或设置环境变量 {}".format(source, ENV_KEYS[source]),
    )
