# AI 增强格式审查

执行前按 [SKILL.md](../SKILL.md) 准备路径与运行环境。

在普通审查之上增加三项语义检查。它们判断的是脚本读不出的规则，请求与响应各自一份文件，与 [AI 增强转换协议](ai-conversion.md) 分开：

| 能力 | 相位 | 检查内容 |
|------|------|----------|
| `citation_order_review` | D | 引用文件／依据文件是否按「国标、国军标、行业标、团体标、企标、国际标、规章制度、其他技术文件」排序；标准号与数字之间是否有空格；是否与正文引用一一对应 |
| `glossary_review` | E | 术语是否按条排列、中文优先；缩略语是否按数字、字母顺序；缩略语一章是否误写定义 |
| `list_prose_review` | F | 列项是否被当作标题使用；列项内是否出现句号 |

两段式流程。生成请求：

```bash
"$SKILL_ROOT/scripts/run.sh" \
  --review \
  --input "$INPUT_FILE" \
  --template "$SKILL_ROOT/assets/wx_template.docx" \
  --report-md "$REVIEW_REPORT_FILE" \
  --llm-enhance all \
  --generate-requests "$REQUEST_DIR"
```

完成条件：`review_requests.jsonl` 与 `review_run.json` 已生成，或报告状态已进入失败。

逐行读取 `review_requests.jsonl`，为每个请求写出一行响应到同目录的 `review_responses.jsonl`。请求含 `request_id`、`capability`、`phase`、`task`、`allowed_codes`、`items` 和 `view_sha256`；`items` 是该能力可判断的文本条目。响应形态：

```json
{
  "request_id": "$request.request_id",
  "response_id": "$response_id",
  "view_sha256": "$request.view_sha256",
  "findings": [
    {
      "code": "$candidate_code",
      "level": "medium",
      "location": "出现位置",
      "evidence": "判断依据",
      "suggestion": "整改建议"
    }
  ]
}
```

`code` 必须取自该请求的 `allowed_codes`，`level` 必须为 `critical` / `high` / `medium` / `low`，`location` 不得为空，`view_sha256` 必须与请求一致。没有发现问题时 `findings` 用空数组。违反任何一条的响应会被记为 `llm_response_invalid` 诊断，不产生问题条目。完成条件：每个待处理请求恰有一个对应响应，且占位值均已替换。

恢复：

```bash
"$SKILL_ROOT/scripts/run.sh" --review --resume "$REQUEST_DIR/review_run.json"
```

恢复会校验输入与模板的摘要与生成请求时一致，不一致即以 `REVIEW_SOURCE_CHANGED` 停止，避免把针对另一份文档的回答套用上来。完成条件：命令退出且评分、问题摘要与 AI 语义检查状态已输出；需要报告时对应文件已生成。
