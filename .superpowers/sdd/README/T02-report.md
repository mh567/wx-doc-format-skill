# T02 实施报告

## 文件改动

| 文件 | 状态 | 说明 |
| :--- | :--- | :--- |
| `scripts/main.py` | 修改 | 旧 parser、旧 model normalization、旧 renderer 与旧 finalizer 的模块级导入改为「优先直连、不可用时延迟绑定」 |
| `scripts/wxdf/template.py` | 修改 | 自持模板 TOC 片段提取，删除对 `template_finalizer` 的导入 |
| `scripts/wxdf/semantics/captions.py` | 修改 | 生成题注节点与 `caption_target` 关联；关联候选携带目标 `source_ref`；为作者题注复述 caption 角色 |
| `scripts/wxdf/semantics/source_ast.py` | 修改 | 构建阶段物化行内图片为显式 image 节点，并物化作者题注（表与图） |
| `scripts/wxdf/rendering.py` | 修改 | 题注段落渲染 `SEQ` 域；作者题注的标签与编号在渲染时投影掉；语义 image 节点不重复渲染图片 |
| `scripts/wxdf/builtin_modules.py` | 修改 | 题注 Module 证据新增 `generated_caption_count` |
| `tests/test_native_semantic_ownership.py` | 新增 | 23 项 T02 聚焦测试 |
| `tests/test_issue3_acceptance.py` | 修改 | 两项结构断言按生成题注块调整 |
| `docs/execution-v4/evidence/T02.md` | 新增 | 命令、退出码、红灯、绿灯、真实转换、对照、断言调整与风险 |
| `.superpowers/sdd/README/T02-report.md` | 新增 | 本报告 |

未改动 `docs/execution-v4/status.json`、`tests/test_native_skill_cutover.py`、`_build/`、`paseo.json`、`scripts/.venv`、发布文件或无关改动。未提交。未派生 Subagent。未执行 Git 操作。

工作树为链接工作树的 detached HEAD，`git rev-parse HEAD` 为 `6de351d`，当前不在 `dev` 分支上。

新增测试与证据受 `.gitignore` 的 `tests/*` 与 `docs/*` 规则约束，需要 `git add -f` 才能纳入提交，本工单未改动 `.gitignore`。

## 红灯证据

T02 冻结红灯，实施前：

```
env PYTHONPATH="$(pwd)/tests" \
  scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -k t02 -q
# 3 failed, 14 deselected in 0.41s，退出码 1

stderr: WXDF-T02-BLOCKED:docx_pipeline
returncode 3
```

三项失败同源：`import main` 在模块级导入 `docx_pipeline`。

新增聚焦红灯，在未改动的 HEAD 树上先运行：

```
env PYTHONPATH=<head-tree>/tests \
  scripts/.venv/bin/python3 -m pytest tests/test_native_semantic_ownership.py -q
# 10 failed, 1 passed in 1.26s，退出码 1
```

`tests/test_native_skill_cutover.py` 未改动，`BLOCKED_LEGACY_MODULES` 未削弱。

## 测试结果

```
env PYTHONPATH="$(pwd)/tests" \
  scripts/.venv/bin/python3 -m pytest tests/test_native_semantic_ownership.py -q
# 23 passed in 4.43s，退出码 0

env PYTHONPATH="$(pwd)/tests" \
  scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -k t02 -q
# 3 passed, 14 deselected in 1.01s，退出码 0

env PYTHONPATH=/Users/harris/Documents/zero项目/wx-doc-format-skill/tests \
  scripts/.venv/bin/python3 -m pytest tests/ --ignore=tests/test_native_skill_cutover.py -q
# 535 passed in 38.38s，退出码 0

env PYTHONPATH="$(pwd)/tests" \
  scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q
# 12 failed, 5 passed in 4.73s，退出码 1

env PYTHONPATH=/Users/harris/Documents/zero项目/wx-doc-format-skill/tests \
  scripts/.venv/bin/python3 -m pytest tests/ -q
# 12 failed, 540 passed in 73.80s，退出码 1
```

排除冻结切换合同文件后基线全绿。冻结文件五项通过为 T01 两项与 T02 三项，十二项失败逐项对应 T03 至 T09。

## 实施要点

### 导入所有权

`main.py` 的三处原始导入位置各自包裹 `try`，直连成功时绑定原函数对象，`ImportError` 时回落到延迟转发包装。运行时五个绑定都指向原函数对象，导入顺序与 T01 实现一致，因此 legacy 输出合同不变。

被拦截时 `import main` 成功，六个拦截模块均不进入 `sys.modules`。

`docx_render` 必须一并延迟，原因是它传递导入 `docx_pipeline`。

### 模板 TOC 自持

`wxdf/template.py` 直接读取模板 OOXML 定位 TOC 内容控件与其后的分页段落。对同一模板，与旧 finalizer 的实现产出**逐字节相同**的两个片段。

### 题注物化

`collect_caption_candidates` 追加生成题注节点候选与 `caption_target` 关联候选，节点携带 `generated_by` 与 `generation_basis`，对应 `docs/modular-pipeline-migration-v3.1.md` 的条目 4。渲染层为题注段落写入 `SEQ` 域，产出的题注段落 XML 与 legacy 一致。

## 第二轮修复

### 关联候选前置条件

初审指出 `caption-association-*` 的 `target_source_refs` 为空。该候选现改为以目标表格或图形为 `target_node_ids`，并以该块 `source_ref` 的深拷贝为 `target_source_refs`，事务的 source reference 前置条件因此有实际校验对象。

### 作者题注物化

