# intent.md — 千里眼 (qianliyan) v0.2 重写意图

> SDLC 阶段一：为什么做、做成什么样算成功。本文不含实现细节（见 spec.md）。

## 一句话定位

**"五眼合一 + 交叉验证 + 频道切片 + 多通道分发"**：把异构 AI 信源（公开资讯、大牛动态、公司内网、自托管订阅、插件生态）熔合成一份带多源加权标记的简体中文 AI 简报。

## 要解决的问题

1. **信源碎片化**：AI 领域高价值信息散落在 REST API、GitHub 仓库、RSS、公司内网 SPA、X 动态等异构渠道，人工逐一巡查成本高、易漏。
2. **噪声与可信度**：单一来源的消息可信度存疑；**同一事件被多个独立信源报道**本身就是最强的重要性信号，需要机器自动做交叉验证与加权。
3. **消费场景多样**：同一份数据要供人读（Markdown 频道页 / HTML 简报）、供 Agent 转述（in-chat）、供系统集成（HTTP API）、供群推送（WeLink / 邮件）。

## 核心价值主张（设计哲学四条铁律）

1. **以本地数据为准，不凭记忆**——一切转述都必须来自落盘的数据底座。
2. **每条必带 URL**——可溯源是底线。
3. **简体中文 + 人话**——面向中文读者的最终呈现。
4. **交叉验证是核心价值**——多源报道自动加权（cross_refs → hotness 加成 → 重磅标记）。

## 成功标准（验收口径）

| # | 标准 | 验证方式 |
|---|------|----------|
| S1 | 五眼中任意一只故障，其余眼照常出数，整体 sync 不中断 | 单眼抛异常的集成测试 |
| S2 | 同一事件多源报道被合并为一条，cross_refs/source_list 正确累积，热度获得加成 | 去重打分单元测试 |
| S3 | 一条 item 可同时进入多个主题频道，频道页与 HTML 简报由同一份统一池派生 | 频道路由测试 + mock 端到端 |
| S4 | LLM Agent 标注层完全不可用时，主流程零阻塞（规则/regex 回退） | 断网/无 key 环境跑通 sync |
| S5 | 全链路可离线验证：`sync --mock` 在无网环境产出 items.jsonl / hotlist.md / channels/*.md / digest.html | pytest + mock sync |
| S6 | 数据目录路径零硬编码，四级解析链可被环境变量完全重定向 | 路径解析单元测试 |
| S7 | 最终交付物（HTML 简报、架构总览）人类可直接阅读，逻辑清晰 | 人工审阅 |

## 非目标（本次重写明确不做）

- **不做**日报 V2 PNG 文生图路线的完整实现（保留 deprecated 接口壳，主路线是 HTML）。
- **不做**公司内网（心声/稼先）页面解析的真实联调——内网 + SSO 无法在开发环境验证，只交付 CDP 连接骨架 + 选择器常量集中 + 降级路径，联调留给内网环境。
- **不做** welink/email 的直连 API 对接——保持"打印 instruction，Agent 接力 skill"的松耦合。
- **不追求**对外部 API（aihot.virxact.com 等）响应格式的精确复刻——解析器做成配置驱动 + 尽力而为 + 容错，格式偏差通过配置修正。

## 干系人与消费方

- **人**：读 `channels/*.md`、`hotlist.md`、`digest.html`。
- **Agent（Claude 等）**：`deliver --channel in-chat` 读盘转述；`daily_digest --write-prompt` 接力选稿。
- **系统**：`api_server` 的 HTTP 接口；`$QLY_DATA_DIR` 目录本身即数据契约。

## 约束

- 运行环境：macOS / Linux，Python ≥ 3.9（开发机为系统 Python 3.9.6）。
- 依赖最小化：核心链路仅 `pyyaml` + `requests`；fastapi/uvicorn（API）、playwright（内眼）为可选 extras。
- 模板渲染**不依赖 jinja2**：自研极简模板引擎。
- 内网 LLM 网关兼容 Anthropic Messages API 协议（`ANTHROPIC_BASE_URL`）。
