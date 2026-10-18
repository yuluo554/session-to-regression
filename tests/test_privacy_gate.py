"""隐私红线（源码层）：纯本地、零上传——不是一句话，而是可被机器核验的断言。

本文件覆盖四层审计里的第 ① 层（源码 import 面）；发布期的完整四层审计
（发布产物扫描 / socket 拦截动态 / 加载面静态）是 M6 的交付物，届时与本文件并存。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Set

from _tools import load_tool

import pytest

#: 判据与"扫描面遍历规则"都只有一份实现：`tools/path_forms.py`。
#: 发布前一把过的门（`scripts/check_release.py`）用的是同一个模块——它要能在没有 pytest 的
#: 干净 clone 上跑，所以判据不能只活在测试里。
_path_forms = load_tool("path_forms")
LOCAL_PATH_PATTERNS = _path_forms.LOCAL_PATH_PATTERNS
_iter_files = _path_forms.iter_scan_files

#: 任何能发起网络调用的顶层模块：出现即红（纯本地定位的一部分）
FORBIDDEN_IMPORTS = frozenset(
    {
        "socket",
        "ssl",
        "http",
        "urllib",
        "urllib2",
        "urllib3",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "requests",
        "httpx",
        "aiohttp",
        "websockets",
        "asyncio",
    }
)

#: 泄露本机形态的字符串形态见 `tools/path_forms.LOCAL_PATH_PATTERNS`（单点实现，此处不复制）。


def test_local_path_pattern_has_a_positive_control():
    """阳性对照：门的判据必须真能命中"像泄漏"的形态——否则它只是个永远绿的装饰。

    样本在**运行时拼装**（源码里不留该字面量，避免扫描器自命中）。
    """
    leaked = "B" + chr(58) + chr(92) + "work" + chr(92) + "demo.txt"
    assert any(pattern.search(leaked) for pattern in LOCAL_PATH_PATTERNS), leaked
    # 阴性对照：转义序列与相对路径不得命中（否则门会淹没在噪声里）
    for benign in ("Output:" + chr(92) + "nSuccess", "tests/regression/test_calc.py", "sess-demo-0001"):
        assert not any(pattern.search(benign) for pattern in LOCAL_PATH_PATTERNS), benign

SCAN_SUFFIXES = (".py", ".toml", ".md", ".yml", ".yaml", ".cfg", ".txt")


def test_local_path_scan_covers_the_repository_text_tree(repo_root):
    """扫描面自证：门要能说出"这一轮扫了几个文件"——否则"没扫到"与"扫干净了"长得一样。"""
    scanned = sum(1 for _ in _iter_files(repo_root))
    assert scanned >= 15, "扫描面过小（{}）".format(scanned)


def _imported_roots(tree: ast.AST) -> Set[str]:
    roots: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
    return roots


def test_no_network_capable_imports_in_source(repo_root):
    offenders: List[str] = []
    scanned = 0
    for path in (repo_root / "src").rglob("*.py"):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = sorted(_imported_roots(tree) & FORBIDDEN_IMPORTS)
        if hits:
            offenders.append("{}: {}".format(path.name, hits))
    assert scanned >= 5, "扫描面过小"
    assert not offenders, "源码出现可发起网络调用的导入: {}".format(offenders)


def test_no_local_absolute_paths_in_repository_text(repo_root):
    """入库文本（含本次新增文档）不得出现本机盘符/家目录形态路径——发布期脱敏的前置门。

    判定走 `tools/path_forms.find_local_path_leaks`（与 `scripts/check_release.py` 同一份实现）。
    """
    offenders, scanned = _path_forms.find_local_path_leaks(repo_root)
    assert scanned >= 15, "扫描面过小（{}）".format(scanned)
    assert not offenders, "发现本机路径形态: {}".format(offenders)


@pytest.mark.parametrize("pattern", ["sessions/", "exports/", "_real_logs/", "data/**/_private/"])
def test_gitignore_blocks_real_logs_and_private_data(repo_root, pattern):
    """隐私红线的第一道闸：真实日志/私有数据目录必须被 .gitignore 挡住。"""
    text = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert pattern in text, "gitignore 缺隐私屏蔽规则: {}".format(pattern)


def test_readme_states_the_zero_upload_guarantee(repo_root):
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "不联网" in readme
    assert "真实日志" in readme and "不入仓" in readme
