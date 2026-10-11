# 0.17.9 发布与桌面构建记录

## 版本来源

- 修复源码提交：`38640f0a190620b5dabcfedfc311b6e1c6855170`，已推送私有仓库 `main`。
- 源码 tree：`62988cd84b9bfe3fa45c618c8c36c478970857fa`。
- 公开生成提交：`a3af1666556bff3497e921cab8329da79b4a9699`。
- 公开 annotated tag：`v0.17.9`。
- Release：https://github.com/mh567/wx-doc-format-skill/releases/tag/v0.17.9 ，非草稿、非预发布。
- 两个平台归档由同一冻结源码提交构建，公开文件树与全历史均无 Python 源文件或字节码。

## 本次修复

相邻重复题注只输出一条；正文题注采用“表 9-1 设备接入核对表”形态；AI 新建题注校验源图表锚点，并与规则生成的空题注合并。更新 Skill 文件协议指导和回归测试。

## 验证

- macOS ARM64 与 Linux ARM64 构建环境均通过全量测试：1267 passed、3 skipped。
- 两个平台编译运行时通过 Markdown、DOCX、文件协议、续跑幂等、复核保护、报告脱敏、离线安装和 ASCII 路径门禁等验收。
- macOS 编译运行时专项验证题注去重、按章格式、表格编号 1/2/3、AI 自动题注及非法候选拒绝。
- 薄 Skill 本地空缓存安装、GitHub 新鲜 clone 空缓存安装、无 WX_DOC_FORMAT 环境变量的离线安装及真实题注转换通过。
- Release 三项资产均为 uploaded，GitHub SHA256 digest 与本地文件一致。
- AI 文件协议验收使用确定性本地响应，未调用外部模型服务。

## 桌面程序

桌面源码基于上游 `468e627c70e1157a8ca4bf8cf5e704f6166dd89b`，在隔离副本中同步三个版本文件为 0.17.9，构建副本提交 `447ad11`。原桌面工作区未修改，桌面版本提交未推送。两个桌面包均从正式 Skill Release 下载并校验运行时。两平台桌面测试各 42 passed；打包后及最终归档解压后均通过重复启动、单实例、实际 Markdown 转换、结果下载和关闭验收。

macOS 包要求 macOS 26.0 或更新版本、Apple Silicon。应用使用 ad hoc 签名，未做 Developer ID 签名与公证。Linux ARM64 为兼容容器验收通过的候选包，银河麒麟 V10 ARM64 真机安装、启动和转换验收仍需目标环境完成。

构建日志、验收记录和交付文件位于本地 `wx_output/release_0_17_9/`，该目录不进入源码仓库。

## 桌面交付 SHA256

- `wx-doc-format-0.17.9-kylin-v10-arm64.tar.gz`：`572fe529fb19626f0b29faf783ec8739aa14fc7ae79e809ef7e9f93746cbdf43`
- `wx-doc-format-0.17.9-macos-arm64.zip`：`c7b9432c1fbf8f16f3b2adab4a15bdd94d03b65f4339abe0c5b10f2fc515b70c`
