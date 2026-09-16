# TASK-FIX-003 独立审查

审查对象：增强文件协议检查点摘要修复。范围为一处行为变更（`scripts/wxdf/normalization.py` 的 `semantic_payload`）与两项新增回归测试。

## 结论

PASS。四项摘要与存储路径内部一致，全量测试 1367 项通过，无阻断项。

## 逐项结论

| 项 | 结论 | 依据 |
|---|---|---|
| 摘要是否应描述剪枝后的树 | PASS | 唯一的重算点在 `validation.py:791-800`，它读的是存下来的快照；`checkpoint_store.py:684` 只做拷贝；`review.py:313` 与 `run_result.py:303` 只做转写。没有任何消费者依赖未剪枝的树身份 |
| 无排除区域时摘要取值是否不变 | PASS | `normalization.py:475` 深拷贝后，`_drop_excluded_blocks` 在排除集为空时直接返回，两棵树结构相同，规范化 JSON 与摘要在修复前后一致 |
| 摘要能否跨序列化稳定 | PASS | AST 与覆盖层的映射键都是字符串节点 id，其余为 str/int/bool；元组序列化为列表后取值相同，集合会在计算摘要时直接报错而不会静默通过 |
| `presentation_digest` 是否有同类缺陷 | PASS | 无。存入视图的 `presentation` 与参与摘要的是同一个对象（`normalization.py:624-640` 构造，`647` 与 `652` 使用），中间没有二次变更 |
| 两项新测试是否真实回归 | PASS | 归一化摘要不变量测试在修复前两条断言均失败；带源目录的协议测试在修复前报 `invalid_checkpoint` |
| 其他问题 | PASS | 见下 |

## 非阻断记录

1. 迁移说明：旧版本写出的、含排除区域的检查点在新版本下同样无法恢复。这类检查点原本就恢复不了（即本缺陷），无排除区域的检查点摘要不变，因此不产生新的破坏面。
2. 已采纳的建议：返回值中的 `source_revision` 原先取自 `source_ast["revision"]`，与摘要载荷里的 `ast["revision"]` 写法不一致，现统一为 `ast["revision"]`。剪枝不改变 `revision`，取值相同，但校验器要求两者相等（`validation.py:783-784`），统一写法让该不变量来自结构而非巧合。
3. 保留项：`semantic_payload` 摘要的是调用方传入的 `overlay` 对象，视图里存的是深拷贝。当前在 `normalize()` 内取值，两者结构相同；若将来出现调用后修改 `overlay` 的用法，摘要不会察觉。
4. 保留项：未新增“无剪枝时摘要相等”的显式测试，该性质由 `test_modular_normalization.py:88` 与 `test_modular_file_protocol.py:1663` 间接覆盖。
5. 有意的行为变更：仅在被排除区域内不同的两个视图，现在 `semantic_equal` 判定为相等。复核目标必须存在于剪枝后的视图（`review.py:337`），且覆盖层变化仍会改变摘要，因此没有放松任何门禁。
