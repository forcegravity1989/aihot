# spec-v0.3.md — 千里眼 v0.3 增量合同（真实数据 + 人物画像 + 读者画像 + 深读/浅读日报）

> 本文是对 `docs/spec.md` 的**增量**，只描述新增/变更。未提及处一律沿用 v0.2 spec（层间边界、schema、算法、路径解析、Agent 回退公约、Python 3.9 兼容、依赖铁律、离线测试等全部不变）。
> 用户明确的四个新诉求：① 接入真实信源（内眼保留槽位不联调）② 主信源 aihot=卡兹克的 AIHOT 站、builders=zarazhang 的 follow-builders 仓库 + 其他真实信源 ③ **人物画像 + 读者画像（读者画像关联历史记录）** ④ **每日简报分深读/浅读**（浅读=看过标题即已接收的速览流；深读=全文重点研读，尤其突出**图片与论点**）。

---

## 0. 真实数据侦察结论（已存 fixtures，实现对着真实结构写）

真实样本已抓存于 `tests/fixtures/real/`：`aihot_feed_curated.xml`（精选 35 条）、`aihot_feed_all.xml`（7 天全量）、`aihot_feed_daily.xml`（官方日报）、`builders_feed_x.json`（13 位 builder）、`builders_feed_podcasts.json`、`builders_feed_blogs.json`、`builders_default_sources.json`、`builders_config_schema.json`。**测试必须对着这些真实 fixtures 写，禁止真实出网。**

### 0.1 AIHOT（左眼，源显示名「AIHOT · 卡兹克」）

站点是 Next.js SSR + 反爬 JS 挑战，但**暴露干净的 RSS 出口**，走 RSS 即可绕开反爬：

| feed | 路径 | 内容 | 建议 weight |
|------|------|------|-------------|
| 精选 | `/feed.xml` | AI 筛选的高信号 top ~50 | 0.85 |
| 全量 | `/feed/all.xml` | 最近 7 天全部动态 | 0.70 |
| 分类 | `/feed/category/{slug}.xml` | slug ∈ `ai-models` `ai-products` `industry` `paper`（仅这四个 200，其余 404） | 0.75 |
| 日报 | `/feed/daily.xml` | 官方每日日报索引（供 daily 版面参考，非普通 item） | — |

**item 字段**（RSS 2.0）：`<title>`（CDATA）、`<link>`（AIHOT 详情页 `/items/<id>`）、`<description>`（CDATA，HTML：首个 `<p>` 是摘要，随后 `🔗 <a href="原文URL">阅读原文</a>`，再 `via AIHOT` 链接）、`<category>`（中文：`AI 模型`/`AI 产品`/`论文`/`行业动态`/`技巧观点`）、`<pubDate>`（RFC822）、`<guid>`（= item id）。

**解析要点**：
- **canonical `url` = description 里「阅读原文」锚点的 href**（真实原文，满足铁律②）；抽不到才回退到 `<link>` 的 AIHOT 详情页。
- `summary` = description 首个 `<p>` 文本（去 HTML 标签、解 HTML 实体）。
- `extra.aihot_id`（guid）、`extra.aihot_url`（`<link>`）、`extra.category`（中文原值）、`extra.category_slug`（映射：AI 模型→models、AI 产品→products、论文→paper、行业动态→industry、技巧观点→opinion）。
- `<description>` 里若含 `<img src>` → 收集进 `extra.images`（供深读用）。
- `tags`：由 category_slug 派生（如 paper→[paper, research]、models→[models]）。
- aihot 内部按 canonical url 或 guid 去重（精选与分类 feed 会重叠）。
- `source_kind="aihot"`，`backend="rss"`，`source="AIHOT · 卡兹克"`。

### 0.2 follow-builders（右眼，zarazhang）

仓库 `zarazhangrui/follow-builders`（default branch `main`，raw 前缀 `https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/`）。

- `feed-x.json`：`{generatedAt, lookbackHours, x:[{source:"x", name, handle, bio, tweets:[{id, text, createdAt(ISO), url, likes, retweets, replies, isQuote, quotedTweetId}]}], stats}`。当前 13 位 builder（含 Zara Zhang 本人 `zarazhangrui`、Amjad Masad `amasad`、Guillermo Rauch `rauchg`、Garry Tan `garrytan`、Peter Steinberger `steipete` 等）。
- `feed-podcasts.json` / `feed-blogs.json`：同信封 `{generatedAt, lookbackHours, podcasts|blogs:[...], stats}`（当前为空数组，解析必须容忍空）。
- `config/default-sources.json`：`{podcasts:[{name,rssUrl,url}], blogs:[{name,type,indexUrl,...}]}`（可作为远眼补充源，可选）。

