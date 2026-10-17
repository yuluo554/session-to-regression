"""合成会话基准生成器（薄 CLI）：参照实现在 `s2r.benchdata`（随包发布，装好后可用）。

用法：

    python tools/gen_bench.py             # 生成到 data/bench（已有非空目录需 --force）
    python tools/gen_bench.py --check     # 重生成到临时目录 → 与冻结物逐字节比对
    python tools/gen_bench.py --out DIR   # 生成到指定目录
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = str(_REPO_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from s2r import benchdata  # noqa: E402
from s2r import config as config_mod  # noqa: E402
from s2r import stdio  # noqa: E402

NEWLINE = chr(10)


def main(argv: Optional[Sequence[str]] = None) -> int:
    stdio.ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="gen_bench", description="合成会话基准生成器（确定性、可 --check）")
    parser.add_argument("--out", metavar="DIR", default=None, help="输出目录（默认 data/bench）")
    parser.add_argument("--check", action="store_true", help="重生成到临时目录并与冻结物逐字节比对（不改动冻结物）")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的非空输出目录（重建冻结语料才需要）")
    parser.add_argument("--config", metavar="PATH", default=None, help="唯一数值口径文件")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    cfg = config_mod.load_config(args.config)
    frozen = Path(args.out) if args.out else _REPO_ROOT.joinpath(*benchdata.BENCH_RELATIVE)

    if args.check:
        with tempfile.TemporaryDirectory(prefix="s2r-bench-") as tmp:
            stats = benchdata.write_corpus(Path(tmp), cfg)
            if not frozen.is_dir():
                sys.stdout.write("bench --check: 冻结语料不存在: {}{}".format(frozen, NEWLINE))
                return 1
            problems = benchdata.diff_trees(frozen, Path(tmp))
        if problems:
            for problem in problems:
                sys.stdout.write(problem + NEWLINE)
            sys.stdout.write("bench --check: 不一致（{} 处）{}".format(len(problems), NEWLINE))
            return 1
        sys.stdout.write(
            "bench --check: 与冻结物逐字节一致（{} 个样本，rng_draws={}）{}".format(stats["samples"], stats["draws"], NEWLINE)
        )
        return 0

    stats = benchdata.write_corpus(frozen, cfg, force=args.force)
    sys.stdout.write(
        "bench: 生成 {} 个样本 → {}（rng_draws={}，manifest sha256={}）{}".format(
            stats["samples"], frozen, stats["draws"], stats["manifest_sha256"][:16], NEWLINE
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
