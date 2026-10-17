# HANDOFF-M2（下一棒：M2 episode 切分）

> 由 M1（数据先行 + 双 harness 读取器 + 合成基准，2026-10-17）交出。**本文件是下一棒的唯一入口**：
> 新对话零上下文，上下文全在这里与 `plan/`、`data/`、`README.md` 里。
> 用法（新对话第一条）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M2.md" 继续完成任务`
> **库内一律相对路径**；绝对路径只出现在用户侧的启动命令里。

---

## 一、当前进度 + 证据

**已完成：M0 骨架 ✅、M1 数据先行 ✅**（`plan/06-里程碑.md` 状态一览、《verify/README.md》逐条留档）

| 交付物 | 证据位置 |
|---|---|
| R1–R3 字段核对闭环 + **三处勘误**（E1 before 镜像判据 / E2 命令工具名 / E3 patch 指令集含 Move to） | `data/README.md` §六（含全量行类型与键集枚举） |
| 双 harness 读取器（按 `payload.type` 分流 → 事件流 IR） | `src/s2r/readers/{__init__,claude,codex}.py` |
| 日志根自适应发现（显式 → 环境变量 → HOME 常见位置；不硬编码用户名） | `src/s2r/paths.py` |
| 统一事件流 IR + 版本指纹表（键集合判据） | `src/s2r/events.py` |
| `s2r scan`（0 = 读到事件 / 1 = 零事件 / 2 = 输入不可用） | `src/s2r/cli.py::cmd_scan` |
| 合成语料 + 植入真值 + manifest 台账（8 类样本，sha256） | `data/bench/**`、`src/s2r/benchdata.py`、`tools/gen_bench.py` |
| 基准对账器（双边门 + 逐样本点名 + 四探活 + 台账门） | `src/s2r/bench.py`、`src/s2r/cli.py::cmd_bench` |
| 测试与门禁 | `tests/`（**179 项**）、`tools/ci_smoke.py`（20/20）、`tools/fix_eol.py` |

**已验证的事实**（详见 `plan/verify/README.md` §M1）：双解释器各 179 项通过；`gen_bench --check` 位级一致；
`s2r bench` = `ok`（检出率 8/8、误报 0、台账一致、覆盖度达标）；**四个探活全部检出**；
5 项新增守门做了变异验证（全红）；真实日志（Claude/Codex 各一份）只读抽验通过。
**CI 未接远端 → 一切 CI 结论为"本地等价校验，未接远端，CI 待首推验证"。**

---

## 二、下一棒待办（M2 episode 切分）

1. **语义词表读取**：唯一来源仍是 `config/scoring.toml` 的 `[vocabulary]`（`bug_terms` / `failure_signals`）；
   源码与测试**不得复述词面**（K13/K20）。
2. **切分状态机**（确定性、可回溯）：起点（`user_msg` 命中 bug 词）→ 失败信号（其后的 `tool_result`：
   Claude 侧看文本；Codex 侧**只在数值上判** `exit_code != 0`，见 K16）→ 修复动作（`tool_call` 的文件写类工具：
   Claude `Edit`/`Write`、Codex `apply_patch`）→ 终点（同一测试命令转通过 → `verified_by_run`；
   会话结束 → `ended_without_pass`）。
3. **打分与阈值过滤**：权重/阈值只读口径文件（`scoring.weights` / `scoring.threshold.min_score`）；
   低分候选**计入过滤统计**（不是静默丢弃）。`episode_id` 用口径文件的 `id_format`（会话 id + 首次修复事件序号，无时钟）。
4. **四类边界情形落定**（每类至少一条断言）：一会话多 bug（`episode_id` 不撞）、先成功后失败、
   失败后未修复、**用户仅提问不得被切为修复 episode**。
5. **`s2r scan` 输出附 `episodes[]`**，并把基准的 `pending_facts.episodes` 从 `pending_m2` 转为**真对账**：
   在 `src/s2r/bench.py` 的 `_facts` 里加入 episode 事实（数量/类型/起止 seq），
   并在 `data/bench/truth/*.truth.json` 的 `expect` 里启用 `episodes`（真值由 `benchdata.Builder` 构造侧登记）。
6. **退役纪律**：M2 不新增子命令，因此 exit 4 无需退役；但 `scan` 的输出 schema 扩展要在
   `tests/test_scan_cli.py` 与 README 演示里同步（schema 升版要改 `events.EVENT_SCHEMA_VERSION`）。

---

## 三、既定口径清单（K 编号，动了会打挂测试）

> 完整表见 `plan/07-交付对标与决策记录.md` §五；下一棒必须遵守的最小集：

* **K1** 唯一数值口径 = `config/scoring.toml`（+ 随包副本逐字节相同）；**代码内零口径数值**。
  ⚠️ 本轮踩过：改了 `config/` 忘了同步随包副本 → `selftest` 的 `config_copies_identical` 当场红（这是设计如此）。
