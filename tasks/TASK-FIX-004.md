# TASK-FIX-004：Phase A/B 第一性原理重构

## 目标

按 `docs/first-principles-design.md` 重构 Phase A 和 Phase B 的 prompt，将 LLM 从"全量分类器"变为"规则纠错器"。

## Phase A 重构

修改 `_build_phase_a_prompt` (approx line 751)：

- 任务描述改为"规则结果审查器"
- 每个 block 展示：rule_block_type、rule_role、level、list_type、caption_type
- 指令：
  - 默认规则判断正确
  - 省略 block 表示接受规则结果
  - decisions 只包含需要修改的项
  - 空 decisions 表示规则全对
  - 重点审查：连续 body 功能点列表、封面元信息误判 heading、题注误判、正文误判

## Phase B 重构

修改 `_build_phase_b_prompt` (approx line 816)：

- 任务描述改为"标题层级异常审查器"
- 输入强调"规则后的最终 AST 摘要"
- 每个 heading 展示 rule_level 和前一个标题层级
- 指令：
  - 默认当前层级正确
  - 连续同级标题合法
  - 文档标题后直接进入 H2 或 H3 合法
  - 只标记真实异常（H2→H4 跳跃等）
  - 空 decisions 表示标题结构合法
  - 优先 adjust_level，只有明显误判标题才 retype

## Phase C

保持，已经在纠正模式。

## 验证

1. `python3 -m pytest tests/ -q`
2. 用 IAM 和生物特征两个文档测试，确保 LLM 修正数量大幅减少（因为规则已经很准了）
