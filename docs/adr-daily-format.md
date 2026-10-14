# ADR：日报版式——哪些深读、哪些浅读，原文图与提炼图各放哪，以什么形态发布

状态：**提议 · 已试水两轮**（待用户拍板）· 对应 issue [#67](https://github.com/forcegravity1989/aihot/issues/67) · 2026-10-14

## 0. 一句话结论

**一期日报 = 一页三部分，按读者肯花的时间从少到多排：① 看图知今天（头条与深读的原文图）② N 秒读完今天（每条一句、带主体、标深浅、看过打勾）③ 正文。正文按「条目」分三档密度（头条 1 / 深读 4±1 / 浅读其余），只有一种版式；深读只给「既重要、原料又厚」的条目；原文图做证据层、提炼产物做导读层，两者并排但各守其位。① 另出一张代码渲染的速读图 PNG 供转发，仍不做图片为主的产品。**

## 1. 要决定什么

10-13 这期正文 12 条 + 快讯 12 条。每条正文无论原料厚薄，都配齐「原文图 + 数字卡 + 逻辑链 + 梗 + 编辑短评」；深浅之分只落在「关注的方向展开、其余折叠」，`digest-final.json` 里没有任何按条目的深浅字段，编辑 Agent 也没被要求做这个判断。要决定三件事：

1. 一天 13+ 条里，哪些值得深读、哪些看过标题就算接收，规则是什么、谁来判。
2. 原文图（真实、信息完整、但密）和 coding agent 提炼图（逻辑链、数字卡、对比图：清楚、但有损、可能错）各放在什么位置。
3. 发布形态：纯文字、图、还是图文。

## 2. 现状测量（archive/2026-10-12 ~ 10-14 实看）

| 日期 | 正文 | 有正文图 | 仅封面/无图 | 自绘 chart | 逻辑链 | 快讯 |
|---|---|---|---|---|---|---|
| 10-12 | 11 | 3 | 8 | 2 | 11 | 10 |
| 10-13 | 12 | 8 | 4 | 4 | 12 | 12 |
| 10-14 | 14 | 9 | 5 | 4 | 14 | 10 |

- 原文图覆盖率 27%~64%，按天大幅波动。「只靠原文图做主视觉」在 1/3~2/3 的条目上做不到；只有 og 封面的条目，封面是品牌图，信息量为零，却占着卡片主视觉位。
- 提炼是满配的：10-13 的 12 条里逻辑链 12/12、梗 12/12、数字卡 10/12，**包括正文只有 584 字符（Blender 教程）和 930 字符（FrontierCode）的条目也被配了 4~5 步逻辑链和一句梗**。原料薄，提炼就只能靠补，补出来的东西没有原文可核。
- 页面其实已经有五级密度：侧栏「30 秒读完今天」（12 行 takeaway）→ 未关注方向折叠行（标题 + 一句 + 缩略图）→ 关注方向整卡 → 详情页（全部原文图 + 要点）→ 原文。缺的不是层级，是**按条目分配层级的规则**。
- 「深读」tab（deep.html）与日报 tab 内容同源，只是不分方向，没有独立含义。

## 3. 业界怎么做（2026-10-14 实读近期一期，细节与 URL 见 §9）

### 3.1 英文日报的共同骨架

| 刊物 | 分层 | 头条怎么定 | 配图 | 阅读时长 |
|---|---|---|---|---|
| The Rundown AI | 3 主稿（180–200 词，The Rundown / The details / Why it matters）+ 5 一句话 + 工具 | 编辑 | 每主稿 1 张**原图，标 Source: Meta** | 网页卡片 |
| smol.ai AINews | **1 条 Top Story**（2500+ 词，facts vs opinions）+ Twitter/Reddit 回顾 | 一天一件事；没有就标题写 "not much happened today" | 无图 | 覆盖声明「checked 544 Twitters」 |
| Axios AI+ | "1 big thing" + 若干短条，每条 Why it matters | 编辑 | 每条 1 图/图表 | **每期**「1,180 words, 4-minute read」 |
| Last Week in AI | 5 Top News（120–290 词）+ 20 一行链接分 5 类 | 编辑 | **原文图 + "Source: anthropic.com"** | 无 |
| The Batch | 4 篇深稿（600–900 词，What's new / How it works / Results / Why it matters） | 全深 | 每稿 1 图（原图或自绘），不署出处 | 每期「18 min read」 |
| TLDR AI | 5 栏 ~15 条**等大**，每条 30–80 词 | 无头条 | **无图** | 每条「(N minute read)」= 原文时长 |
| Import AI | 6 条等大 260–330 词，收尾 Why this matters | 无 | 无图 | 无 |
| Techmeme | 位置 = 重要性；每簇 1 标题 + 1–2 行 + More: 20–30 链 | 簇大小 | 每簇 1 原文缩略图 | 无 |
| The Neuron / Superhuman | 1 主稿 + bullets；3 头条各 60–70 词 | 编辑 | AI 插画 hero、猫图、meme | 「3 minutes a day」 |

四个模式：
1. **几乎都走「1 头条 → 3–5 中稿 → 一行链接」三级阶梯**。等大版式只有 TLDR / Import AI / The Batch，而它们要么全浅（TLDR）要么全深（Batch），都不做 12 条满配。
2. **Why it matters 是近乎通用的收尾**（Rundown、Import AI、Neuron、Batch、Axios）。我们的 `editor_note` 就是这个位置。
3. **配图三分**：无图（TLDR / smol.ai / Import AI / HN，恰恰是最技术、最高密度的几家）；原图 + 署名（LWiAI / Rundown）；自绘或 AI 插画（Neuron / Batch）。**没有一家系统性地为每条生成自己的解释图**；最接近的 The Batch 一期也只有一张自绘图。
4. 阅读时长两种：每期总时长（Axios、Batch）或每条原文时长（TLDR）。

### 3.2 中文日报

| 对象 | 每期条数 | 分层 | 配图 | 形态 / 时长 |
|---|---|---|---|---|
| **AIHOT 日报**（aihot.news/daily，第 176 期 10-14） | 正文 ≤12 + 快讯 ≤10 | **头条 1 → 今日看点 3 → 分版（模型/产品/行业/论文/技巧观点）→ 快讯一行** | **只有头条一张大图，用原文图**；正文只有信源头像；快讯无图 | 网页 / RSS / API / MCP / 飞书推送；「约 5 分钟读完」 |
| 36氪「8点1氪」（10-14） | ~36 | TOP3 → AI 最前沿 5 → 大公司 20 → 投融资… 按栏目不按深浅 | 转载版无图 | 公众号长文 ~4500 字 |
| 极客公园「极客早知道」（10-13） | 11 | 平铺，标题串 3 条暗示重点 | 1 封面 + 8 张新闻稿图，无图注 | 「9 分钟」 |
| 界面「AI早报」 | 6 | 一句导语 + 平铺 | 2~3 张新闻图，无图注无出处 | 网页 / APP |
| AIbase「AI日报」（10-13） | 8~9 | 平铺；每条标题 + 正文 + 3 条提要 | 8 条里 3 条有图，无图注 | 「每天三分钟」 |
| follow-builders（Zara Zhang） | 按信源 | X 每人 2~4 句；播客「Bottom line + 3 要点」 | **明确无图** | Telegram / 邮件；每条必须带直链，没链接就丢 |
| 「AI圈日报」播客 | 3 重点 + 分类简报 | 今日重点 3 → 分类简报 → 深一度 | 音频 | 每天 07:00，8~14 分钟 |
| 东谷探长 / 新浪「AI热点小时报」（AI 自动生成） | 15~20 | P0~P3 分级 | 无 | 网易号 / 新浪 |
| 量子位 / 机器之心 / 新智元 / 虎嗅 | 官网无日报栏目（公众号未核实） | — | 单篇题图 | — |

AIHOT 是唯一把门槛和常量全部公开、可以照着复现的版式（`docs/selection.md`、`reports/edition.ts`、`industry/prompts/selection-score.md`）：

- 规则成刊、不调模型：`MAIN_ENTRIES=12`、`FLASH_ENTRIES=10`、`PER_SOURCE=2`、`MEMORY_DAYS=7`；一件事只出一条，取最权威的一篇代表。
- 排序：`importance = score + 5·log2(1+参与方数) + 5（有官方原帖）− 6（跟进）`，第 1 条头条、第 2~4 条看点。前 7 期报过的事只有当事方自己有新动作或 ≥4 家信源才能回正文，否则降快讯。
- 打分五轴（实质 / 新信息 / 可信 / 共鸣 / 可用）按 7 种内容类型加权；合作 PR sig≤4、常规小版本 sig≤3、营销 sig≤2、「即将推出」nov≤3、汇总稿 sig≤3、标题正文不符总分 ≤30。门槛按信源分级：官方一手 60、准官方 65、媒体与个人 76，两次独立打分之和 ≥ 2×门槛。
- 模型只写总述和栏目导读（~90 字）；**生成文字里出现条目中没有的名称或数字，就改用模板句**。
- 作者公开的排版理由（changelog）：07-03 加「今日看点」和阅读时长是为了「重点更突出」；09-28 报纸化「头版放头条和配图，其余分版」；10-01「一件事只占一条，正文最多 12 条，其余放快讯」。
- 没有 AI 生图。头条图经图片代理加载；引用被撤回时封面和导读一起下线。

中文圈的观察：明确做「头条 / 正文 / 一行快讯」三层的只有 AIHOT；媒体日报基本平铺，靠标题串 3 条暗示重点。配图三派：只给头条配原文图（AIHOT）、新闻稿图随条走不标出处（极客公园 / 界面 / AIbase）、完全无图（36氪转载版 / follow-builders / AI 自动生成日报）。只有开源小工具在用 AI 生图或做 3:4 小红书信息卡。

### 3.3 原文图 vs 提炼图：证据

- **专业媒体从不让机器生内容**：Economist Daily chart、Axios、Visual Capitalist、OWID、FT 的图全是人从原始数据重绘，图下有 source line；「提炼」是人重设计，不是模型造内容。
- **AI 生图信息图**：Keck & Stöckl（AVI 2026）测 100 张 Nano Banana Pro 信息图，**99 张至少一处误导**（编数据、编码不一致、图型错、框架误导）。
- **代码渲染图也不干净**：Ford & Rios（EMNLP 2025 Findings）多 Agent 修复后执行失败降到 4.5%，但人工复核 100 张仍有 6 张幻觉；ChartInsighter 2025 测 LLM 读图写摘要，1083 句里 18% 幻觉，其中 22% 把趋势方向写反。Mermaid / UML 生成的共识是「能渲染 ≠ 结构正确」。
- **一句话结论是最危险的提炼产物**：Kong / Liu / Karahalios（CHI 2018/19）——读者记住的是标题传达的信息而不是图本身；标题一倾斜，同一张图读者得出相反结论。
- **LLM 最安全的位置是给原图写图注**：SciCap Challenge 里专业编辑更喜欢 GPT-4V 为原图写的图注胜过作者自己的。Stokes & Hearst 2022：读者最偏好「注释最多的图」，最不喜欢纯图或纯文。
- **认知负荷**：Mayer 的 coherence（去掉无关材料，23/23 实验，中位 d≈0.86）、signaling（d≈0.41）、spatial contiguity（文字贴着图）；seductive details 68 个实验 g≈−0.33——装饰性 hero 图、猫图、梗是净成本。
- **披露 vs 冒充**：被认为是 AI 做的图让媒体质量感知掉约 10 分（Adena 2025，UK 2870 人）；但不披露被抓到代价更大——Microsoft Learn 2026-02 用 AI 重绘 git-flow 图（箭头走样、生造单词、不署原作者）被撤。
- **阅读行为**：NN/g 眼动——每封 newsletter 51 秒，19% 通读、69% 扫读，读的是标题、首句、bullet、链接；Constant Contact 210 万封——≤3 张图 CTR 最好；Outlook 桌面默认拦外链图，Gmail 102KB HTML 截断。**文字优先、图可选**是硬约束，不是口味。

## 4. 决策

### D0 一页三部分：看图 → N 秒读完 → 正文（2026-10-14 第二轮加）

用户的判断：同一天的内容要给不同人群三种读法——看图的、扫一眼文字的、真读的。业界的首屏也都是「导入层」：The Rundown 开头 4 条、The Neuron 5 条一句话、AIHOT「今日看点」3 条；我们原来把它放在右栏 / 窄屏折叠，位置和价值不匹配。于是首页定为三个部分，顺序固定、没有 tab、没有右栏，三部分本身就是三层入口：

| 部分 | 内容 | 读者 | 规则 |
|---|---|---|---|
| **① 看图知今天** | 头条横幅 + 深读每条一格：日报页上那张原文主图 + 标题 + 一句话 + 「看这里」图注；没有信息图的深读放它数字卡里的第一个数（已逐字核对） | 看图的人、转发的人 | 只有头条与深读进图区（D1：浅读无图）；图是证据、一句话是导读；另出定宽 1080 的 `speed.png`（无头 Chrome 截 `speed.html`，代码渲染、不用图像模型），页首给下载链接，没有 Chrome 就只少这张图 |
| **② N 秒读完今天** | 每条一句，按版面顺序；行上标「深读 / 头条」，头条与深读加粗；右侧一个勾 | 扫一眼的人 | 秒数按实际字数算（400 字/分，取整到 5 秒），不写死「30 秒」；**一句话必须带事件主体**（简报新规则），结论里没有标题的专有名词就退回用标题；打勾 = 看过即接收，和正文行「标记已读」同一份状态，①②③ 三处同步置灰，history 记 received |
| **③ 正文** | 头条 / 深读 / 浅读，按方向分栏，只有一种版式（D1） | 真读的人 | 不再有「深读」tab：深读的全套提炼在详情页；`deep.html` / `timeline.html` 单视图文件照旧落盘，只作调试与 `/daily?view=` 之用 |

### D1 深浅按条目分三档，不按方向

每条正文带 `depth ∈ {lead, deep, glance}`，由编辑 Agent 在选稿时一并给出，规则回退时由代码判。页面密度随 depth 走；「关注的方向」只决定**顺序与分组**，不再决定密度。

| 档 | 条数 | 页面上有什么 | 字数预算 |
|---|---|---|---|
| **头条 lead** | 1（大日子 2；没有够格的就不设，页首写「今天没有大事」） | 标题 + 一句结论 + 原文图 ≤2 张（含解释图注）+ 数字卡 + 逻辑链 + **为什么重要**（editor_note）+ 详情/原文 | ~350 字 |
| **深读 deep** | 4±1（含头条合计 4~6） | 标题 + 一句结论 + **一个视觉位** + 为什么重要 + 详情/原文 | ~150 字 |
| **浅读 glance** | 其余正文（~6） | 标题 + 一句结论 + 来源/时间 + 原文；**无图、无逻辑链、无梗** | ~40 字 |
| 快讯 quick | ≤12，每方向 ≤4 | 标题 + 一句话（不变） | ~30 字 |

一期合计约 1700 字、5~6 分钟，页首标「本期 N 字 · 约 M 分钟」（Axios / The Batch / AIHOT 的做法）。这比 AIHOT「只有头条有图」多给深读各一张图，少于今天的 12 条满配。详情页不分档，照旧放全部原文图 + 要点 + 逻辑链。「深读」tab 从此有了独立含义：只列 lead + deep。

### D2 深读的准入：重要 × 原料，两个都要

深读是版面里最贵的位置，只给同时满足两条的条目：

**重要（任一）**：模型发布方向且 48h 内；`cross_refs ≥ 1` 或 heavy/flash 徽章；编辑按 `config/editorial.yaml` 判定「做 infra 的人今天会改选型/配置/排期」或「结论改变对能力边界/安全/实践的判断」；`personal_score` 进当日前 8。

**原料（必须）**：抓到正文且 `fulltext_chars ≥ 3000`，并且（有 `kind=article` 的原文图 **或** 原文里有 ≥2 个可照抄的数字）。原料不够的条目**无论多重要都只能浅读**——10-13 的 FrontierCode（930 字符）、Blender 教程（584 字符）就是反例：重要性够，但深读出来的逻辑链和梗全是补的。

**配额与覆盖**：每方向最多 2 条深读；同一机构/信源组最多 2 条（含头条）；关注的方向只要有够格的候选就至少给 1 条深读，没有够格的就不硬凑；教程、观点、客户案例默认浅读，除非带可复用的具体方法和数字。

**头条**：深读里编辑排第一的那条；要求有原文图或可比数据，否则宁可不设头条（smol.ai 的 "not much happened today"）。

### D3 原文图是证据层，提炼是导读层——并排、各守其位、永不互换

- **有原文图（`kind=article`）就必须用原文图做主视觉**，图下两行：① 解释图注「看这里：…」（LLM 写，指出看哪条线、哪根柱、哪个数），② 「图源：原文 · Fig. N」并链回原文。这是 LLM 最安全的位置（§3.3 SciCap、Stokes & Hearst）。
- **封面图（og:image）永远不上日报页**，折叠行的缩略图也不用；只在详情页头部可选。
- **提炼视觉（数字卡 / 逻辑链 / 自绘对比图）只做代码渲染的 HTML/SVG，数据来自结构化字段；永不生成栅格图、永不用图像模型**（中文乱码、编数、丢出处三连）。
- **每个数字必须在原文里逐字出现**，`_clean_stats` / `_clean_chart` 加校验：value 字符串（去单位后）在 fulltext 里找不到就丢弃该项，日志记一笔。逻辑链每步 ≤20 字，`evidence` 同样校验。
- **提炼视觉要和原图长得不一样并标注**：卡片角标「示意 · 编辑整理，数据以原文为准」。精致的提炼卡会被过度信任（§3.3 beauty-drives-trust），标注是必要的，不是可选的。
- 视觉位分配：

| 档 | 原文图 | 逻辑链 | 数字卡 | 自绘 chart | 梗 |
|---|---|---|---|---|---|
| 头条 | ≤2 张 + 解释图注 | 有 | 有 | 仅当原文无可用图 | 仅原文原话、注明谁说 |
| 深读 | 1 张 + 解释图注 | 无（详情页有） | 无原文图时顶上 | 无原文图且有可比数据时顶上 | 无 |
| 浅读 | 无 | 无 | 无 | 无 | 无 |

- **梗（meme）从「每条必有」改成「只有原文原话才有」**：它是第二句一句话结论，承担同样的误导风险却没有同样的价值。
- **Codex / baoyu 流程生成的提炼插图**（暖色矢量逻辑图，10-14 给站外博客做过）归同一个「示意」位：只在原文没有信息图的头条 / 深读上顶上，必须标「◔ 示意 · Codex 生成，数字照抄原文」，图里数字先过逐字核对，永远不替代原图、不算深读原料。实现：`--apply-depth` 的行里给 `synth_image`（本机 PNG 路径），以 `kind=synth` 挂到条目最前；原文有信息图的条目给了也不收。第二轮试水给 10-14 的四条无图条目（Argon 头条、Run:ai、Spyre Inductor、Character.ai）各生成一张，提示词 = 风格约束 + 日报提炼 + 原文全文。

### D4 发布形态：文字优先、图可选的 HTML 单页；不做图片为主的产品

- 主产品仍是 `digest.html`（artifact 发布 + 本地页）。每条在图被拦、图不加载时必须自洽：标题 + 一句结论 + 链接就是完整信息，图是加成。原文图下载到 `media/` 随页发布（#52 已做），`alt` 写一句结论而不是标题。
- 不做长图、不做小红书/LinkedIn 式轮播作为主产品：它们在平台上的互动率高是平台效应，不是理解效应；一卡一点的模板适合分享物料，不适合 12 条参考页。分享物料是 D0 的速读图 `speed.png`：整期一张、代码渲染、只用原文图 + 标题 + 一句话，是第一部分的副本而不是另一套内容。
- 一期图的总量压到 **4~6 张**（头条 ≤2 + 深读各 1），其余在详情页。
- 时长标注：页首每期总时长；条目上的「(N 分钟读)」改名为「原文 N 分钟」，避免读者以为是本条的阅读时长。

### D5 不做什么

- 不用 AI 插画 / hero 图 / 猫图（The Neuron 式）——违反 coherence，纯成本。
- 不为每条生成自己的解释图——业界没有一家这么做，证据说它错得多、被过度信任。
- 不做图片为主的发布形态。
- 不恢复「每条满配」。

## 5. 拿 10-13 那期套一遍

| 条目 | 方向 | 正文字符 | 原文图 | 判定 | 理由 |
|---|---|---|---|---|---|
| Meta 发布 Muse Wonder / Wonder Full | models | 5641 | 3 张（AA 指数图） | **头条** | 模型发布 + 第三方可比数据 + 原文图 |
| vLLM 双向 KV 传输 TTFT 降 3.3× | infra | 14472 | 4 张（架构 + 曲线） | **深读** | infra 改选型；原料最厚 |
| ReplaySSM 加速混合模型解码 | infra | 14609 | 4 张 | **深读** | infra 第 2 条（方向上限） |
| Anthropic 蒸馏攻击防御，提取量降 10~100× | industry | 13219 | 3 张 | **深读** | 安全实质披露 + 数字 + 图 |
| Anthropic 研究：小模型接近在 IoT 上自主复制 | research | 17455 | 3 张 | **深读** | 改变安全判断；原料厚 |
| Anthropic Dynamic Workflows 协调多 Agent | agent | 7147（字幕） | 3 张（现场照片，非信息图） | **深读（边界）** | Agent 方向需 ≥1 深读；无信息图 → 视觉位用数字卡；Anthropic 已占 3 席，编辑可降为浅读 |
| AWS priming 改造 Transformer | training | 15668 | 仅封面 | 浅读 | 训练方向未关注；无原文图、无可比数字 → 不够深读 |
| AlphaProof Nexus 攻克 9 个 Erdős 问题 | research | 1422 | 仅封面 | 浅读 | 原料薄（<3000） |
| Epic Games 转向 AI 先行 | industry | 3174 | 仅封面 | 浅读 | 行业动态无数据图 |
| Claude 向费城警方误报线索 | industry | 5989 | 仅封面 | 浅读 | 事件，一句话可接收 |
| Claude + Blender + UE 七步教程 | agent | 584 | 2 张截图 | 浅读 | 教程；原料 584 字符 |
| Cognition FrontierCode 1.1 上线榜 | agent | 930 | 2 张 | 浅读 | 原料 930 字符；今天的逻辑链是补的 |

结果：1 头条 + 5 深读 + 6 浅读 + 12 快讯，页面图 ≤7 张（今天 12 张主视觉 + 12 条逻辑链）。

## 6. 落地改动（实现另开 issue，按 wave 切）

1. **选稿**：`EDITOR_BRIEF` 增加 depth 判定与配额（D2 的文字版）；picks JSON 加 `depth`；`_parse_picks` / `_apply_picks` 落 `depth`；`_rule_picks` 回退按 D2 规则判（原料门槛 + 方向/信源配额）。`digest-final.json` 每条带 `depth`。
2. **写要点**：`BRIEF_BRIEF` 按 depth 分任务——lead/deep 产出 takeaway + figure_note（解释图注）+ stats；lead 另产 logic；glance 只产 takeaway；meme 仅原话。`_clean_stats` / `_clean_logic` 加「数字逐字在原文」校验。
3. **配图**：`_attach_figures` 不再把 cover 当候选上日报页（详情页可留）；figure 记录加 `note`（解释图注）。
4. **模板**：`glance.html.jinja` 按 depth 渲染三种行；头部加本期字数/分钟；提炼卡加「示意」角标；「(N 分钟读)」改「原文 N 分钟」。`deep.html.jinja` 只列 lead + deep。
5. **测试**：E2E——跑 `cmd_finalize` + `render_*` 对 fixture，断言 HTML 里 lead/deep/glance 的元素有无、封面图不出现、数字校验把编造值丢掉。
6. 不碰：详情页结构、方向分栏与关注开关、快讯。

## 6.1 试水（2026-10-14，10-12 / 10-13 / 10-14 三期重出）

实现见 #68（分支 `claude/aihot-daily-format-design-b10e20`，4 个提交）。编辑 Agent 的 `claude -p` 登录过期，三期的 depth / 主图 / 解释图注由会话内的 Claude 以 `--apply-depth` 手工给定；规则回退的判定也跑了一遍作对照。

| 期 | 规则判定（头条/深读/浅读） | 编辑定档 | 日报页图数（原 → 新） |
|---|---|---|---|
| 10-12 | 1 / 1 / 9 | 1 / 3 / 7 | 11 条主视觉 → 1 张（ExploitGym 头条无图，Sakana MASS 工作流图、700 份手稿标「无信息图」） |
| 10-13 | 1 / 3 / 8 | 1 / 5 / 6 | 12 → 5（Meta AA 指数图、vLLM TTFT 曲线、ReplaySSM 原理图、蒸馏 SWE-bench 柱图、IoT 四阶段图） |
| 10-14 | 1 / 5 / 8 | 1 / 5 / 8 | 14 → 2（Gemini 4 Argon 四张全是品牌横幅 → 标「无信息图」，头条改用数字卡 + 逻辑链 + 示意图；源码管理日合并量曲线、Argon ERQA 柱图） |

试水里改掉的两条实现细节：① 「关注」只排序、不再把没关注的方向整栏收成标题——否则 infra 读者默认看不到 vLLM 那条的深读；② 头条也只放一张主图，第二张常是竖排六联跑分长图，压到 440px 只剩一条。规则判定暴露的一个坑：AIHOT 聚合来的稿全算「AIHOT 的」，两条就占光同源额度，已改为按原文站点归并。

发布为三个独立 artifact（不动固定日报链接）：
- 10-12 https://claude.ai/artifact/C1mQL2HDYeKWGzthzegJxG
- 10-13 https://claude.ai/artifact/1N2zVCC5MNw8HP8F1ZraTG
- 10-14 https://claude.ai/artifact/Ac77tmropKJYBk1ASgUE23

### 第二轮（2026-10-14，三部分版式）

用户看了第一轮后定下「一页三部分」（D0），同三期原地重出，artifact 链接不变：

| 期 | ② 秒数（原「30 秒」） | 速读行退回标题的条数 | ① 图区 | speed.png |
|---|---|---|---|---|
| 10-12 | 85 秒 | 3 / 11（vLLM Semantic Router、Karpathy 认知鸿沟等：结论里没主体） | 头条无信息图 → 数字位；深读 3 格 | 1080 × 定高 |
| 10-13 | 75 秒 | 5 / 12 | 头条 AA 指数图 + 深读 5 格（AWS priming 无图 → ">99%"） | 同上 |
| 10-14 | 100 秒 | 3 / 14（Claude Code 提示词、DeepMind 防诈骗等——用户截图里点名的那两行） | 头条 Argon 无信息图 → 数字位；深读 5 格 | 同上 |

两条观察：① 「30 秒」确实虚——三期 75～100 秒，按字数标真实秒数后读者的预期才对；② 结论里丢主体的行每期 3～5 条，占四分之一，简报里加「必须带事件主体」这条规则后应降到零，代码里的标题回退只是兜底。另：定时任务（launchd `com.qianliyan.daily`）跑的是 main 分支的旧代码，10-14 的归档在当天 08:06 被它覆盖过一次（depth / 图注全丢），本轮用 `--apply-depth` 重新定档；分支合并前每天都会这样，合并后自愈。

## 7. 风险与回看

- 编辑 Agent 给 depth 可能偏「全深」（它今天就是这么做的）。配额在 prompt 里说一遍，`_apply_picks` 再硬裁一遍：超过 6 条 deep 时按 editor_rank 降档。
- 原料门槛 3000 字符是拍的；跑两周看被门槛挡下来的条目里有没有本该深读的，再调。
- 解释图注是新的 LLM 产物，同样会错；它的约束是「只能指图里有的元素」，校验办法是图注里的数字也要在原文出现。
- 一个月后回看三件事：深读条数分布、被校验丢掉的数字占比、用户点原文 vs 点详情页的比例。

## 8. 参照

- 本仓已做的版式决定：#21（Techmeme 多信源 + TLDR 文本流）、#48（一句话结论 + 梗 + 逻辑图）、#49（方向分栏，参照 TLDR AI）、#51（侧栏导读 + 30 秒速览）、#52（原文图主视觉）。本 ADR 在 #49 / #52 之上加「按条目分档」，并收紧 #48 的梗与逻辑链。
- 口味与铁律：`config/editorial.yaml`（选稿标准、禁用词、数字照抄）。

## 9. 调研来源

### 英文日报（2026-10-13/14 期实读）
- TLDR AI https://tldr.tech/ai/2026-10-13 ；创始人选稿标准「不会转发到同事 Slack 的别放」https://inc-1-smart-business-story.beehiiv.com/p/the-man-silicon-valley-reads-every-morning
- The Rundown AI https://www.therundown.ai/articles/meta-watermelon-ripens-into-a-frontier-model
- smol.ai AINews https://news.smol.ai/issues/26-09-03-gpt-6-astra ，https://news.smol.ai/issues/26-09-08-navier-stokes
- Import AI #476 https://importai.substack.com/p/import-ai-476-creativity-and-rsi
- The Neuron https://www.theneurondaily.com/p/google-tested-amie-with-98-patients
- Superhuman AI https://www.superhuman.ai/p/meta-drops-two-new-models-topping-fable-astra
- Last Week in AI #346 https://lastweekin.ai/p/last-week-in-ai-346-719-math-manuscripts
- The Batch #374 https://www.deeplearning.ai/the-batch/issue-374/
- Techmeme https://www.techmeme.com/about ；Axios Smart Brevity https://www.journalism.co.uk/how-axios-is-reinventing-text-journalism-with-smart-brevity/ ；Hacker Newsletter #806 https://buttondown.com/hacker-newsletter/archive/806/
- 未核实：AlphaSignal 2026 版式、Axios AI+ 2026 条数、Stratechery 图表用法（付费墙 / 403）。

### 中文日报（2026-10-13/14 期实读）
- AIHOT 日报 https://aihot.news/daily ；成刊规则 https://github.com/KKKKhazix/AIHOT/blob/main/docs/selection.md ；更新日志 https://aihot.news/changelog
- 36氪 8点1氪（网易号转载）https://www.163.com/dy/article/L96JV5IE05118DFD.html ；极客早知道 http://www.geekpark.net/news/372264 ；界面 AI早报 https://m.jiemian.com/article/14180046.html ；AIbase 日报 https://www.aibase.com/zh/daily/31608
- follow-builders https://github.com/zarazhangrui/follow-builders ；歸藏 AIGC 周刊 https://quaily.com/op7418 ；「AI圈日报」播客 https://podcasts.apple.com/us/podcast/id1872685556
- 小红书信息卡工具 https://github.com/geekjourneyx/ai-daily-skill ；AI 生图日报 Hello-devtech/daily-ai-opensource
- 未核实：量子位 / 机器之心 / 新智元公众号是否有日报；歸藏、宝玉在 X 上的每日贴版式；小红书具体账号。

### 视觉与认知证据
- Keck & Stöckl, When AI Lies with Charts (AVI 2026) https://pure.fh-ooe.at/de/publications/when-ai-lies-with-charts-misleading-infographics-in-text-to-image/
- Ford & Rios (EMNLP 2025 Findings) https://arxiv.org/pdf/2506.06175 ；ChartInsighter https://arxiv.org/pdf/2501.09349 ；MermaidSeqBench https://arxiv.org/pdf/2511.14967
- Kong, Liu, Karahalios (CHI 2019) https://mail.zcliu.cs.umd.edu/vistitles/degree-of-misalignment-CHI19.pdf
- Stokes & Hearst 2022 https://arxiv.org/pdf/2209.10789 ；SciCap Challenge https://arxiv.org/pdf/2310.15405
- Mayer, Cambridge Handbook of Multimedia Learning（coherence / signaling / spatial contiguity）；Sundararajan & Adesope 2020（seductive details）https://news.wsu.edu/press-release/2020/03/19/seductive-details-inhibit-learning
- Segel & Heer, Narrative Visualization https://idl.uw.edu/papers/narrative ；Bateman, Useful Junk? (CHI 2010) https://vis.csail.mit.edu/classes/6.859/readings/pdfs/Bateman-UsefulJunk.pdf
- Adena 2025（AI 图披露降信任）https://warwick.ac.uk/fac/soc/economics/research/centres/cage/manage/publications/wp782.2025.pdf ；Microsoft Learn AI 重绘图撤稿 https://gigazine.net/gsc_news/en/20260219-microsoft-ai-slop-git-branching-model ；Ilograph 对 LLM 画图的测试 https://www.ilograph.com/blog/posts/diagrams-ai-can-and-cannot-generate/
- 组合范式：OWID Data Insights https://ourworldindata.org/launching-data-insights ；FT Burn-Murdoch https://gijn.org/stories/data-visualization-storytelling-tips-john-burn-murdoch/ ；Axios 可视化 https://nightingaledvs.com/behind-the-scenes-with-axios-data-visualization/
- 邮件/阅读：NN/g newsletter 眼动（经 https://wyliecomm.com/2022/08/skim-scan-read ）；Constant Contact 图片数 https://www.constantcontact.com/blog/?p=41456 ；Litmus 图片拦截 https://www.litmus.com/blog/the-ultimate-guide-to-email-image-blocking
- 未找到：原文截图 vs 重绘图对信任的直接对照研究；阅读时长标签的严谨数据。
