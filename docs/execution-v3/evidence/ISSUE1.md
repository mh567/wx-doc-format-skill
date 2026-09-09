# Issue #1 实施证据

## 基线与范围

审查基线：`4b595c0`。

原始实现：`b44de5f`。

本轮本地修复提交链：`0799c17`、`8f70c18`、`5f04ee9`、`1e91f88`、`7c8ed9a`、`d053653`。

改动范围：native-v2 模块链、候选裁决、单批事务、列表 adapter、模块契约、CLI 受控入口和回归测试。legacy 默认入口、LLM 文件协议、Kylin 与 Windows 范围保持不变。

## 测试证据

R03 初始红灯命令：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py -q`

结果：3 项新增测试失败，分别暴露 legacy retype 意图丢失、续段候选缺失和空列项锚点缺失。

R04 初始红灯覆盖了 native 注册表和未决决策发布阻断测试。

P1 修复覆盖：

1. Registry 绑定八个 native-v2 阶段的实际运行实现，转换过程调用 `ModuleRegistry.execute`。
2. Renderer 写入临时输出，AuditModule 和 Publisher 共同决定是否替换最终输出。模型审计、保存后审计或严重诊断失败时不发布 DOCX。
3. legacy adapter 将 source reference 传入 DecisionSet，TransactionManager 在提交时重新校验目标引用。

Issue 专项命令：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py -q`

退出码：`0`。结果：`15 passed`。

P1 聚焦回归：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py tests/test_modular_kernel.py -q`

退出码：`0`。结果：`26 passed`。

完整回归：

`scripts/.venv/bin/python3 -m pytest tests/ -q`

退出码：`0`。结果：`418 passed in 28.05s`。

差异检查：

`git diff --check`

退出码：`0`。

## L01 端到端验收基线

2026-09-09 增加 `tests/test_issue1_acceptance.py`，以真实 CLI 分别转换 Markdown 和动态生成的 DOCX 夹具。夹具覆盖一级有序、二级无序、三级有序和第二个一级项，要求源文件保持不变，legacy 默认入口继续可用。

初始红灯：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_acceptance.py -q`

结果：严格验收断言新增后 `2 failed, 1 passed`，暴露 native 列表项按节点 ID 排序导致源阅读顺序反转，以及默认 legacy 报告缺少 native 链路特征。

修复：

1. 移除 `scripts/wxdf/semantics/lists.py` 中对列表、列表项和候选的节点 ID 排序，保留 SourceFacts 的源阅读顺序。
2. 验收断言覆盖 SourceFacts 摘要、Source AST 容器关系和列表族、规范化后的源 AST 保真、RenderPlan 顺序和编号、完整 native 模块链、事务提交、模型与保存后审计。
3. native kernel 对 SourceFacts 生成稳定 digest，在主链前后比对并将结果写入报告。digest 不一致时写入错误诊断，阻止转换完成。
4. 保存后回读 `numbering.xml`、`styles.xml` 和 `document.xml`，校验 `numId`、`ilvl`、`abstractNumId`、`numFmt`，并执行 ZIP 完整性检查。
5. 增加同一嵌套列表 Markdown 夹具的不带 pipeline 参数 legacy 默认入口真实转换检查。
6. RenderPlan 的列表呈现信息显式记录 `restart`，L01 期望值为 `[true, true, false, false]`。

验证：

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_acceptance.py -q`，`3 passed`。

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_acceptance.py tests/test_modular_lists.py tests/test_modular_list_consumption.py tests/test_modular_normalization.py tests/test_modular_rendering.py tests/test_modular_kernel.py tests/test_modular_conversion.py tests/test_modular_baseline.py tests/test_modular_source_facts.py -q`，`45 passed`。

全量回归：

`scripts/.venv/bin/python3 -m pytest tests/ -q`，`421 passed in 28.32s`。

固定复现产物目录：`/tmp/wx-issue1-l01-20260909/`。

