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
| 规模 | **94 个会话 / 43.2 MB**（出题期快照；M1 复核时为 125 个文件——属**快照漂移**，结论不变，见 §六.1） |
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

### 2.5 ⬜ 待核对字段清单（M1 数据先行必须闭环）——**M1 已闭环，结论见 §六**

| # | 待核对项 | 三态 | 影响 | 闭环方式 |
|---|---|---|---|---|
| R1 | Codex `apply_patch` 载荷内的**文件路径字段**（在 JSON arguments 里还是在 patch 文本指令行内？） | ✅ **已核对**（结论：只在 patch 文本指令行内，JSON 侧无独立路径键） | M1 → M3：路径提取错则 episode 关联全错 | 全量 104 次 patch 逐样本核对键集与指令行枚举 → §六.2 |
| R2 | Claude `MultiEdit` 的**字段形态**（本机是否出现？`edits[]` 结构？） | ✅ **已核对**（结论：本机全量 0 次） | M1 → M4：解析不到多段编辑会漏 episode | 全量 125 文件工具名聚合 → §六.3 |
| R3 | Codex `exec` 的**失败判定字段语义**（退出码在哪个键？如何区分命令失败与工具失败？） | ✅ **已核对**（结论：无独立退出码键，信号在 `output` 文本内且**必须取数值**） | M2：失败信号判定错则切分全错 | 全量 3905 条 `function_call_output` 键集与输出形态聚合 → §六.4 |

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
| R1 | Codex `apply_patch` 载荷内文件路径字段 | ✅ 已核对（结论见 §六.2） | M1 |
| R2 | Claude `MultiEdit` 字段形态 | ✅ 已核对：全量 0 次，保留降级语义（§六.3） | M1 |
| R3 | Codex `exec` 失败判定字段语义 | ✅ 已核对（结论见 §六.4） | M1 |
| R4 | 合成日志覆盖度门槛具体数值 | ⬜ 待定 | M1（写进 `data/bench/manifest.json`） |
| R5 | 真实样本量的最终表述（5 强 / 29 弱）是否随复核变化 | ⬜ 待复核 | M1（只更新快照，不改承诺） |

---

## 六、M1 数据先行核对结论与勘误（✅ 2026-10-17，本机全量实测）

**范围**：本次核对为**全量**（非抽样）——本机 `~/.claude/projects/**/*.jsonl` **125 个文件**、
`~/.codex/sessions/**/*.jsonl` **220 个文件**，逐行 JSON 解析后按键聚合。
**方法**：一次性只读探针（落在仓库树外），只聚合**结构信息**（键名集合 / 计数 / 形态）；
探针不写任何文件、不复制日志内容。
**隐私**：下表**只有结构信息与计数**，不含真实代码、真实路径、真实 session id。

### 6.1 行类型与键集枚举（全量）

**Claude（125 文件）** 顶层 `type` 分布：`assistant 7218`、`user 3604`、`attachment 2145`、
`last-prompt 854`、`ai-title 513`、`mode 492`、`permission-mode 462`、`system 257`、
`queue-operation 137`、`file-history-snapshot 123`、`file-history-delta 56`、`atis-latch 50`、
`started 25`、`result 25`、`cost-state 16`。

* 行级键（`assistant`）：`cwd, effort, entrypoint, gitBranch, isSidechain, message, parentUuid, sessionId, timestamp, type, userType, uuid, version`
* 行级键（`user`）多出：`toolUseResult`（结果侧）、`promptId`、`session_id`、`sourceToolAssistantUUID`
* `message.content[]` 块类型：`tool_use 3389`、`tool_result 3386`、`thinking 2463`、`text 1366`、`image 1`
* **写文件类工具（全量）**：`Edit 263`、`Write 128`、`MultiEdit 0`、`NotebookEdit 0`
* `toolUseResult.type` 分布：**键缺失 1609**、`text 514`、`create 82`、`image 14`、`update 4`、`file_unchanged 1`

**Codex（220 文件）** 顶层 `type` 分布：`event_msg 65197`、`response_item 50599`、`turn_context 1258`、
`world_state 966`、`inter_agent_communication_metadata 669`、`session_meta 220`、`compacted 92`、
`token_usage_record 7` ——**行级键恒为** `ordinal, payload, timestamp, type`。

* `payload.type` 分布（前 12）：`token_count 37500`、`item_completed 24583`、`reasoning 15645`、
  `custom_tool_call 10197`、`custom_tool_call_output 10194`、`message 6020`、`function_call 3908`、
  `function_call_output 3905`、**键缺失 3212**、`task_started 1205`、`task_complete 941`、`agent_message 669`
