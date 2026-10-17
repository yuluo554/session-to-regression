"""CLI：本项目唯一交付形态的入口（console script ``s2r`` 与 ``python -m s2r`` 同源）。

命令面即验收面——每条命令的语义、退出码、产物落点都锁进测试：

==============================  ==============================================
命令                            状态
==============================  ==============================================
``s2r --version``               已交付
``s2r selftest``                已交付（可证伪：坏配置必红）
``s2r config show|path``        已交付（唯一数值口径文件的读取出口）
``s2r scan``                    M1 双 harness 读取器 + 事件流
``s2r bench``                   M1 合成会话基准（植入真值）
``s2r reconstruct``             M3 差分还原器
``s2r generate``                M4 pytest 骨架生成器
``s2r verify`` / ``s2r run``    M5 RED/GREEN 验证 + manifest
==============================  ==============================================

未交付命令**不产出任何结果**，只报 ``stage_not_ready`` 与 exit 4（绝不伪造空结果）；
每个里程碑落地时该退出码整项退役（CLI + 文档 + 测试三处同步，见 plan/04）。
"""

from __future__ import annotations

import argparse
import importlib.util
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from . import __version__, config as config_mod, events, serialize, stdio
from .errors import (EXIT_CHECK_FAILED, EXIT_DEPENDENCY_MISSING, EXIT_OK, EXIT_STAGE_NOT_READY, EXIT_USAGE,
                     InputUnavailable, S2RError, StageNotReady, UsageError)

PROG = "s2r"

#: 尚未交付的子命令 → 所属里程碑（里程碑落地时整项摘除）
STAGED_COMMANDS: Dict[str, str] = {
    "scan": "M1",
    "bench": "M1",
    "reconstruct": "M3",
    "generate": "M4",
    "verify": "M5",
    "run": "M5",
}


class Check(NamedTuple):
    name: str
    ok: bool
    detail: str


#: 依赖类自检项的名字：它单独决定 exit 3，不计入"自检失败"（exit 1）
PYTEST_CHECK_NAME = "pytest_available"


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #


def cmd_selftest(args: argparse.Namespace) -> int:
    """本机环境与骨架契约自检。可证伪：坏配置 → exit 1；缺 pytest → exit 3。"""
    config_path = config_mod.resolve_config_path(args.config)
    checks: List[Check] = []
    config: Dict[str, Any] = {}

    try:
        config = config_mod.load_config(config_path)
    except S2RError as exc:
        checks.append(Check("config_load", False, "; ".join(exc.lines())))
    else:
        checks.append(Check("config_load", True, str(config_path)))
        checks.append(_check_config_copies(config_path))
        checks.append(_check_vocabulary(config))
        checks.append(_check_serialize_determinism(config))
        checks.append(_check_events_roundtrip())
        checks.append(_check_harness_detection())

    pytest_ok = pytest_available()
    if not pytest_ok:
        checks.append(Check(PYTEST_CHECK_NAME, False, "pytest 不可用（RED/GREEN 验证无法进行）"))

    payload = {
        "version": __version__,
        "python": platform.python_version(),
        "config_path": str(config_path),
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
    }

    # 依赖缺失（3）与自检失败（1）是两种不同语义：先判自检，再判依赖，别让依赖缺失
    # 被自己的 FAIL 行吞成 1（那会让 CI 分不清"环境缺件"和"契约坏了"）。
    failed = [c for c in checks if not c.ok and c.name != PYTEST_CHECK_NAME]
    payload["status"] = "failed" if failed else ("dependency_missing" if not pytest_ok else "ok")

    if getattr(args, "json", False):
        sys.stdout.write(serialize.dumps_json(payload))
    else:
        for check in checks:
            sys.stdout.write("[{}] {} {}\n".format("ok" if check.ok else "FAIL", check.name, check.detail))
        sys.stdout.write("status: {}\n".format(payload["status"]))

    if failed:
        return EXIT_CHECK_FAILED
    if not pytest_ok:
        return EXIT_DEPENDENCY_MISSING
    return EXIT_OK


def cmd_config_show(args: argparse.Namespace) -> int:
    config = config_mod.load_config(args.config)
    rows = config_mod.describe(config)
    if getattr(args, "json", False):
        sys.stdout.write(serialize.dumps_json({key: value for key, value in rows}))
        return EXIT_OK
    sys.stdout.write("config: {}\n".format(config["__path__"]))
    for key, value in rows:
        sys.stdout.write("{} = {}\n".format(key, _render_value(value)))
    return EXIT_OK


def cmd_config_path(args: argparse.Namespace) -> int:
    sys.stdout.write("{}\n".format(config_mod.resolve_config_path(args.config)))
    return EXIT_OK


