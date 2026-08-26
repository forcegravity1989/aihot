# 千里眼 qianliyan v0.3

**配置驱动的 AI 情报采集、交叉验证与变更情报流水线。**

千里眼把散落各处的 AI 动态收成一份可读的简报：多个信源适配器并行抓取 → 底座层按事件签名
合并去重、按新鲜度与交叉引用打分 → 加工层做频道路由、人物画像、**变更情报**与中文标注 →
入口层渲染 HTML 简报与深读/浅读日报并分发。全链路可离线运行，核心依赖只有 `pyyaml` 与 `requests`。

> **架构说明**：v0.3 起「五眼」只是历史代称，不再是组织原则。信源由 `config/sources.yaml`
> 的**分类型目录**驱动（rss / youtube / github-trending / scrape / arxiv…），每条 item 带
> `extra.format`（news/blog/video/talk/podcast/repo/paper/x/changelog）。加信源只改配置、不动代码。

## 三类信源

| 类别 | 看什么 |
|------|--------|
| **aihot · 卡兹克** | aihot.virxact.com 的 RSS（精选/全量/分类），原文 URL 内嵌 |
| **builders · zarazhang** | zarazhangrui/follow-builders 的 X 动态（带互动数据） |
| **目录源（sources.yaml）** | 官方 blog（OpenAI/DeepMind/HuggingFace/Anthropic）· YouTube 频道（Claude/Anthropic/OpenAI/DeepMind）· AI 播客 · GitHub trending · arXiv |
| **变更源** | Piebald 系统提示词 changelog · anthropics 官方插件市场 · zhoux77899 insights 日榜 |

## 王牌：变更情报（叙事 ↔ 实证）

千里眼不止聚合"谁说了什么"，还回答"**他说的被真实的代码/提示词 diff 证实了吗**"：
对含数字断言的演讲/博客（如"Fable 5 减少 80% 提示词"），去同主题的 changelog `token_delta`
比对方向与量级，标注 🔬 实证 / 存疑 / 矛盾。见 `pipeline/change_intel.py`。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 跑测试（全程离线，不产生任何真实网络请求）
.venv/bin/pytest -q

# 指定数据目录（可选；不指定则走四级解析链）
export QLY_DATA_DIR=~/qianliyan-data
```

要求 Python **3.9+**。可选能力按需安装：`pip install -e ".[api]"`（HTTP API）、
`pip install -e ".[browser]"`（内眼 CDP 抓取）。

### 数据目录怎么定（四级优先级）

```
env QLY_DATA_DIR  >  <repo根>/paths.local.json  >  config/paths.team.json  >  ~/qianliyan-data
```

两个 json 的格式都是 `{"data_dir": "~/some/path"}`。`paths.local.json` 是个人覆盖（已入
`.gitignore`）；`config/paths.team.json` 是团队默认的**唯一来源**——任何模块都不许写死个人路径。

数据目录下的关键产物：`items.jsonl`（去重打分后的统一池，对外数据契约）、`hotlist.md`
（全局热榜 top 50）、`channels/<name>.md`（各频道人读页）、`digest.html`（HTML 简报）、
`sync_meta.json`（最近一次同步的运行元数据）。

## 目录说明

```
qianliyan/
├── core/       底座层：路径解析 / item schema / 去重打分 / 读者画像与历史 / JSONL 存储 / LLM 客户端
├── engine/     抓取后端：HTTP、RSS、YouTube、GitHub trending、HTML scrape、YouTube 字幕、远眼调度、CDP
├── eyes/       信源适配器，每个暴露统一的 fetch(cfg, since) -> list[item]
├── pipeline/   频道路由、变更情报、人物画像、极简模板引擎、HTML 简报渲染、Agent 标注层
└── cli/        编排器与入口：sync / deliver / api_server / daily_digest_all / data_prune / health_check

config/         配置唯一权威层（sources / channels / builders / reader / model-sources / x-follows / paths.team）
templates/      简报模板（digest / glance 浅读 / deep 深读）
scripts/        兼容转发器（把 scripts/x.py 转给 qianliyan.cli.x）
tests/          pytest，全部离线；fixtures/real/ 存真实信源样本
docs/           intent.md · spec.md · spec-v0.3.md · plan.md · PLAYBOOK.md · overview.html
```

**分层铁律**：依赖方向只能是 `cli → pipeline → core` 与 `cli → eyes → engine → core`，
禁止反向依赖，`core` 不得 import 其他三层。

## 核心算法一览

- **去重签名**：标题做 NFKC → casefold → 剔除非字母数字非 CJK → 取前 50 字符后哈希；
  发版条目（`tags` 含 `release` 且有 `metrics.version`）改用 `源|版本号` 作基串，
  避免同一版本在双仓被误合并。
- **热度**：`weight × 0.5^(age_days/7) × (1 + 0.35·ln(1+cross_refs))`——7 天半衰期，
  被越多信源同时报道越热。
- **徽标**：`cross_refs ≥ 3` 打 📈 重磅；`weight ≥ 0.95` 且 24 小时内打 ⚡ 一手速报。

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `QLY_DATA_DIR` | 数据根目录 | 四级解析链兜底 `~/qianliyan-data` |
| `QLY_OFFLINE` | `1` = 禁一切出网（抓取抛错、LLM 不可用、OG 图跳过） | `0` |
| `QLY_AUTH_DIR` / `QLY_BROWSER_PROFILE` | 内眼登录态 / 浏览器 profile | `$data/auth` / `$auth/company-profile` |
| `QLY_POOL_MAX_AGE_DAYS` | 原始池过龄淘汰天数 | 空 = 不淘汰 |
| `QLY_GIT_SNAPSHOT` | `0` = 关闭数据目录的 git 快照提交 | `1` |
| `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` | 内网 LLM 网关 | 见 spec §12 / 空 |
| `QLY_HAIKU_MODEL` | 标注与翻译用的模型名 | `Qwen3.6-27B` |
| `QLY_TRANSCRIPT_PROXY` | YouTube 字幕转写代理（pod2txt / 自托管 youtube-transcript-api） | 空 = 走 CDP/直连 |
| `WEB_ACCESS_PROXY` / `QLY_CDP_PROXY` | 内眼与字幕的 CDP 浏览器端点 | `http://127.0.0.1:9333` |

完整清单见 `docs/spec.md` §12。**LLM 是增强项不是依赖项**：网关不可用时所有标注 Agent
一律走规则回退，绝不阻断主链路。