**解析要点**（每条 tweet → 一个 item）：
- `title` = tweet.text 首行/前 ~80 字（去换行）；`summary` = 完整 text；`url` = tweet.url；`date` = createdAt。
- `source = "@{handle}"`，`source_kind="builders"`，`backend="raw_json"`。
- `metrics = {likes, retweets, replies, engagement: likes+retweets+replies}`。
- `extra = {handle, name, bio, platform:"x", is_quote}`。
- `tags = [x, builders]`（+ 命中关键词可加 models/agent 等，可选）。
- 过滤：`x-follows.yaml` 的 mute 名单剔除；builders.yaml 若配了 allowlist 则只留白名单，否则**默认收录 feed 中出现的全部 builder**。
- weight：builders.yaml 的 `default_weight`（建议 0.8），可按 handle 覆盖。

### 0.3 其他真实信源（远眼，sources.yaml）

保留 v0.2 已配的真实 RSS/官方源（Anthropic News、OpenAI、DeepMind、HuggingFace、arXiv、Simon Willison、Latent Space…），它们是真实可达的公开 feed。可补充 `builders_default_sources.json` 里的播客源。内眼（company）**保留槽位、不联调**，洞眼（insights）保持 v0.2 可选。

---

## 1. 变更：eyes/aihot.py（REST → RSS 重写）

- `sources.yaml` 的 `aihot:` 段新结构：
  ```yaml
  aihot:
    base_url: https://aihot.virxact.com
    source_name: "AIHOT · 卡兹克"
    feeds:            # 通用 feed：{path, kind, weight}
      - {path: /feed.xml,     kind: curated, weight: 0.85}
      - {path: /feed/all.xml, kind: all,     weight: 0.70}
    category_feeds:   # 分类 feed slug（只填真实 200 的）
      slugs: [ai-models, ai-products, industry, paper]
      weight: 0.75
    category_map:     # 中文 category → slug/tags
      "AI 模型": {slug: models,  tags: [models]}
      "AI 产品": {slug: products, tags: [products]}
      "论文":    {slug: paper,   tags: [paper, research]}
      "行业动态": {slug: industry, tags: [industry]}
      "技巧观点": {slug: opinion, tags: [opinion]}
  ```
- `fetch(cfg, since=None)`：对 `feeds` + `category_feeds.slugs` 逐个 `engine.http.get` 取 xml → `parse_feed(xml_text, kind, weight, cfg)` → 汇总 → 按 canonical url/guid 去重（aihot 内部）。任一 feed 失败记 warning 继续（不因单 feed 拖垮整眼）。
- **纯函数** `parse_feed(xml_text, kind, weight, cfg) -> list[dict]`：单测直喂 `aihot_feed_curated.xml` 等真实 fixture。按 §0.1 解析要点产出标准 item（`schema.make_item`）。
- 保留旧 REST 解析函数壳并标 deprecated（无下游），或直接删除——二选一，报告说明。

## 2. 变更：eyes/builders.py（占位仓库 → 真实仓库）

- `config/builders.yaml` 新结构：
  ```yaml
  repo: zarazhangrui/follow-builders
  branch: main
  feeds: [feed-x.json, feed-podcasts.json, feed-blogs.json]
  default_weight: 0.8
  allowlist: []          # 空 = 收录 feed 中全部 builder
  weight_overrides: {}   # {handle: weight}
  ```
- `fetch(cfg)`：raw 拉三个 feed（podcasts/blogs 空则跳过）；`parse_feed_x(payload, cfg)` / `parse_feed_generic(payload, kind, cfg)` 纯函数按 §0.2 产出 item，对着 `builders_feed_x.json` 真实 fixture 测试。mute/allowlist 过滤。

## 3. 新增：pipeline/persona.py（人物画像）

`build_personas(items, cfg=None, client=None) -> list[dict]` + `write_personas(personas) -> None`（写 `$QLY_DATA_DIR/personas.json` + `personas/<handle>.md`）。

- 输入统一池 items，按 `extra.handle` 聚合 builders 类条目（`source_kind=="builders"`）。
- 每个 persona：
  ```
  {handle, name, bio, avatar_path,           # avatar_path: builder-avatars/<handle>.png 若存在
   item_count, total_engagement, avg_engagement, last_active,
   top_items: [{title, url, engagement, date} ...最多3],
   topics: [str ...],                         # LLM 聚类 or 回退：top tags/关键词
   recent_focus: str}                         # LLM 一句话「近期在关注…」or 回退：top_item.title
  ```
- **LLM 可选、必有回退**（遵循 v2 spec §5 回退公约）：`topics` 回退 = 该人 items 的 tags/高频词 top N；`recent_focus` 回退 = engagement 最高 item 的标题。LLM 不可用绝不抛。
- 按 `total_engagement` 降序返回。
- 可扩展：预留 `build_person_from_news`（新闻人物）接口壳，v0.3 只实装 builders，docstring 注明。

## 4. 新增：core/profile.py + 读者画像 + 历史记录

### 4.1 历史记录 `history.jsonl`（数据目录）

追加式，每行 `{sig, ts(ISO), action, title, url}`，`action ∈ {seen, open, deepread, received}`（received=浅读"看过标题即已接收"）。
- `log_history(entries: list[dict]) -> None`：原子追加（复用 storage 风格，读改写或 append）。
- `read_history(limit=None) -> list[dict]`。

