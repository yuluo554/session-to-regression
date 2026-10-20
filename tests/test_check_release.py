"""发布门自身的运行时形态（门也是代码，也要被当代码测）。

**不在这里断言的**："当前仓库 0 FAIL" —— 那是**发布态**结论，发布之前它本就该红
（口径要求"还没发生的事实不许预填"）。把它写进测试，会让发布之前的**正常**提交永远红、
红得没有信息量。发布态自检是收尾时手工跑 `python scripts/check_release.py --online`。

**在这里断言的**：门的三条运行时形态——① 结果清单是模块级的，入口必须清空（否则同进程
连调两次"越跑越绿"）；② 离线时网络类检查报 SKIP 不报 PASS，且统计行把 SKIP 与 PASS 分开计；
③ 检查项名成集合、逐项恰好一条结论（"检查项数要与变异组数对账"的仓内形态）。

**外加一条形态面**（D1 的回归测试）：离线 SKIP 的判据是**具名集合**，不是"恰好 N 条"——
仓库树里 `.git` 恒在，所以"离线不执行的只有 4 个网络类"这条旧不变量**从没在发布物形态上跑过**；
Release 源码包解压树**没有 `.git`**，`worktree` 在那里按设计降级为 SKIP，于是附件里的
`pytest` 红成 `assert 5 == 4`。本文件因此显式声明"哪几条检查允许在 gitless 树上 SKIP"，
并新增一条**在无 `.git` 副本里跑门**的回归测试。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Tuple

from _tools import load_script

RELEASE_CHECK = load_script("check_release")

#: 离线检查项（每项一条结论）+ 离线模式下应报 SKIP 的网络类检查
OFFLINE_CHECKS = ("version", "readme", "ledger", "paths", "worktree", "tag_freshness", "release_doc")
NETWORK_CHECKS = ("remote", "ci", "release", "assets")

#: 只依赖 `.git` 的离线检查：在 **gitless 树**（Release 源码包解压树）里按设计**降级为 SKIP**
#: ——门自己的理由文案就是"这棵树没有 .git（源码包解压树里本就不适用）"。
#:
#: 这是**具名集合**而不是"N 条 SKIP 都行"：多出一条没登记的 SKIP（例如把
#: `version`/`readme`/`ledger`/`paths`/`release_doc` 偷偷降级）会立刻红。判据的来源是门已经登记的
#: 语义，不是"为了让测试变绿"。
GITLESS_SKIPS = frozenset({"worktree", "tag_freshness"})

#: 造 gitless 副本时剔除的东西：`.git` 是要测的对象，其余是构建/环境/交接工作物
#: （副本要 ≈ `git archive` 出来的发布物形态，而不是"带缓存的本机工作树"）。
COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv*", "build", "dist", "__pycache__", ".pytest_cache", "*.egg-info", "update",
)

ROW = re.compile(r"^\[(\w+)\]\s+(\S+)", re.MULTILINE)
SUMMARY = re.compile(r"^release check: (\d+) FAIL / (\d+) WARN / (\d+) PASS / (\d+) SKIP$", re.MULTILINE)


def run_gate(capsys, argv: List[str]) -> Tuple[int, List[Tuple[str, str]], str]:
    """在进程内跑一次门，返回 (退出码, [(状态, 检查名)], 完整输出)。"""
    code = RELEASE_CHECK.main(argv)
    text = capsys.readouterr().out
    return code, [(status, name) for status, name in ROW.findall(text)], text


def status_of(text: str) -> dict:
    return {name: status for status, name in ROW.findall(text)}


def skipped_names(text: str) -> Set[str]:
    return {name for status, name in ROW.findall(text) if status == RELEASE_CHECK.STATUS_SKIP}


def counts_match_rows(text: str) -> Tuple[int, int, int, int]:
    """统计行与逐条结论**逐项对账**（口径：统计行不是另一份真相）。"""
    match = SUMMARY.search(text)
    assert match is not None, "统计行缺失或形态变了:\n{}".format(text)
    failed, warned, passed, skipped = (int(value) for value in match.groups())
    rows = [status for status, _ in ROW.findall(text)]
    assert failed == rows.count(RELEASE_CHECK.STATUS_FAIL), "FAIL 计数与逐条结论不符"
    assert warned == rows.count(RELEASE_CHECK.STATUS_WARN), "WARN 计数与逐条结论不符"
    assert passed == rows.count(RELEASE_CHECK.STATUS_PASS), "PASS 计数与逐条结论不符"
    assert skipped == rows.count(RELEASE_CHECK.STATUS_SKIP), "SKIP 计数与逐条结论不符"
    assert skipped == len(skipped_names(text)), "SKIP 计数与逐条结论的检查名集合不符"
    return failed, warned, passed, skipped


def assert_offline_skip_set_is_registered(text: str) -> Set[str]:
    """离线 SKIP 的**具名集合**不变量（D1 的判据面）。

    ① 四个网络类检查必须都在 SKIP 里——离线**没执行**，不许冒充 PASS；
    ② 除网络类外，只有"本树没有 `.git` 时无对象可查"的检查（`GITLESS_SKIPS`）允许额外 SKIP；
    ③ 统计行与逐条结论逐项对账（含 SKIP 名集合与 SKIP 计数一致）。
    """
    counts_match_rows(text)
    names = skipped_names(text)
    assert set(NETWORK_CHECKS) <= names, "离线未执行的网络类检查必须报 SKIP: {}".format(
        sorted(set(NETWORK_CHECKS) - names)
    )
    unexpected = names - set(NETWORK_CHECKS) - set(GITLESS_SKIPS)
    assert not unexpected, "出现了未登记的离线 SKIP 检查项（不许把没查说成跳过/查过）: {}".format(
        sorted(unexpected)
    )
    return names


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
    # 统计行/逐条结论逐项对账 + SKIP 是具名集合（原来"恰好 4 条"的硬编码口径已换成
    # "四个网络类必 SKIP + 只允许已登记的 gitless 降级项额外 SKIP"，判据见上面的函数注释）。
    # 不断言"恰好等于网络类数"：这条测试本身也要在**发布物形态**（无 `.git`）里通过。
    assert_offline_skip_set_is_registered(text)


def test_offline_gate_in_a_gitless_tree_registers_every_skip(tmp_path):
    """D1 的回归测试：在**没有 `.git`** 的副本里跑门（发布物形态）。

    仓库树里 `.git` 恒在，所以"离线 SKIP 恰好 4 条"这条旧不变量从没在发布物形态上被跑过——
    附件里的 `pytest` 就是这么红的（`assert 5 == 4`）。这里在真无 `.git` 的树上跑门并断言：
    `worktree` **报 SKIP 而不是 PASS**（"没对象可查"不许写成"查过了"），且离线 SKIP 名集合合法、
    统计行自洽、整棵树 0 FAIL。
    """
    tree = tmp_path / "gitless"
    shutil.copytree(RELEASE_CHECK.REPO_ROOT, tree, ignore=COPY_IGNORE)
    assert not (tree / ".git").exists(), "副本里不该有 .git（否则测的不是发布物形态）"

    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "check_release.py")],
        cwd=str(tree),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    text = proc.stdout or ""
    assert "release check:" in text, "门没产出统计行（子进程原始输出）:\n{}".format(text)

    statuses = status_of(text)
    assert statuses.get("worktree") == RELEASE_CHECK.STATUS_SKIP, (
        "gitless 树上 worktree 应报 SKIP（无对象可查），实际 {}".format(statuses.get("worktree"))
    )
    network_count = len(NETWORK_CHECKS)
    names = assert_offline_skip_set_is_registered(text)
    # 旧口径"离线 SKIP 恰好等于网络类数"在这里**本来就该不成立**：这条断言就是 D1 的现场。
    assert len(names) > network_count, "gitless 树里应出现额外的（已登记的）SKIP 项: {}".format(sorted(names))
    assert proc.returncode == 0, "gitless 树上门应 0 FAIL，实际 exit {}:\n{}".format(proc.returncode, text)


def test_results_do_not_accumulate_across_two_runs(capsys):
    """模块级结果清单忘了 clear 时，第二次跑会"越跑越绿"（结果条数变多、PASS 数上涨）。"""
    first_code, first_rows, _ = run_gate(capsys, ["--repo", str(RELEASE_CHECK.REPO_ROOT)])
    second_code, second_rows, _ = run_gate(capsys, ["--repo", str(RELEASE_CHECK.REPO_ROOT)])
    assert len(first_rows) == len(second_rows), "第二次跑结果条数变了：{} → {}".format(
        len(first_rows), len(second_rows)
    )
    assert first_code == second_code
