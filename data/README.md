# data/ 数据台账

**基准日**：2026-10-17（出题阶段查证与本机数据源实测）
**纪律**：**未核对字段语义不得落库 / 不得进入判定与生成路径**；数值口径只在 `config/scoring.toml` 一处定义；题面不写会漂移的硬数字；数据源条数为**本机实测快照**（附基准日），**非交付承诺**。

---

## 一、依据查证记录（逐条三态 + 来源 + 基准日）

### 1.1 痛点证据（HN 原话，✅ 已核对）

| 依据 | 三态 | 原话 / 说明与来源 |
|---|---|---|
| HN item 49981230（Perch 作者） | ✅ **原话已核对** | 原话：*"With AI coding, I find unit tests almost worthless, coding agents will always make them pass and hide regressions/defects by wrapping code in a try/catch, **undoing fixes I made in previous weeks**."* 提交时间 **2026-10-06**。https://news.ycombinator.com/item?id=49981230 |
| HN item 27553345 | ✅ **原话已核对** | 原话：*"When I fix a bug, I have to reproduce it somehow... Adding a regression test is a way to save that work as code."* https://news.ycombinator.com/item?id=27553345 |
| HN item 49227329 | ✅ **原话已核对** | 原话：*"Write failing tests first and run them... now that I can offload that to AI"* https://news.ycombinator.com/item?id=49227329 |
| HN item 48201162 | ✅ **原话已核对** | 原话：*"Our test coverage had been stuck at 70% for four years... because the process was broken"* https://news.ycombinator.com/item?id=48201162 |

> 引用纪律：以上四条为**逐字原话**，可直接引用；四条均只支撑"痛点真实"，**不支撑**任何数值结论（如覆盖率提升幅度）。

### 1.2 开源空白检索（5 条检索式，⬜ 软论据）

**检索渠道**：GitHub Search API（`api.github.com/search/repositories`），**基准日 2026-10-17**。
**论据强度声明**：以下为**软论据 ⬜**——有检索式、有基准日、有 API 计数，但**无入库截图**；不得与"官方原文直连"并列作硬锚点。

| # | 检索式 | total_count | 命中最相关项 | 判定 |
|---|---|---|---|---|
| 1 | `transcript to pytest` | **5** | `Victorchatter/transcript-to-test` **0★**（2026-07-22 建、2026-07-29 停更） | 唯一同名，零星且停更 |
| 2 | `agent session regression test` | **4** | `JumpMasters/assayer` **0★**（测 agent 行为漂移，非仓库代码）；`sumitsauravpat/ai-test-generation-kit` **1★** | 问题域不同 |
| 3 | `session replay regression` | **7** | `stepstitch` **0★**（前端 Playwright 路线）；`agent-blackbox` **1★**（录制回放） | 不做"修复 diff → pytest" |
| 4 | `bug reproduction test AI agent` | **5** | 均为 0–4★ 的 prompt / skill 类条目 | 无工程化实现 |
| 5 | `SWE-bench test generation` | **3** | 全无关 | 空白 |

**判定**：`会话日志 → bug-fix episode → 回归测试骨架` 这一具体形态**接近真空**。以上均为 API 计数软论据，仓库星数与更新时间为检索快照，**会随时间漂移**，不是本题承诺。

### 1.3 品牌隔离声明

本题文档只描述"会话 → 回归测试"问题域；同系列其他项目的名称与业务领域**不在本题文档、README、代码注释中出现**，也不复用其代码。

---

## 二、数据源可获取性记录（本机当场实测，含字段核对）

**⚠️ 路径自适应声明**：以下路径为**本机实测**。用户本机路径因用户名、安装方式、agent 版本而异——工具**必须自适应发现**（`--claude-root` / `--codex-root` / 环境变量 / 常见位置探测），**不得硬编码用户名或绝对路径**；找不到源时显式报错，**不得伪造空结果**（不得退化为 exit 0）。

### 2.1 Claude Code 会话日志（✅ 本机实测，2026-10-17）

