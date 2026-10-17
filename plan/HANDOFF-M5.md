# HANDOFF-M5（下一棒：M5 RED/GREEN 验证 + manifest + 收尾）

> 由 M4（pytest 骨架生成，2026-10-17）交出。**本文件是下一棒的唯一入口**：新对话零上下文，
> 上下文全在这里与 `plan/`、`data/`、`README.md` 里。
> 用法（新对话第一条）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M5.md" 继续完成任务`
> **库内一律相对路径**；绝对路径只出现在用户侧的启动命令里。

---

## 一、当前进度 + 证据

**已完成：M0 骨架 ✅、M1 数据先行与读取器 ✅、M2 episode 切分 ✅、M3 差分还原 ✅、M4 骨架生成 ✅**
（状态表见 `plan/06-里程碑.md`；逐条实跑留档见 `plan/verify/README.md`）

| 交付物 | 证据位置 |
|---|---|
| 双 harness 读取器（按 `payload.type` 分流 → 事件流 IR；`seq` = 源行号 + 字节偏移） | `src/s2r/readers/{__init__,claude,codex}.py` |
| episode 切分（起点 → 失败信号 → 修复动作 → 终点；权重/阈值只读配置） | `src/s2r/episode.py`、`tests/test_episode.py` |
| 三级降级还原 + 逐文件置信度 | `src/s2r/reconstruct.py`、`tests/test_reconstruct{,_cli}.py` |
| **pytest 骨架生成**（约定探测 + AST 读签名 + 证据锚点 + 候选断言；零模型） | `src/s2r/generate.py`、`tests/test_generate.py`（31 项）、`tests/test_generate_cli.py`（17 项） |
| 生成物契约：`skeletons[*] = {episode_id, slug, relative_path, status∈{generated,needs_manual}, reason, assumed_convention, assumed_parts, symbols[], candidates[], anchors{}, sections[], files[], written, content}` + `convention{}` + `counts{}` | 同上（`Skeleton.fact`/`summarize` 是**对账用事实子集**） |
| `s2r generate EPISODE_REF`（`--repo`/`--out-dir`），exit 4 已退役 | `cli.cmd_generate`、`tools/ci_smoke.py`、README 退出码表 |
| 生成层真对账（真值 `expect_generated` 逐键 + 锚点判据）+ 探活 `drop-candidate-assert` | `src/s2r/bench.py`、`src/s2r/benchdata.py`、`data/bench/**` |
| 测试与门禁 | `tests/`（**294 项**）、`tools/ci_smoke.py`（20/20）、`tools/fix_eol.py`（87 文件 0 问题） |

**已验证的事实**：双解释器各 294 项通过；`gen_bench --check` 位级一致（10 样本，rng_draws=5）；
`s2r bench` = `ok`（检出 10/10、误报 0，`pending` 只剩 M5 的 `red_green`）；**八个探活全部检出**；
**15 项变异验证全红**（M1–M3 的 7 项 + M4 新增 8 项）；离屏冒烟 20/20（`generate` 已按真实语义登记 0）；
**生成物语义核对**：同一骨架在修复前 FAILED（ZeroDivisionError）、修复后 1 passed（隔离临时目录内实跑）。
**CI 未接远端 → 一切 CI 结论为"本地等价校验，未接远端，CI 待首推验证"。**

---

## 二、下一棒待办（M5：RED/GREEN 验证 + manifest + 收尾）

> 上游设计在 `plan/04-模块详设.md` §六（M5）；口径文本与默认值在 `config/scoring.toml` 的 `[verify]`
> 与 `[generate]`（`tests_dir` / `manifest_path`）。

1. **`src/s2r/verify.py`**（隔离副本，不碰用户工作区）：
   * 目标仓库 → 临时目录（前缀取 `[verify].tmpdir_prefix`；`shutil.copytree`）；
   * 把 `Reconstruction.before` 写进副本 → 跑**新测试** → **必须失败（RED）**（判据 `red_expectation`）；
   * 切到 `after` → 跑同一测试 → **必须通过（GREEN）**（`green_expectation`）；
   * 任一步不满足 → `unverified` + 原因（测试未失败 / 修复后仍失败 / 环境不可用 / 超时）；
     **未通过项不写进正式测试目录**；
   * 命令与超时只读配置（`pytest_command` / `timeout_seconds`），**代码内不留默认值**；
   * 缺 pytest → `DependencyMissing`（exit 3，`cli.pytest_available()` 已在门口用着）。