### 4.2 读者画像 `config/reader.yaml`（可编辑）

```yaml
interests:
  tags:    {claude-code: 1.6, agent: 1.4, models: 1.3, research: 1.1}
  sources: {}                # 源名子串 → 乘数
  people:  {}                # handle → 乘数
mute:
  tags: []
  people: []
```

### 4.3 `core/profile.py`

- `load_reader_profile() -> dict`：读 `reader.yaml`（缺失给空默认）+ 从 `history.jsonl`、`feedback.jsonl` **派生**近期偏好（按 recency 衰减统计 tag/source/handle 命中频次 → 归一为乘数，与显式 interests 相乘/相加）。返回 `{tags, sources, people, mute, derived}`。
- `personalize(items, profile=None) -> None`（原地）：为每条写 `extra.personal_score = round(hotness * multiplier, 4)` 与 `extra.personal_reasons: [str]`（命中了哪些兴趣）。multiplier = 1 × ∏(命中 tag/source/people 的乘数)，mute 命中则 personal_score=0。不改变 hotness 本身。
- 无历史/无 reader.yaml 时 `personalize` 退化为 `personal_score = hotness`（不报错）。

## 5. 变更：pipeline/report.py + 模板（新增两个版面）

digest.html 在原 10 版面基础上新增：
- **为你推荐**（personalized）：按 `extra.personal_score` 降序 top N，命中兴趣的原因用 chip 展示。
- **人物画像**（personas）：persona 卡片网格——头像（base64 内嵌，缺失首字母占位）、`name` + `@handle`、`bio`、`近期在关注：{recent_focus}`、`topics` chips、`top_items` 链接、影响力数字（total_engagement / item_count，tabular-nums）。
- `render_html` 新签名：`render_html(items, channel_map=None, personas=None, out_path=None)`（personas 省略则不渲染该版面，向后兼容）。

## 6. 变更：cli/daily_digest_all.py（深读/浅读双视图）

用户口径：**浅读 = 看过标题就已接收信息**（极速标题流）；**深读 = 全文重点研读，突出图片与论点**。

- `--prepare`：候选排序改用 `extra.personal_score`（缺失回退 hotness）；候选 = personal top 40 ∪ 各频道 top 5，写 `archive/<date>/digest-draft.json`。
- `--finalize`：对 selected 条目做**深读精读增强**（LLM 可选、回退不阻塞），每条补：
  ```
  distill: {kp: [要点...], chain: 脉络, pull: 影响, limits: 局限, theses: [关键论点...]}
  images: [url...]     # 合并 extra.og_image + extra.images（aihot 描述内嵌图）
  ```
  回退：`kp` = summary 按句切分前 3 句；`theses` = []；`images` = [og_image] 若有。
- `--html`：从**同一份** `digest-final.json` 渲染**两个视图 + 一个带切换的合并页**：
  - **浅读** `templates/glance.html.jinja`：标题流。每行 = 交叉验证徽章(📈/⚡/多源计数) + **标题**(中文优先) + 来源 + 时间 + `[原文]`。设计目标：一眼扫完即"已接收"；每行「标记已读」+ 顶部「全部已接收」，静态页用 localStorage 记已读态并置灰（有 API 时 POST /history action=received）。极简、快扫、无大图。
  - **深读** `templates/deep.html.jinja`：每条一张精读卡——**大图/配图**（images，无图则跳过图区）、标题、完整摘要/正文、**关键论点**（theses 醒目列出）、要点/脉络/影响/局限（distill 四段）、**交叉验证展开**（source_list 各源 + 链接）、原文链接。突出图片与论点。
  - 合并页 `archive/<date>/digest.html`：顶部「浅读 / 深读」切换（vanilla JS 切换两个 `<section>`，默认浅读），复制一份到数据根 `daily.html`。
- 旧 `build_v2_png_prompt` 仍为 deprecated 壳。

## 7. 变更：cli/sync.py（编排接入新层）

在 v2 流程步骤 6→7 之间插入（`--quick` 时跳过 persona/personalize 中的 LLM 部分，但结构性字段仍要落）：
```
... 四个 agent 标注 ...
profile = core.profile.load_reader_profile()
core.profile.personalize(deduped, profile)          # 写 personal_score
personas = pipeline.persona.build_personas(deduped)  # 回退不阻塞
pipeline.persona.write_personas(personas)
routed = channels.run_all(deduped)
report.render_html(deduped, routed, personas=personas)
```
`sync_meta.totals` 增 `personas` 计数。

## 8. 变更：cli/api_server.py（新增端点）

| 端点 | 行为 |
|------|------|
| `GET /personas` | personas.json |
| `GET /profile` | 读者画像（reader.yaml + derived）+ 前 N personalized items |
| `GET /daily?view=glance\|deep` | 返回 archive 当日对应视图 HTML（缺省 deep） |
| `POST /history` | body `{sig, action, title?, url?}` → 追加 history.jsonl，返回 `{logged:true}` |

