# RELEASE-v0.1.1（v0.1.1 发布留档）

> **本文件的时点**：在 tag `v0.1.1` **之前**写好，并**进了 tag 树**（是 Release 正文的来源）。
> 与 v0.1.0 的留档（[RELEASE-M6.md](RELEASE-M6.md)，原样保留、只作归档）的**唯一结构性差异**：
>
> **digest 的登记处从"留档 §四"移到不进载荷的伴随清单 `SHA256SUMS`。**
> 理由是不动点型自指：附件由 tag 树构建，而留档**在 tag 树里**（即在载荷里），
> 把载荷自身的 sha256 写进载荷里就改变了它——无论多早开始算都放不进去。
> 所以本文件只留**"登记处是谁"**（机读键 `digest_registry`）与**不变量**，
> **数值一律落在 `SHA256SUMS`**；发布门在发布态强制这条口径（留档内出现 digest 行 → FAIL）。
> 这样做的收益：`git rev-list --count v0.1.1..HEAD` 可以是 **0**（tag 与当前树同源），
> 而 v0.1.0 那种"tag 树带占位、digest 靠 tag 后追加提交补齐"的漂移不再必要。

```yaml
# release-facts
tag: v0.1.1
repo: yuluo554/session-to-regression
artifact: session-to-regression-0.1.1.tar.gz
digest_registry: SHA256SUMS
```

---

## 一、发布物与版本口径

**三件套**

| # | 件 | 落点 |
|---|---|---|
| ① | annotated tag | `v0.1.1`（tagger 邮箱 = GitHub noreply） |
| ② | Release 附产物 | `session-to-regression-0.1.1.tar.gz`（手工源码包）+ 伴随清单 `SHA256SUMS` |
| ③ | 发布留档 | 本文件（**tag 之前**落盘并进 tag 树） |

**版本四处一致**（由 `scripts/check_release.py` 的 `version` 检查常驻守）

| 处 | 值 |
|---|---|
| tag | `v0.1.1` |
| `pyproject.toml` | 动态版本 → `s2r.__version__`（`[tool.setuptools.dynamic]`，无静态 version） |
| `src/s2r/__init__.py` | `__version__ = "0.1.1"`（`s2r --version` 打印它） |
| `README.md` 状态行 | "**v0.1.1 已发布**" + Release 链接 `releases/tag/v0.1.1` |

**附件形态与"可复算"**：源码包由

    git archive --format=tar --prefix=session-to-regression-0.1.1/ v0.1.1 | gzip -9n > session-to-regression-0.1.1.tar.gz

生成。确定性来源两条：**`git archive` 取的是 commit 时间戳**（不是打包时刻）、
**`gzip -n` 不写文件名与时间戳** → 同一个 tag 重复构建**逐字节一致**。
**明确不承诺**的是 GitHub 页面自动生成的 "Source code" 包：它的字节由 GitHub 控制、
本地算不出 sha256，因此**不作校验对象**——Release 上手工上传的那一个才是。
附件含 `data/bench/**`（合成语料 + 真值 + 磁盘基线夹具）：`bench --check` 能复算，
不含的话"下载解压即用"的基准复核失真。

**提交面脱敏（发布前置）**

| 面 | 事实 |
|---|---|
| 作者/提交者邮箱 | 全部提交的 `%ae` / `%ce` 均为 GitHub noreply 形态；本机 `git config --local user.email` 同值 |
| tag tagger 邮箱 | annotated tag 的 tagger 邮箱同为 noreply 形态（**本地核**；CI 的检出形态会把标注 tag 对象改写掉，那里看到的空值**不是**"变干净了"的证据） |
| 入库文本 | 零本机盘符/家目录形态路径（判据单点在 `tools/path_forms.py`，`tests/test_privacy_gate.py` 与发布门两处消费） |

---

## 二、发布前本地门禁（本机实跑，逐条记退出码）

> 时点 = 2026-10-20；机器 = Windows，**解释器下限已抬到 3.12**（DEC1=C，见 §四）。

