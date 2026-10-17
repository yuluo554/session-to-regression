# plan/verify · 验证留档

**用途**：把"本地等价校验"的逐条证据落成表（命令 → 退出码 → 语义判定），供后续里程碑与发布期复核。
**纪律**：发布里程碑**追加新章节，不覆盖旧章节**；未实际执行的项写 ⬜ + 原因，不留空、不预填。

---

## M0（2026-10-17）骨架验证

### 0. 环境事实（只写实测过的）

| 项 | 实测值 |
|---|---|
| 解释器通道 | `py -3.8`（Python 3.8.8）、`py -3.12`（Python 3.12.10）；`py` 默认 3.8 |
| pytest | 3.8 通道 7.4.4；3.12 通道 8.4.2 |
| 干净环境 | 仓库内 `.venv-check/`（3.12 创建，`.gitignore` 排除）；先 editable 装、后**非 editable 重装**做安装面复核 |
| git 身份 | `yuluo554` / noreply 邮箱（发布期脱敏的前置事实） |
| 已知本机坑 | pip 受系统代理污染 → 安装用 `NO_PROXY="*" no_proxy="*"` + 国内镜像；Windows 控制台中文需 `-X utf8` |

### 1. 逐条命令与退出码语义

| # | 命令 | 退出码 | 语义判定 |
|---|---|---|---|
| 1 | `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：**116 项通过** |
| 2 | `py -3.12 -X utf8 -m pytest -q` | 0 | ✅ 符合：**116 项通过**（同收集数，无 extras 差异） |
| 3 | `py -X utf8 tools/fix_eol.py --check` | 0 | ✅ 符合：40 个文本文件、0 个问题 |
| 4 | `tools/ci_smoke.py`（在 `.venv-check` 环境下） | 0 | ✅ 符合：**20/20 条冒烟符合预期**（bare `s2r` + `python -m s2r` 两形态各 8 条 + 4 条 staged×2） |
| 5 | `tools/ci_smoke.py --no-staged`（要求全部命令已交付） | 1 | ✅ **符合明示语义**：8/20 符合预期——未交付命令返回 4 被当场判为不符（这条本身是"退役待办"的机器提醒） |
| 6 | `pip install .`（非 editable）+ 中立 CWD 下 `s2r selftest` | 0 | ✅ 符合：口径文件由**随包副本**定位（site-packages 内），`config_copies_identical` 报 same——安装面第五面成立 |
| 7 | 仓库根 `s2r selftest` | 0 | ✅ 符合：定位到仓库 `config/scoring.toml`，6 项自检全 ok |
| 8 | `s2r scan|x|y`（未交付命令） | 4 | ✅ 符合：`stage_not_ready` + 报出所属里程碑 + **stdout 为空**（不伪造空结果） |

### 2. 变异验证（"好门要证伪一次"）

六项守门各自被"故意弄坏"后必须变红，实测结果：

| 变异 | 目标测试 | 结果 |
|---|---|---|
| 随包副本与仓库口径文件差一个字节 | `test_repo_and_package_config_are_byte_identical` | RED ✅ |
| 往 `serialize.py` 塞一个 ≥100 的口径数字 | `test_no_large_numeric_policy_literal_in_source` | RED ✅ |
| 往 `events.py` 塞一张三词词表 | `test_no_second_vocabulary_table_in_sources` | RED ✅（**首版判据是假门**：按单字面量计数漏判列表形态，已改为逐文件计数） |
| 把 `README.md` 写成 CRLF | `test_text_hygiene_scan_covers_the_repo` | RED ✅ |
| 去掉名校验后的"未交付命令返回 0" | `test_staged_commands_report_milestone_and_produce_nothing` | RED ✅ |
| README 引一个不存在的子命令 / 少写一个退出码 / extras 与 pyproject 不一致 | `tests/test_readme_gate.py` 三项 | RED ✅（三项各测一次） |

补充自证：`tests/test_config_single_source.py` 的词表门在首版**被自己的注释打红**（注释里举了真实词面例子），
现已明确"留档与注释不复述词面"（K13）。

### 3. 干净环境/安装面复核（本机实跑，非 CI）

* `pip install -e ".[dev]"` → 秒装（零运行期依赖）；
* `pip install .`（非 editable）→ `site-packages/s2r/resources/scoring.toml` 在位；
* 中立 CWD（系统临时目录）跑 console script → 口径文件解析到随包副本，自检 6 项全 ok；
* **本轮抓到一处真缺陷**（本机干净环境才有、CI 也会有）：`pip install -e .` 会在 `src/` 下生成
  `<dist>.egg-info/`（`PKG-INFO` 是 CRLF、`SOURCES.txt` 缺结尾换行）→ EOL 门稳定误报，
  首推必红。处置 = 扫描面按目录后缀剪枝（`.egg-info`/`.dist-info`/`.venv*` 等）；
  并把"哪些文件算仓库文本"的遍历规则**收敛成单点实现**（`tools/fix_eol.py`），测试与 CI 共用。

### 4. 干净 clone 复核（仓库树外，`--no-local`）

为什么单列一节：工作树里跑测**证明不了**"新克隆里也成立"——`.gitattributes` 的 eol=lf 是否真的生效、
有没有只在作者机器存在的文件，都要在这一步才暴露。

| 命令（在 clone 目录内） | 退出码 | 语义判定 |
|---|---|---|
| `py -X utf8 tools/fix_eol.py --check` | 0 | ✅ 符合：42 个文件 0 问题——**eol=lf 那一半生效**（克隆后仍是 LF） |
| 逐文件字节扫描 `\r\n` | 0 | ✅ 符合：`.py` 文件零 CRLF |
| `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：116 项通过（与作者机器同收集数） |
| `PYTHONPATH=src py -X utf8 -m s2r selftest` | 0 | ✅ 符合：口径文件在 clone 内定位成功、6 项自检 ok |

