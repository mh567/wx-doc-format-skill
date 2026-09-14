# Module 深化执行包

## 目标

在 native-v2 架构收敛（T00 至 T09、AC-1 至 AC-4）完成后，按 2026-09-14 架构复审结果，把八个浅 module 候选收敛为深 module，提升 locality、leverage 与可测性。本包只做职责与 seam 收敛，不新增业务能力，不改变稳定 CLI 参数，不改动公开报告合同。

复审报告（HTML，位于操作系统临时目录）给出了每个候选的 before/after 图与推荐强度，本包是其可执行化结果。

## 执行规则

1. `status.json` 是本执行包唯一状态源。
2. 工单按 blocker 优先推进。无 blocker 的 T01、T02、T04 可并行，其余按依赖顺序。
3. 由 Command Code 协同多个智能体实施，覆盖架构与接口定案、实现、测试、独立审查与验收；Git 操作由实施侧统一执行。
4. 每票先红灯，再实现，再定向测试，再由独立审查，修复后复审。
5. 每票在 `evidence/Txx.md` 记录实施提交、红灯、绿灯、相关回归、全量测试、真实转换、审查发现、修复结果、复审结论和下一项工单。
6. 改 `.py` 前先声明路径。
7. 私有 `dev` 开发线。未获授权不 push、不打标签、不发布。
8. `_build/`、`paseo.json` 及无关用户改动保持原状。

## 工单顺序与依赖

| 工单 | 主题 | depends_on |
| :--- | :--- | :--- |
| T01 | Kernel 静态方法归位 | 无 |
| T02 | 语义归属与工厂收敛 | 无 |
| T03 | 保护策略 owner | T02 |
| T04 | 删除 facade 与注入面收敛 | 无 |
| T05 | Digest owner | T04 |
| T06 | 事务证明收敛 | T04、T05 |
| T07 | Checkpoint 拆分与 response_store seam | T04、T05、T06 |
| FU-1 | executor 编排提取（推迟） | 见 `docs/adr/0003` |

推荐推进次序：T01 → T02 → T03 → T04 → T05 → T06 → T07。T01 只动 `kernel.py`，爆炸半径最小，先落地建立节奏；随后语义簇，再持久化簇按依赖推进。FU-1 不在本包内。

## 候选摘要

- **T01 (#7) Kernel 静态方法归位**：约 15 个翻译静态方法散在 kernel，其中一个死代码。按 artifact owner 拆分，kernel 只留 lifecycle。强度 Strong。
- **T02 (#2) 语义归属与工厂收敛**：工厂带死分支，且同一 `*_result` 在多处重算。每 concept 单一 owner，下游读已发布结果。强度 Strong。
- **T03 (#3) 保护策略 owner**：保护正则在 `lists` 与 `protected` 各写一遍，可能静默漂移。强度 Strong。
- **T04 (#1) 删除 facade 与注入面收敛**：生产调用经 `builtin_modules`/`run_store` 名字回查，真实调用图由测试决定。改直接 import。强度 Strong。
- **T05 (#8) Digest owner**：canonical JSON 加 sha256 的身份规则复制在七个 module。强度 Worth exploring。
- **T06 (#5) 事务证明收敛**：证明散在四个 module，`decision_ledger` 重演 `transaction` 的 apply 语义。强度 Strong。
- **T07 (#4) Checkpoint 拆分与 response_store seam**：1488 行的 module 熔了四个概念，且与 `response_store` 形成 lazy import cycle。强度 Strong。
- **FU-1 (#6) executor 编排提取**：推迟，理由见 `docs/adr/0003`。

## 完成定义

1. 七张工单按各自验收条件完成。
2. 不存在 `sys.modules` 名字回查，调用图静态可见。
3. 每个 concept 单一 owner，无重复 `*_result` 重算，无死分支 capability。
4. 全量测试、真实 Markdown 与真实 DOCX 转换、ZIP 与 OOXML 审计通过。
5. FU-1 保持推迟并由 `docs/adr/0003` 记录。

## 不纳入本轮

- 不新增模板能力、LLM 能力或输入格式。
- 不改变稳定 CLI 参数与既有公开报告合同，除非先增加兼容测试。
- 不移动或删除 `_build/`、`paseo.json` 及无关用户改动。
- 不执行 FU-1。
- 不执行远程 push、tag 或 Release。
