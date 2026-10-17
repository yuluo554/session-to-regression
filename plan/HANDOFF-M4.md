# HANDOFF-M4（下一棒：M4 pytest 骨架生成器）

> 由 M3（差分还原，2026-10-17）交出。**本文件是下一棒的唯一入口**：新对话零上下文，
> 上下文全在这里与 `plan/`、`data/`、`README.md` 里。
> 用法（新对话第一条）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M4.md" 继续完成任务`
> **库内一律相对路径**；绝对路径只出现在用户侧的启动命令里。

---

## 一、当前进度 + 证据

**已完成：M0 骨架 ✅、M1 数据先行与读取器 ✅、M2 episode 切分 ✅、M3 差分还原 ✅**
（状态表见 `plan/06-里程碑.md`；逐条实跑留档见 `plan/verify/README.md`）

| 交付物 | 证据位置 |
|---|---|
| 双 harness 读取器（按 `payload.type` 分流 → 事件流 IR；`seq` = 源行号 + 字节偏移） | `src/s2r/readers/{__init__,claude,codex}.py` |
| episode 切分（起点 → 失败信号 → 修复动作 → 终点；权重/阈值只读配置） | `src/s2r/episode.py`、`tests/test_episode.py` |
| **三级降级还原 + 逐文件置信度**（① `originalFile`+非空 `structuredPatch` → ② `old_string` 定位 → ③ Codex `FileChange.unified_diff`/patch 文本 → ④ `no_before_image`） | `src/s2r/reconstruct.py`、`tests/test_reconstruct.py`（21 项）、`tests/test_reconstruct_cli.py`（14 项） |
| 还原产物契约：`files[*] = {file, before, after, hunks[], hunk_count, source, confidence, status, reason, tool, seq, evidence}` + `episodes[*] = {episode_id, status, reason, files[]}` + `counts` | 同上（`Reconstruction.fact()` 是对账用事实子集） |
| `s2r reconstruct SESSION`（`--repo` 显式磁盘基线、`--claude-root/--codex-root`、`--out`） | `cli.cmd_reconstruct`；exit 4 已整项退役 |
| 合成语料 **10 类样本** + 真值（含逐文件 `expect_reconstructions`）+ **磁盘基线夹具** + 台账 sha256 | `data/bench/**`、`src/s2r/benchdata.py` |
| 基准对账（双边门 + 逐样本点名 + 台账门 + **七探活**）；`reconstructions` 进对账面，`pending_facts.before_after` 已摘除 | `src/s2r/bench.py`、`tests/test_bench.py` |
| 测试与门禁 | `tests/`（**243 项**）、`tools/ci_smoke.py`（20/20）、`tools/fix_eol.py`（83 文件 0 问题） |

**已验证的事实**：双解释器各 243 项通过；`gen_bench --check` 位级一致（10 样本，rng_draws=5）；
`s2r bench` = `ok`（检出 10/10、误报 0，`pending` 只剩 M4–M5 两个面）；**七个探活全部检出**；
**7 项新增变异验证全红**；离屏冒烟 20/20（`reconstruct` 已按真实语义登记 0）。
**CI 未接远端 → 一切 CI 结论为"本地等价校验，未接远端，CI 待首推验证"。**

---

## 二、下一棒待办（M4 pytest 骨架生成器）

> 上游设计在 `plan/04-模块详设.md` §五（M4）；口径文本与默认值在 `config/scoring.toml` 的 `[generate]`。

1. **`src/s2r/generate.py`**：纯模板 + AST，**零模型**。
   * **约定探测**：`tests/` 布局、`pytest.ini` / `pyproject.toml [tool.pytest.ini_options]` / `setup.cfg` / `tox.ini`、
     `conftest.py` 与 fixture 风格、包根与 import 形态；**探测失败 → 保守默认 + manifest 标 `assumed_convention`**
     （默认值与占位文本**取自口径文件**，代码内不留字面量）。
   * **输出** `tests/regression/test_<slug>.py`，结构固定：
     **docstring**（会话 id、事件时间戳、**原始用户报告原文**、失败信号摘录、修复文件清单——证据锚点可回溯）；
     **Arrange**（AST 读修改前签名 + 从 diff 推断最小输入；构造不出 → 占位注释 + `needs_manual`）；
     **Act**（调用被改动的符号，diff 中被改动的函数/类优先）；**Assert**（占位 + 候选断言，
     **必须注明"候选，需人工确认"**）。
   * **纪律**：同一 episode → **逐字节相同**骨架（稳定排序、稳定 slug）；目标符号识别不到 → **不生成文件**
     （记 `needs_manual`）；**只写测试目录，不修改任何源码**（K11）。