### 5. 与 CI 的缺口（诚实标注）

* **GitHub Actions 从未运行**（本机无法运行远端 CI 且尚未建远端）→ 一切 CI 结论为
  **"本地等价校验，未接远端，CI 待首推验证"**。
* 等价物清单（已跑）：双解释器全量 pytest、EOL 门、离线 CLI 冒烟（含 console script 与 `-m` 两形态）、
  非 editable 安装 + 中立 CWD 自检、变异验证 6 项。
* **缺口（本机测不了，显式待首推验证）**：① 四腿矩阵的 runner 环境差异（尤其 `ubuntu-22.04 + 3.8` 这一腿）；
  ② `actions/setup-python` 各版本可用性；③ CI 步骤的 shell/接线（本项目已把断言落成 `tools/` 脚本，
  降低内联脚本风险）；④ 网络/代理差异。

---

## M1（2026-10-17）数据先行：字段核对 + 读取器 + 合成基准

### 1. 数据先行核对（R1–R3 闭环）

全量实测（125 个 Claude 文件 / 220 个 Codex 文件，逐行解析后按键聚合），**只读探针**跑在仓库树外；
结论与**三处勘误**写入 `data/README.md` §六。三条要点：

| # | 结论 | 影响 |
|---|---|---|
| R1 | Codex `apply_patch` 的路径**只在 patch 文本指令行**（JSON 侧无独立键）；指令集实测含 `*** Move to:`；一次 patch 可含 1–30 个文件 | M1 按指令行解析；M3 首选 `FileChange.unified_diff` 通道 |
| R2 | Claude `MultiEdit` **本机全量 0 次**；`Edit` 键集恒为 `file_path/new_string/old_string/replace_all` | 占位登记 + 出现即计注记，**不猜** `edits[]` 形态 |
| R3 | Codex 结果侧**没有** `exit_code` 键；退出码在 `output` 文本里（`Exit code: N` / `Process exited with code N`），**必须取数值**（成功输出同样含标记） | 失败判定只在数值上做（K16） |
| E1（勘误） | "`toolUseResult.type == "update"` 带 before 镜像"**判据错**：全量 `update` 仅 4 条，带 `structuredPatch` 的 305 条里多数 `type` 键缺失 | 判据改为**键存在**（K14）——否则 305 条漏 301 条 |
| E2（勘误） | Codex 命令工具名实测是 `shell_command` / `exec_command`（**没有 `exec`**），命令文本键 `command` 与 `cmd` 并存 | 按实测集合匹配（K15） |

