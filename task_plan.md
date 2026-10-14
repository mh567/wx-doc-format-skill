# 当前任务：native-v2 架构收敛和最终复审准备

## 目标

在已完成的 T00 至 T09 基础上，按 `docs/execution-v4/architecture-convergence.md` 完成架构收敛，确保复核链、能力合同、执行调度、代码职责和最终验收形成闭环。

## 当前阶段

- [x] 阶段 1：读取现有状态、实现和历史证据
- [x] 阶段 2：确认架构风险和收敛范围
- [x] 阶段 3：固化四张架构收敛工单
- [ ] 阶段 4：AC-1 统一复核候选重处理链
- [ ] 阶段 5：AC-2 固化能力合同和执行调度
- [ ] 阶段 6：AC-3 收敛代码职责和入口边界
- [ ] 阶段 7：AC-4 综合验收、独立复审和发布准备

## 防复审新增问题规则

每张工单必须经过固定基线、红灯、实现、定向测试、独立审查、修复、复审和证据记录。最终复审只针对单一候选提交。未修复问题、外部门禁和后续工单必须分别记录，不能混在“通过”结论中。

唯一状态源：`docs/execution-v4/status.json` 继续记录 T00 至 T09。AC-1 至 AC-4 在本计划和对应证据中追踪，开始实施前不修改 T00 至 T09 的历史状态。

# 已完成前置任务：execution-v4 native-v2 切换

T00 至 T09 已完成本地实现和候选验收。当前仍有 Kylin 真机、Word/WPS 视觉、远程发布和安装等外部门禁，详见 `docs/execution-v4/evidence/T09.md`。

# 历史任务：方案 3.1 完整能力迁移与可插拔改造

## 目标

将旧架构已验证的解析、语义、模板渲染和审计能力完整接入新 ConversionKernel。先通过 Adapter 保持行为等价，再逐项替换为原生 module。任何 module 只有进入实际执行计划并通过真实模板端到端验收后才能标记完成。

## 当前阶段

- [x] 阶段 1：核对方案 3.0、实际调用链和缺失门禁
- [x] 阶段 2：形成方案 3.1 能力清单、Interface、迁移顺序和完成条件
- [x] 阶段 3：建立真实模板能力等价红灯
- [x] 阶段 4：实现可执行 registry 和完整能力预检
- [x] 阶段 5：接入旧解析、语义、渲染、终结和审计 Adapter
- [x] 阶段 6：补齐 AST 2.0 的 title、表头、题注和模板呈现合同
- [x] 阶段 7：按能力逐项替换 Adapter 内部实现
  - [x] 7.1 SourceParser、SourceRegion、DocumentStructure 进入真实 ExecutionPlan，并输出 shadow evidence
  - [x] 7.2 建立原生候选与旧 Source AST 的逐节点等价审计
  - [ ] 7.3 逐项切换三项能力所有权
    - [x] Adapter 已消费原生 DocumentStructure 裁决，并移除 Adapter 的 `document_structure` 能力声明
    - [x] `source_parser` 输出内容已直接消费原生 SourceFacts 投影，并通过完整消费和等价门禁切换所有权
      - [x] 建立独立解析覆盖审计，不再借用标题与正文结构等价结果
      - [x] 建立 SourceFacts 语义投影，补齐题注前缀、附录分类、单元格多段和公式编号差异合同
      - [x] 真实 DOCX 文本、表格、图片和列表四域解析等价
      - [x] 输出链直接消费原生投影，移除旧 parser provider 后切换所有权
      - [x] 旧 parser 保留为语义骨架与 legacy 回退，重复实现删除转入独立门禁
    - [x] Adapter 已消费原生 SourceRegion 范围上下文，并移除 Adapter 的 `source_region` 能力声明
    - [x] ProtectedContent 已进入真实 ExecutionPlan，输出链消费原生保护裁决，Adapter 已移除 `protected_content` 能力声明
    - [x] List Provider 已进入真实 ExecutionPlan，27 个源列表项完整消费，Adapter 已移除 `list_semantics` 能力声明
    - [x] Table、Caption 和 Appendix Provider 已进入真实 ExecutionPlan 并完成消费审计
- [x] 阶段 8：真实 Markdown、真实 DOCX、视觉和本机编译回归

剩余项为 Kylin V10 ARM64 外部环境验收。Windows x86_64 已按用户决定退出后续打包与验收范围。默认策略切换等待用户明确授权。

## 约束

