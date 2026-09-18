# wx-doc-format-skill 离线发布包

本发布包用于将 Markdown 或 DOCX 文档转换为 WX 模板格式的 DOCX 文档。运行时无需安装 Python，安装和转换过程不下载文件。

当前离线发布目标为 macOS ARM64 和 Kylin V10 ARM64。macOS 制品经最低系统版本校验后进入 accepted 状态。Kylin V10 ARM64 制品保持 candidate 状态，启动时会输出稳定告警并继续执行。

## 安装

将本安装包交给 Agent：

> 安装这个 wx-doc-format 离线 Skill 包

Agent 解压后将包含 `SKILL.md` 的完整目录安装到自身 skills 目录，建议目录名使用 `wx-doc-format`。安装完成后可直接通过对话触发。

也可以执行包内安装脚本，并将目标 Agent 的 Skill 路径作为安装目录传入：

POSIX 系统：

```bash
scripts/install.sh /path/to/agent/skills/wx-doc-format
```

安装前会验证 SHA256，升级时会在目标目录旁保留上一版备份。
安装目录的完整路径必须只包含可打印 ASCII 字符。如果用户主目录含有中文等字符，请显式指定类似 `/opt/wx-doc-format` 的目录。安装脚本和启动脚本会在运行前检查该条件。

## 使用

上传文档，并在对话里说明要做哪一种。Agent 未确定时请用户四选一，用户已说明时直接执行。

| 选项 | 触发说法 | 产物 |
|------|----------|------|
| 普通格式转换 | 技术文档格式、转换为技术文档格式、按技术文档格式排版、整理成技术文档、wx文档格式、WX 格式转换 | 模板化 DOCX 与审计报告 |
| AI 增强格式转换 | 以上任一说法，另加“LLM 增强模式” | 同上，转换前用 LLM 复核目录区域、列表识别与题注 |
| 普通格式审查 | 审查这个文档的格式、检查文档是否符合模板要求、格式审查、文档格式检查、给这个文档的格式打分、生成格式整改报告 | 百分制评分与整改报告，完全离线 |
| AI 增强格式审查 | 以上任一说法，另加“AI 增强” | 同上，另加引用文件排序、术语与缩略语、列项用法的语义检查 |

转换接受 Markdown 或 DOCX，审查只接受 DOCX。示例：

> 技术文档格式，请处理这个附件。

> wx文档格式，请把附件转换成标准格式。

> 审查这个 DOCX 是否符合模板要求，给出评分和整改建议。

普通模式与普通审查不需要任何模型；两个 AI 模式需要 Agent 提供模型响应，离线文件协议见 `SKILL.md`。

以下命令仅用于手工检查：

```bash
scripts/run.sh --version
scripts/run.sh --input source.docx --output output.docx --template assets/wx_template.docx
scripts/run.sh --review --input source.docx --template assets/wx_template.docx --report-md review.md
```

详细模式、文件协议、公开报告摘要和验证要求见 `SKILL.md`。

## 完整性和兼容性

`SHA256SUMS.txt` 用于检查包内文件完整性。当前制品未包含签名认证。manifest 声明当前平台状态和 `public_summary` 报告合同。Kylin V10 制品在 manifest 中标记为 candidate，交付前需要在目标架构真机上完成冒烟测试。