2. **输入面（M3 产物）**：`reconstruct.reconstruct_session(file_read, cfg, repo=None)` → `SessionReconstruction`，
   其中 `files[]` 的 `status ∈ {ok, partial, unresolved, no_before_image}` 与 `hunks[]`
   （`{old_start, old_lines[], new_lines[], old_count, new_count}`）就是 M4 的原料。
   **只有 `ok` 能直接进生成路径**：`partial` 只有片段（`before`/`after` 为 null）需自行决定是否可用片段；
   `unresolved` / `no_before_image` **一律不进生成路径**（记原因，不硬造）。
3. **`s2r generate EPISODE_ID` 落地**（退役 exit 4 四处同步：`cli.STAGED_COMMANDS` → `tools/ci_smoke.py`
   的 `DELIVERED_EXPECT` + 调用形态 → README 退出码表 → `plan/04·06` 状态列），补 bare + module 两形态离线冒烟。
4. **基准**：① 真值加 `expect_generated`（构造侧声明骨架里的**必需锚点**，如会话 id / 报告片段 / 三段在场）；
   ② `bench` 增加生成事实并对账；③ `pending_facts.generated_tests` 从 pending 转为真对账；
   ④ 新增探活（如"把候选断言整段丢掉"必须被检出）。
5. **变异验证"模板不是空转"**：改一段 diff 描述 → 生成的候选断言必须随之变化（改了却不影响输出 = 模板空转）。

---

## 三、既定口径清单（K 编号，动了会打挂测试）

> 完整表见 `plan/07-交付对标与决策记录.md` §五；下一棒必须遵守的最小集：

* **K1** 唯一数值口径 = `config/scoring.toml`（+ 随包副本逐字节相同）；**代码内零口径数值**。
  ⚠️ 改 `config/` 后**必须** `cp config/scoring.toml src/s2r/resources/scoring.toml`（否则 `selftest` 的
  `config_copies_identical` 当场红）；新增叶子键必须同时进 `config.SCHEMA`（`test_every_config_leaf_is_schema_checked`）。
* **K5/K6** 无时钟无随机无网络；同输入双跑逐字节一致；写盘只经 `serialize`，**jsonl 用 `dumps_json_line`**（K18）。
* **K11** **绝不修改用户源码、不碰 git 索引**：M4 只写测试目录（`[generate].tests_dir`），验证在隔离副本内。
* **K13/K20** 库内相对路径、不复述词面；判定词表只准来自配置（集合字面量里出现 ≥2 个词表词即红）；
  ⚠️ **模板/生成物里也不得出现判定词面**（`tests/` 与 `src/` 都在扫描面内）。
* **K24** 还原四态不折叠：`ok` / `partial`（片段）/ `unresolved`（不任选）/ `no_before_image`；
  `status=ok` 必须同时有 before 与 after。
* **K25** 置信度由 before 镜像来源决定（日志内全文 `high` / 磁盘 `medium` / 片段 `low` / 取不到 `none`）；
  磁盘对齐是**显式开关**（`--repo`），不给不读、给了只读。
* **K26** 还原对账面 = 逐文件逐字段（`file/status/source/confidence/before/after/hunk_count`）；
  真值 `expect_reconstructions` 由**构造侧**声明；`pending_facts` 只留未对账的面（M4 落地时必须摘掉
  `generated_tests`，`test_bench.py::test_manifest_declares_coverage_thresholds_and_pending_facts` 会盯住）。
* **K14/K16/K17/K19/K21/K22/K23** 沿用 M1–M2（非空判据 / 数值判失败 / patch 指令全集 / 探活打在判定路径 /
  批量按件隔离 / 同命令配对 / `docs_only` 独立规则）。
* 基准作用域分工：完整性门（台账/在位/覆盖度）看全量语料；判定门（检出率/误报）看 `--only` 选中项。

---

## 四、本机环境坑（只写实测过的）

