# spec.md — 千里眼 (qianliyan) v0.2 功能规格

> SDLC 阶段二：实现合同。**所有实现必须精确遵循本文的目录结构、函数签名与算法定义。**
> 语言：Python，兼容 3.9（禁用 3.10+ 语法：match、`X | Y` 运行时注解等；文件头统一 `from __future__ import annotations`）。
> 依赖铁律：核心链路只允许 stdlib + `pyyaml` + `requests`；`fastapi/uvicorn`、`playwright` 必须以可选方式导入（缺失时给出清晰安装提示或降级，不得让核心链路 ImportError）。

---

## 0. 四层管道架构与模块边界铁律

```
[采集层 eyes/]   →   [底座层 core/]   →   [加工渲染层 pipeline/]   →   [入口分发层 cli/]
  五眼并行抓取        合并/去重/打分        频道路由+Agent标注+HTML        编排+API+多通道
  标准化 item          写盘 items.jsonl      渲染                            推送
```

| 层 | 只负责 | 不负责 |
|----|--------|--------|
| `eyes/` | 每只眼独立 `fetch()` 返回标准化 item list | 抓取细节实现（交给 engine） |
| `engine/` | 抓取后端实现（HTTP/RSS/Git/HTML/CDP） | 业务编排 |
| `core/` | 合并/去重/打分/JSONL IO/路径解析/LLM 客户端 | 信源语义 |
| `pipeline/` | 频道路由 + Agent 标注 + HTML 渲染 | 采集 |
| `cli/` | 编排器与入口 | 算法 |

**跨层依赖方向**：`cli → pipeline → core`；`cli → eyes → engine → core`。禁止反向依赖；`core` 不得 import 其他三层。

## 0.1 目录结构（文件归属的唯一权威）

```
aihot/
├── pyproject.toml
├── README.md
├── .gitignore
├── docs/                      # intent.md spec.md plan.md overview.html
├── config/
│   ├── paths.team.json        # 团队默认数据目录（唯一团队级来源）
│   ├── sources.yaml           # 远眼信源（唯一权威）
│   ├── channels.yaml          # 频道 match 规则
│   ├── builders.yaml          # 右眼大牛白名单
│   ├── model-sources.yaml     # 模型信源 tier 路由
│   └── x-follows.yaml         # X 关注/mute/mode
├── templates/
│   └── digest.html.jinja      # 简报模板（自研引擎语法，文件名沿用 .jinja 后缀）
├── qianliyan/
│   ├── __init__.py            # __version__ = "0.2.0"
│   ├── core/
│   │   ├── __init__.py
│   │   ├── paths.py
│   │   ├── schema.py
│   │   ├── utils.py
│   │   ├── storage.py
│   │   ├── og_image.py
│   │   └── llm_client.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── http.py
│   │   ├── rss.py
│   │   ├── html_page.py
│   │   ├── gitfeed.py
│   │   ├── remote_sync.py
│   │   └── cdp.py
│   ├── eyes/
│   │   ├── __init__.py        # EYES registry
│   │   ├── aihot.py
│   │   ├── builders.py
│   │   ├── company.py
│   │   ├── local.py
│   │   └── insights.py
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── minitpl.py
│   │   ├── channels.py
│   │   ├── report.py
│   │   ├── routing.py
│   │   ├── auto_translate.py
│   │   ├── model_cluster_agent.py
│   │   ├── headline_fit_agent.py
│   │   └── headline_cluster_agent.py
│   └── cli/
│       ├── __init__.py
│       ├── sync.py
│       ├── deliver.py
│       ├── api_server.py
│       ├── daily_digest_all.py
│       ├── data_prune.py
│       └── health_check.py
├── scripts/
│   ├── _compat.py             # 统一转发器
│   ├── sync.py deliver.py api_server.py daily_digest_all.py data_prune.py health_check.py
└── tests/
    ├── conftest.py
    ├── fixtures/              # 各眼原始 payload 样例 + mock item 池
    └── test_*.py
```

---

## 1. item schema（`core/schema.py`）

item 是贯穿全链路的 **plain dict**（不用 dataclass，便于 JSONL 直存），`schema.py` 提供构造与校验：

