"""README / pyproject / 安装面 的门：文档承诺的每一项都要有实物。

对应方法论里「对外声称的东西逐条有实物」：README 里的 extras、命令、退出码、许可文件
都要能被机器核对；pyproject 的包布局与 package-data 要真能解析到文件。
"""

from __future__ import annotations

import re
from pathlib import Path

from s2r import cli, errors, tomlmini

EXTRAS_LINE = re.compile(r'pip install -e "\.\[([^\]]+)\]"')
COMMAND_MENTION = re.compile(r"\bs2r\s+([a-z][a-z0-9-]*)")
EXIT_CODE_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.MULTILINE)


def read_readme(repo_root: Path) -> str:
    return (repo_root / "README.md").read_text(encoding="utf-8")


def load_pyproject(repo_root: Path) -> dict:
    return tomlmini.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"), origin="pyproject.toml")


def test_readme_mentions_real_extras_bidirectionally(repo_root):
    readme = read_readme(repo_root)
    pyproject = load_pyproject(repo_root)
    declared = set(pyproject["project"]["optional-dependencies"])
    mentioned = {token.strip() for match in EXTRAS_LINE.findall(readme) for token in match.split(",")}
    assert mentioned, "README 里没找到 pip install -e \".[...]\" 安装行（扫描面为空）"
    assert mentioned == declared, "README extras 与 pyproject 声明不一致: {} vs {}".format(sorted(mentioned), sorted(declared))


def test_readme_commands_all_exist_in_the_parser(repo_root):
    mentioned = set(COMMAND_MENTION.findall(read_readme(repo_root)))
    assert mentioned, "README 里没找到任何 s2r <命令> 提及（扫描面为空）"
    registered = set(cli.registered_commands())
    unknown = sorted(mentioned - registered)
    assert not unknown, "README 引用了不存在的子命令: {}".format(unknown)


def test_readme_documents_every_exit_code(repo_root):
    documented = {int(value) for value in EXIT_CODE_ROW.findall(read_readme(repo_root))}
    assert documented, "README 没有退出码表"
    missing = sorted(set(errors.EXIT_CODES) - documented)
    assert not missing, "README 退出码表缺: {}".format(missing)


def test_readme_license_claim_has_a_file(repo_root):
    readme = read_readme(repo_root)
    assert "MIT" in readme
    assert (repo_root / "LICENSE").is_file()
    assert "MIT License" in (repo_root / "LICENSE").read_text(encoding="utf-8")


def test_package_layout_resolves_to_real_directories(repo_root):
    """安装面第四面（lite）：package-dir/find 的映射必须解析到真存在的目录。"""
    pyproject = load_pyproject(repo_root)
    package_dir = pyproject["tool"]["setuptools"]["package-dir"]
    where = pyproject["tool"]["setuptools"]["packages"]["find"]["where"]
    assert package_dir == {"": "src"}
    assert where == ["src"]
    for package, mapping in package_dir.items():
        assert (repo_root / mapping).is_dir(), mapping
    assert (repo_root / "src" / "s2r" / "__init__.py").is_file()


def test_runtime_data_file_is_on_the_install_face(repo_root):
    """安装面第五面（lite）：运行期要读的口径文件必须在 package-data 白名单里且真实存在。"""
    pyproject = load_pyproject(repo_root)
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert package_data == {"s2r": ["resources/*.toml"]}
    for pattern in package_data["s2r"]:
        matches = sorted((repo_root / "src" / "s2r").glob(pattern))
        assert matches, "package-data 模式没匹配到文件: {}".format(pattern)
        assert any(path.name == "scoring.toml" for path in matches)
