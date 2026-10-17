# HANDOFF-M1（下一棒：M1 数据先行）

> 由 M0（计划 + 骨架，2026-10-17）交出。**本文件是下一棒的唯一入口**：新对话零上下文，
> 上下文全在这里与 `plan/`、`data/`、`README.md` 里。
> 用法（新对话第一条）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M1.md" 继续完成任务`
> **库内一律相对路径**；绝对路径只出现在用户侧的启动命令里。

---

## 一、当前进度 + 证据

**已完成：M0 计划先行 + 骨架**（`plan/06-里程碑.md` M0 行、状态一览表均为 ✅）

| 交付物 | 证据位置 |
|---|---|
| 计划文档 00–07 | `plan/`（00 总览 / 01 题面 / 02 需求 / 03 架构 / 04 模块详设 / 05 数据计划 / 06 里程碑 / 07 交付对标） |
| 数据台账（依据 + 本机实测 + 合成白名单 + 待核对 R1–R5） | `data/README.md` |
| CLI 契约与退出码（0/1/2/3/4，4 = 阶段未就绪） | `src/s2r/cli.py`、`src/s2r/errors.py`、README 退出码表 |
| 唯一数值口径（仓库 + 随包副本，逐字节相同） | `config/scoring.toml`、`src/s2r/resources/scoring.toml` |
| 严格 TOML 子集读取器（零依赖） | `src/s2r/tomlmini.py` |
| 确定性写盘单点 | `src/s2r/serialize.py` |
| 事件流 IR + harness 判别 | `src/s2r/events.py` |
| 编码守卫 | `src/s2r/stdio.py` |
| 门禁测试 12 文件 / 116 项 | `tests/` |
| 离线冒烟 + EOL 门 | `tools/ci_smoke.py`、`tools/fix_eol.py` |
| CI 工作流（四腿矩阵，未实跑） | `.github/workflows/ci.yml` |
| 本地等价校验逐条留档 + 变异验证 6 项 | `plan/verify/README.md` |

**已验证的事实**（详见 `plan/verify/README.md`）：双解释器各 116 项通过；EOL 门 40 文件 0 问题；
离线冒烟 20/20；非 editable 安装 + 中立 CWD 自检通过；**CI 未接远端，待首推验证**。

---

## 二、下一棒待办（M1 数据先行）

1. **闭环 R1–R3 三个 ⬜ 字段**（未闭环不得进入 M2 判定路径，K4）：
   * R1 Codex `apply_patch` 载荷内的文件路径字段（JSON arguments 里还是 patch 文本指令行里）；
   * R2 Claude `MultiEdit` 字段形态（本机是否出现；未出现则登记"本机 0 次"并保留降级语义）；
   * R3 Codex `exec` 的失败判定字段语义（退出码在哪个键、命令失败 vs 工具失败怎么区分）。
   做法：**只读探针先行**（一次性脚本读本机日志、聚合键集与形态分布），把结论（脱敏样本描述 + 结论 + 口径）
   回填 `data/README.md` §2.5/§五。
2. **`src/s2r/readers/`**：按首行形态分流（不按目录名）→ 归一 `Event`（`seq` = 源行号）→ 版本指纹表落地
   → 行长/事件数安全阀（阈值已在口径文件）→ 未知格式 `unsupported_format`（列缺失键）。
3. **`src/s2r/paths.py`**：日志根发现（`--claude-root`/`--codex-root` → 环境变量 → 常见位置），
   **不得硬编码用户名**；找不到显式报错（exit 2）。
4. **`tools/gen_bench.py` + `data/bench/**`**：双格式合成日志 + 真值标注（只存输入与期望）+ `--check` 位级一致
   + `manifest.json` 覆盖度门槛（类型枚举与门槛数值一处定义）。
5. **`s2r scan` 落地**：退役 exit 4（四处同步：`cli.STAGED_COMMANDS` → `tools/ci_smoke.py` 期望值 → README 退出码表 → `plan/04`/`plan/06` 状态列），
   并补 bare + module 两种形态的离线冒烟。
6. `s2r bench` 落地（真值对账 + 双边门 + 探活）。

---

## 三、既定口径清单（K 编号，动了会打挂测试）

> 完整表见 `plan/07-交付对标与决策记录.md` §五；下一棒必须遵守的最小集：

* **K1** 唯一数值口径 = `config/scoring.toml`（+ 随包副本逐字节相同）；**代码内零口径数值**——
  新阈值/超时/上限一律写进配置文件，不在代码里留默认值。
