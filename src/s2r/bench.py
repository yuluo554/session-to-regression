"""M1 基准：合成语料 + 植入真值的**对账器**（双边门 + 逐样本点名 + 探活）。

三个不能省的形态（方法论里踩过坑的那几条）：

1. **门是双边**：既守**检出率**（真值声明的事实必须对上），也守**误报 0**（该报错的样本必须
   真报错）。只守一边的门，在读取器整体失效时照样全绿——"没有误报"不等于"检出到了"。
2. **逐样本点名**：差异落到**具体样本 + 具体字段**，不许汇总率掩盖"某一类全零"。
3. **探活（`--probe`）**：往对账的输入里注入一个已知缺陷，基准**必须**报未达标；
   没报出来就是基准自己失灵（这时退出码是 1，把"基准失灵"和"数据没对上"都变成可见事件）。

另加一条台账纪律：语料与真值的 sha256 **逐份对账**（冻结物被改过就红），
这就把"字节即契约"从口号变成可执行检查。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import benchdata, episode as episode_mod, events, generate as generate_mod, readers, reconstruct as reconstruct_mod, serialize, verify as verify_mod
from .errors import InputUnavailable, UnsupportedFormat

MANIFEST_NAME = benchdata.MANIFEST_NAME

#: 探活名：向"读到的实际事实"注入缺陷，基准必须检出
PROBES = (
    "drop-tool",
    "zero-events",
    "drop-exit-code",
    "accept-unknown",
    "drop-episode",
    "keep-filtered",
    "swap-before-after",
    "drop-candidate-assert",
    "fold-unverified",
)

#: 对账的字段集（与 manifest 的 checked_facts 对应；`reconstructions` 属 M3、`generated` 属 M4、
#: `red_green` 属 M5）
CHECKED_FACTS = (
    "source",
    "format_version",
    "event_count",
    "kind_counts",
    "tools_used",
    "files_touched",
    "exit_codes",
    "user_report_fragments",
    # M2 起：episode 层事实也进对账面（真值里的 expect_episodes / expect_episode_statuses /
    # expect_filter_reasons 由生成器在**构造侧**声明）
    "episode_count",
    "episode_statuses",
    "filter_reasons",
    # M3 起：还原层事实（真值里的 expect_reconstructions 逐文件声明 before/after/status/source/confidence）
    "reconstructions",
    # M4 起：生成层事实（真值里的 expect_generated 声明骨架数 / 逐 episode 状态与原因 / 候选断言条数 / 锚点）
    "generated",
    # M5 起：验证层事实（真值里的 expect_red_green 逐 episode 声明三态结论 / 原因码 / RED+GREEN 退出码）
    "red_green",
)

#: 逐文件对账的键（真值可只声明其中一部分；键集合由测试锁死）
RECONSTRUCTION_FACTS = ("file", "status", "source", "confidence", "before", "after", "hunk_count")

#: 生成层对账的键（同上：真值声明哪些键、就逐键比对）
GENERATED_FACTS = (
    "generated_count",
    "written_count",
    "statuses",
    "reasons",
    "symbols",
    "candidate_count",
    "sections",
    "assumed_convention",
    "anchor_fragments",
)

#: 验证层逐 episode 对账的键（同上：真值声明哪些键、就逐键比对）
RED_GREEN_FACTS = ("episode_id", "status", "reason", "red_exit", "green_exit")


def load_manifest(data_dir: Path) -> Mapping[str, Any]:
    path = data_dir / MANIFEST_NAME
    if not path.is_file():
        raise InputUnavailable(
            "基准 manifest 不存在: {}".format(path),
            hint="先运行 python tools/gen_bench.py 生成合成语料",
        )
    try:
        manifest = json.loads(serialize.read_text(path))
    except ValueError as exc:
        raise InputUnavailable("基准 manifest 不是合法 JSON: {}: {}".format(path, exc))
    for key in ("bench_version", "coverage", "thresholds", "samples", "checked_facts"):
        if key not in manifest:
            raise InputUnavailable("基准 manifest 缺键: {}".format(key))
    if not manifest["samples"]:
        raise InputUnavailable("基准 manifest 的 samples 为空（扫描面为空会让门永远绿）")
    return manifest


def _facts(file_read: readers.FileRead, expected: Mapping[str, Any], cfg: Mapping[str, Any], repo: Optional[str] = None) -> Dict[str, Any]:
    """从读取结果里算出**可对账的事实**（只算，不判定）。"""
    kinds: Counter = Counter(event.kind for event in file_read.events)
    tools: List[str] = []
    files: List[str] = []
    exit_codes: List[int] = []
    user_texts: List[str] = []
    for event in file_read.events:
        if event.kind == "tool_call" and event.tool != events.UNKNOWN and event.tool not in tools:
            tools.append(event.tool)
        if event.path != events.UNKNOWN and event.path not in files:
            files.append(event.path)
        if event.kind == "tool_result":
            code = event.payload.get("exit_code")
            if isinstance(code, int) and not isinstance(code, bool):
                exit_codes.append(code)
        if event.kind == "user_msg":
            text = event.payload.get("text")
            if isinstance(text, str):
                user_texts.append(text)
    declared_fragments = expected.get("user_report_fragments") or []
    matched_fragments = [fragment for fragment in declared_fragments if any(fragment in text for text in user_texts)]
    session_files = {event.session_id: str(file_read.path) for event in file_read.events}
    split = episode_mod.split(file_read.events, cfg, session_files=session_files)
    reconstruction = reconstruct_mod.reconstruct_session(file_read, cfg, repo=repo)
    # 生成层与验证层共用同一次规划（还原是最贵的一步，别为两个事实面各跑一遍）
    detailed = generate_mod.plan_session_detailed(file_read, cfg, repo=repo)
    generation = [skeleton for _episode, skeleton, _files in detailed]
    declared_generated = expected.get("generated") if isinstance(expected.get("generated"), Mapping) else {}
    anchors = declared_generated.get("anchor_fragments") or []
    return {
        "source": file_read.source,
        "format_version": file_read.format_version,
        "event_count": len(file_read.events),
        "kind_counts": {kind: kinds[kind] for kind in sorted(kinds)},
        "tools_used": tools,
        "files_touched": files,
        "exit_codes": exit_codes,
        "user_report_fragments": matched_fragments,
        "episode_count": len(split.episodes),
        "episode_statuses": [item.status for item in split.episodes],
        "filter_reasons": sorted(str(item["reason"]) for item in split.filtered),
        "reconstructions": [item.fact() for item in reconstruction.files],
        "generated": generate_mod.summarize(generation, anchors=anchors),
        "red_green": _red_green_facts(detailed, cfg, repo),
    }


def _red_green_facts(detailed: Sequence[Any], cfg: Mapping[str, Any], repo: Optional[str]) -> List[Dict[str, Any]]:
    """验证层事实：有目标仓库就**真跑** RED/GREEN，没有就如实记"没有仓库可验证"。

    为什么"没有仓库"也要产出事实（而不是跳过）：那样真值里"这个 episode 该被验证"的声明
    就会被无声吞掉。空集/环境不可用都是**显式结论**，让差异能被逐条点名。
    """
    plans = [verify_mod.Plan(episode, skeleton, files) for episode, skeleton, files in detailed]
    if not repo:
        return verify_mod.red_green_facts(verify_mod.unreachable_verdicts(plans))
    try:
        return verify_mod.red_green_facts(verify_mod.verify_plans(plans, cfg, repo))
    except InputUnavailable:
        # 仓库夹具不在位（台账门已经会报）：这里如实落"环境不可用"，让逐字段对账点名，
        # 而不是让基准整体崩掉（崩掉会被误读成"环境问题"而不是"数据没对上"）
        return verify_mod.red_green_facts(verify_mod.unreachable_verdicts(plans))


def compare(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> List[str]:
    """逐字段比对，差异带上字段名与两边取值（点名到字段，不只是"不一致"）。"""
    mismatches: List[str] = []
    for field in CHECKED_FACTS:
        if field not in expected:
            continue
        if field == "reconstructions":
            # 逐文件、逐字段点名——汇总成"两边不等"就说不清是哪一文件的哪一项坏了
            mismatches.extend(compare_reconstructions(expected[field], actual.get(field)))
            continue
        if field == "generated":
            # 生成层逐键点名（生成事实里含不可对账的明细，故不整体比较）
            mismatches.extend(compare_generated(expected[field], actual.get(field)))
            continue
        if field == "red_green":
            # 验证层逐 episode 点名（同样含不可对账的日志摘录）
            mismatches.extend(compare_red_green(expected[field], actual.get(field)))
            continue
        want, got = expected[field], actual.get(field)
        if want != got:
            mismatches.append("{}: 期望 {!r}，实际 {!r}".format(field, want, got))
    return mismatches


def compare_red_green(expected: Any, actual: Any) -> List[str]:
    """验证层逐 episode 对账：差异点名到 `red_green[<episode>].<键>`（并给出两边取值）。"""
    mismatches: List[str] = []
    want = list(expected or [])
    got = list(actual or [])
    if len(want) != len(got):
        mismatches.append("red_green: 期望 {} 项，实际 {} 项".format(len(want), len(got)))
    for index in range(min(len(want), len(got))):
        left = want[index] if isinstance(want[index], Mapping) else {}
        right = got[index] if isinstance(got[index], Mapping) else {}
        label = "red_green[{}]".format(right.get("episode_id") or left.get("episode_id") or index)
        for key in RED_GREEN_FACTS:
            if key not in left:
                continue
            if left[key] != right.get(key):
                mismatches.append("{}.{}: 期望 {!r}，实际 {!r}".format(label, key, left[key], right.get(key)))
    return mismatches


def compare_generated(expected: Any, actual: Any) -> List[str]:
    """生成层事实逐键对账：差异点名到 `generated.<键>`（并给出两边取值）。"""
    mismatches: List[str] = []
    if not isinstance(expected, Mapping):
        return mismatches
    got = actual if isinstance(actual, Mapping) else {}
    for key in GENERATED_FACTS:
        if key not in expected:
            continue
        if expected[key] != got.get(key):
            mismatches.append("generated.{}: 期望 {!r}，实际 {!r}".format(key, expected[key], got.get(key)))
    return mismatches


def compare_reconstructions(expected: Any, actual: Any) -> List[str]:
    """还原事实逐文件对账（缺项/多项/字段不符都要点名到文件与字段）。"""
    mismatches: List[str] = []
    want = list(expected or [])
    got = list(actual or [])
    if len(want) != len(got):
        mismatches.append("reconstructions: 期望 {} 项，实际 {} 项".format(len(want), len(got)))
    for index in range(min(len(want), len(got))):
        left = want[index] if isinstance(want[index], Mapping) else {}
        right = got[index] if isinstance(got[index], Mapping) else {}
        label = "reconstructions[{}]".format(right.get("file") or left.get("file") or index)
        for key in RECONSTRUCTION_FACTS:
            if key not in left:
                continue
            if left[key] != right.get(key):
                mismatches.append("{}.{}: 期望 {!r}，实际 {!r}".format(label, key, left[key], right.get(key)))
    return mismatches


def _apply_probe(probe: Optional[str], actual: Dict[str, Any]) -> Dict[str, Any]:
    if probe == "drop-tool":
        if actual["tools_used"]:
            actual["tools_used"] = actual["tools_used"][:-1]
    elif probe == "zero-events":
        actual["event_count"] = 0
        actual["kind_counts"] = {}
    elif probe == "drop-exit-code":
        actual["exit_codes"] = []
    elif probe == "drop-episode":
        # 模拟"切分器漏切一条修复链"
        if actual["episode_statuses"]:
            actual["episode_statuses"] = actual["episode_statuses"][:-1]
            actual["episode_count"] = len(actual["episode_statuses"])
    elif probe == "keep-filtered":
        # 模拟"该被过滤的候选没被过滤掉"
        if actual["filter_reasons"]:
            actual["filter_reasons"] = actual["filter_reasons"][:-1]
    elif probe == "swap-before-after":
        # 模拟最典型的还原错误：把 after 当成 before 交出去（"看着像有产物"的那种坏）
        for item in actual.get("reconstructions") or []:
            if isinstance(item, dict) and isinstance(item.get("before"), str) and isinstance(item.get("after"), str):
                item["before"], item["after"] = item["after"], item["before"]
    elif probe == "drop-candidate-assert":
        # 模拟 M4 最典型的空转：骨架照写、三类锚点都在，但**候选断言整段丢掉**
        # （模板退化成"只有 docstring 的装饰品"）。注入打在判定路径上：候选事实归零。
        generated = actual.get("generated")
        if isinstance(generated, dict):
            generated["candidate_count"] = 0
    elif probe == "fold-unverified":
        # 模拟 M5 最典型的空转：把 `unverified` 折叠成 `verified`（等价于"RED 判据恒真"）。
        # 注入打在判定路径上：三态被折成一态，未验证的项冒充通过。
        for item in actual.get("red_green") or []:
            if isinstance(item, dict) and item.get("status") == "unverified":
                item["status"] = "verified"
                item["reason"] = ""
    return actual


def run(
    data_dir: Path,
    limits: Mapping[str, Any],
    cfg: Mapping[str, Any],
    only: Optional[Sequence[str]] = None,
    probe: Optional[str] = None,
) -> Dict[str, Any]:
    """跑一次基准对账，返回机读报告。"""
    if probe is not None and probe not in PROBES:
        raise InputUnavailable("未知探活名: {}（可用: {}）".format(probe, ", ".join(PROBES)))
    manifest = load_manifest(data_dir)
    thresholds = manifest["thresholds"]
    min_rate = float(thresholds["min_detection_rate"])
    max_false_positive = int(thresholds["max_false_positive"])

    ledger_mismatches: List[str] = []
    missing_files: List[str] = []
    sample_reports: List[Dict[str, Any]] = []
    type_counts: Counter = Counter()
    checked = matched = 0
    false_positives: List[str] = []

    # **作用域分工**：完整性类门（台账 sha256 / 文件在位 / 覆盖度）看**全量语料**——
    # 它们描述的是语料的完整性，不该被 `--only`（调试用筛选）缩小；
    # 判定类门（检出率 / 误报）只看被选中的样本。缩小完整性门的扫描面 = 让门漏检。
    for sample in manifest["samples"]:
        type_counts[sample["episode_type"]] += 1
        sample_path = data_dir / sample["file"]
        truth_path = data_dir / sample["truth"]
        if not sample_path.is_file() or not truth_path.is_file():
            missing_files.append(sample["sample"])
            continue
        if serialize.sha256_bytes(sample_path.read_bytes()) != sample["sha256"]:
            ledger_mismatches.append("{}: 语料 sha256 与台账不符".format(sample["file"]))
        if serialize.sha256_bytes(truth_path.read_bytes()) != sample["truth_sha256"]:
            ledger_mismatches.append("{}: 真值 sha256 与台账不符".format(sample["truth"]))
        if only and sample["sample"] not in only and sample["episode_type"] not in only:
            continue
        _check_sample(data_dir, sample, limits, cfg, probe, sample_reports, false_positives)
        checked += 1
        if sample_reports[-1]["status"] in ("pass", "error_ok"):
            matched += 1

    # 磁盘基线夹具也在台账里（"字节即契约"对它们同样成立：改了就没收据）
    for fixture in manifest.get("repo_fixtures", []):
        fixture_path = data_dir / fixture["file"]
        if not fixture_path.is_file():
            missing_files.append(fixture["file"])
        elif serialize.sha256_bytes(fixture_path.read_bytes()) != fixture["sha256"]:
            ledger_mismatches.append("{}: 磁盘基线 sha256 与台账不符".format(fixture["file"]))

    coverage_missing = []
    for episode_type, minimum in sorted(manifest["coverage"]["min_count_by_type"].items()):
        observed = type_counts.get(episode_type, 0)
        if observed < int(minimum):
            coverage_missing.append("{}: 需要 ≥{}，实际 {}".format(episode_type, minimum, observed))

    detection_rate = (float(matched) / checked) if checked else 0.0
    gates = {
        "coverage": {"ok": not coverage_missing, "missing": coverage_missing},
        "detection": {
            "ok": checked > 0 and detection_rate >= min_rate,
            "rate": round(detection_rate, 6),
            "min_rate": min_rate,
            "matched": matched,
            "checked": checked,
        },
        "false_positive": {"ok": len(false_positives) <= max_false_positive, "count": len(false_positives), "max": max_false_positive, "samples": sorted(false_positives)},
        "ledger": {"ok": not ledger_mismatches, "mismatches": sorted(ledger_mismatches)},
        "files_present": {"ok": not missing_files, "missing": sorted(missing_files)},
    }
    all_ok = checked > 0 and all(gate["ok"] for gate in gates.values())
    report: Dict[str, Any] = {
        "bench_version": manifest["bench_version"],
        "data_dir": str(data_dir),
        "probe": probe or "",
        "samples": sample_reports,
        "gates": gates,
        "coverage": {"observed_count_by_type": dict(sorted(type_counts.items())), "min_count_by_type": manifest["coverage"]["min_count_by_type"]},
        "pending_facts": manifest.get("pending_facts", {}),
        "checked_facts": list(CHECKED_FACTS),
        "status": "ok" if all_ok else "not_met",
    }
    if probe:
        # 探活模式：状态取反——"注入的缺陷被检出"才是成功
        report["probe_detected"] = not all_ok
        report["status"] = "probe_detected" if not all_ok else "probe_not_detected"
    return report


def _check_sample(
    data_dir: Path,
    sample: Mapping[str, Any],
    limits: Mapping[str, Any],
    cfg: Mapping[str, Any],
    probe: Optional[str],
    sample_reports: List[Dict[str, Any]],
    false_positives: List[str],
) -> None:
    """对账单个样本：期望报错的样本必须报错；其余样本逐字段核对。"""
    name = sample["sample"]
    episode_type = sample["episode_type"]
    sample_path = data_dir / sample["file"]
    truth_path = data_dir / sample["truth"]
    truth = json.loads(truth_path.read_bytes().decode("utf-8"))
    expected = truth.get("expect") or {}
    # 真值里的 episode 层期望（顶层键）映射到事实名；None 表示该样本不涉 episode 对账
    if truth.get("expect_episodes") is not None:
        expected = dict(expected)
        expected["episode_count"] = truth["expect_episodes"]
        expected["episode_statuses"] = list(truth.get("expect_episode_statuses") or [])
        expected["filter_reasons"] = sorted(str(item) for item in (truth.get("expect_filter_reasons") or []))
    # M3：还原层期望（逐文件 before/after/status/source/confidence）
    if "expect_reconstructions" in truth:
        expected = dict(expected)
        expected["reconstructions"] = list(truth["expect_reconstructions"] or [])
    # M4：生成层期望（骨架数 / 逐 episode 状态与原因 / 候选断言条数 / 必须出现的证据锚点）
    if "expect_generated" in truth:
        expected = dict(expected)
        expected["generated"] = dict(truth["expect_generated"] or {})
    # M5：验证层期望（逐 episode 三态结论 + 原因码 + RED/GREEN 退出码）
    if "expect_red_green" in truth:
        expected = dict(expected)
        expected["red_green"] = list(truth["expect_red_green"] or [])
    expect_error = truth.get("expect_error")
    # 磁盘基线：Codex 侧的全文 before 只能来自"该编辑发生前的磁盘状态"（随语料冻结）
    repo_rel = str(sample.get("repo") or "")
    repo = str(data_dir / repo_rel) if repo_rel else None

    if expect_error:
        if probe == "accept-unknown":
            # 探活：模拟"读取器不再拒绝未知格式、静默成功"这一缺陷。
            # 注入打在**判定路径**上（记为误报），而不是"直接记通过"——后者会把门骗过去，
            # 让探活自己变成假门（首版就是这么写的，四个探活里它唯一没被检出）。
            false_positives.append(name)
            sample_reports.append(
                {
                    "sample": name,
                    "episode_type": episode_type,
                    "status": "false_positive",
                    "mismatches": ["探活注入：本应报 {} 却静默成功".format(expect_error)],
                }
            )
            return
        try:
            readers.read_file(sample_path, limits)
        except UnsupportedFormat as exc:
            ok = exc.code == expect_error
            sample_reports.append(
                {"sample": name, "episode_type": episode_type, "status": "error_ok" if ok else "fail", "mismatches": [] if ok else ["错误码不符: {}".format(exc.code)]}
            )
        except Exception as exc:  # 其它异常同样算"报错形态不符"
            sample_reports.append({"sample": name, "episode_type": episode_type, "status": "fail", "mismatches": ["非预期异常: {}".format(type(exc).__name__)]})
        else:
            false_positives.append(name)
            sample_reports.append({"sample": name, "episode_type": episode_type, "status": "false_positive", "mismatches": ["该样本应报 {}，却读成功".format(expect_error)]})
        return

    try:
        file_read = readers.read_file(sample_path, limits)
    except Exception as exc:
        sample_reports.append({"sample": name, "episode_type": episode_type, "status": "fail", "mismatches": ["读取失败: {}: {}".format(type(exc).__name__, exc)]})
        return

    actual = _apply_probe(probe, _facts(file_read, expected, cfg, repo=repo))
    mismatches = compare(expected, actual)
    sample_reports.append({"sample": name, "episode_type": episode_type, "status": "pass" if not mismatches else "fail", "mismatches": mismatches})


def summary_lines(report: Mapping[str, Any]) -> List[str]:
    """人读摘要（机读报告另存）。"""
    lines = []
    gates = report["gates"]
    lines.append(
        "bench: {}（样本 {}，检出率 {}/{} = {:.2f}，误报 {}）".format(
            report["status"],
            len(report["samples"]),
            gates["detection"]["matched"],
            gates["detection"]["checked"],
            gates["detection"]["rate"],
            gates["false_positive"]["count"],
        )
    )
    for name in ("coverage", "ledger", "files_present"):
        if not gates[name]["ok"]:
            lines.append("  [FAIL] {}: {}".format(name, gates[name].get("missing") or gates[name].get("mismatches")))
    if report.get("probe"):
        lines.append("  probe={} detected={}".format(report["probe"], report.get("probe_detected")))
    for item in report["samples"]:
        if item["status"] not in ("pass", "error_ok"):
            lines.append("  [{}] {}: {}".format(item["status"], item["sample"], "; ".join(item["mismatches"])))
    pending = report.get("pending_facts") or {}
    if pending:
        lines.append("  pending（未对账，不当作通过）: {}".format(", ".join(sorted(pending))))
    return lines