| # | 命令 | 退出码 | 语义判定 |
|---|---|---|---|
| 1 | `py -3.12 -m pytest -q` | 0 | ✅ 符合：**355 项通过**（v0.1.0 时 353；本轮新增 gitless 形态回归 1 项 + 构建元数据守卫 1 项） |
| 2 | `tools/fix_eol.py --check` | 0 | ✅ 符合：**105 个文件 0 问题**（新增 `MANIFEST.in` 与本文件；交接目录 `update/` 按 DEC4=A 不入扫描面） |
| 3 | `tools/gen_bench.py --check` | 0 | ✅ 符合：语料/真值/磁盘夹具再生成**逐字节一致**（10 样本，`rng_draws=5`） |
| 4 | `tools/ci_smoke.py`（`.venv-check`，`s2r` 在 PATH） | 0 | ✅ 符合：**20/20**（`--version`/`selftest`/`config`×2 + 六条业务命令，bare + module 两形态） |
| 5 | `s2r bench` | 0 | ✅ 符合：`ok`（检出 **10/10**、误报 0） |
| 6 | `s2r bench --probe <九个探活>` | 0 ×9 | ✅ 符合：九个注入缺陷**全部被检出**（探活模式 0 = 缺陷被检出） |
| 7 | `scripts/check_release.py`（离线） | 0 | ✅ 符合：**0 FAIL / 1 WARN**——WARN 是 `tag_freshness` 在**未打 tag 时**的本来语义（tag 落后 HEAD 就该提示）；打 tag 后转 PASS（见 §四） |
| 8 | **无 `.git` 的发布物形态**：`pytest -q`（干净 venv，editable 安装） | 0 | ✅ 符合：**355 项通过**（v0.1.0 时 1 failed / 352 passed——D1 的现场） |
| 9 | 同上的发布物形态：`scripts/check_release.py --online` | 0 | ✅ 符合：**0 FAIL**（`worktree` / `tag_freshness` 在无 `.git` 树按设计报 SKIP，见 §四） |
| 10 | `python -m build --wheel --sdist` | 0 | ✅ 符合：**0 条 WARNING**（v0.1.0 时 4 + 8 条弃用告警）；wheel 元数据 `License-Expression: MIT` / `Requires-Python: >=3.12` |
| 11 | sdist 解压树里 `pytest -q`（干净 venv） | 0 | ✅ 符合：**355 项通过**（sdist 自足：`tools/` / `scripts/` / `config/` / `data/bench/` / `.gitignore` 都进包） |
| 12 | 非 editable 安装（wheel）+ **中立 CWD**：`s2r --version` / `config path` | 0 | ✅ 符合：口径文件解析到安装面内的随包副本 `s2r/resources/scoring.toml` |

**修正过的两处"发布物形态"缺陷（v0.1.0 的实际故障）**：

* **D1**：`tests/test_check_release.py` 把离线 SKIP 数**硬编码**成"恰好等于网络类检查数（4）"，
  而 gitless 树按设计多出 `worktree` / `tag_freshness` 两条 SKIP → 附件里 `pytest` 得 `assert 5 == 4`。
  修法 = 换成**具名集合**不变量（四个网络类必 SKIP；除它们外**只允许**已登记的 gitless 降级项，
  且新增 `tag_freshness` 与它同批登记），并补一条**在无 `.git` 副本里跑门**的回归测试。
  这不是"把 `==` 改宽"：多出一个未登记的 SKIP 项仍然红。
* **D2**：依据 tag 对象 sha（而非**剥壳后的提交**）去查 `actions/runs?head_sha=` → 必然查不到 run，
  把"没扫到"伪装成"没跑过"。v0.1.0 之后才修好，本轮让它真正到达下载到包的人。

---

## 三、隐私四层审计（**未重跑**：本轮没有触碰出网面）

* 四层读数（源码 import 面 / 发布产物扫描 / socket 拦截动态 / 加载面静态）的**实测留档**在
  [RELEASE-M6.md](RELEASE-M6.md) §三（v0.1.0 时点，原样保留）。
