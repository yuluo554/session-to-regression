"""唯一数值口径：文件只有一份内容、代码里没有第二份数值表、词表只有一处。

三条门（都做过"故意弄坏必红"的核对）：

1. 仓库权威文件与随包副本**逐字节相同**——否则安装后跑的是另一份口径；
2. **源码里不存在 ≥100 的整数口径字面量**（白名单只有 I/O 读块大小；白名单失效也算红）；
3. **没有任何一处代码/测试内嵌第二张语义词表**（同一字符串里出现 ≥2 个词表词即红）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

from _tools import REPO_ROOT, load_tool
from s2r import config, errors, tomlmini

FIX_EOL = load_tool("fix_eol")

#: 允许存在的 ≥100 整数字面量（表达式必须是"为什么它不是口径值"）。
#: 扫描面 = 生产代码（src/s2r 与 tools/）；**tests/ 不在扫描面内**——测试里的期望数值是
#: "口径锁"（把契约钉死），与"代码里藏第二份口径"不是同一类东西。
ALLOWED_LARGE_INT_LITERALS: Dict[Tuple[str, int], str] = {
    ("serialize.py", 65536): "文件读取块大小（I/O 缓冲），不是口径数值",
    ("fix_eol.py", 8192): "二进制探测读入字节数，不是口径数值",
    ("ci_smoke.py", 120): "子进程冒烟超时（秒），不是口径数值",
    ("ci_smoke.py", 200): "失败输出摘要截断长度，不是口径数值",
}

CJK_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def _py_files(root: Path) -> List[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _source_roots(repo_root: Path) -> List[Path]:
    """生产代码面：源码包 + tools 脚本（不含 tests/，理由见白名单注释）。"""
    return [repo_root / "src" / "s2r", repo_root / "tools"]


def _all_python_roots(repo_root: Path) -> List[Path]:
    return _source_roots(repo_root) + [repo_root / "tests"]


def test_repo_and_package_config_are_byte_identical(repo_root, config_path):
    packaged = config.package_config_path()
    assert packaged.is_file(), "随包副本缺失：安装后定位不到口径文件"
    assert config_path.read_bytes() == packaged.read_bytes()


def test_repo_config_is_found_from_a_nested_cwd(repo_root):
    nested = repo_root / "src" / "s2r"
    found = config.repo_config_path(start=nested)
    assert found is not None
    assert found.resolve() == (repo_root / "config" / "scoring.toml").resolve()


def test_package_copy_is_locatable_without_repo_tree(tmp_path):
    """隔离目录（模拟"装好的包在别处跑"）：定位必须落到随包副本。"""
    resolved = config.resolve_config_path(start=tmp_path)
    assert resolved.resolve() == config.package_config_path().resolve()


def test_resolution_order_explicit_beats_env_beats_cwd(tmp_path, config_path):
    nested = tmp_path / "a" / "b"
    (nested / "config").mkdir(parents=True)
    local = nested / "config" / "scoring.toml"
    local.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
    other = tmp_path / "other.toml"
    other.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

    assert config.resolve_config_path(explicit=str(other), env={"S2R_CONFIG": str(local)}, start=nested) == other
    assert config.resolve_config_path(explicit=None, env={"S2R_CONFIG": str(local)}, start=nested) == local
    assert config.resolve_config_path(explicit=None, env={}, start=nested) == local


def test_missing_config_everywhere_is_input_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "package_config_path", lambda: tmp_path / "absolutely-missing.toml")
    with pytest.raises(errors.InputUnavailable) as excinfo:
        config.resolve_config_path(start=tmp_path)
    assert excinfo.value.exit_code == errors.EXIT_USAGE
    assert "S2R_CONFIG" in (excinfo.value.hint or "")


def test_explicit_and_env_paths_must_exist(tmp_path):
    with pytest.raises(errors.InputUnavailable):
        config.resolve_config_path(explicit=str(tmp_path / "nope.toml"))
    with pytest.raises(errors.InputUnavailable):
        config.resolve_config_path(explicit=None, env={"S2R_CONFIG": str(tmp_path / "nope.toml")})


def test_every_config_leaf_is_schema_checked(config_path):
    """配置里出现的每个叶子键都必须在 SCHEMA 里被校验，防"加了新口径但没人校验"。"""
    loaded = config.load_config(config_path)
    declared = {key for key, _ in config.describe(loaded)}
    assert declared, "扫描面为空（读取或 describe 坏了）"
    assert declared == {dotted for dotted, _ in config.SCHEMA}


def test_declared_weights_match_problem_statement(config_path):
    """题面 §2 M2 的口径声明（+2/+3/+2/+2）在此锁死：改权重必须同时改题面引用与本测试。"""
    loaded = config.load_config(config_path)
    assert config.weights(loaded) == {
        "failure_signal": 2,
        "reconstruction": 3,
        "test_command": 2,
        "code_change": 2,
    }
    assert config.min_score(loaded) >= 0
    assert config.verify_params(loaded)["timeout_seconds"] >= 1


@pytest.mark.parametrize(
    "patch, expect",
    [
        ("config_version = 1\n", "scoring.weights.failure_signal"),
        ("config_version = 1\nscoring = 1\n", "scoring.weights"),
    ],
)
def test_missing_keys_are_reported_with_names(patch, expect):
    with pytest.raises(errors.ConfigError) as excinfo:
        config.validate_config(tomlmini.loads(patch), origin="<inline>")
    assert expect in str(excinfo.value)


def test_wrong_types_and_out_of_range_values_are_rejected(config_path):
    good = tomlmini.loads(config_path.read_text(encoding="utf-8"))

    wrong_type = dict(good)
    wrong_type["scoring"] = dict(good["scoring"])
    wrong_type["scoring"]["weights"] = dict(good["scoring"]["weights"])
    wrong_type["scoring"]["weights"]["failure_signal"] = "2"
    with pytest.raises(errors.ConfigError) as excinfo:
        config.validate_config(wrong_type)
    assert "应为 int" in str(excinfo.value)

    negative = dict(good)
    negative["scoring"] = {"weights": dict(good["scoring"]["weights"]), "threshold": {"min_score": -1}}
    with pytest.raises(errors.ConfigError) as excinfo:
        config.validate_config(negative)
    assert "必须 ≥ 0" in str(excinfo.value)

    empty_vocab = dict(good)
    empty_vocab["vocabulary"] = {"bug_terms": [], "failure_signals": ["x"]}
    with pytest.raises(errors.ConfigError) as excinfo:
        config.validate_config(empty_vocab)
    assert "不得为空" in str(excinfo.value)

    empty_pytest = dict(good)
    empty_pytest["verify"] = dict(good["verify"])
    empty_pytest["verify"]["pytest_command"] = []
    with pytest.raises(errors.ConfigError) as excinfo:
        config.validate_config(empty_pytest)
    assert "verify.pytest_command" in str(excinfo.value)


def test_boolean_is_not_an_integer(config_path):
    good = tomlmini.loads(config_path.read_text(encoding="utf-8"))
    mutated = dict(good)
    mutated["scoring"] = {"weights": dict(good["scoring"]["weights"]), "threshold": {"min_score": True}}
    with pytest.raises(errors.ConfigError) as excinfo:
        config.validate_config(mutated)
    assert "bool" in str(excinfo.value)


def test_no_large_numeric_policy_literal_in_source(repo_root):
    """数值口径只能来自配置：生产代码里出现 ≥100 的整数字面量即红（白名单双向核对）。"""
    found: Set[Tuple[str, int]] = set()
    scanned = 0
    for root in _source_roots(repo_root):
        for path in _py_files(root):
            scanned += 1
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
                    if abs(node.value) >= 100:
                        found.add((path.name, abs(node.value)))
    assert scanned >= 8, "扫描面过小（{} 个文件），门可能扫错了目录".format(scanned)
    assert found == set(ALLOWED_LARGE_INT_LITERALS), "多出或缺失的白名单项：{}".format(sorted(found))


def test_no_second_vocabulary_table_in_sources(repo_root):
    """语义词表只有配置文件一处。

    判据 = **逐文件**统计"独立的词表词"个数：同一文件里出现 ≥2 个词表词即红。
    为什么按文件而不是按单个字符串字面量：第二张词表的典型形态是一串独立的字符串常量
    （形如 ``WORDS = [<词一>, <词二>, <词三>]``），按字面量计数会各算 1 个而整体漏判
    ——首版就是这么写的，变异验证（往源码里塞一张三词词表）当场发现它是假门。
    词面按非字母/非汉字字符切分后**整词相等**才算命中，因此"自检失败""显式报错"这类
    把词表词嵌在更长词里的叙述不会误伤。

    ⚠️ 本文件自身也在扫描面内：**留档与注释不得复述词表词面**（举例子也不行，会被自己扫中）
    ——首版注释里写了个真实例子，当场把自己的门打红。
    """
    terms = sorted({term for values in config.vocabulary(config.load_config(repo_root / "config" / "scoring.toml")).values() for term in values})
    assert len(terms) >= 2, "词表扫描面为空"

    offenders: List[Tuple[str, List[str]]] = []
    scanned = 0
    files_by_terms: Dict[str, List[str]] = {}
    for root in _all_python_roots(repo_root):
        for path in _py_files(root):
            scanned += 1
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            hits: Set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    tokens = set(CJK_TOKEN_SPLIT.split(node.value))
                    hits.update(term for term in terms if term in tokens)
            if hits:
                files_by_terms[path.name] = sorted(hits)
            if len(hits) >= 2:
                offenders.append((path.name, sorted(hits)))
    assert scanned >= 10, "扫描面过小"
    assert not offenders, "发现第二张词表（同一文件出现多个词表词）: {}".format(offenders)
    assert files_by_terms, "扫描面里一个词表词都没有——门可能扫错了目录"


def test_text_hygiene_scan_covers_the_repo(repo_root):
    """EOL/结尾换行门（与 CI 跑的 tools/fix_eol.py 同源）。"""
    problems, checked = FIX_EOL.scan(repo_root)
    assert checked >= 10, "扫描面过小"
    assert not problems, "行尾/结尾换行问题: {}".format([(p.path.name, p.detail) for p in problems])
