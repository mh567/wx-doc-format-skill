# AGENTS.md — wx-doc-format-skill

## 编码偏好

- 不出现"不是...，而是"的句式
- 不出现破折号
- 不随意重命名已稳定的接口

## 项目架构

流水线由 `scripts/wxdf/` 下的 `ConversionKernel` 与 `ModuleRegistry` 编排，共八个阶段：

```
解析 → 范围 → 角色 → 关系 → 规范化 → 渲染 → 审计 → 发布
```

解析产 SourceFacts 与 source AST；范围/角色/关系产候选并裁决；规范化产不可变语义视图；渲染按模板合同写 DOCX；审计校验模型、保存输出、附录保存与样式不变量；发布决定交付。缺陷修复优先放在解析和 source AST 生成阶段。规范化、渲染和审计消费明确的 AST 语义并进行验证。

LLM 增强为 Capability 插拔架构，当前能力：
- `toc_region_review` — 目录区域复核
- `list_detect` — 功能点列表识别
- `caption_gen` — 表格/图片题注生成
- `document_review` — 审计后受限复核（带 `review_packet`）
- 新增能力只需注册 `CapabilityConfig`（`scripts/wxdf/capabilities.py`）

入口：`scripts/run.sh`，它选择解释器、检查依赖并执行 `scripts/main.py`。开发时也可以直接用 `scripts/.venv/bin/python3 -m main`。

### 顶层模块归属

流水线的实现位于 `scripts/wxdf/`。以下顶层模块保留在 `scripts/`，因为它们被流水线复用：`document_model`、`table_formatting`、`table_semantics`、`appendix_semantics`、`front_matter`、`note_semantics`、`toc_detector`、`toc_contract`、`unordered_lists`、`text_utils`、`template_profile`、`reporting`。新增语义能力放入 `scripts/wxdf/semantics/`。LLM 能力的注册与模式选择位于 `scripts/wxdf/capabilities.py`。

## 关键决策记录

### 1. 列表最多两级，按种类落在对应层级样式
- 有序列表嵌套：第一层级用一级有序列表样式（a) b) c) 格式），第二层级用二级有序列表样式（1) 2) 3) 格式）
- 无序列表嵌套：第一层级用一级无序列表样式，第二层级用二级无序列表样式
- 有序与无序交叉嵌套：先出现的种类为一级，后出现的种类为二级，按种类是否已出现过判定，优先一级样式再二级样式
- 最多识别两级列表。层级更深时夹到二级并产出诊断；更深的结构应改用标题层级表达，不用列表
- 每章节遇到标题后从 a) 重新开始
- 2026-09-15 用户裁定：本决策取代原先「所有有编号的列项统一使用一级列表样式」的表述，嵌套项按上述两级模型处理
- 2026-09-20 用户裁定：源未写标记的并列要求按列表处理，规则如下
  - 整节正文由同一句式的并列条款构成（同一标题下连续段落，每段为完整条款并以分号或句号收尾，首二字相同），且该句式在文档别处已被列项样式确认，才推断为一级有序列表。确认门槛为至少两条，且不少于本文档已确认句式最多者的一半（主导句式），避免两个相同首字偶然命中。任一条件不满足即不推断，产出 `inferred_requirement_list` 记录（info，落在 degraded/downgrade 记录中，不把普通文档升级为警告）。文档自身从未确认过列项句式时不推断任何内容
  - 条款内的公式型文本保护（公式、日期、API 路径、IP、法规条款）不阻止整节推断：整节同一句式本身就是结构确认，与已确认编号同级。角色、代码样式、来源保护与标题保护仍然阻止
  - 一段内用软换行（`w:br`）写下的多条并列条款按条款切分为多个条目。首条留在源段落（`update_node_content` 裁去其余分句），其余分句生成为独立段落块与条目，`generated_by` 为 `list_semantics.parallel_clause_split`，`generation_basis` 指向源段落，块自身不携带源引用
  - 切分只作用于被判定为列表项的段落，且不作用于表格单元格内的段落。非列表项段落保留原有换行

