# TASK-FIX-002 独立审查

审查对象：真实输入语料暴露的 7 类缺陷修复。审查范围覆盖 `scripts/wxdf/` 的 8 个改动文件和新增回归测试。

## 结论

首轮 FAIL，一条阻断项。返工后通过。

## 首轮阻断项

`_apply_create_node` 让创建节点继承目的容器的 `appendix_id`、`semantic_scope_id` 与 `source_number_preserved` 三个字段。第三个字段描述节点自身内容，生成题注没有源编号，继承后 `_write_caption` 走“保留源编号”分支，把 7 个附录内生成题注渲染为空白段落。

阻断的原因是审计无法发现该回归：保存输出审计过滤空文本，附录审计只核对归属元数据，因此 `status=completed`、三个审计全部通过，成品里却是空白题注。

返工内容：

1. 创建节点只继承范围字段，新增 `_CREATED_NODE_SCOPE_FIELDS`，移动节点保持原行为不变。
2. 新增端到端测试 `test_generated_caption_inside_an_appendix_keeps_its_label`，断言附录内题注不得为空。
3. 单元测试补断言：创建节点不得继承 `source_number_preserved`。

## 逐项结论

| 项 | 结论 | 依据 |
|---|---|---|
| 修复层次归属 | PASS | 解析类缺陷落在 `parsers.py`，引用闭合落在规范化阶段，范围继承落在事务，渲染文本落在渲染阶段，均符合 `AGENTS.md` 的归属规则 |
| 编号格式放宽是否削弱损坏定义合同 | PASS | 既有测试覆盖的是 `abstractNumId` 缺失或悬空的不可解析定义，`num_fmt` 为 `None`，与格式白名单无关；`numFmt=none` 仍由 `is_marker_only_level` 决定 |
| 引用闭合完整性 | PARTIAL 已补 | 首轮遗漏附录容器的 `title_ref`，本轮一并闭合；`document.associations`、题注 `target_id`、`title_ref` 是 AST 2.0 的全部跨节点引用 |
| 范围继承的优先级 | PASS | `setdefault` 让提案自带的声明优先；题注父节点由锚点位置解析，正文目标的题注不会落进附录容器 |
| `_text` 与审计回读一致性 | PARTIAL | 域缓存结果在解析阶段被 `strip`，`opaque_inline` 中文本框携带的 `w:t` 不在 `cached_result` 内，两者仍可能不一致。均为既有行为，当前语料未触发 |
| 夹紧改为非阻断后是否仍有余量门禁 | PASS | `unresolved_list_parent` 不在 `ADVISORY_AUDIT_CODES` 内，仍同时阻断运行结果与发布；层内跳变用例仍为 failed |
| 回归测试有效性 | PASS | 9 项中 8 项在修复前失败；`test_created_node_keeps_its_own_scope_declaration` 是优先级守卫，非缺陷回归，已在文档字符串中说明 |

## 复审

返工后重新执行全量测试与真实语料转换：1365 项测试通过，13 个输入全部退出码 0，全部输出的题注段落无空白段落。
