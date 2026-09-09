# 方案 3.0 交接说明

## 当前交付

T00 至 T16 的本地开发项已完成。八项源语义能力已由原生 Provider 进入真实 ExecutionPlan，并在真实 Markdown 与真实 DOCX 上完成消费和审计。默认入口保持 legacy，3.0 路径需显式指定 `--pipeline modular --list-policy wx-default-v2`。

当前本机构建产物：

`dist/local/wx-doc-format-skill-0.12.19-macos-arm64.tar.gz`

包内 45 个源码模块与 45 个编译运行时模块完全匹配。macOS ARM64 已完成全量测试、ASCII 安装冒烟、真实 DOCX 转换、DOCX ZIP 检查、专项审计和 6 页页面抽检。

## 当前验证结果

- 全量测试：402 passed。
- 真实 Markdown：八项能力均为 `native_consumed`，2 张表均有表头，2 个自动表题位置正确，风险为空。
- 真实 DOCX：八项能力均为 `native_consumed`，表格、题注、列表、附录、目录和样式审计通过。
- 编译产物：显式 modular 真实 DOCX 验收通过，`unexpected_styles=[]`。
- 公式 layout 表：源与输出的宽度、边距、无框线、行高和 OMML 数量一致。
- 性能：同配置复测三档均满足候选门槛，默认未切换。

## 使用方式

legacy 默认路径：

```bash
scripts/.venv/bin/python3 scripts/main.py \
  --input input/test_baseline.md \
  --output /tmp/wxdf-legacy.docx \
  --template assets/wx_template.docx \
  --pipeline legacy \
  --list-policy legacy \
  --llm-enhance off \
  --report /tmp/wxdf-legacy.json
```

3.0 modular 纯规则路径：

```bash
scripts/.venv/bin/python3 scripts/main.py \
  --input input/test_baseline.md \
  --output /tmp/wxdf-modular.docx \
  --template assets/wx_template.docx \
  --pipeline modular \
  --list-policy wx-default-v2 \
  --llm-enhance off \
  --report /tmp/wxdf-modular.json
```

发行包必须解压到仅含可打印 ASCII 字符的安装目录。当前工作区路径含中文，直接启动编译程序会返回 134。

## 剩余外部门禁

- Kylin V10 ARM64 真机安装与转换验收。
- Windows x86_64 已退出后续打包、验收和发布范围，现有入口仅作历史兼容保留。
- 默认切换需用户明确授权，并在跨平台结果完成后重新评估。
- `modular_online`、`modular_file`、`modular_review` 继续保持 closed。

本轮未推送、未创建标签或 Release，未替换公开仓库和本机安装副本。