2. **`regression/manifest.json`**（落点取 `[generate].manifest_path`）：每条 `episode_id` / `session_id` /
   `source` / 文件与 hunk 摘要 / 生成测试路径 / RED/GREEN 退出码与**截断**日志摘要 /
   `status ∈ {verified, unverified, needs_manual}` / `reason` / 生成器版本；**覆盖度门槛由基准 manifest 下发**
   （`data/bench/manifest.json` 的 `coverage.min_count_by_type`）。
3. **`s2r verify EPISODE_REF` + `s2r run --target DIR` 落地**（退役 exit 4 四处同步：
   `cli.STAGED_COMMANDS` → `tools/ci_smoke.py` 的 `DELIVERED_EXPECT` + 调用形态 → README 退出码表
   （**这一步之后第 4 行才能删**，`tests/test_smoke_coverage.py` 会提醒）→ `plan/04·06` 状态列），
   补 bare + module 两形态离线冒烟。
   * 退出码：`verify`/`run` = 0（至少一条 verified）/ 1（无 verified）/ 2（用法错误）/ 3（缺 pytest）。
4. **基准**：① 真值加 `expect_red_green`（构造侧声明 RED/GREEN 的**期望退出码语义**）；
   ② `bench` 增加 RED/GREEN 事实并对账；③ `pending_facts.red_green` 从 pending 转为真对账
   （**`tests/test_bench.py::test_manifest_declares_coverage_thresholds_and_pending_facts` 现在断言
   `pending == {"red_green"}`，落地时要改成空集**）；④ 至少一个新探活（如"RED 判据改成恒真"必须被检出）。
5. **变异验证"隔离与不落盘是真的"**：① 把副本写回用户仓库 → 必须红；② 未通过项也写进正式目录 → 必须红；
   ③ RED 判据退化成"跑完就算过" → 必须红；④ 缺 pytest 时退化成 exit 0 → 必须红。
6. **收尾**（M5 是交付棒，别漏）：README 三分钟演示补 `verify`/`run`、退出码表删第 4 行、
   `plan/00·04·06·07` 状态回写、`plan/verify/README.md` 追加 M5 章节、
   **版本号与 `--version` 一致性复核**、技术报告（题面 §三 要求的提交材料之一，本仓库尚无 `REPORT` 类文件
   ——**M5 需要定稿一份**，内容建议：会话格式归一化 / 切分口径 / 还原降级策略 / 模板生成与候选断言推理 /
   RED-GREEN 隔离验证 / 隐私设计）。

### 2.1 M5 必须先拍板的两件事（M4 实测发现，别当成实现细节）

* **哪些骨架能被验证**：只有 `status == "generated"`（带**活动**候选断言）的骨架才可能 RED→GREEN。
  `needs_manual` 的骨架（无活动断言）在修复前后都会"通过"——正确结论是 **`unverified` + 原因
  "测试未失败"，不写正式目录**，而不是把它当 verified。M4 已实测：候选断言在
  `return a / b` 上抛 ZeroDivisionError、在 `return a / b if b else 0` 上通过（见
  `tests/test_generate_cli.py::test_generated_candidate_is_red_before_and_green_after`，可直接借用其
  隔离-临时目录-跑 pytest 的形态）。
* **"生成时间"与 K5（无时钟）冲突**：设计里 manifest 要写"生成器版本与生成时间"，但本项目
  **全链路禁时钟**，同输入双跑必须逐字节一致（基准的台账门就是靠这个）。建议落法：时间戳
  **取会话日志自身的 `end_ts`**（已在生成物 docstring 里，可复算），或另开一个**显式**的
  `--generated-at` / 环境变量覆盖（不给就是确定值）。**不要 `datetime.now()`**——那会让
  "同输入同产物"当场失效，也会让 manifest 无法进台账。
* 另：`before`/`after` 是**全文**，写进副本要用"日志里的文件路径 → 仓库内相对路径"的映射。
  `src/s2r/generate.py` 里已有 `_under_root(file_path, root)` 做这件事（目前是私有的，
  M5 需要时**把它提升为公开 helper 并保留同一份实现**，别在 verify 里再写一份）。

---