| 场景 | 报告 | Source AST | Normalized AST | 输出 DOCX |
| :--- | :--- | :--- | :--- | :--- |
| Markdown native-v2 | `/tmp/wx-issue1-l01-20260909/l01.md.report.json` | `/tmp/wx-issue1-l01-20260909/l01.md.source.json` | `/tmp/wx-issue1-l01-20260909/l01.md.normalized.json` | `/tmp/wx-issue1-l01-20260909/l01.md.output.docx` |
| DOCX native-v2 | `/tmp/wx-issue1-l01-20260909/l01.docx.report.json` | `/tmp/wx-issue1-l01-20260909/l01.docx.source.json` | `/tmp/wx-issue1-l01-20260909/l01.docx.normalized.json` | `/tmp/wx-issue1-l01-20260909/l01.docx.output.docx` |
| Markdown legacy 默认 | `/tmp/wx-issue1-l01-20260909/l01.legacy.report.json` | 未生成 | 未生成 | `/tmp/wx-issue1-l01-20260909/l01.legacy.docx` |

两份 native 报告均为 `status=completed`、`source_facts_immutable=true`、八个模块执行、`restart=[true,true,false,false]`。三份 DOCX 均通过 `unzip -t`。

提交：`96083d3`、`8fad0b3`、`9645173`、`ffa0b0a`、`875d16f`、`e843d47`、`121647e`。当前未推送远程。

## L02 至 L06 层级与序列验收

2026-09-09 增加 `tests/test_issue2_acceptance.py`，使用真实 CLI 覆盖 L02 至 L06。Markdown 夹具覆盖最近有效父项、多个续段、外部正文、跳号和标题重启、连续空行与制表符；DOCX 夹具覆盖多级编号跳级及未决发布阻断。

初始红灯：

`scripts/.venv/bin/python3 -m pytest tests/test_issue2_acceptance.py -q`

结果：`2 failed, 4 passed`。L03 暴露多个续段都指向同一 source item 的 AST 合同冲突，L05 暴露标题后的目标编号未重启。

修复：

1. 续段关联改为 owner → continuation → continuation 链，保持每个源节点最多一个 continuation target。
2. 正文标题和附录标题按所属作用域重置 wx-default-v2 目标计数，列表项内部标题不污染外层计数。
3. 列表项直属普通 block 由 Renderer 直接写入，续段保留正文样式且不继承编号承载。
4. 测试共享 CLI runner 和作用域 helper，失败路径检查报告存在并保留 stderr。

验证：

`scripts/.venv/bin/python3 -m pytest tests/test_issue2_acceptance.py -q`，`6 passed`。

`scripts/.venv/bin/python3 -m pytest tests/test_issue1_acceptance.py tests/test_issue2_acceptance.py tests/test_modular_lists.py tests/test_modular_list_consumption.py tests/test_modular_normalization.py tests/test_modular_rendering.py tests/test_modular_kernel.py tests/test_modular_conversion.py tests/test_modular_baseline.py tests/test_modular_source_facts.py -q`，`51 passed`。

全量回归：`scripts/.venv/bin/python3 -m pytest tests/ -q`，`427 passed in 29.64s`。

提交：`4da79d0`、`fb7dc21`。当前未推送远程。

## L07 至 L10 特殊承载与局部作用域验收

2026-09-09 增加 `tests/test_issue3_acceptance.py`，覆盖列表首块为表格、子列表或真实 Markdown 图片内容，真正空列项、行内图片、DOCX 图片列项、合并单元格、嵌套表格、quote 内列表和列表内 quote。

初始 review 暴露的问题包括 DOCX 原生列表项多余空锚点、保存后编号审计只检查全局存在性、垂直合并缺少 OOXML 证据、嵌套表格顺序反转、嵌套表格 RenderMap 定位重复、DOCX 图片段落被忽略和编号定义缺少校验。

修复提交链：`ded9112`、`fb2b7c7`、`e8e0761`、`9a03ba9`、`a708b71`、`f4ae8d8`、`fe269be`、`b6dcefe`、`3f6a8c1`、`e62f8f3`、`d69cffe`。