- 旧 legacy 默认路径保持稳定。
- 新路径缺少必需能力时返回 unsupported 或 failed。
- Source AST 只保存源语义，不保存模板样式名。
- 标题、表头、题注、附录和保护内容在 AST 或关系裁决阶段定稿。
- 目录片段、样式、编号、分页和表格格式由 TemplateProvider、NormalizationPolicy 和 Renderer 负责。
- finalizer 只应用明确呈现合同，不重判语义。
- 所有 Python 修改先建立红灯并声明具体路径与分工。
- `_build/`、`paseo.json` 和无关用户改动保持不动。

## 完成定义

1. 旧能力清单中每项都有 module 所有者、Adapter、必需审计和真实样本。
2. ConversionKernel 实际执行 registry 生成的计划。
3. 默认模板输出保留模板目录策略，文档标题使用文档标题样式。
4. Markdown 表头、数据表题注、SEQ 域、列表、注释、附录、图片和资源关系通过专项审计。
5. modular 与 legacy 在批准差异外达到语义和呈现等价。
6. 全量测试、真实转换、DOCX 包检查和页面抽检通过后才允许切换默认路径。

## 遇到的错误

| 错误 | 次数 | 处理 |
| :--- | ---: | :--- |
| T13、T14 module 文件存在但未进入 Kernel | 1 | 增加 module 接线和必需审计门禁 |
| modular 审计只检查文本顺序与表格数量 | 1 | 增加能力覆盖清单和模板专项审计 |
| 真实模板测试后直接修补 renderer 表格 | 1 | 停止症状修补，回到 AST 与 RenderPlan 分层迁移 |

# 历史任务：方案 3.0 执行包编制

当前授权为生成执行方案，本轮不实施 Python 改造。历史条目保留供参考。

1. 仓库接口、测试和约束核对：complete。
2. 总入口、任务卡、验收矩阵和状态编制：complete。
3. 路径、依赖、覆盖和命令校验：complete。

交付结果：docs/execution-v3/ 共 22 个文件，含 17 张任务卡。全部源码实施任务仍为 pending。已校验任务依赖、方案摘要、读路径、拟新增测试路径和文档链接；未执行源码测试或性能基线。

执行入口：docs/execution-v3/README.md。未来源码实施进度以该目录 status.json 为准。

工具记录：历史规划批量读取发生截断，改为针对性分段核对；首次规划文件补丁因 progress.md 标题不匹配而未应用，核对真实标题后重试。

# 嵌套列表层级保真修复

## 目标

在 DOCX 解析阶段统一重建有序、无序及混合嵌套列表的层级，保留现有 WX 样式映射和模板接口。

## 阶段

- [x] 阶段 1：确认根因与现有测试缺口
- [x] 阶段 2：实现源 AST 的统一列表层级解析
- [x] 阶段 3：接入规范化、渲染与审计
- [x] 阶段 4：补充回归测试并运行测试集
- [x] 阶段 5：修复代码审查发现的编号边界与父项审计问题
- [x] 阶段 6：修复 Markdown 图表题注的 AST 解析与渲染
- [x] 阶段 7：Sol 对比 DOCX 与 Markdown 流水线，形成分层实施方案
- [x] 阶段 8：Terra 建立 Markdown 专项红灯测试与源语义清单
- [x] 阶段 9：Terra 修复 Markdown 解析、AST 映射与审计缺陷
- [x] 阶段 10：Terra 执行专项测试和真实文件回归
- [x] 阶段 11：Sol 复审实现与 DOCX 回归边界
- [x] 阶段 11A：Terra 修复终审阻断项与守恒审计假绿
- [x] 阶段 11B：Sol 复审阻断项修复
- [x] 阶段 11C：Terra 修复终审端到端语义和容器边界阻断项
- [x] 阶段 11E：Terra 修复回读语义合同、顺序守恒与结构化残留审计
- [x] 阶段 11G：Terra 修复跨类型文档顺序守恒
- [x] 阶段 11I：Terra 修复 OOXML 实际顺序回读与媒体语义合同
- [x] 阶段 11K：Terra 修复图片关系身份与附录 source AST 合同
- [x] 阶段 11M：Terra 修复关系类型、URL 解码与纯附录合同
- [x] 阶段 11N：Sol 最终复审并确认无确定性阻断
- [x] 阶段 12：Codex 汇总验证结果并交付
- [x] 阶段 13：刷新标准验收环境并同步本机 Skill 0.12.16
- [x] 阶段 14：升级 0.12.17、复验、同步安装副本并提交
- [x] 阶段 15：推送私有 dev/main 并发布公开 origin/main

## 约束