```python
REQUIRED_FIELDS = ("sig", "title", "url", "source", "source_kind", "backend", "weight", "date")

def make_item(*, title, url, source, source_kind, backend, weight,
              date=None, summary="", tags=None, metrics=None, extra=None) -> dict
    # 返回含全部字段的 dict；date 缺省用当前 UTC；自动调用 utils.item_signature 填 sig；
    # cross_refs=0, source_list=[source], hotness=0.0, badges=[],
    # sync_run_id="", fetched_at=当前 UTC ISO。
def validate_item(item: dict) -> list[str]   # 返回缺陷描述列表，空列表=合法
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `sig` | str | 去重签名，见 §2.1 |
| `title` / `summary` / `url` | str | summary 可空；url 必填（铁律 2） |
| `source` | str | 人类可读信源名，如 `Anthropic News` |
| `source_kind` | str | `aihot` \| `builders` \| `company` \| `local` \| `insights` |
| `backend` | str | `rest` \| `raw_json` \| `cdp` \| `rss` \| `git` \| `html` \| `sitemap` \| `arxiv` |
| `weight` | float | 0–1 信源权威度 |
| `date` | str | 内容发布时间，ISO 8601 UTC（`2026-08-25T03:00:00+00:00`） |
| `cross_refs` | int | 合并后 = len(source_list) - 1 |
| `source_list` | list[str] | 报道过该事件的信源名（有序去重） |
| `hotness` | float | 见 §2.3 |
| `badges` | list[str] | `"heavy"`（重磅）/ `"flash"`（一手速报），渲染层译为 📈/⚡ |
| `tags` | list[str] | 主题标签，小写 |
| `metrics` | dict | 数值指标（stars、rank、version…） |
| `extra` | dict | 松散字段：`category`（aihot）、`platform`、`og_image`、`lang`、`title_zh`、`summary_zh`、`cluster_key`、`story_key`、`headline_fit`、`tier`、`date_precision` |
| `sync_run_id` / `fetched_at` | str | 由 sync 编排器统一盖章 |

**`extra.date_precision`**（`day` / `unknown`，精确时不写）：`date` 的可信精度。源没给发布
时间时 `make_item` 会补当前时刻，一批条目于是全撞在同一秒；只给到天的源一律是
`T00:00:00`。两种情况若不标记，渲染层会把它们画成精确到分钟的假时刻，读者无从分辨
「04:26 发布」「这天发的、不知道几点」「根本不知道什么时候」。精度一旦丢在归一化这步
就再也补不回来，所以落库时就记下。**只标有损的**——缺省即精确，`extra` 初始为空这条
契约对绝大多数条目仍然成立。时间轴据此三档显示：`04:26` / `全天` / `—`，且日内把
有真实时分的排在前面，避免「补出来的时刻」冒充当天最新。

时间工具（放 `utils.py`）：`now_utc() -> datetime`、`iso(dt) -> str`、`parse_date(s) -> datetime|None`（容忍 ISO、RFC822、`YYYY-MM-DD`；解析失败返回 None，调用方兜底为 now）。

---

## 2. core 层算法（`core/utils.py`）——必须逐条精确实现

### 2.1 `item_signature(item_or_title, source=None, version=None) -> str`

- **release 特例**：若 item 的 `tags` 含 `"release"` 且 `metrics.version` 非空 → 签名基串 = `f"{source}|{version}"`（防同一版本号在双仓被误合并/漏合并）。
- **一般情形**：`normalize_title(title)` = `unicodedata.normalize("NFKC", title)` → `casefold()` → 删除所有非字母数字非 CJK 字符（正则 `[^0-9a-z一-鿿]+` 置空，注意先 casefold）→ 取前 **50** 个字符。
- 返回 `hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]`。

### 2.2 `dedup_and_score(items, now=None) -> list[dict]`

按 `sig` 分组，每组合并为一条：

- `source_list`：按组内出现顺序去重累积各 item 的 `source_list`（单条 item 的 source_list 至少含自身 source）。
- `cross_refs = len(source_list) - 1`。
- `title` / `url`：取组内 **weight 最高** 的 item 的（并列取先到者）。
- `summary`：取组内**最长**非空 summary。
- `date`：取组内**最早**可解析日期；全不可解析则用 now。
- `weight`：组内最大值。
- `tags`：并集（保序去重）；`metrics` / `extra`：浅合并，weight 高者的键胜出。
- `source_kind`/`backend`：随 title 取自 weight 最高者。
- `hotness = compute_hotness(weight, date, cross_refs, now)`。
- `badges`（重磅校准 v0.2）：`cross_refs >= 3` → append `"heavy"`；`weight >= 0.95 且 (now - date) <= 24h` → append `"flash"`。
- 返回按 `hotness` 降序。

### 2.3 `compute_hotness(weight, date_iso, cross_refs, now=None) -> float`

```
age_days  = max(0.0, (now - parse_date(date_iso)).total_seconds() / 86400)   # 解析失败按 0 天
freshness = 0.5 ** (age_days / 7.0)          # 半衰期 7 天
CROSS_BONUS = 0.35
hotness   = weight * freshness * (1 + CROSS_BONUS * math.log1p(cross_refs))
```

返回 `round(hotness, 4)`。**单调性质（写进测试）**：同 weight 下越新越热；同新鲜度下 cross_refs 越多越热。

---

## 3. 路径解析（`core/paths.py`）

### 3.1 `resolve_data_dir() -> Path`，四级优先级

```
env QLY_DATA_DIR  >  <repo根>/paths.local.json  >  <repo根>/config/paths.team.json  >  ~/qianliyan-data
```

- 两个 json 的格式：`{"data_dir": "~/some/path"}`，值做 `expanduser`。
- `paths.local.json` 在仓库根（**入 .gitignore**）；`paths.team.json` 是**团队默认唯一来源，任何模块禁写死个人路径**。
- repo 根定位：`Path(__file__).resolve().parents[2]`。
- 解析结果目录不存在则 `mkdir(parents=True)`。

同族解析器：

- `resolve_auth_dir()`：`env QLY_AUTH_DIR` > `data_dir/auth`
- `resolve_browser_profile_dir()`：`env QLY_BROWSER_PROFILE` > `auth_dir/company-profile`
- `config_dir() -> Path`：仓库 `config/`；`templates_dir()` 同理。
- `data_path(*parts) -> Path`：`resolve_data_dir().joinpath(*parts)`，自动建父目录。

### 3.2 数据底座目录布局（`$QLY_DATA_DIR/`）

```
raw_pool.jsonl        # 去重前原始池（增量合并的载体）
items.jsonl           # 统一池（去重打分后，对外数据契约）
sync_meta.json        # 最近一次 sync 的运行元数据
hotlist.md            # 全局热榜 top 50
translations.json     # 翻译缓存 {sig: {title_zh, summary_zh}}
channels/<name>.md    # 各频道人读页
channels.json         # {channel: [sig, ...]} 机器可读索引
digest.html           # 最新 HTML 简报
items/<date>/         # 每日原始抓取快照（eye 级 jsonl，供审计）
archive/<date>/       # 日报归档：digest-draft.json / prompt.md / digest.html
stories/ auth/ repos/ mirrors/ builder-avatars/ cache/   # 各自途用
feedback.jsonl        # API 反馈流水
```

### 3.3 `sync_meta.json` 格式

```json
{"run_id": "20260825T120000Z-ab12", "started_at": "...", "finished_at": "...",
 "eyes": {"aihot": {"ok": true, "count": 42, "duration_s": 3.1, "error": null}, ...},
 "totals": {"raw": 180, "deduped": 155, "heavy": 4, "flash": 2}}