验收结果：

1. SourceFacts、Source AST、Normalize、RenderPlan、渲染和保存后审计均有专项断言。DOCX 解析器为源引用写入 `parser_version=1.1.0`，事务校验同时比较 `node_path` 和 parser version。
2. DOCX 原生图片列项允许图片段落参与编号检测，资源路径统一到 DOCX 包内路径，动态 DOCX 夹具验证图片资源、编号样式和输出 `document.xml`。真正空的 DOCX 编号段落也进入列表语义识别。
3. 空列项继续生成唯一 marker anchor，已有文字或行内图片的段落直接承担编号承载。真实 Markdown 图片列项和动态 DOCX 图片列项均经过 parser、Source AST、RenderPlan 和保存后审计。独立 block image 节点继续由 AST 合同表达，当前 parser 对列表图片按 inline image 语义保留。
4. 合并单元格通过 `gridSpan` 和 `vMerge` 保存后 OOXML 验收。嵌套表格保持源顺序，RenderMap 使用递增的唯一表格定位值。
5. `check_saved_output()` 逐项核对列表文本、样式、`numId`、`abstractNumId`、`numFmt` 和有效层级，并校验 `numId` 到 `abstractNumId` 的期望映射。保存后图片审计核对图片关系目标、媒体 part 集合和内容摘要，编号或图片篡改会阻断发布。一级无序样式按模板的 `numFmt=none` 审计，二级无序样式按 `bullet` 审计。
6. quote 容器、外部列表项和局部标题的父子关系写入 AST 验收，保存后段落顺序保持。

专项测试：`scripts/.venv/bin/python3 -m pytest tests/test_issue3_acceptance.py -q`，`17 passed`。

相关回归：`scripts/.venv/bin/python3 -m pytest tests/test_issue1_acceptance.py tests/test_issue2_acceptance.py tests/test_issue3_acceptance.py tests/test_modular_rendering.py tests/test_list_numbering.py tests/test_list_semantics.py -q`，`73 passed`。

完整回归：`scripts/.venv/bin/python3 -m pytest tests/ -q`，`444 passed`。

最终双轴 review：Standards 通过，Spec 通过。复核确认真实图片解析链、关系目标、媒体集合、编号映射、空 DOCX 列项和 L09 源树顺序均已闭环。混合 DOCX 文字与图片顺序列入 L14 后续范围，审查过程未修改文件。

## L11 保护语义与输出保真验收

2026-09-10 增加 `tests/test_issue4_acceptance.py`，覆盖 Markdown 和动态 DOCX 两类输入。用例包含代码中的 `1.`、日期、带前缀日期、版本号、带前缀版本、HTTP API、API 标签、无冒号注语、标题样式保护、有效 DOCX 编号以及多行 fenced code。

初始红灯暴露以下问题：保护候选缺少日期、版本、API 标签和无冒号注语；DOCX 有效编号会被文本启发式遮蔽；标题结构候选可以覆盖保护语义；opaque 代码内容未进入 native 输出；保存审计未回读 `w:br`。

修复内容：

1. 统一补充代码、日期、版本、API 和注语义的保护识别，显式 protected 证据优先于编号状态，已验证的 DOCX 列表结构优先于文本启发式。
2. native-v2 结构候选通过显式保护语义候选参与 Resolver 和单批事务，API 标签保持 body，编号注保持 numbered_note，旧 modular 路径的结构统计合同保持不变。
3. opaque 源单元保留原文并沿用父容器渲染，代码中的编号和多行代码不会进入列表语义，表格嵌套位置保持。
4. 保存后审计同时回读 `w:t`、`w:br`、`w:cr` 和 `w:tab`，验收测试逐节点核对 transaction overlay、RenderPlan、输出段落文本和保护样式。
5. Renderer 产物注册为真实 RenderPlan，避免以 RenderMap 代替计划接口。

验证：

`tests/test_issue4_acceptance.py`，`4 passed`。

专项及相关回归命令：