`GET /items` 增 `sort=personal` 支持（按 personal_score）。

## 9. 测试（全部离线，对着 real fixtures）

新增/更新：
- `test_eyes_real.py`：aihot `parse_feed` 对 `aihot_feed_curated.xml`/`aihot_feed_all.xml` 出标准 item，**canonical url = 原文 href**、category 映射、内嵌 img 收集、aihot 内部去重；builders `parse_feed_x` 对 `builders_feed_x.json` 出 item、engagement 计算、mute 过滤、空 podcasts/blogs 容忍。
- `test_persona.py`：多 builder 条目 → persona 聚合、engagement 排序、无 LLM 回退（topics/recent_focus 落值不抛）。
- `test_profile.py`：reader.yaml 加载、history/feedback 派生偏好、personalize 写 personal_score/reasons、mute 归零、无配置退化为 hotness、history 追加读回。
- `test_daily_v3.py`：finalize 回退 distill 字段齐全、glance/deep/合并页渲染产物含各自要素（浅读无大图有标题流+已读控件；深读有 theses/images/distill/交叉验证展开；合并页有切换 JS）。
- `test_report_v3.py`：render_html 传 personas 渲染人物版面 + 为你推荐版面（personal_score 排序）。
- 更新 `test_sync_mock.py`：断言 personas.json / daily 流程产出、personal_score 落字段。
- 更新 `test_api.py`：/personas、/profile、/history、/daily 端点。

## 10. 交付物（人读）

更新 `docs/overview.html`：新增「真实数据接入」「人物画像」「读者画像与历史」「深读/浅读日报」四块 + 刷新验证结论与版面数。保持单文件自包含、双主题、简体中文。发布/更新 Artifact（同一 URL）。

---

## 11. 架构取舍：从「五眼硬骨架」到「分类型信源目录」（取精华去糟粕）

用户反馈：原 v0.2 设计被最初的头脑风暴带偏，需取精华去糟粕；且 **YouTube（尤其 Claude 大会 50+ 演讲）、官方 blog、先锋公司播客、GitHub trending、方法论内容（loop / AI-native SDLC）** 才是关键信息。

**精华（保留强化）**：交叉验证加权（核心）、统一池去重打分、深读/浅读日报、人物画像、读者画像+历史、单文件 HTML 简报、离线可测 + 单源故障隔离、每条带 URL + 简中人话。

**重塑**：把「五眼」硬骨架让位给**配置驱动的分类型信源目录**——`local`（远眼）升级为**目录眼 catalog**，吃一份丰富的 `sources.yaml`；`aihot`/`builders` 只是两个「带专用解析器的源」。新增信源只改 `sources.yaml`，不动代码。**「眼」退化为 item 上的一个 tag，不再是架构约束。**

**去糟粕（降级为可选/槽位，代码保留不删，靠配置与叙述收敛，可逆）**：内眼 CDP（保留空槽，不联调）、洞眼 insights（可选、默认不进主流程）、3 个投机性 LLM 标注 agent（model_cluster/headline_fit/headline_cluster 仅回退态、非主链路）、welink/email/PNG 日报（stub）。真正有价值的 LLM 用途只有两个：**翻译（英→中）** 与 **深读 distill**。

---

## 12. 新增 item 维度：`extra.format`（贯穿分组/深读/浅读）

每条 item 落 `extra.format ∈ {news, blog, video, talk, podcast, repo, paper, x}`。由源配置的 `format` 或 backend 推断。它驱动：频道分组（talks/practices/trending…）、深读卡的呈现（video/talk 突出缩略图 + 演讲描述；repo 突出 star 增量；paper 突出摘要）、浅读流的图标。

## 13. engine 层新增后端（供目录眼消费）

### 13.1 `engine/youtube.py`
- `feed_url(cfg) -> str`：`channel_id` → `https://www.youtube.com/feeds/videos.xml?channel_id={id}`；`playlist_id` → `?playlist_id={id}`；`handle`（@name）→ 需先解析 channel_id（在线，离线测试不触发）。
- YouTube feed 是 **Atom**（`<entry>`：`<title>`、`<yt:videoId>`、`<link href>`、`<published>`、`<media:description>`、`<media:thumbnail url>`）。
- `parse(xml_text, cfg) -> list[dict]`（纯函数，喂 `tests/fixtures/real/youtube_anthropic.xml`）：item `url`=视频链接、`summary`=media:description、`date`=published、`extra={platform:youtube, video_id, thumbnail, channel}`、`format` = 源配置（channel→video，conference playlist→talk）、tags 来自源。

