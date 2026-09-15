# 当前架构收敛发现

## 2026-09-14 真实 DOCX 基础格式语义修复

- 真实文件的 `1.`、`a)` 段落没有 `numPr`，native DOCX SourceFacts 没有生成 marker_kind，列表规范化没有输入证据。
- 真实文件有 31 个非空段落以制表符开头，native 输出完整保留这些制表符，同时目标 Normal 样式带首行缩进，形成正文首行缩进叠加风险。
- 图片独立段落后面的“助手配置用例图”“智能对话用例图”“副驾驶用例图”“AI助手顶层数据流图”没有图表前缀，当前 caption_parts 无法识别，Source AST 没有 authored caption association，输出生成空图题注并保留普通正文标题。
- `_docx_style_role()` 只识别 Heading 1 至 Heading 6，Heading 7 和 Heading 8 的结构候选会被降为 body。
- 统一修复方向是扩展源事实和语义模块接口，Renderer 继续只消费已裁决的 list、caption、heading 和 paragraph presentation 结果。
- 列表切片验证：真实文件 source position 52 的 `1.` 至 `5.` 和 position 173 的 `a)` 至 `g)` 均进入 ordered list evidence；SourceFacts 保留原文，Source AST 列表物化后去除可见前缀，供模板编号生成。

2026-09-12：当前 native-v2 顶层链路合理，主要剩余风险集中在复核链重复编排、能力总线弱类型、执行计划依赖表达和实现文件职责集中。四张收敛工单已写入 `docs/execution-v4/architecture-convergence.md`。

2026-09-12：`native-reviewer` 直接调用 Resolver、Transaction、Normalization、Render 和 Audit。该实现可以工作，但复核候选存在与普通转换链分叉的可能，因此列为 AC-1 首要收敛项。

2026-09-12：`ModuleExecutionContext` 通过字符串能力名和 `dict[str, Any]` 传递工件，同时保留 `request.options` 与 `services` 访问路径。当前运行期读写观测有效，关键载荷合同仍需加强，因此列为 AC-2。

2026-09-12：Registry 依赖检查基于固定阶段排序和注册顺序。当前固定链可验证，后续扩展 Module 时需要显式依赖和确定性调度合同，因此列为 AC-2。

2026-09-12：`builtin_modules.py`、`kernel.py` 和 `run_store.py` 职责集中。拆分属于维护性收敛项，必须保持稳定 CLI、公开报告合同、编译模块清单和 legacy 发布拒绝行为，因此列为 AC-3。

# 历史发现：execution-v4 初始切换

2026-09-10：源码 native-v2 纯规则主链已形成，但公开 Skill 默认命令、LLM 增强、文件恢复、审计复核、分发冒烟和编译模式清单仍依赖 legacy。完整切换需要先开放三项 modular 模式，再切默认，随后移除发布运行时 legacy 表面。

2026-09-10：执行包拆为 `docs/execution-v4/T00.md` 至 `T09.md`。Kernel 加深、原生能力补齐、默认切换、薄 Skill 契约和 legacy 删除分别设置独立审查门禁。远程发布保持独立授权。

# 历史发现：方案 3.1 完整迁移

2026-09-08：方案 3.0 已写明完整能力映射和阶段 G 的双渲染路径收敛，但执行任务把 module 存在性测试当成主链迁移完成。实际 ConversionKernel 只调用 parser、list analysis、normalize、render 和轻量 audit。registry 没有执行实现，也没有被 Kernel 调用。regions、structure、protected、tables、captions、appendices 等 module 未进入生产计划。

真实 Markdown 诊断显示：首个一级标题在 Source AST 中仍为 heading level 1；AST 角色合同缺少 title；表格没有表头语义字段；数据表没有 caption block 和 caption_target 关联。模块化 Renderer 清空模板正文且未调用旧模板目录片段和 template finalizer。模块化审计未检查文档标题、目录、题注 SEQ、模板样式和完整表格合同，因此产生假通过。

迁移应分为两层。第一层通过 Legacy Adapter 将旧能力完整接入新 Kernel，恢复能力等价。第二层在相同 Interface 和黄金测试下逐项替换为 Native module。可插拔 seam 只设在真实存在多实现的 Parser、SemanticModule、TemplateProvider、Renderer、AuditModule 和 EnhancementExecutor，普通 OOXML helper 保留为 module 内部实现。

2026-09-08 实施结果：ModuleRegistry 已绑定 descriptor 与可执行实现，ConversionKernel 会校验 `wx_full_rules` 必需能力并执行计划。未注入完整 Adapter 时返回 `MODULAR_PROFILE_INCOMPLETE`，不再输出语义缺失的成功文档。CLI 已注入 `legacy-full-v1` 过渡 module，保留旧解析、规范化、渲染、目录片段、finalizer、审计和文件协议能力。

AST 2.0 已增加 `title` role、table `header_rows`、table_cell `cell_role`、生成题注来源和 `caption_target` 关联。模块化 CLI 的 `--source-ast` 与 `--ast` 已分别输出 AST 2.0 和 Normalized AST 1.0。

真实 `benchmark_report.md` 与 `assets/wx_template.docx` 验收通过：文档标题样式为“文档标题”，TOC 域 1 个，SEQ Table 域 2 个，2 张表格的表头与单元格完整，旧审计项全部通过，`risk_warnings=[]`。LibreOffice 页面渲染中文缺字在 legacy 对照件中同样出现，当前证据指向渲染环境对模板字体的兼容限制。

