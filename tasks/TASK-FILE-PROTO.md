实现 docs/file-protocol-design.md 文件协议重构。

1. 阅读 docs/file-protocol-design.md
2. 新增 scripts/llm_file_protocol.py：
   - 写入 llm_requests.jsonl + run.json
   - 读取校验 llm_responses.jsonl（input_hash、schema）
3. 修改 scripts/main.py：
   - 新增 --generate-requests DIR
   - 新增 --resume RUN_JSON
   - Phase 1: 生成 requests 后停止
   - Phase 3: resume 走原有 enhance/validate/apply
4. 修改 scripts/llm_enhancer.py（保持 llm_call 兼容）
5. 更新 SKILL.md 为两步流程
6. 测试：生成 requests + 校验 response + end-to-end