### 2. 逐条命令与退出码语义（本机实跑）

| # | 命令 | 退出码 | 语义判定 |
|---|---|---|---|
| 1 | `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：**179 项通过** |
| 2 | `py -3.12 -X utf8 -m pytest -q` | 0 | ✅ 符合：**179 项通过** |
| 3 | `python tools/gen_bench.py --check` | 0 | ✅ 符合：与冻结物**逐字节一致**（8 个样本，rng_draws=4） |
| 4 | `s2r bench` | 0 | ✅ 符合：`ok`（检出率 8/8、误报 0、台账一致、覆盖度达标；`episodes` 记 pending_m2） |
| 5 | `s2r bench --probe <4 个探活>` | 0 ×4 | ✅ 符合：四个注入缺陷**全部被检出**（`probe_detected`） |
| 6 | `s2r scan <合成语料目录> --out events.json` | 0 | ✅ 符合：双跑逐字节一致 |
| 7 | `s2r scan <invalid 样本>` | 2 | ✅ 符合：`unsupported_format`（未知格式显式报错） |
| 8 | `s2r scan <valid 文件但零事件>` | 1 | ✅ 符合：显式降级，不伪装成功 |
| 9 | `python tools/ci_smoke.py`（已装包环境） | 0 | ✅ 符合：**20/20 符合预期**（`scan`/`bench` 已按真实语义登记，其余四条仍为阶段未就绪 4） |
| 10 | `python tools/fix_eol.py --check` | 0 | ✅ 符合：66 个文件 0 问题 |
| 11 | 真实日志抽验（Claude + Codex 各一份，只读） | 0 | ✅ 符合：harness/版本/事件/路径/退出码均按实测形态读出（含真实失败样本 `code 1`） |

### 3. 变异验证（M1 新增门，5 项全红）

| 变异 | 目标 | 结果 |
|---|---|---|
| 退出码解析改成"标记命中即失败" | 读取器退出码测试 + 基准对账 | RED ✅ |
| 版本指纹表清空 | 指纹测试 + `selftest` 契约 | RED ✅ |
| jsonl 改回 pretty 序列化（行边界消失） | 语料再生成位级一致 | RED ✅ |
| 探活重新变成"直接记通过" | 探活检出测试 | RED ✅（这正是首版的实际形态——探活自己成了假门） |
| 隐私自检改成恒通过 | 隐私自检变异测试 | RED ✅ |

另：本轮还当场抓到并修掉两处"自己人"误报——词表门命中我自己的**测试夹具**（已把判据按
"生产代码逐文件 / 测试单字面量"分两级，并说明测试是消费者而不是第二份词表）、脱敏门被
`Output:` + 换行转义误伤（已收紧到大写盘符并补**阳性对照**）。

### 4. 与 CI 的缺口（诚实标注）

* **GitHub Actions 仍未运行**（未建远端）→ 一切 CI 结论为"本地等价校验，未接远端，CI 待首推验证"。
* 本轮新增的等价物：双解释器全量 179 项、`bench` 对账 + 四探活、语料 `--check` 位级一致、
  离线冒烟 20/20（含两条新交付命令）、EOL 门、真实日志只读抽验。
* **缺口（本机测不了）**：四腿矩阵的环境差异、CI 步骤接线、代理/网络差异——均待首推验证。

### 5. 干净 clone 复核（M1，仓库树外，`--no-local`）

判据重点：**冻结语料的 `-text` 是否真的生效**——若 git 在克隆时归一了字节，manifest 里的 sha256 就会失配，
台账门当场红。所以"clone 里跑 `s2r bench` 通过"是这条纪律的端到端证明。

| 命令（clone 目录内） | 退出码 | 语义判定 |
|---|---|---|
| `PYTHONPATH=src python -m s2r bench` | 0 | ✅ 符合：`ok`（8/8、误报 0）——**台账 sha256 与克隆后字节一致 = `-text` 冻结生效** |
| `python tools/gen_bench.py --check` | 0 | ✅ 符合：语料再生成逐字节一致（8 样本，rng_draws=4） |
| `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：179 项通过（与作者机器同收集数） |
| `python tools/fix_eol.py --check` | 0 | ✅ 符合：70 个文件 0 问题 |

