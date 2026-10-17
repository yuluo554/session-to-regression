"""M4 pytest 骨架生成器：纯模板 + AST，**零模型**（不调模型、不联网、无时钟、无随机）。

输入面是 M3 的还原产物（`SessionReconstruction.files[*]`），输出是一份可人工确认的
`tests/regression/test_<slug>.py` 骨架。四条纪律（每条都会被打挂测试）：

1. **只写测试目录**：生成物落在目标仓库的 `[generate].tests_dir`（或显式 `--out-dir`），
   **绝不改源码、不碰 git 索引**（K11）；
2. **宁缺勿造**：目标符号识别不到 → **不生成文件**，只记 `needs_manual` + 明确原因；
   还原状态不是 `ok` 的文件（片段 `partial` / 无法唯一定位 `unresolved` / 无 before 镜像
   `no_before_image`）**一律不进生成路径**——没有修改前全文就读不出"修改前的签名"，
   硬造出来的只是看起来像测试的东西；
3. **候选不自作主张**：`assert` 只在**能从 diff 推出**时才写成活动断言，且必须带
   `[generate].candidate_note` 那句"需人工确认"；推不出来只留占位 + 证据注释，并记 `needs_manual`；
4. **确定性**：同一个 episode → **逐字节相同**骨架（稳定 slug、稳定排序、无时钟、写盘只经 `serialize`）。

骨架结构固定（段名取自 `[generate].sections`）：

* **docstring**：会话 id / 事件时间戳 / **原始用户报告原文** / 失败信号摘录 / 修复文件清单
  ——五类证据锚点都可在会话日志里回溯（`s2r scan` 能复算）；
* **arrange**：AST 读**修改前**签名 → 逐形参绑最小输入；
* **act**：调用被改动行所属的**最内层符号**（diff 中被改动的函数/类优先）；
* **assert**：占位注释 + 候选断言。

候选断言的推导规则只有一条（`candidate_of`）：改动的新行是 `return <expr> if <cond> else <alt>`、
`<cond>` 恰是被调符号的**形参名**、`<alt>` 是**字面量**——此时把形参绑成与 `<alt>` 同型的假值，
修复前的代码在该输入上抛错（红），修复后返回 `<alt>`（绿），于是 `assert result == <alt>` 是
"能证伪"的候选。推不出来（例如返回值被包了一层调用、条件不是裸形参）就不猜：如实 `needs_manual`，
只给证据注释。
"""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import config as config_mod
from . import episode as episode_mod
from . import events, reconstruct as reconstruct_mod, serialize
from .errors import InputUnavailable, UsageError

#: 生成产物 schema 版本（对外契约；字段集变化即升版）
SCHEMA_VERSION = 1

# ---- 状态（两态显式可见，不折叠）------------------------------------------ #
STATUS_GENERATED = "generated"
STATUS_NEEDS_MANUAL = "needs_manual"
STATUSES = (STATUS_GENERATED, STATUS_NEEDS_MANUAL)

# ---- 原因码（needs_manual 必须带原因，不许"没有理由的降级"）----------------- #
REASON_NONE = ""
REASON_RECONSTRUCTION_NOT_USABLE = "reconstruction_not_usable"
REASON_NOT_PYTHON = "not_python"
REASON_BEFORE_UNPARSABLE = "before_unparsable"
REASON_CHANGED_LINES_UNKNOWN = "changed_lines_unknown"
REASON_SYMBOL_NOT_IDENTIFIED = "symbol_not_identified"
REASON_PROBE_NOT_DERIVABLE = "probe_not_derivable"
REASON_ASSERT_NOT_DERIVABLE = "assert_not_derivable"

#: 首期只生成 Python 测试（其它语言的修复只识别、只进 manifest，不生成测试文件）
PYTHON_SUFFIX = ".py"

#: 惯例探测的四个面（探测到的进 confirmed，探测不到的进 assumed_parts）
CONVENTION_PARTS = ("tests_dir", "pytest_config", "conftest", "package_root")

#: pytest 配置的候选位置（按优先级；只认实测常见形态，认不出来就算探测失败）
PYTEST_CONFIG_MARKERS = (
    ("pyproject.toml", "[tool.pytest.ini_options]"),
    ("pytest.ini", "[pytest]"),
    ("setup.cfg", "[tool:pytest]"),
    ("tox.ini", "[pytest]"),
)

#: fixture 风格的判据（conftest 里有没有 fixture 定义）
FIXTURE_MARKER = "@pytest.fixture"

#: 段标记（`[generate].sections` 的段名 → 生成物里的一行标记）
SECTION_MARKER = "# ------- {} -------"

#: `return <expr> if <cond> else <alt>`（唯一被承认的"可推候选断言"形态）
GUARD_RETURN = re.compile(r"^\s*return\s+(?P<expr>.+?)\s+if\s+(?P<cond>.+?)\s+else\s+(?P<alt>.+?)\s*$")

#: act 段里结果绑定的变量名（生成物内部用，不是口径值）
RESULT_NAME = "result"


# --------------------------------------------------------------------------- #
# 惯例探测
# --------------------------------------------------------------------------- #