### 2. 图片段落不继承任何样式
- pPr 只保留 jc=center 与清零的 ind（`firstLine=0 firstLineChars=0 left=0 leftChars=0`）
- 2026-09-20 用户裁定：模板自身的图片段落存在清零缩进的形态，交付形态取这一种。只写 jc=center 会让图片段落继承模板 Normal 的 2 字符首行缩进，居中图整体右移

### 3. 题注用 SEQ 域代码
- 编号标识符与域形态取自模板自身的题注域（本模板为 `SEQ 表` 与 `SEQ 图`），不写死英文标识符。Word 的交叉引用按序列标识符匹配编号项，标识符必须是模板的那一个，否则「引用类型」选 图/表 时编号项列表为空。域按模板写成复杂域（`fldChar begin` + `instrText` + `separate` + 缓存编号 + `end`），正文按章编号的题注在标签与编号之间留一个空格，例如“表 9-1 设备接入核对表”；全文连续编号和附录题注保留已有形式。模板没有对应种类的题注域时回落 `表` / `图`
- 表题注在表格上方，图题注在图片下方
- 图题注只给独占段落的图片。与正文同段的内联图标、表格单元格内的图片不配题注
- 题注自带源编号且属于附录范围时保留源编号形态（`表A.1`），不注入文档级 SEQ，因为该序列只覆盖正文
- 2026-09-22 用户裁定：正文题注默认按关联章节编号输出（`图5-1`、`表6-2`），域形态即 Word 自身「包含章节号」的写法：标签 run + `STYLEREF 1 \s`（取最近一级标题的编号，模板 `Heading 1` 自带自动编号）+ `-` + `SEQ 表 \* ARABIC \s 1`（`\s 1` 使序数逐章重置）。章号与章内序数在渲染遍历时按一级标题边界计算并写入缓存结果，故未重算域的阅读器也显示正确编号，Word/WPS 打开时经 `updateFields` 重算。章号取正文一级标题（`is_body_outline_heading` 且派生大纲级别为 1）；附录标题不推进章号，附录题注仍按上一条保留源编号。首个一级标题之前的题注无章可借，回落纯序号形态。`--caption-numbering sequential`（默认 `chapter`）切换为全文连续编号（`图1`）

### 4. API 文档特殊处理
- API 标签默认不识别为标题
- 2026-09-17 用户裁定：源已声明的标题样式强于 API 标签保护。`接口说明`、`请求参数` 这类标签在接口文档里就是章节标题，源把它写成 Heading 3 时标题角色保留，标签理由仍作为证据落在角色候选上。没有标题样式的光标签仍不产生标题角色，标签文本对列表的保护不变
- API 示例表格不插入题注
- 代码示例表、`callout` 表和 `layout` 表保留源底纹（表级与单元格级都保留），普通语义表格（`data`）不保留源底纹，按模板外观输出。判据与表格格式合同同一处：`table_role` 只要不是 `data` 就恢复源底纹

### 5. 排版格式
- 普通语义表格单元格使用 表正文 样式
- 普通语义表格使用全框线、`autofit` 和表级默认单元格边距
- 单元格不写入独立 `tcMar`，WPS 中继承整张表格的默认边距
- 普通语义表格最左列的编号列使用模板的普通十进制编号（1、2、3），不套用一二级列项样式；单元格里其他位置的编号仍按列项处理
- 公式编号容器等 `layout` 表在解析阶段确定角色，保留源宽度、边距、行高、无框线和段落格式

### 6. 目录
- 源文档自带目录被自动跳过
- 输出以模板自动生成的目录为准，转换器只识别源目录区间并整体替换为模板的目录部分，不重写模板的域指令
- 正文区域使用 Heading 样式的“目录”或“目次”保留原标题层级
- 目录标题固定为“目  次”，使用黑体四号、不加粗、居中和 1.25 倍行距
- TOC 1 至 TOC 4 使用宋体小四、不加粗和 1.25 倍行距，按层级依次缩进 0、2、4、6 字符
- 目录域只包含 1 至 4 级标题，并写入打开文档时更新域标记