（clone 内 `.pytest_cache/README.md` 带 CRLF 属 pytest 自身缓存产物，已被 EOL 门的跳过规则排除——门报的是
"扫描面 0 问题"，与"树里一个 CR 字节都没有"不是同一命题，这里如实区分。）

---

## M2（2026-10-17）episode 切分

### 1. 逐条命令与退出码语义（本机实跑）

| # | 命令 | 退出码 | 语义判定 |
|---|---|---|---|
| 1 | `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：**204 项通过**（M1 时 179） |
| 2 | `py -3.12 -X utf8 -m pytest -q` | 0 | ✅ 符合：**204 项通过** |
| 3 | `s2r scan data/bench/claude` | 0 | ✅ 符合：`schema_version=2`，含 `episodes[]`/`filtered[]`/`skipped[]`/`failed[]` |
| 4 | `s2r scan ~/.claude/projects`（真实日志，只读） | 0 | ✅ 符合：读入 122、跳过 3、失败 0 → 94 会话 / 8359 事件 / 11 episode |
| 5 | `s2r scan ~/.codex/sessions`（真实日志，只读） | 0 | ✅ 符合：读入 220、失败 0 → 63 会话 / 36336 事件 / 64 episode（上限调到 16 MiB 后全读入） |
| 6 | `s2r scan <invalid 样本>` | 2 | ✅ 符合：全读不了 → exit 2，且**错误消息里带逐件原因**（`unsupported_format`） |
| 7 | `s2r scan <状态文件>` | 2 | ✅ 符合：`not_a_session_log`（非会话日志不是"格式不支持"，单独一个码） |
| 8 | `s2r bench` | 0 | ✅ 符合：`ok`（检出 8/8、误报 0）；`pending` 只剩 M3–M5 三个面 |
| 9 | `s2r bench --probe <6 个探活>` | 0 ×6 | ✅ 符合：六个注入缺陷全部被检出（新增 `drop-episode` / `keep-filtered`） |
| 10 | `python tools/gen_bench.py --check` | 0 | ✅ 符合：语料与真值再生成逐字节一致 |
| 11 | `python tools/ci_smoke.py`（已装包环境） | 0 | ✅ 符合：20/20 |

### 2. 变异验证（M2 新增门，7 项全红）

| 变异 | 目标 | 结果 |
|---|---|---|
| ASCII 词改成子串匹配（`debug` 会命中 `bug`） | 词边界测试 | RED ✅ |
| 通过判定丢掉"同现失败信号即不算通过" | 通过判定测试 | RED ✅ |
| 删掉「只改文档」丢弃规则 | 边界测试 | RED ✅ |
| 通过不再要求"同一测试命令" | 终点测试 | RED ✅ |
| before 镜像判据退回"键存在即算"（空 patch 也算 diff） | 新文件测试 + 基准 | RED ✅ |
| 生产代码里塞第二张判定词表 | 词表门 | RED ✅ |
| 基准不再把 episode 期望映射进对账面 | 逐样本点名测试 | RED ✅（**首版是假门**：断言"摘要出现样本名"被台账门那行同一名字满足，已收窄到只锚 `episode_count`） |

### 3. 与 CI 的缺口

* **GitHub Actions 仍未运行**（未建远端）→ CI 结论一律"本地等价校验，未接远端，CI 待首推验证"。
* 本轮新增等价物：双解释器 204 项、真实日志双源只读全量扫描、六探活、语料 `--check`、离线冒烟 20/20。
* 缺口不变：四腿矩阵环境差异、CI 步骤接线、代理/网络差异。

---

## M3（2026-10-17）差分还原：三级降级 + 逐文件置信度 + 基准真对账

### 1. 逐条命令与退出码语义（本机实跑）

| # | 命令 | 退出码 | 语义判定 |
|---|---|---|---|
| 1 | `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：**243 项通过**（M2 时 204） |
| 2 | `py -3.12 -X utf8 -m pytest -q` | 0 | ✅ 符合：**243 项通过**（同收集数） |
| 3 | `s2r reconstruct data/bench/claude/multi-bug-1.jsonl --out rec.json` | 0 | ✅ 符合：`claude/sess-demo-0003` → 2 个文件（完整 2）+ 2 个 episode；双跑 sha256 相同 |
| 4 | `s2r reconstruct data/bench/claude/structured-patch-1.jsonl` | 0 | ✅ 符合：① 路径（`claude_structured_patch` / `high` / `aligned_by=line_number`） |
| 5 | `s2r reconstruct data/bench/claude/ambiguous-old-string-1.jsonl` | 1 | ✅ 符合：`unresolved` + `ambiguous_match`（`occurrences=2`，**不产出** before/after） |
| 6 | `s2r reconstruct data/bench/claude/new-file-write-1.jsonl` | 1 | ✅ 符合：`no_before_image`（after 取自 `Write.content`，before 为 null） |
| 7 | `s2r reconstruct data/bench/codex/happy-path-1.jsonl` | 1 | ✅ 符合：无磁盘基线 → `partial`（片段，`before`/`after` 为 null） |
| 8 | `s2r reconstruct … --repo data/bench/repo/happy-path-1` | 0 | ✅ 符合：`codex_unified_diff` / `medium` → before/after 与真值逐字符相同 |
| 9 | `s2r reconstruct data/bench/claude/base-drift-1…`（同 7 的基线对不上形态） | 1 | ✅ 符合：语料 `base-drift-1` 走 `partial` + `base_mismatch`（磁盘已被改动，如实降级） |
| 10 | `s2r reconstruct sess-demo-0001`（认不出的引用） | 2 | ✅ 符合：`input_unavailable` + 提示 `<source>:<session_id>` 或日志路径 |
| 11 | `s2r reconstruct claude:sess-demo-0003 --claude-root <临时根>` | 0 | ✅ 符合：按会话 id 定位（只探测每份文件前 N 行） |
| 12 | `s2r bench` | 0 | ✅ 符合：`ok`（检出 **10/10**、误报 0、台账含磁盘基线夹具）；`pending` 只剩 M4–M5 |
| 13 | `s2r bench --probe <7 个探活>` | 0 ×7 | ✅ 符合：七个注入缺陷全部被检出（新增 `swap-before-after`） |
| 14 | `python tools/gen_bench.py --check` | 0 | ✅ 符合：语料/真值/磁盘夹具再生成**逐字节一致**（10 样本，rng_draws=5） |
| 15 | `python tools/ci_smoke.py`（已装包环境） | 0 | ✅ 符合：**20/20**（`reconstruct` 已退役 4 → 按真实语义 0，另加 bare/module 两形态） |
| 16 | `python tools/fix_eol.py --check` | 0 | ✅ 符合：83 个文件 0 问题 |

