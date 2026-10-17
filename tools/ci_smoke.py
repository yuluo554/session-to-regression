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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = str(REPO_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from s2r import cli, errors, stdio  # noqa: E402  （需要先补 sys.path）

BARE = "bare"
MODULE = "module"

#: 每个子命令的冒烟调用形态（covers = 被覆盖的子命令或 --version；form = 调用形态）。
#: 期望退出码**自动推导**：仍在「阶段未就绪」表里的命令 = 4；已交付的必须在此登记真实期望值。
#: 里程碑落地时：把命令从 `cli.STAGED_COMMANDS` 摘除 → 这里必须补一条 DELIVERED_EXPECT，
#: 并且**在同一次提交里**退役 README 退出码表里对应的旧语义。
INVOCATIONS = {
    # 用**目录**而不是某个具体样本：语料文件名由确定性 RNG 在两种 harness 间选型，
    # 写死文件名会在某次重生成后失效（首版实测：指向 claude/happy-path-1.jsonl，
    # 而该样本被 RNG 分到了 codex/ 下 → 冒烟报 input_unavailable）。
    "scan": ["data/bench/claude"],
    "bench": [],
    "reconstruct": ["sess-demo-0001"],
    "generate": ["sess-demo-0001#1"],
    "verify": ["sess-demo-0001#1"],
    "run": ["--target", "demo-target"],
}

#: 已交付命令的真实期望退出码（未交付的走 STAGED_COMMANDS → 4，由 build_smoke 推导）
DELIVERED_EXPECT = {
    "scan": errors.EXIT_OK,
    "bench": errors.EXIT_OK,
}


class Smoke(NamedTuple):
    covers: str
    form: str
    argv: Sequence[str]
    expect_exit: int
    note: str


def _staged_argv(command: str, form: str) -> Sequence[str]:
    head = ["s2r", command] if form == BARE else [sys.executable, "-m", "s2r", command]
    return list(head) + list(INVOCATIONS[command])


def expected_exit(command: str) -> int:
    """期望退出码：未交付 → 阶段未就绪；已交付 → 必须显式登记（否则 KeyError，逼你更新冒烟）。"""
    if command in cli.STAGED_COMMANDS:
        return errors.EXIT_STAGE_NOT_READY
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
        staged = command in cli.STAGED_COMMANDS
        note = "阶段未就绪（{}）".format(cli.STAGED_COMMANDS[command]) if staged else "已交付"
        for form in (BARE, MODULE):
            rows.append(Smoke(command, form, _staged_argv(command, form), expected_exit(command), note))
    return rows


def run_one(row: Smoke, cwd: Path) -> Tuple[bool, int, str]:
    """跑一条冒烟；返回 (是否符合预期, 实际退出码, 输出摘要)。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            list(row.argv),
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except FileNotFoundError:
        return False, -1, "命令不存在: {}".format(row.argv[0])
    except subprocess.TimeoutExpired:
        return False, -2, "超时"
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