| 项 | 实测结果 |
|---|---|
| 路径 | `~/.claude/projects/<project-slug>/<session-uuid>.jsonl`（`<project-slug>` 由 cwd 变形，如 `D--ProgramData-claude-code`） |
| 规模 | **94 个会话 / 43.2 MB** |
| 顶层 `type` | 题面所依据的实测：存在 `assistant` / `user` 行与 `file-history-snapshot` 行；**完整枚举以 M1 全量聚合为准**（⬜ 不在本题出题阶段宣称完整清单） |
| 工具调用 | `message.content[]` 内 `{"type":"tool_use","name":…,"input":…}`；结果侧 `{"type":"tool_result"}`，行级 `toolUseResult` |
| Edit 形态 | **252 次，252/252 全部带 `file_path` + `old_string` + `new_string`** → before/after 的文本级底座 |
| Write 形态 | **82 次带完整 `content`**（新建文件） |
| Edit 结果增强字段 | 行级 `toolUseResult.type == "update"` 带 `structuredPatch`（含 `oldStart` / `oldLines` / `lines`）与 `originalFile`（**修改前全文**）；抽样 **4/4 均有** |
| Write 结果 | create 结果 **82 条中 0 条**带 `originalFile`（新建文件无"修改前"，属预期，不是缺陷） |
| 改动文件类型 | `md 114 / mjs 86 / py 62 / js 45 / ts 40` |

### 2.2 Codex 会话日志（✅ 本机实测，2026-10-17）

| 项 | 实测结果 |
|---|---|
| 路径 | `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO时间>-<uuid>.jsonl` |
| 规模 | **220 个会话 / 831.6 MB** |
| 行类型 | `session_meta` / `response_item` / `event_msg` / `turn_context` |
| 工具调用容器 | `payload.type ∈ {function_call, custom_tool_call}`；结果侧 `function_call_output` |
| 写文件工具 | **`apply_patch` 104 次**，覆盖 **106 个唯一文件**；patch 文本为标准 `*** Begin Patch` / `*** Update File:` / `*** Add File:` / `*** Delete File:` 格式，可直接解析 |
| patch 目标类型 | `py 73 / md 44 / json 24 / ts 13` |

### 2.3 行为链实测（✅ 本机实测，2026-10-17）

| 项 | 实测结果 |
|---|---|
| bug → 失败 → 修复链 | **28 个会话**存在"工具结果报错 → 下一次 Edit"转移，共 **128 次** |
| 测试命令 | **11 个会话**跑过 test 命令；**8 个**改过 test 文件 |
| 端到端可固化样本 | **严格口径**（用户报 bug + 失败输出 + 改文件）**5 个会话**，其中 **4 个全程没写任何测试**；**宽松口径 29 个会话** |
| git 关联 | 37 个改文件会话中仅 **7 个**在 git 仓库内 → **修复前后内容必须从日志恢复，不能依赖 git** |

> **样本量诚实条款**：严格口径 5 个样本不足以支撑统计结论。本题的**基准与回归一律用合成会话 + 植入真值**；真实样本只作演示与人工抽验，样本数**不作交付承诺**，也不得在 README 里包装成"大规模验证"。

### 2.4 真实可复现样本（✅ 本机实测；仅本地留存）

| 项 | 说明 |
|---|---|
| 样本描述（脱敏） | 某 Claude 会话含 `npm test` 失败 → 修改 `server/ai-chat.mjs`、`shared/codex-executable.mjs` 的完整链 |
| 本地定位 | `~/.claude/projects/D--ProgramData-claude-code/<session>.jsonl`（session id 前缀 `9df592e7`，**完整 id 不入仓**） |
| 用途 | 人工抽验与演示；**不复制进仓库**，不在文档中记录真实绝对路径 |

### 2.5 ⬜ 待核对字段清单（M1 数据先行必须闭环）

| # | 待核对项 | 三态 | 影响 | 闭环方式 |
|---|---|---|---|---|
| R1 | Codex `apply_patch` 载荷内的**文件路径字段**（在 JSON arguments 里还是在 patch 文本指令行内？） | ⬜ 待核对 | M1 → M3：路径提取错则 episode 关联全错 | M1 全量逐样本核对，记录键集与路径形态分布 |
| R2 | Claude `MultiEdit` 的**字段形态**（本机是否出现？`edits[]` 结构？） | ⬜ 待核对 | M1 → M4：解析不到多段编辑会漏 episode | M1 全量工具名聚合；未出现则登记"本机 0 次"并保留降级语义 |
| R3 | Codex `exec` 的**失败判定字段语义**（退出码在哪个键？如何区分命令失败与工具失败？） | ⬜ 待核对 | M2：失败信号判定错则切分全错 | M1 取成功/失败样本各若干，逐字段对比 |

> **纪律**：R1–R3 任一未闭环，对应字段只能落 `unknown` 或整条跳过，**不得进入 M2 判定路径**，更不得进入 M4 生成路径。核对结论回填本表并改为 ✅，同时补齐"样本出处（脱敏描述）+ 结论 + 口径"三列内容。

### 2.6 渠道坑与实测教训（预登记，M1 复核）