2026-09-08 阶段 6：新增 `TemplateBundle` 深 module interface，一次加载模板路径、SHA256、样式、编号、表格、目录、附录、分页和题注序列合同，目录 OOXML 以冻结字节保留。Normalizer 只写入可序列化的呈现合同，Renderer 通过 TemplateBundle 恢复目录片段和 `updateFields`。模块化 CLI 导出的 Normalized AST 已包含真实模板合同。

# 历史记录：方案 3.0 执行包发现

2026-09-08：当前在 dev；模板为 assets/wx_template.docx；标准测试命令为 scripts/.venv/bin/python3 -m pytest tests/ -q。docs/* 和多数新 tests 文件受忽略规则影响，需在未来私有实施阶段精准放行。发布模式禁止完整 AST 导出，诊断验收只能在私有源码模式进行。预存 _build/ 与 paseo.json 保留。session-catchup 正常退出，无补充输出。历史发布授权不自动扩展为本次发布授权。

执行包额外核对：当前 HEAD 为 cd68200d521146508c306ca7f62f32590d729587，私有运行时 --version 为 0.12.19，开发 Python 为 3.14.4。scripts/benchmark.py 含旧版本及旧模板常量，T15 规定另建有固定输入合同的基准入口。执行包内容、依赖和路径验证通过，60 个拟新增实施文件仅为计划，不是当前已有实现。

# 发现

## 2026-08-24 闭源核心与内网离线分发初步盘点

- 当前本地仓库以 `dev` 远程连接私有 `wx-doc-format-skill-dev`，以 `origin` 远程连接公开 `wx-doc-format-skill`，已具备私有开发与公开分发的双仓库基础。
- 公开 Skill 当前通过 `python3 scripts/main.py` 直接调用，`scripts/` 中包含解析、规范化、渲染、审计与 LLM 协议等全部核心 implementation。
- 当前运行时要求 Python 3.10+、`python-docx`、`lxml` 和 `markdown-it-py`。闭源分发后应将这些依赖收入原生制品，使内网终端无需 Python 或 pip。
- `SKILL.md` 公开了较多内部模块名、处理流程和裁决细节。后续应收缩为触发方式、CLI 参数、输入输出、错误码、验证不变量和已知边界。
- 内网离线部署不能依赖首次运行下载 GitHub Release。需要可由管理员携带和校验的完整离线包。
- Nuitka 官方建议先用 standalone 模式排查依赖与数据文件，确认可分发后再切换 onefile。改造应把 standalone 设为构建验证产物，onefile 设为对外候选产物。
- Nuitka onefile 运行时会解压到临时或指定缓存目录。内网终端若有安全软件、无临时目录执行权限或较严格的白名单，standalone 目录包可能更稳定。
- GitHub 当前托管 runner 已提供 Windows x64、macOS Intel、macOS arm64、Linux x64 和 Linux arm64 标签。部分 ARM 标签仍处于预览状态，生产发布需要锁定 runner 标签并保留自托管 runner 备选。
- Nuitka 标准版会将 Python 源码编译为原生制品，但字符串常量、规则名、提示词和部分调试信息仍可能被读取。严格的加密验收需要 Nuitka Commercial 的 data hiding 与 traceback protection，或自行实现等价保护。
- 当前公开 `origin/main` 仍包含全部核心 `scripts/*.py`，历史源码已经公开。清理公开仓库历史可降低随手获取的便利性，无法撤回既有 clone、fork 或缓存副本。
- 为保持只维护两个 Skill 仓库，应保留 `wx-doc-format-skill-dev` 作为私有源码仓库，将 `wx-doc-format-skill` 原地转为公开分发仓库并替换为无源码历史。
- 公开分发的外部 seam 继续使用现有 CLI 参数。联网安装 Adapter 在安装期下载并校验制品，离线安装 Adapter 只读取本地离线包。两者安装后共享同一本地运行 interface，转换过程不发起网络请求。
- 内网默认制品建议使用 Nuitka standalone 目录经压缩后分发。onefile 保留为后续可选制品，需要先通过临时目录执行、安全软件、白名单和冷启动测试。
- 离线包只构建 Windows x86_64、macOS arm64 和 Kylin V10 arm64 三个目标，另行提供包含三个平台制品的管理员离线介质包。
- 发布版报告只保留稳定结果 schema、风险类型、样式不变量与输入输出摘要。完整 AST、候选打分、证据权重、内部规则名和原始 traceback 只进入私有诊断构建。
- 内网 LLM 增强优先保留文件协议和 `--llm-command` 外部命令 Adapter。基础离线制品不携带 OpenAI 或 Anthropic SDK，也不执行云端自动探测。
- 安全发布门禁需覆盖源码测试、standalone 冒烟、安装后转换、断网运行、泄漏扫描、符号剥离、SBOM、制品哈希、签名和目标系统验收。

## 2026-08-24 Nuitka 实现环境发现

- 当前本机为 macOS 26.6.2 arm64，已安装 Apple clang 21，具备本地 C 编译器。
- 项目 `scripts/.venv` 使用 Python 3.14.4，已安装 `python-docx 1.2.0`、`lxml 6.1.1`、`markdown-it-py 4.2.0` 与 pytest，当前没有 Nuitka。
- Nuitka 4.1 已经持续修复 Python 3.14 兼容性，官方路线图仍将 3.14 完整支持列为后续目标。生产构建应锁定 Python 3.13，本机 3.14 只用于可行性冒烟。
- 本机当前没有可直接使用的 Python 3.13 安装。五平台工作流需通过 `actions/setup-python` 统一锁定 3.13。
- 首轮实现将开发版 `SKILL.md` 直接复制到离线包，会同时导致调用缺失的 `scripts/main.py` 和泄露内部模块名，必须使用独立的公开 Skill contract。
- 覆盖式升级会保留旧公开版 `scripts/*.py`，应使用同目录 staging、旧版备份和整体切换，验收时确认目标目录无 Python 源码残留。
- Ubuntu 24.04 原生制品不能直接标记为 Kylin V10。标准 Nuitka Linux standalone 会受构建机 glibc 基线影响，Kylin 候选制品应在更低 glibc 基线容器或 Kylin 自托管 runner 构建，并保留真机冒烟门禁。
- GitHub Artifact 直接上传目录会丢失 POSIX 执行权限，应先产生 tar.gz，Windows 使用 zip，再上传归档文件。
- 打包后验证必须执行真实 runtime，至少覆盖 `--version` 和最小 Markdown 转 DOCX；占位二进制文件只能验证组装逻辑。
- 当前 manifest 与 SHA256 可提供完整性检查，来源真实性仍需发布签名密钥。本阶段不伪造签名，将其保留为发布前门禁。
- Nuitka 会打包 `python-docx` 的 `docx/templates` 数据文件，同时省略只承载已编译模块的 `docx/parts` 目录。`python-docx` 使用包含 `parts/..` 的相对路径访问模板，因此发布运行时必须显式保留空的 `docx/parts` 目录。
- 本机 Python 3.14.4 处于 Nuitka 4.1.2 实验支持范围。制品位于中文父目录时在 `appendStringSafeW` 中止，复制到纯 ASCII 路径后可正常启动和转换。Python 3.13 三目标 CI 与中文路径目标验证仍是发布门禁。
- standalone 目录中没有 `.py`、`.pyc` 或 `.pyo`，但本机 59 MB 运行时仍包含 Python 动态库、扩展模块和可识别模块信息。保护目标是显著提高复制与二开成本，不能视为不可逆加密。
- manylinux2014 ARM64 的 Python 3.13 环境安装 Pillow 12.3.0 时会回退到源码构建。基础镜像缺少 JPEG 开发头文件，本地 Linux ARM64 构建入口需要先安装 Pillow 编译依赖。
- Nuitka 4.1.2 standalone 可执行文件位于中文路径时，macOS ARM64 与 Linux ARM64 均在程序入口前收到 SIGABRT。Python 3.13、普通 UTF-8 locale 和业务代码均无法规避，发布安装器需要 ASCII 路径门禁，或升级到经最小探针验证修复的 Nuitka 版本。
- Nuitka 4.1.3 的最小 standalone 程序在 macOS ARM64 中文路径下仍收到 SIGABRT。当前发布合同明确要求可打印 ASCII 安装路径，安装器和启动器同时执行检查。
- manylinux2014 ARM64 制品已在 ARM64 容器内通过真实转换、完整性校验和安装验证。该制品仍是 Kylin V10 候选件，交付前保留真机冒烟门禁。
- 使用 macOS 26.5 SDK 和当前 Homebrew Python 3.13 生成的 Mach-O 最低系统版本为 macOS 26.0。该制品用于本机可行性验证，通用 macOS ARM64 发布需要低版本 macOS 构建环境或兼容的 Python 工具链。
- 公开仓库在缺少个别平台制品时仍可作为 Skill 安装。平台可用性以版本化 `artifacts/AVAILABLE_PLATFORMS.txt` 和 `SHA256SUMS.txt` 为准，启动器在获取运行时前停止不可用平台。
- 薄 Skill 支持两种首次运行路由：默认从同版本 GitHub Release 获取归档，内网通过 `WX_DOC_FORMAT_ARCHIVE_DIR` 从本地介质获取。两条路由都先验证归档 SHA256，再调用平台包安装器验证包内文件集。
- 当前公开仓库只有 `main` 分支和 `v0.12.18` annotated tag，两者均可达旧源码提交链。仓库没有 fork、PR、开放 Issue，有 2 个 star，因此可达引用范围较小。
- GitHub 官方文档说明，仅重写并强制推送历史后，旧提交仍可能通过旧 clone、fork、已知 SHA 缓存页面或 PR 引用访问。GitHub Support 对非敏感数据通常不执行服务端彻底清除。来源：https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository

- 当前 `_docx_list_shape()` 对存在编号描述的段落直接使用 `ilvl`。
- `normalize_unordered_hierarchy()` 对已检测编号同样优先使用 `ilvl`，忽略缩进型子列表。
- 有序列表不存在等价的缩进层级重建。
- 渲染端已消费 AST 的 `level` 和 `list_type`，因此修复应前置到 AST。
- 已新增 `list_hierarchy.py`，以 `ilvl`、缩进簇和活动父项重建每个连续列表运行区间。
- 解析阶段保留命名 WX 二级样式的声明层级，规范化阶段会将缺少父项的孤立嵌套项回退为一级。
- `audit_list_preservation()` 现会报告 `missing_list_parents`。
- 统一层级解析器现保留源编号定义的 `restart` 边界，避免相邻独立有序列表被合并。
- 父项审计现按顺序维护活动层级栈，拒绝后出现、同级或更深层级的伪父项。
- Markdown 题注当前先解析为正文，规范化阶段只改写 `block_type`，没有调用 `caption_parts()` 提取标签与编号。渲染器因此输出重复的“表”。
- Markdown 图片语法当前同样作为正文处理，图题注无法关联图片并触发孤立题注审计。
- 修复后，Markdown 解析阶段使用 `caption_parts()` 生成净题注文本、题注类型和自动编号语义。渲染阶段为表题和图题写入 `SEQ Table` 或 `SEQ Figure` 域。
- 支持标准本地 Markdown 图片语法 `![替代文本](相对路径)`，解析为 `image` 块并在渲染时居中嵌入。远程图片下载不在本次范围内。

## 2026-08-24 Markdown 泛化诊断

- 当前 Markdown 入口按行执行正则，缺少 Markdown block token 和 inline token。
- `1. 内容` 同时命中标题与列表规则，标题分支位于列表分支之前，49 个真实列表项全部进入 heading AST。
- `parse_md_table()` 读取表头后没有将其加入返回行，真实文件的 5 张表均丢失一个表头行。
- 标准 `-`、`*`、任务列表、围栏代码块、引用、Setext 标题、软换行和带对齐冒号的表格没有明确 AST 语义。
- 行内代码、加粗和链接标记会残留在成品文本中，现有 Markdown 残留审计覆盖不足。
- Markdown 路径没有设置 `source_lists`，列表审计以期望值 0 通过，无法发现列表在解析阶段丢失。
- 现有 180 项测试全部通过，但没有 `parse_md_to_model()` 专项测试和完整 Markdown 语义守恒测试。
- DOCX 可复用部分包括 source AST 构造器、显式角色合同、`resolve_list_hierarchy()`、规范化、渲染、模板映射和输出审计。
- DOCX 专属 OOXML 编号、样式名、段落直接格式和媒体关系取证不能直接复用于 Markdown。
- 推荐在 Markdown token 到 source AST 的 seam 内集中处理语法差异，让后续流水线共享同一 AST interface。
- Sol 确认 `resolve_list_hierarchy()`、模型规范化、统一 renderer 和输出审计属于可复用深模块。
- Sol 建议将 Markdown nesting 写入 `source.numbering.ilvl`，并设置 `status=detected`、family、restart 和行号。
- Sol 发现 source model 摘要字段与报告读取字段存在不一致，且 source AST 验证没有完整进入报告。
- Sol 建议引入 `markdown-it-py` 作为结构 token 解析器，并通过延迟导入保持 DOCX 路径独立可用。
- 真实样例验收目标为 title 1、正文 heading 40、list_item 49、table 5、总表格行数 32、表头 5、Markdown 残留 0。
- `markdown-it-py` 4.2.0 已安装到项目 `.venv`，并以延迟导入方式限定在 Markdown 输入路径。
- 新 adapter 将 token 的嵌套列表层级写入 `source.numbering.ilvl`、`status=detected`、`family`、`restart` 与行号，现有 `resolve_list_hierarchy()`、renderer 和列表审计可直接复用。
- 新增 `markdown_source_audit` 和 `markdown_preservation_audit`。后者核对 source AST、规范化 AST、表格行数和表头，输出残留时产生高风险项。
- Markdown 残留检测初版将 `client_id`、`redirect_uri` 和 `__Host-PORTAL_SESSION` 误判为强调格式。已将下划线规则收紧为 Markdown 边界匹配。
- 真实样例的 source AST 和 normalized AST 均为 title 1、正文 heading 40、list_item 49、table 5、总行 32、表头 5。列表审计与 Markdown 语义守恒通过，`unexpected_styles` 为空，Markdown 残留为 0。
- 根 `.gitignore` 忽略 `requirements.txt` 与默认 `tests/*`，新增依赖文件和两个 Markdown 测试文件仍在工作区，等待 Hermes 决定纳入方式。
- 严格模式首次仅因 `document_model_diff` 返回非零。根因是 renderer 的目录分页布局段使用 `文档标题` 样式且没有文本，输出 AST 将其计为额外 title heading。
- `build_document_model_from_output()` 现忽略无文本、无媒体的布局段落。该规则不会排除真实正文标题；新增回归测试验证真实 heading 丢失仍保留 `block_count_delta` 与 `block_type_count_delta` 告警。
- 修复后真实 Markdown 样例的 normalized 与 rendered AST 均为 166 块，严格模式无风险成功退出。
- Sol 终审确认首轮实现仍有发布阻断项：代码块可能被规范化为标题，缩进代码可能静默消失，quote 内列表和标题容器语义不完整。
- 首轮 `markdown_preservation_audit` 只比较块数量和表格行数，无法发现表格单元格、列表层级和文本被修改的假绿。
- 远程图片、HTML、任务状态、链接 URL 和 inline code 空格需要明确保留或风险记录。
- 阶段 11A 将所有 Markdown token 生成块标记为 `semantic_origin=markdown_token`，并在规范化前保护 `code_block`、`quote` 与引用容器上下文。缩进代码 `code_block` 与围栏代码统一生成显式代码角色。
- 引用内标题不会生成文档 title；列项内标题内容并入该列项并标记风险。远程或混合图片、HTML 和任务列表保留可读内容，远程 URL 不进入本地 `asset_path`。
- Markdown 守恒审计现核对 token 清单到 source AST、标题文本、表格完整矩阵、列表层级和家族、规范化 AST 与 rendered model。删除单元格、改表头、降低列项层级的反例都会失败。
- Markdown 残留检测只在 Markdown 输入启用，DOCX 的合法反引号不再触发 Markdown 风险。
- 阶段 11C 的 source、normalized、rendered 守恒比较覆盖正文角色与文本、标题文本、表格内容矩阵、列项文本层级和家族，以及代码块和引用内容。渲染后的表格 cell role 由 finalizer 语义合同负责，守恒比较核对完整单元格文本矩阵。
- 列项内引用生成独立 quote body 块并记录 `container_quote_depth`。引用包裹的列表保留 list_item 与引用上下文。任务状态和混合远程图片进入 unsupported token 风险记录。
- Markdown 残留审计接收已确认 code_block 文本集合，合法代码文本中的反引号不再形成残留项。
- 依赖与新增门禁必须通过 `.gitignore` 例外进入普通差异，避免 fresh checkout 缺失。
- 阶段 11E 发现旧守恒快照会排序语义元组，并用 `source.role` 回退，因而可能掩盖顺序和顶层角色丢失；现已改为按输出 API 可证实的语义通道保留原始顺序，并只核对显式顶层 role。
- renderer 为 Markdown token 段落写入可追踪 `w14:paraId`，报告保存段落与表格的语义映射。保存后回读按此映射恢复 code_block、quote、列项层级、家族、重启、父项、任务状态和引用容器深度。
- Markdown 代码块不再删除 token 内容末尾换行。Markdown 残留审计改用段落位置，文本集合不再能豁免普通正文中的反引号。
- DOCX API 分离段落和表格集合，无法提供原始混合节点顺序；守恒审计因此保留每个语义通道的顺序、完整内容与角色，避免构造不可证实的跨集合序列。
- 阶段 11G 已将 Markdown 守恒快照改为统一 source AST 顺序。renderer 为 Markdown 段落和表格记录同一 `document_order`，输出 AST 回读按该映射恢复顺序，因而可核验 heading、body、quote、list 和 table 的跨类型相对位置。
- 阶段 11I 已移除按 `document_order` 重排回读模型的做法。输出模型直接遍历 `document.element.body.iterchildren()`，按保存后 OOXML 的段落和表格节点顺序构造 AST；预期序号只作为身份和审计证据。
- Markdown renderer 现为 image、caption、appendix 与普通段落使用同一可回读段落语义映射。图片保留 asset path，自动题注回读使用规范化 caption text，避免 Word 显示编号前缀造成假差异。
- 阶段 11K 的 Markdown 本地图片 source AST 记录 asset SHA256。renderer 建立实际图像 relationship 后记录 target blob SHA256，回读从 `a:blip` 的 `r:embed` relationship 读取 part blob 并重新计算 SHA256。关系失效或图片交换会形成守恒失败。
- Markdown 附录在 source AST 构造时使用 `parse_appendix_title()` 定稿 appendix_id、title、title_lines 和分类信息。回读先识别 Markdown paraId，再过滤空文本，因此空标题附录不会丢失。
- 阶段 11M 中 image relationship 必须具有 `RT.IMAGE` 类型，只有可读取 blob 不足以证明其为图像关系。Markdown 本地图片目标在删除 query/fragment 后经 `unquote` 解码，支持中文、空格和 `file:` 本地路径，远程 scheme 继续降级为风险。
- 纯“附录”已纳入 appendix 标题识别，source AST 直接给出由 `parse_appendix_title()` 决定的默认 appendix_id 和空 title_lines 合同。
- Sol 阶段 11N 最终复审通过。无效图片关系类型、图片关系交换、中文与空格路径、纯附录、带编号附录、保存后 OOXML 物理顺序变化均有确定性反例门禁。
- 最终守恒合同覆盖 token 清单、统一有序块序列、显式角色、正文与标题、代码和引用、列表父子关系、表格表头与单元格角色、图片关系目标 SHA256、题注及附录字段。
- DOCX 入口不写入 Markdown 专属 `w14:paraId` 或 `markdown_render_semantics`，Markdown 残留审计也只在 Markdown 输入启用。
- 阶段 16 将 Markdown inline token 映射为 `inline_runs`，每个 run 的 text 拼接等于块 text，支持 strong、emphasis、code 和 href。分隔线成为无文本 separator 块；首个语义内容并非 H1 时，以文件名生成唯一 title 并记录告警。
- Markdown token body 被排除出 DOCX 语义列表发现，避免普通 Markdown 段落在规范化期间意外扩张为列表。

## 2026-09-08 方案 3.1 阶段 7.1

- T13 的范围与结构纯函数此前只被单元测试调用，ConversionKernel 的执行计划仍只有 `legacy-full-adapter`。
- 原生 SourceRegion 和 DocumentStructure 需要同一次转换内共享 SourceFacts。采用每次运行独立的上下文即可形成真实 seam，无需引入远程端口或动态插件扫描。
- 执行函数若嵌在 Kernel 内会迫使每次扩展都修改编排器。现已提取为 `builtin_modules.py` 中的 module 工厂，descriptor、依赖、产物和执行函数由 module 自己封装。
- DocumentStructure 原实现未消费范围结果，会为已排除的 front matter 和源目录继续提交 body 候选。现已把 SourceRegion 结果作为显式输入并过滤 excluded 节点。
- registry 原实现只验证全局 provider 是否存在，允许 consumer 在 provider 之前执行。现增加按最终执行顺序验证的 capability 门禁。
- 本步只能证明原生 module 已执行且产物满足合同。由于旧 Adapter 仍独立完成源模型构建和最终转换，能力状态保持 `shadow_verified`。能力所有权切换需要逐节点比较原生候选和旧 Source AST，并让后续阶段消费同一裁决结果。
- 真实基准报告显示原生 parser 读取 146 个 unit、150 个 container，SourceRegion 生成 295 个 region，DocumentStructure 生成 146 个候选。该样本没有源目录或显式 front matter，因此 excluded 数量为 0，后续还需真实 DOCX 范围样本。

## 2026-09-08 方案 3.1 阶段 7.2

- 旧迁移 AST 缺少可用源引用，全部落为 `legacy-source` 和零摘要。逐节点对照采用文档顺序、可见文本及同文本出现次数作为临时关联键，同时保存两侧 node_id 供定位。
- 等价审计是独立必需 module，消费 SourceFacts、SourceRegion、DocumentStructure 和 legacy RunResult。未导出旧 Source AST 时状态为 `shadow_unobserved`，发现差异时为 `shadow_mismatch`，全部通过时为 `shadow_verified`。
- Markdown 基准首次对照 146 个节点全部关联，唯一差异类别为文首 H1 的 title 裁决和后续标题层级偏移。迁移该合同后 146 个节点等价通过。
- 临时 DOCX 探针使用 Title、Heading 1 和 Normal 三个真实 OOXML 段落。原生候选将三者都视为 body；旧链输出 title、空文本 heading 和 body，其中“第一章”被手工编号清理逻辑全部剥离。
- DOCX 的正确目标应保留“第一章”文本并识别其标题角色。该项属于需要批准的基线差异，不能用复制旧空标题行为换取等价审计通过。
- 真实 DOCX 的首个执行阻断来自 Adapter 枚举映射，旧分类值为“资料性”，AST 2.0 只接受 `informative`、`normative`、`unknown`。缺失映射会导致成品已经生成但 modular 结果失败。
- 旧模型已有附录范围 context，迁移器此前只创建 appendix 标题容器，后续附录标题和正文仍挂在 root。修复后 children 图保留整个显式 appendix 范围。
- 真实文件在有效文档标题之前包含空的“文档标题”样式段落。空段落曾提前占用 title 槽位，修复后有效标题按样式定稿为 title。
- SourceRegion 仅凭“目次”文本排除 DOCX 节点会误伤正文中的同名 Heading 2。DOCX 目录排除需要位置检测证据，文本 fallback 只适用于 Markdown。
- 结构等价审计不能把 note、numbered_note、caption、list 和 appendix 的差异归给 DocumentStructure。收紧职责后，真实 DOCX 的 56 个结构域节点全部匹配；10 个其他语义域节点被明确忽略。

## 2026-09-08 方案 3.1 阶段 7.3

- Adapter 通过运行时上下文接收同次 ExecutionPlan 产生的 SourceFacts 和 DocumentStructure 候选，在旧 Source Model 规范化前按源位置应用裁决。
- 桥接只处理 title、heading 和 body。list_item、caption 及其他专用角色直接跳过，不会被文档结构 module 覆盖。
- DOCX SourceFacts 原先把非 `w:p` 与 `w:tbl` 的 body 子节点计入 `source_position`，真实样本与旧模型整体偏移 4 位。现已统一为可转换块序号。
- DocumentStructure 候选范围收紧到根文档直接段落，表格单元格、列表、引用等容器内容留给各自语义 module。
- `document_structure` 能力 provider 已从 legacy full Adapter 转移到原生 module。`source_parser` 与 `source_region` 仍保持 shadow，因为旧输出链仍独立构建 Source Model，DOCX 目录及前置页位置检测也尚未交由原生 SourceRegion。

## 2026-09-08 方案 3.1 阶段 7.3 SourceRegion

- `resolve_source_regions()` 成为统一 interface。DOCX 的目录候选、前置页、规范标题和排除位置由该 module 一次产出，Adapter 不再为纯规则路径独立重新判定。
- 范围合同增加 `canonical_title`。源标题位置仍从原始正文排除，DocumentStructure 可继续消费其标题事实，标题重建不再被等价审计误判为范围残留。
- Markdown 文本兜底只处理根文档的普通段落。`# 目录` 及引用、表格、列表容器中的“目录”不会开启排除范围。
- 等价审计只对 title 和 heading 使用标题编号清理后的关联键，正文及排除范围仍按原始可见文本严格对照。
- 带封面和手工目录的真实 DOCX 探针排除 6 个源位置，Source AST 仅保留唯一标题和正文，目录替换、输出结构和逐节点等价审计通过。
- `source_region` provider 已从 legacy full Adapter 移除。SourceParser 仍保持 shadow，后续需要让输出阶段直接消费原生 SourceFacts。

## 2026-09-08 方案 3.1 SourceParser 切换门禁

- 原 `source_parser=shadow_verified` 复用了 DocumentStructure 的结构等价审计。该审计主动忽略列表、表格、题注、图片和附录，无法证明完整解析器可切换。
- 新增 SourceParser 独立等价审计，以 SourceFacts、SourceRegion 和旧 Source AST 为单一 interface，核对文本清单、表格网格、图片资源和列表项数量。
- legacy full Adapter 现在通过每次运行的内存工件返回旧 Source Model，审计无需用户指定 `--source-ast`，也不在公开报告中泄漏完整源 AST。
- 真实模板 DOCX 审计结果为 `shadow_mismatch`。表格数量与网格、图片数量守恒；文本域为 127 对 118，列表域为 27 对 28。
- 文本差异包含题注标签剥离、附录标题拆分和单元格多段合并。这些转换尚未在 SourceFacts 到输出模型的原生 Adapter 中明确表达，因此保留旧 parser 所有权。

## 2026-09-08 方案 3.1 SourceFacts 语义投影

- 新增单一 SourceFacts 语义投影 interface，把题注标签剥离、完整附录标题拆分、表格单元格多段合并和源材料库存核对收口到同一模块。
- 孤立的“附录”结构标题保留为原文。只有带分类行，或由多行组成的完整附录标题块才进入附录拆分。
- 真实 DOCX 中的 `(3)` 由旧解析器误生成空列表项。新投影保留公式编号文本，等价审计仅在源位置、公式编号形态和旧空列表项同时匹配时记录批准差异。
- 真实模板转换的文本为 118 对 118，表格 4 对 4，图片 3 对 3，列表 27 对 27，四域全部等价。
- 当前仅证明 shadow 结果等价。输出链仍由旧 Source Model 提供，`source_parser` 所有权不切换。

## 2026-09-08 方案 3.1 SourceParser 内容所有权切换

- SourceProjection 已成为 modular 输出内容的权威来源，按源位置覆盖旧语义骨架中的段落、题注、附录和表格内容。
- 所有权门禁同时要求候选完整消费、无歧义和四域解析覆盖等价，缺少任一条件时保持 partial 或 mismatch 状态。
- 真实 DOCX 完整消费 99 个段落和 4 张表，文本、表格、图片和列表四域等价。公式编号“(3)”由原生投影恢复为正文公式角色。
- 旧 parser 暂时保留语义骨架和 legacy 回退。其删除依赖后续保护内容、列表、表格、题注和附录 provider 完成真实接线。

## 2026-09-08 方案 3.1 ProtectedContent 所有权切换

- Markdown 的“备注：”此前未进入原生保护候选，DOCX 注释标记清理也与 SourceProjection 的等价文本合同分离。两者已统一到 `protected_unit_semantics`。
- 表格单元格段落与顶层表格共享 DOCX 源位置。若直接按位置应用文档级保护候选，会把整张表误改为正文。保护候选现排除表格后代，表内语义留给 Table Provider。
- 真实 DOCX 修复后保持 4 张表，9 个文档级候选完整消费，解析覆盖、注释保真和表格语义审计均通过。

## 2026-09-09 方案 3.1 List 所有权切换

- SourceFacts 曾把 `analyze_docx_lists` 返回的 ignored 编号候选全部写为列表 marker，真实样本因此从 27 项膨胀到 43 项。现在仅 detected 候选产生列表语义，ignored 判定证据继续保留。
- 原生列表关系映射到过渡 Source Model 后，以 `native_list_decision` 固化一级有编号样式和章节重启。旧层级规范化读取该裁决，保留原始 ilvl 供审计。
- 公式编号需要在 SourceProjection 阶段同时写入保护合同，仅修改 block role 无法阻止后续文本列表启发式。修复后归一化 AST 保持 27 个列表项。

## 2026-08-24 Markdown AST 系统化识别框架

- 当前 `md_pipeline.py` 同时承担 token adapter、容器状态机、语义裁决、AST 构造、源审计和守恒审计，interface 稳定但 implementation 过宽，局部修改仍容易跨职责遗漏。
- 目标 seam 位于 Markdown token 流到统一 `DocumentModel` 之间，依赖属于进程内纯计算，适合深化为单一识别模块。
- 新模块必须把块级 token、容器上下文、行内 span、识别证据、降级结果和诊断作为一次识别结果返回，调用者不应理解 token 遍历细节。
- `parse_md_to_model()` 继续作为兼容入口，内部委托新的识别 interface，避免影响 CLI、Desktop 和现有测试调用方。
- 测试表面应转向识别 interface 的可观察结果和不变量，覆盖原子语法、容器组合、顺序组合、未知语法降级和真实文件。
- 现有单次 token 循环同时维护 `heading_level`、`quote_depth`、`list_stack`、`list_item_stack`、table row/cell 状态和标题前置状态，任何新增容器都需要修改同一巨型分支。
- `inline_runs`、独立图片和本地资源解析是块识别内部的三个并列子过程，目前没有统一 Inline Span 结果或结构化 Diagnostic 返回值。
- source audit 由遍历过程直接增量写入 report，识别结果和报告副作用耦合，导致测试必须了解内部 report 字段。
- 现有测试已覆盖多个语义不变量，但主要通过 `parse_md_to_model()` 和完整转换间接验证，缺少面向识别 interface 的语法类别矩阵。
- 当前固定方言实际启用 11 个 block rule 和 10 个 inline rule。系统规范需要逐类声明每项规则的 Semantic Block、Inline Span 或 Fallback Representation，不能只列“支持常用 Markdown”。
- reference link、autolink、escape 和 entity 会被 tokenizer 归一化成已有 inline token。当前 link close 额外追加可见 URL，autolink 因而可能形成重复 URL；新框架需把链接目标作为 Inline Span 语义，并将可见文本投影策略单独规定。
- 三种 interface 方案比较结果：最小 interface 方案 depth 最高；插件引擎方案扩展能力最强，但当前会形成三个假设 seam；默认调用方案迁移成本最低。最终采用最小 interface 与默认调用方案组合，并保留内部可扩展 dispatch。
- 已形成 Markdown AST 识别规范，系统规定语法映射、识别优先级、Container Context、标题裁决、Diagnostic code、Recognition Contract、Preservation Contract 和五层测试矩阵。
- 仓库原先整体忽略 `docs/` 和新测试文件。已为识别规范、ADR 和 `test_markdown_recognition.py` 增加精确例外，避免规范与门禁只存在于本地工作区。
- Desktop 同步工具使用显式单文件 MODULES 清单。新增 `markdown_recognition.py` 后必须将它加入清单和 vendored manifest，单文件 module 方案无需修改递归同步机制。
- Terra 阶段 17 首版创建了纯函数外观，但 `markdown_recognition.py` 通过运行期 import 反向调用 `md_pipeline._recognize_legacy`。删除新 module 后复杂度仍完整留在旧文件，未通过深 module 的 deletion test。
- 首版 `RecognitionDiagnostic` dataclass 未被结果使用，title Diagnostic 未进入 diagnostics，Preservation Contract 只含计数，兼容 Adapter 还遗漏 `inferred_lists` 投影并新增 `content_warnings` 副作用。这些都需要在继续迁移时修正。
- 第二步已经通过 deletion test：tokenizer、inline span、资源解析、Container Context、dispatch 和 AST 装配全部进入 `markdown_recognition.py`，`md_pipeline.py` 只剩稳定 Adapter、列表类型兼容和守恒审计。
- 第二步仍有合同缺口：`RecognitionResult.diagnostics` 仍保存 dict，title Diagnostic 未汇总，未知 block 和 inline token 仍静默跳过，Preservation Contract 只有 inventory 和 block count，返回前没有执行规范中的 Recognition Contract。
- 第三步已补类型化 Diagnostic、未知 token Fallback、源码 hash、完整识别序列和返回前合同校验。合同探针随即发现松散列表中的多段列项只追加 block text，未同步追加 Inline Span，现会正确阻断该不一致，需要修复识别装配逻辑。
- `missing_local_image` 的规范 code 与 implementation 的 `local_image_missing` 不一致，Diagnostic token_type 也未进入 legacy dict，需在稳定 code 发布前统一。
- Preservation Contract 当前保存完整 block 副本，既能防止后续模型修改污染基线，但 legacy 守恒审计仍维护一份独立 semantic snapshot。应提取单一 canonical semantic sequence 编译器供二者共用。
- 最终实现已将 tokenizer、Container Context、inline span、资源解析、语义裁决和 AST 装配收口到 `markdown_recognition.py`，`md_pipeline.py` 仅保留兼容 Adapter 和守恒审计。
- `canonical_semantic_sequence()` 现同时驱动 Recognition Contract 与规范化后守恒审计，避免两套语义快照漂移。
- 松散列表多段内容已合并文本和 Inline Span，无法完整合并时删除不可信的 span，Recognition Contract 会阻断任何显式不一致。
- 两份真实 Markdown 的 `markdown_preservation_audit.passed` 均为 true。原始文件识别 14 个列表项、26 个 separator 和 2 张表，正式版识别 49 个列表项和 5 张表。
- Desktop 的显式 vendoring 清单和 manifest 已包含 `markdown_recognition.py`，并且放在 `md_pipeline.py` 前。打包后应用可完成 Markdown 转换冒烟。
- Desktop 对两份真实文件的转换报告都含有 schema 1.0 的 Recognition Contract，应用版本和引擎版本均为 0.12.18，语义守恒通过。

# 2026-09-09 方案 3.0 收口发现

- Table、Caption 和 Appendix 原生 Provider 已进入真实 ExecutionPlan，八项源语义能力在真实 Markdown 与真实 DOCX 中均达到 `native_consumed`。
- 真实 DOCX 的 `b0029` 图题问题由 Caption Provider 的显式目标关系修复，模型和保存后位置审计均通过。
- 模板 TOC 片段在插入时升级为 1 至 4 级和附录样式映射，保留模板内容控件与打开更新标记。
- 编译产物默认 legacy 入口仍保留旧题注风险，显式 modular 入口通过全部专项审计，符合默认未切换的兼容策略。
- 当前发布包模块集合为源码 45 项、运行时 45 项。新增语义 bridge 均进入编译报告。
- 两个公式 layout 表的宽度、边距、无框线、行高和 OMML 数量在源与编译输出间相等。
- LibreOffice 页面渲染缺少部分中文字体字形，源文档与输出表现一致。该限制不影响 OOXML 结构审计结论。
- 首次性能基准的 large 档失败，同配置复测通过。两份报告均保留，默认策略继续保持 legacy。

# 2026-09-14 真实 DOCX 基础格式语义修复

- 根因按模块分开：列表问题来自解析器未记录手工可见标记和列表 AST 清理结果未进入原生流水线；正文缩进来自展示性制表符直接进入 AST；题注问题来自题注候选只接受显式“图/表”前缀；深层标题问题来自结构样式正则只覆盖 1 至 6 级。
- 列表前导空格被保留为证据，编号语义深度统一为 0，输出使用模板一级列表样式，Source AST 移除正文中的手工前缀。
- 根级正文段落消费 `leading_whitespace` 证据，表格范围和图片混合段落不做无条件清理。
- 题注模块使用对象邻接和样式边界识别无编号题注，排除“相关表介绍”等 Heading 标题，括号包裹题注先解包再关联。
- 真实输出回读：目标图题均为 Caption 样式，`Heading 7/8` 标题为 Heading 6，`事件运营需求` 下的编号正文无手工前缀且使用一级列表样式。
- 真实转换状态为 `completed_with_warnings`，退出码 0。模型、保存输出和附录审计通过。8 条剩余诊断均来自源文件编号定义不完整或不可恢复的显式深层列表，未阻塞发布。
