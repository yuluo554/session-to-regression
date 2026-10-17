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


def sample_happy_path(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """正常修复链：报 bug → 测试失败 → 改代码 → 测试通过（Claude 侧）；Codex 侧同型。"""
    if rng.below(2) == 0:
        # Claude 变体
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
                "originalFile": "def divide(a, b):\n    return a / b\n",
                "structuredPatch": [{"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": [" def divide(a, b):", "-    return a / b", "+    return a / b if b else 0"]}],
            },
        )
        builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-happy-run2")
        builder.claude_tool_result("toolu-happy-run2", builder.passing_output(0))
    else:
        builder = Builder("codex", session_id, cfg, rng)
        builder.codex_session_meta()
        builder.codex_message("user", builder.bug_report("format_amount() drops the sign"))
        builder.codex_command("python -m pytest tests/test_format.py -q", "call-happy-run1")
        builder.codex_command_output("call-happy-run1", builder.failure_output(1), 1)
        patch = "*** Begin Patch\n*** Update File: {}\n@@\n-    return value\n+    return abs(value)\n*** End Patch".format(BETA_FORMAT)
        builder.codex_apply_patch("call-happy-patch", patch, [BETA_FORMAT], "")
        builder.codex_patch_output("call-happy-patch", "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM {}\n".format(BETA_FORMAT))
        builder.codex_file_change({BETA_FORMAT: {"type": "update", "unified_diff": "@@ -1 +1 @@\n-    return value\n+    return abs(value)\n"}})
        builder.codex_command("python -m pytest tests/test_format.py -q", "call-happy-run2")
        builder.codex_command_output("call-happy-run2", builder.passing_output(0), 0)
    return builder, {"type": "happy_path", "expect_episodes": 1, "expect_episode_statuses": ["verified_by_run"], "expect_filter_reasons": []}


def sample_ended_without_pass(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """修复后会话结束、未观察到通过（终点应为 ended_without_pass）。"""
    builder = Builder("codex", session_id, cfg, rng)
    builder.codex_session_meta()
    builder.codex_message("developer", "<demo developer instructions>")
    builder.codex_message("user", builder.bug_report("cli crashes on empty input"))
    builder.codex_command("python -m pytest tests/test_cli.py -q", "call-eop-run1")
    builder.codex_command_output("call-eop-run1", builder.failure_output(1, "IndexError: list index out of range"), 1)
    patch = "*** Begin Patch\n*** Update File: {}\n@@\n-    return items[0]\n+    return items[0] if items else None\n*** End Patch".format(ALPHA_CALC)
    builder.codex_apply_patch("call-eop-patch", patch, [ALPHA_CALC], "")
    builder.codex_patch_output("call-eop-patch", "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM {}\n".format(ALPHA_CALC))
    return builder, {"type": "ended_without_pass", "expect_episodes": 1, "expect_episode_statuses": ["ended_without_pass"], "expect_filter_reasons": []}


def sample_multi_bug(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """一个会话两次修复（M2 应切出两个 episode，episode_id 不撞）。"""
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
    return builder, {"type": "multi_bug", "expect_episodes": 2, "expect_episode_statuses": ["ended_without_pass", "verified_by_run"], "expect_filter_reasons": []}


def sample_no_failure_signal(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """用户报 bug 但日志内无失败输出（打分低 → 应被过滤，不得切为 episode）。"""
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("the CSV export looks wrong"))
    builder.claude_assistant_message("Let me look at the exporter first.")
    builder.claude_tool_call("Read", {"file_path": BETA_FORMAT}, path=BETA_FORMAT, tool_use_id="toolu-nofail-read")
    builder.claude_tool_result("toolu-nofail-read", "def format_amount(value):\n    return value\n")
    return builder, {"type": "no_failure_signal", "expect_episodes": 0, "expect_episode_statuses": [], "expect_filter_reasons": ["no_failure_evidence"]}


def sample_doc_only(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """只改文档：有失败信号与 diff，但改的是 .md（打分应低 → 过滤）。"""
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("the README instructions are outdated"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-doc-run")
    builder.claude_tool_result("toolu-doc-run", builder.failure_output(1))
    builder.claude_tool_call("Edit", {"file_path": DOC_TARGET, "old_string": "run it", "new_string": "run it with --demo", "replace_all": False}, path=DOC_TARGET, tool_use_id="toolu-doc-edit")
    builder.claude_tool_result("toolu-doc-edit", "The file has been updated.", {"filePath": DOC_TARGET, "originalFile": "run it\n", "structuredPatch": []})
    return builder, {"type": "doc_only", "expect_episodes": 0, "expect_episode_statuses": [], "expect_filter_reasons": ["docs_only"]}


def sample_ambiguous_old_string(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """old_string 在原文件里出现多次（M3 必须标 unresolved，不得任选一处）。"""
    builder = Builder("claude", session_id, cfg, rng)
    builder.claude_user_message(builder.bug_report("rounding is applied twice"))
    builder.claude_tool_call("Bash", {"command": "python -m pytest tests/test_calc.py -q"}, tool_use_id="toolu-amb-run")
    builder.claude_tool_result("toolu-amb-run", builder.failure_output(1))
    builder.claude_tool_call("Edit", {"file_path": ALPHA_CALC, "old_string": "    return value", "new_string": "    return round(value, 2)", "replace_all": False}, path=ALPHA_CALC, tool_use_id="toolu-amb-edit")
    builder.claude_tool_result(
        "toolu-amb-edit",
        "The file has been updated.",
        {
            "filePath": ALPHA_CALC,
            "originalFile": "def a(value):\n    return value\n\n\ndef b(value):\n    return value\n",
            "structuredPatch": [{"oldStart": 2, "oldLines": 1, "newStart": 2, "newLines": 1, "lines": ["-    return value", "+    return round(value, 2)"]}],
        },
    )
    return builder, {"type": "ambiguous_old_string", "expect_episodes": 1, "expect_episode_statuses": ["ended_without_pass"], "expect_filter_reasons": []}


def sample_new_file_write(rng: Splitmix64, cfg: Mapping[str, Any], session_id: str) -> Tuple[Builder, Dict[str, Any]]:
    """纯新建文件：有失败链与代码改动，但**没有 before 镜像**。

    这是一条**应被保留**的 episode（分数够），题目在 M3：还原器必须如实记
    `no_before_image`，而不是硬造一个 before。首版样本没有失败信号，于是它只验证了
    "无失败证据即丢弃"，压根没走到"无 before 镜像"这条路径（M2 复核时补上）。
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
    for folder in ("claude", "codex", "invalid", "truth"):
        (out_dir / folder).mkdir(parents=True, exist_ok=True)

    manifest_samples: List[Dict[str, Any]] = []
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
            }
        )

    manifest = {
        "bench_version": 1,
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
        ],
        "pending_facts": {
            "before_after": "M3：before/after 还原落地后由 bench 逐字符对账",
            "generated_tests": "M4：pytest 骨架生成落地后由 bench 对账生成内容",
            "red_green": "M5：RED/GREEN 双验证落地后由 bench 对账退出码",
        },
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