* `session_meta.payload` 核心键：`id, session_id, cli_version, cwd, timestamp, model_provider,
  originator, source, thread_source`（在实测的三种变体里都出现）→ 用作**版本指纹**的必需键集
* 命令/工具调用侧 `function_call.name` 计数：`wait_agent 1258`、`wait 916`、`js 576`、
  `send_message 309`、**`shell_command 271`**、`spawn_agent 158`、**`exec_command 93`**、`list_agents 86`

### 6.2 R1 结论：Codex 路径字段

* `custom_tool_call` 的 payload 键集**唯一形态**：`call_id, id, input, internal_chat_message_metadata_passthrough, name, status, type`
  ——**没有独立的文件路径字段**；路径**只在 `input`（字符串）的 patch 指令行里**。
* 全量 104 次 `apply_patch` 的指令行枚举：`*** Begin Patch` 104、`*** End Patch` 104、
  `*** Update File: <path>` 85、`*** Add File: <path>` 71、**`*** Move to: <path>` 15**。
* 路径形态两种都在用：**Windows 绝对路径**（盘符开头）与**相对路径**；可出现**中文/非 ASCII 文件名**。
* 一次 patch 涉及文件数分布：1 个 74 次、2 个 11、3 个 7、4 个 7、11 个 1、**30 个 1** → **多文件 patch 是真实形态**，必须逐文件切分。
* patch 调用结果 `custom_tool_call_output` 形态：104/104 有 `output`，正文形如
  `Exit code: <N>\nWall time: …\nOutput:\nSuccess. Updated the following files:\nA <path>\nM <path>`
  （`A`/`M`/`D` 前缀分别表示新增/修改/删除，可作为路径与动作的第二来源）。

### 6.3 R2 结论：Claude `MultiEdit`

* **全量 125 文件内 `MultiEdit` 出现 0 次**（`Edit 263` / `Write 128` 是实际形态）。
* 处置（按"本机 0 次出现的形态同样不编造"）：解析器把 `MultiEdit` 登记为**已知工具名占位**；
  一旦出现，按"单次编辑"的保守语义降级处理并**计入 ParseNote**（显式可见，不静默丢弃），
  **不猜测 `edits[]` 的字段形态**。
* `Edit` 的 input 键集唯一形态：`file_path, new_string, old_string, replace_all`；
  `Write`：`content, file_path`。

### 6.4 R3 结论：Codex 命令失败的判定字段

* `function_call_output` 键集只有两种：`call_id, id, internal_chat_message_metadata_passthrough, output, type`（3785）
  与去掉 `id` 的变体（120）→ **没有 `exit_code` / `status` / `metadata` 字段**。
* 失败信号只在 `output` 字符串里，两种格式：
  * `Chunk ID: …\nWall time: …\nProcess exited with code <N>\nOriginal token count: …\nOutput:\n<正文>`（命令类）
  * `Exit code: <N>\nWall time: …\nOutput:\n<正文>`（`apply_patch` 类）
* 标记字符串计数（仅作形态统计）：`Exit code` 664、`Process exited` 90、`exited with code` 90、
  `Script failed` 45、`Traceback` 3、`FAILED` 2。
* ⚠️ **判定必须取数值再比较（N≠0），不能按标记字符串命中**：`Process exited with code 0` 同样命中标记，
  按字符串判会把大量成功当失败。
* 命令文本的参数键名**不统一**：`shell_command` → `command`（271 次）；`exec_command` → `cmd`（93 次）；
  两者都带 `workdir`。测试命令检出必须同时认这两个键。

### 6.5 勘误表（出题期认知 → 实测）