### 13.2 `engine/github_trending.py`
- `fetch(since="daily", spoken_lang=None) -> list[dict]`：GET `https://github.com/trending?since={since}`；`parse(html_text, since) -> list[dict]`（纯函数，喂 `tests/fixtures/real/github_trending.html`，结构 `<article class="Box-row">`）。
- 每 repo → item：`title = "owner/repo"`、`url = https://github.com/owner/repo`、`summary` = 描述、`metrics = {stars, stars_period, forks}`、`date` = now（trending 无发布时间）、`tags = [github, trending] (+ 语言)`、`extra = {format: "repo", language, since}`、`source = "GitHub Trending"`。

### 13.3 `engine/html_page.py` 扩展：blog 抓取（无 RSS 源，如 Anthropic）
- `fetch_articles(index_url, article_pattern, base_url=None, limit=20) -> list[dict]`：GET 索引页，抽匹配 `article_pattern`（如 `^/news/[a-z0-9-]+$`）的 `<a>`，标题取链接文本/`aria-label`，相对链接补全。喂 `tests/fixtures/real/anthropic_news.html`。item `format="blog"`。

### 13.4 `engine/remote_sync.py`：`detect_backend` + `fetch_source` 增分支
`detect_backend` 增：源 `type` 显式为 `youtube`/`youtube-playlist`/`github-trending`/`scrape` 时直接采信；`youtube.com` host 且带 channel/playlist → youtube。`fetch_source` 按新 backend 分发到上述模块。item 继承源的 `weight/tags/format/category`。

## 14. `config/sources.yaml`：真实信源目录（已核验端点）

`sources:` 列表每项：`{name, type, weight, tags, format, category, ...type 专属字段}`。**必须用下列已核验为真的端点**（本机 curl 实测状态在括号）：

```yaml
sources:
  # —— 官方 blog（RSS，真实可达）——
  - {name: OpenAI News,          type: rss, url: "https://openai.com/news/rss.xml",        weight: 0.95, format: blog, category: models,   tags: [official, openai, models]}      # 200
  - {name: Google DeepMind Blog, type: rss, url: "https://deepmind.google/blog/rss.xml",   weight: 0.92, format: blog, category: models,   tags: [official, google, models]}      # 200
  - {name: HuggingFace Blog,     type: rss, url: "https://huggingface.co/blog/feed.xml",   weight: 0.85, format: blog, category: tools,    tags: [vendor, huggingface, tools]}    # 200
  - {name: Simon Willison,       type: rss, url: "https://simonwillison.net/atom/everything/", weight: 0.8, format: blog, category: practices, tags: [kol, dev, practices]}         # 200
  # —— Anthropic（无 RSS，走 scrape）——
  - {name: Anthropic News,        type: scrape, url: "https://www.anthropic.com/news",        article_pattern: "^/news/[a-z0-9-]+$",        weight: 0.98, format: blog, category: models,     tags: [official, anthropic, models]}   # 200(html)
  - {name: Anthropic Engineering, type: scrape, url: "https://www.anthropic.com/engineering", article_pattern: "^/engineering/[a-z0-9-]+$", weight: 0.96, format: blog, category: practices, tags: [official, anthropic, practices]}
  - {name: Claude Blog,           type: scrape, url: "https://claude.com/blog",               article_pattern: "^/blog/[a-z0-9-]+$",        weight: 0.9,  format: blog, category: claude-code, tags: [official, claude, claude-code]}
  # —— 官方 YouTube（频道 feed，真实可达）——
  - {name: Anthropic YouTube,      type: youtube, channel_id: UCrDwWp7EBBv4NwvScIpBDOA, weight: 0.9,  format: video, category: talks, tags: [official, anthropic, video, talks]}   # 200
  - {name: OpenAI YouTube,         type: youtube, channel_id: UCXZCJLdBC09xxGZ6gcdrc6A, weight: 0.85, format: video, category: talks, tags: [official, openai, video, talks]}       # 200
  - {name: Google DeepMind YouTube, type: youtube, channel_id: UCP7jMXSY2xbc3KCAE0MHQ-A, weight: 0.82, format: video, category: talks, tags: [official, google, video, talks]}      # 200
  # —— Claude 大会演讲（播放列表，playlist_id 待用户/后续确认后填入；先注释占位，不填不影响其余源）——
  # - {name: "Code with Claude 大会", type: youtube, playlist_id: "PLxxxxxxxx", weight: 0.95, format: talk, category: talks, tags: [official, anthropic, talk, conference, practices]}
  # —— AI 播客（RSS，源自 follow-builders default-sources）——
  - {name: Latent Space,         type: rss, url: "https://www.latent.space/feed",       weight: 0.78, format: podcast, category: podcasts, tags: [kol, podcast]}                    # 200
  - {name: "No Priors",          type: rss, url: "https://feeds.megaphone.fm/nopriors",  weight: 0.72, format: podcast, category: podcasts, tags: [kol, podcast]}                    # 200
  - {name: "Training Data (Sequoia)", type: rss, url: "https://feeds.megaphone.fm/trainingdata", weight: 0.7, format: podcast, category: podcasts, tags: [vc, podcast]}
  - {name: "Unsupervised Learning", type: rss, url: "https://feeds.simplecast.com/dOSE_bdP", weight: 0.7, format: podcast, category: podcasts, tags: [kol, podcast]}
  # —— GitHub Trending（scrape，真实可达）——
  - {name: GitHub Trending (Daily),  type: github-trending, since: daily,  weight: 0.68, format: repo, category: trending, tags: [github, trending, tools]}
  - {name: GitHub Trending (Weekly), type: github-trending, since: weekly, weight: 0.62, format: repo, category: trending, tags: [github, trending, tools]}
  # —— 论文（arXiv API）——
  - {name: arXiv cs.CL, type: arxiv, url: "http://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=20", weight: 0.7, format: paper, category: papers, tags: [research, arxiv]}
  - {name: arXiv cs.AI, type: arxiv, url: "http://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=20", weight: 0.7, format: paper, category: papers, tags: [research, arxiv]}
```