### 7. 附录
- 全文通用块生成后在 source AST 定稿前执行附录候选裁决和范围内标题重分类
- 独立“附录”可使用 Normal、Heading 1、Heading 2 或其他样式，分类文字、A.1 和专用样式均为可选证据
- 正文多级编号中的“附录”使用 `nested_main_numbered_heading` 负证据阻止误开启范围
- 附录内普通 Heading 按最浅层级重建一至三级相对层级
- 附录标题正文保留已有分类和标题行，根据实际内容使用零至两个 Shift+Enter，附录编号由模板 auto-numbering 显示
- AST 记录每个附录的起止范围，范围内的块共享 `appendix_id`
- appendix AST 记录置信度、正负证据和 `layout.page_break_before=true`，渲染时保证唯一显式分页边界
- 附录已有题注保留 A.1 形式的源编号
- 附录容器在 AST 中的落位从 AST 自身读取：创建索引取范围内首个节点在父节点子节点列表中的位置。SourceFacts 的 root 位置不能兼作该索引，因为 AST 会自行物化节点（内联图片、列表容器），用事实位置会把容器插到范围之前的块前面，把这些块挤到附录之后
- 附录标题在交付文档中带直接大纲级别 0（目录第 1 级），附录一至三级标题不写直接级别、沿用样式提供的大纲级别（目录第 2 至 4 级）。这是 2026-09-16 的裁定 A：模板的目录域按大纲级别生成条目，而模板的 `附录标题` 样式位于大纲级别 1，会让附录标题落到目录第 2 级；模板与源文档的目录缓存都把附录标题放在第 1 级
- 目录域通过 `\\t` 显式映射附录标题和一至三级附录标题：该映射属于已删除的旧诊断链路的域改写（`toc_contract.TOC_CUSTOM_STYLE_LEVELS`），当前链路按上一条用大纲级别表达，不改写模板的域指令

### 8. 正文大纲契约
- `level` 保留源语义层级，解析等价、决策账本和迁移合同继续按源层级比较，不做夹紧
- 正文标题的渲染层级由 `presentation.outline` 的派生 `outline_level` 承载，按阅读顺序取 `min(6, previous + 1)`，游标跟随派生值，一处归一向下递推
- 附录标题由附录语义拥有并跳过，不推进正文游标，与 `check_model` 的跳过规则一致
- 渲染与审计读派生值，缺失时回退源层级；`level` 不承担输出大纲职责
- 源层级与渲染层级的分歧作为 `source_heading_level_normalized` 上报，属可修复项但不阻断交付，交付文档已满足大纲合同
- 渲染大纲本身违反合同时报 `heading_level_jump`，仍然阻断
- 大纲归一的判定属于解析和标准化阶段，不依赖 LLM 复核

### 9. 行内不可重建对象
- 公式（OMML）、域代码、交叉引用与书签无法按语义重建，解析阶段登记为行内原子并保留源引用，渲染阶段只按引用复制
- 原子的载荷以资源形式保留，资源 `kind` 为 `inline_fragment`，通过源引用解析
- DOCX 与 Markdown 两个解析器对未识别的行内对象一律登记为 `opaque_inline` 并产出诊断，禁止静默丢弃
- 域原子携带 `instruction` 与 `cached_result`，文本投影包含缓存结果，使审计与渲染读到同一文本
- 题注自己的编号域在题注语义中被丢弃，由题注模块重建，避免与复制回来的源域重复

