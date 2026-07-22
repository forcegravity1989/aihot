# 瓶颈定位 · 2026-07-22(优化师阶段)

> 方法:通读全部 8 个源码文件(464 行,见 `docs/baseline.md` 代码基线)+ `grep` 交叉核验
> 每个字段的真实消费者。以下每条都附具体文件:行,不是泛泛而谈。本阶段只定位、不改代码
> (改动留给下一棒的构建阶段)。按影响从高到低排序。

## 1.〔最高〕HN 单条目详情抓取 = 全链路耗时的绝对大头

**位置**:`aihot/sources/hackernews.py:56`(`ThreadPoolExecutor(max_workers=16)`)+ `config.json`(`hackernews.fetch_limit: 200`)

**证据**(`docs/baseline.md` 2026-07-20 真实测量,未重新编造):

| 阶段 | 耗时(s) | 占总耗时 |
|---|---|---|
| HN fetch | 66.02 | **89%** |
| arXiv fetch | 8.26 | 11% |
| filter+score+dedup | 0.01 | ~0% |

`hackernews.fetch()` 先拉 1 个 story-id 列表(`topstories.json`,快),再对每个 story id 单独
发一次 `urllib.request.urlopen` 请求详情(`hackernews.py:16` `_get_json`,`hackernews.py:20-37`
`_fetch_item`)——200 个 id 就是 200 次独立 HTTPS 请求,`urlopen` 不复用连接(每次新建
TLS 连接,无 keep-alive/连接池)。`max_workers=16` 是 HN 数据源原型阶段(`fe054f5`)定的值,
此后从未随 `fetch_limit`(100→200,见 `docs/SPEC.md` 假设 2 核验)调整过。

**为什么是"扛得住更多人用"的关键**:filter/dedup/render 全部是 O(n) 且 <0.01s,不是瓶颈;
系统能不能撑住更多关注面配置(更多 `story_lists`、更高 `fetch_limit`)完全取决于这 66s。
这是本阶段唯一算得上"性能可疑点"的真实数据支撑项,其余都是代码结构层面的观察。

**未验证**:16→更高并发度是否会触发 HN API 限流,需要真实压测,本阶段未做(定位阶段职责
是指出瓶颈,不是动手改)。

## 2.〔可删〕arXiv 条目的 `id` 字段与 `url` 字段值完全重复

**位置**:`aihot/sources/arxiv.py:50-51`

```python
"id": arxiv_id,
...
"url": arxiv_id,
```

两个字段被赋了**同一个变量**,即同一条目里 `item["id"] == item["url"]`,逐字符相同。不是
"可能重复"而是代码写死的恒等。`id` 字段在全链路(`filter.py`/`dedup.py`/`render.py`/`main.py`)
无任何消费者(见下条),对 arXiv 条目而言更是纯粹的字节冗余。

## 3.〔可删〕`id` / `time` / (HN)`score` 三个字段全链路无消费者

**位置**:写入处 `aihot/sources/hackernews.py:32-36`、`aihot/sources/arxiv.py:50,55`;
消费处交叉核验 `grep -rn '\["id"\]\|\.get("id"\|\["time"\]\|\.get("time"' aihot/`(排除
`sources/` 自身)→ **0 处命中**。

- `id`:两个源都写入,`dedup.py` 用的是**归一化标题**做判重键(`dedup.py:22`),不是 id;
  `render.py` 也只读 `title`/`url`/`summary`/`matched_keywords`。
- `time`:两个源都写入(HN 是 unix 时间戳,arXiv 是 ISO 发布时间——**类型都不统一**),
  但 `render.py`/`main.py` 都不读取、不用于排序(排序键是 `filter.py:19` 的 `match_score`)。
- `score`(HN 热度分):`hackernews.py:35` 写入 `item.get("score", 0)`,同样无消费者;
  `filter.py` 的排序用的是关键词命中数 `match_score`,不是这个原始热度分。

这三个字段是抓取阶段做的功、渲染阶段完全不用的数据,是"只加不删"的典型残留——每次抓取
200+40 条目都在序列化/传递这些从未被读取的字段。**注意**:`docs/SPEC.md` §3 把
`id`/`time`/`score` 列为"数据源契约"的一部分,删除前需要先确认是否有 SPEC 之外的消费方
(如未来的排序/去重升级打算用 `time`),不能在没有决策的情况下直接删——本阶段先如实标注,
决策留给下一棒。

## 4.〔低〕`render.py` 摘要截断 + 省略号逻辑重复两处

**位置**:`aihot/render.py:35-36`(Markdown,160 字符)与 `aihot/render.py:51-52`(HTML,220 字符)

```python
snippet = it["summary"][:160]
lines.append(f"  > {snippet}{'…' if len(it['summary']) > 160 else ''}")
...
snippet = html.escape(it["summary"][:220])
summary_html = f'<p class="summary">{snippet}{"…" if len(it["summary"]) > 220 else ""}</p>'
```

同一个"截断到 N 字符 + 超长加省略号"的逻辑写了两遍,阈值不同(160/220)且没有命名常量。
影响小(4 行左右),但符合"重复逻辑"标准,值得下次改 `render.py` 时顺手提成一个
`_truncate(text, limit)` 小函数,不需要单独立项。

## 5.〔低〕arXiv 抓取按分类顺序阻塞抓取,未并发

**位置**:`aihot/sources/arxiv.py:26-34`(`for cat in categories: ... urlopen(...)`)

`hackernews.py` 对 200+ 个 item 用了 `ThreadPoolExecutor` 并发(`hackernews.py:56`),但
`arxiv.py` 对 `categories`(当前配置 3 个:`cs.AI`/`cs.CL`/`cs.LG`)是顺序阻塞请求。当前
耗时 8.26s(占总耗时 11%),相对第 1 条影响小得多,但架构上不一致——两个源用了两种并发策略,
是同一类"HTTP 拉取"代码里唯一一处结构不统一的地方。优先级低于第 1 条,列出供下一棒参考。

## 未发现的问题(如实记录,避免"找不到就硬凑")

- 未发现算法复杂度问题:`dedup.py`/`filter.py`/`render.py` 全部是 O(n) 或 O(n·k)(k=关键词数,
  当前 15 个,可忽略),配合基线里 <0.01s 的实测耗时,排除代码逻辑本身是瓶颈的可能。
- 未发现无用依赖:项目零 pip 依赖(纯 stdlib),没有"引入了用不到的第三方包"这类问题。
- 未发现测试代码膨胀:`tests/` 465 行 vs 源码 464 行,接近 1:1,`test_main.py`(183 行,全仓最大
  的测试文件)行数集中在 6 个独立场景(配置错误/0 命中/正常出报/telemetry 计数/连续天数/
  日期覆盖),每个场景都对应 `docs/SPEC.md` 里一条真实契约,不是复制粘贴膨胀。

## 排序结论 / 建议优先级

**第 1 条(HN 并发抓取)** 是唯一有真实性能数据支撑、且直接决定"能不能扛住更多人用"的瓶颈,
应作为下一阶段(构建/优化实施)的**唯一高优先级技术项**。第 2、3 条是"可删"候选,响应本阶段
"只优化不删减是警报"的戒律,但因为 §SPEC 3 已把这些字段写进契约,删除前需要先过一遍
"是否真的没有任何未来用途"的决策,不能本阶段直接删代码。第 4、5 条是顺手项,不值得单独立项。
