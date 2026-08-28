# CLAUDE.md

本仓（`forcegravity1989/aihot`，千里眼 qianliyan）给 Claude Code 的工作约定。仓库定位见 [README.md](README.md)；完整实现合同见 [docs/spec.md](docs/spec.md) + [docs/spec-v0.3.md](docs/spec-v0.3.md)；SDLC 与 loop 工作流见 [docs/PLAYBOOK.md](docs/PLAYBOOK.md)；领域术语撞车先查 [CONTEXT.md](CONTEXT.md)；给不读 CLAUDE.md 的通用 agent 工具的指针在 [AGENTS.md](AGENTS.md)（内容跟这里走,不要另开一份）。

## 提交必须关联 issue

- 每次 commit 前先确认这次改动对应哪个 GitHub issue；没有就先建一个（哪怕是子任务），再动代码。
- commit message 正文用 `Closes #N`（完成并应关闭该 issue）或 `Refs #N`（相关但未完结）显式关联；纯杂务性改动（如格式化、依赖锁文件更新）也要说明关联的 issue 或直接在信息里写明"不关联具体 issue 的原因"。
- 一次提交尽量对应一个 issue 的范围；多个 issue 的改动要分开提交，不要合成一坨。

## 分支与合并

- 不直接向 `main` 提交。新工作开分支（建议 `epic-<N>/<slug>` 或 `issue-<N>/<slug>`），完工后开 PR，PR 描述里用 `Closes #N` 关联对应 issue。
- **push 与 merge 是红线动作**（承袭全局约定）：分支可以推送、PR 可以开，但合并到 `main` 永远等用户在 GitHub 上自己点，不自动合并。
- 参考 [PR #13](https://github.com/forcegravity1989/aihot/pull/13) 的做法：新分支要基于 `origin/main` 重建（而不是推一段和远端无共同历史的分支），否则 PR 没法干净合并。

## 数据与代码隔离——不可动摇的边界

千里眼的运行时数据（`items.jsonl`、`digest.html`、`channels/*.md`、`personas.json`、`history.jsonl`……）永远存在 `$QLY_DATA_DIR` 指向的目录，**结构性地在这个 git 仓库之外**（四级解析见 `core/paths.py:resolve_data_dir`，缺省 `~/qianliyan-data`）。这不是"最好这样"，是硬约束：

- 写代码时不要假设数据目录在仓库内、不要往仓库路径下拼运行时产物。
- 任何时候 `git status`/`git add` 里出现看起来像运行时数据的文件（`*.jsonl` 除 `tests/fixtures/` 外、生成的 `digest.html`、`channels/<name>.md`……），先停下来查是不是 `QLY_DATA_DIR` 配置指错了，而不是顺手提交。
- `config/*.yaml`（sources/channels/builders/reader……）**是代码**，决定行为、要 review、要进 git——不要因为它是"配置"就当成数据对待，也不要把真数据当成配置塞进 `config/`。

详细定义见 [CONTEXT.md](CONTEXT.md) 「数据与代码隔离」词条。

## `.bw/` 是 Builders' Workbench 的资产目录，不是项目文档

`.bw/` 下的文件（`PROJECT.md`、`project.toml`、`standard.toml`、`managed.toml`、`issue-policy.toml`、`metrics.toml`、`releases.md`、`scripts/`）是 [builders-workbench](/Users/gravity/projects/builders-workbench)（buddy）铺给这个项目的规范骨架（v5.0），供 buddy 的项目管理面板读取，**不是**给读者看的项目说明——项目说明仍在 `README.md`/`docs/`。

- `issue-policy.toml`：评审与合并策略（`who_can_merge = "repo_write"`、`require_pr_for = ["code","docs","prototype"]`）与开工工具映射，是通用铺底模板，未按本项目定制。
- `metrics.toml` 的北极星（三个月可度量目标）尚未填——这是产品决策，需要用户定，不要替用户瞎猜着填。
- 改 `.bw/*` 前先想一下：是在改 buddy 认的骨架数据，还是在改项目本身的文档——两者别混。
