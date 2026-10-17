# session-to-regression（`s2r`）

**从 agent 会话日志里切出「用户报 bug → 出现失败信号 → 改文件修好」的 episode，把修复 diff 反推成可复跑的 pytest 骨架，并做 RED/GREEN 双验证——让一次修复固化成测试。**

纯本地 Python CLI：不调用任何模型、不联网、无遥测、不依赖 git。修改前的内容就在日志里
（`structuredPatch` / `originalFile` / `old_string` / `apply_patch`），因此八成以上不在 git 仓库里的
会话同样能处理。

> 痛点来自公开抱怨：coding agent 常常「让测试变绿」而不是「让代码变对」，甚至把上周的修复改回去
> （HN item 49981230，2026-10-06）。修复是一次性的，回归是永久的——本工具把每一次修复钉成回归测试。

**状态（2026-10-17）**：**M0 骨架**已落地——计划文档（`plan/00`–`07`）、CLI 契约与退出码、
唯一数值口径文件、确定性写盘单点、事件流 IR、双解释器测试与 CI 冒烟。**M1–M5 尚未实现**：
`scan` / `bench` / `reconstruct` / `generate` / `verify` / `run` 现在只报 `stage_not_ready` 并退出 4，
**不产出任何结果**（也不伪造空结果）。未交付能力不会出现在本文档的"可用命令"里。

---

## 安装

```bash
git clone <repo-url> session-to-regression
cd session-to-regression
pip install -e ".[dev]"     # dev 是唯一 extras（pytest），CI 与本文档使用同一集合
```

要求 Python ≥ 3.8，运行期**零第三方依赖**（TOML 口径文件由内置的严格子集读取器解析）。

## 三分钟演示（当前可用）

```bash
s2r --version              # 版本行
s2r selftest               # 本机环境与骨架契约自检（坏配置必红）
s2r config path            # 定位唯一数值口径文件
s2r config show            # 打印全部口径值（人读）
s2r config show --json     # 机读 JSON，可直接喂给报告/基准
```

`selftest` 是可证伪的：把 `--config` 指向一个写坏的 TOML 会 **exit 1**；指向不存在的路径会 **exit 2**。

## 退出码语义

| 退出码 | 语义 |
|---|---|
| 0 | 成功；对 `verify` / `run` 表示「至少一条 verified」 |
| 1 | 未达标：无 verified（含全部 unverified）、基准未达标、`selftest` 自检失败 |
| 2 | 用法错误或输入不可用：未知子命令、参数错误、路径不存在、未知日志格式（`unsupported_format`） |
| 3 | 运行依赖缺失（如 pytest 不可用）——**不会**退化成「零条结果 + exit 0」 |
| 4 | 阶段未就绪：该子命令所属模块尚未交付（骨架期专用，随里程碑整项退役） |

判断"跑挂了"与"按契约返回非 0"的区别：以本表为准，非 0 不必然是故障
（例如将来 `run` 检出全部 `unverified` 时返回 1 是既定语义）。

## 配置与"唯一数值口径"

**全项目所有数值只在一处定义**：`config/scoring.toml`（打分权重、过滤阈值、验证超时、临时目录策略、
测试目录与 manifest 落点、语义词表）。README、技术报告、基准输出一律**引用**该文件，
不再写第二份数值表；代码里不内嵌任何口径数值（`tests/test_config_single_source.py` 守门）。

定位顺序：`--config` → 环境变量 `S2R_CONFIG` → 当前目录向上 6 层内的 `config/scoring.toml`
→ 随包副本 `s2r/resources/scoring.toml`（安装后形态）。**读不懂就报错**（exit 2），绝不回落到内置默认值。

## 隐私：纯本地、零上传

* 不联网、无遥测、不调用任何模型；会话日志（含 prompt 与代码）**只在你的机器上被读取**；
* 真实日志、真实路径清单、真实 session id **一律不入仓**；本公开仓库只含**合成基准**（假项目名/假路径/假 id）；
* 生成物只写目标仓库的测试目录与临时副本，**不修改用户源码、不碰 git 索引**。

## 首期范围与已知限制

* **只固化已发生的修复**，不替用户补覆盖率、不凭空设计新功能测试；
* **首期只生成 Python 测试**：`.mjs` / `.ts` 的修复只识别、只进 manifest，不生成测试文件；
* **不依赖 git**：git 只作为可选的回放加速手段，缺 git 时全链路仍可跑；
* **样本量诚实声明**：本机严格口径的真实端到端样本仅 5 个会话（宽松 29 个），
  不足以支撑统计型结论——因此**基准与回归一律使用合成会话 + 程序化植入真值**，
  真实样本只作演示与人工抽验，样本数不作交付承诺；
* **不承诺跨版本兼容**：会话格式随 agent 版本演进，解析器以"本机实测字段 + 版本指纹"为依据，
  不认识的新格式**显式报错**（`unsupported_format`），不猜测解析。

## 开发

```bash
pytest -q                      # 单元与契约测试（双解释器：3.8 与 3.12 都应全绿）
python tools/ci_smoke.py       # 离线 CI 冒烟：逐条跑已交付命令 + 断言退出码语义
```

计划与数据台账见 `plan/`（`00` 总览、`01` 题目、`02` 需求解读、`03` 架构、`04` 模块详设、
`05` 数据计划、`06` 里程碑、`07` 交付对标与决策记录）与 `data/README.md`。

## 许可

MIT，见 [LICENSE](LICENSE)。署名 `yuluo554`。
