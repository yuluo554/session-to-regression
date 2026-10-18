# HANDOFF-M6（下一棒：发布三件套）

> 由 M5（RED/GREEN 隔离验证 + manifest + 交付收尾，2026-10-17）交出。**本文件是下一棒的唯一入口**：
> 新对话零上下文，上下文全在这里与 `plan/`、`data/`、`README.md`、`docs/技术报告.md` 里。
> 用法（新对话第一条）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M6.md" 继续完成任务`
> **库内一律相对路径**；绝对路径只出现在用户侧的启动命令里。

---

## 一、当前进度 + 证据

**已完成：M0 骨架 ✅、M1 数据先行与读取器 ✅、M2 episode 切分 ✅、M3 差分还原 ✅、
M4 骨架生成 ✅、M5 RED/GREEN 隔离验证 + manifest + 技术报告 ✅**
（状态表见 `plan/06-里程碑.md`；逐条实跑留档见 `plan/verify/README.md` §M5）

| 交付物 | 证据位置 |
|---|---|
| 隔离副本 RED/GREEN 验证（before 必失败 / after 必通过；四类 `unverified`；三态不折叠） | `src/s2r/verify.py`、`tests/test_verify.py`（19 项） |
| `regression/manifest.json`（逐 episode 证据链 + 门槛自基准下发 + 禁时钟 + 摘录可复现） | `verify.build_manifest` / `coverage_thresholds` / `stable_excerpt`、`tests/test_manifest.py`（8 项） |
| `s2r verify` / `s2r run`（退出码 0/1/2/3；**只有 verified 落测试目录**） | `cli.cmd_verify` / `cmd_run`、`tests/test_verify_cli.py`（14 项）、`tests/test_run_cli.py`（9 项） |
| **退出码 4 整项退役**（常量/异常类/命令表/staged handler 一并删；README 第 4 行已删） | `errors.py`、`cli.py`、`tools/ci_smoke.py`、README、`tests/test_cli_contract.py`、`tests/test_smoke_coverage.py` |
| 验证层真对账（`expect_red_green` 逐 episode 声明三态 + RED/GREEN 退出码）+ 新探活 `fold-unverified` | `bench.py`（`red_green` 事实面）、`benchdata.py`、`data/bench/**`、`tests/test_bench.py` |
| 技术报告（题面 §三 提交材料） | `docs/技术报告.md` |
| 测试与门禁 | `tests/`（**348 项**，双解释器各 348）、`tools/ci_smoke.py`（20/20）、`tools/fix_eol.py`（98 文件 0 问题）、`tools/gen_bench.py --check`（10 样本位级一致） |

**已验证的事实**：双解释器各 348 项通过；19 项变异全红（M1–M4 的 15 项复核 + M5 新增 4 项：隔离失效 /
不落盘被折叠 / RED 判据恒真 / 缺依赖退化成照跑）；离线冒烟 20/20（`verify` / `run` 就位，产物落种子临时目录）；
`s2r bench` = `ok`（检出 10/10、误报 0，`pending_facts` 空表）；九探活全部检出；
非 editable 安装 + 中立 CWD 自检通过；**断网运行**（socket 桩）全链路 exit 0；
真实日志只读抽验（Claude 40 份 → 7 episode / Codex 20 份 → 8 episode，全部 `needs_manual`，如实降级）。
**CI 未接远端 → 一切 CI 结论为"本地等价校验，未接远端，CI 待首推验证"。**

---

## 二、下一棒待办（M6：发布三件套）

> 上游口径见 `plan/06-里程碑.md` 的 M6 段与 `plan/00-README总览.md` §五、§七。

1. **`scripts/check_release.py`（M6 交付物，当前不存在）**：发布前一把过、无 FAIL。
   建议覆盖：①版本一致性（`s2r.__version__` ↔ `pyproject` 动态版本 ↔ README 里出现的版本行）；
   ②README 的 extras/命令/退出码/许可与实物一致（可复用 `tests/test_readme_gate.py` 的判据，
   但脚本要能在**未安装**的干净 clone 上跑）；③冻结语料台账 sha256 自洽（复用 `s2r.benchdata`）；
   ④入库文本零本机路径形态（复用 `tests/test_privacy_gate.py` 的正则，**别新写一份**）；
   ⑤`git status` 干净 + 无未跟踪的产物（尤其 `data/bench/**` 之外别混进临时件）；
   ⑥**发布留档在位且引用的 sha256 与附件一致**。失败即 FAIL 并列出逐条原因。