* 本轮**未重跑**该四层审计——如实说明，不把它写成"已复核"：本轮 diff 面是
  `tests/` + `tools/`（EOL 门的跳过名单）+ `scripts/`（发布门）+ 打包元数据 + CI + 文档，
  **`src/s2r/**` 只有一行版本号改动**，没有新增 import、没有新增 URL 字面量、没有新增子进程出网通道。
* 常驻的**零网络守门仍然在跑**：`tests/test_privacy_gate.py`（AST 禁网络模块顶层导入 +
  扫描面自证 ≥15 文件 + 阳性/阴性对照）随全量测试通过（§二 第 1 条）。

---

## 四、tag 后追加事实（本节只放**不变量**，数值在 `SHA256SUMS`）

> v0.1.0 的留档把 sha256 写在下面这一节里，代价是"tag 树带占位、digest 靠 tag 后追加提交补齐"，
> 于是 tag 与 HEAD 必然漂移、附件里的发布门必然报"未登记"。v0.1.1 把登记处移出载荷后，
> 这一节不再承载"必须等事实发生才能填"的数值；**数值的唯一登记处 = 伴随清单 `SHA256SUMS`**。

* **登记处**：`SHA256SUMS`（`sha256sum` 标准文法 `<hex>  <name>`，`sha256sum -c` 直接可吃）。
* **不变量（可自查，不追平绝对数字）**：
  1. `git rev-list --count v0.1.1..HEAD` == **0**（tag 与当前树同源；附件 == `git archive v0.1.1` 解压树，`diff -r` 无差异）；
  2. 真下载的附件 sha256 == `SHA256SUMS` 里的值 == GitHub API 自报 `assets[].digest`（**三方对撞**）；
  3. 一条 push / tag 事件恰好一条 CI run，且该 run **逐 job success**；
  4. 发布门 `scripts/check_release.py --online` 在**仓库树**与**无 `.git` 的发布物形态**里都报 **0 FAIL**。
* **两类检查在"无 `.git` 树"里的登记语义**（发布门自己就这么写的，测试同批登记）：
  `worktree`（无工作区可查）与 `tag_freshness`（无 tag 可查）→ **SKIP**，
  理由文案"源码包解压树里本就不适用"。**SKIP 不是 PASS**：这两项在仓库树里必须给出 PASS/WARN。
* **本版不重打 v0.1.0**：已发布物不可变（附件已有下载记录）。v0.1.0 的 tag 与其附件**原样保留**，
  其留档 [RELEASE-M6.md](RELEASE-M6.md) 也**一字未改**（归档）。

---

## 五、已知限制与口径（不构成已验证声明）

* **发布形态 = 源码包 + editable 安装**：本版本仍**不上 PyPI**（DEC3=A：只补 `MANIFEST.in` 让 sdist 自足）。
  sdist 已实测自足（§二 第 11 条）；wheel 形态下仓库根的 `data/bench/**` 不在安装面内——
  这是**登记过的**行为：非 editable 安装下 `s2r bench` 需要显式 `--data-dir`（README 已写）。
* **解释器下限 3.12**（DEC1=C）：3.8 于 2024-10-07 EOL、3.9 于 2025-10-31、3.10 于 2026-10-01 EOL
  （endoflife.date，2026-10-20 取）。CI 因此收敛为 `{ubuntu-latest, windows-latest} × 3.12` 两腿，
  并撤掉 `ubuntu-22.04` 那个"为 3.8 钉旧镜像"的钉子。**3.8 腿在被撤之前是绿的**——
  这是一次口径选择，不是修 bug。
* **本机可验的边界**：`ubuntu` 腿只有 CI 原生运行的证据；本机只做等价校验（355 项、九探活、
  `gen_bench --check`、离线冒烟 20/20、干净 venv 装 wheel/sdist、无 `.git` 树跑全量）。
  凡"未实际执行"的项一律不写成 ✅。
* **能力边界**与 README「首期范围与已知限制」同源，此处不复制口径。
* **"零上传"的范围**即 §三 所述四层（含"本轮未重跑"这条如实声明）。
