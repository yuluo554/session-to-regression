# HANDOFF-M3（下一棒：M3 差分还原）

> 由 M2（episode 切分，2026-10-17）交出。**本文件是下一棒的唯一入口**：新对话零上下文，
> 上下文全在这里与 `plan/`、`data/`、`README.md` 里。
> 用法（新对话第一条）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M3.md" 继续完成任务`
> **库内一律相对路径**；绝对路径只出现在用户侧的启动命令里。

---

## 一、当前进度 + 证据

**已完成：M0 骨架 ✅、M1 数据先行与读取器 ✅、M2 episode 切分 ✅**
（状态表见 `plan/06-里程碑.md`；逐条实跑留档见 `plan/verify/README.md`）

| 交付物 | 证据位置 |
|---|---|
| 双 harness 读取器（按 `payload.type` 分流 → 事件流 IR；`seq` = 源行号 + 字节偏移） | `src/s2r/readers/{__init__,claude,codex}.py` |
| 批量隔离：非会话日志 → `skipped[]`、读不了 → `failed[]`（都显式）；exit 0/1/2 语义 | 同上 + `cli.cmd_scan` |
| episode 切分（起点 → 失败信号 → 修复动作 → 终点；权重/阈值只读配置） | `src/s2r/episode.py`、`tests/test_episode.py`（22 项） |
| `s2r scan` 输出 `episodes[]`/`filtered[]`（schema 2） | `tests/test_scan_cli.py` |
| 合成语料 + 植入真值 + 台账（8 类样本，含 episode 层期望） | `data/bench/**`、`src/s2r/benchdata.py` |
| 基准对账（双边门 + 逐样本点名 + 台账 sha256 + **六探活**） | `src/s2r/bench.py`、`tests/test_bench.py` |
| 测试与门禁 | `tests/`（**204 项**）、`tools/ci_smoke.py`（20/20）、`tools/fix_eol.py` |

**已验证的事实**：双解释器各 204 项通过；`gen_bench --check` 位级一致；`s2r bench` = `ok`（检出 8/8、误报 0，
`pending` 只剩 M3–M5 三个面）；**六个探活全部检出**；7 项新增守门变异验证全红；
**双源真实日志只读全量扫描通过**（Claude 122 读入/3 跳过 → 94 会话/11 episode；Codex 220 读入 → 63 会话/64 episode）。
**CI 未接远端 → 一切 CI 结论为"本地等价校验，未接远端，CI 待首推验证"。**

---

## 二、下一棒待办（M3 差分还原）

1. **`src/s2r/reconstruct.py`**：三级降级还原（题面 §2 M3）
   * ① Claude：`originalFile`（修改前全文）+ 非空 `structuredPatch`（有 hunk）——**判据按"键存在且非空"**（K14）；
   * ② Claude：`old_string`/`new_string` 在 `originalFile`／磁盘文件／同会话 `Write.content` 上定位替换，
     **多处命中且无法唯一定位 → `unresolved`（不得任选一处）**；
   * ③ Codex：**首选 `FileChange.changes[*].unified_diff`**（结构化、带路径），`*** Begin Patch` 文本解析降为兜底；
   * ④ 都不可得 → 该文件不进生成路径，episode 记 `unverified: no_before_image`。
2. **原文回溯**：事件流**不复制大文本**——用 `readers.read_source_line(path, seq)` 按 `seq` 取回原始行
   （`episode.source_file` 给出文件路径）；同一日志 + 同一磁盘状态 → 同一 diff。
3. **输出**：`Reconstruction{file, before, after, hunks[], source, confidence}`；多文件 → 列表，**逐文件独立置信度**。
4. **`s2r reconstruct SESSION`** 落地（退役 exit 4 四处同步：`cli.STAGED_COMMANDS` → `tools/ci_smoke.py`
   的 `DELIVERED_EXPECT` → README 退出码表 → `plan/04·06` 状态列）。
5. **基准**：① 语料真值加 `expect_reconstructions`（逐文件 before/after，构造侧声明）；
   ② `bench` 增加还原事实（每文件 `source`/`confidence`/`before==期望`）并对账；
   ③ `pending_facts.before_after` 从 pending 转为真对账；④ 新增探活（如"把 before 换成 after"必须被检出）。
   * 语料已备好对应样本：`ambiguous_old_string`（应 `unresolved`）、`new_file_write`（应 `no_before_image`）、
     `happy_path`/`multi_bug`（应给出 before/after 且 confidence 高）。

---

## 三、既定口径清单（K 编号，动了会打挂测试）

> 完整表见 `plan/07-交付对标与决策记录.md` §五；下一棒必须遵守的最小集：

* **K1** 唯一数值口径 = `config/scoring.toml`（+ 随包副本逐字节相同）；**代码内零口径数值**。
  ⚠️ 改 `config/` 后**必须** `cp config/scoring.toml src/s2r/resources/scoring.toml`（否则 `selftest` 的 `config_copies_identical` 当场红）。
