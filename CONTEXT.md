# CONTEXT.md — 千里眼领域语言（Ubiquitous Language）

单人构建的 AI 情报采集与变更情报系统。本文件是**唯一的规范词表**：代码、文档、issue、对话都用这里选定的词。

> 定义原则：每个词只说它**是什么**（1–2 句），不写实现；`_Avoid_` 列出容易混淆的说法。设计事实源是 `docs/spec.md` + `docs/spec-v0.3.md`——命名冲突时以这两份为准，这里只是词表不是规格。

## 数据模型

**item**：贯穿全链路的最小单元，一条 AI 情报（一篇资讯 / 一条推文 / 一个版本变更 / 一个 repo 排名）。字段见 `core/schema.py` 的 `REQUIRED_FIELDS`。
_Avoid_：entry（那是 `remote_sync.fetch_source` 内部的中间形态，还没长成合法 item）。

**sig**：item 的去重签名，标题归一化后取 SHA1 前 16 位。同 `sig` 的多条 item 会被合并成一条。
_Avoid_：id（sig 是内容指纹，不是自增主键）。

**source_kind**：item 来自哪个大类适配器——`aihot` / `builders` / `company` / `local` / `insights`（`core/schema.py:SOURCE_KINDS`）。**这五个值是历史遗留的"五眼"名字，不代表真实架构还是五路并行**——`local` 一类下实际挂了 19+ 个配置驱动的真实信源（RSS/YouTube/GitHub trending/scrape/arXiv），加源只改 `config/sources.yaml`，不新增 `source_kind`。
_Avoid_：把"眼"当成架构原则去扩展；新信源优先挂 `local`，不要为每个信源发明新 `source_kind`。

**backend**：item 的抓取后端实现——`rest`/`raw_json`/`cdp`/`rss`/`git`/`html`/`sitemap`/`arxiv`（`core/schema.py:BACKENDS`，写入前必须是这八个值之一）。
_Avoid_：把非法后端字符串（如 `youtube`、`github-trending`）直接塞进 `backend`——这些属于 `extra.source_type`，`backend` 只认合法值。

**extra.format**：item 的呈现形态——`news`/`blog`/`video`/`talk`/`podcast`/`repo`/`paper`/`x`/`changelog`。驱动频道分组与深读/浅读的差异化渲染，比 `source_kind` 更贴近"这条该怎么呈现"。

## 交叉验证与打分

**cross_refs**：同一事件被多少个*额外*信源报道过（= `source_list` 长度 − 1）。千里眼的核心价值主张——多源报道自动加权。

**hotness**：`weight × 新鲜度(半衰期7天) × (1 + 0.35·ln(1+cross_refs))`。见 `core/utils.py:compute_hotness`。

**badge**：`heavy`(📈 重磅，`cross_refs≥3`) / `flash`(⚡ 一手速报，`weight≥0.95` 且 24h 内)。`core/schema.py:BADGES`，只有这两个合法值。
_Avoid_：把叙事↔实证的"实证"标记（`corroboration.badge = "verified"`）和这里的 badge 混为一谈——两者字段不同、含义不同。

## 变更情报（本系统的护城河）

**变更源**：产出 `extra.format="changelog"` item 的三个适配器——`eyes/cc_prompts.py`（Claude Code 系统提示词版本变更）、`eyes/plugins_official.py`（官方插件市场 bump）、`eyes/insights.py`（插件/skill 生态日榜）。

**叙事（claim）**：演讲/博客里含数字断言的说法（如"减少了 80%"），由 `pipeline/change_intel.py:cross_map_claims` 从 `extra.format∈{video,talk,blog,news}` 的 item 里抽取。

**实证（evidence）**：同主题变更源 item 的 `token_delta` 聚合，用来验证叙事断言的方向与量级。

**corroboration**：叙事↔实证映射的结果，写在叙事 item 的 `extra.corroboration = {claim, evidence, verdict}`。`verdict` 三态：`corroborated`(🔬 实证，方向量级吻合) / `unverified`(存疑，找不到实证) / `contradicted`(矛盾，方向相反)。
_Avoid_：把 `verdict` 和 `badge` 当同一套枚举——corroboration 是独立结构，不进 `item["badges"]`（`schema.BADGES` 不认 `verified`）。

## 人物 / 读者

**人物画像（persona）**：为被追踪的 builder 生成的档案（bio/近期在关注/影响力），`pipeline/persona.py:build_personas`。
_Avoid_：和"读者画像"弄反——persona 是关于*被追踪对象*的，不是关于用户自己的。

**读者画像（reader profile）**：用户自己的兴趣模型，`config/reader.yaml` 显式配置 × `history.jsonl`/`feedback.jsonl` 派生偏好，`core/profile.py:load_reader_profile`。

**personal_score**：一条 item 对*这个读者*的个性化得分（`hotness × 兴趣乘数`），不改变 `hotness` 本身。

## 深读 / 浅读

**浅读（glance）**：标题即已接收的极速标题流。**深读（deep）**：突出图片与论点的精读卡，含 `distill`（LLM 精读增强：`kp`/`chain`/`pull`/`limits`/`theses`）。两者渲染自同一份 `digest-final.json`，见 `cli/daily_digest_all.py`。

## 数据与代码隔离

**QLY_DATA_DIR**：千里眼的运行时数据（`items.jsonl`/`channels/`/`personas.json`/`digest.html`……）永远解析到 git 仓库*之外*的一个目录（四级优先级见 `core/paths.py:resolve_data_dir`，缺省 `~/qianliyan-data`）。这不是约定俗成，是写进代码的硬约束——本仓库应该**永远不会**出现被 track 的运行时数据文件。改动前如果看到 `items.jsonl`/`digest.html`/`hotlist.md` 之类文件被 `git add`，那是 bug，不是正常状态。
_Avoid_：把 `config/` 和"数据"混为一谈——`config/*.yaml`（sources/channels/builders/reader……）是**代码的一部分**（决定行为、需要 review、进 git），和 `$QLY_DATA_DIR` 下的运行时产物完全是两回事。

## 工程操作词

**规范铺底**：把 [builders-workbench](https://github.com/forcegravity1989/loop-buddy)（bw/buddy）的项目治理骨架（`.bw/` 下的 `PROJECT.md`/`standard.toml`/`issue-policy.toml`/`metrics.toml`……）铺进本仓，见 `.bw/` 目录与 [issue #14](https://github.com/forcegravity1989/aihot/issues/14)。这是治理层，不是项目文档——项目怎么用看 `README.md`/`docs/`。

**Wave（波次）**：本仓实现阶段的并行工作单元，波内文件集互斥，见 `docs/plan.md`、`docs/PLAYBOOK.md`。