2. **发布留档 `plan/RELEASE-M6.md`**：**必须在打 tag 之前写好并进 tag 树**（内容：产物文件名 + sha256 +
   下载复核步骤 + 环境前提 + 已知限制 + 隐私审计四层结论）。
3. **tag + Release**：`git tag -a`（annotated，tagger 邮箱用 noreply）；
   `gh release create <tag> <zip> --notes-file plan/RELEASE-M6.md`；
   **源码 zip 为手工上传附件**，本地必须存在同名副本 + sha256 留档，**不得**以"仓库 zipball 即产物"替代。
4. **发布物本体复核**：**真下载**（`gh release download`）+ **中立目录**解压 → 跑
   `s2r selftest` / `s2r config path` / 一条 `run` 演示；附件与 tag 树逐字节同源（比对 sha256）。
5. **隐私四层审计**（M0 只落了第 ① 层）：①源码 import 面（已有测试）；
   ②发布产物扫描（zip 内文本零本机路径形态）；③socket 拦截**动态**验证（可复用本轮的 offline 探针形态）；
   ④加载面静态检查（`python -X importtime` 或 AST 遍历：装好的包不引入网络模块）。
6. **提交面脱敏**：`git log --format='%ae %ce %an %cn'` 零真实邮箱（含 **tag tagger 邮箱**）；
   `git grep` 零本机盘符/家目录形态。
7. **文档回写**：`plan/00` §一/§三/§七、`plan/06` M6 段与状态表、`plan/07` §一第 6 行、
   README 顶部状态行（M6 完成后改"已发布"并给 Release 链接）。
8. **tag 后事实**作为**追加提交**分栏记录（附件 sha256 / 下载复核 / Release URL），**不改写历史措辞**。

### 2.1 M6 必须先拍板的两件事

* **对外动作要用户确认**：`gh repo create` / `git push` / `git tag` / `gh release create` 都是对外动作，
  **先问用户**（D6 待用户确认）。仓库名固定 `session-to-regression`（干净名），本地目录保留日期标签。
* **产物范围**：源码 zip 里**要不要含 `data/bench/**`**？建议**含**（它是交付物的一部分：基准与回归的
  输入冻结在仓里，`--check` 能复算），并在留档里写明体积与 sha256；
  若不含，`s2r bench` 在下载解压后无输入、只能跑 `--data-dir` 指向别处——那会让"下载即用"的复核失真。

---

## 三、既定口径清单（K 编号，动了会打挂测试）

> 完整表见 `plan/07-交付对标与决策记录.md` §五；下一棒必须遵守的最小集（**M5 新增 K30/K31**）：

* **K1** 唯一数值口径 = `config/scoring.toml`（+ 随包副本逐字节相同）；**代码内零口径数值**。
  ⚠️ 改 `config/` 后**必须** `cp config/scoring.toml src/s2r/resources/scoring.toml`；新增叶子键必须同时进
  `config.SCHEMA`（`test_every_config_leaf_is_schema_checked`）。
* **K2** 退出码 0/1/2/3（**4 已整项退役**）。改动退出码 = 四处同步（`errors.py` / `cli.py` / README / `ci_smoke.py`）。
* **K5/K6** 无时钟无随机无网络；同输入双跑逐字节一致（中间层 + CLI 出口两层）；写盘只经 `serialize`。
  ⚠️ manifest 的生成时间取日志 `end_ts`；日志摘录只留 `E`/`FAILED`/`ERROR` 行（否则 manifest 不可复现）。
* **K11** **绝不修改用户源码、不碰 git 索引**：验证在**隔离临时副本**内进行；只有 `verified` 落测试目录。
* **K12** CLI 命令面冻结（六条业务命令 + `--version`/`config`/`selftest`）；每条已交付命令都要
  bare + module 两种形态的离线冒烟。