- 层级语义在源 AST 阶段确定。
- `ilvl`、缩进、相邻项和父项关系共同参与裁决。
- 保持 `1.1`、`1.2`、`2.1`、`2.2` WX 样式接口稳定。
- Markdown 人工题注在 source AST 阶段解析为显式 `caption` 块，表格题注保留表题在上，图题注保留图题在下。
- Markdown 明确语法在解析阶段直接生成显式 source AST 角色，纯文本启发式只处理无结构标记的候选。
- DOCX 的 AST 构造器、列表层级解析、规范化、渲染和审计合同可以复用，OOXML 取证逻辑不进入 Markdown 解析器。
- 保持 `parse_md_to_model()` 与稳定 AST 字段兼容，避免随意重命名接口。

## 当前阶段

阶段 25：将离线分发收口为标准 Skill 安装包。

## 阶段 25：标准离线 Skill 安装

- [x] 确认现有平台归档已包含完整 Skill 和本机运行时
- [x] 删除用户手工将归档放入 `artifacts/` 的安装路径
- [x] 更新公开 README、薄 Skill 和离线包内说明
- [x] 验证平台包直接解压到 skills 目录后可被识别和运行
- [x] 使用现有 Nuitka 运行时重新封装 macOS ARM64 和 Linux ARM64 候选包
- [x] 提交并推送私有开发线
- [x] 获得授权后替换 `v0.12.18` 同版本 Release 资产并发布公开生成树

## 阶段 24：公开 Skill 交互和零配置启动

- [x] 将公开 README 改为对话触发、模式选择和交付说明
- [x] 在公开 `SKILL.md` 固定交互流程和 LLM 文件协议
- [x] 让 POSIX 和 Windows 启动器优先使用 `artifacts/` 内置归档
- [x] 为非 ASCII 默认缓存路径自动选择 ASCII 回退目录
- [x] 完成专项、全量、Skill 和真实编译制品验证
- [x] 提交私有 `dev/main` 并发布公开 `origin/main`

## 阶段 23：私有仓库开发流程

- [x] 建立开发流程单一事实源文档
- [x] 在 `AGENTS.md` 固定私有开发、公开生成和仓库隔离守则
- [x] 在私有 README 增加开发文档入口
- [x] 验证文档链接、Git 边界、命令和差异范围
- [x] 提交并推送私有 `dev/main`

## 阶段 22：公开历史收口

- [x] 将公开 README 精简为核心能力、安装方法和使用方法
- [x] 重新生成并验证薄 Skill
- [x] 创建无父提交的公开根历史
- [x] 使用 force-with-lease 更新 `main`并替换 `v0.12.18` 标签
- [x] 通过远程引用、GitHub 页面和全新克隆验证单提交历史

## 阶段 21：macOS 与 Linux 双平台 Skill 发布

- [x] 核对公开仓库基线、版本和当前发布机制
- [x] 实现允许 Windows 制品缺失的公开 Skill 组装与安装流程
- [x] 生成只包含 macOS ARM64 和 Linux ARM64 的发布内容
- [x] 验证 Skill、离线安装、缺失平台提示和公开源码边界
- [x] 提交私有 dev/main，按 cherry-pick 规则发布公开 main
- [x] 核验公开远程文件和安装路径

## 阶段 20：三目标本地构建

- [x] 恢复 Nuitka 实现、三目标矩阵和本机构建环境
- [x] 增加 macOS ARM64 本地构建入口
- [x] 增加 Linux ARM64 容器构建入口
- [x] 增加 Windows x86_64 PowerShell 构建入口
- [x] 使用 Python 3.13 重建 macOS ARM64 制品并冒烟
- [x] 启动 Colima 并构建 Linux ARM64 制品
- [x] 验证归档、SHA256、源码泄漏和安装流程
- [ ] 降低 macOS ARM64 制品的最低系统版本，当前本机制品为 macOS 26.0
- [x] 取消 Windows x86_64 主机打包与验收，现有入口仅作历史兼容保留
- [x] 提交并推送本地构建能力

## 阶段 19：Nuitka 闭源核心与离线分发实现

- [x] 恢复阶段 18 方案与当前 Git 基线
- [x] 固定发布版 CLI interface、版本资源与断网运行配置
- [x] 实现 Nuitka standalone 构建、制品组装、manifest 和 SHA256
- [x] 实现 POSIX 与 Windows 离线安装及运行 Adapter
- [x] 增加构建、安装、版本和断网合同测试
- [x] 增加 Windows x86_64、macOS arm64 和 Linux arm64 构建工作流
- [x] 在本机 macOS arm64 完成 standalone 编译、离线包组装和转换冒烟
- [x] 执行 Claude 实现复核与 Codex 独立审查
- [x] 汇总改动、未完成平台验证和后续发布步骤

