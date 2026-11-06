# 0.18.3 发布验收

## 发布身份

- 版本：`0.18.3`
- 冻结源码：`dfc0ebe3deb7665fab9980633800b6a674873551`
- 源码树：`95d554c1df0f16a65ac1ec645f17ce2275a066c2`
- 公开提交：`544ab3e`
- Release：https://github.com/mh567/wx-doc-format-skill/releases/tag/v0.18.3

## 本次变更

默认依据输出文件和转换报告核查交付，移除题注生成后的默认截图要求。人工复核项按适用情况告知用户，保留原有交付门槛。页面视觉检查和 Word/WPS 原生应用验收由用户明确要求触发，无界面渲染不可用时不自动切换 Computer Use。

## 构建与验证

- macOS ARM64 与 Linux ARM64 均从同一冻结源码重建，Python 3.13.15、Nuitka 4.1.2。
- 两个平台各自全量测试：1319 通过、3 跳过。
- 两个平台真实 Markdown/DOCX 转换、command 增强、文件协议与恢复、复核修复、拒绝复核保护、发布门禁和离线安装验收通过。
- macOS 全部 Mach-O 为 ARM64，最大最低系统版本为 26.0；Linux 为 ELF aarch64，保留 candidate 状态。
- 公开薄 Skill validator、shell 语法、当前树与公开历史无 Python 源码门禁通过。
- 本机 HTTP 空缓存下载、安装与缓存复用通过。
- 发布后全新克隆并从 GitHub 空缓存下载，包内校验全部通过，运行时返回 0.18.3。
- 发布后在线与离线真实转换均为 completed，模型审计、保存输出审计和附录保真审计通过，异常样式为零，诊断码为空，DOCX ZIP 完整性通过。
- Release 非草稿、非预发布，三个资产均为 uploaded，GitHub digest 与本地摘要一致。

## 制品摘要

| 制品 | SHA256 |
| --- | --- |
| macOS ARM64 | `470390990ce4251196b08d388453000c713fe8f096a0edc62fd40d3f43179f9c` |
| Kylin V10 ARM64 | `ff694f4460df6a8b3d1e0543823703195f92c957b8cefab901c2abcf67032ff2` |
| SHA256SUMS.txt | `3bb1a885d16d76f05d9f81122edf872d46ae7f0f9518cc876a07125c39ecb5fb` |

## 验证边界

未执行 Kylin V10 ARM64 真机验收、Word/WPS 页面视觉验收或 agent 行为压力测试。本次变更未经过独立 agent 审查。macOS 包要求 macOS 26.0 或以上。Desktop 未随本次 Skill 发布更新。

## 本地证据

- macOS 构建：`/tmp/wxdf-release-0183/build/nuitka/local-macos-arm64/`
- macOS 冒烟：`/tmp/wx-doc-format-macos-smoke.DjCbnC/evidence/`
- Linux 构建：`build/nuitka/local-linux-arm64/`
- 公开薄 Skill 冒烟：`/tmp/wxdf-0183-public-acceptance/`
- 发布后新鲜克隆：`/tmp/wxdf-verify-0183/`
- 发布后实际转换：`/tmp/wxdf-0183-postrelease/`
- 离线解包：`/tmp/wxdf-0183-offline/`
