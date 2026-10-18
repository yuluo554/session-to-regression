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

**与 HANDOFF-M6 措辞的两处差异（写入留档，免得读者去猜）**：

* HANDOFF 写的是"源码 **zip**"，实际产出是 **`.tar.gz`**（`git archive --format=tar` + `gzip -9n`）。
  理由：这两步的确定性来源简单且已实测（commit 时间戳 + `-n` 不写文件名/时间戳 → 同 tag 重复构建
  **逐字节一致**）；zip 形态要额外固定 `date_time` 与 `external_attr` 才能达到同等可复算性。
  命令语义等价、目标（"手工上传、本地可复算、与 tag 树同源"）不变；
* **仓库内不放该二进制的耐久副本**：它是 Release 附件（永久可下载、带 digest 可复验），
  再进一次 git 只会让仓库多一份 ~300 KB 的二进制、并牵动二进制属性与台账；
  `plan/00-README总览.md` §三 的措辞已同步为"留档登记 + 门可复验"，不再承诺"仓库内耐久副本"。

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

### 2.1 干净 clone 复核（`git clone --no-local`，仓库树外）+ 安装面

本轮改了版本号、动了 `tests/` 与 `tools/`、新增 `scripts/`，所以干净 clone 是"跟踪树本身可跑通"的
唯一证据（逐条表另见 [verify/README.md](verify/README.md) §M6.3）：

| 命令（clone 目录内） | 退出码 | 语义判定 |
|---|---|---|
| `tools/gen_bench.py --check` | 0 | ✅ 再生成**逐字节一致**（10 样本）——`data/bench/**` 的 `-text` 冻结在克隆里仍生效 |
| `s2r bench` | 0 | ✅ `ok`（10/10、误报 0） |
| `pytest -q` | 0 | ✅ **353 项通过**（与作者机器同收集数） |
| `scripts/check_release.py`（离线） | 0 | ✅ **0 FAIL / 6 PASS / 4 SKIP**——发布门在干净 clone 里自足（不依赖已安装的包） |
| `s2r run --target <树外目录> …` | 0 | ✅ 9 会话 → 8 条结论（verified 2 / unverified 1 / needs_manual 5），写出 2 个测试 + manifest；**clone 内 `git status` 干净** |
| `tools/fix_eol.py --check` | 0 | ✅ 103 个文件 0 问题 |
| 非 editable 安装 + **中立 CWD**：`pip install --no-build-isolation --no-deps --target <树外>` → `s2r selftest` / `config path` / `--version` | 0 | ✅ 安装面里有 `s2r/resources/scoring.toml`（安装面第五面）；自检 6 项全 ok、`config path` 指向随包副本、`s2r 0.1.0 (python 3.8.8)` |

### 2.2 发布门（`scripts/check_release.py`）的形态与边界

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
  **6/6 红、6/6 还原后回绿**，每组的"检查项数"与"变异组数"逐条对账——预期变红集合是**显式声明**的
  （改跟踪文件必然连带 `worktree`；留档不在位会连带 `version`/`readme`，三条检查都消费同一份机读
  事实块，属**有意的因果依赖**而非张冠李戴）。

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

> 本节在 tag 树里是**占位**（口径要求"不许预填还没发生的事实"）。tag 之后的追加提交把本节填满；
> **不改写本节以上内容**。tag 树里的 DoD 复选框因此必然是空框——这条差异记在这里，别读成"tag 树漏打勾"。
>
> **两处设计差异，先写清免得读者以为漏了**：
> ① README 顶部的发布状态行与 Release 链接**在 tag 树里就已写入**——链接由 tag 名决定
> （`releases/tag/v0.1.0`），不随树内容变化；而**附件 sha256 会随树内容变**（不动点），只能登记在本节。
> ② **本版不重打 tag** 的理由：本体复核发现 README 少一条"非 editable 安装下 `bench` 需 `--data-dir`"
> 的说明（**文档缺口，不是发布物缺陷**：代码、数据、命令语义都没问题，README 快速开始那条 editable
> 路径实测全通）。按纪律走"追加提交 + 本节登记"——窗口虽未关，但重打 tag 会让**已登记的附件哈希立刻
> 过期**，收益为负。

### 4.1 附件与 tag

* **Release URL**：`https://github.com/yuluo554/session-to-regression/releases/tag/v0.1.0`
  （`isDraft=false`；本文件也是 Release 正文的来源——`gh release create … --notes-file plan/RELEASE-M6.md`，
  正文是 tag 时点的那一版，本节内容不回灌正文）
* 产物：`session-to-regression-0.1.0.tar.gz`，**311564 字节**（含 `data/bench/**`）
* sha256（由 `sha256sum` 输出直接重定向生成，人只写注释行）：

```
sha256: f7dec59e5cb8587d348d2f345cc2d6aa01944e30195075862e6a33407c43760c  session-to-regression-0.1.0.tar.gz
```