## 三、既定口径清单（K 编号，动了会打挂测试）

> 完整表见 `plan/07-交付对标与决策记录.md` §五；下一棒必须遵守的最小集：

* **K1** 唯一数值口径 = `config/scoring.toml`（+ 随包副本逐字节相同）；**代码内零口径数值**。
  ⚠️ 改 `config/` 后**必须** `cp config/scoring.toml src/s2r/resources/scoring.toml`（否则 `selftest` 的
  `config_copies_identical` 当场红）；新增叶子键必须同时进 `config.SCHEMA`（`test_every_config_leaf_is_schema_checked`）。
* **K2** 退出码 0/1/2/3/4：**M5 落地后 4 整项退役**（四处同步，README 第 4 行删除只有在这时才是对的）。
* **K5/K6** 无时钟无随机无网络；同输入双跑逐字节一致（**中间层 + CLI 出口两层**）；写盘只经 `serialize`
  （jsonl 用 `dumps_json_line`，K18）。
* **K11** **绝不修改用户源码、不碰 git 索引**：M5 的验证在**隔离临时副本**内进行；未通过项不写正式目录；
  `--repo`/`--target` 只读，写入只发生在副本。
* **K13/K20** 库内相对路径、不复述词面；判定词表只准来自配置（集合字面量里出现 ≥2 个词表词即红）；
  ⚠️ **模板/生成物/manifest 文案里也不得出现判定词面**（`tests/` 与 `src/` 都在扫描面内）。
* **K24/K25** 还原四态不折叠；置信度由 before 镜像来源决定；磁盘对齐是显式开关（不给不读、给了只读）。
* **K26** 还原对账面 = 逐文件逐字段；`pending_facts` 只留未对账的面。
* **K27**（M4 新增）**生成层两态不折叠**：只有还原 `ok` 进生成路径；非 `.py` 记 `not_python`；
  符号识别不到 → **不生成文件**；**只写测试目录**；同 episode 逐字节相同。
* **K28**（M4 新增）**候选断言只认一种可证伪形态**（守卫形态），必须带 `[generate].candidate_note` 标注；
  "改一段 diff 描述 → 候选断言随之变化"是防模板空转的判据。
* **K29**（M4 新增）**生成层对账面 = 逐键**；真值 `expect_generated` 由构造侧声明；锚点判据 =
  "声明过的片段必须真的在骨架里"。
* **K14/K16/K17/K19/K21/K22/K23** 沿用 M1–M3（非空判据 / 数值判失败 / patch 指令全集 / 探活打在判定路径 /
  批量按件隔离 / 同命令配对 / `docs_only` 独立规则）。
* 基准作用域分工：完整性门（台账/在位/覆盖度）看全量语料；判定门（检出率/误报）看 `--only` 选中项。

---

## 四、本机环境坑（只写实测过的）

| 坑 | 处置 |
|---|---|
| **用脚本改代码/文档风险高**（转义层吃反斜杠 + 脚本写文件在 Windows 会变 CRLF） | 改**代码**用编辑工具逐处改；脚本回写文档必须 `open(..., newline=chr(10))`，改完立刻跑 `py -X utf8 tools/fix_eol.py`（M4 本轮踩过一次：`plan/00` 被脚本写成 CRLF，143 处） |
| **`Path.write_text(newline=...)` 是 3.10+ 参数** | 3.8 通道一律 `open(..., newline=chr(10))`；`tests/` 里 `test_reconstruct.py` / `test_generate*.py` 都有 `write_text` 小工具可复用 |
| **`Path.is_file()` 在 3.8 遇非法文件名会抛 `OSError`** | 包一层 `try/except (OSError, ValueError)`（`reconstruct._is_file` / `generate._is_file` 都是这么来的） |
| Bash 里 `\r`/`\n` 转义会被工具层吃掉一层；**Python 字符串里的 `/tmp/...` 不会被 Git Bash 翻译** | 判断行尾用 Python 读字节；临时目录用 `$TEMP` 展开出的真实 Windows 路径（`cygpath -w` 或 `$LOCALAPPDATA/Temp`），别在 Python 字面量里写 `/tmp` |
| 真实日志大体量（Claude 125 文件 / Codex 831 MB 量级） | 只读探针逐行 + 早停；**单行上限 16 MiB**（口径文件，实测有 8.8 MB 的行）；探针落在仓库树外、只打印聚合读数 |
| 两个解释器通道 | `py -3.8` 与 `py -3.12` 都要跑；3.8 是底线（禁 3.9+ 语法与参数） |
| 本机 venv `.venv-check/`（3.12，editable 装） | 安装面复核：`PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py` |
| 冒烟要跑需要产物落点的命令（`generate` / 将来 `verify`、`run`） | `tools/ci_smoke.py` 有 `%TMP%` 占位：`run_one` 现开临时目录替换并在 `finally` 里删掉——**冒烟不许往仓库里写文件** |
| 冻结语料含 `.py` 夹具（`data/bench/repo/**`） | 会被 EOL/脱敏门扫到：新增夹具必须 LF + 无绝对路径 + 不进 `pytest` 收集面（`testpaths = ["tests"]`） |
| `data/bench/**` 标了 `-text` | 改语料必须**重生成**（`tools/gen_bench.py --force`）并同步 manifest sha256；手改冻结物会被台账门抓住 |
| 变异验证跑批 | 复用仓库树外的 `%TEMP%/s2r-mutations/mutate_m4.py`（备份→改→**先确认模块可导入**→跑→还原）；"RED" 必须是判据红，不是"改坏了模块导致收集失败" |
| M5 会在临时副本里跑子进程 pytest | 显式传 `env`（含 `PYTHONUTF8=1`）、`cwd=副本`、`encoding="utf-8"`（K7 三层加固之一），别依赖父进程环境 |