### 阶段 19 范围约束

- 保持现有转换 CLI 参数稳定。
- 发布版运行时不执行首次下载或云端 SDK 自动探测。
- 保留文件协议与 `--llm-command` 内网 Adapter。
- 离线包在无 Python、pip 和 Git 的目标机上可运行。
- 当前只在私有开发线实现，不替换公开历史，不推送，不发布。
- 预存 `_build/` 与 `paseo.json` 不进入本次改动。

## 阶段 18：闭源核心与内网离线分发

- [x] 恢复现有双仓库、发布与规划上下文
- [x] 盘点当前公开 Skill 的调用 interface、依赖与源码暴露面
- [x] 评估 Nuitka 五平台构建、制品结构和签名校验约束
- [x] 设计联网安装与完全离线安装的双 Adapter
- [x] 定义私有开发仓库到公开分发仓库的发布门禁
- [x] 形成分阶段迁移、测试和回滚方案
- [x] 汇总改造结论并交付

## 阶段 17：Markdown AST 识别框架与规范

- [x] 固化 Markdown 识别领域术语和语义不变量
- [x] 对现有 token 遍历、容器状态、inline span 和审计逻辑进行模块边界盘点
- [x] 用三种不同 interface 方案比较深度、局部性和 seam 位置
- [x] 形成 Markdown AST 识别规范和架构决策
- [x] Terra 按选定 interface 实现深模块并保持 `parse_md_to_model()` 兼容
- [x] 建立语法类别矩阵、组合测试和真实文件回归门禁
- [x] Codex 复核 Skill 与 Desktop 同步边界

### 阶段 17 interface 决策

采用最小 interface 与默认调用方案的组合。核心只公开 `recognize_markdown()` 和结构化 RecognitionResult，`parse_md_to_model()` 保留为兼容 Adapter。首版使用内部规范表与 dispatch，暂不开放插件、方言和策略注册表。

## Sol 规划结论

1. 保留 `parse_md_to_model()` 稳定 interface，将 Markdown token 到统一 DocumentModel 设为 source adapter seam。
2. 直接复用 `document_model.py`、`list_hierarchy.py`、`model_normalization.py`、`word_model_renderer.py` 和现有审计闭环。
3. OOXML 编号、Word 样式、视觉格式和媒体关系取证留在 DOCX adapter。
4. 引入 `markdown-it-py`，缺少依赖时只阻断 Markdown 输入。
5. 首批门禁锁定 title、heading、list_item、表格行数、目录、Markdown 残留和模板样式守恒。

## 遇到的错误