`tests/test_issue4_acceptance.py tests/test_modular_rendering.py tests/test_modular_semantics.py`，`12 passed`。

全量回归：`scripts/.venv/bin/python3 -m pytest tests/ -q`，`448 passed`。

最终双轴 review：Standards 通过，Spec 通过。未跟踪 `_build/` 和 `paseo.json` 保持原状，未纳入提交。列表语义物化时点和通用 Resolver protected-node 集合属于后续架构复核项，当前 L11 验收不阻断。

## L12 正文标题、附录和编号作用域验收

2026-09-10 增加 `tests/test_issue5_acceptance.py`，覆盖 Markdown 和动态 DOCX 两类输入。用例包含正文标题、两个附录、附录内相对标题、附录内列表、附录标题样式、附录分页，以及 DOCX 普通正文“附录 A 一”这类相似文本的误识别防护。

初始红灯：

1. 新增验收首先因 SourceFacts 附录范围缺少 root 节点映射失败。
2. DOCX 列表段落同时命中列表关系和附录移动候选，Resolver 出现 `candidate_conflict`。
3. 生成列表容器在附录移动后才接收源段落，附录范围审计发现节点作用域元数据缺失。

修复内容：

1. SourceFacts 附录范围记录起止 root 节点和标题节点，native 候选通过 create、move 和语义更新操作进入 Resolver 与单批事务。DOCX 生成列表容器先映射到附录范围，避免与列表关系候选争用同一源段落。
2. 附录标题候选使用 Heading、专用附录样式、空标题标记或分类后缀作为证据，普通正文相似文本不会开启新附录范围。
3. 事务对同一父容器内的 create 和 move 决策按源位置稳定排序。移动时向目标节点及后代传播 `appendix_id`、`semantic_scope_id` 和源编号保真标记，附录审计逐节点核对范围归属、标题元数据、相对层级和分页边界。
4. Normalize 展示节点同时保留 `numbering_scope_id` 和 `semantic_scope_id`。模板编号继续按 document scope 计算，附录语义通过独立 semantic scope 记录。Renderer 使用附录标题和一级至三级附录标题样式，并将唯一分页边界写入输出段落。
5. 附录审计在 native Audit 阶段执行并纳入 Publisher 门禁，审计失败时不替换最终输出。

验证：

专项测试：`scripts/.venv/bin/python3 -m pytest tests/test_issue5_acceptance.py -q`，退出码 `0`，`2 passed`。

附录和 native 相关回归：`scripts/.venv/bin/python3 -m pytest tests/test_issue5_acceptance.py tests/test_modular_appendix_consumption.py tests/test_modular_semantics.py tests/test_modular_normalization.py tests/test_modular_lists.py tests/test_modular_rendering.py tests/test_modular_conversion.py tests/test_modular_kernel.py -q`，退出码 `0`，`40 passed`。

完整回归：`scripts/.venv/bin/python3 -m pytest tests/ -q`，退出码 `0`，`450 passed in 28.77s`。

差异检查：`git diff --check`，退出码 `0`。

最终审查：Standards reviewer 发现范围普通块缺少显式附录归属和审计覆盖，已修复并由专项测试验证。规格审查按 L12 条款完成本地核对，候选消费、DOCX/Markdown 范围、编号与语义作用域分离、节点元数据和保存后分页均通过。reviewer 自动实例在限定时间内未返回规格终稿，未将其记为通过。

提交：`acd6ed2`。当前未推送远程。

下一步：开始 L13 验收。

## L13 同级列表切换和缺失编号定义验收

2026-09-10 增加 `tests/test_issue6_acceptance.py`，覆盖动态 DOCX 的同级有序、无序、再有序切换，缺失编号定义，嵌套表格缺失定义，以及未知标记后的深层父项隔离。

初始红灯：

1. 同级类型切换虽能拆组，Source AST 没有记录组边界证据。
2. 缺失 `numId` 定义只保留为 ignored，没有在列表语义报告中明确正文降级。
3. 缺失定义的公共编号证据没有统一保留，未知 `marker_kind` 会默认按有序处理。
4. 未知标记跳过后活动父项未清理，嵌套表格中的缺失定义编号没有按单元格重算。

