# 0.18.2 发布验收

## 冻结身份

- Skill 源码提交：`9b11fdebfb91bc9cf0cb383ee2ad8abad2a441a3`
- Skill 源码树：`94c75067894f588a3df81ccc2b6e5ef5438a0dea`
- Desktop 源码提交：`73bb33f6c1a353b2a79469e599f2077411a697b2`
- 公开薄 Skill 提交：`7bcd7b9`，公开历史与当前树均无 `.py`、`.pyc`、`.pyo`
- 版本：两个仓库与全部制品均为 `0.18.2`

## 本次内容

- 源用直接 `w:outlineLvl` 声明标题时按大纲级别判标题，层级取 `min(outline_level + 1, 6)`，`outlineLvl=9` 仍按正文文本处理
- 源目录控件内的表格随控件一并排除，封面修订表不再残留为空表
- 图题注只给独占段落的图片，与正文同段的内联图标和表格单元格内的图片不再配题注
- 源未声明标题时按前置策略补文档标题，DOCX 写入 `document_title_generated` 风险提示
- 模型审计新增 `source_outline_not_materialized`，源声明根级大纲标题而交付模型无标题时阻断

## Skill 制品

两个平台均从同一冻结源码构建。Python 3.13.15，Nuitka 4.1.2。各平台全量测试 1319 通过、3 跳过。

| 平台 | SHA256 | 验收范围 |
| --- | --- | --- |
| macOS ARM64 | `9da9d68c719706429f768608c38bc2ac9f1bc54b4c0f2030dcc6bcf91a5fcc63` | 原生构建与转换通过，全部 Mach-O 为 ARM64，最大 minos 26.0 |
| kylin-v10-arm64 | `deb1669d67d97554965da0f402fc3d002879653ce486070b00ac0ebab6c14374` | Linux aarch64 容器构建与转换通过，保留 candidate 标识 |

公开清单的 `source_identity` 记录 version 0.18.2、commit `9b11fde`、tree `94c7506`，两个平台一致。

门禁结果：Skill validator 通过，`/bin/sh -n scripts/run.sh` 通过，公开树无 `.py`、`.pyc`、`.pyo`，`artifacts/SHA256SUMS.txt` 与 `dist/local/` 归档 SHA256 一致。本机 HTTP 空缓存下载、安装与缓存复用通过。

发布后验证：全新克隆 `7bcd7b9`，validator、shell 与公开历史门禁再次通过。空缓存执行 `scripts/run.sh --version` 得到 `0.18.2`。归档解压到空目录后在不设置 `WX_DOC_FORMAT_*` 变量下执行 `--version` 得到 `0.18.2`。

真实转换使用发布包内 `assets/wx_template.docx` 与用户提供的 `副本1管理平台用户手册-v10.docx`，CLI 返回码 0，`status` 为 `completed_with_warnings`，`model_audit` 与 `saved_output_audit` 均通过，`unexpected_styles` 为空。报告保留一条可修复的 `source_heading_level_normalized` 与一条 `document_title_generated`。

## Desktop 验证

Desktop 与 Skill 同版本发布，`tools/check_release.py --tag v0.18.2` 通过。

正式 Release 工作流 `38599967940` 的 validate、portable、kylin、publish 全部 success；同源码的 CI 也 success（`38599967982`、`38599962338`）。

| Desktop 发布资产 | SHA256 |
| --- | --- |
| macOS ARM64 ZIP | `4853526fb7a4a247d948594fedf1a58f6555d2a389f8f2ed4be6f3941c94b17f` |
| kylin-v10-arm64 tar.gz | `95a41dafc6ba78da525e65679f6c3ae3ba117a2080878c1e6383bd350005af05` |

两个 Release 均为非草稿、非预发布，三个资产状态 uploaded，GitHub digest 与本地 SHA256 一致。Skill 与 Desktop annotated tag 分别解引用到公开提交 `7bcd7b9` 与 Desktop 源码提交 `73bb33f`。

- [Skill Release](https://github.com/mh567/wx-doc-format-skill/releases/tag/v0.18.2)
- [Desktop Release](https://github.com/mh567/wx-doc-format-desktop/releases/tag/v0.18.2)

## 边界

- macOS 包要求 macOS 26.0 或以上，Apple Silicon。
- Linux 容器通过不代表银河麒麟 V10 真机验收。
- 未完成 Word/WPS 页面视觉与目标应用验收。
- 本次提供 macOS ARM64 和 Linux ARM64 两个平台，未构建 Windows 制品。

## 证据位置

- macOS Skill 验收：`build/nuitka/local-macos-arm64/acceptance.json`，冒烟证据 `/tmp/wx-doc-format-macos-smoke.zc2Dtz/evidence`
- Linux Skill 构建：仓库忽略目录 `build/nuitka/local-linux-arm64/`
- 公开树冒烟：`/private/tmp/wxdf-0182/evidence`
- 发布后克隆验证：`/private/tmp/wxdf-verify-0182`
- 离线安装与真实转换：`/private/tmp/wxdf-offline-0182`