* **K2** 退出码 0/1/2/3/4；**4 = 阶段未就绪**，命令落地时**四处同步退役**。
* **K3** 事件流 IR 字段集 + `seq` = **源文件行号**（不是数组下标）；`unknown` 哨兵，不写空串。
* **K4** **R1–R3 未闭环的字段不得进入 M2 判定路径与 M4 生成路径**；不认识就 `unsupported_format`。
* **K5** 无时钟、无随机、无网络、无并发；**同输入双跑逐字节一致**（单元 + CLI 出口两层）。
* **K6** 写盘只经 `serialize.write_*`（`sort_keys=True` + `ensure_ascii=False` + LF + 结尾单换行 + 原子写）。
* **K7** 编码三层加固；`tools/` 下每个会打印的脚本都要调 `stdio.ensure_utf8_stdio`。
* **K8/K9** 合成数据白名单（零真实值）；真值**只存输入与期望**、期望可由输入独立复算，禁止 `observed_*`。
* **K10** 基准**双边门**（误报率 0 + 检出率 ≥ 门槛）+ 逐样本点名 + 探活 + 变异验证。
* **K12** CLI 命令面冻结；每条已交付命令要有 bare + module 两形态离线冒烟。
* **K13** 库内一律相对路径、入库文本零本机路径形态、**留档与注释不复述词表词面**。

---

## 四、本机环境坑（只写实测过的）

| 坑 | 处置 |
|---|---|
| pip 受系统代理污染 | 安装一律 `NO_PROXY="*" no_proxy="*" py -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple …` |
| Windows 控制台中文 | 命令一律 `py -X utf8`（代码层已有守卫，但脚本习惯照旧） |
| 两个解释器通道 | `py -3.8` 与 `py -3.12` 都要跑；**3.8 是底线**（禁用 3.9+ 语法与 `pathlib` 的 3.10+ 参数） |
| `pyc` 偶发损坏 | 全程 `PYTHONDONTWRITEBYTECODE=1`；出怪错先清 `__pycache__` |
| `pip install -e .` 生成 `src/*.egg-info/` | 构建元数据（CRLF/缺尾换行）已从 EOL 扫描面剪枝；别再手写第二份遍历规则 |
| Git Bash 的 `grep -c $'\r'` 会因转义失真 | 判断行尾用 `py` 读字节（`b.count(b"\r\n")`），别信 shell 里的 `\r` 模式 |
| 本机有 `.venv-check/`（非 editable 安装的 3.12 环境） | 已 gitignore；可直接用它的 `s2r.exe` 跑安装面复核（`PATH` 需含其 `Scripts`） |
| 本机自动化拉不起浏览器 / 无远端 | Web 类验收不适用；发布类结论只能写"本地等价校验" |

---

## 五、DoD 逐项 checklist（M1）

- [ ] R1–R3 三项**逐样本核对**完成，结论回填 `data/README.md`（含脱敏样本描述 + 结论 + 口径）
- [ ] `s2r scan` 对合成日志产出稳定事件流：**同一日志双跑逐字节一致**（单元 + CLI 出口两层）
- [ ] 未知格式显式 `unsupported_format` 并列出缺失键（有测试）
- [ ] 超大会话/超长行安全阀生效且显式报错（有测试）
- [ ] 日志根发现不得硬编码用户名；找不到源 → exit 2（有测试）
- [ ] `tools/gen_bench.py` 生成器 + 真值标注入仓；`--check` 位级一致（两层：冻结产物形态 + 重生成比对）
- [ ] `data/bench/**` 在 `.gitattributes` 标 `-text`，克隆后 sha256 不失配（有核对记录）
- [ ] 覆盖度门槛写进 `data/bench/manifest.json`（类型枚举 + 最低条数一处定义）
- [ ] `s2r bench` 双边门 + 逐样本点名 + 探活（注入缺陷必报未达标）
- [ ] exit 4 对 `scan`/`bench` **整项退役**（四处同步，`grep -rn "STAGE_NOT_READY\|阶段未就绪"` 复核落点）
- [ ] 新增守门做变异验证（弄坏必红）；双解释器全量 pytest 全绿；`tools/ci_smoke.py` 全绿
- [ ] `plan/00` §一/§三/§五 与 `plan/06` 状态表回写 M1
- [ ] **本对话内写完 `plan/HANDOFF-M2.md`**（六节）→ commit

---

## 六、关键命令速查（下一棒开工就用这些）

```bash
# 环境
py -0p                                             # 列出解释器通道（3.8 / 3.12）
python -m pytest -q                                # 全量测试（3.8 与 3.12 各跑一遍）
export PYTHONDONTWRITEBYTECODE=1

# 当前可用命令（M0 已交付）
s2r --version
s2r selftest
s2r config show --json
s2r config path
python tools/ci_smoke.py                           # 离线冒烟（需已 pip install -e .）
python tools/fix_eol.py --check                    # 行尾门

# M1 落地后应新增（DoD 对应）
python tools/gen_bench.py --check                  # 基准再生成位级一致
s2r scan data/bench/claude/*.jsonl --out events.json
s2r bench
```
