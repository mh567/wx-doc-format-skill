当前会话的文件系统权限是只读，且 `approval_policy=never`，所以我无法直接改文件或运行写入型测试。已完成现有架构检查，推荐按下面方案落地。

**改动范围**

- 新增 `scripts/llm_file_protocol.py`
  - 写入 `.wx-doc-format/llm_requests.jsonl`
  - 写入 `.wx-doc-format/run.json`
  - 读取并校验 `.wx-doc-format/llm_responses.jsonl`
  - 校验 `source_sha256`、`input_hash`、response schema
- 修改 `scripts/llm_enhancer.py`
  - 保持 `llm_call(prompt) -> str` 兼容
  - 增加可选扩展协议：当 `llm_call` 有 `prepare_request(prompt, meta)` 时传入 `phase`、`capability`、`batch_index`
- 修改 `scripts/main.py`
  - 新增 `--generate-requests DIR`
  - 新增 `--resume RUN_JSON`
  - `--resume` 从 `run.json` 恢复原始 CLI 参数
  - Phase 1 生成 requests 后停止，不保存最终 docx
  - Phase 3 用 response replay 走原有 `enhance_document_model` 校验和应用逻辑
- 修改 `SKILL.md`
  - LLM 增强默认说明为文件协议两步流程
  - API Key 和 `LLM_COMMAND` 作为兼容路径保留
- 新增测试
  - request JSONL 生成
  - response schema 校验失败
  - `input_hash` 篡改失败
  - `raw_response` patch schema 失败
  - resume 后仍走 `validate_patch` 和 `apply_patch_to_model`

**协议建议**

request 行：

```json
{
  "protocol_version": "1.0",
  "run_id": "20260712T120000Z-a1b2c3d4",
  "request_id": "A-0001",
  "phase": "A",
  "capability": "list_detect",
  "input_hash": "sha256:...",
  "prompt": "..."
}
```

response 行：

```json
{
  "protocol_version": "1.0",
  "run_id": "20260712T120000Z-a1b2c3d4",
  "request_id": "A-0001",
  "raw_response": "{\"schema_version\":\"1.0\",\"phase\":\"A\",\"decisions\":[]}"
}
```

**关键实现点**

`input_hash` 建议计算：

```python
sha256(canonical_json({
  "protocol_version": "1.0",
  "run_id": run_id,
  "request_id": request_id,
  "phase": phase,
  "capability": capability,
  "prompt": prompt,
}))
```

`run.json` 还需要记录：

```json
{
  "protocol_version": "1.0",
  "run_id": "...",
  "source_path": "...",
  "source_sha256": "...",
  "args": {
    "input": "...",
    "output": "...",
    "template": "...",
    "llm_enhance": "ab"
  },
  "requests_path": "llm_requests.jsonl",
  "responses_path": "llm_responses.jsonl"
}
```

**SKILL.md 流程**

```bash
python3 -m main \
  --input source.docx \
  --output output.docx \
  --template assets/wx_template.docx \
  --llm-enhance all \
  --generate-requests .wx-doc-format/
```

Agent 读取 `.wx-doc-format/llm_requests.jsonl`，逐行生成 `raw_response`，写入 `.wx-doc-format/llm_responses.jsonl`。

```bash
python3 -m main --resume .wx-doc-format/run.json
```

验证状态：未执行写入和测试，原因是当前沙箱只读。解除写权限后可以直接按这个范围实现并跑 `scripts/.venv/bin/python3 -m pytest tests/ -q`。