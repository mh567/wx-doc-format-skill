当前环境只有读取权限，`apply_patch` 被拒绝，所以我无法直接改文件。已定位需要改的点在 [scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:751) 的 `_build_phase_a_prompt` 和 [scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:816) 的 `_build_phase_b_prompt`。

建议改法如下：

```diff
Phase A prompt:
- 任务改为“规则结果审查器”
- 每个 block 明确展示 rule_block_type、rule_role、level、list_type、caption_type
- 明确说明：
  - 默认规则判断正确
  - 省略 block 表示接受规则结果
  - decisions 只包含需要修改的项
  - 空 decisions 表示规则全对
  - 重点审查连续 body 功能点列表、封面元信息误判 heading、题注误判、正文误判

Phase B prompt:
- 任务改为“标题层级异常审查器”
- 输入强调“规则后的最终 AST 摘要”
- 每个 heading 展示 rule_level 和前一个标题层级
- 明确说明：
  - 默认当前层级正确
  - 连续同级标题合法
  - 文档标题后直接进入 H2 或 H3 可以合法
  - 只标记真实异常，例如 H2 到 H4 跳跃
  - 空 decisions 表示标题结构合法
  - 优先 adjust_level，只有明显误判标题才 retype
```

`validate_patch` 我建议保留。它不再承担对抗全量覆盖的主要职责，但保留 schema、block_id、operation、confidence 校验可以维持向后兼容，也能防止坏 JSON 或旧客户端输出污染 AST。

我没有运行测试，因为补丁没有成功写入。解除只读限制后，应补充两个测试：检查 Phase A prompt 包含“规则结果审查器”和“空 decisions 表示规则结果全对”，检查 Phase B prompt 包含“连续同级标题是合法结构”和“空 decisions 表示当前标题结构合法”。