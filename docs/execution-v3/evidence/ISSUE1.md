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

修复提交链：`ded9112`、`fb2b7c7`、`e8e0761`、`9a03ba9`、`a708b71`、`f4ae8d8`、`fe269be`、`b6dcefe`、`3f6a8c1`。

验收结果：

1. SourceFacts、Source AST、Normalize、RenderPlan、渲染和保存后审计均有专项断言。DOCX 解析器为源引用写入 `parser_version=1.1.0`，事务校验同时比较 `node_path` 和 parser version。
2. DOCX 原生图片列项允许图片段落参与编号检测，资源路径统一到 DOCX 包内路径，动态 DOCX 夹具验证图片资源、编号样式和输出 `document.xml`。真正空的 DOCX 编号段落也进入列表语义识别。
3. 空列项继续生成唯一 marker anchor，已有文字或行内图片的段落直接承担编号承载。真实 Markdown 图片列项和动态 DOCX 图片列项均经过 parser、Source AST、RenderPlan 和保存后审计。独立 block image 节点继续由 AST 合同表达，当前 parser 对列表图片按 inline image 语义保留。
4. 合并单元格通过 `gridSpan` 和 `vMerge` 保存后 OOXML 验收。嵌套表格保持源顺序，RenderMap 使用递增的唯一表格定位值。
5. `check_saved_output()` 逐项核对列表文本、样式、`numId`、`abstractNumId`、`numFmt` 和有效层级，并校验 `numId` 到 `abstractNumId` 的期望映射。保存后图片审计核对图片关系、媒体 part 数量和内容摘要，编号或图片篡改会阻断发布。
6. quote 容器、外部列表项和局部标题的父子关系写入 AST 验收，保存后段落顺序保持。

专项测试：`scripts/.venv/bin/python3 -m pytest tests/test_issue3_acceptance.py -q`，`14 passed`。

相关回归：`scripts/.venv/bin/python3 -m pytest tests/test_issue1_acceptance.py tests/test_issue2_acceptance.py tests/test_issue3_acceptance.py tests/test_modular_rendering.py tests/test_list_numbering.py tests/test_list_semantics.py -q`，`70 passed`。

完整回归：`scripts/.venv/bin/python3 -m pytest tests/ -q`，`441 passed`。

双轴 review：Standards 通过。Spec review 在修复编号层级映射回归、补充块图片保存审计并同步本证据后通过，审查过程未修改文件。

## 真实转换证据

Markdown `input/benchmark_report.md` 通过显式 `--modular-profile native-v2` 转换成功，命令退出码为 `0`。报告路径为 `/tmp/wx-issue1-native-p1-markdown-report.json`，报告显示 `execution_plan.profile=wx_full_rules-native-v2`、八个 native 模块均列入执行计划、事务状态 `committed`、决策数 `21`、模型审计和保存后审计均通过。输出路径为 `/tmp/wx-issue1-native-p1-markdown.docx`，`unzip -t` 退出码为 `0`。

无列表 Markdown 通过显式 `--modular-profile native-v2` 进入同一 native-v2 链，报告显示 `execution_plan.profile=wx_full_rules-native-v2`，无诊断，列表能力按无候选状态标记为 `delivered`。

DOCX 最小列表夹具 `/tmp/wx-issue1-input.docx` 通过 `scripts/main.py --modular-profile native-v2` 转换成功，命令退出码为 `0`。报告路径为 `/tmp/wx-issue1-native-p1-docx-report.json`，报告显示列表 1 个、列表项 2 个、生成锚点候选 2 个且全部消费，事务状态为 `committed`，模型审计和保存后审计均通过。输出路径为 `/tmp/wx-issue1-native-p1-docx.docx`，`unzip -t` 退出码为 `0`。

把模板 DOCX 直接作为输入的试验因模板内图片资源缺少可解析路径失败，诊断为 `RenderError`。该试验未计入通过项。

## 审查与残留项

双轴审查固定点为 `4b595c0`，原始实现提交为 `b44de5f`。本轮已补充显式 profile 路由、列表结构单批事务、续段关系、生成锚点、legacy retype 标准语义更新、注册表实际执行绑定、source reference 预条件校验和 failure policy 发布阻断。

L11 至 L16 的逐项双 parser、页面视觉和外部平台矩阵仍未在本轮补齐，不能据此宣称 Issue #1 全部验收完成。Kylin V10 ARM64 真机和发布流程仍属于外部门禁。远程推送、标签、Release 和公开 Skill 同步均未执行。

下一步：补齐 L11 至 L16 验收矩阵及外部平台证据，再根据外部平台验收结果评估默认策略切换。