修复内容：

1. 列表组记录 `run_start`、`same_level_list_kind_switch` 和 `parent_item_switch` 边界，native AST 与报告均保留该证据。同级有序、无序、再有序形成三个明确兄弟组。
2. DOCX 编号证据统一保留 marker 文本、源序号、源层级、缩进单位、重启提示和编号定义引用。缺失或无效定义输出 `missing_numbering_definition` 或 `invalid_numbering_definition`，保留源内容并声明 `body_paragraph` 降级。
3. 未知标记类型不再默认归入有序列表，输出 `unknown_list_marker_kind` 诊断并保留正文。跳过未知标记时清理对应深度的活动父项，防止后续深层项错误继承。
4. 嵌套表格编号按单元格、编号实例、层级和格式重算 `ordinal` 与 `restart`，缺失定义场景的证据与顶层场景一致。

验证：

专项测试：`scripts/.venv/bin/python3 -m pytest tests/test_issue6_acceptance.py -q`，退出码 `0`，`4 passed`。

列表及 native 相关回归：`scripts/.venv/bin/python3 -m pytest tests/test_issue6_acceptance.py tests/test_modular_lists.py tests/test_modular_list_consumption.py tests/test_modular_kernel.py tests/test_modular_contracts.py -q`，退出码 `0`，`28 passed in 3.10s`。

完整回归：`scripts/.venv/bin/python3 -m pytest tests/ -q`，退出码 `0`，`454 passed in 29.51s`。

Python 编译检查：`scripts/.venv/bin/python3 -m py_compile scripts/wxdf/parsers.py scripts/wxdf/semantics/lists.py scripts/wxdf/kernel.py`，退出码 `0`。

差异检查：`git diff --check`，退出码 `0`。验收文件原被 `.gitignore` 的 `tests/*` 规则忽略，已使用强制暂存纳入提交。

最终双轴 review：Standards PASS，未发现 AGENTS.md 硬性规范违反或可明确归类的 Fowler smell。Spec PASS，确认同级切换建组、缺失定义显式降级、编号证据保留和未知标记保真处理符合 `acceptance.md:54`、`modular-pipeline-design-v3.md:105,107,209,288-309`。review 发现的全部问题均已修复并由最终复审确认。

源代码及验收测试提交：`826ede9de0ca35e425596b49e8b5d10ac4ba542e`。当前未推送远程。未跟踪 `_build/` 和 `paseo.json` 保持原状，未纳入提交。

下一步：开始 L14 验收。

## L14 混合 inline 内容顺序和资源关系验收

2026-09-10 增加 `tests/test_issue7_acceptance.py`，覆盖 Markdown 和 DOCX 的文字、行内图片、超链接、文字、软换行组合，覆盖 DrawingML 与 VML 两种 DOCX 图片嵌入形式，并覆盖篡改超链接目标后的保存产物审计拒绝。

初始红灯：

1. DOCX inline 解析把段落文字整体放在图片之前，丢失文字、图片、链接和换行的源顺序。
2. DOCX 超链接没有进入 SourceFacts，渲染阶段只生成带下划线的普通文字，目标关系没有写回输出。
3. 保存产物审计没有比较 inline 结构顺序和超链接目标。
4. VML `v:imagedata r:id` 图片引用未被识别。

修复内容：

1. `scripts/wxdf/parsers.py` 按 XML 子节点顺序读取文字、制表符、换行、DrawingML `a:blip`、VML `v:imagedata` 和超链接，统一保留图片资源哈希与超链接目标。
2. `scripts/wxdf/rendering.py` 为外部链接建立 DOCX hyperlink relationship，将链接子节点按源顺序写入超链接容器，图片、文字和换行均保留在正确位置。
3. `scripts/wxdf/auditing.py` 对混合 inline 段落比较文字、图片、链接、换行和制表符结构，并核对超链接关系目标；原有图片哈希和媒体关系审计继续生效。
4. RenderMap 记录 inline 内容，验收测试覆盖 DrawingML、VML、Markdown、DOCX 和篡改输出负例。