* **K13/K20** 库内相对路径、不复述词面；判定词表只准来自配置（集合字面量里出现 ≥2 个词表词即红）；
  ⚠️ **模板/生成物/manifest/文档文案里也不得出现判定词面**。
* **K24–K29** 沿用 M3/M4（还原四态不折叠 / 置信度由 before 镜像来源决定 / 磁盘对齐显式开关 /
  还原对账面逐文件逐字段 / 生成层两态不折叠 / 候选断言只认守卫形态 / 生成层对账面逐键）。
* **K30**（M5 新增）**验证三态不折叠**：`verified` / `unverified`（四类原因） / `needs_manual`；
  验证在临时副本内；只有 `verified` 落盘；有内容没断言 → 跑两次 → `unverified` + `red_not_failed`；
  生成层没内容 → `needs_manual` + 生成层原因码且两次退出码为 `None`；路径映射只认**已存在**的文件。
* **K31**（M5 新增）**manifest 对账面与确定性**：逐 episode `episode_id`/`status`/`reason`/`red_exit`/
  `green_exit`；`pending_facts` 空表；门槛自基准 manifest 下发（定位不到就空表 + 空来源）；
  生成时间取日志 `end_ts`；manifest 只经 `serialize` 落盘且不含骨架源码。
* 基准作用域分工：完整性门（台账/在位/覆盖度）看全量语料；判定门（检出率/误报）看 `--only` 选中项。

---

## 四、本机环境坑（只写实测过的）

| 坑 | 处置 |
|---|---|
| **用脚本改代码/文档风险高**（转义层吃反斜杠 + 脚本写文件在 Windows 会变 CRLF） | 改**代码**用编辑工具逐处改；脚本回写文档必须 `open(..., newline=chr(10))`，改完立刻跑 `py -X utf8 tools/fix_eol.py` |
| **`Path.write_text(newline=...)` 是 3.10+ 参数** | 3.8 通道一律 `open(..., newline=chr(10))`；`tests/` 里多个测试文件有 `write_text` 小工具可复用 |
| **`Path.is_file()` 在 3.8 遇非法文件名会抛 `OSError`** | 包一层 `try/except (OSError, ValueError)`（`reconstruct._is_file` / `generate._is_file` / `verify._is_file` 都是这么来的） |
| Bash 里 `\r`/`\n` 转义会被工具层吃掉一层；**Python 字符串里的 `/tmp/...` 不会被 Git Bash 翻译** | 判断行尾用 Python 读字节；临时目录用 `$TEMP` 展开出的真实 Windows 路径，别在 Python 字面量里写 `/tmp` |
| 真实日志大体量（Claude 125 文件 / Codex 831 MB 量级） | 只读探针逐行 + 早停；**单行上限 16 MiB**；探针落在仓库树外、只打印聚合读数；**按体积取最大若干份**才有 episode（体积升序的前 40 份实测 0 episode） |
| 两个解释器通道 | `py -3.8` 与 `py -3.12` 都要跑；3.8 是底线（禁 3.9+ 语法与参数） |
| 本机 venv `.venv-check/`（3.12） | 安装面复核：`pip install --no-build-isolation --no-deps .`（**venv 里要先有 `setuptools`**：pip 25 不再自带，用镜像装）；跑完记得 `-e .` 装回 editable |
| 冒烟要跑需要仓库的命令（`verify` / `run`） | `tools/ci_smoke.py` 的 `%TMP_SEED:<仓库内相对目录>%`：现开临时目录 + 把该目录拷进去当目标仓库——**冒烟不许往仓库里写文件** |
| 变异验证跑批 | 复用仓库树外的 `%TEMP%/s2r-mutations/mutate_m5.py`（备份→改→**先确认模块可导入**→跑→还原）；"RED" 必须是判据红，不是"改坏了模块导致收集失败" |
| 验证跑子进程 pytest | 显式传 `env`（含 `PYTHONUTF8=1`/`PYTHONDONTWRITEBYTECODE=1`）、`cwd=副本`、`encoding="utf-8"`；**口径文件里 `pytest_command` 的 `{python}` 占位解析成 `sys.executable`**（裸 `python` 在本机是 WindowsApps 桩，会返回非 0） |
| 干净 clone 复核 | `git clone --no-local <repo> <树外临时目录>`；**未提交的改动不在 clone 里**——先在本地提交 M5，再做这一步 |

