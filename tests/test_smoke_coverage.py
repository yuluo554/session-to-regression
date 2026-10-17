"""CI 冒烟覆盖面：每个已注册子命令都必须有 bare 与 module 两种形态的冒烟。

判据来源是方法论的「绿的矩阵是证据还是装饰」——把「已交付命令清单」与「冒烟覆盖的命令清单」
并排数一遍：缺一条就是"这条命令其实从没在 CI 里跑过"。
"""

from __future__ import annotations

from _tools import load_tool
from s2r import cli, errors

SMOKE = load_tool("ci_smoke")


def _covers_forms():
    rows = SMOKE.build_smoke(any_staged=True)
    table = {}
    for row in rows:
        table.setdefault(row.covers, set()).add(row.form)
    return table


def test_every_registered_command_is_smoke_covered_in_both_forms():
    registered = set(cli.registered_commands()) | {"--version"}
    table = _covers_forms()
    assert set(table) == registered, "冒烟覆盖面与已注册子命令不一致: {}".format(sorted(set(table) ^ registered))
    for command, forms in sorted(table.items()):
        assert forms == {SMOKE.BARE, SMOKE.MODULE}, "{} 缺调用形态: {}".format(command, sorted(forms))


def test_every_staged_command_has_a_real_invocation_in_the_smoke_table():
    """冒烟的 argv 必须是可真跑的形态（不能只写命令名导致 argparse 报错被误判为契约）。"""
    assert set(SMOKE.EXPECTED_DEFAULT_ARGS) == set(cli.registered_commands()) - {"selftest", "config"}
    for command, args in sorted(SMOKE.EXPECTED_DEFAULT_ARGS.items()):
        for form in (SMOKE.BARE, SMOKE.MODULE):
            argv = list(SMOKE._staged_argv(command, form))
            assert command in argv, argv
            assert "--config" not in argv  # 默认定位口径文件（显式传会掩盖定位缺陷）


def test_staged_expectation_is_the_retirement_contract():
    """骨架期期望值 = 阶段未就绪；里程碑落地时这条断言会红——它就是"退役待办"的机器提醒。"""
    rows = SMOKE.build_smoke(any_staged=True)
    staged_rows = [row for row in rows if row.covers in cli.STAGED_COMMANDS]
    assert staged_rows, "没有覆盖到任何未交付命令"
    assert {row.expect_exit for row in staged_rows} == {errors.EXIT_STAGE_NOT_READY}
    assert {row.covers for row in staged_rows} == set(cli.STAGED_COMMANDS)


def test_eol_tool_check_mode_agrees_with_scan(repo_root):
    """CI 里跑的是 tools/fix_eol.py --check；这里锁住"检查面非空且干净"。"""
    fix_eol = load_tool("fix_eol")
    problems, checked = fix_eol.scan(repo_root)
    assert checked >= 10
    assert not problems
