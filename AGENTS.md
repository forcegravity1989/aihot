# AGENTS.md

本仓的规则正本在 [CLAUDE.md](CLAUDE.md)——提交-issue 关联约定、分支/合并红线、`.bw/` 用途说明都在那里，本文件只是给按 `AGENTS.md` 惯例找规则的通用 agent 工具（Codex/Cursor 等）一个指针，内容随 CLAUDE.md 变化，不要在这里另开一份。

## 30 秒定位

- 这是什么、怎么跑：[README.md](README.md)
- 实现合同（目录结构/算法/接口的唯一权威）：[docs/spec.md](docs/spec.md) + [docs/spec-v0.3.md](docs/spec-v0.3.md)
- 领域词表（术语撞车先查这里）：[CONTEXT.md](CONTEXT.md)
- SDLC 与 loop 工作流：[docs/PLAYBOOK.md](docs/PLAYBOOK.md)
- 项目治理骨架（bw v5.0，非项目文档）：[`.bw/`](.bw/)

## 一条硬约束

运行时数据（`items.jsonl`、`digest.html`、`channels/*.md`……）永远在 `$QLY_DATA_DIR` 之外的目录，不在本仓库里。见 [CONTEXT.md](CONTEXT.md) 「数据与代码隔离」一节。
