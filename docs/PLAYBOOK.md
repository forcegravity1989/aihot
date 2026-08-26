# PLAYBOOK.md — 千里眼的 AI-native SDLC 与 loop 工作流

> 本项目的开发方法学。对应 GitHub Epic [#12](https://github.com/forcegravity1989/aihot/issues/12)。
> 一句话：**文档即合同，issue 即工作单元，wave 即并行实现，loop 即推进节律。**

## 1. 四段式 SDLC（文档是持久产物）

```
Explore（侦察真实信源/约束）→ Plan → 落三份文档 → 拆 issue → loop 波次实现 → 验证 → 交付
```

| 阶段 | 产物 | 谁 |
|------|------|----|
| Intent | `docs/intent.md`（为什么做、S1–S7 成功标准、非目标） | Fable |
| Spec | `docs/spec.md` + `docs/spec-v0.3.md`（**唯一实现合同**：目录/schema/算法/接口/测试） | Fable |
| Plan | `docs/plan.md`（波次、文件归属互斥表、DoD） | Fable |
| Issues | GitHub issues（Epic + 子任务，见 #7–#11） | Fable |
| Implement | 代码 + 测试 | Opus/Sonnet 子代理 |
| Verify | 全量 pytest + 离线端到端冒烟 | Fable |

**铁律**：先写 spec 再写码；真实信源先侦察成 `tests/fixtures/real/` 再对着写解析器；子代理**一律不用 Fable**（全局约定）。

## 2. Wave 模型（并行实现的单元）

一个 issue 拆成一个或多个 wave。**波内文件集互斥**（杜绝并行写冲突），每个 wave 有明确 DoD。已完成波次：

| Wave | 范围 | 状态 |
|------|------|------|
| 1 | core 底座 | ✅ 99 测试 |
| 2a/2b | 五眼+engine / pipeline+模板 | ✅ |
| 3 | cli + 集成 | ✅ 356 |
| A/B | 真实 aihot·builders / 人物·读者画像 | ✅ |
| E/F | 信源目录+新后端 / 深读浅读+报告版面 | ✅ 450 |
| G | 集成：sync/api/目录眼/频道 | 🚧 |
| H | 变更情报 + transcript（#7 #8） | ⏳ |

## 3. loop 工作流（推进节律）

每一轮 loop：

```
1. 取下一个 open issue（按 Epic 顺序：集成 → #7 → #8 → #9 → #10 → #11）
2. 若需并行，拆 wave 并派子代理（Opus/Sonnet），波内文件互斥
3. 等 wave 完成通知 → 全量 pytest 必须绿 + 离线冒烟无 traceback（DoD）
4. 达标则关 issue、勾 Epic 清单；否则派修复
5. 回到 1，直到 backlog 清空 → 转维护 loop（CI 巡检/信源健康）
```

**Definition of Done（每个 issue 通用）**：
- `.venv/bin/pytest -q` 全绿，新增功能有离线测试（对 `tests/fixtures/real/`，禁真实出网）。
- `QLY_OFFLINE=1 python -m qianliyan.cli.sync --mock` 端到端产出五件套无 traceback。
- 与 spec 的偏离在 PR/报告里如实说明。

**外发动作红线**（全局约定，永不自动放行）：push / merge / 删数据 需用户逐次显式批准——故 #11「首次推送」独立成 issue、默认不执行。

## 4. 状态看板

- 主线：Epic [#12](https://github.com/forcegravity1989/aihot/issues/12)
- 进行中：集成波（G）
- 待办：#7 变更情报 · #8 transcript · #9 信源补全 · #10 收尾 · #11 推送（待批准）