（aihot 段、insights 段沿用 §1；insights 默认不进 sync 主流程。）

## 15. 频道重构（`config/channels.yaml`）：按 format + tag 收敛，凸显用户关切

在原有基础上重构/新增，match 支持新键 `formats`（对 `extra.format`）与 `categories`（对源 category，透传到 item `extra.category`/`extra.source_category`）：
- **头条**（跨源热榜，热度+交叉验证）
- **models**（模型）· **claude-code** · **practices**（方法论：tags_any[practices] 或 keywords_any[loop, sdlc, agent, eval, harness, workflow]）
- **talks**（formats_any[video, talk]，Claude 大会 / 官方频道演讲）
- **podcasts**（formats_any[podcast]）
- **trending**（formats_any[repo] / GitHub）
- **papers**（formats_any[paper]）
- **builders-live**（source_kinds[builders]，X 动态）
- **company-internal**（保留槽位）

`pipeline/channels.match_item` 增 `formats` / `categories` 匹配键（块内 AND、any_of 表 OR，语义同 v2 §8.2）。

## 16. 深读/浅读接入 format（补充 §6）

- **浅读**：按 format 分组的标题流，每行前置 format 图标（📰 news / 📝 blog / 🎬 video / 🎤 talk / 🎧 podcast / 📦 repo / 📄 paper / 🐦 x）。
- **深读**：video/talk 卡突出**缩略图 + 演讲要点**；repo 卡突出 **star 增量 + 语言**；paper 卡突出**摘要 + arXiv 链接**；其余突出 images + 论点。distill 对 talk/video 侧重「讲了什么观点」（theses），呼应用户「大会视频传递观点」。

## 17. 测试补充（真实 fixtures，全离线）

- `tests/test_eyes_real.py`（**补 Wave A 未竟**）：aihot `parse_feed` 对 3 个真实 aihot feed、builders `parse_feed_x` 对 `builders_feed_x.json`（13 builder、engagement、mute）。
- `tests/test_engines_real.py`：youtube `parse` 对 `youtube_anthropic.xml`（video_id/thumbnail/date）、github_trending `parse` 对 `github_trending.html`（owner/repo、stars、语言）、html_page `fetch_articles` 对 `anthropic_news.html`（/news/<slug> 抽取）、detect_backend 新分支。
- 频道 formats/categories 匹配、深读/浅读 format 分组渲染。

---

## 18. YouTube 字幕/转写能力（transcript）——可插拔提供方 + 优雅降级

用户诉求：读取 YouTube 字幕（尤其 Claude 大会 50+ 演讲），让深读能真正"读懂演讲观点"而非只看标题描述。参考 baoyu-skills 的 youtube-transcript 能力。

**已核验的现实**（本机 curl 实测）：YouTube 对纯 requests 的字幕抓取做了强反爬——WEB client 返回 `UNPLAYABLE / Video unavailable`，ANDROID/IOS innertube 的公开 key 已失效（返回 error），watch 页 1MB+ 常抓取超时。**从数据中心 IP 直连基本拿不到字幕**。follow-builders 自己的 Latent Space 源都用 `pod2txt.vercel.app` 转写代理，印证了「靠第三方转写服务」才是稳的路子。

**因此设计为可插拔提供方 + 回退**（与全系统「可选增强、失败有回退」一致）：

### 18.1 `engine/youtube_transcript.py`

```python
def parse_timedtext(text, fmt="auto") -> list[dict]        # 纯函数：json3 或 srv/xml → [{start, dur, text}]
def transcript_text(segments) -> str                       # 段落拼接为纯文本（去纯换行段、折叠空白）
def get_transcript(video_id, lang_pref=("en","zh-Hans","zh","zh-Hant")) -> "str|None"
    # 提供方按序尝试，任一成功即返回纯文本；全失败返回 None（绝不抛）：
    #   1) 配置代理  env QLY_TRANSCRIPT_PROXY（如 pod2txt / 自托管 youtube-transcript-api / baoyu skill 服务）
    #      GET {proxy}?v={video_id}（或 {proxy}/{video_id}），响应为 json3/srv/纯文本 → parse_timedtext/直用
    #   2) 直连 best-effort  innertube player→captionTracks→timedtext&fmt=json3（YouTube 放松或住宅 IP 下可成）
    #   3) 都失败 → None
    # QLY_OFFLINE=1 直接返回 None（不出网）。
```

