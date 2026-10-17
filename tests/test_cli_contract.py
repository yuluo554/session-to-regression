"""CLI 契约：退出码语义 + 「未交付命令不产出任何结果」。

这些断言是「交付路径 == 验收路径」的最小形态：里程碑落地时**同一次提交**里改这里的期望值，
并同步退役 `errors.EXIT_CODES` 里对应的语义（CLI 文案 / README 退出码表 / 本测试 / CI 冒烟四处）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _tools import load_tool
from s2r import cli, config, errors

SMOKE = load_tool("ci_smoke")
STAGED_ARGS = SMOKE.INVOCATIONS


def test_version_and_help_exit_zero(capsys):
    assert cli.main(["--version"]) == errors.EXIT_OK
    version_out = capsys.readouterr().out
    assert cli.main(["--help"]) == errors.EXIT_OK
    help_out = capsys.readouterr().out
    assert "s2r" in version_out
    assert "COMMAND" in help_out


def test_no_command_is_usage_error(capsys):
    assert cli.main([]) == errors.EXIT_USAGE
    assert "缺子命令" in capsys.readouterr().err


def test_unknown_command_is_usage_error(capsys):
    assert cli.main(["definitely-not-a-command"]) == errors.EXIT_USAGE
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["scan"], ["run"], ["reconstruct"], ["generate"], ["verify"]])
def test_missing_required_args_exit_2(argv, capsys):
    assert cli.main(argv) == errors.EXIT_USAGE
    assert "usage" in capsys.readouterr().err


def test_config_without_subcommand_is_usage_error(capsys):
    assert cli.main(["config"]) == errors.EXIT_USAGE
    assert "config" in capsys.readouterr().err


def test_staged_commands_report_milestone_and_produce_nothing(capsys):
    """未交付命令：exit 4 + 报出所属里程碑 + **stdout 为空**（不伪造空结果）。"""
    assert set(STAGED_ARGS) == set(cli.registered_commands()) - {"selftest", "config"}
    for command in sorted(cli.STAGED_COMMANDS):
        code = cli.main([command] + list(STAGED_ARGS[command]))
        captured = capsys.readouterr()
        assert code == errors.EXIT_STAGE_NOT_READY, command
        assert "stage_not_ready" in captured.err, command
        assert cli.STAGED_COMMANDS[command] in captured.err, command
        assert captured.out == "", command


def test_selftest_ok_with_explicit_config(repo_root, capsys):
    code = cli.main(["selftest", "--config", str(repo_root / "config" / "scoring.toml")])
    out = capsys.readouterr().out
    assert code == errors.EXIT_OK
    assert "status: ok" in out


def test_selftest_json_is_machine_readable(repo_root, capsys):
    code = cli.main(["selftest", "--json", "--config", str(repo_root / "config" / "scoring.toml")])
    payload = json.loads(capsys.readouterr().out)
    assert code == errors.EXIT_OK
    assert payload["status"] == "ok"
    assert payload["checks"], "自检项不得为空（否则等于什么都没验）"
    assert all(row["ok"] for row in payload["checks"])


def test_selftest_is_falsifiable_with_broken_config(tmp_path, capsys):
    """可证伪性：配置写坏 → exit 1（不是恒 0 的假自检）。"""
    broken = tmp_path / "scoring.toml"
    broken.write_text("config_version = 1\n", encoding="utf-8")
    code = cli.main(["selftest", "--config", str(broken)])
    out = capsys.readouterr().out
    assert code == errors.EXIT_CHECK_FAILED
    assert "FAIL" in out
    assert "status: failed" in out


def test_selftest_missing_config_exit_2(tmp_path, capsys):
    code = cli.main(["selftest", "--config", str(tmp_path / "nope.toml")])
    assert code == errors.EXIT_USAGE
    assert "input_unavailable" in capsys.readouterr().err


def test_selftest_reports_missing_pytest_as_dependency(monkeypatch, repo_root, capsys):
    """依赖缺失语义：pytest 不可用 → exit 3，不退化成 exit 0。"""
    monkeypatch.setattr(cli, "pytest_available", lambda: False)
    code = cli.main(["selftest", "--config", str(repo_root / "config" / "scoring.toml")])
    captured = capsys.readouterr()
    assert code == errors.EXIT_DEPENDENCY_MISSING
    assert "pytest_available" in captured.out
    assert "status: dependency_missing" in captured.out


def test_config_show_json_is_deterministic_and_complete(config_path, capsys):
    assert cli.main(["config", "show", "--json", "--config", str(config_path)]) == errors.EXIT_OK
    first = capsys.readouterr().out
    assert cli.main(["config", "show", "--json", "--config", str(config_path)]) == errors.EXIT_OK
    second = capsys.readouterr().out
    assert first == second, "同输入双跑必须逐字节一致"
    payload = json.loads(first)
    loaded = config.load_config(config_path)
    assert set(payload) == {key for key, _ in config.describe(loaded)}
    assert payload["scoring.weights.reconstruction"] == config.weights(loaded)["reconstruction"]


def test_config_show_human_lists_same_keys(config_path, capsys):
    assert cli.main(["config", "show", "--config", str(config_path)]) == errors.EXIT_OK
    out = capsys.readouterr().out
    loaded = config.load_config(config_path)
    for key, _ in config.describe(loaded):
        assert key in out


def test_config_path_prints_resolved_file(config_path, capsys):
    assert cli.main(["config", "path", "--config", str(config_path)]) == errors.EXIT_OK
    printed = Path(capsys.readouterr().out.strip())
    assert printed.resolve() == config_path.resolve()


def test_registered_commands_match_staged_map():
    """已注册子命令 = 冒烟表覆盖的命令 ∪ 无参的两条；未交付的就是"已注册但不在已交付期望表"的那些。"""
    registered = set(cli.registered_commands())
    assert registered == set(SMOKE.INVOCATIONS) | {"selftest", "config"}
    assert set(cli.STAGED_COMMANDS) <= registered
    assert set(cli.STAGED_COMMANDS) == registered - set(SMOKE.DELIVERED_EXPECT) - {"selftest", "config"}


def test_console_script_target_is_importable(repo_root):
    """console script 指向的对象必须真的存在（否则装好的命令一敲就报错）。"""
    import importlib

    from s2r import tomlmini

    data = tomlmini.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"), origin="pyproject.toml")
    scripts = data["project"]["scripts"]
    assert scripts == {"s2r": "s2r.cli:main"}
    module_name, _, attr = scripts["s2r"].partition(":")
    assert callable(getattr(importlib.import_module(module_name), attr))