```

---

## 4. 存储（`core/storage.py`）

```python
def read_jsonl(path) -> list[dict]            # 容忍坏行（跳过并 warning），文件不存在返回 []
def write_jsonl(path, rows) -> None           # 原子写：tmp 文件 + os.replace
def read_json(path, default=None) / write_json(path, obj)   # 同为原子写
def merge_pool_by_eyes(old_raw, new_items, ran_kinds, max_age_days=None, now=None) -> list[dict]
```

`merge_pool_by_eyes` 语义（**按 source_kind 增量**）：

1. 保留 `old_raw` 中 `source_kind not in ran_kinds` 的条目（没跑的眼数据不动）。
2. 丢弃 `old_raw` 中 `source_kind in ran_kinds` 的条目，换成 `new_items`（跑过的眼以本次快照为准）。
3. 若 `max_age_days`（来自 env `QLY_POOL_MAX_AGE_DAYS`）非空：按 `date` 淘汰过龄条目。
4. 返回合并池（供 `dedup_and_score` 消费）。

### og_image（`core/og_image.py`）

`enrich(items, top_n=30, cache_path=None, offline=False) -> None`（原地改）：对 hotness 前 top_n 且无 `extra.og_image` 的 item，GET 其 url（timeout 8s，浏览器 UA），正则抽 `<meta property="og:image" content="...">`；结果（含失败标记 `""`）写入 `cache/og_image.json`（key=sig）避免重抓。`offline=True` 或 env `QLY_OFFLINE=1` 时直接返回。一切异常静默吞掉。

---

## 5. LLM 客户端（`core/llm_client.py`）

```python
class LLMUnavailable(Exception): ...

