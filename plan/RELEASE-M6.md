# RELEASE-M6（v0.1.0 发布留档）

> **本文件的时点**：在 tag `v0.1.0` **之前**写好，并**进了 tag 树**（M6 三件套之③）。
> 所以这一版里 §四「tag 后追加事实」只有占位——**tag 之后才成立的事实**（附件 sha256、tag 对象
> sha、真下载复核、CI 逐 run 对账）不可能出现在 tag 树里；其中**附件 sha256 是"不动点"型自指**：
> 产物由 tag 树构建，把 digest 写进那棵树就改变了它（无论多早开始算都放不进去）。
> 仓库 `main` 上的同名文件是**完整版**（§四 由 tag 之后的追加提交补齐）。
> **纪律**：不预填尚未发生的事实；tag 之后的补充一律走**追加提交**，不改写本节以上内容。

```yaml
# release-facts
tag: v0.1.0
repo: yuluo554/session-to-regression
artifact: session-to-regression-0.1.0.tar.gz
```

---

## 一、发布物与版本口径

**三件套**

| # | 件 | 落点 |
|---|---|---|
| ① | annotated tag | `v0.1.0`（tagger 邮箱 = GitHub noreply） |
| ② | Release 附产物 | `session-to-regression-0.1.0.tar.gz`（手工源码包）+ 伴随清单 `SHA256SUMS` |
| ③ | 发布留档 | 本文件（**tag 之前**落盘并进 tag 树；tag 后事实走追加提交） |

**版本四处一致**（由 `scripts/check_release.py` 的 `version` 检查常驻守）

| 处 | 值 |
|---|---|
| tag | `v0.1.0` |
| `pyproject.toml` | 动态版本 → `s2r.__version__`（`[tool.setuptools.dynamic]`，无静态 version） |
| `src/s2r/__init__.py` | `__version__ = "0.1.0"`（`s2r --version` 打印它） |
| `README.md` 状态行 | "**v0.1.0 已发布**" + Release 链接 |

**附件形态与"可复算"**：源码包由

    git archive --format=tar --prefix=session-to-regression-0.1.0/ <tag> | gzip -9n > session-to-regression-0.1.0.tar.gz

生成。两条确定性来源必须写清，否则下一棒会以为"随便打个包也行"：
**`git archive` 取的是 commit 时间戳**（不是打包时刻）、**`gzip -n` 不写文件名与时间戳** → 同一个 tag
重复构建**逐字节一致**（实测两次构建 sha256 相同，见 §四）。**明确不承诺**的是 GitHub 页面自动生成的
"Source code" 包：它的字节由 GitHub 控制、本地算不出 sha256，因此**不作校验对象**——Release 上手工上传的
那一个才是。
**附件含 `data/bench/**`**（合成语料 + 真值 + 磁盘基线夹具，用户拍板）：它是交付物的一部分（`bench --check`
能复算），不含的话"下载解压即用"的基准复核失真。体积与 sha256 见 §四。

**提交面脱敏（发布前置，四步之外单独核）**

| 面 | 事实 |
|---|---|
| 作者/提交者邮箱 | 全部提交的 `%ae` / `%ce` 均为 GitHub noreply 形态（`<id>+<user>@users.noreply.github.com`）；本机 `git config --local user.email` 同值 |
| tag tagger 邮箱 | annotated tag 的 tagger 邮箱同为 noreply 形态。**本地核**（`git for-each-ref --format='%(taggername)|%(taggeremail)' refs/tags`）：CI 的检出形态会把标注 tag 对象改写掉，那里看到的空值 **不是**"tag 变干净了"的证据 |
| 入库文本 | 零本机盘符/家目录形态路径（判据单点在 `tools/path_forms.py`，`tests/test_privacy_gate.py` 与发布门两处消费） |

---

## 二、发布前本地门禁（本机实跑，逐条记退出码）

