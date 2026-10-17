# session-to-regression（`s2r`）

**从 agent 会话日志里切出「用户报 bug → 出现失败信号 → 改文件修好」的 episode，把修复 diff 反推成可复跑的 pytest 骨架，并做 RED/GREEN 双验证——让一次修复固化成测试。**

纯本地 Python CLI：不调用任何模型、不联网、无遥测、不依赖 git。修改前的内容就在日志里
（`structuredPatch` / `originalFile` / `old_string` / `apply_patch`），因此八成以上不在 git 仓库里的
会话同样能处理。

> 痛点来自公开抱怨：coding agent 常常「让测试变绿」而不是「让代码变对」，甚至把上周的修复改回去
> （HN item 49981230，2026-10-06）。修复是一次性的，回归是永久的——本工具把每一次修复钉成回归测试。

**状态（2026-10-17）**：**M1（双 harness 读取器）+ M2（episode 切分）+ M3（差分还原）+ M4（pytest 骨架生成）已交付**——
读本机日志、按 `payload.type` 归一事件流、切出"报 bug → 失败 → 改文件"的 episode 并确定性打分过滤、
把每次修复的 before/after 从日志里还原出来（三级降级 + 逐文件置信度），再把还原结果生成一份带证据锚点与
候选断言的 pytest 骨架（模板 + AST，**零模型**）。合成语料基准逐字段对账（含 episode 层、**还原层**
逐字符比对与**生成层**：骨架数 / 逐 episode 结论与原因 / 候选断言条数 / 必需证据锚点）。
**M5 尚未实现**：`verify` / `run` 现在只报 `stage_not_ready` 并退出 4，**不产出任何结果**（也不伪造空结果）。
未交付能力不会出现在本文档的"可用命令"里。

---

## 安装

```bash
git clone <repo-url> session-to-regression
cd session-to-regression
pip install -e ".[dev]"     # dev 是唯一 extras（pytest），CI 与本文档使用同一集合
```

要求 Python ≥ 3.8，运行期**零第三方依赖**（TOML 口径文件由内置的严格子集读取器解析）。

## 三分钟演示（当前可用）

```bash
s2r --version              # 版本行
s2r selftest               # 本机环境与骨架契约自检（坏配置必红）
s2r config path            # 定位唯一数值口径文件
s2r config show            # 打印全部口径值（人读）
s2r config show --json     # 机读 JSON，可直接喂给报告/基准

# M1-M2：读会话日志 → 统一事件流 + episode 切分（合成语料在 data/bench，可直接跑）
s2r scan data/bench/claude --out events.json
#   → events.json 里含 events[] 与 episodes[]（分数拆分 / 状态 / 证据锚点 / 丢弃原因）
s2r scan ~/.claude/projects --out real.json     # 真实日志只读（整目录扫描按件隔离）

# M3：把每个改动文件的 before/after 还原出来（logs-only，不依赖 git）
s2r reconstruct data/bench/claude/multi-bug-1.jsonl --out rec.json
#   → rec.json 里含 files[]（逐文件 before/after/hunks/source/confidence/status）与 episodes[]
s2r reconstruct claude:<session_id> --claude-root ~/.claude/projects      # 按会话引用定位
s2r reconstruct data/bench/codex/happy-path-1.jsonl --repo data/bench/repo/happy-path-1
#   → Codex 侧日志里没有 before 全文，全文基线只能来自磁盘（--repo 是显式开关，只读）

# M4：把一个 episode 生成 pytest 回归骨架（模板 + AST，零模型）
s2r generate data/bench/claude/multi-bug-1.jsonl#4 --out-dir /tmp/s2r-out
#   → /tmp/s2r-out/tests/regression/test_sess_demo_0003_4.py（docstring 证据锚点 + arrange/act/assert 三段）
s2r generate data/bench/claude/structured-patch-1.jsonl#4 --repo myrepo
#   → 写进 myrepo/tests/regression/（只写测试目录，源码一个字节都不动）
#   stdout 是机读产物（骨架内容与逐 episode 结论），stderr 是人读摘要；退出码即结论

# 合成基准对账（真值逐字段比对，含 episode 层、还原层与生成层；退出码即结论）
s2r bench
s2r bench --probe swap-before-after      # 探活：把 before/after 调换，基准必须报未达标
s2r bench --probe drop-candidate-assert  # 探活：把候选断言整段丢掉，基准必须报未达标
python tools/gen_bench.py --check        # 语料再生成位级一致（冻结物未被手改）
```

`s2r scan` 读真实的 `~/.claude/projects/**/*.jsonl` 与 `~/.codex/sessions/**/*.jsonl`，也用
`--claude-root DIR` / `--codex-root DIR` / 环境变量 `S2R_CLAUDE_ROOT`、`S2R_CODEX_ROOT` 覆盖路径
（**不硬编码用户名**；找不到源显式报错，不伪造空结果）。事件流不复制大文本：`originalFile` /
`structuredPatch` / `apply_patch` 全文只留**引用**（`seq` = 源文件行号），原文由 `reconstruct` 按需回溯。