### 2. 变异验证（M3 新增/改写门，7 项全红）

跑批脚本落在仓库树外（`%TEMP%/s2r-mutations/mutate.py`：备份 → 改 → 跑 → 还原 → 校验），
一次性 7 项，逐项结果：

| 变异 | 目标判据 | 结果 |
|---|---|---|
| 还原优先级写反（① 结构化补丁那条分支永不成立） | 优先级测试（① 与 ② 结论不同的构造） | RED ✅ |
| 多处命中不再拒绝：任选第一处替换 | `unresolved` 判据 + 语料 | RED ✅ |
| 空 `structuredPatch` 也算 diff（K14 退版为"键存在"） | 空表测试 + 语料（② 路径） | RED ✅ |
| 探活注入被摘掉（`swap-before-after` 成永不触发的假门） | 探活检出测试 | RED ✅ |
| 还原期望不再映射进对账面 | 逐文件差异点名测试 | RED ✅ |
| 不给 `--repo` 也用当前工作目录兜底 | 磁盘读取显式开关测试（可证伪：基线就摆在 CWD 下） | RED ✅ |
| hunk 定位多处命中时任选第一处 | `ambiguous_match` 判据 + 语料 | RED ✅ |

### 3. 与 CI 的缺口（诚实标注）

* **GitHub Actions 仍未运行**（未建远端）→ CI 结论一律"本地等价校验，未接远端，CI 待首推验证"。
* 本轮新增等价物：双解释器 243 项、七探活、语料+真值+磁盘基线**三类冻结物**的台账 sha256、
  `reconstruct` 的 bare/module 离线冒烟、EOL 门（83 文件）。
