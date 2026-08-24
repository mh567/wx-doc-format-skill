# wx-doc-format-skill

## 核心能力

将 Markdown 或 DOCX 文档转换为 WX 模板格式的 DOCX，自动处理标题、目录、列表、表格、题注和附录，并生成审计报告。

运行时使用本机编译包，目标机无需安装 Python。当前提供 macOS ARM64 和 Linux ARM64 制品，Windows x86_64 运行时待后续补充。

## 安装方法

对支持 Skill 的 Agent 说：

> 从 GitHub 仓库 mh567/wx-doc-format-skill 安装 wx-doc-format skill

安装完成后可直接通过对话触发。首次转换会自动获取当前平台制品、验证 SHA256 并完成本地安装，无需手工执行命令或配置环境变量。

内网离线部署时，将对应平台的 Release 归档放入 Skill 的 `artifacts/` 目录，再分发完整 Skill 目录。首次触发会自动识别本地归档并完成安装。

## 使用方法

上传 Markdown 或 DOCX 文档，并在对话中使用“wx文档格式”触发。例如：

> wx文档格式，请把附件转换成标准格式。

> 使用 wx文档格式的普通模式处理这个 DOCX。

> 使用 wx文档格式的 LLM 增强模式处理这个 Markdown。

未指定模式时，Agent 会请用户选择：

1. 普通模式：使用本地规则转换，适合结构清晰的文档。
2. LLM 增强模式：增加模糊目录、语义列表、题注生成和审计后受限复核。

用户已在指令中选定模式时，Agent 会直接执行。完成后返回转换后的 DOCX，并说明审计结果和需要人工复核的项目。