* 伴随清单 `SHA256SUMS`（101 字节，标准 `sha256sum` 文法 → `sha256sum -c` 直接可吃；
  其 sha256 `aa987347e4fd40fb6b127d635dee90b33980aac7c3c00699b75232ee96902650`）
* 打包口径：`git archive --format=tar --prefix=session-to-regression-0.1.0/ v0.1.0 | gzip -9n`
  ——**同 tag 两次独立构建 sha256 逐位相同、`cmp` 逐字节一致**；构建件留底在**仓库树外**
* annotated tag `v0.1.0`：对象 sha `b1cae60aa2d25fbd969ab84ff84d1205e4a34581`（`git cat-file -t` = `tag`），
  指向提交 `93e2aae26bbc1ceadc707d1f0c19893d83745c80`；tagger 邮箱为 **noreply 形态**（本地核；
  地址形态见 §一，此处不复述字面值——留档跑自己的扫描器时不该自命中）

### 4.2 发布物本体验证（真下载 + 中立目录）

| # | 步骤 | 结果 |
|---|---|---|
| 1 | `gh release download v0.1.0`（仓库目录内） | ✅ 两件附件（311564 B / 101 B） |
| 2 | `sha256sum -c SHA256SUMS` | ✅ `session-to-regression-0.1.0.tar.gz: OK` |
| 3 | 与本地留档件 `cmp` | ✅ 逐字节一致 |
| 4 | 中立目录解压 → 安装 → `s2r selftest` | ✅ `status: ok`（口径文件解析到**安装面内**的随包副本） |
| 5 | **中立 CWD** 下 `s2r config path` | ✅ 指向 `<安装目标>/s2r/resources/scoring.toml` |
| 6 | 解包树根 `s2r bench`（editable 等价形态） | ✅ `ok`（10/10、误报 0）——`data/bench/**` 随包、`-text` 冻结生效 |
| 7 | 解包树根 `s2r run --target <树外目录>` | ✅ 9 会话 → 8 条结论（verified 2 / unverified 1 / needs_manual 5），写出 2 个测试 + manifest |
| 8 | 附件解包树 vs `git archive v0.1.0` 解包树 `diff -r` | ✅ **零差异**（103 个文件） |
| 9 | 隐私四层审计的层②/③/④按**产物解包树**重跑 | ✅ 禁用导入 0、调用面 0、`src/` 内 URL 0；全链路 exit 0、阻断 0 次 |
| 10 | 非 editable 安装（`pip install --target`）下的 `bench` | ⚠️ 报 `input_unavailable`（按"包外三级"找语料）→ 显式 `--data-dir data/bench` 后 `ok`；**已补进 README 已知限制**（本追加提交），**不重打 tag**（理由见本节开头） |

> 第 8 步的一个操作坑（值得记）：**解压树会被自己的验证动作污染**——第 4/6 步在同一棵解压树里跑过
> `pip install` 之后，树里多出 `build/` 与 `src/*.egg-info/`，于是 `diff -r` 会报"多出两项"。
> 正解 = **另解一份干净的**再比（本项目即如此），别把"验证方法的污染"读成"附件与 tag 树不同源"。

### 4.3 CI 对账（快照 + 不变量）

| 事件 | run id | head_sha | 结论 |
|---|---|---|---|
| push `main` | 39083499234 | 93e2aae | ✅ success（4 腿：ubuntu-22.04/3.8、ubuntu-latest/3.12、windows-latest/3.8、windows-latest/3.12） |
| push `v0.1.0`（tag） | 39083675618 | 93e2aae | ✅ success（同 4 腿） |

**不变量**：一条 push / tag 事件恰好一条 run、`head_sha` 与本地提交逐条对上；**tag 那条也必须绿**
才算发布提交全绿。本表是**截至 `93e2aae` 的快照**——本文件所在的追加提交又是一次 push，按不变量
会再产生一条 run，**不追平总数**（数字追不平，写出不变量就够可审计）。
首推**零诊断轮**：四条腿一次全绿，未动用"诊断 + 修复 + 再推一轮"的预算。

**对账定稿（停止信号）**：此后本表**不再追加绝对数字**。核对方式 = 用不变量自查——
`gh api repos/yuluo554/session-to-regression/actions/runs --jq '.total_count'` 应等于该仓收到的
push/tag 事件数，且逐条 `head_sha` 都能在 `git log origin/main`（及 `refs/tags`）里找到对应提交。
本文件所在的提交本身又是一次 push，再改就会自我循环，**故就此停止**；后续 run 以 Actions 页实查为准。

### 4.4 可见性与匿名复核（用户确认后执行）

