建议选第二类方案：`main.py` 不直接调用 LLM，只负责生成结构化 prompt、校验 response、应用 patch。Agent 负责读取请求、调用当前会话里的 LLM、写回 response，再让 `main.py` 继续渲染。

**推荐架构**

保留现有 `llm_enhancer.py` 的核心边界：`llm_call: Callable[[str], str]` 仍作为单测和内部接口存在。但 CLI 层新增一个“Agent 托管 LLM”模式：

1. `main.py --llm-enhance all --llm-provider agent`
2. `main.py` 解析文档，生成 `.wx-doc-format/llm_requests.jsonl`
3. Agent 读取每条 request，用自身 LLM 生成 JSON response
4. Agent 写入 `.wx-doc-format/llm_responses.jsonl`
5. `main.py --resume .wx-doc-format/run.json` 校验 response、应用 patch、渲染、审计

对用户来说仍然是“选择 LLM 增强模式后直接转换”。多步执行由 Skill 的 `SKILL.md` 工作流隐藏，用户不需要设置 `LLM_COMMAND`、API Key 或模型名。

**为什么这最合理**

当前代码里 `main.py` 的 `_resolve_llm_call()` 依赖命令、API Key、桥接脚本，问题在于它假设子进程能主动访问模型。实际 Agent LLM 位于父进程或工具层，Hermes、Codex、Claude Code 的暴露方式也不一致。

文件协议更稳：

- 跨 Agent 通用，不要求 stdin/stdout LLM CLI
- 不绑定 `codex exec`、`claude`、Hermes 私有工具
- `main.py` 保持可测试、可复现、可离线降级
- response 仍由 `main.py` 做 schema 校验、置信度过滤和 report 记录
- 失败时可以从 request/response 文件恢复，便于审计

**不建议的方案**

`main.py` 输出 prompt 后阻塞等待 Agent 写 response，体验看似更像“一次运行”，但跨 Agent 不稳定。很多 Agent 不会在子进程阻塞期间可靠地读取中间文件并继续写回，超时、取消、并发转换都会变复杂。

`LLM_COMMAND` 自动设置也只能覆盖 Codex、Claude Code 这类有 CLI 的环境。Hermes 这种只能通过 Agent 工具调 LLM 的环境仍然卡住，所以它不能作为无感化主方案。

**落地形态**

新增一个协议层比改增强逻辑更合适：

```json
{
  "protocol_version": "1.0",
  "run_id": "...",
  "capability": "list_detect",
  "phase": "A",
  "request_id": "A-0001",
  "prompt": "...",
  "response_format": "json_patch",
  "input_hash": "..."
}
```

Agent 写回：

```json
{
  "protocol_version": "1.0",
  "run_id": "...",
  "request_id": "A-0001",
  "raw_response": "{...}"
}
```

`main.py` 只信任 `request_id`、`input_hash`、schema 校验结果，不信任 Agent 直接写好的 patch。这样安全边界清楚。

**结论**

最合理的无感化设计是：LLM 调用上移到 Agent 工作流，`main.py` 下沉为“prompt 生成器 + response 校验器 + patch 应用器”。`LLM_COMMAND` 和 API Key 可以保留为高级兼容路径，但默认 LLM 增强模式应走 Agent 托管文件协议。