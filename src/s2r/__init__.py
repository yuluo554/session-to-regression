"""session-to-regression：从本机 agent 会话日志固化回归测试。

定位：读取本机 Claude Code / Codex 会话日志，切出「用户报 bug → 出现失败信号 → 改文件修好」
的 episode，把修复 diff 反推成可复跑的 pytest 骨架，并做 RED/GREEN 双验证。

纪律：纯本地、零网络、零遥测、不调用任何模型；修改前内容来自日志本身，不依赖 git。
"""

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]