| # | 命令 | 退出码 | 语义判定 |
|---|---|---|---|
| 1 | `py -3.8 -X utf8 -m pytest -q` | 0 | ✅ 符合：**353 项通过**（M5 时 348；新增发布门 4 项 + 隐私门扫描面自证 1 项） |
| 2 | `py -3.12 -X utf8 -m pytest -q` | 0 | ✅ 符合：**353 项通过**（同收集数） |
| 3 | `py -3.8/-3.12 -X utf8 tools/fix_eol.py --check` | 0 | ✅ 符合：**103 个文件 0 问题**（两解释器各跑一遍） |
| 4 | `py -3.8 -X utf8 tools/gen_bench.py --check` | 0 | ✅ 符合：语料/真值/磁盘夹具再生成**逐字节一致**（10 样本，rng_draws=5） |
| 5 | `tools/ci_smoke.py`（`.venv-check` 环境，`s2r` 已在 PATH） | 0 | ✅ 符合：**20/20**（`--version`/`selftest`/`config`×2 + 六条业务命令，bare + module 两形态） |
| 6 | `s2r bench` | 0 | ✅ 符合：`ok`（检出 **10/10**、误报 0）；`pending_facts` 为空表 |
| 7 | `s2r bench --probe <九个探活>` | 0 ×9 | ✅ 符合：九个注入缺陷**全部被检出**（探活模式 0 = 缺陷被检出） |
| 8 | `python scripts/check_release.py`（离线） | 0 | ✅ 符合：**6 项检查全 PASS**、4 项网络类按设计报 **SKIP**（离线不冒充 PASS） |
| 9 | 隐私四层审计探针（`privacy_audit_m6.py`，仓库树外） | 0 | ✅ 符合：四层读数见 §三 |

### 2.1 发布门（`scripts/check_release.py`）的形态与边界

* **离线 6 项**：`version`（四处一致）/ `readme`（extras·命令·退出码·许可·包布局·运行期数据六面 + 附件名
  双向）/ `ledger`（27 份冻结件 sha256 逐份一致）/ `paths`（82 个入库文本 0 处本机路径形态）/
  `worktree`（干净、无未跟踪产物）/ `release_doc`（留档在位、机读块可解析、五个小节成**标题**存在）。
* **`--online` 追加**（走 `gh`）：仓库存在与可见性 / tag 对象形态（annotated）与提交 / 该提交 CI
  **逐 job** success / Release 非 draft 且附件集合 == 载荷 + `SHA256SUMS` / 逐个附件**下载回来重算 sha256** /
  伴随清单恰好登记同一批载荷。发布态的结论按事实登记在 §四。
* **刻意不进 CI 的是"发布终态"那一层**：它要求"事实已经发生"，发布之前必然红；挂进 CI 只会让 tag 之前的
  正常提交永远红、红得没有信息量。判别 = **"这道门在当前时点本来就该红吗？"** 该红 → 只能收尾时手工跑。
  离线那 6 项则**常驻测试**（`tests/test_check_release.py`）。
* **门自己也是代码**，三条运行时形态各有一条测试：① 结果清单是模块级的 → 入口 `clear()`
  （否则同进程连调两次"越跑越绿"）；② 离线网络项报 SKIP 且与 PASS **分离计数**；③ 检查项名成集合、
  逐项恰好一条结论。另有 **6 组变异验证**（见 [verify/README.md](verify/README.md) §M6.2）：
  每组的"检查项数"与"变异组数"逐条对账。

---

## 三、隐私四层审计（"纯本地、零上传"的审计范围与措辞对齐）

四层读数都由仓库树外的一次性探针（`%TEMP%/s2r-mutations/privacy_audit_m6.py`）实算；**逐层留档**，
不用"只扫了本项目源码"去支撑"运行期零网络调用"这种更强的措辞。

| 层 | 做什么 | 本轮读数 |
|---|---|---|
| ① 源码 import 面 | `src/**/*.py` 的 AST 导入根 ∩ 网络能力模块表 | **19 个文件，0 处命中** |
| ② 发布产物扫描 | 解包树 / 仓库树的 `.py` 禁用导入 + 调用面模式 + URL 字面量（**判据按位置分流**：`src/` 里的 URL 是"出网目标"=硬；测试夹具/文档出处只报不阻断） | **55 个 .py / 77 个文本文件；禁用导入 0、调用面 0、`src/` 内 URL 0**；advisory 4 条（TOML 解析器的非法值夹具 `example.invalid`、HN 出处 ×2、HANDOFF 里的镜像地址） |
| ③ socket 拦截动态 | 把 `socket.socket` / `create_connection` / `getaddrinfo` 换成计数+抛异常的桩，再跑**全链路** `run`（含隔离副本里的两次 pytest） | **exit 0；阻断尝试 0 次**（9 会话 → 8 条结论，写出 2 个测试 + manifest） |
| ④ 加载面静态 | ③ 之后导出 `sys.modules`，按"本项目 / 标准库 / 第三方"分类逐文件扫 | 加载模块文件：本项目 18 / 标准库 85 / 第三方 7；**本项目文件 0 处网络模式**；唯一一条落在标准库 `socket.py` 自身（它定义 `getaddrinfo`） |