---

## 五、DoD 逐项 checklist（M5）

- [ ] `verify.py`：隔离副本 RED/GREEN（before 必失败 / after 必通过），四类 `unverified` 原因各一条
- [ ] `needs_manual` 骨架 → `unverified` + 原因（**不许**当 verified；不许写进正式目录）
- [ ] `manifest.json` schema + `status`/`reason` 枚举约束 + 覆盖度门槛由基准 manifest 下发
- [ ] 时间戳落法确定（**禁时钟**：取日志 `end_ts` 或显式注入；同输入双跑逐字节一致）
- [ ] 退出码 0/1/2/3 各有测试；缺 pytest → exit 3（import 桩复现）
- [ ] `s2r verify` / `s2r run` 落地并退役 exit 4（四处同步；README 第 4 行删除）+ bare/module 两形态冒烟
- [ ] 基准：`expect_red_green` 真对账 + 摘掉 `pending_facts.red_green` + 至少一个新探活
- [ ] 变异验证：隔离/不落盘/RED 判据/缺依赖语义 四项"弄坏必红"
- [ ] 双解释器全量 pytest 全绿；`tools/ci_smoke.py` 全绿；`gen_bench --check` 位级一致；`fix_eol --check` 干净
- [ ] `pip install .`（非 editable）+ 中立 CWD 实跑；断网运行（零上传的端到端证明）
- [ ] 技术报告定稿（题面 §三 提交材料）；README 三分钟演示含 `verify`/`run`
- [ ] `plan/00` §三/§五、`plan/04` §一/§六、`plan/06` 状态表、`plan/07` K 清单、README、`plan/verify/README.md` 回写
- [ ] **本对话内写完 `plan/HANDOFF-M6.md`**（六节）→ commit

---

## 六、关键命令速查（下一棒开工就用这些）

```bash
# 环境
py -0p                                             # 解释器通道（3.8 / 3.12）
python -m pytest -q                                # 全量测试（3.8 与 3.12 各跑一遍）

# 已交付命令（M1–M4）
s2r scan data/bench/claude --out events.json
s2r reconstruct data/bench/claude/multi-bug-1.jsonl --out rec.json
s2r bench                                          # 基准对账（10/10，含还原层与生成层）
s2r bench --probe drop-candidate-assert            # 探活（八个）
s2r generate "data/bench/claude/multi-bug-1.jsonl#4" --out-dir /tmp/s2r-out
s2r generate "data/bench/claude/structured-patch-1.jsonl#4" --repo <目标仓库>

# 语料与门禁
python tools/gen_bench.py --check                  # 语料/真值/夹具再生成位级一致
python tools/fix_eol.py --check                    # 行尾卫生（改完文件立刻跑）
PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py

# 变异验证跑批（仓库树外）
py -3.8 -X utf8 "$TEMP/s2r-mutations/mutate_m4.py"
```