`s2r reconstruct` 是**三级降级**的还原器：① Claude `originalFile` + 非空 `structuredPatch` →
② Claude `old_string`/`new_string` 在 before 基线上定位替换 → ③ Codex `FileChange.unified_diff`
（`*** Begin Patch` 文本作兜底）→ ④ 都不可得就如实记 `no_before_image`。三条纪律：

* **不任选**：多处命中且无法唯一定位 → `unresolved`（`replace_all` 时全替换才是明确语义）；
* **不伪造**：拿不到全文基线时落 `partial`（只给日志里真实出现的行），绝不编上下文行；
* **只读**：`--repo` 是磁盘对齐的显式开关（语义 = "该编辑发生前的磁盘状态"），不给就不碰工作区，
  给了也只读、对不上就降级。

`confidence` 由**before 镜像的来源**决定而非结果好坏：日志内全文 = `high`、磁盘工作区 = `medium`、
只有片段 = `low`、取不到 = `none`。

`s2r generate` 把 M3 的还原结果变成一份**可人工确认**的 pytest 骨架：docstring 里五类证据锚点
（会话 id / 事件时间戳 / **原始用户报告原文** / 失败信号摘录 / 修复文件清单）都能回到会话日志复算；
arrange 段按**修改前**的签名（AST 读）绑最小输入；act 段调用被改动行所属的最内层符号；
assert 段是占位 + **候选断言**。三条纪律：

* **候选必须注明**：每条候选断言都带"需人工确认"的标注（文案取自口径文件），且**只在能从 diff 推出**时才
  写成活动 `assert`——判据是 `return <expr> if <cond> else <alt>`、`<cond>` 恰是被调符号的形参名、
  `<alt>` 是字面量；推不出来就只留证据注释并标 `needs_manual`，**不猜**；
* **宁缺勿造**：目标符号识别不到 / 修改前源码读不出来 / 还原状态不是 `ok` → **不生成文件**，
  只记 `needs_manual` 与明确原因（`not_python` / `symbol_not_identified` / `before_unparsable` /
  `changed_lines_unknown` / `reconstruction_not_usable:<状态>`）；同一会话里每个 episode 各有结论；
* **只写测试目录**：落点由 `--repo`（目标仓库根）或 `--out-dir`（隔离输出根）显式给出，两者都不给就报用法错误；
  **绝不改源码、不碰 git 索引**，同一 episode 双跑逐字节一致。

`s2r bench --probe <NAME>` 是**探活**模式：它向对账路径注入一个已知缺陷，要求基准必须报未达标——
此时 `0` 表示"缺陷被检出"，`1` 表示"基准失灵"。这是"基准自己有没有被抓漏"的自检通道。
八个探活里有一个专打生成层：**把候选断言整段丢掉**（模板退化成只有 docstring 的装饰品）。

`selftest` 是可证伪的：把 `--config` 指向一个写坏的 TOML 会 **exit 1**；指向不存在的路径会 **exit 2**。

## 退出码语义

| 退出码 | 语义 |
|---|---|
| 0 | 成功；对 `run` / `verify` 表示「至少一条 verified」；对 `reconstruct` 表示「至少一个文件给出完整 before/after」；对 `generate` 表示「至少一份含候选断言的骨架」 |
| 1 | 未达标：无 verified（含全部 unverified）、基准未达标、`scan` 读到文件但零事件、`selftest` 自检失败、`reconstruct` 一个完整 before/after 都没有（全为片段/无法定位/无 before 镜像）、`generate` 一份骨架都没推出来（符号识别不到/推不出候选断言/还原状态不是 ok） |
| 2 | 用法错误或输入不可用：未知子命令、参数错误、路径不存在、未知日志格式（`unsupported_format`）、认不出的 episode 引用、缺落点（既没给 `--repo` 也没给 `--out-dir`） |
| 3 | 运行依赖缺失（如 pytest 不可用）——**不会**退化成「零条结果 + exit 0」 |
| 4 | 阶段未就绪：该子命令所属模块尚未交付（当前仅 `verify` / `run`；随里程碑整项退役） |

判断"跑挂了"与"按契约返回非 0"的区别：以本表为准，非 0 不必然是故障
（例如将来 `run` 检出全部 `unverified` 时返回 1 是既定语义）。

`bench --probe <NAME>` 是**探活**模式：它向对账路径注入一个已知缺陷，要求基准必须报未达标——
此时 `0` 表示"缺陷被检出"，`1` 表示"基准失灵"。这是"基准自己有没有被抓漏"的自检通道。

## 配置与"唯一数值口径"

**全项目所有数值只在一处定义**：`config/scoring.toml`（打分权重、过滤阈值、验证超时、临时目录策略、
测试目录与 manifest 落点、语义词表）。README、技术报告、基准输出一律**引用**该文件，
不再写第二份数值表；代码里不内嵌任何口径数值（`tests/test_config_single_source.py` 守门）。