def cmd_config_bare(args: argparse.Namespace) -> int:
    raise UsageError("config 需要子命令: show 或 path")


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return "[{}]".format(", ".join(_render_value(item) for item in value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --------------------------------------------------------------------------- #
# 自检子检查
# --------------------------------------------------------------------------- #


def pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _check_config_copies(config_path: Path) -> Check:
    packaged = config_mod.package_config_path()
    if not packaged.is_file():
        return Check("config_copies_identical", False, "随包副本缺失: {}".format(packaged))
    try:
        repo = config_mod.repo_config_path(start=Path(__file__).resolve().parent)
    except OSError:
        repo = None
    if repo is None or repo == packaged:
        return Check("config_copies_identical", True, "单形态（随包副本）: {}".format(packaged))
    left = serialize.sha256_file(packaged)
    right = serialize.sha256_file(repo)
    ok = left == right
    return Check("config_copies_identical", ok, "{} vs {}: {}".format(packaged.name, repo, "same" if ok else "DIFF"))


def _check_vocabulary(config: Dict[str, Any]) -> Check:
    vocab = config_mod.vocabulary(config)
    empty = sorted(key for key, value in vocab.items() if not value)
    if empty:
        return Check("vocabulary_declared", False, "空词表: {}".format(", ".join(empty)))
    return Check("vocabulary_declared", True, "口径文件内定义: {}".format(", ".join(sorted(vocab))))


def _check_serialize_determinism(config: Dict[str, Any]) -> Check:
    first = serialize.dumps_json(config)
    second = serialize.dumps_json(config)
    if first != second:
        return Check("serialize_deterministic", False, "同输入双跑字节不一致")
    with tempfile.TemporaryDirectory(prefix="s2r-selftest-") as tmp:
        one = serialize.write_json_atomic(Path(tmp) / "a.json", config)
        two = serialize.write_json_atomic(Path(tmp) / "b.json", config)
        if serialize.sha256_file(one) != serialize.sha256_file(two):
            return Check("serialize_deterministic", False, "同输入双写字节不一致")
    return Check("serialize_deterministic", True, "同输入双跑/双写逐字节一致")


def _check_events_roundtrip() -> Check:
    sample = events.Event(
        source="claude",
        session_id="sess-selftest",
        seq=1,
        kind="user_msg",
        ts="1970-01-01T00:00:00Z",
        payload={"text": "自检样例"},
    )
    if sample.to_dict() != events.Event.from_json(sample.to_json()).to_dict():
        return Check("events_ir_roundtrip", False, "Event 序列化往返不一致")
    if sample.to_json() != events.Event.from_dict(sample.to_dict()).to_json():
        return Check("events_ir_roundtrip", False, "Event 双跑字节不一致")
    return Check("events_ir_roundtrip", True, "字段集 {} 往返一致".format(len(events.EVENT_FIELDS)))


def _check_harness_detection() -> Check:
    claude = events.detect_harness({"type": "user", "message": {"role": "user"}})
    codex = events.detect_harness({"type": "session_meta", "payload": {"id": "x"}})
    if (claude, codex) != ("claude", "codex"):
        return Check("harness_detection", False, "首行形态判别结果异常: {} / {}".format(claude, codex))
    try:
        events.detect_harness({"nonsense": True})
    except S2RError:
        return Check("harness_detection", True, "已知两种形态可判别，未知形态显式报错")
    return Check("harness_detection", False, "未知首行形态未报错（静默归类风险）")


# --------------------------------------------------------------------------- #
# 未交付命令
# --------------------------------------------------------------------------- #


def _make_staged_handler(command: str):
    milestone = STAGED_COMMANDS[command]

    def handler(args: argparse.Namespace) -> int:  # noqa: ARG001 - 契约占位，参数由里程碑消费
        raise StageNotReady(command=PROG + " " + command, milestone=milestone, version=__version__)

    handler.__name__ = "cmd_staged_" + command
    return handler


# --------------------------------------------------------------------------- #
# 参数面
# --------------------------------------------------------------------------- #


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="唯一数值口径文件（默认按 --config > S2R_CONFIG > config/scoring.toml > 随包副本 的顺序定位）",
    )


def add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--claude-root", metavar="DIR", default=None, help="Claude Code 日志根目录覆盖（不硬编码用户名）")
    parser.add_argument("--codex-root", metavar="DIR", default=None, help="Codex 日志根目录覆盖（不硬编码用户名）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="从本机 agent 会话日志切出 bug-fix episode，把修复 diff 反推成可复跑的 pytest 骨架（纯本地、零上传）",
    )
    parser.add_argument("--version", action="version", version="{} {} (python {})".format(PROG, __version__, platform.python_version()))
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    selftest = sub.add_parser("selftest", help="本机环境与骨架契约自检")
    add_config_arg(selftest)
    selftest.add_argument("--json", action="store_true", help="输出机读 JSON")
    selftest.set_defaults(handler=cmd_selftest)

    # --config 只挂在叶子命令上：argparse 的子解析器默认值会覆盖父解析器已解析的值，
    # 挂两层会埋"看起来传了、其实被默认值覆盖"的坑。
    config_parser = sub.add_parser("config", help="查看唯一数值口径文件")
    config_parser.set_defaults(handler=cmd_config_bare)
    config_sub = config_parser.add_subparsers(dest="config_command", metavar="SUBCOMMAND")
    config_show = config_sub.add_parser("show", help="打印全部口径值（人读/机读双轨）")
    add_config_arg(config_show)
    config_show.add_argument("--json", action="store_true", help="输出机读 JSON")
    config_show.set_defaults(handler=cmd_config_show)
    config_path = config_sub.add_parser("path", help="只打印解析到的配置文件路径")
    add_config_arg(config_path)
    config_path.set_defaults(handler=cmd_config_path)

    scan = sub.add_parser("scan", help="M1：读日志 → 归一事件流（+ episode 候选）")
    scan.add_argument("inputs", nargs="+", metavar="LOG", help="jsonl 日志文件或目录")
    add_source_args(scan)
    scan.add_argument("--out", metavar="FILE", default=None, help="事件流产物落点（不传则打 stdout）")
    add_config_arg(scan)
    scan.set_defaults(handler=_make_staged_handler("scan"))

    bench = sub.add_parser("bench", help="M1：合成会话基准 + 植入真值对账")
    bench.add_argument("--only", nargs="*", default=None, metavar="TYPE", help="只跑指定 episode 类型")
    bench.add_argument("--out-dir", metavar="DIR", default=None, help="中间产物与报告落点（可单步复查）")
    bench.add_argument("--check", action="store_true", help="只做再生成位级一致校验")
    add_config_arg(bench)
    bench.set_defaults(handler=_make_staged_handler("bench"))

    reconstruct = sub.add_parser("reconstruct", help="M3：before/after 差分还原（logs-only，不依赖 git）")
    reconstruct.add_argument("session", metavar="SESSION", help="会话引用（source:session_id 或日志路径）")
    reconstruct.add_argument("--repo", metavar="DIR", default=None, help="可选目标仓库根（用于对齐磁盘文件）")
    reconstruct.add_argument("--out", metavar="FILE", default=None, help="还原产物落点（不传则打 stdout）")
    add_config_arg(reconstruct)
    reconstruct.set_defaults(handler=_make_staged_handler("reconstruct"))

    generate = sub.add_parser("generate", help="M4：生成 pytest 回归骨架")
    generate.add_argument("episode", metavar="EPISODE_ID", help="episode_id")
    generate.add_argument("--repo", metavar="DIR", default=None, help="目标仓库根（生成物只写其测试目录）")
    generate.add_argument("--out-dir", metavar="DIR", default=None, help="输出目录覆盖（隔离验证用）")
    add_config_arg(generate)
    generate.set_defaults(handler=_make_staged_handler("generate"))

    verify = sub.add_parser("verify", help="M5：RED/GREEN 双验证（隔离副本内）")
    verify.add_argument("episode", metavar="EPISODE_ID", help="episode_id")
    verify.add_argument("--repo", metavar="DIR", default=None, help="目标仓库根")
    verify.add_argument("--timeout", type=int, default=None, metavar="SECONDS", help="单例超时（缺省取口径文件，代码内不留默认数值）")
    verify.add_argument("--out-dir", metavar="DIR", default=None, help="中间产物落点")
    add_config_arg(verify)
    verify.set_defaults(handler=_make_staged_handler("verify"))

    run = sub.add_parser("run", help="M5：全链路一条命令（读取 → 切分 → 还原 → 生成 → 验证 → manifest）")
    run.add_argument("--target", metavar="DIR", required=True, help="目标仓库根（测试与 manifest 落点）")
    add_source_args(run)
    run.add_argument("--out-dir", metavar="DIR", default=None, help="中间产物落点")
    add_config_arg(run)
    run.set_defaults(handler=_make_staged_handler("run"))

    return parser


def registered_commands(parser: Optional[argparse.ArgumentParser] = None) -> Tuple[str, ...]:
    """已注册的子命令集合（供 CI 冒烟覆盖面对账使用）。"""
    built = parser if parser is not None else build_parser()
    for action in built._actions:  # noqa: SLF001 - argparse 无公开读取子命令的接口
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return tuple(sorted(action.choices))
    return ()


def main(argv: Optional[Sequence[str]] = None) -> int:
    stdio.ensure_utf8_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    except SystemExit as exc:  # --help/--version → 0；用法错误（含未知子命令）→ 2
        code = exc.code
        return int(code) if isinstance(code, int) else EXIT_USAGE

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_usage(sys.stderr)
        sys.stderr.write("{}: 缺子命令\n".format(PROG))
        return EXIT_USAGE

    try:
        return int(handler(args))
    except S2RError as exc:
        for line in exc.lines():
            sys.stderr.write(line + "\n")
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