@dataclass
class Convention:
    """目标仓库的测试约定（探测不到的面进 `assumed_parts`，生成物里显式标注）。"""

    root: str = ""
    tests_dir: str = ""
    pytest_config: str = "none"
    conftest: str = ""
    fixture_style: str = "none"
    package_root: str = ""
    assumed_parts: List[str] = field(default_factory=list)

    @property
    def assumed(self) -> bool:
        return bool(self.assumed_parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "tests_dir": self.tests_dir,
            "pytest_config": self.pytest_config,
            "conftest": self.conftest,
            "fixture_style": self.fixture_style,
            "package_root": self.package_root,
            "assumed": self.assumed,
            "assumed_parts": list(self.assumed_parts),
        }


def probe_conventions(target: Optional[str], cfg: Mapping[str, Any]) -> Convention:
    """探测目标仓库的测试约定；**探测失败不报错**，回落到口径文件里的保守默认并记明未确认的面。

    为什么探测失败不是错误：生成骨架这件事在"仓库里还没有 tests/ 目录"时照样有意义——
    但那时的落点与 import 形态都是**猜的**，所以必须显式标出来（`assumed_convention`）。
    """
    params = config_mod.generate_params(cfg)
    convention = Convention(tests_dir=str(params["assumed_tests_dir"]))
    if not target:
        convention.assumed_parts = list(CONVENTION_PARTS)
        return convention

    root = Path(target)
    if not _is_dir(root):
        raise InputUnavailable("目标仓库不存在或不是目录: {}".format(target))
    convention.root = str(root)

    confirmed: List[str] = []
    tests_dir = str(params["tests_dir"])
    if _is_dir(root / "tests") or _is_dir(root.joinpath(*tests_dir.split("/"))):
        confirmed.append("tests_dir")
        convention.tests_dir = tests_dir
    else:
        convention.tests_dir = str(params["assumed_tests_dir"])

    for name, marker in PYTEST_CONFIG_MARKERS:
        path = root / name
        if _is_file(path) and marker in serialize.read_text(path):
            convention.pytest_config = "{}[{}]".format(name, marker.strip("[]"))
            confirmed.append("pytest_config")
            break

    for candidate in (root / "tests" / "conftest.py", root / "conftest.py"):
        if _is_file(candidate):
            convention.conftest = _relative_text(candidate, root)
            convention.fixture_style = "fixture" if FIXTURE_MARKER in serialize.read_text(candidate) else "plain"
            confirmed.append("conftest")
            break

    if _is_dir(root / "src"):
        convention.package_root = "src"
        confirmed.append("package_root")

    convention.assumed_parts = [part for part in CONVENTION_PARTS if part not in confirmed]
    return convention


def _relative_text(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


# --------------------------------------------------------------------------- #
# AST：读修改前的签名
# --------------------------------------------------------------------------- #


@dataclass
class Symbol:
    """一个定义（模块级函数 / 类 / 类内方法）。"""

    qualified: str
    node: ast.AST
    kind: str
    owner: Optional[ast.ClassDef]


@dataclass
class Target:
    """被改动行所属的目标符号（供 arrange / act 用）。"""

    qualified: str
    top: str
    kind: str
    receiver: str
    bind_as_method: bool
    has_receiver: bool
    receiver_args: List[str]
    args: List[str]
    keyword_args: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "qualified": self.qualified,
            "kind": self.kind,
            "top": self.top,
            "args": list(self.args),
            "keyword_args": list(self.keyword_args),
            "bind_as_method": self.bind_as_method,
        }


