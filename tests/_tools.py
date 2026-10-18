"""按路径加载 tools/ 与 scripts/ 下的脚本：测试与 CI 必须共用同一份实现，别在测试里复制第二份清单。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(relative: Path, module_name: str) -> ModuleType:
    assert relative.is_file(), "脚本不存在: {}".format(relative)
    spec = importlib.util.spec_from_file_location(module_name, str(relative))
    assert spec is not None and spec.loader is not None, "无法加载: {}".format(relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool(name: str) -> ModuleType:
    return _load(REPO_ROOT / "tools" / (name + ".py"), "tools_" + name)


def load_script(name: str) -> ModuleType:
    """加载 `scripts/` 下的脚本（发布/审计类门；它们不是 `tools/` 的运行期工具）。"""
    return _load(REPO_ROOT / "scripts" / (name + ".py"), "scripts_" + name)