- `parse_timedtext` 用真实样本 `tests/fixtures/real/timedtext_sample.json3.json` 与 `timedtext_sample.srv.xml` 做**离线单测**（json3 的 `events[].segs[].utf8` 拼接、srv 的 `<text>` 抽取、去纯 `\n` 段）。
- `get_transcript` 的网络部分**不进离线测试**（monkeypatch 掉或 QLY_OFFLINE 短路）。

### 18.2 深读接入（补 §6/§16 的 --finalize distill）

对 `extra.format ∈ {video, talk}` 且有 `extra.video_id` 的选中条目：`--finalize` 先 `get_transcript(video_id)`：
- 成功 → `extra.transcript` 存全文；distill 以**字幕全文**为输入让 LLM 提炼演讲**论点 theses / 要点 kp**（呼应「大会视频传递观点」）；LLM 不可用则 `kp` 回退取字幕前几句。
- 失败/None → 回退用 RSS 已抓的 `media:description`（`extra.summary`）做 distill 输入。
- 全程不阻塞：无字幕、无 LLM、离线，深读卡至少仍有标题/描述/缩略图/原链接。

### 18.3 配置

- env `QLY_TRANSCRIPT_PROXY`：转写代理基址（缺省空 = 只走直连 best-effort）。
- 文档说明：把 baoyu youtube-transcript skill 或自托管 `youtube-transcript-api` 服务地址填到该环境变量即可启用稳定字幕；从用户住宅 IP 直连也可能直接成功。

### 18.4 提供方顺序（按 baoyu-skills 的真实方法修订）

已阅 `JimLiu/baoyu-skills` 的 `packages/baoyu-fetch/src/adapters/youtube/transcript.ts`：**它是浏览器方案**——在 watch 页内读 `window.ytInitialPlayerResponse` 与页面自带 `ytcfg.data_.INNERTUBE_API_KEY`，用**页面自身会话**（`credentials:'include'`）以 `clientName:"ANDROID"` POST `/youtubei/v1/player`，取 `captionTracks`（优先非 asr），再 fetch `track.baseUrl` 拿字幕 XML，解析 `<text start dur>` / `<p t d>` 段。纯 requests 从数据中心 IP 拿不到，正因为缺这个真实浏览器上下文。

`get_transcript(video_id)` 提供方顺序：
1. **web-access CDP（首选，用户指定）**：复用已装的 `web-access` skill 的 **CDP/Chrome 能力**（真实浏览器、真实会话）打开 watch 页，注入 baoyu 的页内脚本（读 `ytInitialPlayerResponse` + 页面自带 `INNERTUBE_API_KEY` → `credentials:'include'` POST `/youtubei/v1/player` (ANDROID) → captionTracks → 抓字幕 XML → 解析）。千里眼运行侧同法：`engine/cdp.py` 连常驻 Chrome（CDP `:9333` 或 `web-access` 提供的端点）→ `goto` → `evaluate` baoyu 脚本。浏览器不可用则跳过。示例频道 `https://www.youtube.com/@claude/videos`（channel_id `UCV03SRZXJEz-hchIAogeJOg`）。
2. **配置代理**：env `QLY_TRANSCRIPT_PROXY`（pod2txt / 自托管 youtube-transcript-api）。
3. **直连 best-effort**：innertube→timedtext（住宅 IP 可成）。
4. 全失败 → None（深读回退 media:description）。`QLY_OFFLINE=1` 直接 None。

> **@claude 官方频道**（比 @anthropic-ai 更聚焦，channel_id `UCV03SRZXJEz-hchIAogeJOg`，feed 已验 200）应加入 `sources.yaml`（format video, category talks）；Claude 大会 50+ 演讲的 playlist_id 待确认（可用 web-access CDP 翻 @claude/videos 的 playlists 抽取）。见 issue #9。

`parse_timedtext`（json3/srv-xml，纯函数）离线测试，样本已存 `timedtext_sample.json3.json` / `timedtext_sample.srv.xml`。

### 18.5 归属

`engine/youtube_transcript.py` + 深读 wiring 由**变更情报波（Wave H）**实现。测试 `tests/test_transcript.py`（纯 parse，离线）。

---

## 19. 变更情报（Change Intelligence）——本系统的最高价值层

用户诉求（核心）：不只聚合，要从**变更流**里发现深入信息——把**提示词变更**与**官方 changelog** 绑定，找出**关键变更点**，并与 **YouTube/talk 里的观点做映射验证**（例："Fable5 减少了 80% 提示词"这类叙事，能否被提示词 token 实测 diff 证实）；同时用 **trending 插件/skill** 勾勒**业界资产演进**（multica、teams.ai、kanban.ai 这类新项目）。

