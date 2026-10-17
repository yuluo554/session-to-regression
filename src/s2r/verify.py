"""M5：RED/GREEN 隔离验证 + manifest 证据链（**不验证不落盘**）。

一个骨架写完不等于它有用：只有当它在"修复前"真的失败、在"修复后"真的通过时，它才是一次修复的
**回归证明**。本模块把这件事做成可复核的结论，四条纪律（每条都有测试）：

1. **隔离**：验证在目标仓库的**临时副本**里跑（`[verify].tmpdir_prefix` 前缀），
   用户工作区一个字节都不动（K11）——副本内的源码由本模块按日志还原出的 `before`/`after` 覆盖；
2. **不验证不落盘**：只有 `verified`（修复前失败 **且** 修复后通过）的测试才写进目标仓库的测试目录；
   `unverified` 只记原因码（测试未失败 / 修复后仍失败 / 环境不可用 / 超时），`needs_manual` 记生成层原因；
3. **判据只读口径文件**：命令（`pytest_command`，含解释器占位）、超时、两个期望值、日志摘录上限
   全部取自 `[verify]`，代码内没有任何默认数值；
4. **确定性**：无时钟、无随机；生成时间取**会话日志自身的 `end_ts`**，日志摘录只保留
   `E`/`FAILED`/`ERROR` 前缀的行（pytest 的头部含平台/版本/耗时/临时目录，进 manifest 就会让
   "同输入双跑逐字节一致"当场失效）。

三态不折叠（与 M3 的四态、M4 的两态同一条纪律）：`verified` / `unverified` / `needs_manual`
各自显式可见，绝不把"没验过"折成"通过"。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import __version__
from . import benchdata, config as config_mod, events, generate as generate_mod, reconstruct as reconstruct_mod, serialize
from .errors import DependencyMissing, InputUnavailable, UsageError

#: manifest 产物 schema 版本（对外契约；字段集变化即升版）
SCHEMA_VERSION = 1

#: 结论三态（不折叠）
STATUS_VERIFIED = "verified"
STATUS_UNVERIFIED = "unverified"
STATUS_NEEDS_MANUAL = "needs_manual"
STATUSES = (STATUS_VERIFIED, STATUS_UNVERIFIED, STATUS_NEEDS_MANUAL)

#: `unverified` 的四类原因（DoD：每类各有一条测试）
REASON_NONE = ""
REASON_RED_NOT_FAILED = "red_not_failed"
REASON_GREEN_NOT_PASSED = "green_not_passed"
REASON_ENVIRONMENT_UNAVAILABLE = "environment_unavailable"
REASON_TIMEOUT = "timeout"
UNVERIFIED_REASONS = (REASON_RED_NOT_FAILED, REASON_GREEN_NOT_PASSED, REASON_ENVIRONMENT_UNAVAILABLE, REASON_TIMEOUT)

#: 单次跑测试的结局（由退出码语义判定，不由输出文本判定）
OUTCOME_PASSED = "passed"
OUTCOME_FAILED = "failed"
OUTCOME_ENVIRONMENT = "environment"
OUTCOME_TIMEOUT = "timeout"

#: pytest 退出码语义（0 = 通过、1 = 有测试失败；其余码属"环境/用法"问题，不当作判定依据）
PYTEST_EXIT_PASSED = 0
PYTEST_EXIT_TESTS_FAILED = 1

#: 口径文件里的期望值 → 它要求的结局（键集合必须与 `config.VERIFY_EXPECTATIONS` 一致，测试锁死）
EXPECTATION_OUTCOMES = {
    "must_fail": OUTCOME_FAILED,
    "must_pass": OUTCOME_PASSED,
}

#: 副本不复制的东西（版本库与缓存：既省时间，也避免把用户 git 索引带进副本）
COPY_IGNORE = (".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", "node_modules")

#: 子进程环境显式项（K7：编码与环境不依赖父进程）
CHILD_ENV = {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}

#: 日志摘录保留的行前缀（pytest 的失败明细与短摘要；不含头部/耗时/平台信息）
EXCERPT_PREFIXES = ("E ", "FAILED ", "ERROR ")


# --------------------------------------------------------------------------- #
# 输入面：口径值 → 命令 / 超时
# --------------------------------------------------------------------------- #


def pytest_available() -> bool:
    """pytest 是否可用（`selftest` 与 `verify`/`run` 的依赖门共用一个判据）。"""
    return importlib.util.find_spec("pytest") is not None


def resolve_command(cfg: Mapping[str, Any]) -> List[str]:
    """口径文件里的 `pytest_command` → 真正可执行的 argv。

    只做一件事：把 `[verify].interpreter_token` 换成**当前解释器**（`sys.executable`）。
    为什么不留裸 `python`：装机后裸 `python` 未必在 PATH 上（本机实测 Windows 的
    App Execution Alias 会直接返回非 0），照抄会把"环境缺件"伪装成"测试没失败"。
    命令的其余部分逐字来自口径文件，代码内不留第二个默认值。
    """
    params = config_mod.verify_params(cfg)
    token = str(params["interpreter_token"])
    return [sys.executable if str(item) == token else str(item) for item in params["pytest_command"]]


def timeout_of(cfg: Mapping[str, Any], override: Optional[int] = None) -> int:
    """单例超时：显式覆盖优先，否则取口径文件（代码内不留默认数值）。"""
    if override is None:
        return int(config_mod.verify_params(cfg)["timeout_seconds"])
    seconds = int(override)
    if seconds < 1:
        raise UsageError("超时必须 ≥ 1 秒: {}".format(override), hint="口径文件 [verify].timeout_seconds 是缺省来源")
    return seconds


def coverage_thresholds(data_dir: Optional[str] = None) -> Tuple[Dict[str, int], str]:
    """覆盖度门槛**自基准 manifest 下发**（`coverage.min_count_by_type`）。

    定位不到（例如装好的包不在源码树里）就返回空表 + 空来源，**绝不编造阈值**——
    "没有门槛"与"门槛是 0"是两件事，前者必须显式可见。
    """
    base = Path(data_dir) if data_dir else benchdata.REPO_ROOT.joinpath(*benchdata.BENCH_RELATIVE)
    path = base / benchdata.MANIFEST_NAME
    if not _is_file(path):
        return {}, ""
    try:
        manifest = json.loads(serialize.read_text(path))
    except ValueError:
        return {}, ""
    coverage = manifest.get("coverage") if isinstance(manifest, Mapping) else None
    thresholds = coverage.get("min_count_by_type") if isinstance(coverage, Mapping) else None
    if not isinstance(thresholds, Mapping):
        return {}, ""
    return {str(key): int(value) for key, value in sorted(thresholds.items())}, _relative_label(path)


def _relative_label(path: Path) -> str:
    try:
        return str(path.relative_to(benchdata.REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# 计划面：episode × 骨架 × 还原结果
# --------------------------------------------------------------------------- #


@dataclass
class Plan:
    """待验证的一件：episode 实体 + 它的骨架 + 它的还原结果（三者来自同一条生成路径）。"""

    episode: Any
    skeleton: generate_mod.Skeleton
    files: Sequence[reconstruct_mod.Reconstruction]

    @property
    def episode_id(self) -> str:
        return str(getattr(self.episode, "episode_id", events.UNKNOWN))


def plans_of_session(file_read: Any, cfg: Mapping[str, Any], repo: Optional[str] = None) -> List[Plan]:
    """一个会话 → 逐 episode 的待验证件（复用 M4 的同一条规划路径）。"""
    return [Plan(episode, skeleton, files) for episode, skeleton, files in generate_mod.plan_session_detailed(file_read, cfg, repo=repo)]


@dataclass
class Step:
    """一次 pytest 跑的结局（判定只看退出码；摘录供人读）。"""

    expectation: str
    exit_code: Optional[int]
    outcome: str
    excerpt: str
    truncated: bool

    def satisfied(self) -> bool:
        """该步是否满足口径文件里声明的期望（期望值认不出来即报错，不静默放行）。"""
        if self.expectation not in EXPECTATION_OUTCOMES:
            raise UsageError(
                "口径文件的期望值认不出来: {}".format(self.expectation),
                hint="[verify].red_expectation / green_expectation 只认 {}".format("/".join(sorted(EXPECTATION_OUTCOMES))),
            )
        return self.outcome == EXPECTATION_OUTCOMES[self.expectation]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expectation": self.expectation,
            "exit_code": self.exit_code,
            "outcome": self.outcome,
            "excerpt": self.excerpt,
            "truncated": self.truncated,
        }


@dataclass
class Verdict:
    """一个 episode 的验证结论（写盘内容与"为什么没写"都在这）。"""

    episode_id: str
    session_id: str
    source: str
    source_file: str
    generated_at: str
    episode_status: str
    generation_status: str
    generation_reason: str
    assumed_convention: bool
    assumed_parts: List[str]
    test_path: str
    status: str
    reason: str
    files: List[Dict[str, Any]] = field(default_factory=list)
    red: Optional[Step] = None
    green: Optional[Step] = None
    content: str = ""
    written: bool = False

    def fact(self) -> Dict[str, Any]:
        """对账用的事实子集（真值只声明这几项；键集合由测试锁死）。"""
        return {
            "episode_id": self.episode_id,
            "status": self.status,
            "reason": self.reason,
            "red_exit": self.red.exit_code if self.red is not None else None,
            "green_exit": self.green.exit_code if self.green is not None else None,
        }

    def to_dict(self) -> Dict[str, Any]:
        """manifest 条目：**不含骨架源码**（源码落点在 `test_path`，manifest 只记证据）。"""
        return {
            "episode_id": self.episode_id,
            "session_id": self.session_id,
            "source": self.source,
            "source_file": self.source_file,
            "generated_at": self.generated_at,
            "generator_version": __version__,
            "episode_status": self.episode_status,
            "generation_status": self.generation_status,
            "generation_reason": self.generation_reason,
            "assumed_convention": self.assumed_convention,
            "assumed_parts": list(self.assumed_parts),
            "test_path": self.test_path,
            "written": self.written,
            "status": self.status,
            "reason": self.reason,
            "files": [dict(item) for item in self.files],
            "red": self.red.to_dict() if self.red is not None else None,
            "green": self.green.to_dict() if self.green is not None else None,
        }


def red_green_facts(verdicts: Sequence[Verdict]) -> List[Dict[str, Any]]:
    """基准对账的事实面：逐 episode 的结论 + 两个退出码（顺序即骨架产出顺序）。"""
    return [verdict.fact() for verdict in verdicts]


# --------------------------------------------------------------------------- #
# 验证：隔离副本内 RED → GREEN
# --------------------------------------------------------------------------- #


def verify_plans(plans: Sequence[Plan], cfg: Mapping[str, Any], repo: Optional[str], timeout: Optional[int] = None) -> List[Verdict]:
    """一批待验证件 → 逐件结论（一个隔离副本）。"""
    return verify_many([plans], cfg, repo, timeout=timeout)


def verify_session(file_read: Any, cfg: Mapping[str, Any], repo: Optional[str], timeout: Optional[int] = None) -> List[Verdict]:
    """一个会话 → 逐 episode 的验证结论（`s2r verify` 的单件入口）。"""
    return verify_plans(plans_of_session(file_read, cfg, repo=repo), cfg, repo, timeout=timeout)


def unreachable_verdicts(plans: Sequence[Plan]) -> List[Verdict]:
    """**没有目标仓库**时的结论面（基准与批量场景用；不跑任何子进程）。

    两种情形分开说，不折叠：生成层本来就没产出内容 → 照生成层的原因记 `needs_manual`；
    生成了内容却没有仓库可验证 → `unverified` + 环境不可用（**不当作通过**）。
    """
    out: List[Verdict] = []
    for plan in plans:
        if not plan.skeleton.content:
            out.append(_make_verdict(plan, STATUS_NEEDS_MANUAL, plan.skeleton.reason, None, None))
        else:
            out.append(_make_verdict(plan, STATUS_UNVERIFIED, REASON_ENVIRONMENT_UNAVAILABLE, None, None))
    return out


def verify_many(
    batches: Sequence[Sequence[Plan]],
    cfg: Mapping[str, Any],
    repo: Optional[str],
    timeout: Optional[int] = None,
) -> List[Verdict]:
    """多批待验证件共用**同一份隔离副本**（`run` 的全量扫描走这条）。

    每件验证前都会把自己的 `before`/`after` 重新写进副本，所以批与批之间不会互相污染判定；
    共用副本省掉"每件拷一次仓库"的开销。
    """
    if not pytest_available():
        raise DependencyMissing(
            "pytest 不可用：RED/GREEN 验证无法进行（不得退化成零结果 + exit 0）",
            hint="装上 pytest（dev extras 里有）后重试",
        )
    params = config_mod.verify_params(cfg)
    command = resolve_command(cfg)
    seconds = timeout_of(cfg, timeout)
    limit = int(params["log_excerpt_chars"])

    plans = [plan for batch in batches for plan in batch]
    if not [plan for plan in plans if plan.skeleton.content]:
        # 没有可跑的东西 → 不需要目标仓库（生成层没产出内容时，缺仓库不是问题）
        return [_make_verdict(plan, STATUS_NEEDS_MANUAL, plan.skeleton.reason, None, None) for plan in plans]

    root = _require_repo(repo)
    isolated = _make_isolated(root, params)
    try:
        verdicts: List[Verdict] = []
        for batch in batches:
            for plan in batch:
                if not plan.skeleton.content:
                    verdicts.append(
                        _make_verdict(plan, STATUS_NEEDS_MANUAL, plan.skeleton.reason, None, None)
                    )
                    continue
                verdicts.append(_verify_one(plan, cfg, isolated, command, seconds, limit))
        return verdicts
    finally:
        shutil.rmtree(str(isolated), ignore_errors=True)


def _require_repo(repo: Optional[str]) -> Path:
    if not repo:
        raise UsageError(
            "缺少目标仓库：验证要在它的**隔离副本**里跑，所以必须先知道仓库在哪",
            hint="给 --repo（verify）/ --target（run）指向目标仓库根",
        )
    root = Path(repo)
    if not _is_dir(root):
        raise InputUnavailable("目标仓库不存在或不是目录: {}".format(repo))
    return root


def _make_isolated(root: Path, params: Mapping[str, Any]) -> Path:
    """目标仓库 → 临时副本（前缀取口径文件；复制时跳过版本库与缓存目录）。"""
    base = Path(tempfile.mkdtemp(prefix=str(params["tmpdir_prefix"])))
    isolated = base / root.name if root.name else base / "repo"
    shutil.copytree(str(root), str(isolated), ignore=shutil.ignore_patterns(*COPY_IGNORE))
    return isolated


def _verify_one(plan: Plan, cfg: Mapping[str, Any], isolated: Path, command: Sequence[str], seconds: int, limit: int) -> Verdict:
    """一件：写测试 → 覆盖 before → 跑 → 覆盖 after → 跑 → 三态结论。"""
    skeleton = plan.skeleton
    states = _episode_states(plan, isolated)
    if states is None:
        # 目标仓库里定位不到该文件：绝不"新建"一个猜出来的路径（那是在改用户仓库）
        return _make_verdict(plan, STATUS_UNVERIFIED, REASON_ENVIRONMENT_UNAVAILABLE, None, None)

    test_rel = str(skeleton.relative_path).replace("\\", "/")
    serialize.write_text_atomic(isolated.joinpath(*test_rel.split("/")), skeleton.content)

    params = config_mod.verify_params(cfg)
    red = _run_state(states, "before", str(params["red_expectation"]), command, isolated, test_rel, seconds, limit)
    green = _run_state(states, "after", str(params["green_expectation"]), command, isolated, test_rel, seconds, limit)
    status, reason = _classify(red, green)
    return _make_verdict(plan, status, reason, red, green)


def _episode_states(plan: Plan, isolated: Path) -> Optional[List[Tuple[str, str, str]]]:
    """→ `[(副本内相对路径, before 全文, after 全文)]`；任一路径映射不出来 → None。

    同一文件在同一个 episode 里被改多次时：`before` 取**最早**那条（episode 起点状态）、
    `after` 取**最晚**那条（episode 终点状态）——这正是"一个 episode 一次修复"的语义。
    """
    by_path: Dict[str, List[reconstruct_mod.Reconstruction]] = {}
    for item in plan.files:
        if item.status != reconstruct_mod.STATUS_OK:
            continue
        if not isinstance(item.before, str) or not isinstance(item.after, str):
            continue
        relative = generate_mod.relative_under_root(str(item.file), isolated)
        if relative is None:
            return None
        by_path.setdefault(relative, []).append(item)
    states: List[Tuple[str, str, str]] = []
    for relative in sorted(by_path):
        items = sorted(by_path[relative], key=lambda entry: int(entry.seq or 0))
        states.append((relative, items[0].before or "", items[-1].after or ""))
    return states or None


def _run_state(
    states: Sequence[Tuple[str, str, str]],
    side: str,
    expectation: str,
    command: Sequence[str],
    isolated: Path,
    test_rel: str,
    seconds: int,
    limit: int,
) -> Step:
    """把副本里的源码切成该侧（`before` / `after`）后跑一次测试。"""
    for relative, before, after in states:
        text = before if side == "before" else after
        serialize.write_text_atomic(isolated.joinpath(*relative.split("/")), text)
    return _run_pytest(command, isolated, test_rel, expectation, seconds, limit)


def _run_pytest(
    command: Sequence[str],
    cwd: Path,
    test_rel: str,
    expectation: str,
    seconds: int,
    limit: int,
) -> Step:
    """跑一次测试；只按退出码判结局（0 = 通过、1 = 有测试失败、其余 = 环境/用法问题）。"""
    argv = list(command) + [test_rel]
    env = dict(os.environ)
    env.update(CHILD_ENV)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            timeout=seconds,
        )
    except subprocess.TimeoutExpired as exc:
        excerpt, truncated = stable_excerpt(_as_text(exc.stdout), limit)
        return Step(expectation, None, OUTCOME_TIMEOUT, excerpt, truncated)
    except OSError as exc:  # 命令不存在/不可执行
        return Step(expectation, None, OUTCOME_ENVIRONMENT, "{}".format(exc), False)
    excerpt, truncated = stable_excerpt(proc.stdout or "", limit)
    return Step(expectation, proc.returncode, _outcome_of(proc.returncode), excerpt, truncated)


def _outcome_of(exit_code: Optional[int]) -> str:
    if exit_code == PYTEST_EXIT_PASSED:
        return OUTCOME_PASSED
    if exit_code == PYTEST_EXIT_TESTS_FAILED:
        return OUTCOME_FAILED
    return OUTCOME_ENVIRONMENT


def stable_excerpt(text: str, limit: int) -> Tuple[str, bool]:
    """只保留可复现的行（失败明细与短摘要）→ 摘录也能进台账（同输入双跑逐字节一致）。

    为什么必须过滤：pytest 的头部含平台/版本/耗时/临时目录，直接摘录会让 manifest 每次都不同，
    "同输入同产物"这条承诺就会在 manifest 上失效——而 manifest 正是要给别人复核的那份证据。
    """
    kept: List[str] = []
    for line in str(text).replace("\r\n", "\n").split("\n"):
        body = line.rstrip()
        if body.strip() and body.lstrip().startswith(EXCERPT_PREFIXES):
            kept.append(body)
    joined = "\n".join(kept)
    return joined[:limit], len(joined) > limit


def _classify(red: Step, green: Step) -> Tuple[str, str]:
    """两步结局 → 三态结论 + 原因码（超时/环境优先于"测试没失败"，别把坏环境说成判据不符）。"""
    for step in (red, green):
        if step.outcome == OUTCOME_TIMEOUT:
            return STATUS_UNVERIFIED, REASON_TIMEOUT
    for step in (red, green):
        if step.outcome == OUTCOME_ENVIRONMENT:
            return STATUS_UNVERIFIED, REASON_ENVIRONMENT_UNAVAILABLE
    if not red.satisfied():
        return STATUS_UNVERIFIED, REASON_RED_NOT_FAILED
    if not green.satisfied():
        return STATUS_UNVERIFIED, REASON_GREEN_NOT_PASSED
    return STATUS_VERIFIED, REASON_NONE


def _make_verdict(plan: Plan, status: str, reason: str, red: Optional[Step], green: Optional[Step]) -> Verdict:
    episode = plan.episode
    skeleton = plan.skeleton
    return Verdict(
        episode_id=plan.episode_id,
        session_id=str(getattr(episode, "session_id", events.UNKNOWN)),
        source=str(getattr(episode, "source", events.UNKNOWN)),
        source_file=str(getattr(episode, "source_file", getattr(skeleton, "source_file", events.UNKNOWN))),
        generated_at=str(getattr(episode, "end_ts", events.UNKNOWN)),
        episode_status=str(getattr(episode, "status", events.UNKNOWN)),
        generation_status=str(skeleton.status),
        generation_reason=str(skeleton.reason),
        assumed_convention=bool(skeleton.assumed_convention),
        assumed_parts=list(skeleton.assumed_parts),
        test_path=str(skeleton.relative_path),
        status=status,
        reason=reason,
        files=[_file_summary(item) for item in plan.files],
        red=red,
        green=green,
        content=skeleton.content,
    )


def _file_summary(item: reconstruct_mod.Reconstruction) -> Dict[str, Any]:
    """逐文件证据摘要：还原结论 + hunk 摘要（人读能顺藤摸瓜回到日志）。"""
    summary = dict(item.fact())
    summary["seq"] = int(item.seq or 0)
    summary["hunks"] = [dict(hunk) for hunk in item.hunks]
    return summary


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


# --------------------------------------------------------------------------- #
# 落盘：只有 verified 进正式测试目录
# --------------------------------------------------------------------------- #


def write_verified(target: str, verdicts: Sequence[Verdict]) -> List[str]:
    """把 `verified` 的测试写进目标仓库的测试目录（**只写测试目录**，K11）。

    `unverified` / `needs_manual` 一律不落正式目录：它们的结论已经写进 manifest，
    硬落盘只会让"仓库里有个测试"被误读成"这次修复被证明过了"。
    """
    root = Path(target)
    written: List[str] = []
    for verdict in verdicts:
        if verdict.status != STATUS_VERIFIED or not verdict.content:
            verdict.written = False
            continue
        path = root.joinpath(*str(verdict.test_path).split("/"))
        serialize.write_text_atomic(path, verdict.content)
        verdict.written = True
        written.append(str(path))
    return written


# --------------------------------------------------------------------------- #
# manifest：证据链
# --------------------------------------------------------------------------- #


def build_manifest(
    cfg: Mapping[str, Any],
    target: str,
    verdicts: Sequence[Verdict],
    sources: Optional[Mapping[str, Any]] = None,
    scan: Any = None,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """把逐件结论拼成 `regression/manifest.json` 的内容（纯函数，不写盘）。"""
    params = config_mod.verify_params(cfg)
    thresholds, bench_manifest = coverage_thresholds(data_dir)
    observed = {status: len([item for item in verdicts if item.status == status]) for status in STATUSES}
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "s2r verify",
        "generator_version": __version__,
        "target": str(target),
        "manifest_path": str(config_mod.generate_params(cfg)["manifest_path"]),
        "sources": dict(sources or {}),
        "verify": {
            "pytest_command": [str(item) for item in params["pytest_command"]],
            "interpreter_token": str(params["interpreter_token"]),
            "resolved_interpreter": sys.executable,
            "timeout_seconds": int(params["timeout_seconds"]),
            "tmpdir_prefix": str(params["tmpdir_prefix"]),
            "red_expectation": str(params["red_expectation"]),
            "green_expectation": str(params["green_expectation"]),
        },
        "coverage": {
            "min_count_by_type": thresholds,
            "source": bench_manifest,
            "observed": observed,
        },
        "counts": {
            "entries": len(verdicts),
            "written": len([item for item in verdicts if item.written]),
            "verified": observed[STATUS_VERIFIED],
            "unverified": observed[STATUS_UNVERIFIED],
            "needs_manual": observed[STATUS_NEEDS_MANUAL],
        },
        "entries": [item.to_dict() for item in verdicts],
        "skipped": list(getattr(scan, "skipped", []) or []),
        "failed": list(getattr(scan, "failed", []) or []),
    }


def manifest_path_of(cfg: Mapping[str, Any], target: str) -> Path:
    relative = str(config_mod.generate_params(cfg)["manifest_path"])
    return Path(target).joinpath(*relative.split("/"))


def write_manifest(cfg: Mapping[str, Any], target: str, manifest: Mapping[str, Any]) -> Path:
    """manifest 落 `[generate].manifest_path`（写盘只经 `serialize` 单点，K6）。"""
    return serialize.write_json_atomic(manifest_path_of(cfg, target), dict(manifest))


def summarize(verdicts: Sequence[Verdict]) -> Dict[str, int]:
    """人读摘要用的计数（键与 manifest 的 `counts` 同形，不另立一份口径）。"""
    return {
        "entries": len(verdicts),
        "written": len([item for item in verdicts if item.written]),
        "verified": len([item for item in verdicts if item.status == STATUS_VERIFIED]),
        "unverified": len([item for item in verdicts if item.status == STATUS_UNVERIFIED]),
        "needs_manual": len([item for item in verdicts if item.status == STATUS_NEEDS_MANUAL]),
    }


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def _is_file(path: Path) -> bool:
    """`Path.is_file()` 在 3.8 上遇到非法文件名会抛 `OSError`（与 `generate._is_file` 同因）。"""
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False
