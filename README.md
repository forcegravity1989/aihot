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

## 定时抓取

抓取跑在 launchd 上（macOS），每天 **07:30** 一轮，给编辑留出选稿时间。

```bash
# 安装
DATA=$(.venv/bin/python -c 'from qianliyan.core import paths; print(paths.resolve_data_dir())')
sed -e "s|__REPO__|$PWD|g" -e "s|__DATA_DIR__|$DATA|g" \
    scripts/com.qianliyan.daily.plist > ~/Library/LaunchAgents/com.qianliyan.daily.plist
launchctl load ~/Library/LaunchAgents/com.qianliyan.daily.plist
```

`launchctl start com.qianliyan.daily` 手动触发一轮；`launchctl list | grep qianliyan`
看状态（第二列是上次退出码）；`launchctl unload ...` 停用。改时间就改 plist 里的
`StartCalendarInterval` 再 unload/load 一次。

**为什么不是 crontab**：这是会合盖休眠的笔记本。到点时机器睡着，cron 那一轮就永久
错过、第二天才有数据；launchd 会在唤醒后补跑。对「每天抓一次」，差别就是今天有没有日报。

调度入口是 [`scripts/qly-daily.sh`](scripts/qly-daily.sh)，做三件事：

| 步骤 | 说明 |
|------|------|
| `sync` | 抓取 → 去重打分 → 富化 → 渲染简报 |
| `daily_digest_all --prepare` | 备好当日选稿草案 |
| 日志 | `$QLY_DATA_DIR/logs/daily-<日期>.log`，保留 30 天 |

**选稿与定稿刻意不自动做**——那两步需要判断力，是编辑（人或 Agent）的活，自动跑只会
产出一份没人看过的日报。草案备好后由编辑选条目、写 `editor_note`，再 `--finalize --html`。

退出码语义（决定要不要报警）：

| 码 | 含义 |
|----|------|
| 0 | 当日数据拿到了（主信源 aihot OK） |
| 1 | 抓取失败 / 主信源挂了 / 全部眼失败——需要人看一眼 |
| 2 | 上一轮还在跑，本轮跳过——不是错误 |

**不用 `sync --strict`**：内网 `company` 眼在没有 CDP 浏览器的机器上天天失败，`--strict`
会让每一轮都非零退出，告警很快被当噪音忽略。判据改成「主信源有没有拿到数据」，
那才是「今天的日报有没有原料」这个真问题。

## 云端信源哨兵

抓取跑在本机（launchd），**盯梢跑在云端**（Claude routine
[`千里眼 · GitHub 信源哨兵`](https://claude.ai/code/routines/trig_01TSB1SbHWxg7uaVynxuQ7Q7)，
每天 08:00 London / 07:00 UTC）。两层分工是被约束逼出来的：

| | 跑在哪 | 为什么只能在这 |
|---|---|---|
| 抓取 | 本机 launchd | 产物要落 `$QLY_DATA_DIR`，云端 agent 碰不到本机文件系统 |
| 盯梢 | 云端 routine | 不依赖本机，笔记本合盖照跑；而本机 cron 失败只写日志、没人看 |

**云端沙箱的出网被 egress 策略卡死**——实测连 `example.com`、`arxiv.org` 都是
`CONNECT tunnel failed 403`，只有 `api.github.com` 通。所以哨兵**不跑 `health_check`**
（那会把十几个源全报 FAIL，是它自己出不了网，不是信源挂了），只盯四个住在 GitHub 上的信源：

| 眼 | 仓库 | 路径 |
|---|---|---|
| insights | `zhoux77899/claude-code-insights` | `plugins/plugins-daily-insight.md` |
| cc_prompts | `Piebald-AI/claude-code-system-prompts` | `CHANGELOG.md` |
| plugins_official | `anthropics/claude-plugins-official` | `.claude-plugin/marketplace.json` |
| builders | `zarazhangrui/follow-builders` | 见 `config/builders.yaml` |

范围小，但覆盖的恰好是**最容易静默坏掉**的那类：文件被改名/移位/改 schema，本地抓取会
静悄悄少一块数据，没人会发现。哨兵每天做两件事——确认路径还在，以及把线上内容喂给
仓库里那个真实的解析器，和 `tests/fixtures/real/` 的基线样本比**条目数与字段非空率**。

**一切正常就什么都不做**（不开 issue、不留评论）。有问题才开一条 `信源哨兵 · <日期>` 的
issue，且先查重——已有未关闭的就只追加评论。哨兵是只读的：不改代码、不开 PR、不 push。

> 由来：`health_check` 探测主信源用的一直是迁移前的 REST 接口，天天返回 404 被当噪音
> 忽略，直到 2026-08-31 才发现——而那期间主信源其实是好的。**误报比漏报更有害**，
> 所以哨兵的判据写得保守，且明确写了「不把自己出不了网当成信源故障」。

## 给云端 routine 开放网络

云端环境默认是 **Trusted** 级别——只放行包管理器 / GitHub / 云 SDK，`aihot`、YouTube、
各家官方 blog 全在墙外。网络级别是**每个环境自己的设置**（None / Trusted / Full / Custom），
你自己就能改，没有组织级白名单可推送。

出网域名清单从探测目标同源导出，加信源后重跑一次即可：

```bash
.venv/bin/python -m qianliyan.cli.health_check --print-domains
```

把输出填进 **claude.ai/code 消息框上方的云图标 → 环境设置 → Network access → Custom →
Allowed domains**，并勾选「Also include default list of common package managers」
（否则 pypi / GitHub 会被一起关掉）。环境选择器**没有设置页也没有直达 URL**，只能在 UI 点。

> 建议单独建一个环境给哨兵用，把 Default 留在 Trusted：哨兵只读公开 feed，放宽它一个就够，
> 不必让所有云端会话都能出网。

## HTTP 服务

服务同样由 launchd 托管（`KeepAlive` 崩溃自动拉起、开机自启），不再挂在一次性 shell
会话里——会话一结束服务就没了，重启也不回来。

```bash
DATA=$(.venv/bin/python -c 'from qianliyan.core import paths; print(paths.resolve_data_dir())')
sed -e "s|__REPO__|$PWD|g" -e "s|__DATA_DIR__|$DATA|g" \
    scripts/com.qianliyan.api.plist > ~/Library/LaunchAgents/com.qianliyan.api.plist
launchctl load ~/Library/LaunchAgents/com.qianliyan.api.plist
```

监听 `127.0.0.1:8787`。**只绑本机是刻意的**：`QLY_API_KEY` 未设时所有端点无鉴权，
绝不能暴露到局域网；要对外先设 `QLY_API_KEY`。

主要端点：`/daily`（三视图首页）、`/daily?view=glance|timeline|deep`、`/story/<sig>`
（单条详情页）、`/digest`（全池简报）、`/items`、`/hotlist`、`/status`。

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