def parse_before(source: Optional[str]) -> Optional[ast.AST]:
    """解析**修改前**源码；读不懂返回 None（调用方如实落 `before_unparsable`）。"""
    if not isinstance(source, str) or not source.strip():
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def symbols_of(tree: ast.AST) -> List[Symbol]:
    """收集可作目标的定义：模块级函数、类、类内方法（不递归进函数体——嵌套函数由外层代表）。"""
    out: List[Symbol] = []

    def walk(body: Sequence[ast.AST], prefix: Tuple[str, ...], owner: Optional[ast.ClassDef]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(Symbol(".".join(prefix + (node.name,)), node, "function", owner))
            elif isinstance(node, ast.ClassDef):
                out.append(Symbol(".".join(prefix + (node.name,)), node, "class", None))
                walk(node.body, prefix + (node.name,), node)

    walk(getattr(tree, "body", []), (), None)
    return out


def changed_lines(source: str, hunks: Sequence[Mapping[str, Any]]) -> Optional[List[int]]:
    """hunk 旧侧文本在修改前全文里的**行号集合**（1-based）；定位不唯一/找不到 → None。

    为什么不一味信 `hunk["old_start"]`：实测两种通道给的 `old_lines` 形态不同（结构化补丁给整行、
    `old_string` 通道给不带缩进的片段），行号也可能为 null。统一判据 = "旧侧文本在 before 里
    **唯一**出现"——两种通道都适用，不唯一时如实拒绝（不任选）。
    """
    numbers: List[int] = []
    for hunk in hunks:
        block = "\n".join(str(item) for item in (hunk.get("old_lines") or []))
        if not block:
            continue
        start = source.find(block)
        if start < 0 or source.find(block, start + 1) >= 0:
            return None
        first = source.count("\n", 0, start) + 1
        numbers.extend(range(first, first + block.count("\n") + 1))
    return sorted(set(numbers)) or None


def pick_symbol(tree: ast.AST, lines: Sequence[int]) -> Optional[Symbol]:
    """被改动行所属的**最内层**定义（跨度最小者；同跨度按限定名排序，保证确定性）。"""
    wanted = sorted({int(item) for item in lines})
    if not wanted:
        return None
    covering = [symbol for symbol in symbols_of(tree) if _covers(symbol.node, wanted)]
    if not covering:
        return None
    covering.sort(key=lambda item: (_span(item.node), item.qualified))
    return covering[0]


def _covers(node: ast.AST, wanted: Sequence[int]) -> bool:
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    return any(start <= line <= end for line in wanted)


def _span(node: ast.AST) -> int:
    start = getattr(node, "lineno", 0) or 0
    end = getattr(node, "end_lineno", start) or start
    return int(end) - int(start)


def target_of(symbol: Symbol) -> Optional[Target]:
    """符号 → arrange/act 需要的形态；`None` = 签名读不出最小调用（arrange 构造不出）。"""
    parts = symbol.qualified.split(".")
    top = parts[0]
    if symbol.kind == "class":
        args, keyword_args, vararg = _signature_params(symbol.node, "class")
        receiver, has_receiver, bind_as_method, receiver_args = "", False, False, []
    elif len(parts) > 1:  # 方法：需要一个接收者（实例或类）
        decorators = _decorator_names(symbol.node)
        args, keyword_args, vararg = _signature_params(symbol.node, "function", is_method=True)
        receiver = ".".join(parts[:-1])
        receiver_args = []
        bind_as_method = False
        has_receiver = True
        if "staticmethod" in decorators:
            args, keyword_args, vararg = _signature_params(symbol.node, "function", is_method=False)
            bind_as_method = False
        elif "classmethod" in decorators:
            bind_as_method = False
        else:
            bind_as_method = True
            owner_init = _init_of(symbol.owner)
            if owner_init is not None:
                receiver_args, _kw, receiver_vararg = _signature_params(owner_init, "function", is_method=True)
                if receiver_vararg:
                    return None
    else:
        args, keyword_args, vararg = _signature_params(symbol.node, "function")
        receiver, has_receiver, bind_as_method, receiver_args = "", False, False, []
    if vararg:
        return None
    return Target(
        qualified=symbol.qualified,
        top=top,
        kind=symbol.kind,
        receiver=receiver,
        bind_as_method=bind_as_method,
        has_receiver=has_receiver,
        receiver_args=receiver_args,
        args=args,
        keyword_args=keyword_args,
    )


def _init_of(owner: Optional[ast.ClassDef]) -> Optional[ast.AST]:
    if owner is None:
        return None
    for node in owner.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
            return node
    return None


def _decorator_names(node: ast.AST) -> List[str]:
    names: List[str] = []
    for decorator in getattr(node, "decorator_list", []) or []:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def _signature_params(node: Optional[ast.AST], kind: str, is_method: bool = False) -> Tuple[List[str], List[str], bool]:
    """→ (必需位置形参名, 必需关键字形参名, 是否有可变参数)。

    类目标用 `__init__` 的签名（没有 `__init__` 就是零参数可调用，仍可构造）。
    方法目标丢掉首个形参（`self`/`cls` 由接收者提供）。
    """
    if node is None:
        return [], [], False
    if kind == "class":
        node = _init_of(node)
        if node is None:
            return [], [], False
        is_method = True
    args_node = getattr(node, "args", None)
    if args_node is None:
        return [], [], False
    vararg = args_node.vararg is not None or args_node.kwarg is not None
    positional = list(getattr(args_node, "posonlyargs", [])) + list(args_node.args)
    defaults = len(args_node.defaults)
    required = positional[: len(positional) - defaults] if defaults else positional
    names = [item.arg for item in required]
    if is_method and names:
        names = names[1:]
    keyword_only = [item.arg for item, default in zip(args_node.kwonlyargs, args_node.kw_defaults) if default is None]
    return names, keyword_only, vararg


# --------------------------------------------------------------------------- #
# 候选断言：从 diff 推
# --------------------------------------------------------------------------- #


@dataclass
class Candidate:
    probe: str
    assert_line: str
    evidence: str


def candidate_of(hunks: Sequence[Mapping[str, Any]], target: Target, cfg: Mapping[str, Any]) -> Optional[Candidate]:
    """从 diff 推候选断言；推不出 → None（调用方落 `needs_manual` + `assert_not_derivable`）。"""
    fallback_probe = str(config_mod.generate_params(cfg)["arrange_probe"])
    bindable = set(target.args) | set(target.keyword_args)
    for hunk in hunks:
        old_side = [str(item) for item in (hunk.get("old_lines") or [])]
        new_side = [str(item) for item in (hunk.get("new_lines") or [])]
        added = _added_lines(old_side, new_side)
        removed = _removed_lines(old_side, new_side)
        for index, line in enumerate(added):
            match = GUARD_RETURN.match(line)
            if match is None:
                continue
            condition = _parse_expr(match.group("cond"))
            if not isinstance(condition, ast.Name) or condition.id not in bindable:
                continue
            alternative = match.group("alt").strip()
            node = _parse_expr(alternative)
            if node is None or not _is_literal(node):
                continue
            probe = alternative if _is_falsy_literal(node) else fallback_probe
            # 候选断言成立的前提是"探测值能把守卫弄假"：推不出假值就不推候选（宁缺勿造）
            probe_node = _parse_expr(probe)
            if probe_node is None or not _is_falsy_literal(probe_node):
                continue
            if alternative == "None":
                assert_line = "assert {} is None".format(RESULT_NAME)
            else:
                assert_line = "assert {} == {}".format(RESULT_NAME, alternative)
            before_text = (removed[index] if index < len(removed) else (removed[0] if removed else "")).strip()
            return Candidate(
                probe=probe,
                assert_line=assert_line,
                evidence="修改前 `{}` / 修改后 `{}`".format(before_text, line.strip()),
            )
    return None


def _added_lines(old_side: Sequence[str], new_side: Sequence[str]) -> List[str]:
    """真被加上去的行（用 difflib 的 replace/insert 段——上下文行不算"改动"）。"""
    matcher = difflib.SequenceMatcher(None, list(old_side), list(new_side), autojunk=False)
    added: List[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            added.extend(list(new_side)[j1:j2])
    return added


def _removed_lines(old_side: Sequence[str], new_side: Sequence[str]) -> List[str]:
    matcher = difflib.SequenceMatcher(None, list(old_side), list(new_side), autojunk=False)
    removed: List[str] = []
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(list(old_side)[i1:i2])
    return removed


def _parse_expr(text: str) -> Optional[ast.AST]:
    try:
        return ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return None


#: "不是字面量"哨兵（`None` 本身是合法字面量，不能拿它当哨兵）
NOT_LITERAL = object()


def _quick_value(node: ast.AST) -> Any:
    """能静态判定的字面量值；不是字面量 → `NOT_LITERAL`。

    带符号的数字（`-1`）在 AST 里是 `UnaryOp` 而不是 `Constant`——实测 `else -1` 这种写法
    就是它，所以必须一并认（否则整条降级路径会被一个符号吃掉）。
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = node.operand
        if isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float)) and not isinstance(operand.value, bool):
            return -operand.value if isinstance(node.op, ast.USub) else operand.value
        return NOT_LITERAL
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None if not node.elts else NOT_LITERAL
    if isinstance(node, ast.Dict):
        return None if not node.keys else NOT_LITERAL
    return NOT_LITERAL


def _is_literal(node: ast.AST) -> bool:
    return _quick_value(node) is not NOT_LITERAL


def _is_falsy_literal(node: ast.AST) -> bool:
    value = _quick_value(node)
    return value is not NOT_LITERAL and not value


# --------------------------------------------------------------------------- #
# 路径 / 模块名 / slug
# --------------------------------------------------------------------------- #


def module_of(file_path: str, convention: Convention) -> str:
    """改动文件 → 点分模块名（有仓库根就按相对路径取，没有就按日志路径形态取）。"""
    relative = file_path
    if convention.root:
        candidate = relative_under_root(file_path, Path(convention.root))
        if candidate is not None:
            relative = candidate
    relative = relative.replace("\\", "/").lstrip("/")
    if convention.package_root and relative.startswith(convention.package_root + "/"):
        relative = relative[len(convention.package_root) + 1 :]
    body = relative[: -len(PYTHON_SUFFIX)] if relative.endswith(PYTHON_SUFFIX) else relative
    parts = [part for part in body.split("/") if part and part != "."]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_python(file_path: str) -> bool:
    return file_path.lower().endswith(PYTHON_SUFFIX)


def relative_under_root(file_path: str, root: Path) -> Optional[str]:
    """日志里的文件路径 → 目标仓库内的**相对路径**（仓库里真的存在该文件时才认）。

    为什么必须"真的存在"：日志里的路径可能是另一个项目、也可能已经改名——把内容硬写到
    一个猜出来的相对路径上，等于往用户仓库里**新建**文件（K11 红线）。所以这里只做映射，
    映射不出来就如实返回 None，由调用方落"环境不可用"一类的原因。

    M5 的隔离副本写盘（verify）与 M4 的模块名推导（module_of）共用**这一份**实现。
    """
    candidates = [] if Path(file_path).is_absolute() else [file_path]
    candidates.append(file_path.replace("\\", "/").lstrip("/"))
    for candidate in candidates:
        if _is_file(root.joinpath(*candidate.split("/"))):
            return candidate
    return None


def slugify(text: str) -> str:
    """稳定 slug：非 `[0-9a-z_]` 一律折叠成下划线（文件名与测试函数名共用）。"""
    chars = []
    for char in str(text).lower():
        chars.append(char if (char.isascii() and (char.isalnum() or char == "_")) else "_")
    slug = "".join(chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


# --------------------------------------------------------------------------- #
# 骨架
# --------------------------------------------------------------------------- #


@dataclass
class Skeleton:
    """一个 episode 的生成结论（写盘的内容与"为什么没写"都在这）。"""

    episode_id: str
    slug: str
    relative_path: str
    status: str
    reason: str
    assumed_convention: bool
    assumed_parts: List[str]
    symbols: List[str] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)
    anchors: Dict[str, Any] = field(default_factory=dict)
    sections: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    content: str = ""

    @property
    def written(self) -> bool:
        return bool(self.content)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "slug": self.slug,
            "relative_path": self.relative_path,
            "status": self.status,
            "reason": self.reason,
            "assumed_convention": self.assumed_convention,
            "assumed_parts": list(self.assumed_parts),
            "symbols": list(self.symbols),
            "candidates": list(self.candidates),
            "anchors": dict(self.anchors),
            "sections": list(self.sections),
            "files": list(self.files),
            "written": self.written,
            "content": self.content,
        }


def plan_episode(
    episode: Any,
    files: Sequence[reconstruct_mod.Reconstruction],
    file_read: Any,
    cfg: Mapping[str, Any],
    convention: Convention,
) -> Skeleton:
    """一个 episode → 一个骨架结论（**纯函数，不写盘**）。"""
    params = config_mod.generate_params(cfg)
    episode_id = str(getattr(episode, "episode_id", events.UNKNOWN))
    slug = slugify(episode_id) or "episode"
    skeleton = Skeleton(
        episode_id=episode_id,
        slug=slug,
        relative_path="{}/test_{}.py".format(str(params["tests_dir"]).rstrip("/"), slug),
        status=STATUS_NEEDS_MANUAL,
        reason=REASON_NONE,
        assumed_convention=convention.assumed,
        assumed_parts=list(convention.assumed_parts),
        files=[str(item.file) for item in files],
    )

    usable = [item for item in files if item.status == reconstruct_mod.STATUS_OK and isinstance(item.before, str)]
    if not usable:
        skeleton.reason = _unusable_reason(files)
        return skeleton

    # 首期只生成 Python 测试：非 .py 的改动（实测真实会话里是 .md / .css 一类）只识别、不生成
    python_files = [item for item in usable if _is_python(str(item.file))]
    if not python_files:
        skeleton.reason = REASON_NOT_PYTHON
        return skeleton

    primary = sorted(python_files, key=lambda item: (str(item.file), int(item.seq or 0)))[0]
    tree = parse_before(primary.before)
    if tree is None:
        skeleton.reason = REASON_BEFORE_UNPARSABLE
        return skeleton

    lines = changed_lines(primary.before or "", primary.hunks)
    if not lines:
        skeleton.reason = REASON_CHANGED_LINES_UNKNOWN
        return skeleton
    symbol = pick_symbol(tree, lines)
    if symbol is None:
        skeleton.reason = REASON_SYMBOL_NOT_IDENTIFIED
        return skeleton

    target = target_of(symbol)
    skeleton.symbols = [symbol.qualified if target is None else target.qualified]
    candidate = candidate_of(primary.hunks, target, cfg) if target is not None else None
    if target is None:
        skeleton.reason = REASON_PROBE_NOT_DERIVABLE
    elif candidate is None:
        skeleton.reason = REASON_ASSERT_NOT_DERIVABLE
    else:
        skeleton.status = STATUS_GENERATED
        skeleton.candidates = [candidate.assert_line]
    skeleton.content = _render(skeleton, episode, file_read, convention, primary, target, candidate, cfg, files)
    return skeleton


def _unusable_reason(files: Sequence[reconstruct_mod.Reconstruction]) -> str:
    statuses = sorted({str(item.status) for item in files})
    return "{}:{}".format(REASON_RECONSTRUCTION_NOT_USABLE, ",".join(statuses) if statuses else events.UNKNOWN)


def _render(
    skeleton: Skeleton,
    episode: Any,
    file_read: Any,
    convention: Convention,
    primary: reconstruct_mod.Reconstruction,
    target: Optional[Target],
    candidate: Optional[Candidate],
    cfg: Mapping[str, Any],
    related: Sequence[reconstruct_mod.Reconstruction] = (),
) -> str:
    """按固定模板拼出骨架源码（docstring + 三段）；同一输入 → 逐字节相同。"""
    params = config_mod.generate_params(cfg)
    sections = [str(name) for name in params["sections"]]
    markers = ["    " + SECTION_MARKER.format(name) for name in sections]
    while len(markers) < 3:
        markers.append("    " + SECTION_MARKER.format(""))
    module = module_of(str(primary.file), convention)
    if module and skeleton.symbols:
        import_line = "from {} import {}".format(module, skeleton.symbols[0].split(".")[0])
    else:
        import_line = "# TODO: import（模块名推不出来，请人工补 import）"
    probe = candidate.probe if candidate is not None else str(params["arrange_probe"])
    bindings = _bindings(target, probe)
    arrange_lines, act_lines, assert_lines = _body_lines(target, bindings, candidate, primary, params)

    header = _docstring_block(skeleton, episode, file_read, convention, cfg, files=list(related or skeleton.files))
    body = [
        "def test_{}():".format(skeleton.slug),
        '    """{} 的回归骨架（episode {}）。"""'.format(skeleton.symbols[0] if skeleton.symbols else "目标符号", skeleton.episode_id),
        "",
        markers[0],
    ]
    body.extend(arrange_lines)
    body.append(markers[1])
    body.extend(act_lines)
    body.append(markers[2])
    body.extend(assert_lines)
    skeleton.sections = list(sections)
    return "\n".join(header + ["", import_line, "", ""] + body) + "\n"


def _bindings(target: Optional[Target], probe: str) -> List[Tuple[str, str, bool]]:
    if target is None:
        return []
    bindings = [(name, probe, False) for name in target.args]
    bindings.extend((name, probe, True) for name in target.keyword_args)
    return bindings


def _call_text(target: Target, bindings: Sequence[Tuple[str, str, bool]]) -> str:
    arguments = [name for name, _value, keyword in bindings if not keyword]
    arguments.extend("{}={}".format(name, value) for name, value, keyword in bindings if keyword)
    if target.bind_as_method:
        callee = "instance.{}".format(target.qualified.split(".")[-1])
    else:
        callee = target.qualified
    return "{}({})".format(callee, ", ".join(arguments))


def _body_lines(
    target: Optional[Target],
    bindings: Sequence[Tuple[str, str, bool]],
    candidate: Optional[Candidate],
    primary: reconstruct_mod.Reconstruction,
    params: Mapping[str, Any],
) -> Tuple[List[str], List[str], List[str]]:
    if target is None:
        arrange = [
            "    " + str(params["arrange_placeholder"]),
            "    # 修改前的签名读不出最小调用（含可变参数或形态不认），不猜参数——请人工补 arrange",
        ]
        act = ["    " + str(params["act_placeholder"]), "    # 调用写不出来，理由同上"]
        assert_lines = ["    " + str(params["assert_placeholder"]), "    # {}：{}".format(_candidate_note(params), _changed_evidence(primary))]
        return arrange, act, assert_lines

    arrange: List[str] = []
    if target.bind_as_method:
        receiver_bindings = [(name, str(params["arrange_probe"]), False) for name in target.receiver_args]
        for name, value, _keyword in receiver_bindings:
            arrange.append("    {} = {}".format(name, value))
        arrange.append("    instance = {}({})".format(target.receiver, ", ".join(name for name, _v, _k in receiver_bindings)))
    for name, value, _keyword in bindings:
        arrange.append("    {} = {}".format(name, value))
    if not bindings and not target.bind_as_method:
        arrange.append("    # 修改前的签名没有必需形参，直接调用")
    act = ["    {} = {}".format(RESULT_NAME, _call_text(target, bindings))]
    assert_lines = ["    " + str(params["assert_placeholder"])]
    if candidate is not None:
        assert_lines.append("    # {}：{}".format(_candidate_note(params), candidate.evidence))
        assert_lines.append("    " + candidate.assert_line)
    else:
        assert_lines.append("    # {}：{}".format(_candidate_note(params), _changed_evidence(primary)))
        assert_lines.append("    # 未能从 diff 推出可自动断言的形态，请人工补断言（本骨架只做证据锚点）")
    return arrange, act, assert_lines


def _candidate_note(params: Mapping[str, Any]) -> str:
    return str(params["candidate_note"])


def _changed_evidence(primary: reconstruct_mod.Reconstruction) -> str:
    pieces = []
    for hunk in primary.hunks:
        old_side = " / ".join(str(item).strip() for item in (hunk.get("old_lines") or []))
        new_side = " / ".join(str(item).strip() for item in (hunk.get("new_lines") or []))
        pieces.append("修改前 `{}` / 修改后 `{}`".format(old_side, new_side))
    return "；".join(pieces)


def _docstring_block(
    skeleton: Skeleton,
    episode: Any,
    file_read: Any,
    convention: Convention,
    cfg: Mapping[str, Any],
    files: Sequence[Any],
) -> List[str]:
    """证据锚点块：会话 id / 时间戳 / 报告原文 / 失败信号摘录 / 修复文件清单。"""
    params = config_mod.generate_params(cfg)
    report = str(getattr(episode, "user_report", "") or "")
    excerpt = failure_excerpt(episode, file_read, cfg)
    lines = [
        "回归测试骨架：{}".format(skeleton.episode_id),
        "",
        "会话: {}（{}）".format(getattr(episode, "session_id", events.UNKNOWN), getattr(episode, "source", events.UNKNOWN)),
        "事件时间戳: {} ~ {}".format(getattr(episode, "start_ts", events.UNKNOWN), getattr(episode, "end_ts", events.UNKNOWN)),
        "用户报告原文: {}".format(report),
        "失败信号摘录: {}".format(excerpt),
        "修复文件清单:",
    ]
    listed = skeleton_reconstructions(files)
    for line in listed or skeleton.files:
        lines.append("  * {}".format(line))
    lines.append("")
    lines.append("证据锚点全部取自会话日志（可用 `s2r scan` 复算）；本骨架由 `s2r generate` 用模板 + AST 生成，零模型。")
    lines.append("{}：先确认它在修复前失败（红），再确认修复后通过（绿）。".format(_candidate_note(params)))
    if convention.assumed:
        lines.append("惯例探测: 未确认 {}，按保守默认落盘（把 --repo 指向目标仓库可解除）。".format("、".join(convention.assumed_parts)))
    skeleton.anchors = {
        "session_id": str(getattr(episode, "session_id", events.UNKNOWN)),
        "source": str(getattr(episode, "source", events.UNKNOWN)),
        "start_ts": str(getattr(episode, "start_ts", events.UNKNOWN)),
        "end_ts": str(getattr(episode, "end_ts", events.UNKNOWN)),
        "user_report": report,
        "failure_excerpt": excerpt,
        "files": list(skeleton.files),
    }
    return _docstring(lines)


def skeleton_reconstructions(files: Sequence[Any]) -> List[str]:
    """docstring 的修复文件清单：带还原状态与置信度（证据可回溯到"凭什么"）。"""
    out: List[str] = []
    for item in files:
        if isinstance(item, reconstruct_mod.Reconstruction):
            out.append("{}（{} / {} / {}）".format(item.file, item.status, item.source, item.confidence))
        elif isinstance(item, Mapping):
            out.append("{}（{}）".format(item.get("file"), item.get("status") or events.UNKNOWN))
        else:
            out.append(str(item))
    return out


def failure_excerpt(episode: Any, file_read: Any, cfg: Mapping[str, Any]) -> str:
    """失败信号摘录：该 episode 第一条带失败信号的 `tool_result` 文本里**那一行**。"""
    signals = list(config_mod.vocabulary(cfg)["failure_signals"])
    wanted = sorted({int(item.get("seq", 0)) for item in getattr(episode, "failure_evidence", []) or []})
    by_seq = {int(event.seq): event for event in getattr(file_read, "events", [])}
    for seq in wanted:
        event = by_seq.get(seq)
        payload = event.payload if event is not None else None
        text = payload.get("text") if isinstance(payload, Mapping) else None
        if not isinstance(text, str) or not text:
            continue
        chosen = _first_signal_line(text, signals)
        if chosen:
            return chosen
    for seq in wanted:
        event = by_seq.get(seq)
        payload = event.payload if event is not None else None
        text = payload.get("text") if isinstance(payload, Mapping) else None
        if isinstance(text, str) and text.strip():
            return text.strip().split("\n")[0].strip()
    return events.UNKNOWN


def _first_signal_line(text: str, signals: Sequence[str]) -> str:
    for line in text.split("\n"):
        body = line.strip()
        if body and any(signal in body for signal in signals):
            return body
    return ""


# --------------------------------------------------------------------------- #
# docstring 渲染（原文逐字出现，别被转义层吃掉）
# --------------------------------------------------------------------------- #


def _docstring(lines: Sequence[str]) -> List[str]:
    """把行集渲染成模块 docstring；含引号/反斜杠时挑不冲突的引号形态，实在冲突就退成注释。

    "原文逐字出现"是证据锚点的一部分，所以**不转义**：转义会改字节，锚点就查不到了。
    """
    body = "\n".join(lines)
    prefixes = []
    if "\\" not in body:
        prefixes = [""]
    elif not body.endswith("\\"):
        prefixes = ["r"]
    for prefix in prefixes:
        for quote in ('"""', "'''"):
            if quote not in body:
                return [prefix + quote] + list(lines) + [quote]
    return ["'''原文含引号或结尾反斜杠，直接放进 docstring 会破坏语法；原文见下方注释'''"] + [
        "# {}".format(line) for line in lines
    ]


# --------------------------------------------------------------------------- #
# 会话级：规划 / 写盘 / 事实
# --------------------------------------------------------------------------- #


@dataclass
class GenerationRun:
    source: str
    session_id: str
    source_file: str
    episode_id: str
    convention: Convention
    skeletons: List[Skeleton] = field(default_factory=list)
    written: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        return {
            STATUS_GENERATED: len([item for item in self.skeletons if item.status == STATUS_GENERATED]),
            STATUS_NEEDS_MANUAL: len([item for item in self.skeletons if item.status == STATUS_NEEDS_MANUAL]),
            "written": len([item for item in self.skeletons if item.written]),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "session_id": self.session_id,
            "source_file": self.source_file,
            "episode_id": self.episode_id,
            "convention": self.convention.to_dict(),
            "skeletons": [item.to_dict() for item in self.skeletons],
            "written": list(self.written),
            "counts": self.counts(),
        }

    def to_json(self) -> str:
        return serialize.dumps_json(self.to_dict())


def split_episodes(file_read: Any, cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """会话 → episode 实体（`episode_id → Episode`）。

    单独成函数是为了让**生成层与验证层走同一条切分路径**：`episode_id` 的形状、起点序号、
    `end_ts` 这些字段一旦两边各切一次就会出现"生成看的是 A、验证跑的是 B"的错位。
    """
    session_id = _session_id_of(file_read)
    split = episode_mod.split(file_read.events, cfg, session_files={session_id: str(getattr(file_read, "path", events.UNKNOWN))})
    return {item.episode_id: item for item in split.episodes}


def related_reconstructions(
    file_read: Any,
    cfg: Mapping[str, Any],
    repo: Optional[str] = None,
) -> Tuple[List[str], Dict[str, List[reconstruct_mod.Reconstruction]]]:
    """会话 → `(episode 顺序, episode_id → 该 episode 相关的还原结果)`。

    M4 生成与 M5 验证共用这一份：验证要按 episode 把 `before`/`after` 写回副本，
    如果两边对"这个 episode 由哪些文件构成"的理解不一致，RED/GREEN 就不再是同一个测试的结论。
    还原是整条链上最贵的一步，所以顺序与归组一次算完（别为排序再跑一遍还原）。
    """
    reconstruction = reconstruct_mod.reconstruct_session(file_read, cfg, repo=repo)
    by_file: Dict[str, List[reconstruct_mod.Reconstruction]] = {}
    for item in reconstruction.files:
        by_file.setdefault(str(item.file), []).append(item)
    order: List[str] = []
    by_episode: Dict[str, List[reconstruct_mod.Reconstruction]] = {}
    for verdict in reconstruction.episodes:
        episode_id = str(verdict.get("episode_id", events.UNKNOWN))
        order.append(episode_id)
        related: List[reconstruct_mod.Reconstruction] = []
        for item in verdict.get("files") or []:
            related.extend(by_file.get(str(item.get("file")), []))
        by_episode[episode_id] = related
    return order, by_episode


def plan_session(
    file_read: Any,
    cfg: Mapping[str, Any],
    repo: Optional[str] = None,
    convention: Optional[Convention] = None,
) -> List[Skeleton]:
    """会话 → 逐 episode 的骨架结论（**不写盘**；基准对账与 CLI 共用同一条路径）。"""
    return [skeleton for _episode, skeleton, _files in plan_session_detailed(file_read, cfg, repo=repo, convention=convention)]


def plan_session_detailed(
    file_read: Any,
    cfg: Mapping[str, Any],
    repo: Optional[str] = None,
    convention: Optional[Convention] = None,
) -> List[Tuple[Any, Skeleton, List[reconstruct_mod.Reconstruction]]]:
    """同 `plan_session`，但**同时交出** episode 实体与它相关的还原结果。

    M5 的验证需要这三样对齐（谁的测试、哪些文件的 before/after）——如果验证侧再算一遍
    episode 或还原，就会出现"生成看的是 A、验证跑的是 B"的错位。还原是整条链上最贵的一步，
    所以只在这里算一次，由两个调用面共享。
    """
    probe = convention if convention is not None else probe_conventions(repo, cfg)
    entities = split_episodes(file_read, cfg)
    order, related_by_episode = related_reconstructions(file_read, cfg, repo=repo)

    out: List[Tuple[Any, Skeleton, List[reconstruct_mod.Reconstruction]]] = []
    for episode_id in order:
        entity = entities.get(episode_id)
        if entity is None:
            continue
        files = related_by_episode.get(episode_id, [])
        out.append((entity, plan_episode(entity, files, file_read, cfg, probe), files))
    return out


def _session_id_of(file_read: Any) -> str:
    for event in getattr(file_read, "events", []):
        if event.session_id != events.UNKNOWN:
            return str(event.session_id)
    return events.UNKNOWN


def generate_episode(
    file_read: Any,
    cfg: Mapping[str, Any],
    fix_seq: str,
    repo: Optional[str] = None,
    out_root: Optional[str] = None,
    write: bool = True,
) -> GenerationRun:
    """`s2r generate` 的主体：只生成**指定序号**那一个 episode 的骨架（可选落盘）。

    为什么必须先规划全部再筛：`episode_id` 的形状由 `[episode].id_format` 定义，
    序号是"首次修复事件的源行号"——只有把会话切完才知道这个序号指向哪个 episode；
    找不到时列出可用序号（把"没这个 episode"和"没切出 episode"分开说）。
    """
    session_id = _session_id_of(file_read)
    convention = probe_conventions(repo, cfg)
    skeletons = plan_session(file_read, cfg, repo=repo, convention=convention)
    wanted = [item for item in skeletons if item.episode_id.endswith("#" + str(fix_seq))]
    if not wanted:
        raise InputUnavailable(
            "会话 {} 里没有 episode {}".format(session_id, "#" + str(fix_seq)),
            hint="可用: {}".format(", ".join(sorted(item.episode_id for item in skeletons)) or "（该会话没有切出任何 episode）"),
        )
    run = GenerationRun(
        source=str(getattr(file_read, "source", events.UNKNOWN)),
        session_id=session_id,
        source_file=str(getattr(file_read, "path", events.UNKNOWN)),
        episode_id=wanted[0].episode_id,
        convention=convention,
        skeletons=wanted,
    )
    if write:
        run.written = write_skeletons(out_root, wanted, cfg)
    return run


def write_skeletons(out_root: Optional[str], skeletons: Sequence[Skeleton], cfg: Mapping[str, Any]) -> List[str]:
    """写盘：**只写测试目录**（`<target>/<tests_dir>/test_<slug>.py`），源码树一个字节都不动（K11）。"""
    if not out_root:
        raise UsageError(
            "缺少落点：给 --repo（目标仓库根）或 --out-dir（隔离输出根）",
            hint="生成物只落测试目录，所以必须先知道目标在哪",
        )
    root = Path(out_root)
    if not _is_dir(root):
        raise InputUnavailable("输出根不存在或不是目录: {}".format(out_root))
    written: List[str] = []
    for skeleton in skeletons:
        if not skeleton.content:
            continue
        target = root.joinpath(*skeleton.relative_path.split("/"))
        serialize.write_text_atomic(target, skeleton.content)
        written.append(str(target))
    return written


def summarize(skeletons: Sequence[Skeleton], anchors: Sequence[str] = ()) -> Dict[str, Any]:
    """生成事实（**可对账的子集**）：真值只声明其中一部分键时按声明逐键比对。"""
    written = [item for item in skeletons if item.written]
    sections: List[str] = []
    for item in written:
        for name in item.sections:
            if name not in sections:
                sections.append(name)
    return {
        "generated_count": len([item for item in skeletons if item.status == STATUS_GENERATED]),
        "written_count": len(written),
        "statuses": [item.status for item in skeletons],
        "reasons": [item.reason for item in skeletons],
        "symbols": sorted({name for item in skeletons for name in item.symbols}),
        "candidate_count": len([name for item in skeletons for name in item.candidates]),
        "sections": sorted(sections),
        "assumed_convention": bool(skeletons) and all(item.assumed_convention for item in skeletons),
        "anchor_fragments": [fragment for fragment in anchors if any(fragment in item.content for item in written)],
        "episodes": [{"episode_id": item.episode_id, "status": item.status, "reason": item.reason} for item in skeletons],
    }


def parse_episode_ref(ref: str) -> Tuple[str, str]:
    """`<会话引用>#<首次修复事件序号>` → (会话引用, 序号)。

    会话引用与 `s2r reconstruct` 同形（`<source>:<session_id>` 或 jsonl 日志路径）；
    episode_id 的形态由 `[episode].id_format` 定义，两者拼起来就是一个可定位的引用。
    """
    head, separator, tail = str(ref).rpartition("#")
    if not separator or not head or not tail:
        raise UsageError(
            "episode 引用认不出来: {}".format(ref),
            hint="写成 <会话引用>#<序号>，会话引用可以是 <source>:<session_id> 或 jsonl 日志路径",
        )
    return head, tail


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def _is_file(path: Path) -> bool:
    """`Path.is_file()` 在 3.8 上遇到非法文件名会抛 `OSError`（与 `reconstruct._is_file` 同因）。"""
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False
