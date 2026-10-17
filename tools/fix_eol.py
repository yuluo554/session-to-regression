"""行尾与编码卫生：全仓文本文件统一 LF + 结尾单个换行。

为什么要有工具而不是靠 `.gitattributes` 一条：`.gitattributes` 只在**检出/提交**时生效，
工作树里已有的 CRLF 不会被它清掉——而「CR 守门测试」看的是工作树字节。本机 CRLF、
别处 LF 会让「本机全绿、clone/CI 红」变成常态。所以两半都要：① `.gitattributes` 管住
未来的检出/提交；② 本工具做一次性归一 + `--check` 守门（CI 与测试同源调用）。

用法：

    python tools/fix_eol.py            # 归一（就地重写有问题的文件，打印清单）
    python tools/fix_eol.py --check    # 只检查，有问题 exit 1

二进制扩展名与常见构建/缓存目录一律跳过；冻结快照目录（登记过 sha256 的合成基准）由
`.gitattributes` 标 -text，本工具同样不改其字节（列在 SKIP_DIRS/SKIP_SUFFIX 之外也仍然
只在**有 CR 或结尾换行异常**时才动它，且基准生成器保证输出 LF）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "node_modules",
    }
)

SKIP_SUFFIXES = frozenset(
    {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".zip", ".whl", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".ttf"}
)

#: 目录名**后缀**匹配的构建产物/环境目录。
#: 为什么要按后缀剪枝：`pip install -e .` 会在树里生成 `src/<dist>.egg-info/`（PKG-INFO 是 CRLF、
#: SOURCES.txt 缺结尾换行），CI 每次都装包，于是这条门会稳定误报——**本机干净环境复核时抓到**
#: （首推必红的形态之一）。构建元数据不是我们的源码，不该进门的扫描面。
SKIP_DIR_SUFFIXES = (".egg-info", ".dist-info", ".eggs", ".venv", ".conda", ".pytest_cache", ".mypy_cache", ".ruff_cache")

SKIP_NAMES = frozenset({"Thumbs.db", "Desktop.ini", ".DS_Store"})

#: 修改前先用这些特征判断是不是文本（含 NUL 即视为二进制）
_BINARY_PROBE = b"\x00"
_PROBE_BYTES = 8192


class Problem(NamedTuple):
    path: Path
    detail: str


def is_skipped_dir(part: str) -> bool:
    """目录名是否属于"不入扫描面"的一类（缓存/构建元数据/虚拟环境）。"""
    return part in SKIP_DIRS or part.startswith(".venv") or part.endswith(SKIP_DIR_SUFFIXES)


def is_skipped(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    if path.name.startswith(".venv") or path.name.startswith("venv"):
        return True
    return False


def iter_text_files(root: Path = REPO_ROOT) -> Iterator[Path]:
    """遍历仓库内文本文件（跳过缓存/构建目录与二进制），产出稳定排序的路径。"""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(is_skipped_dir(part) for part in path.relative_to(root).parts[:-1]):
            continue
        if is_skipped(path):
            continue
        try:
            with path.open("rb") as stream:
                head = stream.read(_PROBE_BYTES)
        except OSError:
            continue
        if _BINARY_PROBE in head:
            continue
        yield path


def check_bytes(payload: bytes) -> List[str]:
    """返回该字节串的卫生问题清单（空 = 干净）。"""
    problems: List[str] = []
    if b"\r\n" in payload:
        problems.append("CRLF（{} 处）".format(payload.count(b"\r\n")))
    elif b"\r" in payload:
        problems.append("裸 CR（{} 处）".format(payload.count(b"\r")))
    if payload and not payload.endswith(b"\n"):
        problems.append("缺结尾换行")
    if payload.endswith(b"\n\n"):
        problems.append("结尾多余空行")
    return problems


def normalize(payload: bytes) -> bytes:
    text = payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if text and not text.endswith(b"\n"):
        text += b"\n"
    while text.endswith(b"\n\n"):
        text = text[:-1]
    return text


def scan(root: Path = REPO_ROOT) -> Tuple[List[Problem], int]:
    problems: List[Problem] = []
    checked = 0
    for path in iter_text_files(root):
        try:
            payload = path.read_bytes()
        except OSError as exc:  # pragma: no cover - 权限类异常
            problems.append(Problem(path, "不可读: {}".format(exc)))
            continue
        checked += 1
        for detail in check_bytes(payload):
            problems.append(Problem(path, detail))
    return problems, checked


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ensure_src_on_path()
    from s2r import stdio  # 本脚本会打印，编码守卫照上（tools/ 下每个入口都要）

    stdio.ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="fix_eol", description="文本文件行尾/结尾换行卫生（默认就地归一）")
    parser.add_argument("--check", action="store_true", help="只检查，不写盘；有问题 exit 1")
    parser.add_argument("--root", metavar="DIR", default=str(REPO_ROOT), help="扫描根（默认仓库根）")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    root = Path(args.root)
    _ensure_src_on_path()
    problems, checked = scan(root)
    if not checked:
        sys.stderr.write("check_failed: 扫描面为空（路径或跳过规则写错了？）\n")
        return 1

    if args.check:
        for problem in problems:
            rel = _rel(problem.path, root)
            sys.stdout.write("{}: {}\n".format(rel, problem.detail))
        sys.stdout.write("eol check: {} 个文件，{} 个问题\n".format(checked, len(problems)))
        return 0 if not problems else 1

    fixed = 0
    for problem in problems:
        payload = problem.path.read_bytes()
        normalized = normalize(payload)
        if normalized != payload:
            problem.path.write_bytes(normalized)
            fixed += 1
            sys.stdout.write("fixed {}: {}\n".format(_rel(problem.path, root), problem.detail))
    remaining, _ = scan(root)
    sys.stdout.write("eol fix: {} 个文件，修复 {}，剩余问题 {}\n".format(checked, fixed, len(remaining)))
    return 0 if not remaining else 1


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _ensure_src_on_path() -> None:
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


if __name__ == "__main__":
    sys.exit(main())
