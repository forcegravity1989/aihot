# plan.md — 千里眼 v0.2 实施计划

> SDLC 阶段三：怎么做、谁来做、怎么验收。架构与文档由 Fable 负责；实现由 Opus / Sonnet 子代理分波承担（**子代理一律不用 Fable**）。

## 分波与文件归属（波内文件集互斥，杜绝并行冲突）

### Wave 1 · 地基（Opus）——阻塞后续所有波

**交付文件**：
- `pyproject.toml`（项目名 qianliyan，0.2.0，requires-python >=3.9；deps: pyyaml, requests；extras: api=[fastapi, uvicorn], browser=[playwright], dev=[pytest, httpx]）
- `.gitignore`（.venv/ __pycache__/ paths.local.json *.egg-info .pytest_cache）
- `qianliyan/__init__.py`、`qianliyan/core/*`（paths / schema / utils / storage / og_image / llm_client 全部六件）
- `config/paths.team.json`
- `tests/conftest.py`、`tests/test_utils.py`、`tests/test_paths.py`、`tests/test_storage.py`
- `README.md`（简版：定位 + 快速开始 + 目录说明，中文）

**验收**：`pytest tests/test_utils.py tests/test_paths.py tests/test_storage.py` 全绿；hotness 单调性与四级路径解析被测试锁死。

### Wave 2a · 五眼 + engine（Sonnet）——依赖 Wave 1

**交付文件**：`qianliyan/engine/*`（含 `__init__.py` 共 7 件）、`qianliyan/eyes/*`（共 6 件）、`config/sources.yaml`、`config/builders.yaml`、`config/x-follows.yaml`、`tests/test_engines.py`、`tests/test_eyes.py`、`tests/fixtures/`（aihot_payload.json / builders_payload.json / insights_payload.json / rss_sample.xml / atom_sample.xml / sitemap_sample.xml）

**验收**：两份测试全绿；每眼 parse 与网络壳分离；`QLY_OFFLINE=1` 下 fetch 快速抛 OfflineError 而非挂起。

### Wave 2b · pipeline + 模板（Opus）——依赖 Wave 1，与 2a 并行

**交付文件**：`qianliyan/pipeline/*`（共 9 件）、`templates/digest.html.jinja`、`config/channels.yaml`、`config/model-sources.yaml`、`tests/test_minitpl.py`、`tests/test_channels.py`、`tests/test_report.py`、`tests/test_agents.py`

**验收**：四份测试全绿；digest.html 单文件自包含（三排序按钮 + Ctrl+K palette + badge）；四个 agent 在无 LLM 环境全部走回退且不抛异常。

### Wave 3 · cli + scripts + 集成（Sonnet）——依赖 Wave 2a + 2b

**交付文件**：`qianliyan/cli/*`（共 7 件）、`scripts/*`（_compat.py + 6 个 wrapper）、`tests/fixtures/mock_items.jsonl`、`tests/test_sync_mock.py`、`tests/test_api.py`

**验收**：全量 `pytest` 绿；`python -m qianliyan.cli.sync --mock` 在 `QLY_OFFLINE=1` 下端到端产出五件套（items.jsonl / hotlist.md / channels/*.md / digest.html / sync_meta.json）；`--status` 可读。

### Wave 4 · 集成验证与修缮（Fable 亲自）

跑全量 pytest + mock sync + api TestClient 冒烟；发现的问题直接修或派 Sonnet 修。

### Wave 5 · 人类可读交付物（Fable 亲自）

`docs/overview.html`：单文件架构总览（四层管道图、五眼表、数据流、算法、配置链、CLI 与分发、与 intent/spec/plan 的映射、验证结论），发布为 Artifact。

## 子代理公约

1. 开工先读 `docs/spec.md`（唯一合同）与本文件自己波次的条目；**只写自己波次归属的文件**。
2. Python 3.9 兼容；依赖铁律见 spec §0；venv 在 `.venv/`（`.venv/bin/pytest` 直接可用）。
3. 完工前必须自跑本波验收命令并让其全绿；报告中如实说明任何偏离 spec 之处。
4. 不做 git commit；不出网（开发与测试全程 `QLY_OFFLINE=1` 思维，真实抓取逻辑只写不跑）。

## 风险与对策

| 风险 | 对策 |
|------|------|
| 外部 API（aihot/builders/insights）真实响应格式未知 | 解析器配置驱动 + 多字段名尽力匹配 + fixtures 锁行为；上线后只改 config/fixtures |
| 内眼无法离线联调 | 选择器常量集中 + CDP/代理双降级 + 单眼故障不外溢（S1 保证） |
| 并行子代理写文件冲突 | 波内文件集互斥（本文件归属表是唯一权威） |
| 系统 Python 3.9 语法踩坑 | spec 明令禁 3.10+ 语法；pytest 在 3.9 venv 上跑即验证 |
