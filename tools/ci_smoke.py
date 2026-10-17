"""离线 CI 冒烟：逐条跑「已交付命令」，断言退出码语义，并打印逐条结论表。

为什么要有这个脚本（而不是把断言写进 workflow 内联 shell）：

* 内联脚本受 shell/缩进/行尾影响，本机无法复跑；落成脚本后**本机与 CI 跑的是同一份**；
* 每条命令的断言集中在一处，**里程碑落地时的"退役旧退出码断言 + 补新冒烟"只需改这一处**；
* 「绿的矩阵是证据还是装饰？」——本脚本把"已交付命令"清单与"冒烟覆盖的命令"清单并排数：
  ``tests/test_smoke_coverage.py`` 断言每个已注册子命令都有 ``bare``（console script）与
  ``module``（``python -m s2r``）两种形态的冒烟，且两种形态都真跑过。

用法（仓库根执行）：

    python tools/ci_smoke.py

退出码：0 = 全部符合预期；1 = 有命令不符；3 = 安装面缺失（console script 找不到，
说明「我们自己的包没进安装面」，不得当成"没跑过就算了"）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = str(REPO_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from s2r import cli, errors, stdio  # noqa: E402  （需要先补 sys.path）

BARE = "bare"
MODULE = "module"

#: 临时目录占位符：需要产物落点的命令（如 `generate`/`verify`/`run`）用它——冒烟**不许往仓库里写文件**
#: （K11：只写测试目录；写进本仓库会污染工作区、还会被 EOL/脱敏门扫到）。
TMP_TOKEN = "%TMP%"

#: 「带种子的临时目录」占位符：`%TMP_SEED:<仓库内相对目录>%` —— 现开临时目录并把该目录拷进去，
#: 把**拷贝后的那层**当作命令的目标仓库。为什么需要：`run`/`verify` 要在"编辑前的工作区"里跑
#: RED/GREEN（没有源文件就无从验证），而真实仓库不能作为落点（生成物会写进仓库）。
TMP_SEED_PATTERN = re.compile(r"^%TMP_SEED:([^%]+)%$")

#: 每个子命令的冒烟调用形态（covers = 被覆盖的子命令或 --version；form = 调用形态）。
#: 期望退出码来自 DELIVERED_EXPECT（缺一条即 KeyError，逼你更新冒烟）。
INVOCATIONS = {
    # 用**目录**而不是某个具体样本：语料文件名由确定性 RNG 在两种 harness 间选型，
    # 写死文件名会在某次重生成后失效（首版实测：指向 claude/happy-path-1.jsonl，
    # 而该样本被 RNG 分到了 codex/ 下 → 冒烟报 input_unavailable）。
    "scan": ["data/bench/claude"],
    "bench": [],
    # reconstruct 用**固定 harness 的样本**（multi-bug-1 恒为 Claude 侧），
    # 不依赖 RNG 选型；它同时是 ② 路径（old_string + originalFile）的冒烟面。
    "reconstruct": ["data/bench/claude/multi-bug-1.jsonl"],
    # generate 也是固定 harness 的样本（multi-bug-1 恒为 Claude 侧）；`#<序号>` 是 episode 引用，
    # 序号取该会话里**首个**修复事件的源行号（语料冻结 → 序号也冻结）。
    # 落点用 %TMP%：生成物必须写到测试目录，**不许写进本仓库**（K11），故由 run_one 现开临时目录。
    "generate": ["data/bench/claude/multi-bug-1.jsonl#4", "--out-dir", TMP_TOKEN],
    # verify 的目标仓库用"带种子的临时目录"：隔离副本要在**有源文件**的仓库里跑才谈得上 RED/GREEN，
    # 而生成物（verified 的测试）必须落在仓库外——种子目录同时满足这两条。
    "verify": ["data/bench/claude/structured-patch-1.jsonl#4", "--repo", "%TMP_SEED:data/bench/repo/structured-patch-1%"],
    # run 的全链路：日志根指向**合成语料**（本机没有真实日志也能跑），目标仓库用种子临时目录。
    "run": ["--target", "%TMP_SEED:data/bench/repo/multi-bug-1%", "--claude-root", "data/bench/claude", "--codex-root", "data/bench/codex"],
}

#: 已交付命令的真实期望退出码（五条业务命令全在表里；缺一条 `expected_exit` 会当场 KeyError）
DELIVERED_EXPECT = {
    "scan": errors.EXIT_OK,
    "bench": errors.EXIT_OK,
    "reconstruct": errors.EXIT_OK,
    # multi-bug-1 的第一个 episode 走 ①/② 路径（before 全文可得），diff 是"加守卫"形态
    # → 能推出候选断言 → 至少一份骨架 = 0；推不出来时会是 1（那时才该来改这条登记）。
    "generate": errors.EXIT_OK,
    # structured-patch-1 的候选断言可证伪（修复前 ZeroDivisionError / 修复后返回 0）→ verified ≥ 1
    "verify": errors.EXIT_OK,
    # 目标仓库里 `demo/alpha/src/calc.py` 在位 → 至少一条 verified（其余如实 unverified/needs_manual）
    "run": errors.EXIT_OK,
}


class Smoke(NamedTuple):
    covers: str
    form: str
    argv: Sequence[str]
    expect_exit: int
    note: str


def argv_for(command: str, form: str) -> Sequence[str]:
    head = ["s2r", command] if form == BARE else [sys.executable, "-m", "s2r", command]
    return list(head) + list(INVOCATIONS[command])


def expected_exit(command: str) -> int:
    """期望退出码：必须显式登记（否则 KeyError，逼你在里程碑落地时更新冒烟）。"""
    return DELIVERED_EXPECT[command]


def build_smoke() -> List[Smoke]:
    rows: List[Smoke] = []
    for form in (BARE, MODULE):
        head = ["s2r"] if form == BARE else [sys.executable, "-m", "s2r"]
        rows.append(Smoke("--version", form, head + ["--version"], errors.EXIT_OK, "版本行"))
        rows.append(Smoke("selftest", form, head + ["selftest"], errors.EXIT_OK, "自检全绿"))
        rows.append(Smoke("config", form, head + ["config", "show", "--json"], errors.EXIT_OK, "口径文件机读输出"))
        rows.append(Smoke("config", form, head + ["config", "path"], errors.EXIT_OK, "口径文件定位"))
    for command in sorted(INVOCATIONS):
        for form in (BARE, MODULE):
            rows.append(Smoke(command, form, argv_for(command, form), expected_exit(command), "已交付"))
    return rows


def materialize(argv: Sequence[str], created: List[str]) -> Tuple[List[str], Optional[str]]:
    """把占位符换成真实临时目录；返回 (argv, 错误说明)。

    * ``%TMP%`` → 现开一个空临时目录；
    * ``%TMP_SEED:<rel>%`` → 现开临时目录，并把仓库内的 ``<rel>`` 拷进去（目标仓库 = 拷贝后的那层）。
    """
    out: List[str] = []
    for item in argv:
        if item == TMP_TOKEN:
            path = tempfile.mkdtemp(prefix="s2r-smoke-")
            created.append(path)
            out.append(path)
            continue
        match = TMP_SEED_PATTERN.match(item)
        if not match:
            out.append(item)
            continue
        source = REPO_ROOT.joinpath(*match.group(1).split("/"))
        if not source.is_dir():
            return out, "冒烟种子目录不存在: {}".format(source)
        holder = tempfile.mkdtemp(prefix="s2r-smoke-seed-")
        created.append(holder)
        seeded = Path(holder) / source.name
        shutil.copytree(str(source), str(seeded))
        out.append(str(seeded))
    return out, None


def run_one(row: Smoke, cwd: Path) -> Tuple[bool, int, str]:
    """跑一条冒烟；返回 (是否符合预期, 实际退出码, 输出摘要)。"""
    created: List[str] = []
    argv, problem = materialize(row.argv, created)
    if problem is not None:
        for path in created:
            shutil.rmtree(path, ignore_errors=True)
        return False, -1, problem
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except FileNotFoundError:
        return False, -1, "命令不存在: {}".format(argv[0])
    except subprocess.TimeoutExpired:
        return False, -2, "超时"
    finally:
        for path in created:
            shutil.rmtree(path, ignore_errors=True)
    output = (proc.stdout or "").replace("\r\n", "\n").strip()
    tail = " | ".join(line for line in output.split("\n") if line)[-200:]
    return proc.returncode == row.expect_exit, proc.returncode, tail


def main(argv: Optional[Sequence[str]] = None) -> int:
    stdio.ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="ci_smoke", description="离线 CI 冒烟（本机与 CI 同源）")
    parser.add_argument("--cwd", metavar="DIR", default=str(REPO_ROOT), help="在哪个目录跑（默认仓库根）")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    console_script = shutil.which("s2r")
    if console_script is None:
        sys.stderr.write("dependency_missing: 找不到 console script s2r\n")
        sys.stderr.write("hint: 先运行 pip install -e . —— 安装面缺我们自己的包等于冒烟从未跑过，不是「跳过」\n")
        return errors.EXIT_DEPENDENCY_MISSING

    cwd = Path(args.cwd)
    # 脚本自己也数一遍覆盖面：新增子命令却忘了补冒烟 → 这里当场报出来（不是靠人眼）
    registered = set(cli.registered_commands()) - {"selftest", "config"}
    missing = sorted(registered - set(INVOCATIONS))
    if missing:
        sys.stderr.write("check_failed: 冒烟表缺命令: {}\n".format(", ".join(missing)))
        return errors.EXIT_CHECK_FAILED
    rows = build_smoke()
    failures = 0
    sys.stdout.write("console script: {}\n".format(console_script))
    sys.stdout.write("{:<4} {:<8} {:<12} {:<8} {:<8} {}\n".format("#", "form", "command", "expect", "actual", "verdict"))
    for index, row in enumerate(rows, start=1):
        ok, actual, tail = run_one(row, cwd)
        failures += 0 if ok else 1
        sys.stdout.write(
            "{:<4} {:<8} {:<12} {:<8} {:<8} {}\n".format(
                index, row.form, row.covers, row.expect_exit, actual, "ok" if ok else "MISMATCH"
            )
        )
        if not ok:
            sys.stdout.write("       输出: {}\n".format(tail))
    sys.stdout.write("smoke: {}/{} 符合预期\n".format(len(rows) - failures, len(rows)))
    return errors.EXIT_OK if failures == 0 else errors.EXIT_CHECK_FAILED


if __name__ == "__main__":
    sys.exit(main())
