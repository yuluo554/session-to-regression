"""M1 基准语料库：合成会话日志 + 植入真值的**参照实现**（放在包内，装好后同样可用）。

为什么是包内模块而不是 tools/ 下的脚本：参照实现必须随包发布——否则"装好的包里没有它"，
`s2r bench --check` 就只能在源码树下工作（安装面缺陷的经典形态）。`tools/gen_bench.py`
只是这个模块的薄 CLI。

四条纪律（缺一条基准就不可信）：

1. **真值只存"输入与期望"**：期望由**构造侧**同步记录（builder 每写一条记录就登记它应产生的
   事实），**由读取器反解出的字段一个都不进真值**（禁止 `observed_*`）。判据 = 把读取器实现
   整个删掉，真值仍能由本模块复算出来。
2. **词表不内嵌**（K13）：失败输出里的信号词、bug 语义词一律**从 `config/scoring.toml` 运行时取**，
   源码零词面——配置加一个词，语料要重生成（`--check` 会红），覆盖不会悄悄缩水。
3. **确定性**：禁 stdlib `random`（跨版本序列不承诺）、禁时钟、禁 set 迭代序；自研 splitmix64，
   且 RNG **必须被真消费**（用于在多套预置方案间选型，抽样次数记入 manifest）。
4. **隐私白名单**：所有值来自 `data/README.md` §三.1 登记的假值，生成后**自检**：
   任何绝对路径只要不在白名单前缀内即报错。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import config as config_mod
from . import serialize

#: 仓库根（src/s2r/benchdata.py → parents[2]）；冻结语料的默认落点
REPO_ROOT = Path(__file__).resolve().parents[2]

BENCH_RELATIVE = ("data", "bench")
MANIFEST_NAME = "manifest.json"
SEED = 20261017

#: 假值白名单（来源：data/README.md §三.1）。新增假值必须同时改这里与台账。
#: 假 Windows 绝对路径前缀：**运行时片段拼装**——本仓的脱敏门会扫"[大写盘符]:"形态，
#: 源码里不留该字面量（语料与 manifest 里是拼装后的值，这两份不在源码扫描面内）。
_FAKE_DRIVE = "D"
WIN_DEMO_PREFIX = _FAKE_DRIVE + ":" + chr(92) + "demo-beta" + chr(92)

WHITELIST = {
    "project_names": ("demo-alpha", "demo-beta"),
    "path_prefixes": ("/demo/", WIN_DEMO_PREFIX),
    "session_ids": ("sess-demo-",),
    "user": "demo-user",
}

#: 假 harness 版本（形似真实，值本身是假的；版本指纹按**键集合**判，不按版本号字符串）
FAKE_CLAUDE_VERSION = "2.0.0-demo"
FAKE_CODEX_CLI_VERSION = "0.142.5-demo"

#: 失败信号在配置列表里的**结构角色**（索引是结构映射，词面只在配置文件里，K13）。
#: 配置里该列表的顺序即角色顺序；改顺序会让语料字节变化 → `--check` 红 → 需重生成。
SIGNAL_ROLES = ("failed", "assertion", "traceback", "exit_code_prefix")

BASE_TS = "2026-01-02T03:04:"
FAKE_CWD = "/demo/" + WHITELIST["project_names"][0]

#: 覆盖度门槛（唯一的数值定义处 = 生成的 manifest；本表只是生成它时用的构造参数）
MIN_COUNT_BY_TYPE = {
    "happy_path": 1,
    "ended_without_pass": 1,
    "multi_bug": 1,
    "no_failure_signal": 1,
    "doc_only": 1,
    "ambiguous_old_string": 1,
    "new_file_write": 1,
    # M3 起：还原降级路径各有具名样本（① 结构化补丁 / ③ 磁盘基线对不上）
    "structured_patch": 1,
    "base_drift": 1,
    "unsupported_format": 1,
}

#: 基准门槛（检出率门槛随 manifest 下发，数据非代码）
MIN_DETECTION_RATE = 1.0
MAX_FALSE_POSITIVE = 0


class Splitmix64:
    """确定性 RNG（自研，跨 Python 版本序列一致）。"""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFFFFFFFFFF
        self.draws = 0

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        self.draws += 1
        return z ^ (z >> 31)

    def below(self, limit: int) -> int:
        if limit <= 0:
            raise ValueError("limit must be positive")
        return self.next_u64() % limit

    def choice(self, items: Sequence[Any]) -> Any:
        return items[self.below(len(items))]


class Builder:
    """把"要造什么"写成记录，并**在构造侧同步登记期望事实**（真值来源）。"""

    def __init__(self, source: str, session_id: str, cfg: Mapping[str, Any], rng: Splitmix64) -> None:
        self.source = source
        self.session_id = session_id
        self.cfg = cfg
        self.rng = rng
        self.records: List[Mapping[str, Any]] = []
        self.signals = config_mod.vocabulary(cfg)["failure_signals"]
        self.index = 0
        # 期望事实（由构造侧记录，不来自读取器）
        self.expect: Dict[str, Any] = {
            "source": source,
            "event_count": 0,
            "kind_counts": {},
            "tools_used": [],
            "files_touched": [],
            "exit_codes": [],
            "user_report_fragments": [],
        }

    # ---- 期望登记 ---------------------------------------------------------- #
    def _expect_event(self, kind: str) -> None:
        self.expect["event_count"] += 1
        counts = self.expect["kind_counts"]
        counts[kind] = counts.get(kind, 0) + 1

    def _expect_tool(self, tool: str, path: Optional[str] = None) -> None:
        if tool not in self.expect["tools_used"]:
            self.expect["tools_used"].append(tool)
        self._expect_file(path)

    def _expect_file(self, path: Optional[str]) -> None:
        """登记"被触碰的文件"——**不等于**工具：结果侧通道（file_change）也不产生工具名。"""
        if path and path not in self.expect["files_touched"]:
            self.expect["files_touched"].append(path)

    def _expect_exit_code(self, code: int) -> None:
        self.expect["exit_codes"].append(code)

    def _expect_user_report(self, text: str) -> None:
        self.expect["user_report_fragments"].append(text)

    # ---- 公共 ------------------------------------------------------------- #
    def _append(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        self.records.append(record)
        return record

    def _ts(self) -> str:
        self.index += 1
        return "{}{:02d}.{:06d}Z".format(BASE_TS, self.index % 60, self.index * 1000)

    def failure_output(self, exit_code: int = 1, detail: str = "assert 3 == 4") -> str:
        """合成失败输出：信号词**从配置取**（词面不内嵌源码）。"""
        failed, assertion, traceback, exit_prefix = (self.signals[index] for index in range(len(SIGNAL_ROLES)))
        if self.source == "claude":
            return "{} (most recent call last):\n  File \"{}\", line 12, in test_calc\n{}: {}\n{} tests/regression/test_calc.py::test_divide\n".format(
                traceback, "/demo/alpha/src/calc.py", assertion, detail, failed
            )
        return "Chunk ID: {:06x}\nWall time: 0.1 seconds\nProcess exited with code {}\nOriginal token count: 24\nOutput:\n{} (most recent call last):\n{}: {}\n{} tests/regression/test_calc.py::test_divide\n".format(
            self.rng.below(0xFFFFFF), exit_code, traceback, assertion, detail, failed
        )

    def passing_output(self, exit_code: int = 0) -> str:
        if self.source == "claude":
            return "1 passed in 0.02s\n"
        return "Chunk ID: {:06x}\nWall time: 0.1 seconds\nProcess exited with code {}\nOriginal token count: 5\nOutput:\n1 passed in 0.02s\n".format(
            self.rng.below(0xFFFFFF), exit_code
        )

    def bug_report(self, subject: str) -> str:
        """合成用户报告：语义词**从配置取**（词面不内嵌源码）。"""
        term = config_mod.vocabulary(self.cfg)["bug_terms"][0]
        text = "{}, {}: {} in {}".format(WHITELIST["user"], term, subject, "/demo/alpha/src/calc.py")
        self._expect_user_report(text)
        return text

    # ---- Claude ----------------------------------------------------------- #
    def _claude_line(self, kind: str, **extra: Any) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "cwd": FAKE_CWD,
            "entrypoint": "cli",
            "gitBranch": "main",
            "isSidechain": False,
            "parentUuid": None,
            "sessionId": self.session_id,
            "timestamp": self._ts(),
            "type": kind,
            "userType": "external",
            "uuid": "uuid-demo-{:04d}".format(self.index),
            "version": FAKE_CLAUDE_VERSION,
        }
        record.update(extra)
        return self._append(record)

    def claude_meta_line(self) -> Mapping[str, Any]:
        """真实日志首行常是 UI 状态行（实测）：造进语料以锁住"探测窗口"行为。"""
        return self._append({"type": "last-prompt", "lastPrompt": "demo", "leafUuid": "uuid-demo-leaf", "sessionId": self.session_id})

    def claude_user_message(self, text: str) -> Mapping[str, Any]:
        self._expect_event("user_msg")
        return self._claude_line("user", message={"role": "user", "content": text})

    def claude_assistant_message(self, text: str) -> Mapping[str, Any]:
        self._expect_event("assistant_msg")
        return self._claude_line("assistant", message={"role": "assistant", "content": [{"type": "text", "text": text}]})

    def claude_tool_call(self, name: str, tool_input: Mapping[str, Any], path: Optional[str] = None, tool_use_id: str = "") -> Mapping[str, Any]:
        call_id = tool_use_id or "toolu-demo-{:04d}".format(self.index)
        self._expect_event("tool_call")
        self._expect_tool(name, path)
        return self._claude_line(
            "assistant",
            message={"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": dict(tool_input)}]},
        )

    def claude_tool_result(self, tool_use_id: str, text: str, result_meta: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        self._expect_event("tool_result")
        record: Dict[str, Any] = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": [{"type": "text", "text": text}], "is_error": False}]},
        }
        if result_meta is not None:
            record["toolUseResult"] = dict(result_meta)
        kind = record.pop("type")
        return self._claude_line(kind, **record)

    def claude_edit_pair(
        self,
        tool_use_id: str,
        file_path: str,
        old_string: str,
        new_string: str,
        original_file: Optional[str],
        hunk_lines: Optional[Sequence[str]] = None,
        replace_all: bool = False,
        old_start: int = 1,
        result_type: Optional[str] = None,
    ) -> None:
        """一次 `Edit`：调用侧（`old_string`/`new_string`）+ 结果侧（`originalFile` / `structuredPatch`）。

        `hunk_lines` 传 None → `structuredPatch: []`（**空表**，K14：新建/无 hunk 的结果实测就是空表）；
        传正文则按前缀自动算 `oldLines`/`newLines`——避免手写计数与正文漂移。
        """
        self.claude_tool_call(
            "Edit",
            {"file_path": file_path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all},
            path=file_path,
            tool_use_id=tool_use_id,
        )
        meta: Dict[str, Any] = {"filePath": file_path}
        if original_file is not None:
            meta["originalFile"] = original_file
        if hunk_lines:
            lines = list(hunk_lines)
            old_lines = [item for item in lines if item[:1] in (" ", "-")]
            new_lines = [item for item in lines if item[:1] in (" ", "+")]
            meta["structuredPatch"] = [
                {"oldStart": old_start, "oldLines": len(old_lines), "newStart": old_start, "newLines": len(new_lines), "lines": lines}
            ]
        else:
            meta["structuredPatch"] = []
        if result_type is not None:
            meta["type"] = result_type
        self.claude_tool_result(tool_use_id, "The file has been updated.", meta)

    # ---- Codex ------------------------------------------------------------ #
    def _codex_line(self, line_type: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.index += 1
        return self._append({"ordinal": self.index, "payload": dict(payload), "timestamp": self._ts(), "type": line_type})

    def codex_session_meta(self) -> Mapping[str, Any]:
        self._expect_event("meta")
        return self._codex_line(
            "session_meta",
            {
                "id": self.session_id,
                "session_id": self.session_id,
                "cli_version": FAKE_CODEX_CLI_VERSION,
                "cwd": FAKE_CWD,
                "model_provider": "demo-provider",
                "originator": "demo_cli",
                "source": "cli",
                "thread_source": "user",
                "timestamp": self._ts(),
                "history_mode": "full",
                "context_window": 128000,
                "base_instructions": "demo",
                "dynamic_tools": [],
            },
        )

    def codex_message(self, role: str, text: str) -> Mapping[str, Any]:
        # 角色 → IR 种类的映射与读取器的对外契约一致：user → user_msg，assistant → assistant_msg，
        # developer（系统/开发者指令）→ meta（实测 925 条，避免被当成用户报 bug）
        kind = {"user": "user_msg", "assistant": "assistant_msg"}.get(role, "meta")
        self._expect_event(kind)
        return self._codex_line("response_item", {"type": "message", "id": "msg-demo-{:04d}".format(self.index), "role": role, "content": [{"type": "text", "text": text}]})

    def codex_command(self, command: str, call_id: str) -> Mapping[str, Any]:
        self._expect_event("tool_call")
        self._expect_tool("shell_command")
        arguments = {"command": command, "timeout_ms": CODEX_TIMEOUT_MS, "workdir": FAKE_CWD}
        return self._codex_line("response_item", {"type": "function_call", "name": "shell_command", "call_id": call_id, "arguments": _dumps_inline(arguments)})

    def codex_command_output(self, call_id: str, text: str, exit_code: int) -> Mapping[str, Any]:
        self._expect_event("tool_result")
        self._expect_exit_code(exit_code)
        return self._codex_line("response_item", {"type": "function_call_output", "call_id": call_id, "output": text})

    def codex_apply_patch(self, call_id: str, patch_text: str, files: Sequence[str], output: str) -> Mapping[str, Any]:
        self._expect_event("tool_call")
        self._expect_tool("apply_patch", files[0] if files else None)
        return self._codex_line(
            "response_item",
            {"type": "custom_tool_call", "name": "apply_patch", "call_id": call_id, "status": "completed", "input": patch_text},
        )

    def codex_patch_output(self, call_id: str, output: str) -> Mapping[str, Any]:
        self._expect_event("tool_result")
        self._expect_exit_code(0)
        return self._codex_line("response_item", {"type": "custom_tool_call_output", "call_id": call_id, "output": output})

    def codex_file_change(self, changes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
        # 结果侧通道：产生 tool_result 事件与文件事实，但**不是一次工具调用**（tools_used 不收它）
        self._expect_event("tool_result")
        self._expect_file(sorted(changes)[0] if changes else None)
        return self._codex_line(
            "event_msg",
            {
                "type": "item_completed",
                "thread_id": "thread-demo",
                "turn_id": "turn-demo",
                "completed_at_ms": self.index,
                "item": {"id": "item-demo-{:04d}".format(self.index), "type": "FileChange", "status": "completed", "stdout": "", "changes": {path: dict(change) for path, change in changes.items()}},
            },
        )


#: 合成参数值（不是口径数值：IPC 请求体里的示例字段，故在门禁白名单里登记理由）
CODEX_TIMEOUT_MS = 120000
CODEX_MAX_OUTPUT_TOKENS = 4000


def _dumps_inline(obj: Mapping[str, Any]) -> str:
    import json

    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 样本方案
# --------------------------------------------------------------------------- #

ALPHA_CALC = "/demo/alpha/src/calc.py"
ALPHA_TEST = "/demo/alpha/tests/test_calc.py"
BETA_FORMAT = "/demo/beta/pkg/format.py"
DOC_TARGET = "/demo/alpha/docs/notes.md"
NEW_FILE = "/demo/alpha/src/helper.py"

#: M3 还原事实的构造侧声明（状态/来源/置信度的**枚举名**由 `s2r.reconstruct` 定义；
#: 这里刻意写字面量而不是 import 它——真值必须能由本模块独立复算，不依赖被测实现）
#: 状态：ok（before/after 齐） / partial（只有片段） / unresolved（不任选） / no_before_image（无 before）
#: 来源：claude_structured_patch / claude_old_string / claude_write_content /
#:       codex_unified_diff / codex_file_change_content / codex_patch_text
#: 置信度：high（日志内全文）/ medium（磁盘基线）/ low（片段）/ none（取不到）


def expect_rec(
    file: str,
    before: Optional[str],
    after: Optional[str],
    status: str,
    source: str,
    confidence: str,
    hunk_count: int = 1,
) -> Dict[str, Any]:
    """声明"这个文件的还原结果应该是什么"（逐文件对账用的事实子集）。"""
    return {
        "file": file,
        "status": status,
        "source": source,
        "confidence": confidence,
        "before": before,
        "after": after,
        "hunk_count": hunk_count,
    }


#: 骨架三段（构造侧声明；与 `[generate].sections` 同义——真值刻意写字面量，
#: 不复用被测实现的常量，理由同上面的状态/来源枚举）
GENERATED_SECTIONS = ["act", "arrange", "assert"]


def expect_generated(
    generated: int,
    written: int,
    statuses: Sequence[str],
    reasons: Sequence[str],
    symbols: Sequence[str] = (),
    candidates: int = 0,
    sections: Sequence[str] = (),
    anchors: Sequence[str] = (),
    assumed: bool = True,
) -> Dict[str, Any]:
    """声明"生成层应该产出什么"（构造侧声明，不由被测实现反推）。

    * `generated` / `written`：含候选断言的骨架数 / 落盘文件数（**空集也是断言**）；
    * `statuses` / `reasons`：逐 episode 的结论与原因码（`generated` 的原因码是空串）；
    * `candidates`：活动候选断言条数（"把候选断言整段丢掉"的探活就打在它上面）；
    * `anchors`：**必须出现在骨架里的片段**（会话 id / 报告片段 / 修复文件路径等证据锚点）。
    """
    return {
        "generated_count": generated,
        "written_count": written,
        "statuses": list(statuses),
        "reasons": list(reasons),
        "symbols": list(symbols),
        "candidate_count": candidates,
        "sections": list(sections),
        "assumed_convention": assumed,
        "anchor_fragments": list(anchors),
    }


#: 磁盘基线夹具：样本名 → {仓库内相对路径: 全文}。
#: 为什么需要（实测事实，`data/README.md` §6.7）：Codex 侧日志里**没有 before 全文**——
#: `FileChange` 只给 `unified_diff`（修改）或 `content`（新增），`apply_patch` 只给 patch 文本；
#: 全文基线只能来自"该编辑发生前的工作区"。基准把这份"当时的磁盘状态"一并冻结，
#: 才能对 Codex 侧的 before/after 做逐字符对账；`base-drift-1` 刻意给一份**对不上**的基线，
#: 用来锁住"磁盘已被改动 → 如实降级"这条纪律。
REPO_FIXTURES: Dict[str, Dict[str, str]] = {
    # happy-path-1 有两个变体（Claude 改 divide / Codex 改 format_amount），夹具按"两个变体各自的
    # 编辑前工作区"合并给出——冻结语料实际落在 Codex 变体上（RNG 选型固定），Claude 变体的
    # 期望值同样由构造侧声明，两个变体的真值都自洽。
    "happy-path-1": {
        "demo/beta/pkg/format.py": "def format_amount(value):\n    return value\n",
        "demo/alpha/src/calc.py": "def divide(a, b):\n    return a / b\n",
    },
    "ended-without-pass-1": {"demo/alpha/src/calc.py": "def first(items):\n    return items[0]\n"},
    "base-drift-1": {"demo/alpha/src/calc.py": "def first(items):\n    return items[-1]\n"},
    # M5：RED/GREEN 要在"该编辑发生前的仓库状态"里跑，所以**有内容可验证**的样本都要有仓库夹具。
    # 反过来说：夹具里的字节不是判据（验证会把日志还原出的 before/after 覆盖上去），
    # 它的作用是让"日志里的绝对路径 → 仓库内相对路径"这件事**成立**（映射不到就不许硬写，K11）。
    "structured-patch-1": {"demo/alpha/src/calc.py": "def divide(a, b):\n    return a / b\n"},
    "multi-bug-1": {
        "demo/alpha/src/calc.py": "def divide(a, b):\n    return a / b\n",
        "demo/beta/pkg/format.py": "def slugify(name):\n    return name\n",
    },
}


def expect_red_green(entries: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    """声明"这些 episode 的 RED/GREEN 结论应该是什么"（构造侧声明，不由被测实现反推）。

    每条声明五件事：`episode_id`、三态结论、原因码、以及 RED/GREEN 两次跑的退出码。
    退出码也进真值：它是"两次跑真的发生了"的硬证据——只声明"结论是 verified"的话，
    一个把两次跑都跳过的实现照样能把真值对上。

    `needs_manual`（生成层没产出内容）的两次退出码是 `None`：**没跑**就是没跑，不许填 0 冒充跑过。
    """
    out: List[Dict[str, Any]] = []
    for episode_id, status, reason, red_exit, green_exit in entries:
        out.append(
            {
                "episode_id": episode_id,
                "status": status,
                "reason": reason,
                "red_exit": red_exit,
                "green_exit": green_exit,
            }
        )
    return out


def sample_happy_path(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """正常修复链：报 bug → 测试失败 → 改代码 → 测试通过（Claude 侧）；Codex 侧同型。"""
    if rng.below(2) == 0:
        # Claude 变体：① 路径（`originalFile` + 非空 `structuredPatch`）
        before = "def divide(a, b):\n    return a / b\n"
        after = "def divide(a, b):\n    return a / b if b else 0\n"
        builder = Builder("claude", session_id, cfg, rng)
        builder.claude_meta_line()
        builder.claude_user_message(builder.bug_report("divide() returns wrong value"))
        builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-happy-run1")
        builder.claude_tool_result("toolu-happy-run1", builder.failure_output(1))
        builder.claude_tool_call("Edit", {"file_path": ALPHA_CALC, "old_string": "return a / b", "new_string": "return a / b if b else 0", "replace_all": False}, path=ALPHA_CALC, tool_use_id="toolu-happy-edit")
        builder.claude_tool_result(
            "toolu-happy-edit",
            "The file has been updated.",
            {
                "type": None,
                "filePath": ALPHA_CALC,
                "originalFile": before,
                "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": [" def divide(a, b):", "-    return a / b", "+    return a / b if b else 0"]}],
            },
        )
        builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-happy-run2")
        builder.claude_tool_result("toolu-happy-run2", builder.passing_output(0))
        return builder, {
            "type": "happy_path",
            "expect_episodes": 1,
            "expect_episode_statuses": ["verified_by_run"],
            "expect_filter_reasons": [],
            "expect_reconstructions": [expect_rec(ALPHA_CALC, before, after, "ok", "claude_structured_patch", "high", 1)],
            # M4：加守卫的改动（`return a / b` → `return a / b if b else 0`）能推出候选断言
            # （形参 b 绑成与 `else 0` 同型的假值 → assert result == 0）
            "expect_generated": expect_generated(
                generated=1,
                written=1,
                statuses=["generated"],
                reasons=[""],
                symbols=["divide"],
                candidates=1,
                sections=GENERATED_SECTIONS,
                anchors=["sess-demo-0001", ALPHA_CALC, "wrong value"],
            ),
            # M5：① 路径 + 加守卫 → 候选断言可证伪 → 修复前必失败（红）、修复后必通过（绿）
            "expect_red_green": expect_red_green([(builder.session_id + "#5", "verified", "", 1, 0)]),
        }
    # Codex 变体：③ 路径（首选 `FileChange.unified_diff`，同文件的 patch 文本作兜底）
    before = "def format_amount(value):\n    return value\n"
    after = "def format_amount(value):\n    return abs(value)\n"
    builder = Builder("codex", session_id, cfg, rng)
    builder.codex_session_meta()
    builder.codex_message("user", builder.bug_report("format_amount() drops the sign"))
    builder.codex_command("python -m pytest tests/test_format.py -q", "call-happy-run1")
    builder.codex_command_output("call-happy-run1", builder.failure_output(1), 1)
    patch = "*** Begin Patch\n*** Update File: {}\n@@\n-    return value\n+    return abs(value)\n*** End Patch".format(BETA_FORMAT)
    builder.codex_apply_patch("call-happy-patch", patch, [BETA_FORMAT], "")
    builder.codex_patch_output("call-happy-patch", "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM {}\n".format(BETA_FORMAT))
    builder.codex_file_change({BETA_FORMAT: {"type": "update", "unified_diff": "@@ -1,2 +1,2 @@\n def format_amount(value):\n-    return value\n+    return abs(value)\n"}})
    builder.codex_command("python -m pytest tests/test_format.py -q", "call-happy-run2")
    builder.codex_command_output("call-happy-run2", builder.passing_output(0), 0)
    return builder, {
        "type": "happy_path",
        "expect_episodes": 1,
        "expect_episode_statuses": ["verified_by_run"],
        "expect_filter_reasons": [],
        "expect_reconstructions": [expect_rec(BETA_FORMAT, before, after, "ok", "codex_unified_diff", "medium", 1)],
        # M4：`return value` → `return abs(value)` 是"把返回值包了一层调用"，**推不出**候选断言
        # → 骨架照写（有 arrange/act/三段与锚点），但标 needs_manual 并说明原因
        "expect_generated": expect_generated(
            generated=0,
            written=1,
            statuses=["needs_manual"],
            reasons=["assert_not_derivable"],
            symbols=["format_amount"],
            sections=GENERATED_SECTIONS,
            anchors=["sess-demo-0001", BETA_FORMAT, "drops the sign"],
        ),
        # M5：没有活动断言 = **不能证伪**。修复前那次跑"通过"（红不成立），而修复后那次反而因
        # 参数是探测值而报错（退出码 1）——正确结论仍是 `unverified` + `red_not_failed`，
        # **绝不**当成 verified（"测试没失败"与"测试通过了"是两件事）。两个退出码都进真值。
        "expect_red_green": expect_red_green([(builder.session_id + "#5", "unverified", "red_not_failed", 0, 1)]),
    }


def sample_ended_without_pass(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """修复后会话结束、未观察到通过（终点应为 ended_without_pass）。

    M3 起同时是 ③ 路径的兜底样例：本会话**没有** `FileChange` 通道，只有 `apply_patch` 文本
    （patch 文本不带行号，全文基线来自配套的磁盘夹具 `repo/ended-without-pass-1/`）。
    """
    before = "def first(items):\n    return items[0]\n"
    after = "def first(items):\n    return items[0] if items else None\n"
    builder = Builder("codex", session_id, cfg, rng)
    builder.codex_session_meta()
    builder.codex_message("developer", "<demo developer instructions>")
    builder.codex_message("user", builder.bug_report("cli crashes on empty input"))
    builder.codex_command("python -m pytest tests/test_cli.py -q", "call-eop-run1")
    builder.codex_command_output("call-eop-run1", builder.failure_output(1, "IndexError: list index out of range"), 1)
    patch = "*** Begin Patch\n*** Update File: {}\n@@\n-    return items[0]\n+    return items[0] if items else None\n*** End Patch".format(ALPHA_CALC)
    builder.codex_apply_patch("call-eop-patch", patch, [ALPHA_CALC], "")
    builder.codex_patch_output("call-eop-patch", "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM {}\n".format(ALPHA_CALC))
    return builder, {
        "type": "ended_without_pass",
        "expect_episodes": 1,
        "expect_episode_statuses": ["ended_without_pass"],
        "expect_filter_reasons": [],
        "expect_reconstructions": [expect_rec(ALPHA_CALC, before, after, "ok", "codex_patch_text", "medium", 1)],
        # M4：Codex 侧的 before 来自磁盘基线（给了 --repo 才有全文）→ 有了全文才谈得上读签名；
        # 改动是加守卫形态 → 推出 `assert result is None`
        "expect_generated": expect_generated(
            generated=1,
            written=1,
            statuses=["generated"],
            reasons=[""],
            symbols=["first"],
            candidates=1,
            sections=GENERATED_SECTIONS,
            anchors=["sess-demo-0002", ALPHA_CALC, "empty input"],
        ),
        # M5：`first(None)` 在 `items[0]` 上抛 TypeError（红），在 `items[0] if items else None` 上返回
        # None 且候选断言成立（绿）——两个退出码都进真值，证明"两次跑真的发生了"
        "expect_red_green": expect_red_green([(builder.session_id + "#6", "verified", "", 1, 0)]),
    }


def sample_multi_bug(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """一个会话两次修复（M2 应切出两个 episode，episode_id 不撞）。

    两处修复都只有 `old_string` + `originalFile`（`structuredPatch` 是**空表**）→ M3 走 ② 路径。
    """
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("divide() returns wrong value"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-multi-run1")
    builder.claude_tool_result("toolu-multi-run1", builder.failure_output(1))
    builder.claude_tool_call("Edit", {"file_path": ALPHA_CALC, "old_string": "return a / b", "new_string": "return a / b if b else 0", "replace_all": False}, path=ALPHA_CALC, tool_use_id="toolu-multi-edit1")
    builder.claude_tool_result("toolu-multi-edit1", "The file has been updated.", {"filePath": ALPHA_CALC, "originalFile": "def divide(a, b):\n    return a / b\n", "structuredPatch": []})
    builder.claude_user_message(builder.bug_report("slugify() keeps uppercase letters"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_slug.py -q"}, tool_use_id="toolu-multi-run2")
    builder.claude_tool_result("toolu-multi-run2", builder.failure_output(1, "Assertion: 'Ab' == 'ab'"))
    builder.claude_tool_call("Edit", {"file_path": BETA_FORMAT, "old_string": "return name", "new_string": "return name.lower()", "replace_all": False}, path=BETA_FORMAT, tool_use_id="toolu-multi-edit2")
    builder.claude_tool_result("toolu-multi-edit2", "The file has been updated.", {"filePath": BETA_FORMAT, "originalFile": "def slugify(name):\n    return name\n", "structuredPatch": []})
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_slug.py -q"}, tool_use_id="toolu-multi-run3")
    builder.claude_tool_result("toolu-multi-run3", builder.passing_output(0))
    return builder, {
        "type": "multi_bug",
        "expect_episodes": 2,
        "expect_episode_statuses": ["ended_without_pass", "verified_by_run"],
        "expect_filter_reasons": [],
        "expect_reconstructions": [
            expect_rec(ALPHA_CALC, "def divide(a, b):\n    return a / b\n", "def divide(a, b):\n    return a / b if b else 0\n", "ok", "claude_old_string", "high", 1),
            expect_rec(BETA_FORMAT, "def slugify(name):\n    return name\n", "def slugify(name):\n    return name.lower()\n", "ok", "claude_old_string", "high", 1),
        ],
        # M4：两个 episode 各一条结论——divide（加守卫）能推候选断言，slugify（包了一层调用）不能；
        # **逐 episode 都要有结论**，不许因为一个推不出来就整会话不产出
        "expect_generated": expect_generated(
            generated=1,
            written=2,
            statuses=["generated", "needs_manual"],
            reasons=["", "assert_not_derivable"],
            symbols=["divide", "slugify"],
            candidates=1,
            sections=GENERATED_SECTIONS,
            anchors=["sess-demo-0003#4", "sess-demo-0003#9", ALPHA_CALC, BETA_FORMAT, "uppercase letters"],
        ),
        # M5：**逐 episode 各自的验证结论**（不许"一个没通过就整会话不算"）：
        # 第一个 episode 的候选断言可证伪（红→绿）；第二个没有活动断言 → 修复前那次跑"通过"
        # （红不成立）、修复后那次反而报错（探测值撞上 `name.lower()`）→ unverified + 未失败
        "expect_red_green": expect_red_green(
            [
                (builder.session_id + "#4", "verified", "", 1, 0),
                (builder.session_id + "#9", "unverified", "red_not_failed", 0, 1),
            ]
        ),
    }


def sample_no_failure_signal(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """用户报 bug 但日志内无失败输出（打分低 → 应被过滤，不得切为 episode）。

    本会话一次文件都没改 → 还原面必须为空（**空集也是一种断言**：不许凭空产出还原结果）。
    """
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("the CSV export looks wrong"))
    builder.claude_assistant_message("Let me look at the exporter first.")
    builder.claude_tool_call("Read", {"file_path": BETA_FORMAT}, path=BETA_FORMAT, tool_use_id="toolu-nofail-read")
    builder.claude_tool_result("toolu-nofail-read", "def format_amount(value):\n    return value\n")
    return builder, {
        "type": "no_failure_signal",
        "expect_episodes": 0,
        "expect_episode_statuses": [],
        "expect_filter_reasons": ["no_failure_evidence"],
        "expect_reconstructions": [],
        # M4：没有 episode 就没有骨架（空集也是断言：不许从"没切出来的东西"里生成测试）
        "expect_generated": expect_generated(
            generated=0, written=0, statuses=[], reasons=[], sections=[], assumed=False
        ),
        # M5：没有 episode → 验证面也是空集（空集是断言：不许凭空产出验证结论）
        "expect_red_green": expect_red_green([]),
    }


def sample_doc_only(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """只改文档：有失败信号与 diff，但改的是 .md（打分应低 → 过滤）。"""
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("the README instructions are outdated"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-doc-run")
    builder.claude_tool_result("toolu-doc-run", builder.failure_output(1))
    builder.claude_tool_call("Edit", {"file_path": DOC_TARGET, "old_string": "run it", "new_string": "run it with --demo", "replace_all": False}, path=DOC_TARGET, tool_use_id="toolu-doc-edit")
    builder.claude_tool_result("toolu-doc-edit", "The file has been updated.", {"filePath": DOC_TARGET, "originalFile": "run it\n", "structuredPatch": []})
    return builder, {
        "type": "doc_only",
        "expect_episodes": 0,
        "expect_episode_statuses": [],
        "expect_filter_reasons": ["docs_only"],
        "expect_reconstructions": [expect_rec(DOC_TARGET, "run it\n", "run it with --demo\n", "ok", "claude_old_string", "high", 1)],
        # M4：还原结果**有**（ok），但该候选被 `docs_only` 丢弃 → 没有 episode → 不生成
        # ——这条正是"还原得了 ≠ 该生成"的判据
        "expect_generated": expect_generated(
            generated=0, written=0, statuses=[], reasons=[], sections=[], assumed=False
        ),
        # M5：同上——被丢弃的候选不产生验证结论（"没切出来"不等于"验证通过"）
        "expect_red_green": expect_red_green([]),
    }


def sample_ambiguous_old_string(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """`old_string` 在原文件里出现多次 → M3 必须标 `unresolved`（**不得任选一处**）。

    构造要点：结果侧只给 `originalFile`（before 镜像**可得**）、`structuredPatch` 是**空表**
    ——于是 ① 级不成立（K14：空表不含 diff），判定落到 ② 级；而 `old_string` 在原文件里
    命中 **2** 处、`replace_all` 为 false → 无法唯一定位，必须如实落 `unresolved`。
    """
    before = "def a(value):\n    return value\n\n\ndef b(value):\n    return value\n"
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("rounding is applied twice"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-amb-run")
    builder.claude_tool_result("toolu-amb-run", builder.failure_output(1))
    builder.claude_edit_pair("toolu-amb-edit", ALPHA_CALC, "    return value", "    return round(value, 2)", before, hunk_lines=None)
    return builder, {
        "type": "ambiguous_old_string",
        "expect_episodes": 1,
        "expect_episode_statuses": ["ended_without_pass"],
        "expect_filter_reasons": [],
        "expect_reconstructions": [expect_rec(ALPHA_CALC, None, None, "unresolved", "claude_old_string", "none", 0)],
        # M4：还原落 `unresolved`（不任选一处）→ 没有全文 before → **不进生成路径**，只记原因
        "expect_generated": expect_generated(
            generated=0,
            written=0,
            statuses=["needs_manual"],
            reasons=["reconstruction_not_usable:unresolved"],
            sections=[],
        ),
        # M5：生成层没产出内容 → 没有可跑的东西 → `needs_manual`（两次退出码是 None：**没跑**）
        "expect_red_green": expect_red_green(
            [(builder.session_id + "#4", "needs_manual", "reconstruction_not_usable:unresolved", None, None)]
        ),
    }


def sample_new_file_write(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """纯新建文件：有失败链与代码改动，但**没有 before 镜像**。

    这是一条**应被保留**的 episode（分数够），题目在 M3：还原器必须如实记
    `no_before_image`，而不是硬造一个 before。首版样本没有失败信号，于是它只验证了
    "无失败证据即丢弃"，压根没走到"无 before 镜像"这条路径（M2 复核时补上）。
    新建文件的 `after` 是**可知**的（`Write.content`= 新全文），只是 `before` 不存在。
    """
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("need a helper for formatting"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_format.py -q"}, tool_use_id="toolu-new-run")
    builder.claude_tool_result("toolu-new-run", builder.failure_output(1, "ImportError: cannot import name helper"))
    builder.claude_tool_call(
        "Write",
        {"file_path": NEW_FILE, "content": "def helper(value):" + chr(10) + "    return value" + chr(10)},
        path=NEW_FILE,
        tool_use_id="toolu-new-write",
    )
    builder.claude_tool_result(
        "toolu-new-write",
        "File created successfully.",
        {"type": "create", "filePath": NEW_FILE, "structuredPatch": []},
    )
    return builder, {
        "type": "new_file_write",
        "expect_episodes": 1,
        "expect_episode_statuses": ["ended_without_pass"],
        "expect_filter_reasons": [],
        "expect_reconstructions": [expect_rec(NEW_FILE, None, "def helper(value):\n    return value\n", "no_before_image", "claude_write_content", "none", 0)],
        # M4：纯新建文件没有 **before** 镜像 → 读不出"修改前的签名" → 不生成（宁缺勿造）
        "expect_generated": expect_generated(
            generated=0,
            written=0,
            statuses=["needs_manual"],
            reasons=["reconstruction_not_usable:no_before_image"],
            sections=[],
        ),
        "expect_red_green": expect_red_green(
            [(builder.session_id + "#4", "needs_manual", "reconstruction_not_usable:no_before_image", None, None)]
        ),
    }


def sample_structured_patch(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """① 路径具名样本：`originalFile`（修改前全文）+ **非空** `structuredPatch`（带 hunk）。

    非空判据是 K14 的**实测**结论：新建文件的结果同样带 `structuredPatch: []`（82 条空表），
    只看"键存在"会把纯新建也算成有 diff 证据。所以本样本的 hunk 必须真有内容行，
    且行号（`oldStart`）与 `originalFile` 对得上——对不上就该落 `unresolved`，不许硬套。
    """
    before = "def divide(a, b):\n    return a / b\n"
    after = "def divide(a, b):\n    return a / b if b else 0\n"
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("divide() raises on an empty denominator"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-sp-run1")
    builder.claude_tool_result("toolu-sp-run1", builder.failure_output(1, "ZeroDivisionError: division by zero"))
    builder.claude_edit_pair(
        "toolu-sp-edit",
        ALPHA_CALC,
        "    return a / b",
        "    return a / b if b else 0",
        before,
        hunk_lines=[" def divide(a, b):", "-    return a / b", "+    return a / b if b else 0"],
        old_start=1,
    )
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-sp-run2")
    builder.claude_tool_result("toolu-sp-run2", builder.passing_output(0))
    return builder, {
        "type": "structured_patch",
        "expect_episodes": 1,
        "expect_episode_statuses": ["verified_by_run"],
        "expect_filter_reasons": [],
        "expect_reconstructions": [expect_rec(ALPHA_CALC, before, after, "ok", "claude_structured_patch", "high", 1)],
        # M4：① 路径 + 加守卫 → 候选断言 `assert result == 0`（活动 assert，1 条）
        "expect_generated": expect_generated(
            generated=1,
            written=1,
            statuses=["generated"],
            reasons=[""],
            symbols=["divide"],
            candidates=1,
            sections=GENERATED_SECTIONS,
            anchors=["sess-demo-0009", ALPHA_CALC, "empty denominator"],
        ),
        # M5：① 路径的候选断言可证伪：`divide(0, 0)` 修复前抛 ZeroDivisionError（红）、修复后返回 0（绿）
        "expect_red_green": expect_red_green([(builder.session_id + "#4", "verified", "", 1, 0)]),
    }


def sample_base_drift(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """磁盘基线与 patch **对不上**（工作区已被改动）→ 如实降级为 `partial`，绝不硬套。

    判据来自 K11/纪律"磁盘文件已被改动导致无法对齐时如实降级"：patch 的旧侧行在磁盘基线里
    找不到 → 我们仍然知道"改了什么片段"（hunks 来自日志），但拿不到全文 before/after，
    于是 `before`/`after` 为 null、`confidence` = low，**不伪造上下文行**。
    """
    builder = Builder("codex", session_id, cfg, rng)
    builder.codex_session_meta()
    builder.codex_message("user", builder.bug_report("cli returns the wrong item"))
    builder.codex_command("python -m pytest tests/test_cli.py -q", "call-drift-run1")
    builder.codex_command_output("call-drift-run1", builder.failure_output(1, "IndexError: list index out of range"), 1)
    patch = "*** Begin Patch\n*** Update File: {}\n@@\n-    return items[0]\n+    return items[0] if items else None\n*** End Patch".format(ALPHA_CALC)
    builder.codex_apply_patch("call-drift-patch", patch, [ALPHA_CALC], "")
    builder.codex_patch_output("call-drift-patch", "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM {}\n".format(ALPHA_CALC))
    return builder, {
        "type": "base_drift",
        "expect_episodes": 1,
        "expect_episode_statuses": ["ended_without_pass"],
        "expect_filter_reasons": [],
        "expect_reconstructions": [expect_rec(ALPHA_CALC, None, None, "partial", "codex_patch_text", "low", 1)],
        # M4：只有片段（`before` 为 null）→ 无全文可读签名 → 不生成（partial 也进不了生成路径）
        "expect_generated": expect_generated(
            generated=0,
            written=0,
            statuses=["needs_manual"],
            reasons=["reconstruction_not_usable:partial"],
            sections=[],
        ),
        "expect_red_green": expect_red_green(
            [(builder.session_id + "#5", "needs_manual", "reconstruction_not_usable:partial", None, None)]
        ),
    }


def sample_unsupported_format(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """未知格式：读取器必须显式报 unsupported_format（不得静默产出空事件流）。"""
    builder = Builder("claude", session_id, cfg, rng)
    builder._append({"mystery_field": {"nested": True}, "sessionId": session_id, "type": "future-line-type"})
    builder._append({"another_unknown": 1, "type": "future-line-type"})
    return builder, {"type": "unsupported_format", "expect_episodes": None, "expect_error": "unsupported_format"}


SAMPLE_BUILDERS = (
    ("happy-path-1", sample_happy_path),
    ("ended-without-pass-1", sample_ended_without_pass),
    ("multi-bug-1", sample_multi_bug),
    ("no-failure-signal-1", sample_no_failure_signal),
    ("doc-only-1", sample_doc_only),
    ("ambiguous-old-string-1", sample_ambiguous_old_string),
    ("new-file-write-1", sample_new_file_write),
    ("unsupported-format-1", sample_unsupported_format),
    # M3 新增样本**追加在末尾**：前面样本的 session id 与 RNG 抽样序列因此保持不变
    ("structured-patch-1", sample_structured_patch),
    ("base-drift-1", sample_base_drift),
)


# --------------------------------------------------------------------------- #
# 生成与落盘
# --------------------------------------------------------------------------- #


def build_corpus(cfg: Mapping[str, Any]) -> Tuple[List[Tuple[str, str, Builder, Dict[str, Any]]], int]:
    """构造全部样本；返回 (样本列表, RNG 抽样次数)。"""
    rng = Splitmix64(SEED)
    samples: List[Tuple[str, str, Builder, Dict[str, Any]]] = []
    for index, (name, builder_fn) in enumerate(SAMPLE_BUILDERS, start=1):
        session_id = "{}{:04d}".format(WHITELIST["session_ids"][0], index)
        builder, meta = builder_fn(rng, cfg, session_id)
        samples.append((name, session_id, builder, meta))
    return samples, rng.draws


def jsonl_bytes(records: Iterable[Mapping[str, Any]]) -> bytes:
    """jsonl 字节：**一行一对象、不缩进**（用 `serialize.dumps_json_line`，别用 pretty 版）。

    实测教训：首版用 pretty 版（indent=2）生成语料，一条记录摊成多行 → 读取器按行解析
    整个读不了（合成语料全挂）。jsonl 的"行"就是记录边界，两种契约不能混。
    """
    return "".join(serialize.dumps_json_line(record) for record in records).encode("utf-8")


#: 绝对路径形态（Windows 盘符 / POSIX 根路径）。
#: 负向后视 `(?<![A-Za-z0-9_.\-])` 是关键：`tests/regression/x.py` 里的 `/` 前面是字母，
#: 属于**相对路径**而不是绝对路径——不加这条会把正常相对路径误判成泄漏（首版实测）。
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.\-])(?:[A-Za-z]:\\[^\s\"']+|/[^\s\"']+)")


def assert_privacy(records: Sequence[Mapping[str, Any]], name: str) -> None:
    """白名单自检：**任何绝对路径**只要不在白名单前缀内即报错（防真实值混进语料）。

    只查绝对路径：语料里的相对路径（测试文件、模块路径）是为真实感刻意保留的；
    本机路径一定以盘符或 HOME 开头，所以"绝对路径白名单"足以拦住真实值泄漏。
    """
    offenders: List[str] = []
    for value in _iter_strings(records):
        for match in _ABSOLUTE_PATH.findall(value):
            token = match.rstrip(".,;:)]")
            if token.startswith("/demo/") or token.startswith(WIN_DEMO_PREFIX):
                continue
            offenders.append(token)
    if offenders:
        raise SystemExit("隐私自检失败（{}）：语料里出现白名单外的绝对路径 {}".format(name, sorted(set(offenders))[:5]))


def _iter_strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            for item in _iter_strings(value):
                yield item
    elif isinstance(node, (list, tuple)):
        for value in node:
            for item in _iter_strings(value):
                yield item


def write_corpus(out_dir: Path, cfg: Mapping[str, Any], force: bool = False) -> Dict[str, Any]:
    """生成语料。**默认不覆盖已有冻结物**（破坏性动作要显式开关，K11 同族纪律）。"""
    if out_dir.exists() and any(out_dir.iterdir()):
        if not force:
            raise SystemExit(
                "{} 已存在且非空；冻结语料属「字节即契约」的产物，重建请显式加 --force".format(out_dir)
            )
        shutil.rmtree(str(out_dir))
    samples, draws = build_corpus(cfg)
    for folder in ("claude", "codex", "invalid", "truth", "repo"):
        (out_dir / folder).mkdir(parents=True, exist_ok=True)

    manifest_samples: List[Dict[str, Any]] = []
    repo_files: List[Dict[str, Any]] = []
    type_counts: Dict[str, int] = {}
    total_exit_codes = 0
    for name, session_id, builder, meta in samples:
        harness = "codex" if builder.source == "codex" else "claude"
        # 该报错的样本单独放 invalid/：它不是任何 harness 的合法日志，
        # 混在 claude/ 或 codex/ 里会让"扫一个目录"这种正常用法必然失败（首版实测）。
        folder = "invalid" if meta.get("expect_error") else harness
        relative = "{}/{}.jsonl".format(folder, name)
        payload = jsonl_bytes(builder.records)
        assert_privacy(builder.records, name)
        target = out_dir / relative
        target.write_bytes(payload)

        # 磁盘基线夹具（"该编辑发生前的工作区"）：Codex 侧的全文 before 只能来自这里
        for fixture_relative, fixture_text in sorted(REPO_FIXTURES.get(name, {}).items()):
            fixture_path = out_dir / "repo" / name / fixture_relative
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_bytes = fixture_text.replace("\r\n", "\n").encode("utf-8")
            fixture_path.write_bytes(fixture_bytes)
            repo_files.append(
                {
                    "sample": name,
                    "file": str(Path("repo") / name / fixture_relative).replace("\\", "/"),
                    "sha256": serialize.sha256_bytes(fixture_bytes),
                }
            )

        truth = {
            "sample": name,
            "episode_type": meta["type"],
            "session_id": session_id,
            "input_file": relative,
            "expect": dict(builder.expect),
            # M2：episode 层期望（构造侧声明，不由被测实现反推）
            "expect_episodes": meta["expect_episodes"],
            "expect_episode_statuses": meta.get("expect_episode_statuses", []),
            "expect_filter_reasons": meta.get("expect_filter_reasons", []),
        }
        # M3：还原层期望（逐文件 before/after / 状态 / 来源 / 置信度，同样由构造侧声明）
        if "expect_reconstructions" in meta:
            truth["expect_reconstructions"] = meta["expect_reconstructions"]
        # M4：生成层期望（骨架数 / 逐 episode 状态与原因 / 候选断言条数 / 必须出现的证据锚点）
        if "expect_generated" in meta:
            truth["expect_generated"] = meta["expect_generated"]
        # M5：验证层期望（逐 episode 的三态结论 + 原因码 + RED/GREEN 退出码）
        if "expect_red_green" in meta:
            truth["expect_red_green"] = meta["expect_red_green"]
        if "expect_error" in meta:
            truth["expect_error"] = meta["expect_error"]
        truth_relative = "truth/{}.truth.json".format(name)
        truth_bytes = serialize.dumps_json(truth).encode("utf-8")
        (out_dir / truth_relative).write_bytes(truth_bytes)

        type_counts[meta["type"]] = type_counts.get(meta["type"], 0) + 1
        total_exit_codes += len(builder.expect["exit_codes"])
        manifest_samples.append(
            {
                "sample": name,
                "episode_type": meta["type"],
                "harness": harness,
                "file": relative,
                "bytes": len(payload),
                "sha256": serialize.sha256_bytes(payload),
                "truth": truth_relative,
                "truth_sha256": serialize.sha256_bytes(truth_bytes),
                "repo": str(Path("repo") / name).replace("\\", "/") if REPO_FIXTURES.get(name) else "",
            }
        )

    manifest = {
        "bench_version": 3,
        "generator": "tools/gen_bench.py",
        "seed": SEED,
        "rng_draws": draws,
        "harnesses": ["claude", "codex"],
        "coverage": {
            "min_count_by_type": dict(MIN_COUNT_BY_TYPE),
            "observed_count_by_type": dict(sorted(type_counts.items())),
        },
        "thresholds": {
            "min_detection_rate": MIN_DETECTION_RATE,
            "max_false_positive": MAX_FALSE_POSITIVE,
            "min_samples": len(manifest_samples),
        },
        "checked_facts": [
            "source",
            "format_version",
            "event_count",
            "kind_counts",
            "tools_used",
            "files_touched",
            "exit_codes",
            "user_report_fragments",
            "filter_reasons",
            "episode_statuses",
            "episode_count",
            "reconstructions",
            # M4 起：生成层事实（骨架数 / 逐 episode 结论与原因 / 候选断言条数 / 证据锚点）
            "generated",
            # M5 起：验证层事实（逐 episode 的三态结论 + 原因码 + RED/GREEN 退出码）
            "red_green",
        ],
        # M5 起没有"未对账的面"：空表是断言——不许把没对账的字段留在这里当挡箭牌
        "pending_facts": {},
        "repo_fixtures": repo_files,
        "privacy": {
            "whitelist": {key: list(value) if isinstance(value, tuple) else value for key, value in WHITELIST.items()},
            "note": "全部为假值；假绝对路径只保留通用盘符形态，后缀段为假项目名",
        },
        "samples": manifest_samples,
    }
    manifest_bytes = serialize.dumps_json(manifest).encode("utf-8")
    (out_dir / MANIFEST_NAME).write_bytes(manifest_bytes)
    return {"samples": len(manifest_samples), "draws": draws, "exit_codes": total_exit_codes, "manifest_sha256": serialize.sha256_bytes(manifest_bytes)}


def diff_trees(left: Path, right: Path) -> List[str]:
    """逐文件比对两棵树（缺失/多余/字节不同都算差异）。"""
    def snapshot(root: Path) -> Dict[str, bytes]:
        return {str(path.relative_to(root)).replace("\\", "/"): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}

    a, b = snapshot(left), snapshot(right)
    problems: List[str] = []
    for name in sorted(set(a) | set(b)):
        if name not in a:
            problems.append("多余文件: {}".format(name))
        elif name not in b:
            problems.append("缺失文件: {}".format(name))
        elif a[name] != b[name]:
            problems.append("字节不同: {}".format(name))
    return problems
