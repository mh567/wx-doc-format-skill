# 0.18.1 发布验收

## 冻结身份

- Skill 源码提交：`059cb1f9d6d2387bb7e2f436b3b0efb587f8005d`
- Skill 源码树：`edfb63d67e4d9d71a9c535324f25575939cd6327`
- Desktop 源码提交：`83245a6fba6dee1a04dd8f4937fa59e55afefc3c`
- 公开薄 Skill 提交：`298fd2c`，公开历史与当前树均无 `.py`、`.pyc`、`.pyo`。

## Skill 制品

两个平台均从同一冻结源码构建。Python 3.13.15，Nuitka 4.1.2。各平台全量测试 1302 通过、3 跳过；standalone 的 Markdown/DOCX、增强协议、复核提交与拒绝、报告摘要、文件集/hash、离线安装及 ASCII 路径门禁通过。

| 平台 | SHA256 | 验收范围 |
| --- | --- | --- |
| macOS ARM64 | `a769aa69e5c98d12b2be6ea7fee38b4ddc518483e7bb414ab2f3621997a3a634` | 原生构建和转换通过，全部 Mach-O 为 ARM64，最大 minos 26.0 |
| kylin-v10-arm64 | `940bf23911fb467d97ee60c4f0ffbba9eabdb0c845daceb87478ecfd8f3f8376` | Linux aarch64 容器构建及转换通过，保留 candidate 标识 |

公开 Skill validator、shell 语法、清单与本地 HTTP 空缓存安装验收通过。GitHub 新鲜克隆的空缓存下载、安装、版本检查与真实用户手册转换通过。真实转换显式使用发布包 `assets/wx_template.docx`，严格交付策略通过全部专项审计。

## Desktop 验证

本地 macOS Desktop 使用上述已校验原生 Skill 打包；54 项测试通过。重复启动、原生服务模式、Markdown 转换、三个下载入口和关闭清理通过。ZIP 解压后代码签名 deep/strict 与运行时版本检查通过。真实用户手册通过打包服务转换，交付策略为 desktop，审计 passed，无额外样式、诊断或复核项。

Desktop 正式 Release 工作流 `38547812361` 的 validate、portable、kylin、publish 全部 success，同源码的两次 CI 也 success。最终线上 macOS ZIP 与独立验收的 CI ZIP 字节完全一致；正式 CI 制品转换用户手册 completed/audit passed/无诊断。两个平台下载清单校验通过，GitHub digest 与本地下载一致。

| Desktop 最终发布资产 | SHA256 |
| --- | --- |
| macOS ARM64 ZIP | `90e6ffe63d86802603f8df3e141817cebd8aaf37f3a19b63755c76f8f68446cc` |
| kylin-v10-arm64 tar.gz | `3d9d2c33b47e401260cb22077b0906ff22606ee0e7f7eea13d5fd8f49e313deb` |

两个仓库 Release 均为非草稿、非预发布，三个资产状态 uploaded。Skill 与 Desktop annotated tag 分别解引用到公开提交 `298fd2cc4b13bf3df449d91efa2e38d00fdab624` 与 Desktop 源码提交。Skill 下载归档的标准离线安装及真实用户文档严格转换再次通过。

- [Skill Release](https://github.com/mh567/wx-doc-format-skill/releases/tag/v0.18.1)
- [Desktop Release](https://github.com/mh567/wx-doc-format-desktop/releases/tag/v0.18.1)

本地 Linux Desktop 的补充构建在 Colima 共享目录最终 `cp -a` 遇到扩展权限错误，按相同布局复制完成候选归档；最终公开资产来自成功的官方 CI 工作流，未使用该补充归档。

## 边界

- macOS 包要求 macOS 26.0 或以上，Apple Silicon。
- macOS Desktop 使用临时签名，未执行 Developer ID 公证。
- Linux 容器通过不代表银河麒麟 V10 真机验收。
- 未完成 Word/WPS 页面视觉与目标应用验收。
- 本次提供 macOS ARM64 和 Linux ARM64 两个平台。

## 证据位置

- macOS Skill：`/private/tmp/wxdf-release-0181-source/build/nuitka/local-macos-arm64/acceptance.json`
- 公开树安装：`/private/tmp/wxdf-0181-public-acceptance/`
- Linux Skill：仓库忽略目录 `build/nuitka/release-linux-0181-source/`
- 打包 Desktop 用户手册：`/private/tmp/wxdf-desktop-user-0181/evidence.json`
- 线上 Skill 用户手册：`/private/tmp/wxdf-release-user-document/online-report.json`
