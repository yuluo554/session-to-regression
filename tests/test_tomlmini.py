"""TOML 子集读取器：支持面与**拒绝面**都要有测试。

拒绝面是重点——"读不懂就显式报错"必须逐形态验过，否则某天某形态被静默忽略，
口径文件里写错的数值会悄悄消失。
"""

from __future__ import annotations

import pytest

from s2r import errors, tomlmini

SAMPLE = """\
# 顶层注释
config_version = 1
name = "demo"
quoted = "转义\\t制表 \\" 引号"
literal = '原样 \\n 不转义'
flag = true
off = false
ratio = 1.5
items = ["a", "b", ]  # 尾随逗号
nested = [{ name = "x" }, { name = "y" }]
multi = [
  "one",   # 跨行数组
  "two",
]

[table]
key = 7

[table.sub]
deep = "v"
"quoted key" = 9
"""


def test_parses_supported_subset():
    data = tomlmini.loads(SAMPLE)
    assert data["config_version"] == 1
    assert data["name"] == "demo"
    assert data["quoted"] == '转义\t制表 " 引号'
    assert data["literal"] == "原样 \\n 不转义"
    assert data["flag"] is True
    assert data["off"] is False
    assert data["ratio"] == 1.5
    assert data["items"] == ["a", "b"]
    assert data["nested"] == [{"name": "x"}, {"name": "y"}]
    assert data["multi"] == ["one", "two"]
    assert data["table"] == {"key": 7, "sub": {"deep": "v", "quoted key": 9}}


def test_parsing_is_deterministic():
    assert tomlmini.loads(SAMPLE) == tomlmini.loads(SAMPLE)


def test_comments_inside_strings_are_not_comments():
    data = tomlmini.loads('url = "https://example.invalid/#frag"  # 真注释\n')
    assert data["url"] == "https://example.invalid/#frag"


@pytest.mark.parametrize(
    "text, expect",
    [
        ("[[array_of_tables]]\n", "数组表"),
        ("[table]\nkey = 1\nkey = 2\n", "键重复定义"),
        ("[table]\n[table]\n", "表重复定义"),
        ("just_words\n", "key = value"),
        ("key =\n", "缺值"),
        ("key = \n", "缺值"),
        ("key = 1.2.3\n", "值形态不支持"),
        ('key = "unterminated\n', "收尾引号"),
        ('key = """multi"""\n', "多行字符串"),
        ("key.sub = 1\n", "键名不合法"),
        ("key = [1, 2\nother = 3\n", "值未收尾"),
        ('key = "a" ]\n', "多余的收尾括号"),
        ("key = [1, 2, 3.5]\n", "数组元素类型不一致"),
        ("key = [1,, 2]\n", "连续逗号"),
        ("table.x = 1\n", "键名不合法"),
        ('key = "\\q"\n', "不支持的转义"),
        ("[a\n", "缺少收尾的 ]"),
        ("key = {x = 1\n", "值未收尾"),
        ('key = {x = "a" y = "b"}\n', "字符串后有意外内容"),
        ("key = {x = 1, x = 2}\n", "内联表键重复"),
        ("key = [1] junk\n", "数组 ] 后有多余内容"),
        ("key = {x = 1} junk\n", "内联表 } 后有多余内容"),
        ("= 1\n", "缺键名"),
        ("[]\n", "空表名"),
    ],
)
def test_unsupported_forms_are_rejected_with_line_number(text, expect):
    with pytest.raises(errors.ConfigError) as excinfo:
        tomlmini.loads(text, origin="inline.toml")
    message = str(excinfo.value)
    assert expect in message, message
    assert "inline.toml:" in message, message


def test_error_reports_the_offending_line():
    with pytest.raises(errors.ConfigError) as excinfo:
        tomlmini.loads("a = 1\nb = 2\nc = nope\n", origin="f.toml")
    assert "f.toml:3" in str(excinfo.value)


def test_trailing_comma_positions_are_checked():
    assert tomlmini.loads('k = ["a", "b",]\n')["k"] == ["a", "b"]
    with pytest.raises(errors.ConfigError):
        tomlmini.loads('k = ["a",, "b"]\n')
    with pytest.raises(errors.ConfigError):
        tomlmini.loads('k = [, "b"]\n')


def test_reads_repo_pyproject_and_config(config_path, repo_root):
    """dogfood：本读取器必须能读我们自己的 pyproject.toml 与口径文件。"""
    pyproject = tomlmini.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"), origin="pyproject.toml")
    assert pyproject["project"]["scripts"] == {"s2r": "s2r.cli:main"}
    assert pyproject["build-system"]["build-backend"] == "setuptools.build_meta"

    scoring = tomlmini.loads(config_path.read_text(encoding="utf-8"), origin="scoring.toml")
    assert isinstance(scoring["scoring"]["weights"]["failure_signal"], int)
    assert scoring["vocabulary"]["bug_terms"]