---

## 五、DoD 逐项 checklist（M6）

- [ ] `scripts/check_release.py` 落地并**无 FAIL**（六项检查逐条列出结论）
- [ ] `plan/RELEASE-M6.md` 写好（**在 tag 之前**）并进入 tag 树
- [ ] `git tag -a`（annotated；tagger 邮箱为 noreply）
- [ ] **GitHub Release 附产物**：手工上传源码 zip；本地留同名副本 + sha256 留档；**不以 zipball 替代**
- [ ] 发布物本体复核：真下载 + 中立目录解压 + `s2r selftest` / `config path` / 一条 `run` 演示；附件与 tag 树 sha256 同源
- [ ] 隐私四层审计逐层留档（源码 import / 发布产物扫描 / socket 拦截动态 / 加载面静态）
- [ ] 提交面脱敏：作者/提交者/tag tagger 邮箱零真实值；入库文本零本机路径形态
- [ ] README / topic / description 与定位一致；README 顶部状态行改为"已发布"并给链接
- [ ] `plan/00` §一/§三/§七、`plan/06` M6 段与状态表、`plan/07` §一第 6 行回写
- [ ] tag 后事实（附件 sha256 / 下载复核 / Release URL）作为**追加提交**分栏记录
- [ ] **本对话内写完 `plan/HANDOFF-M7.md`（若还有下一棒）或在本文件尾部记明"旅程结束"** → commit

---

## 六、关键命令速查（下一棒开工就用这些）

```bash
# 环境
py -0p                                             # 解释器通道（3.8 / 3.12）
python -m pytest -q                                # 全量测试（3.8 与 3.12 各跑一遍；本机用 py -3.x -X utf8 -m pytest）

# 全链路演示（目标仓库 = 语料里那份"编辑前的工作区"的拷贝）
cp -r data/bench/repo/multi-bug-1/. <临时目录>/
s2r run --target <临时目录> --claude-root data/bench/claude --codex-root data/bench/codex
s2r verify "data/bench/claude/structured-patch-1.jsonl#4" --repo <临时目录>
s2r bench && python tools/gen_bench.py --check

# 门禁
PATH="$PWD/.venv-check/Scripts:$PATH" .venv-check/Scripts/python.exe tools/ci_smoke.py
python tools/fix_eol.py --check
python scripts/check_release.py                    # 发布前一把过；发布态加 --online

# 变异验证跑批（仓库树外）
py -3.8 -X utf8 "$TEMP/s2r-mutations/mutate_m5.py"      # M1–M5 的 19 项
py -3.8 -X utf8 "$TEMP/s2r-mutations/mutate_m6.py" "$PWD"   # M6 发布门 6 组

# 断网探针（仓库树外；socket 桩）
.venv-check/Scripts/python.exe "$TEMP/s2r-mutations/offline_run.py" <目标仓库> "$PWD/data/bench"
# 隐私四层审计（仓库树外；可选第二参数 = 产物解包树）
.venv-check/Scripts/python.exe "$TEMP/s2r-mutations/privacy_audit_m6.py" "$PWD" [<解包树>]

# 发布（**先向用户确认**）
gh repo create yuluo554/session-to-regression --private --source . --remote origin
git push -u origin main
git tag -a v0.1.0 -m "session-to-regression v0.1.0"
git archive --format=tar --prefix=session-to-regression-0.1.0/ v0.1.0 | gzip -9n > session-to-regression-0.1.0.tar.gz
gh release create v0.1.0 <tarball> SHA256SUMS --notes-file plan/RELEASE-M6.md --verify-tag
```

---

## 七、旅程结束（M6 收官声明 · 2026-10-18）

**M6（发布）已完成，本系列到此收官——不存在 `HANDOFF-M7`。**

DoD 逐项落地与证据（**全部已发生**，不是计划）：

