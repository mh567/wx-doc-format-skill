# Desktop 审计与交付策略

## 目标与边界

Desktop 在生成可读取的 DOCX 后交付文件，同时呈现审计发现和未完成检查。Skill 默认保持严格策略，使用审计结果驱动受限修复与重新验收。审计结论不因交付而改写为通过。

本次不修改题注语义、不关闭检查、不改变稳定 AST 或模板接口、不执行构建与远程发布。真实用户手册的三处题注差异作为策略验收样例。

## 接口

- CLI 新增 `--delivery-policy strict|desktop`，默认 `strict`。
- Kernel options 使用 `delivery_policy`。检查点记录并恢复该值，防止恢复过程改变交付语义。
- Desktop 运行时显式传入 `--delivery-policy desktop`，不复用 `strict_normalize`。
- 原有 `status`、`exit_code` 与公开摘要 1.0 保持兼容，摘要增加交付策略、转换状态、审计状态、交付状态与安全的发现项。

## 决策归属

`wxdf/delivery.py` 拥有唯一交付策略。发布阶段计算并提供交付决策，结果汇总消费该决策，不重复实施审计门禁。

严格策略保留现有审计、事务和复核接受门禁。Desktop 策略允许审计失败、未知发现与检查异常的有效候选交付，但独立验证 DOCX ZIP 完整性与读取能力。输入、解析、事务、渲染、SourceFacts 不可变性和保存失败仍阻断。

受限复核接受候选时使用该候选；复核拒绝或回滚时仅允许 Desktop 选择有效基线。复核的 acceptance 保持原结论，禁止将拒绝候选伪装成已接受候选。

## 审计与报告

各审计检查独立执行，检查异常记录为未完成并继续其余检查。结果包含 code、category、severity、certainty、block_id 或 location 和可读 message。保存文件的内容差异与格式偏差分别标记，未知代码明确提示复核。

状态分为转换完成或失败、审计通过或有发现或未完成、交付完成或阻断。Desktop 已交付且有发现时沿用 `completed_with_warnings`，应用显示需复核。损坏文件不得交付。

公开摘要仅提供白名单字段，不暴露 AST、私有决策账本或内部路径。Desktop 将安全发现项与具体位置写入 HTML 和 JSON 报告。运行失败也保留独立的诊断报告，避免临时目录清理后丢失证据。

## Desktop 保存合同

DOCX、JSON 与 HTML 在暂存区生成完毕后作为一个保存事务发布。保存失败回滚已替换的文件，旧结果保持完整。拒绝源文件与输出或报告路径相同、多个目标重合的请求。

异常路径不删除用户原有目标。失败报告使用独立文件名，保留已有成功结果与报告，并在错误信息中给出诊断报告位置。

## 验收

1. 相同审计失败：strict 返回非零且不发布正式 DOCX；desktop 返回零并发布可读取 DOCX 与真实失败发现。
2. 审计异常：Desktop 交付有效文件，报告标记未完成；strict 阻断。
3. 模拟正文、图片、公式缺失：Desktop 展示具体风险；strict 阻断。
4. 损坏 DOCX、事务失败与 SourceFacts 变化：两种策略均阻断。
5. 拒绝复核候选：Desktop 只交付有效基线，strict 保持既有回滚行为。
6. 保存失败：原有 DOCX 与报告保持字节一致，失败诊断可取得。
7. 新 CLI 参数、公开摘要、检查点恢复、Desktop 实际子进程入口通过回归。
8. 用户提供的原始手册按项目模板以两种策略运行，核验退出码、报告、ZIP 与可读取性。

## 修改路径与职责

架构智能体已完成只读定案。实施智能体修改 `scripts/wxdf/delivery.py`、`native_stages.py`、`run_result.py`、`auditing.py` 与核心测试。主代理实施 `scripts/main.py` 和 CLI、公开摘要测试。Desktop 实施智能体在临时副本修改 `native_runtime.py`、`service.py` 与相关测试，验证后写回配对仓库。审查智能体对两仓库改动独立审查，修复后复审。
