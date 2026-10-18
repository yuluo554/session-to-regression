"""发布门自身的运行时形态（门也是代码，也要被当代码测）。

**不在这里断言的**："当前仓库 0 FAIL" —— 那是**发布态**结论，发布之前它本就该红
（口径要求"还没发生的事实不许预填"）。把它写进测试，会让发布之前的**正常**提交永远红、
红得没有信息量。发布态自检是收尾时手工跑 `python scripts/check_release.py --online`。

**在这里断言的**：门的三条运行时形态——① 结果清单是模块级的，入口必须清空（否则同进程
连调两次"越跑越绿"）；② 离线时网络类检查报 SKIP 不报 PASS，且统计行把 SKIP 与 PASS 分开计；
③ 检查项名成集合、逐项恰好一条结论（"检查项数要与变异组数对账"的仓内形态）。
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from _tools import load_script

RELEASE_CHECK = load_script("check_release")

#: 离线检查项（每项一条结论）+ 离线模式下应报 SKIP 的网络类检查
OFFLINE_CHECKS = ("version", "readme", "ledger", "paths", "worktree", "release_doc")
NETWORK_CHECKS = ("remote", "ci", "release", "assets")

ROW = re.compile(r"^\[(\w+)\]\s+(\S+)", re.MULTILINE)
SUMMARY = re.compile(r"^release check: (\d+) FAIL / (\d+) WARN / (\d+) PASS / (\d+) SKIP$", re.MULTILINE)


def run_gate(capsys, argv: List[str]) -> Tuple[int, List[Tuple[str, str]], str]:
    """在进程内跑一次门，返回 (退出码, [(状态, 检查名)], 完整输出)。"""
    code = RELEASE_CHECK.main(argv)
    text = capsys.readouterr().out
    return code, [(status, name) for status, name in ROW.findall(text)], text


def status_of(text: str) -> Dict[str, str]:
    return {name: status for status, name in ROW.findall(text)}


def test_every_check_reports_exactly_once(capsys):
    _, rows, _ = run_gate(capsys, ["--repo", str(RELEASE_CHECK.REPO_ROOT)])
    names = [name for _, name in rows]
    expected = OFFLINE_CHECKS + NETWORK_CHECKS
    for name in expected:
        assert names.count(name) == 1, "检查项 {} 的结论条数不是 1: {}".format(name, names)
    assert sorted(names) == sorted(expected), "出现了未登记的检查项名: {}".format(sorted(set(names) - set(expected)))


def test_offline_mode_reports_network_checks_as_skip_not_pass(capsys):
    _, _, text = run_gate(capsys, ["--repo", str(RELEASE_CHECK.REPO_ROOT)])
    statuses = status_of(text)
    for name in NETWORK_CHECKS:
        assert statuses[name] == RELEASE_CHECK.STATUS_SKIP, "{} 离线时应报 SKIP，实际 {}".format(
            name, statuses[name]
        )


def test_offline_skips_are_counted_separately_from_passes(capsys):
    _, _, text = run_gate(capsys, ["--repo", str(RELEASE_CHECK.REPO_ROOT)])
    match = SUMMARY.search(text)
    assert match is not None, "统计行缺失或形态变了:\n{}".format(text)
    failed, warned, passed, skipped = (int(value) for value in match.groups())
    rows = [status for status, _ in ROW.findall(text)]
    assert failed == rows.count(RELEASE_CHECK.STATUS_FAIL), "FAIL 计数与逐条结论不符"
    assert warned == rows.count(RELEASE_CHECK.STATUS_WARN)
    assert passed == rows.count(RELEASE_CHECK.STATUS_PASS)
    assert skipped == rows.count(RELEASE_CHECK.STATUS_SKIP)
    assert skipped == len(NETWORK_CHECKS), "SKIP 数应等于离线未执行的网络类检查数（没查 ≠ 查过了）"


def test_results_do_not_accumulate_across_two_runs(capsys):
    """模块级结果清单忘了 clear 时，第二次跑会"越跑越绿"（结果条数变多、PASS 数上涨）。"""
    first_code, first_rows, _ = run_gate(capsys, ["--repo", str(RELEASE_CHECK.REPO_ROOT)])
    second_code, second_rows, _ = run_gate(capsys, ["--repo", str(RELEASE_CHECK.REPO_ROOT)])
    assert len(first_rows) == len(second_rows), "第二次跑结果条数变了：{} → {}".format(
        len(first_rows), len(second_rows)
    )
    assert first_code == second_code