### 10. 证据优先级与呈现归属
- 显式标题样式强于注的文本前缀；仅当标题文本是光秃秃的“注”标记时由样式裁决，“注2 说明”这类带序号的注仍是注
- 手工编号标题按 2026-09-16 裁定处理：源已声明的标题（DOCX 标题样式，或 Markdown 的标题标记）强于 `manual_heading_marker` 文本保护，标题保留层级，交付文本去掉段首手工标记。标记词表归 `text_utils.strip_heading_marker_prefix` 单一所有者，标记由 `model.consume_leading_text` 按可见字符数从行内内容取出，标题内部的 marks 保留。整段只有标记时保留源文本并留 `heading_marker_only_text` 记录，标记跨过无法消费的行内对象时留 `heading_marker_not_consumed` 记录。同一优先级按 2026-09-17 裁定适用于 `api_label`（见决策 4）。其余文本型保护理由（API 路径、日期版本、法规条款、公式、IP 地址）优先级不变，带角色的保护结论（注、有编号注、代码块、公式原子）亦不变
- 列项样式层级的 `numFmt=none` 且 `lvlText` 非空时仍是列项，标记与列表种类由 `lvlText` 决定，不由 `numFmt` 决定
- 段落呈现记录在 `presentation.paragraphs`。由该记录决定对齐的只有两类段落：图片段落居中（决策 2，模板的图片段落不带样式，带 `jc=center` 与清零的 ind），以及公式编号保留源对齐（源把它右对齐，没有样式表达这一点）。附录标题的记录承载的是大纲级别而非对齐（决策 7、8）。**其余段落的对齐一律由模板样式决定**，正文即模板 `Normal` 的两端对齐，作者的其它对齐不保留。这是 2026-09-18 的裁定：一切以模板要求为准，审查按模板报出正文对齐偏差，转换器不为其保留作者对齐
- 语义表格单元格的样式与对齐由表格格式合同拥有，段落呈现不覆盖它
- 源目录由 `w:sdt` 承载时，解析阶段为该控件内每个段落与表格登记 `source_toc` 证据，范围模块据该证据排除，容器随其子树一并移除。控件同时包住封面内容时，封面表格随之离场，不留空表
- 源用直接 `w:outlineLvl` 声明标题（样式仍是 Normal）时，解析阶段按直接属性优先、样式链兜底的顺序记下 `outline_level` 证据，角色模块据此判标题，层级取 `min(outline_level + 1, 6)`；`outlineLvl=9` 是 Word 的正文文本值，不产生标题。该证据同时让列表模块不把标题提升为列项
- 列项自身的段落就是承载标记的段落，空段落也不例外；只有列项自身没有段落时才生成承载锚点，且只生成一个

### 11. Markdown 读取
- 软换行是空格，只有硬换行成为换行；来源同一段的折行不改变输出换行
- 方言关闭 HTML，标签按文本到达，读取时剥离标签保留可读文本并记录 `html_inline`
- 源没有一级标题时，结构语义按文件名生成 `role=title` 的块（`generated_by=structure_semantics.filename_title`），并在报告写入 `markdown_title_generated` 风险提示

### 12. 文档标题派生
- 模板在目录上方渲染一行文档标题。源未声明标题时补一个标题块，`generated_by` 为 `front_matter.document_title`
- 2026-10-14 用户裁定：DOCX 的标题来源依次为封面标题、文档核心属性标题、文件名，由 `front_matter.analyze_front_matter` 单一所有。解析阶段把结果记入 `facts["source"]["document_title"]`，`build_source_ast` 据此把标题块注入 source AST 首位
- 标题块属于 source AST，检查点各处重建 AST 得到同一标题，规范化视图与保存的源 AST 保持一致，标题不进入候选与决策账本
- 只有正文能渲染的根级段落算源已声明标题。标题样式落在表格单元格内或源目录控件内时不算，仍补生成标题
- 报告按源类型写入风险提示：DOCX 为 `document_title_generated`，Markdown 为 `markdown_title_generated`（见决策 11）