* **K5/K6** 无时钟无随机无网络；同输入双跑逐字节一致；写盘只经 `serialize`，**jsonl 用 `dumps_json_line`**（K18）。
* **K13/K20** 库内相对路径、不复述词面；判定词表只准来自配置（集合字面量里出现即红）。
* **K14** before 镜像判据 = **键存在且非空**（空 `structuredPatch` 不算 diff，实测新建文件就是空表）。
* **K21** 批量扫描按件隔离（`skipped`/`failed` 分区 + 退出码语义）。
* **K22** "转为通过"必须**同一条测试命令**（按 `call_id`/`tool_use_id` 配对，不按时间序猜）。
* **K23** 「只改文档」是独立丢弃规则（`docs_only`），不靠阈值碰巧过滤。
* **K19** 探活注入打在**判定路径**上（记为误报/不符），不得"直接记通过"。
* 基准作用域分工：完整性门（台账/在位/覆盖度）看全量语料；判定门（检出率/误报）看 `--only` 选中项。

---

## 四、本机环境坑（只写实测过的）

| 坑 | 处置 |
|---|---|
| **用脚本改代码文件风险高**（转义层吃反斜杠 + 行手术易留残括号，本项目 M2 踩过两次） | 改**代码**用编辑工具逐处改；脚本只用于文档/数据回写，且改完立刻跑 `pytest` 与 `py -X utf8 tools/fix_eol.py` |
| 脚本写文件在 Windows 会变 CRLF | 一律 `open(str(p), "w", encoding="utf-8", newline=chr(10))`；改完跑 `tools/fix_eol.py` |
| `Path.write_text(newline=...)` 是 3.10+ 参数 | 3.8 通道用 `open(..., newline=chr(10))` |
| Bash 里 `\r`/`\n` 转义会被工具层吃掉一层 | 判断行尾用 Python 读字节；生成文件优先用编辑工具 |
| 真实日志大体量（Claude 125 文件 / Codex 831 MB 量级） | 只读探针逐行 + 早停；**单行上限 16 MiB**（口径文件，实测有 8.8 MB 的行） |
| `~/.claude/projects` 里混着非会话 jsonl | 已按件隔离为 `skipped`（`not_a_session_log`），别改成"静默跳过" |
| 两个解释器通道 | `py -3.8` 与 `py -3.12` 都要跑；3.8 是底线（禁 3.9+ 语法） |
| 本机 venv `.venv-check/`（3.12，editable 装） | 安装面复核：`PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py` |

---

## 五、DoD 逐项 checklist（M3）

- [ ] 三级降级还原实现，每级标 `source` 与 `confidence`；**逐文件**独立置信度
- [ ] `originalFile`/非空 `structuredPatch` 为第一优先；`old_string` 多处命中 → `unresolved`（**不任选**）
- [ ] Codex 侧优先 `FileChange.unified_diff`，patch 文本作兜底；**绝不伪造上下文行**
- [ ] 都不可得 → `unverified: no_before_image`（合成语料的 `new_file_write` 样本必须走这条路）
- [ ] 同一日志 + 同一磁盘状态 → 同一 diff（双跑逐字节一致，含 CLI 出口）
- [ ] `s2r reconstruct` 落地并退役 exit 4（四处同步），补 bare + module 两形态离线冒烟
- [ ] 基准：`before/after` 逐字符对账真跑起来（`pending_facts.before_after` 摘除）+ 至少一个新探活
- [ ] 新增/改写守门做变异验证（弄坏必红）；双解释器全量 pytest 全绿；`tools/ci_smoke.py` 全绿
- [ ] `plan/00` §三/§五、`plan/04`、`plan/06` 状态表、`plan/verify/README.md`（M3 章节）回写
- [ ] **本对话内写完 `plan/HANDOFF-M4.md`**（六节）→ commit

---

## 六、关键命令速查（下一棒开工就用这些）

```bash
# 环境
py -0p                                             # 解释器通道（3.8 / 3.12）
export PYTHONDONTWRITEBYTECODE=1
python -m pytest -q                                # 全量测试（3.8 与 3.12 各跑一遍）

# 已交付命令（M1–M2）
s2r selftest
s2r config show --json
s2r scan data/bench/claude --out events.json       # 含 episodes[]/filtered[]
s2r scan data/bench/invalid                        # 应 exit 2（unsupported_format）
s2r scan ~/.claude/projects --out real.json         # 真实日志只读；按件隔离
s2r bench                                          # 基准对账（退出码即结论）
s2r bench --probe drop-episode
s2r bench --only happy_path

# 语料与门禁
python tools/gen_bench.py --check                  # 语料与真值再生成位级一致
python tools/fix_eol.py --check                    # 行尾卫生（改完文件立刻跑）
PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py
```