验证：

专项测试：`scripts/.venv/bin/python3 -m pytest tests/test_issue7_acceptance.py -q`，退出码 `0`，`4 passed`。

相关回归：`scripts/.venv/bin/python3 -m pytest tests/test_issue7_acceptance.py tests/test_issue3_acceptance.py tests/test_issue6_acceptance.py tests/test_modular_source_facts.py tests/test_modular_source_consumption.py tests/test_modular_rendering.py -q`，退出码 `0`，`38 passed in 4.33s`。

完整回归：`scripts/.venv/bin/python3 -m pytest tests/ -q`，退出码 `0`，`458 passed in 30.54s`。

Python 编译检查：`scripts/.venv/bin/python3 -m py_compile scripts/wxdf/parsers.py scripts/wxdf/rendering.py scripts/wxdf/auditing.py`，退出码 `0`。

差异检查：`git diff --check`，退出码 `0`。验收文件原被 `.gitignore` 的 `tests/*` 规则忽略，已使用强制暂存纳入提交。

最终双轴 review：Standards PASS，未发现 AGENTS.md 硬性规范违反或明确 Fowler smell。Spec PASS，确认 Markdown、DOCX、DrawingML、VML、SourceFacts、Source AST、规范化、渲染和保存产物审计均满足 `acceptance.md:55` 的顺序与资源关系要求。Spec 初审指出的 VML 图片缺口已修复，复审确认通过。

源代码及验收测试提交：`10bc9ff`。当前未推送远程。未跟踪 `_build/` 和 `paseo.json` 保持原状，未纳入提交。

下一步：开始 L15 验收。

## 真实转换证据

Markdown `input/benchmark_report.md` 通过显式 `--modular-profile native-v2` 转换成功，命令退出码为 `0`。报告路径为 `/tmp/wx-issue1-native-p1-markdown-report.json`，报告显示 `execution_plan.profile=wx_full_rules-native-v2`、八个 native 模块均列入执行计划、事务状态 `committed`、决策数 `21`、模型审计和保存后审计均通过。输出路径为 `/tmp/wx-issue1-native-p1-markdown.docx`，`unzip -t` 退出码为 `0`。

无列表 Markdown 通过显式 `--modular-profile native-v2` 进入同一 native-v2 链，报告显示 `execution_plan.profile=wx_full_rules-native-v2`，无诊断，列表能力按无候选状态标记为 `delivered`。

DOCX 最小列表夹具 `/tmp/wx-issue1-input.docx` 通过 `scripts/main.py --modular-profile native-v2` 转换成功，命令退出码为 `0`。报告路径为 `/tmp/wx-issue1-native-p1-docx-report.json`，报告显示列表 1 个、列表项 2 个、生成锚点候选 2 个且全部消费，事务状态为 `committed`，模型审计和保存后审计均通过。输出路径为 `/tmp/wx-issue1-native-p1-docx.docx`，`unzip -t` 退出码为 `0`。

把模板 DOCX 直接作为输入的试验因模板内图片资源缺少可解析路径失败，诊断为 `RenderError`。该试验未计入通过项。

## 审查与残留项

双轴审查固定点为 `4b595c0`，原始实现提交为 `b44de5f`。本轮已补充显式 profile 路由、列表结构单批事务、续段关系、生成锚点、legacy retype 标准语义更新、注册表实际执行绑定、source reference 预条件校验和 failure policy 发布阻断。

L11 至 L16 的逐项双 parser、页面视觉和外部平台矩阵仍未在本轮补齐，不能据此宣称 Issue #1 全部验收完成。Kylin V10 ARM64 真机和发布流程仍属于外部门禁。远程推送、标签、Release 和公开 Skill 同步均未执行。

下一步：补齐 L11 至 L16 验收矩阵及外部平台证据，再根据外部平台验收结果评估默认策略切换。
