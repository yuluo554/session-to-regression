"""异常层级与退出码契约（全项目唯一声明处）。

退出码语义：

======  ================================================================
退出码  语义
======  ================================================================
0       成功；对 verify / run 表示「至少一条 verified」
1       未达标：无 verified（含全部 unverified）、基准未达标、selftest 自检失败
2       用法错误或输入不可用：未知子命令、参数错误、路径不存在、未知日志格式
3       运行依赖缺失（如 pytest 不可用）——不得退化为「零条结果 + exit 0」
======  ================================================================

> 历史：骨架期曾占用 **4 = 阶段未就绪**（未交付命令不产结果）。M5 之后五条子命令全部交付，
> 该语义**整项退役**（`cli` 命令表 / `tools/ci_smoke.py` / README 退出码表 / `plan/04·06`
> 四处同一次提交同步）——留着它就是让"幽灵语义"复活。

所有领域异常都继承 ``ValueError``：CLI 那条 ``except`` 抓得住，不会逃逸成未捕获的 traceback
（「输入有问题，退出码 2」与「程序崩了」是两种用户可见结果）。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_USAGE = 2
EXIT_DEPENDENCY_MISSING = 3

#: 允许出现的退出码全集；README 的退出码表必须逐条覆盖（守门测试锁死）。
EXIT_CODES = (EXIT_OK, EXIT_CHECK_FAILED, EXIT_USAGE, EXIT_DEPENDENCY_MISSING)


class S2RError(ValueError):
    """所有领域异常的基类：携带机读 code 与进程退出码。"""

    code = "s2r_error"
    exit_code = EXIT_USAGE

    def __init__(self, message: str, hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def lines(self) -> List[str]:
        out = ["{}: {}".format(self.code, self.message)]
        if self.hint:
            out.append("hint: {}".format(self.hint))
        return out


class UsageError(S2RError):
    """参数/用法错误。"""

    code = "usage_error"
    exit_code = EXIT_USAGE


class InputUnavailable(S2RError):
    """输入不可用：路径不存在、文件不可读、目标仓库不可用、配置不可读。"""

    code = "input_unavailable"
    exit_code = EXIT_USAGE


class ConfigError(InputUnavailable):
    """配置文件读不懂（语法或形状不合法）。绝不回落到默认值。"""

    code = "config_invalid"


class UnsupportedFormat(InputUnavailable):
    """日志格式不认识：显式报错并列出缺失键，不得静默产出空事件流。"""

    code = "unsupported_format"

    def __init__(self, message: str, missing_keys: Tuple[str, ...] = (), hint: Optional[str] = None) -> None:
        super().__init__(message, hint=hint)
        self.missing_keys = tuple(missing_keys)

    def lines(self) -> List[str]:
        out = super().lines()
        if self.missing_keys:
            out.append("missing_keys: {}".format(", ".join(self.missing_keys)))
        return out


class DependencyMissing(S2RError):
    """运行依赖缺失（pytest 等）。"""

    code = "dependency_missing"
    exit_code = EXIT_DEPENDENCY_MISSING


class NotASessionLog(S2RError):
    """文件根本不是会话日志（如 harness 状态文件）。

    实测：`~/.claude/projects/**/*.jsonl` 里混着大量非会话文件（本机 125 个文件里 122 个是会话日志、3 个是子代理索引一类的东西）。批量扫描时这类文件按件隔离并**显式列出**（不是「格式不支持」，也不是静默跳过）；单文件指向它时是一种用法错误（退出码 2）。批量扫描时这类文件按件隔离并**显式列出**（不是"格式不支持"，
    也不是静默跳过）；单文件指向它时是一种用法错误（退出码 2）。
    """

    code = "not_a_session_log"
    exit_code = EXIT_USAGE

    def __init__(self, message: str, observed_types: Tuple[str, ...] = (), hint: Optional[str] = None) -> None:
        super().__init__(message, hint=hint)
        self.observed_types = tuple(observed_types)

    def lines(self) -> List[str]:
        out = super().lines()
        if self.observed_types:
            out.append("observed_line_types: {}".format(", ".join(self.observed_types)))
        return out


class CheckFailed(S2RError):
    """自检/基准未达标。"""

    code = "check_failed"
    exit_code = EXIT_CHECK_FAILED
