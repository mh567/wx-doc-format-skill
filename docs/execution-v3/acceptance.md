# 验收矩阵与命令

## 1. 环境与命令目录

所有下列 shell 命令从仓库根目录 /Users/harris/Documents/zero项目/wx-doc-format-skill 执行。测试运行器为 scripts/.venv/bin/python3，模板为 assets/wx_template.docx。执行时核对二者实际可用。

旧脚本 scripts/benchmark.py 含过期模板与版本常量，T15 创建专用基准工具后再用于新架构验收。禁止把历史 257 等测试数量作为本轮结果。

基线命令，T00 执行：

```bash
git status --short
git branch --show-current
git rev-parse HEAD
scripts/.venv/bin/python3 --version
scripts/.venv/bin/python3 -m pytest tests/ -q
mkdir -p wx_output/modular-v3/baseline
env -u WX_DOC_FORMAT_RELEASE -u WX_DOC_LLM_ENHANCE scripts/.venv/bin/python3 scripts/main.py --input input/test_baseline.md --output wx_output/modular-v3/baseline/baseline.docx --template assets/wx_template.docx --llm-enhance off --report wx_output/modular-v3/baseline/report.json --source-ast wx_output/modular-v3/baseline/source.json --ast wx_output/modular-v3/baseline/normalized.json
unzip -t wx_output/modular-v3/baseline/baseline.docx
```

T09 完成参数实现后，新路径命令：

```bash
mkdir -p wx_output/modular-v3/candidate
env -u WX_DOC_FORMAT_RELEASE -u WX_DOC_LLM_ENHANCE scripts/.venv/bin/python3 scripts/main.py --input input/test_baseline.md --output wx_output/modular-v3/candidate/baseline.docx --template assets/wx_template.docx --pipeline modular --list-policy wx-default-v2 --llm-enhance off --report wx_output/modular-v3/candidate/report.json --source-ast wx_output/modular-v3/candidate/source.json --ast wx_output/modular-v3/candidate/normalized.json
unzip -t wx_output/modular-v3/candidate/baseline.docx
scripts/.venv/bin/python3 -m pytest tests/ -q
git diff --check
```

DOCX 样例由 tests/modular_support.py 在临时目录生成，并通过 subprocess 调用实际 CLI；测试保留报告路径。另选用户已有代表性真实 DOCX，登记于 manifest 的 local_only_inputs，缺少时记录真实输入覆盖不足，不伪造现场验收。含生成序号的输出需解析 numbering.xml 和段落 numPr，不能仅检查 paragraph.text。

文件式恢复由 test_modular_resume.py 构造真实请求文件和确定性合法/非法响应，使用当前协议生成函数计算哈希，再启动独立进程恢复。测试响应无需真实 LLM。完整 AST 命令只在私有模式使用，发布模式另做拒绝与脱敏断言。

## 2. 列表矩阵

每项用例必须含实际 Markdown 字符串或动态构建 DOCX、公共事实断言、源 AST 关系、策略结果和输出审计。格式不支持某语法时标明不适用原因，不强行要求两格式语法相同。

| ID | 输入与必需预期 |
| :--- | :--- |
| L01 | 有序→无序→有序三级嵌套；每层正确父列项，无序不消耗目标有序计数 |
| L02 | 多个一级项后出现二级项；归属最近有效父项，无跨前项挂载 |
| L03 | 列项内多段正文及子列表后续段；续段保持 owner，只有一处编号承载 |
| L04 | 外部正文隔开两列表；正文留外层，列表组与源续号分别判断 |
| L05 | 跳号、重复 1、非 1 起始、中途重启；源事实保留，目标策略显式映射 |
| L06 | 连续空行、制表符、缩进跳级、多级回退；按语法合同处理，孤立项 unresolved |
| L07 | 列项首块为表格、图片或子列表；列项自身没有段落时用一个承载锚点承载标记，锚点不重复 |
| L08 | 真正空列项自身没有段落，用一个承载锚点承载标记；只含行内图片的列项使用自身段落 |
| L09 | 合并单元格与嵌套表格内列表；单元格内容唯一、作用域隔离 |
| L10 | quote 内列表、列表内 quote；局部标题不影响外部计数 |
| L11 | 代码中的 1.、日期、版本号、API 标签、注1；保护角色不误判 |
| L12 | 正文标题、附录、分节切换；模板编号作用域与语义作用域分开 |
| L13 | 同级 ordered/unordered 切换及缺失编号定义；建组明确或显式降级 |
| L14 | 文字＋行内图片＋链接＋文字、软换行；顺序与资源关系保真 |
| L15 | 修正父项后外观相同；语义修复接受，不能 no_effect |
| L16 | 标题修为列表后重算与恢复；有效修订保持，源变化使旧修订失效 |

## 3. 系统合同矩阵

| ID | 必需验证 | 负责任务 |
| :--- | :--- | :--- |
| C01 | Schema、引用、双父、环、类型、表格跨度与行内链接 | T01/T04 |
| C02 | 独立 parser adapter 与相同语义消费者，无私有对象依赖 | T03 |
| C03 | 候选顺序变更不影响裁决，跨域冲突及 unknown 保真 | T05 |
| C04 | 修订幂等、supersedes、invalidated、conflicted | T05/T12 |
| C05 | 旧视图读取和每类 Patch 翻译，歧义与过期映射拒绝 | T10 |
| C06 | 多阶段暂停、重复/不同响应、输入/模板/版本变化 | T11 |
| C07 | 语义/呈现独立判断、无效果、拒绝候选不污染基线 | T12 |
| C08 | 发布日志、清单、半写故障及恢复，未完成不报告完成 | T09/T12 |
| C09 | 保存后篡改 OOXML 的段落、表格、图片与编号被审计捕获 | T08 |
| C10 | 样式、TOC、题注 SEQ、附录、注释、layout 不变量 | T13/T14 |
| C11 | 全量复制/校验/索引次数受阶段约束，规模增长不平方退化 | T04/T15 |
| C12 | 新旧 CLI、模式清单、release AST 限制及报告脱敏 | T02/T09/T12/T16 |
| C13 | 编译/源码能力清单一致，真实运行时转换与恢复 | T16 |

## 4. 性能与视觉证据

T15 创建 tests/modular_benchmark.py，提供 --baseline、--candidate、--output 参数；参数值为已验证运行配置文件路径。配置固定解释器、入口、模板、输入及模式，禁止接受任意 shell 字符串。旧基线可由受控源码快照或原有 legacy 模式复测，记录其适用性，不能把已改动的旧路径假称原始基线。

预热两次，至少十次同机交替测量。记录小/中/大 N/E/K、P50/P95、峰值内存以及 full_model_copy_count、full_graph_validation_count、index_rebuild_count、stage_commit_count。纯规则 P95 回退不超过 5%，内存回退不超过 10%；短输入的绝对容差在比较前固定并写 evidence。

视觉抽检覆盖多级列表展平、续段缩进、图片前后、单元格列表、目录及附录分页。优先使用当前环境已有可验证文档渲染工具；不可用时保留 visual_pending，不把 XML 通过写成视觉通过。平台缺失标 pending_external，不混入 passed。

## 5. 证据文件格式

每个 evidence/Txx.md 至少记录任务 ID、基线、文件、红灯与绿灯命令及实际退出码、测试统计、运行报告路径、人工期望来源、审查结果、剩余问题和 next_action。日志不存在或命令未执行时写 not_run。

status.json 的 done 不得早于任务卡完成条件。文档编制时所有任务均 pending；执行包的链接和 JSON 校验只验证执行包自身。