| # | 出题期写法 | 实测结论 | 影响与处置 |
|---|---|---|---|
| E1 | "`toolUseResult.type == "update"` 带 `structuredPatch` + `originalFile`，抽样 4/4" | 全量：`type == "update"` **仅 4 条**；带 `structuredPatch` 键的结果 **305 条**（其中非空 223、空 82），其中 **107 条同时带 `originalFile`**，而这些结果的 `type` 键**多数缺失**（键缺失 219 条） | **判据改为按"键存在"判定**，不得写 `type == "update"`（否则 305 条里会漏掉 301 条）。抽样 4/4 结论**方向正确、判据错误**——这正是"抽样不足以下判据"的实例 |
| E2 | "Codex 的工具调用是 `function_call`（如 `exec`）" | 本机 **没有名为 `exec` 的工具**；命令类实测为 `shell_command` / `exec_command`；`exec` 只作为 patch 结果文本里的 `Exit code` 字样出现 | M1/M2 按实测工具名匹配；文档与代码不得写 `exec` 作为工具名 |
| E3 | "patch 指令行 `*** Begin Patch / *** Update File / *** Add File / *** Delete File`" | 实测另有 **`*** Move to: <path>` 15 次**（题面未提）；本次样本未出现 `*** Delete File` | 解析器按**实测全集**支持（含 Move to），未出现的形态保留分支并计注记；**不宣称**未实测形态已支持 |
| E4 | "Claude 顶层 `type` 行与 `file-history-snapshot` 行" | 全量共 **15 种**顶层 `type`（见 §6.1） | 版本指纹只对**必需键**做承诺，其余类型按"非事件行"跳过并计注记（不静默丢弃） |
| E5 | "060 次 Edit 全带 file_path+old_string+new_string（252/252）" | 全量 `Edit 263` / `Write 128`（数量随使用增长，属快照漂移，非结论变化） | 计数类表述一律标注基准日；**结论（字段齐全）不变** |

### 6.6 口径增量（M1 起生效，写入既有 K 清单）

* **K14**：Claude 的 before-image 判据 = "`structuredPatch`/`originalFile` **键存在**"，不是 `type` 字段值（E1）。
* **K15**：Codex 命令工具名按实测集合匹配（`shell_command` / `exec_command`），命令文本键 `command` 与 `cmd` 都要认（E2/R3）。
* **K16**：失败判定只在**数值**上做（`Exit code N` / `Process exited with code N`，N≠0）；标记字符串命中不等于失败（R3）。
* **K17**：patch 指令集以实测全集为准（含 `*** Move to:`），未实测形态保留分支并计注记（E3）。

### 6.7 R1 补充：Codex 还有一条**结构化**的文件变更通道（实测，1892 条）

`payload.type == "item_completed"` 的 `item` 有 8 种类型：`AgentMessage 8529`、`Reasoning 8418`、
`SubAgentActivity 3377`、**`FileChange 1892`**、`McpToolCall 1136`、`UserMessage 973`、`WebSearch 151`、
`ContextCompaction 92`。其中 `FileChange` 的形态是：

* `item.status` 恒为 `completed`（实测 1892/1892）；
* `item.changes` 是 **dict：路径 → 变更对象**（1892/1892 都是 dict，**不是列表**）；
* 变更对象的键只有两种形态：`{type, content}`（1274 个，新增文件 → 全量正文）与
  `{type, unified_diff, move_path}`（2347 个，修改/移动 → **统一 diff**）；
* 路径键是**完整路径**（绝对或相对），与 `apply_patch` 指令行里的路径同源。

**处置（M1 起）**：
* 读取器把 `FileChange` 作为**结果侧事件**纳入事件流（`kind=tool_result`、`tool=file_change`、
  payload 携带 `files`），**不承诺**与某次 `apply_patch` 调用配对（该通道**没有 call_id**，
  按时间序配对属于猜测，猜测不做）；
* **M3 的还原优先级因此更新**：Codex 侧首选 `FileChange.changes[*].unified_diff`（结构化、带路径），
  `*** Begin Patch` 文本解析降为**第二来源**（用于该通道缺失时的兜底）；
* `item_completed` 里的 `AgentMessage 8529` / `UserMessage 973` 与 `response_item` 的 `message`
  是**重复通道** → 读取器**只取一条**（实测选定 `response_item` 的 `message`），另一条按注记跳过，
  避免同一句话在事件流里出现两次。

### 6.8 用户消息的载体形态（实测，写入解析器契约）

* **Claude**：用户文本消息以 `message.content` 为**字符串**的形态出现（200 条）；
  `content` 为列表且含 `text` 块的只有 18 条；列表形态绝大多数是 `tool_result`（3386 条）。
  → 解析器**两种形态都要认**（字符串 → `user_msg`；列表 → 逐块分流）。
* **Codex**：用户消息在 `payload.type == "message"` 且 `payload.role == "user"` 上
  （`role` 取值实测：`assistant 3552` / `user 1543` / `developer 925`），`content` 是
  `[{type: "text", text: …}]` 的列表；`role == "developer"` 是系统/开发者指令，**不是用户报告**，
  单列为 `meta` 侧信息（不参与"用户报 bug"判定）。