定位顺序：`--config` → 环境变量 `S2R_CONFIG` → 当前目录向上 6 层内的 `config/scoring.toml`
→ 随包副本 `s2r/resources/scoring.toml`（安装后形态）。**读不懂就报错**（exit 2），绝不回落到内置默认值。

## 隐私：纯本地、零上传

* 不联网、无遥测、不调用任何模型；会话日志（含 prompt 与代码）**只在你的机器上被读取**；
* 真实日志、真实路径清单、真实 session id **一律不入仓**；本公开仓库只含**合成基准**（假项目名/假路径/假 id）；
* 生成物只写目标仓库的测试目录与临时副本，**不修改用户源码、不碰 git 索引**。

## 合成基准（`data/bench`）

基准与回归**一律使用合成会话日志**（假项目名/假路径/假 session id；白名单见 `data/README.md` §三），
真实日志只在本机演示、不入仓。语料由 `tools/gen_bench.py`（实现随包在 `s2r.benchdata`）程序化生成，
**生成时同步写出真值**（应切 episode 类型、每个文件的 before/after 与还原来源/置信度、生成层的骨架与
候选断言期望、RED/GREEN 期望）；真值里只存"输入与期望"，不允许出现观测类字段。

覆盖 10 类样本：正常修复链、未观察到通过、一会话多 bug、无失败信号、只改文档、多重定位、
纯新建文件、结构化补丁还原、磁盘基线对不上、未知格式（放在 `invalid/`，必须被拒绝）。
门槛（每类最低条数、检出率下限、误报上限）随 `data/bench/manifest.json` 下发；`s2r bench` 同时核对
**语料、真值与磁盘基线夹具的 sha256 台账**，冻结物被手改即红。`--probe` 是探活：向对账路径注入
已知缺陷（含"把 before 换成 after"与"把候选断言整段丢掉"）并要求基准报未达标——0 表示缺陷被检出、
1 表示基准失灵。

## 首期范围与已知限制

* **只固化已发生的修复**，不替用户补覆盖率、不凭空设计新功能测试；
* **首期只生成 Python 测试**：`.mjs` / `.ts` 与 `.md` 一类的修复只识别、只进 manifest / 只记原因
  （`not_python`），不生成测试文件；
* **生成的骨架是候选，不是成品**：能自动推出活动断言的是"加守卫"这一类形态
  （`return X if cond else alt`，且 `cond` 是被调符号的形参）——推不出来时如实标 `needs_manual` 并
  只给证据锚点与 diff 摘录，**不硬凑断言**；落地前请人工确认候选断言；
* **不依赖 git**：git 只作为可选的回放加速手段，缺 git 时全链路仍可跑；
* **Codex 侧的全文 before 只能来自磁盘基线**：实测其日志只给 `unified_diff`（修改）或 `content`
  （新增），没有"修改前全文"通道——所以 Codex 的 before/after 需要 `--repo` 指向编辑前的工作区；
  给不出就落 `partial`（片段级证据），**不伪造上下文行**；生成层同理：没有全文 before 就不生成骨架；
* **`--repo` 是"当时"的磁盘状态**：工具不做时序推断。指到当前工作区（会话已被应用）时，通常是
  hunk 对不上 → 如实降级为 `partial`，不会把 after 当成 before 交出去；
* **测试约定探测不到就走保守默认**：落点与 import 形态按口径文件的默认值，并在骨架 docstring 与
  机读产物里显式标 `assumed_convention`（不给 `--repo` 时必然如此）；
* **样本量诚实声明**：本机严格口径的真实端到端样本仅 5 个会话（宽松 29 个），
  不足以支撑统计型结论——因此**基准与回归一律使用合成会话 + 程序化植入真值**，
  真实样本只作演示与人工抽验，样本数不作交付承诺；
* **不承诺跨版本兼容**：会话格式随 agent 版本演进，解析器以"本机实测字段 + 版本指纹"为依据，
  不认识的新格式**显式报错**（`unsupported_format`），不猜测解析。

## 开发

```bash
pytest -q                      # 单元与契约测试（双解释器：3.8 与 3.12 都应全绿）
python tools/ci_smoke.py       # 离线 CI 冒烟：逐条跑已交付命令 + 断言退出码语义
python tools/gen_bench.py --check   # 基准语料再生成位级一致
python tools/fix_eol.py --check     # 行尾/结尾换行卫生（CI 同款门）
```

计划与数据台账见 `plan/`（`00` 总览、`01` 题目、`02` 需求解读、`03` 架构、`04` 模块详设、
`05` 数据计划、`06` 里程碑、`07` 交付对标与决策记录）与 `data/README.md`。

## 许可

MIT，见 [LICENSE](LICENSE)。署名 `yuluo554`。