- **Windows 路径两套写法**：Git Bash 下 `~` 展开为 `/c/Users/...`，Windows Python 不认 → 脚本内一律 `os.path.expanduser` + `pathlib`，**不得把 shell 展开结果传给 Python**；
- **两种 harness 结构完全不同**：Claude 由顶层 `type` 驱动、工具调用藏在 `message.content[]`；Codex 由 `payload.type` 驱动、工具调用是 `function_call` / `custom_tool_call` → **必须经统一事件流归一**，下游不得直接吃原始格式；
- **jsonl 可能含超长单行**（Codex rollout 尤甚）：逐行读取要设行长上限并显式报错，避免内存爆掉；
- **同一会话可能包含多次修复**：不得假设"一会话一 episode"。

---

## 三、合成数据纪律

**总则**：基准与演示一律使用**合成会话日志**；**零真实项目、零真实路径、零真实用户名、零真实 session id**。

1. **白名单登记**（假值统一登记，禁止真实值入库）：
   - 假项目名：`demo-alpha` / `demo-beta`（禁真实项目名、真实仓库名）；
   - 假文件路径：`/demo/alpha/src/calc.py`、`/demo/beta/pkg/format.py`（禁真实用户目录、真实业务文件名）；
   - 假 session id：`sess-demo-0001` 起的固定序列（**禁**真实 UUID）；
   - 假用户名：`demo-user`（禁真实用户名）。
2. **植入真值（程序化标注）**：生成器在造样例时**同步写出**真值文件（`*.truth.json`）——应切出的 episode 起止事件序号、before/after 全文、期望的 RED/GREEN 结果、应被过滤的低分候选。**真值不得事后人工补**。
3. **覆盖度门槛**：基准必须按类型设置最低覆盖项，口径写进 `manifest.json` 的 `coverage.min_count_by_type`。首期类型枚举（数值只在 manifest 一处定义）：
   - `happy_path`：用户报 bug → 失败 → 修复 → 测试通过；
   - `ended_without_pass`：修复后会话结束、未观察到通过；
   - `multi_bug`：单会话多个 episode；
   - `no_failure_signal`：用户报 bug 但日志内无失败输出（应被过滤/标低分）；
   - `doc_only`：只改了 `.md`（打分应低）；
   - `ambiguous_old_string`：`old_string` 多处命中（应标 `unresolved`，不得任选）；
   - `new_file_write`：纯新建文件（无 before，不得进入生成路径）；
   - `unsupported_format`：未知格式（必须显式报错）。
4. **合成日志形态**：Claude 侧仿 `type` + `message.content[]` + `toolUseResult`；Codex 侧仿 `session_meta` / `response_item` + `payload.type` + `*** Begin Patch`；**字段名与本机实测一致**，值全部为白名单假值。
5. **冻结与可审计**：生成器与真值标注入仓；生成的样例可一条命令重建（`--check` 位级一致）；样例体积控制在仓库友好范围。
6. **不落真实日志**：`.gitignore` 已屏蔽 `sessions/` / `exports/` / `_real_logs/`；任何从本机日志导出的中间件不得入库。

---

## 四、数值口径纪律

- **唯一权威位置**：`config/scoring.toml`（M1 落定文件名；若改名，全仓只改这一处并记入决策记录）；
- **episode 打分权重**：失败信号 +2 / diff +3 / 测试命令 +2 / 非文档代码改动 +2；阈值一项（题面 §2 口径声明，实值在配置文件）；
- **验证参数**：单例超时、临时目录策略、pytest 调用形态，均在该文件一处定义；
- **禁止第二份数值表**：README、技术报告、基准输出、题面一律**引用**，不得复制一份数值列表；报告里的展示值必须从配置读取后渲染；
- **题面不写会漂移的硬数字**（测试数 / 文件数 / 体积 / 通过率）；数据源条数为**本机实测快照**（附基准日 2026-10-17）。

---

## 五、待核对项闭环表

| # | 项 | 状态 | 闭环里程碑 |
|---|---|---|---|
| R1 | Codex `apply_patch` 载荷内文件路径字段 | ⬜ 待核对 | M1 |
| R2 | Claude `MultiEdit` 字段形态 | ⬜ 待核对 | M1 |
| R3 | Codex `exec` 失败判定字段语义 | ⬜ 待核对 | M1 |
| R4 | 合成日志覆盖度门槛具体数值 | ⬜ 待定 | M1（写进 manifest） |
| R5 | 真实样本量的最终表述（5 强 / 29 弱）是否随复核变化 | ⬜ 待复核 | M1（只更新快照，不改承诺） |