| 坑 | 处置 |
|---|---|
| **用脚本改代码文件风险高**（转义层吃反斜杠 + 行手术易留残括号） | 改**代码**用编辑工具逐处改；脚本只用于文档/数据回写，且改完立刻跑 `pytest` 与 `py -X utf8 tools/fix_eol.py`（M2/M3 各踩过一次） |
| **`Path.write_text(newline=...)` 是 3.10+ 参数** | 3.8 通道一律 `open(..., newline=chr(10))`（M3 测试首版就这么红的）；`tests/` 里已有 `write_text` 小工具可复用 |
| **`Path.is_file()` 在 3.8 遇非法文件名会抛 `OSError`**（如 Windows 的 `claude:`），3.12 只返回 False | 包一层 `try/except (OSError, ValueError)`（`reconstruct._is_file` 就是这么来的） |
| 脚本写文件在 Windows 会变 CRLF | 一律 `open(str(p), "w", encoding="utf-8", newline=chr(10))`；改完跑 `tools/fix_eol.py` |
| Bash 里 `\r`/`\n` 转义会被工具层吃掉一层（heredoc 里的 Python 字符串替换会静默不生效） | 判断行尾用 Python 读字节；生成文件优先用编辑工具 |
| 真实日志大体量（Claude 125 文件 / Codex 831 MB 量级） | 只读探针逐行 + 早停；**单行上限 16 MiB**（口径文件，实测有 8.8 MB 的行） |
| 两个解释器通道 | `py -3.8` 与 `py -3.12` 都要跑；3.8 是底线（禁 3.9+ 语法与参数） |
| 本机 venv `.venv-check/`（3.12，editable 装） | 安装面复核：`PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py` |
| 冻结语料含 `.py` 夹具（`data/bench/repo/**`） | 会被 EOL/脱敏门扫到：新增夹具必须 LF + 无绝对路径 + 不进 `pytest` 收集面（`testpaths = ["tests"]`） |
| `data/bench/**` 标了 `-text` | 改语料必须**重生成**（`tools/gen_bench.py --force`）并同步 manifest sha256；手改冻结物会被台账门抓住 |
| 变异验证跑批 | 复用仓库树外的 `%TEMP%/s2r-mutations/mutate.py`（备份→改→跑→还原），把"弄坏必红"落成一次原子动作 |

---

## 五、DoD 逐项 checklist（M4）

- [ ] 约定探测（`tests/` 布局 / pytest 配置 / conftest / 包根与 import）落地；探测失败 → 保守默认 + `assumed_convention` 显式标注
- [ ] 生成的骨架含固定三段 + docstring 证据锚点（会话 id / 时间戳 / **报告原文** / 失败信号摘录 / 修复文件清单）
- [ ] 候选断言**必须注明"候选，需人工确认"**；无法构造 Arrange → 占位注释 + `needs_manual`（带原因）
- [ ] 目标符号识别不到 → **不生成文件**（宁缺勿造），`needs_manual` 有明确原因
- [ ] 同 episode → **逐字节相同**骨架（双跑 + CLI 出口两层）
- [ ] **不改任何源码**：断言目标仓库源码树哈希不变（K11）
- [ ] `s2r generate` 落地并退役 exit 4（四处同步），补 bare + module 两形态离线冒烟
- [ ] 基准：生成内容真对账（`pending_facts.generated_tests` 摘除）+ 至少一个新探活
- [ ] 变异验证：**改一段 diff 描述 → 候选断言随之变化**（防模板空转）；弄坏必红
- [ ] 双解释器全量 pytest 全绿；`tools/ci_smoke.py` 全绿；`gen_bench --check` 位级一致；`fix_eol --check` 干净
- [ ] `plan/00` §三/§五、`plan/04` §五、`plan/06` 状态表、`plan/07` K 清单、README、`plan/verify/README.md` 回写
- [ ] **本对话内写完 `plan/HANDOFF-M5.md`**（六节）→ commit

---

## 六、关键命令速查（下一棒开工就用这些）

```bash
# 环境
py -0p                                             # 解释器通道（3.8 / 3.12）
python -m pytest -q                                # 全量测试（3.8 与 3.12 各跑一遍）

# 已交付命令（M1–M3）
s2r scan data/bench/claude --out events.json       # 事件流 + episodes[]/filtered[]
s2r reconstruct data/bench/claude/multi-bug-1.jsonl --out rec.json
s2r reconstruct data/bench/codex/happy-path-1.jsonl --repo data/bench/repo/happy-path-1
s2r bench                                          # 基准对账（10/10，含还原层）
s2r bench --probe swap-before-after                # 探活（七个）
s2r bench --only structured_patch --out report.json

# 语料与门禁
python tools/gen_bench.py --check                  # 语料/真值/磁盘夹具再生成位级一致
python tools/fix_eol.py --check                    # 行尾卫生（改完文件立刻跑）
PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py
```