* **K5** 无时钟、无随机、无网络；同输入双跑逐字节一致（单元 + CLI 出口两层）。
* **K6** 写盘只经 `serialize.write_*`；**jsonl 用 `dumps_json_line`（一行一对象）**，与 pretty 版是两种契约（K18）。
* **K13** 库内相对路径、不复述词面；**K20** 词表门两级判据（生产代码逐文件 / 测试单字面量）。
* **K14–K17** 读取器侧的实测结论（before 镜像按**键存在**判、命令工具名实测集合、失败判定取数值、patch 指令集含 Move to）。
* **K19** 探活注入必须打在**判定路径**上（记为误报让误报门抓），不得"直接记通过"。
* 基准作用域分工：完整性门（台账/在位/覆盖度）看全量语料；判定门（检出率/误报）看 `--only` 选中项。

---

## 四、本机环境坑（只写实测过的）

| 坑 | 处置 |
|---|---|
| **用 Python 脚本改文件会把行尾写成 CRLF**（Windows 默认换行转换）→ EOL 门红 | 每轮脚本化改动后立刻 `py -X utf8 tools/fix_eol.py`；或用 `open(..., newline=chr(10))` |
| `Path.write_text(newline=...)` 是 **3.10+** 参数，3.8 上直接 TypeError | 3.8 通道一律 `open(str(p), "w", encoding="utf-8", newline=chr(10))` |
| py 启动器跑某些含中文的临时脚本报 `Non-UTF-8 code …`（同内容用 heredoc 跑正常） | 一次性脚本优先 `py -X utf8 - <<'PYEOF'`（heredoc）；失败就换写法，别在编码上纠缠 |
| Bash 里 `$'\r'` / `\\n` 这类转义在工具调用层会被吃掉一层 | 判断行尾用 Python 读字节（`b.count(b'\r\n')`）；生成文件内容用 Write 工具或 heredoc，别在 shell 里拼转义 |
| `pip install -e .` 生成 `src/*.egg-info/`（CRLF/缺尾换行） | 已在 `tools/fix_eol.py` 的扫描面按目录后缀剪枝；**别再手写第二份遍历规则** |
| 两个解释器通道 | `py -3.8` 与 `py -3.12` 都要跑；3.8 是底线（禁 3.9+ 语法） |
| 本机 venv `.venv-check/`（3.12，editable 装） | 跑安装面复核用：`PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py` |
| 真实日志很大（Claude 125 文件 / Codex 831 MB 量级） | 探针/抽验一律逐行 + 早停；事件数与行长上限在口径文件里 |

---

## 五、DoD 逐项 checklist（M2）

- [ ] 语义词表从配置读取；源码/测试零词面（词表门与变异验证保持绿）
- [ ] 切分状态机四步齐备，起点/失败信号/修复动作/终点各自可回溯（`episode` 带起止 seq 与证据列表）
- [ ] `episode_id` 按口径文件 `id_format` 生成，同一输入 → 同一 id、同一排序
- [ ] 打分权重只读配置；低分候选进过滤统计（有断言）
- [ ] 四类边界情形各有断言（含"仅提问不得成 episode"）
- [ ] `s2r scan` 输出 `episodes[]`；schema 版本按需升版并同步测试与 README
- [ ] 基准 `pending_facts.episodes` 转真对账：`data/bench/truth/*.json` 启用 `episodes`，`bench` 报 `ok`
- [ ] 新增/改写守门做变异验证（弄坏必红）；双解释器全量 pytest 全绿；`tools/ci_smoke.py` 全绿
- [ ] `plan/00` §三/§五、`plan/06` 状态表、`plan/verify/README.md`（M2 章节）回写
- [ ] **本对话内写完 `plan/HANDOFF-M3.md`**（六节）→ commit

---

## 六、关键命令速查（下一棒开工就用这些）

```bash
# 环境
py -0p                                             # 解释器通道（3.8 / 3.12）
export PYTHONDONTWRITEBYTECODE=1
python -m pytest -q                                # 全量测试（3.8 与 3.12 各跑一遍）

# 已交付命令（M1）
s2r --version
s2r selftest                                       # 含 M1 新增的版本指纹自洽检查
s2r config show --json
s2r scan data/bench/claude --out events.json       # 事件流（M2 起附 episodes[]）
s2r scan data/bench/invalid                        # 应 exit 2 + unsupported_format
s2r bench                                          # 基准对账（退出码即结论）
s2r bench --probe drop-exit-code                   # 探活：0 = 缺陷被检出
s2r bench --only happy_path

# 语料与门禁
python tools/gen_bench.py --check                  # 语料再生成位级一致
python tools/fix_eol.py --check                    # 行尾卫生（改完文件先跑这个）
PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py
```