**结论与措辞**：四层全过 → 保留"**纯本地、零上传**"的措辞，并在此写明**审计范围**即上表四层。
第 ④ 层的"标准库/第三方命中"如实登记——那是**加载面能力**（标准库自带网络栈），不是**调用面行为**
（③ 实测 0 次出网）；两者不是一件事，不混写。

---

## 四、tag 后追加事实

> 本节在 tag 树里是**占位**（口径要求"不许预填还没发生的事实"）。以下四类事实只能在 tag 之后成立：
> **tag 对象 sha / tag push 那条 CI run / 附件 sha256 / 真下载复核结论**。tag 之后的追加提交把本节填满，
> 并同步 `gh release edit --notes-file` 保持 Release 正文与文件一致。
> 另需注明一处**设计差异**：README 顶部的发布状态行与 Release 链接**在 tag 树里就已写入**——链接由
> tag 名决定（`releases/tag/v0.1.0`），不随树内容变化，故它不是"未发生的事实"；而**附件 sha256 会随树
> 内容变**（不动点），只能登记在这里。tag 树里的 DoD 复选框同理：只能在本节补齐时才能打勾。

* 附件（`session-to-regression-0.1.0.tar.gz`）：sha256 ⬜ 待登记、字节数 ⬜ 待登记（两次独立构建一致性 ⬜ 待登记）
* 伴随清单 `SHA256SUMS`：内容与载荷集合一致性 ⬜ 待登记
* annotated tag `v0.1.0`：对象 sha ⬜ 待登记、tagger 邮箱 ⬜ 待登记（本地核）
* 真下载复核：`gh release download` → `sha256sum -c` → **中立目录**解压 → `s2r selftest` /
  `s2r config path` / 一条 `run` 演示；附件解包树 vs `git archive <tag>` 解包树 `diff -r` ⬜ 待登记
* CI 对账（**每个 push / tag 事件恰一 run、head_sha 全对上**；tag run 也必须绿）⬜ 待登记
* 可见性：先 `--private` 跑 CI，绿后切 `public`；匿名无凭据复核（裸 https clone + 裸 curl 下载件
  sha256 比对 + Release 页 200）⬜ 待登记

---

## 五、已知限制与口径（不构成已验证声明）

* **发布形态 = 源码包 + editable 安装**：本版本**不出 wheel/sdist、不上 PyPI**。仓库根的数据（`data/bench/**`、
  `config/`）随源码包走；wheel 形态下这些不在安装面内，**未验证**，不在本版承诺范围（显式跳过，
  不是"待办"）。运行期真正需要随包的那一份（`s2r/resources/scoring.toml`）已在安装面内并由门守着。
* **本机可验的边界**：`ubuntu` 腿只有 CI 原生运行的证据；本机只做等价校验（双解释器 353 项、九探活、
  `gen_bench --check`、离线冒烟、干净 clone 复核）。凡"未实际执行"的项一律 ⬜ + 原因，不预填 ✅。
* **能力边界**（与 README「首期范围与已知限制」同源，此处不复制口径）：只固化已发生的修复、首期只生成
  Python 测试、生成的骨架是**候选**（推不出活动断言时如实标 `needs_manual`）、不依赖 git、
  样本量诚实声明（严格口径真实端到端样本仅 5 个会话）。
* **"零上传"的范围**即 §三 的四层；超出这四层的陈述（如"证明任何第三方依赖都不会出网"）不作承诺。
* **本文件与 README 的哈希片段**：README 只给真实产物名 + 校验命令，**不给 sha256 字面值**——理由见
  卷首与 §四（不动点型自指）；哈希值以本文件 §四 为唯一登记处，配套 `SHA256SUMS` 附件可机读复核。