* 可见性：`gh repo edit --visibility public --accept-visibility-change-consequences` → 回读
  `visibility=PUBLIC`、`isPrivate=false`（gh 2.93+ 必须带该 flag，否则报 usage error）；
  topics = `cli / local-first / pytest / python / regression-testing / session-log`（加完回读确认）
* **匿名（完全不带凭据）复核**——判据是"换成陌生人来做，结果一样吗"：
  * ① 匿名 `git clone https://github.com/…`：⬜ **未完成** —— 本机 `github.com:443` 连续 3 次
    25s 超时（`curl` 取该 URL `http=000`），同一时刻 `api.github.com` 200、`codeload.github.com` 200
  * ② 匿名 `curl https://api.github.com/repos/…/releases/tags/v0.1.0`（无 token / 无 header）：
    ✅ `isDraft=false`、`tag_name=v0.1.0`、附件名 / 字节数 / digest 与留档**逐项一致**
  * ③ 匿名 API 资产端点（`Accept: application/octet-stream`）下载附件：✅ 311564 B，
    sha256 与定稿值**逐位一致**
  * ④ 匿名 `codeload.github.com` 取 tag 树 tarball → 解包 vs 发布附件解包树 `diff -r`：✅ **零差异**
  * Release 页 HTML（`github.com/…/releases/tag/v0.1.0`）HTTP 码：⬜ **未测**（同 ①，主站 443 本机不可达）
* **措辞纪律**：支撑"公开可访问"的是 ②③④（三条都是无凭据通道）；① 与页面 HTTP 码标 ⬜ 并写清
  原因——**不把"没测到"写成"验过了"**，也不因为两条通道不通就把整段结论改成"不可访问"。

### 4.6 全历史脱敏终验（逐 commit 扫；带非空跑断言 + 阳性对照）

    py -3.8 -X utf8 <树外探针>/history_scan_m6.py <repo>

| 面 | 读数 |
|---|---|
| 可达提交 | **19**（`rev-list --all`，含 tag 可达对象） |
| 扫过的树文件条目 | **1526** |
| 路径形态命中 | **16**，**全部**落在 `data/bench/manifest.json` 的 `privacy.whitelist.path_prefixes` —— 即**合成语料自己声明的那条假盘符前缀**（`data/README.md` §三："假绝对路径只保留通用盘符形态，后缀段为假项目名"） |
| 阳性对照 | 用同一条命令扫一个必然存在的串 → 命中 4，证明扫描通道真的在工作 |
| 提交身份面 | `%ae|%ce|%an|%cn` 全历史**只有一个值**，且是 GitHub noreply 形态 |

**裁决：不重写历史**，三条理由：① 命中串是**声明为假的**值（不是本机真实路径），它是冻结语料
白名单的**内容本体**，重写会打挂 `data/bench/**` 的 sha256 台账；② 该串只出现在 `.json` **数据文件**里，
而 tree 级 `paths` 门的扫描面是**入库文本**（`.py/.toml/.md/.yml/.yaml/.cfg/.txt` +
`.gitignore/.gitattributes/LICENSE`）——**两者扫描面不同是设计，不是漏检**，写在这里免得被误读；
③ 判据自身的阳性对照常驻（`tests/test_privacy_gate.py::test_local_path_pattern_has_a_positive_control`），
"它在被扫的那个面上真能红"有实测。

**"没扫到"与"扫干净了"必须分得开**：上面的 19 / 1526 / 4 三个数就是覆盖面证据——探针内带
`assert len(commits) > 1`，浅克隆会把扫描面缩到 1 个提交，那时的"0 命中"毫无信息量。

### 4.7 tag 之后代码面的差异（发布后每落一笔提交都要重证）

    git diff --name-only v0.1.0..HEAD

本追加提交的面 = `README.md` + `plan/**`（文档面）+ **`scripts/check_release.py` 一处发布期工具修复**。
逐项说清，别让读者自己去猜"代码动没动"：

* **用户面代码零改动**：`git diff --name-only v0.1.0..HEAD -- src/ tests/ tools/ pyproject.toml config/`
  的输出为**空**——`s2r` 的功能、测试、口径文件与 tag 树完全一致；
* **唯一改动的代码是发布期工具**：`scripts/check_release.py` 的 `--online` 原先直接拿
  `git/ref/tags/<tag>` 的 `object.sha` 去查 CI——而 **annotated tag 的该字段是 tag 对象本身、不是提交**
  → 必然查不到 run，把"**没扫到**"伪装成"**没跑过**"（本 skill 反复强调的那类假绿）。修复 = 先经
  `git/tags/<sha>` 剥一层拿到真实提交再查。
  ⚠️ **发布物（tag 树）里那份脚本没有这个修复**：所以**不要**用附件里的 `--online` 判发布态，
  从 `main` 取该脚本（或直接读本节）。这条差异**不重打 tag**——它是发布期工具的缺陷、不在用户面，
  重打会让已登记附件哈希立刻过期，收益为负。

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