class LLMClient:
    def __init__(self, base_url, api_key, model, timeout=60): ...
    @classmethod
    def from_env(cls) -> "LLMClient"
        # ANTHROPIC_BASE_URL 默认 "http://10.44.198.92:28701"
        # ANTHROPIC_API_KEY（无则 is_available()=False）
        # model = env QLY_HAIKU_MODEL 默认 "Qwen3.6-27B"
    def is_available(self) -> bool     # 有 key 且 QLY_OFFLINE!=1
    def complete(self, prompt, system=None, max_tokens=1024) -> str
        # POST {base_url}/v1/messages，Anthropic Messages 协议
        # headers: x-api-key, anthropic-version: 2023-06-01
        # 网络/HTTP 错误 → raise LLMUnavailable
    def complete_json(self, prompt, system=None, max_tokens=1024) -> dict|list
        # 剥 ```json 围栏后 json.loads；解析失败重试 1 次（提示"只输出 JSON"）；再失败 raise LLMUnavailable
    def batch_json(self, prompts, system=None, max_workers=4) -> list   # ThreadPoolExecutor，单条失败该位置为 None
```

**Agent 层公约**：所有 `pipeline/*_agent.py` 与 `auto_translate` 必须遵循——`is_available()` 为假或调用抛 `LLMUnavailable`/任意异常时，**走规则回退，绝不向上抛**（S4）。

---

## 6. engine 层

- `engine/http.py`：`get(url, timeout=15, headers=None) -> requests.Response`，统一 Chrome UA（`Mozilla/5.0 ... Chrome/126 Safari/537.36`）；`get_json(url)`；env `QLY_OFFLINE=1` 时抛 `OfflineError(RuntimeError)`。全模块唯一出网口（cdp 除外）。
- `engine/rss.py`：`parse(xml_text) -> list[dict]`，stdlib `xml.etree`，兼容 RSS2 与 Atom，产出 `{title, url, summary, date}`；`fetch(url)` = get + parse。
- `engine/gitfeed.py`：GitHub 仓库动态，**不 clone**：`releases(owner_repo)` 拉 `https://github.com/{owner_repo}/releases.atom`，`tags/commits` 同理；复用 rss.parse；release 条目 metrics.version 从标题抽（正则 `v?\d+[\w.\-]*`），tags 加 `"release"`。
- `engine/html_page.py`：`fetch_links(url, limit=20)` 通用页面抓取：`html.parser` 抽 `<a>`（标题启发式：链接文本 ≥ 8 字符、去导航噪声），返回 `{title, url}` list；相对链接补全。
- `engine/remote_sync.py`：远眼调度。`detect_backend(url, declared=None) -> str`：declared 优先；`arxiv.org` → `arxiv`；路径含 `sitemap` → `sitemap`；后缀 `.xml`/`.atom` 或路径含 `feed|rss` → `rss`；`github.com/{o}/{r}` → `git`；否则 `html`。`fetch_source(src_cfg) -> list[dict]` 按 backend 分发（arxiv 用官方 API `http://export.arxiv.org/api/query?search_query=...`，Atom 解析复用 rss.py；sitemap 抽 `<loc>` 前 20 条）。
- `engine/cdp.py`：`connect(cdp_url="http://127.0.0.1:9333")` 惰性 `import playwright`（缺失 → raise `CDPUnavailable`）；`connect_over_cdp` 复用常驻 Edge（首次人工 SSO，此后免登录）。`fetch_via_proxy(url)`：env `WEB_ACCESS_PROXY`/`QLY_CDP_PROXY`（默认 `http://127.0.0.1:3456`）回退通道，POST `/fetch` `{url}`；失败 raise。

---

## 7. eyes 层（五眼）

统一接口：每眼模块暴露 `fetch(cfg: dict, since=None) -> list[dict]`（item 用 `schema.make_item` 构造；**允许抛异常**，由 sync 统一捕获记账——单眼故障不外溢即可）。`eyes/__init__.py`：

```python
EYES = {"aihot": aihot.fetch, "builders": builders.fetch, "company": company.fetch,
        "local": local.fetch, "insights": insights.fetch}
```

**可测试性铁律**：每眼把「网络壳」与「解析」分开——`fetch()` 只做取数，解析放纯函数 `parse_payload(payload, cfg) -> list[dict]`，单测用 fixtures 直喂 parse。

| 眼 | source_kind | 数据来源与解析要点 |
|----|-------------|--------------------|
| 左眼 aihot | `aihot` | REST `https://aihot.virxact.com`（需浏览器 UA）。cfg 来自 sources.yaml 的 `aihot:` 段：`base_url`、`endpoint`(默认 `/api/news`)、`categories`（5 个，默认 `[llm, agent, dev, research, product]`）。逐 category 请求 `?category=X`；响应兼容裸 list 或 `{data|items|list: [...]}`；字段映射尽力而为（`title|name`, `url|link`, `summary|desc|description`, `date|publish_time|created_at`, category 存 `extra.category`）。weight 默认 0.75。 |
| 右眼 builders | `builders` | `config/builders.yaml`：`{repo, branch, data_path, default_weight: 0.8, builders: [{handle, name, weight?}]}`。拉 `https://raw.githubusercontent.com/{repo}/{branch}/{data_path}` JSON（兼容 list 或 `{tweets|items: []}`，字段 `handle|user|author`、`text|content`、`url|link`、`created_at|date`）。白名单过滤（builders 列表 + x-follows.yaml 的 mute 剔除）；title = 文本首 80 字符；tags `[x, builders]`；weight = builder 级覆盖或 default。 |
| 内眼 company | `company` | 心声 + 稼先。稼先是 Vue SPA + Uniportal SSO + 动态 grapKey → **CDP 复用常驻浏览器**方案：`engine.cdp.connect()` → 打开列表页 → 等待选择器 → 抽条目。所有选择器/URL 收敛为模块头部常量块（内网联调只改这里）。降级链：CDP 失败 → `fetch_via_proxy` → 仍失败**抛异常**（由 sync 记账）。tags `[company, internal]`，weight 0.85。 |
| 远眼 local | `local` | 读 `config/sources.yaml` 的 `sources:` 列表，逐源 `engine.remote_sync.fetch_source`，item 继承源的 `weight`/`tags`，source=源 name。单源失败记 warning 继续（远眼内部的"多源不互殃"）。 |
| 洞眼 insights | `insights` | `claude-code-insights` 仓库。env `QLY_INSIGHTS_PREFER_RAW=1` → raw.githubusercontent 直拉 cfg 指定 data 文件；否则 git clone/pull 到 `data/repos/claude-code-insights`（`git` 命令行，clone 失败自动回退 raw）。解析插件排行 JSON（尽力而为：list 内 dict 找 `name`、`installs|count|downloads`、`description`），rank 存 `metrics.rank`，tags `[plugins, insights]`，weight 0.6。 |

---

## 8. 配置体系（`config/`，唯一权威层）

### 8.1 `sources.yaml`（远眼唯一权威 + aihot 段）

```yaml
aihot:
  base_url: https://aihot.virxact.com
  endpoint: /api/news
  categories: [llm, agent, dev, research, product]
  weight: 0.75
insights:
  repo: anthropics/claude-code-insights      # 占位，可改
  branch: main
  data_paths: [data/plugins.json]
sources:
  - {name: Anthropic News,        url: "https://www.anthropic.com/news",                type: html, weight: 0.98, tags: [official, anthropic, models]}
  - {name: Claude Code Releases,  url: "https://github.com/anthropics/claude-code",     type: git,  weight: 0.97, tags: [official, claude-code, release]}
  - {name: OpenAI News,           url: "https://openai.com/news/rss.xml",               type: rss,  weight: 0.95, tags: [official, openai, models]}
  - {name: Google DeepMind Blog,  url: "https://deepmind.google/blog/rss.xml",          type: rss,  weight: 0.92, tags: [official, google, models]}
  - {name: HuggingFace Blog,      url: "https://huggingface.co/blog/feed.xml",          type: rss,  weight: 0.85, tags: [vendor, huggingface]}
  - {name: arXiv cs.CL,           url: "http://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=20", type: arxiv, weight: 0.7, tags: [research, arxiv]}
  - {name: Simon Willison,        url: "https://simonwillison.net/atom/everything/",    type: rss,  weight: 0.8,  tags: [kol, dev]}
  - {name: Latent Space,          url: "https://www.latent.space/feed",                 type: rss,  weight: 0.75, tags: [kol, podcast]}
```

### 8.2 `channels.yaml`

规则语义：一个频道一个 `match`；`match` 内可用字段（**块内 AND**）：`tags_any` / `tags_all` / `sources_include`（对 source_list 任一做大小写不敏感子串匹配）/ `keywords_any`（title+summary casefold 子串）/ `aihot_category` / `platforms`（对 `extra.platform`）/ `source_kinds`。需要 OR 用 `any_of: [块, 块]`。频道字段：`title`（中文显示名）、`limit`（默认 30）。

必配频道：`claude-code` / `models` / `model-dev` / `plugins` / `company-internal` / `x-watch` / `builders-live`（match 规则按语义自拟合理值，如 claude-code = keywords_any[claude code, claude-code] 或 tags_any[claude-code]）。

### 8.3 其余

- `paths.team.json`：`{"data_dir": "~/qianliyan-data"}`。
- `builders.yaml`：见 §7；预填 25 个占位大牛（karpathy、simonw、swyx、gdb、amanrsanger、alexalbert__ 等真实 handle，name 对应）。
- `model-sources.yaml`：`tiers: {official: 1.0, vendor: 0.9, dev: 0.8, kol: 0.7, community: 0.5}` + `mapping: {源名或子串: tier}`。
- `x-follows.yaml`：`follows: [{handle, mode: all|highlights, mute: false}]`。

配置加载工具（放 `core/paths.py` 或 `pipeline` 共用处均可，推荐 `core/paths.py`）：`load_yaml_config(name) -> dict`（从 `config_dir()` 读，文件缺失返回 `{}`）。

---

## 9. pipeline 层

### 9.1 `minitpl.py` — 极简模板引擎（**不依赖 jinja2**）

支持且仅支持：

- `{{ path }}`：点路径取值（dict key / list index / 属性），缺失渲染空串；默认 **HTML 转义**；`{{ path|safe }}` 不转义。
- `{% for x in path %} ... {% endfor %}`：循环变量注入上下文；支持嵌套。
- `{% if path %} / {% if not path %} ... {% endif %}`：truthiness 判断；支持嵌套。
- 实现方式：编译为节点树后渲染（不许用 eval/exec 执行模板内容）。
- 接口：`render(template_text: str, context: dict) -> str`。

### 9.2 `channels.py`

```python
def load_channels() -> list[dict]                      # 读 channels.yaml
def match_item(item, match_cfg) -> bool                # §8.2 语义
def route(items, channels) -> "OrderedDict[str, list[dict]]"   # 一条 item 可入多频道；频道内按 hotness 降序、截断 limit
def run_all(items) -> dict                             # route + 写盘：channels/<name>.md、channels.json；返回路由结果供 report 复用
```

`channels/<name>.md` 格式：`# {title}` + 更新时间 + 每条 `- [📈/⚡ ]**标题**（source_list 用 `+` 连接） hotness — [链接](url)`，中文标题优先用 `extra.title_zh`。

### 9.3 `report.py` — HTML 简报

`render_html(items, channel_map, out_path=None) -> str`：用 `minitpl` + `templates/digest.html.jinja` 渲染，写 `$QLY_DATA_DIR/digest.html`。**单文件自包含**（内联 CSS/JS，无外链），必须具备：

1. 版面：热榜 + 各频道分区（即"跨 10 个版面"），每条含标题（中文优先）、来源徽章（source_list）、时间、hotness、📈 重磅 / ⚡ 一手速报 标记、URL 链接。
2. **三排序按钮**：🔥 热度 / 🕒 时间 / 📈 得分（得分=weight），vanilla JS 基于内嵌 `<script type="application/json">` 数据或 data-* 属性原地重排。
3. **Ctrl+K Command Palette**：浮层输入框，模糊过滤所有版面的条目标题，回车跳转对应锚点/打开链接，Esc 关闭。
4. 大牛头像：`data/builder-avatars/{handle}.png` 存在则 base64 内嵌，否则首字母圆形占位。
5. 简体中文界面文案；浅色明快风格即可，不追求暗色模式。

### 9.4 `routing.py`

`load_tiers() -> (tiers: dict, mapping: dict)`（读 model-sources.yaml）；`classify_source(source_name) -> str`（mapping 子串匹配，默认 `community`）；`annotate(items)`：为 models 相关 item 写 `extra.tier`。

### 9.5 Agent 标注层（全部遵循 §5 回退公约）

| 模块 | 接口 | LLM 行为 | 回退规则 |
|------|------|----------|----------|
| `auto_translate.py` | `translate(items, cache_path) -> None` 原地写 `extra.title_zh/summary_zh` | 批量英→中（每批 ≤10 条，`batch_json`），命中 `translations.json` 缓存则跳过 | 已是中文（CJK 占比 > 0.3）直接跳过；LLM 不可用则不译（渲染层落回英文原文） |
| `model_cluster_agent.py` | `annotate(items) -> None` 写 `extra.cluster_key` | 模型族聚类 | 正则匹配已知族名（claude/gpt/gemini/llama/qwen/deepseek/mistral/grok/kimi/glm…）取首个命中 |
| `headline_fit_agent.py` | `annotate(items) -> None` 写 `extra.headline_fit` (0–1) | 头条适配度评分 | `min(1.0, hotness / max_hotness)` |
| `headline_cluster_agent.py` | `annotate(items) -> None` 写 `extra.story_key` | 同题聚合 | `story_key = sig`（各自成题） |

---

## 10. cli 层

### 10.1 `sync.py` — 主编排器

```
python -m qianliyan.cli.sync [--quick] [--eye NAME]... [--since ISO] [--no-html] [--strict] [--status] [--mock]
```

`run_sync(eyes=None, quick=False, since=None, no_html=False, strict=False, mock=False) -> dict`（返回 sync_meta）：

1. 确定要跑的眼（默认全部；`--eye` 可多次）。`--mock`：跳过真实抓取，从 `tests/fixtures/mock_items.jsonl` 读入并按眼过滤（S5 离线端到端），同时强制 offline。
2. `ThreadPoolExecutor` 并行调各眼 `fetch`，逐眼 try/except 记账（ok/count/error/duration_s）；`--strict` 时任一眼失败则最终 exit code 非 0（但仍完成其余流程）。
3. 抓取快照写 `items/<YYYY-MM-DD>/<eye>.jsonl`（审计用）。
4. `merge_pool_by_eyes(读 raw_pool.jsonl, 新 items, ran_kinds, env QLY_POOL_MAX_AGE_DAYS)` → 写回 raw_pool.jsonl。**ran_kinds 只含本轮抓取成功（ok=True）的眼**——失败眼保留旧池数据，故障 ≠ 空结果（S1）。
5. `dedup_and_score` → 盖章 `sync_run_id`/`fetched_at` → `og_image.enrich`（`--quick` 跳过）→ 写 `items.jsonl`。
6. `pipeline.auto_translate.translate`（`--quick` 跳过）→ `routing.annotate` + 三个 agent（`--quick` 跳过；全部有回退）。
7. `channels.run_all` → 写 hotlist.md（top 50）→ `report.render_html`（`--no-html` 跳过）。
8. 写 `sync_meta.json`；若 env `QLY_GIT_SNAPSHOT` != "0" 且 data_dir 是 git 仓库 → `git add -A && git commit -m "sync <run_id>"`（静默失败）。
9. `--status`：不跑 sync，读 sync_meta.json 友好打印后退出。

### 10.2 `deliver.py` — 四通道分发

```
python -m qianliyan.cli.deliver --channel in-chat|html|welink|email [--topic <频道名>]
```

- `in-chat`：读 `channels/<topic>.md`（缺省 hotlist.md）打印 stdout，头部附四条铁律提醒（Agent 转述遵循）。
- `html`：确认 `digest.html` 存在，打印路径；`--open` 时 `webbrowser.open()`。
- `welink`：**不直连 API**——打印结构化 instruction 块（目标群、当日精选条目、每条带 URL），供 Agent 接力 welink-controller skill。
- `email`：stub——打印"请使用 send-email skill"与建议正文，退出码 0。

### 10.3 `api_server.py` — FastAPI（可选依赖，缺失时报安装提示退出）

`create_app() -> FastAPI`；鉴权：若 env `QLY_API_KEY` 非空，所有端点要求 header `X-API-Key` 一致，否则 401。

| 端点 | 行为 |
|------|------|
| `GET /digest` | 返回 digest.html（HTMLResponse；无文件 404） |
| `GET /items?channel=&limit=50&since=` | 读 items.jsonl（+channels.json 过滤），JSON 返回 |
| `GET /hotlist` | hotlist.md 文本 |
| `GET /status` | sync_meta.json |
| `POST /sync` | BackgroundTasks 调 `run_sync(quick=True)`，立即返回 `{"started": true}` |
| `POST /feedback` | body `{sig, action: up|down|hide, note?}` 追加 feedback.jsonl |

main：uvicorn 监听 `QLY_HOST`(默认 `0.0.0.0`) / `QLY_PORT`(默认 `8787`)。

### 10.4 `daily_digest_all.py` — 日报编排（HTML 主路线）

```
--prepare   # 从 items.jsonl 取 hotness top 40 ∪ 各频道 top 5，写 archive/<date>/digest-draft.json
            # draft 条目 = item 精简字段 + {"selected": false, "editor_note": ""}
--check     # 校验 draft：存在、每条有 url、selected ≥ 1（--prepare 后 selected 全 false 属正常，提示待选稿）
--write-prompt  # 生成 archive/<date>/prompt.md：给 Agent 的选稿提示词（四条铁律 + draft 摘要 + 回写说明）
--finalize  # 读 draft 的 selected 条目；可选 LLM 精读增强 distill（每条补 kp/chain/pull/limits 字段，失败跳过）
--html      # 与 --finalize 连用：用 report 模板渲染精选版 → archive/<date>/digest.html 并复制为 data 根 digest.html
--date YYYY-MM-DD   # 缺省今天(UTC)
```

V2 PNG 路线：保留 `build_v2_png_prompt(draft) -> str` 函数壳，docstring 标注 **deprecated（由 HTML 路线取代）**，不再有下游。

### 10.5 `data_prune.py`

`--dry-run`（默认 True，`--yes` 才真删）`--keep-items-days N`（清 items/<date>/ 与 archive/<date>/ 中过龄目录）`--clear-ephemeral`（清 cache/、mirrors/；`QLY_PRUNE_EPHEMERAL=1` 等效）`--git-gc`（data_dir 为 git 仓库时 `git gc`）。逐项打印将删/已删列表。

### 10.6 `health_check.py`

逐信源探测：远眼各 url GET（10s 超时，2xx/3xx=ok）、aihot base_url、builders raw url、insights raw url；company 默认 skip（`--include-internal` 才测 CDP 连通）。输出对齐表格（信源/后端/状态/耗时/错误摘要）；`QLY_OFFLINE=1` 时全部 skip。exit code = 失败信源数（skip 不计）。

### 10.7 `scripts/`

`_compat.py`：`forward(module_name)` —— 把 `scripts/x.py` 的 argv 原样转给 `qianliyan.cli.x.main()`。每个 wrapper 三行以内。

---

## 11. 测试（pytest，**全部离线**，禁一切真实网络）

`tests/conftest.py`：公共 fixtures——`sample_items`（≥8 条、覆盖多源同题/release/中英文/无日期）、`tmp_data_dir`（monkeypatch `QLY_DATA_DIR` + `QLY_OFFLINE=1` + `QLY_GIT_SNAPSHOT=0`）。
`tests/fixtures/`：`aihot_payload.json`、`builders_payload.json`、`insights_payload.json`、`rss_sample.xml`、`atom_sample.xml`、`sitemap_sample.xml`、`mock_items.jsonl`（≥20 条、覆盖五种 source_kind、含同题多源与 24h 内高权重条目以触发两种 badge）。

| 文件 | 覆盖 |
|------|------|
| `test_utils.py` | 签名归一化（大小写/标点/前50字）、release 特例、dedup 合并全字段语义、hotness 单调性、badge 阈值边界 |
| `test_paths.py` | 四级优先级逐级覆盖（monkeypatch env + tmp json）、auth/profile 解析 |
| `test_storage.py` | jsonl 原子写读回、坏行容忍、merge_pool_by_eyes 增量替换与池龄淘汰 |
| `test_engines.py` | rss/atom/sitemap 解析 fixtures、detect_backend 全分支、gitfeed version 抽取 |
| `test_eyes.py` | 各眼 `parse_payload` 喂 fixtures 出标准 item、白名单/mute 过滤 |
| `test_channels.py` | 各匹配字段 + any_of + 多频道归属 + limit |
| `test_minitpl.py` | 变量/转义/safe/for/if/嵌套 |
| `test_report.py` | 渲染产物含三排序按钮、palette 标记、badge emoji、条目 URL |
| `test_agents.py` | 无 LLM 环境下四个 agent 的回退行为（不抛异常、字段落值） |
| `test_sync_mock.py` | `run_sync(mock=True)` 端到端：产出 items.jsonl/hotlist/channels/digest.html/sync_meta，单眼故障注入不中断（S1） |
| `test_api.py` | TestClient：各 GET 端点、API key 鉴权 401、feedback 落盘 |

---

## 12. 环境变量总表

| 变量 | 说明 | 默认 |
|------|------|------|
| `QLY_DATA_DIR` | 数据根目录 | 四级解析链兜底 `~/qianliyan-data` |
| `QLY_AUTH_DIR` / `QLY_BROWSER_PROFILE` | 内眼登录态 / Edge profile | `$data/auth` / `$auth/company-profile` |
| `QLY_OFFLINE` | 1=禁一切出网（engine.http 抛 OfflineError；llm 不可用；og 跳过） | 0 |
| `QLY_INSIGHTS_PREFER_RAW` | 1=不 clone insights 仓库 | 0 |
| `QLY_POOL_MAX_AGE_DAYS` | 原始池过龄淘汰天数 | 空=不淘汰 |
| `QLY_GIT_SNAPSHOT` | 0=关闭 data 目录 git commit | 1 |
| `QLY_PRUNE_EPHEMERAL` | 1=prune 时清临时文件 | 0 |
| `QLY_API_KEY` / `QLY_HOST` / `QLY_PORT` | API 鉴权与监听 | 空 / 0.0.0.0 / 8787 |
| `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` | 内网 LLM 网关 | `http://10.44.198.92:28701` / 空 |
| `QLY_HAIKU_MODEL` | 标注/翻译模型名 | `Qwen3.6-27B` |
| `WEB_ACCESS_PROXY` / `QLY_CDP_PROXY` | 内眼 CDP 回退代理 | `http://127.0.0.1:3456` |

## 13. 编码公约

- 每个模块头部一段中文 docstring 说明职责与边界；对外函数带简明 docstring。
- 日志用 stdlib `logging`（logger 名 `qianliyan.<module>`），不许 print（cli 的用户输出除外）。
- 网络调用全部显式 timeout；捕获异常时记录 warning 而非静默（og_image 除外，按 §4）。
- 不引入 spec 之外的三方依赖；不写死任何个人路径（S6）。