初审指出作者题注只在 `collect_source_caption_semantics` 旁路识别，Source AST 中仍无 `role="caption"` 与 `target_id`，因此已带作者题注的目标还会被再插入一个 `inline_content` 为空的 `native-caption-*`。

`source_ast.py` 新增 `_authored_caption_materialization`，在构建阶段把作者题注段落变为真正的题注块，保留原 `source_ref` 与可见 `inline_content`，写入 `caption_type`、`target_id` 与 `caption_target` 关联。`document-structure` 的默认 `body` 角色由题注 Module 的 `update_role` 候选复述回正，写法与 `legacy_capability.py` 既有一致。`_captioned_targets` 因此能识别作者题注，同目标不再生成题注节点。

判定收敛于 `status == "associated"` 且目标可解析为表格容器或图片节点的题注。渲染层把作者题注的标签与编号投影掉，由前缀 run 与 `SEQ` 域接管编号。同夹具的 native 与 legacy 可见题注文本一致：`服务接口` 与 `参数说明` 各出现一次，`表 1 服务接口` 出现零次。

### 图片的 AST 所有权与图题注

初审指出图题注没有 AST 所有权：原生解析把图片放在 `paragraph.inline_content`，Source AST 没有图片节点，图题注既无有效目标，也没有 `SEQ Figure` 输出。

`source_ast.py` 的 `_inline_image_nodes` 为每个行内图片生成显式 `image` 节点，携带图片部件自身的 `source_ref`、`resource_id` 与非空 `alt_text`，并以 `generated_by="source_ast.inline_image"` 和 `generation_basis=[<段落 id>, <行内下标>]` 记录来源；节点插入在其来源段落之后。

原段落完整保留文本、其他行内 token、行内图片与顺序。这是既有合同的硬要求：`test_issue3_acceptance.py` 要求行内图片所在的列表项保留该段落并承载列表编号，`test_issue7_acceptance.py` 要求混合行内顺序 `["text","image","text","link","text","break","text"]` 在产物中逐项保持。渲染仍由承载 token 的段落负责，语义 image 节点不输出第二张图片，`inline_shapes` 数量不变。

作者图题注经 `image_targets` 映射到 image 节点，`caption_type="figure"`、`target_id` 指向有效 image，位置在图片之后。无题注的图片由图片分支生成一条图题注，候选引用该 image 节点的 `source_ref`，且不产生重复题注。

生成的图题注与表题注一致，只有前缀与 `SEQ` 域，正文留空，可由 T03 的 `caption_gen` 能力经 ContentOverlay 补齐。

题注 Module 证据新增 `generated_caption_count`，报告因此同时呈现作者题注与生成题注。基准夹具的报告为 `caption_count: 0` 与 `generated_caption_count: 2`，与产物中的两条 `SEQ Table` 一致。

四个夹具的 native 题注数量与 legacy `SEQ Table` 数量逐项相同。

## 结构断言调整

Codex 扩展路径声明后，`tests/test_issue3_acceptance.py` 的两项结构断言已调整。

- `test_l07_first_table_and_sublist_get_one_evidenced_anchor`：改为按 `block_type == "table"` 选取 `list_item` 内的表格，不再依赖子节点位置
- `test_l09_merged_and_nested_table_cells_are_unique_and_scope_local`：`root_labels` 收集循环跳过 `role == "caption"` 的块，第 530 行期望值保持原样

两项调整都只排除生成题注节点，其余断言未改动。生成题注块插在表格之前是 T02 的既定语义，legacy 链路对同一夹具本就产出题注（`l09.docx` 一条 `SEQ Table`，`l07.md` 一条）。

## 剩余风险

- 语义 image 节点以 `generated_by="source_ast.inline_image"` 作为渲染层跳过依据，同时满足 AST 2.0 对 `source_ref` 与 `generated_by` 的任一要求
- 生成的图题注正文留空，题注正文由 T03 的 `caption_gen` 能力经 ContentOverlay 补齐
- DOCX 图片缺少替代文本时，`alt_text` 由图片部件文件名补足，保证块级 image 的非空校验
- 作者题注的角色由题注 Module 复述一次，依赖该 Module 的候选排在 `document-structure` 之后，顺序由 `_native_candidate_set` 固定
- 题注段落渲染为单一文本 run，作者题注内的行内标记与链接不进入题注文本，与 legacy 一致
- `except ImportError` 会在旧模块因其他原因不可导入时同样回落，错误推迟到 legacy 路径真正调用时抛出
- 该仓库 legacy 产物与报告对输出目录的使用历史敏感。legacy 对照实验必须在同一输出目录交替运行，跨路径或跨目录历史的摘要比较无效
- 既有 `scripts/main.py` 中四处破折号位于未改动的行，本工单未引入新的破折号

## 风格检查

对本工单 diff 的全部新增行（498 行）扫描 Unicode 破折号码位 U+2010 至 U+2015 与 U+2212，以及 AGENTS.md 禁止的否定对比句式，两个模式均为零命中。新增测试文件同样零命中。

## 自我复审

- 严格 TDD：先跑 T00 冻结红灯，再写聚焦红灯，再实现
- 未使用源码正文断言证明所有权，所有权由导入拦截下的真实转换与保存后审计证明
- 真实转换覆盖四个夹具，含导入拦截、`unzip -t` 与 legacy 题注数量对齐
- legacy 输出合同用同深度双树交替运行证明逐轮一致，另用绑定同一性交叉验证
- 未触碰授权范围外的文件，`git diff --check` 退出码 0
- 发现一处范围外冲突，先按 brief 要求停止并记录，路径扩展后再调整断言，未自行扩大路径
- `scripts/wxdf/builtin_modules.py` 只新增证据键，能力产出与描述符未改动，T03 及后续行为不受影响

## next_action

`next_action: Codex review`
