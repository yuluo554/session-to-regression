"""测试公共配置。

源码树可直接 `pytest`（未安装时把 src 补进 sys.path）；**装了包就用装的那份**——
不无条件抢占 sys.path，否则「测试通过」可能证明的是源码副本而不是安装面。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

try:  # pragma: no cover - 取决于当前环境是否已安装本包
    import s2r  # noqa: F401
except ImportError:  # pragma: no cover
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def config_path(repo_root: Path) -> Path:
    return repo_root / "config" / "scoring.toml"
