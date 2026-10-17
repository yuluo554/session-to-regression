"""按路径加载 tools/ 下的脚本：测试与 CI 必须共用同一份实现，别在测试里复制第二份清单。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str) -> ModuleType:
    path = REPO_ROOT / "tools" / (name + ".py")
    assert path.is_file(), "tools 脚本不存在: {}".format(path)
    spec = importlib.util.spec_from_file_location("tools_" + name, str(path))
    assert spec is not None and spec.loader is not None, "无法加载: {}".format(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
