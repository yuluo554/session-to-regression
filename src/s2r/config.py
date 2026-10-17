"""配置读取与形状校验：`scoring.toml` 是全项目唯一数值口径文件。

解析路径顺序（前一个命中即用，全都不命中 → exit 2，绝不回落内置默认值）：

1. ``--config`` 显式路径；
2. 环境变量 ``S2R_CONFIG``；
3. 当前工作目录向上最多 6 层内的 ``config/scoring.toml``（源码树/目标仓库形态）；
4. 随包副本 ``s2r/resources/scoring.toml``（安装后形态；与仓库文件逐字节相同，守门测试锁死）。

为什么有两处同名文件：仓库权威文件必须在 ``config/scoring.toml``（读者按题面去那里找），
而 ``pip install .``（非 editable）不会把仓库根目录带进安装面——只留仓库那份会让装好的包
在任意 CWD 下都定位不到口径文件。处置 = 两处逐字节相同 + 守门测试 + CI 步骤双向断言。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import tomlmini
from .errors import ConfigError, InputUnavailable

CONFIG_FILENAME = "scoring.toml"
REPO_RELATIVE = ("config", CONFIG_FILENAME)
PACKAGE_RELATIVE = ("resources", CONFIG_FILENAME)
ENV_VAR = "S2R_CONFIG"

ASCENT_LIMIT = 6

#: 必填键 → 期望类型。缺失或类型不符即报错（一次性列出全部问题）。
SCHEMA: Sequence[Tuple[str, type]] = (
    ("config_version", int),
    ("scoring.weights.failure_signal", int),
    ("scoring.weights.reconstruction", int),
    ("scoring.weights.test_command", int),
    ("scoring.weights.code_change", int),
    ("scoring.threshold.min_score", int),
    ("episode.id_format", str),
    ("episode.max_events_per_session", int),
    ("episode.max_line_bytes", int),
    ("episode.max_result_chars", int),
    ("episode.version_probe_lines", int),
    ("verify.timeout_seconds", int),
    ("verify.tmpdir_prefix", str),
    ("verify.pytest_command", list),
    ("verify.red_expectation", str),
    ("verify.green_expectation", str),
    ("generate.tests_dir", str),
    ("generate.assumed_tests_dir", str),
    ("generate.manifest_path", str),
    ("generate.arrange_placeholder", str),
    ("generate.assert_placeholder", str),
    ("vocabulary.bug_terms", list),
    ("vocabulary.failure_signals", list),
)

#: 必须为正整数的键（≥1）
POSITIVE_KEYS = (
    "episode.max_events_per_session",
    "episode.max_line_bytes",
    "episode.max_result_chars",
    "episode.version_probe_lines",
    "verify.timeout_seconds",
)

#: 必须为非负整数的键（≥0）
NON_NEGATIVE_KEYS = (
    "scoring.weights.failure_signal",
    "scoring.weights.reconstruction",
    "scoring.weights.test_command",
    "scoring.weights.code_change",
    "scoring.threshold.min_score",
)

#: 必须为非空字符串列表的键
NON_EMPTY_STR_LIST_KEYS = ("verify.pytest_command", "vocabulary.bug_terms", "vocabulary.failure_signals")


def package_config_path() -> Path:
    """随包副本路径（安装在 site-packages 里也稳定可定位）。"""
    return Path(__file__).resolve().parent.joinpath(*PACKAGE_RELATIVE)


def repo_config_path(start: Optional[Path] = None) -> Optional[Path]:
    """从 ``start``（默认 CWD）向上找仓库权威配置文件；找不到返回 None。"""
    current = Path(start if start is not None else Path.cwd()).resolve()
    for _ in range(ASCENT_LIMIT + 1):
        candidate = current.joinpath(*REPO_RELATIVE)
        if candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def resolve_config_path(explicit: Optional[str] = None, env: Optional[Dict[str, str]] = None, start: Optional[Path] = None) -> Path:
    """按声明的顺序定位配置文件；全不命中时抛 ``InputUnavailable``（exit 2）。"""
    environ = os.environ if env is None else env
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise InputUnavailable("配置文件不存在: {}".format(path))
        return path
    from_env = environ.get(ENV_VAR)
    if from_env:
        path = Path(from_env)
        if not path.is_file():
            raise InputUnavailable("{} 指向的配置文件不存在: {}".format(ENV_VAR, path))
        return path
    found = repo_config_path(start)
    if found is not None:
        return found
    packaged = package_config_path()
    if packaged.is_file():
        return packaged
    raise InputUnavailable(
        "定位不到 {}".format("/".join(REPO_RELATIVE)),
        hint="显式传 --config，或设置环境变量 {}".format(ENV_VAR),
    )


def load_config(path: Optional[Any] = None, **resolve_kwargs: Any) -> Dict[str, Any]:
    """读取并校验配置；返回嵌套 dict（``config_version`` 已校验）。"""
    config_path = Path(path) if path is not None else resolve_config_path(**resolve_kwargs)
    if not config_path.is_file():
        raise InputUnavailable("配置文件不存在: {}".format(config_path))
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputUnavailable("配置文件不可读（需 UTF-8）: {}: {}".format(config_path, exc))
    config = tomlmini.loads(text, origin=str(config_path))
    validate_config(config, origin=str(config_path))
    config["__path__"] = str(config_path)
    return config


def validate_config(config: Dict[str, Any], origin: str = "<config>") -> None:
    """形状校验：缺失键、类型不符、取值越界、空词表——一次列全。"""
    problems: List[str] = []
    for dotted, expected in SCHEMA:
        found, value = _lookup(config, dotted)
        if not found:
            problems.append("缺键 {}".format(dotted))
            continue
        if expected is int and isinstance(value, bool):
            problems.append("{} 应为 {}，实际 bool".format(dotted, expected.__name__))
            continue
        if not isinstance(value, expected):
            problems.append("{} 应为 {}，实际 {}".format(dotted, expected.__name__, type(value).__name__))
    if problems:
        raise ConfigError("配置形状不合法（{}）: {}".format(origin, "; ".join(problems)))

    for dotted in POSITIVE_KEYS:
        if _lookup(config, dotted)[1] < 1:
            problems.append("{} 必须 ≥ 1".format(dotted))
    for dotted in NON_NEGATIVE_KEYS:
        if _lookup(config, dotted)[1] < 0:
            problems.append("{} 必须 ≥ 0".format(dotted))
    for dotted in NON_EMPTY_STR_LIST_KEYS:
        value = _lookup(config, dotted)[1]
        if not value:
            problems.append("{} 不得为空".format(dotted))
        elif any(not isinstance(item, str) for item in value):
            problems.append("{} 的元素必须是字符串".format(dotted))
    if problems:
        raise ConfigError("配置取值越界（{}）: {}".format(origin, "; ".join(problems)))


def _lookup(config: Dict[str, Any], dotted: str) -> Tuple[bool, Any]:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


# --- 类型化视图：全项目只经这些函数读口径值，别处不得再写第二份常量表 ---


def weights(config: Dict[str, Any]) -> Dict[str, int]:
    return dict(config["scoring"]["weights"])


def min_score(config: Dict[str, Any]) -> int:
    return int(config["scoring"]["threshold"]["min_score"])


def episode_params(config: Dict[str, Any]) -> Dict[str, Any]:
    return dict(config["episode"])


def verify_params(config: Dict[str, Any]) -> Dict[str, Any]:
    return dict(config["verify"])


def generate_params(config: Dict[str, Any]) -> Dict[str, Any]:
    return dict(config["generate"])


def vocabulary(config: Dict[str, Any]) -> Dict[str, List[str]]:
    return {key: list(value) for key, value in config["vocabulary"].items()}


def describe(config: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """扁平化的 (键, 值) 列表，按键排序——供 CLI 展示与报告引用（不新增数值）。"""
    flat: List[Tuple[str, Any]] = []

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key in sorted(node):
                if str(key).startswith("__"):
                    continue
                walk("{}.{}".format(prefix, key) if prefix else str(key), node[key])
        else:
            flat.append((prefix, node))

    walk("", config)
    return sorted(flat)