| DoD 项 | 落地事实与证据 |
|---|---|
| `scripts/check_release.py` 无 FAIL | 离线 **0 FAIL / 6 PASS / 4 SKIP**（网络项离线报 SKIP）；离线检查项**常驻测试**（`tests/test_check_release.py` 4 项）；发布门变异 **6/6 红、6/6 还原回绿** |
| `plan/RELEASE-M6.md` 在 tag 之前写好并进 tag 树 | 提交 `e8b92a7`（tag 前）落盘；§二–§三 在 tag 树里已是实测值 |
| `git tag -a`（annotated，tagger 邮箱 noreply） | `v0.1.0` = tag 对象 `b1cae60aa2d25fbd969ab84ff84d1205e4a34581`，指向 `93e2aae`；本地核 tagger 邮箱为 noreply 形态 |
| Release 附产物（手工源码包，**非 zipball**） | `session-to-regression-0.1.0.tar.gz`（311564 B，sha256 `f7dec59e…c43760c`）+ `SHA256SUMS`；两次独立构建逐字节一致；`isDraft=false` |
| 发布物本体复核（真下载 + 中立目录） | 下载件 `sha256sum -c` OK、与本留档件 `cmp` 一致、解包树 vs `git archive v0.1.0` `diff -r` **零差异**、`selftest`/`bench`/`run` 全通（[RELEASE-M6.md](RELEASE-M6.md) §4.2） |
| 隐私四层审计逐层留档 | ① 19 文件 0 命中 / ② 产物树调用面 0、`src/` URL 0 / ③ socket 拦截全链路 exit 0、阻断 0 / ④ 加载面本项目 0 命中（[RELEASE-M6.md](RELEASE-M6.md) §三） |
| 提交面脱敏 | 全部提交 `%ae`/`%ce` 均为 noreply；入库文本零本机路径形态（`tools/path_forms.py` 单点判据） |
| README 状态行转正 + 链接；topic / description 与定位一致 | README 头部"v0.1.0 已发布"+ Release 链接 + 下载即用节；`gh repo view` 回读 `visibility=PUBLIC`、topics 六项 |
| 文档回写 00 §一/§三/§五/§七、06 M6 段与状态表、07 §一第 6 行与 D6/决策表、verify/README §M6 | 各文件对应章节（含 K32 与 M6 决策行） |
| tag 后事实作为**追加提交**分栏记录 | [RELEASE-M6.md](RELEASE-M6.md) §四（附件 sha256 / tag 对象 / 真下载复核 / CI 对账 / 可见性与匿名复核） |
| 本对话内写完下一棒 HANDOFF 或记明"旅程结束" | **本文件尾部即收官声明**（M6 是最后一棒） |

**CI 事实（本轮首次接远端）**：push `main` run `39083499234` ✅、push `v0.1.0` run `39083675618` ✅，
各 4 腿（`{ubuntu-22.04, ubuntu-latest, windows-latest, windows-latest} × {3.8, 3.12}`）
**首推即全绿、零诊断轮**。按纪律：M6 之前的所有"本地等价校验，未接远端，CI 待首推验证"措辞
**保留原样不改写**，真实 run 结论只在这里与 [RELEASE-M6.md](RELEASE-M6.md) §4.3 追加。

**诚实的未完成项（⬜ + 原因，不预填）**：① 匿名裸 https `git clone` 与 Release 页 HTTP 码——
本机 `github.com:443` 连续超时（`api.github.com` / `codeload.github.com` 均 200），改由同样无凭据的
另三条通道取证；② `--online` 的 Linux 腿行为只有 CI 可覆盖（本机无该环境）。

**给将来接手者的最短入场**：读 [`plan/RELEASE-M6.md`](RELEASE-M6.md)（发布留档兼完结档）→
`plan/06-里程碑.md` 状态表 → `plan/verify/README.md` §M6 → 跑 `python scripts/check_release.py --online`。
本项目**不依赖 git 还原 diff**，任何实现若引入 git 必需依赖即为偏离题面；对外动作（改可见性 /
删仓 / 历史重写）一律先经用户确认。
