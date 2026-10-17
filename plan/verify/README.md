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

### 4. 与 CI 的缺口（诚实标注）

* **GitHub Actions 从未运行**（本机无法运行远端 CI 且尚未建远端）→ 一切 CI 结论为
  **"本地等价校验，未接远端，CI 待首推验证"**。
* 等价物清单（已跑）：双解释器全量 pytest、EOL 门、离线 CLI 冒烟（含 console script 与 `-m` 两形态）、
  非 editable 安装 + 中立 CWD 自检、变异验证 6 项。
* **缺口（本机测不了，显式待首推验证）**：① 四腿矩阵的 runner 环境差异（尤其 `ubuntu-22.04 + 3.8` 这一腿）；
  ② `actions/setup-python` 各版本可用性；③ CI 步骤的 shell/接线（本项目已把断言落成 `tools/` 脚本，
  降低内联脚本风险）；④ 网络/代理差异。
