"""本机路径形态的判据与「入库文本」遍历规则（**单点实现**）。

为什么单独成模块，而不是留在某一个消费方里：

* 同一个判据有**两个消费方**——`tests/test_privacy_gate.py`（随全量测试常驻的守门）与
  `scripts/check_release.py`（发布前一把过的门）。后者要能在**未安装、甚至没有 pytest** 的
  干净 clone 上跑，所以不能经"加载测试模块"那条通路（测试模块 import pytest）。
* 判据各写一份必然漂移：一边改了正则、另一边没改，就会出现"测试绿而发布门红"或反过来——
  这类"一半真一半假"正是本项目反复吃过的形态（见 `tools/fix_eol.py` 的"哪些文件算仓库文本"）。

遍历规则同样只有一份：委托给 `tools/fix_eol.py` 的 `iter_text_files`（EOL 门与两个脱敏门
共用同一份"扫描面"定义）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterator, List, Pattern, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 泄露本机形态的字符串形态（脱敏：入库文本不得含本机绝对路径）。
#: 判据用正则而不是字面量：① 只匹配"真实路径形态"（**大写盘符**+反斜杠、家目录+用户名段），
#: 不误伤文档里讨论路径**形式**的叙述；② 正则源码本身不含被匹配的字面串，扫到自己也不会自命中。
#:
#: 为什么盘符限定大写：源码里的 `...:\n` 这类**转义序列**（如 `"Output:\nSuccess"`）会命中
#: `<任意字母>:\` 这种宽判据——本项目首版就这么误报了三处（`t:\`/`e:\` 来自 `\n` 与 `\s`）。
#: 真实日志里的 Windows 路径按惯例是大写盘符，所以收紧到大写既保住检出（阳性对照见
#: `tests/test_privacy_gate.py`），又消掉转义噪声。
LOCAL_PATH_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(r"[A-Z]:\\"),
    re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
)

#: 进入脱敏扫描面的文本扩展名与无扩展名特例（与 `iter_text_files` 的跳过规则叠加）。
SCAN_SUFFIXES = (".py", ".toml", ".md", ".yml", ".yaml", ".cfg", ".txt")
SCAN_NAMES = (".gitignore", ".gitattributes", "LICENSE")


def _fix_eol():
    """按**同目录路径**加载 `fix_eol`，不依赖调用方的 `sys.path`（脚本态与 importlib 态都能用）。"""
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import fix_eol  # noqa: E402  （需要先补同目录）

    return fix_eol


def iter_scan_files(root: Path) -> Iterator[Path]:
    """产出脱敏扫描面里的文本文件（稳定排序；跳过缓存/构建目录与二进制）。"""
    fix_eol = _fix_eol()
    for path in fix_eol.iter_text_files(root):
        if path.suffix.lower() in SCAN_SUFFIXES or path.name in SCAN_NAMES:
            yield path


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def find_local_path_leaks(root: Path) -> Tuple[List[str], int]:
    """扫描 `root` 下全部入库文本，返回 (逐条命中 "相对路径: 命中串", 扫描文件数)。

    扫描文件数一并返回：**"没扫到"与"扫干净了"输出长得一样**，所以调用方必须能把
    "这轮扫了几个文件"打到结论里（门要自己数它扫到了多少）。
    """
    offenders: List[str] = []
    scanned = 0
    for path in iter_scan_files(root):
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in LOCAL_PATH_PATTERNS:
            match = pattern.search(text)
            if match:
                offenders.append("{}: {}".format(rel(path, root), match.group(0)))
    return offenders, scanned