### 13. 交付形态：纯 Python 源码
- 仓库直接包含可运行的 Python 源码、测试、模板与文档，不再编译、加密或组装发布包
- 运行时由目标机 Python 3.10 或更新版本解释执行，`scripts/run.sh` 负责选择解释器、检查依赖并执行 `scripts/main.py`
- 依赖只声明在 `requirements.txt`，由目标机通过 `scripts/setup.sh` 或 `pip install -r requirements.txt` 安装。仓库不携带 `.so`、`.dylib`、`.pyd`、wheel 或 `payload.bin`
- `lxml` 是唯一的原生扩展，安装在目标机 Python 环境或本目录 `.venv` 中，不进入仓库，因此不会触发对 Skill 目录的原生二进制扫描
- 离线 zip 在 `wheelhouse/` 携带对应平台与 Python 小版本的依赖 wheel。首次运行 `scripts/run.sh` 会从包内安装到 `.venv`，固定使用 `--no-index`，不访问网络
- 从源码仓库安装依赖需要联网，执行 `scripts/setup.sh`；离线环境执行 `scripts/setup.sh --offline`
- `scripts/main.py` 通过 `scripts/../VERSION` 读取版本，源码树与 Skill 目录布局一致

## 输出验证不变量

- unexpected_styles 必须为空
- 仓库内原生二进制数为零，不得出现 `.so`、`.dylib`、`.pyd`、`.pyc`、`.pyo`、wheel 或 `payload.bin`
- 离线 zip 的 `wheelhouse/` 覆盖 `manifest.json` 声明的平台与 Python 小版本，首次运行使用 `--no-index`，不访问网络
- 标题文本不含手工编号
- 普通语义表格单元格全部为 表正文
- 表格格式合同的段落部分覆盖单元格内全部段落，不止每格首段（渲染器在单元格内容生成后执行合同，`strip_direct_format=False`，因为列项编号和源表头 marks 由渲染器所有权持有）
- 代码示例表等非 `data` 表格的源底纹保留（表级与单元格级），普通语义表格不保留
- `layout` 表不进入普通表格格式合同
- 目录标题、TOC 1 至 TOC 4 和目录域最大层级通过专项审计
- 附录范围、标题语义、一至三级样式、多级编号、软换行和附录题注保真通过专项审计
- 列表每章节重启
- 源文档自带目录区段不进入输出，目录域只来自模板
- 公式、域代码与书签按源引用复制，数量与源文档一致
- 表格编号列使用模板普通十进制编号，不出现列项样式
- 源没有声明标题时仍产出文档标题：DOCX 给出 `document_title_generated` 风险提示，Markdown 给出 `markdown_title_generated`

## 多 Agent 协作分工

- 本项目统一使用 Command Code 实施，允许 Command Code 协同多个智能体共同完成。
- 典型协同：架构智能体负责接口与归属定案、任务拆分和验收；实施智能体负责编码与测试；审查智能体负责独立审查与复审。

不声明项目级外部执行主体或模型例外。

硬性规则：改 .py 前必须先声明路径（→架构智能体定案 / →实施智能体实现 / →审查智能体独立审查）。

## 仓库管理

执行分支操作、标签或 Release 前，必须完整读取 [`docs/development-workflow.md`](docs/development-workflow.md)。

- 只维护一个公开仓库 `mh567/wx-doc-format-skill`，源码、测试、文档同仓。
- 本地 `dev` 推送到公开仓库的 `main`，不再保留私有仓库与生成树。
- 发布打 `vX.Y.Z` tag，并上传按平台区分的离线 zip 作为 Release 资产。
- 历史重写、强制推送、同版本标签移动或 Release 资产替换需要用户明确授权。

## 常用命令

```bash
./scripts/setup.sh --dev

./scripts/run.sh \
  --input input/xxx.docx \
  --output wx_output/xxx.docx \
  --template assets/wx_template.docx \
  --report wx_output/report.json

.venv/bin/python3 -m pytest tests/ -q
```