这是「交叉验证」铁律的最高形态：从"N 个信源报道了同一事件"升级为 **"叙事（talk/blog 的说法）与实证（真实 prompt/changelog diff）是否吻合"**。

### 19.1 三个真实变更源（新增适配器，fixtures 已存 tests/fixtures/real/）

| 适配器 | 源 | 消费文件 | 产出 |
|--------|----|----------|------|
| `adapters/cc_prompts.py`（勿与既有混淆，放 eyes/ 或 engine/ 皆可，建议 eyes/cc_prompts.py） | `Piebald-AI/claude-code-system-prompts` | `CHANGELOG.md`（raw） | 每个版本块 → 一条变更 item |
| `eyes/insights.py`（改指真实仓） | `zhoux77899/claude-code-insights` | `plugins/plugins-daily-insight.md`（7KB，**不取 31MB/75MB 的 history/repos.json**） | Top 榜插件/skill → item（业界资产） |
| `eyes/plugins_official.py`（新） | `anthropics/claude-plugins-official` | `.claude-plugin/marketplace.json` + `.github/bump-tracking.json` | 官方插件清单 + 版本 bump → 变更 item |

**cc_prompts CHANGELOG 解析要点**（fixture `ccprompts_changelog.md`）：块头 `#### [X.Y.Z](commit_url)` 或 `# [X.Y.Z](...)`；块内 `_+30,636 tokens_`（可负）= `token_delta`；条目按前缀分类 `**NEW:**` / `**REMOVED:**` / 无前缀=修改，冒号前是组件类型（System Prompt / System Reminder / Tool Description / Agent Prompt / Skill / Data）。item：title=`Claude Code {ver} 提示词变更 · {±N} tokens · {M} 项`、url=commit、summary=前几条变更、`extra={format:"changelog", subject:"claude-code-prompts", version, token_delta, changes:[{kind, component, title, desc}]}`、tags=[claude-code, prompts, changelog, practices]、weight 0.9。「No changes」块产 0 条或标记空。

**insights_daily 解析要点**（fixture `insights_daily.md`）：Markdown 表格 `| # | [repo](url) | ⭐stars | 🍴forks | date | desc |`。每行 → item：title=repo 名、url、metrics={stars, forks, rank}、summary=desc、`extra={format:"repo", subject:"plugin-ecosystem", rank}`、tags=[plugins, skills, insights, trending]、weight 0.6。

**plugins_official 解析要点**：`marketplace.json` 取 owner/plugins 列表（name+version）；`bump-tracking.json`（tiny）取近期 bump。item：官方插件版本变更。extra.format="changelog"、subject="plugins-official"。

### 19.2 `pipeline/change_intel.py`（绑定 + 叙事↔实证映射 + 资产演进）

- `extract_subject(item) -> str|None`：从标题/摘要抽「主题键」——模型/版本（fable-5/opus-5/sonnet-5/haiku、claude-code X.Y.Z）、特性词。changelog item 自带 `extra.subject/version`。
- `bind_changes(items) -> list[dict]`：把同一版本/主题的 **prompt 变更**（cc_prompts）与 **plugin 变更**（plugins_official）绑成一条「版本变更卡」（时间线）。
- `cross_map_claims(items) -> None`（核心）：对**叙事类** item（talk/video/blog/news，尤其含数字断言如「减少 80% / 提升 3x」）抽断言 → 找**同主题的实证类** item（changelog token_delta / prompt diff）→ 计算 `extra.corroboration = {claim, evidence, verdict}`，verdict ∈ `corroborated`（数字方向/量级吻合，如 talk 说 -80% 且 changelog 大幅负 token_delta）/ `unverified`（无实证）/ `contradicted`（相反）。命中 corroborated → badge `"verified"`（🔬 实证）。LLM 可选（抽断言、判定），**regex 回退**（抽 `\d+%|\dx|\d+倍`、token_delta 符号与量级比对），失败不阻塞。
- `industry_evolution(items) -> list[dict]`：从 insights item 提取**新入场/上升**项目（对比历史或就用当日 Top 榜 + 名称启发式），产出「业界资产演进」列表（multica/teams.ai/kanban.ai 这类）。

### 19.3 数字面呈现

- 新频道/版面 **变更情报**：Claude Code 提示词变更时间线（每版 token_delta + NEW/REMOVED 关键点）+ 官方插件 bump。
- **叙事↔实证** 在深读卡里高亮：某 talk 的断言旁挂 🔬 实证/存疑/矛盾 + 证据链接（呼应「Fable5 -80% 提示词」）。
- **业界资产演进** 版面：trending 新项目卡（stars/forks/rank + 一句话）。

### 19.4 归属与量级控制

Wave H 实现；**严禁整取 history.json(31MB)/repos.json(75MB)**，只用 daily-insight.md 与 changelog（engine.http 增 `max_bytes` 保护，超限截断并 warning）。测试 `tests/test_change_intel.py`（对 fixtures，离线；corroboration regex 回退、subject 抽取、changelog/insights/marketplace 解析、cross-map verdict 三态）。
