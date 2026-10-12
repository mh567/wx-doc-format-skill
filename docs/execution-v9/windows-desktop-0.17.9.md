# Windows x86_64 桌面构建验收

版本：0.17.9。本次只生成 Windows 桌面 ZIP，未修改公开 Skill Release 资产。

引擎冻结源码：77f0c2942bba50279bc4d4e773c9ebb43e71c926，tree 682be182114b6e72dad807e86176a102869f05e4。
桌面基线：468e627c70e1157a8ca4bf8cf5e704f6166dd89b，隔离构建同步 VERSION、UPSTREAM_VERSION 和 _version.py。

为支持 Windows 修复了模板快照重读文件锁、请求 JSONL 的 CRLF 指纹冲突及外部命令 POSIX 引号经 Windows shell 解析失败。现有桌面单元测试用 Python 脚本模拟引擎，在隔离测试夹具中适配 Windows EXE 文件名和 Python 调用；最终程序验收使用真实编译 EXE。

- Windows 适用引擎测试：1021 passed、5 skipped、8 deselected，macOS/Linux 发布包专项测试另行排除。
- 后续固定同一源码，复跑题注、格式和文件协议测试，再完成 Nuitka 4.1.2 / Python 3.13.16 / MSVC x64 编译。
- 桌面测试：42 passed。
- 打包程序和最终 ZIP 解压后：重复启动、单实例、Markdown 转换、结果下载和关闭通过。
- 对最终 ZIP 内的真实引擎执行 DOCX 输入转换、题注去重和章节题注检查通过，输出“表 1-1 设备接入核对表”。
- 本地下载后：ZIP 完整性、SHA256、两个 EXE 的 AMD64 PE 机器码通过。
- 验收平台为 Windows Server 2022 GitHub runner；包未进行 Authenticode 签名。

运行记录：
- 引擎测试 https://github.com/mh567/wx-doc-format-skill-dev/actions/runs/38197769343
- 运行时构建 https://github.com/mh567/wx-doc-format-skill-dev/actions/runs/38200287481
- 桌面打包 https://github.com/mh567/wx-doc-format-skill-dev/actions/runs/38201487334
- 最终 ZIP 的 DOCX 验收 https://github.com/mh567/wx-doc-format-skill-dev/actions/runs/38201733096

本地交付与日志：wx_output/windows_desktop_20261012/。原桌面工作区未修改，未发布桌面 GitHub Release。

桌面 ZIP SHA256：e4392bfdaaa0c283d4cb3fb7232d90a20911729e6dbc1a4a70915c8b53685dd9