* **新增缺口（本机测不了，显式待首推验证）**：① `data/bench/repo/**` 里的夹具文件名在大小写不敏感
  文件系统（macOS/Windows）与敏感文件系统（Linux）上的行为差异；② Windows 盘符形态的磁盘候选路径
  只在 Windows 腿上有意义（Linux 腿上走 POSIX 分支）。

### 4. 干净 clone 复核（M3，仓库树外，`--no-local`）

判据重点：**新语料的台账 sha256（含 `data/bench/repo/**` 磁盘基线夹具）在克隆后是否仍然一致**——
git 若在克隆时归一了字节，`s2r bench` 的台账门会当场红。这条因此是"`-text` 冻结生效"的端到端证明。

| 命令（clone 目录内） | 退出码 | 语义判定 |
|---|---|---|
| `PYTHONPATH=src py -3.8 -X utf8 -m s2r bench` | 0 | ✅ 符合：`ok`（10/10、误报 0）——**台账 sha256 与克隆字节一致 = `-text` 冻结生效**（含磁盘基线夹具） |
| `py -3.8 -X utf8 tools/gen_bench.py --check` | 0 | ✅ 符合：语料/真值/夹具再生成逐字节一致（10 样本，rng_draws=5） |
| `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：**243 项通过**（与作者机器同收集数） |
| `py -3.8 -X utf8 tools/fix_eol.py --check` | 0 | ✅ 符合：84 个文件 0 问题 |
| `PYTHONPATH=src py -3.12 -X utf8 -m s2r selftest` | 0 | ✅ 符合：口径文件在 clone 内定位成功、6 项自检 ok |
| 逐文件字节扫描 `\r\n`（82 个文本文件） | 0 | ✅ 符合：仅 `.pytest_cache/README.md` 带 CRLF（pytest 自身缓存产物，EOL 门已按跳过规则排除，与"树里一个 CR 字节都没有"不是同一命题） |

### 5. 真实日志只读抽验（M3，本机全量目录内按体积抽样）

**方法**：在 `~/.claude/projects` 与 `~/.codex/sessions` 内按**文件体积升序**取前若干份（Claude 40 / Codex 20，
跳过 >20 MB 的），逐份 `read_file` → `reconstruct_session(repo=None)`。**只读探针落在仓库树外**，
**只打印聚合计数**（不打印路径、内容、session id），不写任何文件。

| 来源 | 读入 | 有写文件证据 | 还原文件 | 状态分布 | 来源分布 | 置信度 |
|---|---|---|---|---|---|---|
| Claude | 37 份 | 11 份 | 23 个 | `ok 11` / `no_before_image 12` | `claude_structured_patch 11` / `claude_old_string 9` / `claude_write_content 3` | `high 11` / `none 12` |
| Codex | 20 份 | 7 份 | 28 个 | `no_before_image 25` / `partial 3` | `codex_file_change_content 25` / `codex_unified_diff 3` | `none 25` / `low 3` |

**这三行读数怎么解读（写清楚，别包装成"效果很好"）**：

* **① 路径在真实日志里真的成立**：Claude 侧 11 个文件的全文 before 全部来自
  `originalFile` + 非空 `structuredPatch`，且都判到 `high` 置信度——与 `data/README.md` §6.1 的
  全量计数（305 条带 `structuredPatch`、其中 223 条非空）方向一致；
* **不传 `--repo` 时，缺 `originalFile` 的 Edit 只能落 `no_before_image`**（Claude 9 个）——
  日志里确实没有"修改前全文"这一项，这正是"磁盘基线可选对齐"存在的理由，不是实现缺陷（**没有伪造出前后文**）；
* **Codex 侧全部落在 `no_before_image` / `partial`**：抽样里 25 个是**新建文件**（`content` 通道，
  天然没有 before），3 个是 `unified_diff` 但未给磁盘基线 → 与 §6.10 的 M3-1 结论互相印证；
* **样本量与抽样方式如实标注**：这是按体积抽的小样本、只作演示约束，**不是**交付承诺，
  也不支撑任何比率结论（真实样本量声明见 `data/README.md` §2.3）。