| 错误 | 次数 | 处理 |
|---|---:|---|
| 系统 Python 缺少 pytest | 1 | 改用 `scripts/.venv/bin/python3` |
| `paragraph.text` 未包含 SEQ 域缓存值 | 1 | 改为核验 Word XML 中的 SEQ 域和缓存编号 |
| 项目虚拟环境缺少 `markdown-it-py` | 1 | 总控已获授权并安装 4.2.0；依赖已写入新增 requirements 文件 |
| Terra 最终汇总阶段模型容量错误 | 1 | 保留共享工作区改动，主 Agent 独立复测后恢复 Terra |
| 首轮严格模式受生成目录布局段影响 | 1 | 跳过无文本无媒体布局段并补真实差异测试 |
| Sol 终审发现标准 Markdown 假绿反例 | 1 | 重开 Terra 阶段，补语义保护、token inventory 和完整守恒测试 |
| 阶段 17 首版只增加外层 interface，核心仍反向调用 `_recognize_legacy` | 1 | 继续把 token dispatch、状态和 AST 装配迁入深 module，删除循环依赖 |
| 深 module 迁移后 Diagnostic 仍为 dict、未知 token 静默忽略、合同只有计数 | 1 | 增加类型化 Diagnostic、显式 known no-op 与 unknown fallback、完整合同编译及返回前校验 |
| Recognition Contract 暴露松散列表多段列项的 inline span 未合并 | 1 | 合并续段文本和 Inline Span，并增加容器组合回归 |
| 创建 `codex/nuitka-offline-distribution` 时 `.git/refs` 无写权限 | 1 | 使用经授权的 Git 环境创建隔离分支 |
| 授权环境因当前额度限制拒绝创建分支 | 1 | 停止分支创建，在 `dev` 工作区保留未提交实现，不提交不推送 |
| Nuitka 默认缓存目录被沙箱拒绝 | 1 | 将本机验证缓存定向到 `/private/tmp` |
| Nuitka 下载 ccache 时沙箱 DNS 失败 | 1 | 经授权联网下载官方 ccache 后继续构建 |
| Python 3.14 制品在中文可执行路径启动崩溃 | 1 | 确认为 Nuitka 实验版本路径缺陷；ASCII 路径冒烟通过，生产工作流固定 Python 3.13 并保留中文路径专项门禁 |
| 编译制品转换缺少 `docx/parts` 中间目录 | 1 | 构建与组装阶段恢复运行时目录合同并增加回归测试 |
| Linux ARM64 容器内 Pillow 12.3.0 缺少 wheel，源码编译找不到 JPEG 头文件 | 1 | 在 manylinux2014 构建入口安装 Pillow 所需系统开发库后重跑 |
| manylinux2014 默认未展开静态 libpython，Nuitka 无法自动链接 | 1 | 构建前展开镜像内 `static-libs-for-embedding-only.tar.xz` |
| Nuitka 4.1.2 standalone 在 macOS 与 Linux 的中文可执行路径启动时 SIGABRT | 2 | 使用最小程序测试后续稳定版；无修复时由安装器强制 ASCII 安装路径 |
| zsh 循环变量 `path` 覆盖 PATH，导致后续命令无法找到 | 1 | 改用 `artifact_file` 等任务专用变量名并重新执行只读检查 |
| 公开分支 cherry-pick 私有发布工具提交时出现 modify/delete 冲突 | 1 | 公开基线本来不含私有 packaging 与测试；按发布目标保留最终薄 Skill 生成树并完成 cherry-pick |
| 公开工作树整体替换时 `git rm` 拒绝删除已暂存的 cherry-pick 新文件 | 1 | 冲突已解决且目标树已确认；使用强制删除只作用于隔离发布工作树，随后复制生成树 |
| 公开隔离工作树的未跟踪 `scripts/__pycache__` 在替换后被暂存 | 1 | 删除精确字节码目录，最终公开树增加 `.py`、`.pyc`、`.pyo` 零文件门禁 |
| `apply_patch` 同一补丁对 README 同时执行 Delete 和 Add 被拒绝 | 1 | 改用单个 Update File 补丁替换全文 |

## 2026-09-14 真实 DOCX 基础格式语义修复

### 目标

基于 `奇安信-技术方案0903.docx` 的真实转换回归，按语义类型修复列表、正文首行缩进、题注和深层标题识别。语义裁决停留在 SourceFacts、Source AST 和对应 semantic module，保持 AST 到 DOCX 的渲染链只消费显式结果。

### 实施阶段

- [x] 阶段 1：建立真实文件红灯并确认模块归属
- [x] 阶段 2：DOCX 可见列表标记进入 SourceFacts 和列表语义
- [x] 阶段 3：正文首部制表符进入段落呈现语义合同
- [x] 阶段 4：图片、表格与作者题注建立对象邻接关联
- [x] 阶段 5：Heading 7 及以上源层级进入结构语义映射
- [x] 阶段 6：补源到输出审计和真实文件回归
- [ ] 阶段 7：独立代码审查、修复复审和提交

### 当前红灯

- 手工可见编号已进入 SourceFacts，列表语义统一为模板一级列表并移除正文中的可见前缀。
- 根级 DOCX 段落的展示性首部空白已在段落语义投影中消费，表格和图片混合段落保留其内联结构。
- 图片后的无编号标题、表格前的短表名和括号包裹的混合图片题注已建立显式关联。
- `Heading 7`、`Heading 8` 已映射为模板支持的六级标题。
- 真实转换退出码为 0，模型、保存输出和附录审计通过，保留 8 条源文件编号定义信息提示。

### 非目标

- 不在 rendering.py 中增加识别启发式。
- 不修改稳定的 AST 到 DOCX 调用接口。
- 不处理公开仓库、版本发布、远程推送或预存构建目录。

# 2026-10-14 Desktop 审计交付策略

目标：Desktop 显式使用非阻断审计交付策略，Skill 默认严格策略；保留审计证据与有效 DOCX，输入和保存失败仍阻断。

- [x] 架构定案与设计文档
- [x] 引擎统一交付决策、审计异常隔离与 CLI
- [x] Desktop 接线、报告保留和安全发布
- [x] 定向测试、全量回归、真实手册验证
- [x] 独立审查与证据记录

# 2026-10-14 0.18.1 后续题注修复

- [x] 已验收策略改动与版本本地提交
- [x] 分页类型与题注行内合同架构定案
- [x] 真实手册与最小夹具红灯
- [x] 解析、行内渲染与保存审计修复
- [x] 反向篡改测试、全量回归与真实文件严格转换
- [x] 独立审查、复审与后续提交
