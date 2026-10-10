# ADR：基于卡兹克开源的 AIHOT 重构千里眼

状态：**暂缓**（2026-10-10 用户决定先不接模型、先不跑、先不 fork，要求先弄清 AIHOT 为什么问题这么多，见 [insight-aihot-churn.md](insight-aihot-churn.md)）· 对应 issue [#54](https://github.com/forcegravity1989/aihot/issues/54) · 2026-10-10

## 1. 背景

千里眼从 v0.1 起一直在自制一条「采集 → 过滤 → 评分 → 分方向排版 → 发布」的链路（`core/`、`config/*.yaml`、`templates/`）。2026-10 卡兹克把 AIHOT 全部开源（[KKKKhazix/AIHOT](https://github.com/KKKKhazix/AIHOT)，MIT；许可要求不使用 AIHOT 的名字与 Logo）。上游已经有：

- 六种信源（RSS / 网页列表 / JSON 接口 / X / 公众号 / 外部推送），自适应抓取频率，旧文不刷屏规则；
- 预筛 → 两次独立评分 → 内容理解 → 结构化 → 事件归组 → 热榜，门槛按信源分级；
- 日报（规则成刊）、周报月报（由日报汇编），RSS、`/api/v1`、MCP、`llms.txt`、`/agent`；
- 后台：信源试抓、模型与评测页、SelectBench、金标集校准脚本。

这正是我们在造的那一层，而且比我们成熟。继续自制没有意义。

## 2. 决定

1. **引擎用 fork，不再自制。** fork 上游，只改上游明确留给站点的两处：`site/`（站名文案）与 `industry/`（分类、标签、信源、提示词、门槛）。流水线与页面代码不动，便于持续合并上游。
2. **内容定位：通用 AI + LLM infra。** 通用领域照上游口味；额外重点盯推理引擎与 serving、算子与编译、训练系统、量化压缩、芯片与集群。模型发布所有人都看；infra 的实质进展对做 infra 的读者就是头条。
3. **千里眼退成提炼与发布层。** 读 fork 的 `/api/v1`（精选、事件、日报），做我们自己的增值：一句话结论、传播梗、逻辑图、原文配图、按方向分栏、30 秒速览，发布成 artifact 推到用户手里。`core/` 里与上游重复的采集、信源解析、评分代码逐步下线。

## 3. 分工边界

| 层 | 归谁 | 内容 |
|---|---|---|
| 采集、精选、归组、热榜、日报成刊、RSS/API/MCP | fork（上游代码 + 我们的 `industry/`） | 不改引擎代码 |
| 口味 | fork 的 `industry/` | infra 类别与标签、infra 信源、评分提示词里的 infra 读者与价值规则、门槛校准 |
| 提炼 | 千里眼 | 结论 / 梗 / 逻辑图 / 配图 / 方向分栏 / 速览 / artifact 发布 / 定时任务 |
| 数据 | 各自 | fork 用 PostgreSQL；千里眼仍只在 `$QLY_DATA_DIR`，不进仓 |

`config/sources.yaml` 的 40 个信源已搬进 fork 的 `industry/sources.json`（见第 4 节），搬完后它在千里眼这边只剩「哪些方向关注」的角色，`tracks.yaml` 的方向判定可以改为直接读上游的 category 与 tags。

## 4. 已完成（fork 工作副本）

本地 `~/projects/qianliyan-hot`，上游 remote `upstream`，分支 `customize/general-plus-llm-infra`，一个提交；逐项清单在该仓 `docs/qianliyan-customization.md`。摘要：

- `industry/taxonomy.ts`：新增 `infra` 类别（日报分节「推理与训练基础设施」）；主题标签 +5（推理引擎/Serving、算子与编译、训练系统、量化压缩、芯片与集群）；实体标签 +4（NVIDIA、vLLM、SGLang、PyTorch）；主体 +6；身份词典、发布方域名、近义词同步。
- `industry/topics.json`：公司主题 +7（vLLM、SGLang/LMSYS、PyTorch、Together、Modular、Thinking Machines、AMD），方向 +5。
- `industry/sources.json`：上游 18 个保留，补 36 个，全部 2026-10-10 实测可达。含 Anthropic News / Engineering（上游缺，用 `web_list` 选择器）、7 个 infra 项目的 GitHub Releases（`json_list`）、infra 官方博客、个人分析、官方 YouTube、播客、arXiv cs.DC/CL/LG。
- `industry/prompts/`：评分读者画像加入 infra 工程师；放开原先把「底层优化、量化、训练方法」一律压到 `sig ≤ 4` 的规则，改成按「有无开源实现与主流规模实测」区分；标签白名单、归类说明、术语表同步。
- `site/site.ts`：站名千里眼，`mcpPrefix: qianliyan`，`crawlerName: QianliyanBot/1.0`，文案点出 infra。
- 验证：`npm run typecheck` 通过。`npm test`、`scripts/smoke.ts` 需要 PostgreSQL 与模型密钥，部署时跑。

## 5. 待用户决定

| # | 事项 | 我的建议 |
|---|---|---|
| 1 | 模型供应商与密钥 | DeepSeek（上游默认、便宜、JSON 模式）；也可千问 DashScope 或智谱。不能挪用 Claude Code 的额度 |
| 2 | 跑在哪 | 先在这台 8G M2 本机跑通（brew 装 PostgreSQL），再考虑云服务器 |
| 3 | fork 放哪 | forcegravity1989 下新建 fork（保留上游历史，便于合并） |
| 4 | 站名 | 千里眼 |

## 6. 后续步骤（拍板后）

1. fork 推到 GitHub；`site.github` 填上仓库地址。
2. 本机：`brew install postgresql@17`，`createdb qianliyan`，`node scripts/init-env.ts --llm-key …`，`npm run db:migrate`，`npm run dev:api` / `dev:worker` / `dev:web`，`npm test`，`node scripts/smoke.ts`。
3. 跑一周，标注金标集，`scripts/eval-selection.ts` 看 infra 条目的查准查全，决定是否给 `infra` 类别单独门槛。
4. 千里眼：新增 `core/upstream.py` 读 `/api/v1`，日报的素材来源切到它；`qly-daily.sh` 不再抓源。下线 `core/` 里重复的采集与评分。
5. 品牌：换掉 `site/brand/` 里上游模板自带的通用图标。

## 7. 不做什么

- 不改上游引擎代码；要改的先给上游提 issue / PR。
- 不把运行时数据（PostgreSQL 导出、`.data/`）放进任何 git 仓。
- 不用 AIHOT 的名字和 Logo 作为站点品牌；关于页底部保留上游默认的框架署名。
