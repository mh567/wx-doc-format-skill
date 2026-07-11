我看完当前实现了。结论是这个设计方向成立，而且要改的点很集中。当前权限是只读，我不能直接提交改动。

建议落地为两阶段：

1. Phase A 保留有序列表识别  
   保留当前 `retype -> list_item` 能力，收窄 prompt，只强调连续功能点段落。避免继续让它处理 heading、caption 等宽泛角色。

2. Phase B 替换旧 Phase C，专做题注生成  
   在 [scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:24) 中把 Phase B 的允许操作改成只允许 `set_caption_text`。删除旧 Phase C 的 `set_table_type`、`set_header_rows`、`set_caption_type` 输出能力，避免再产生机械标签。

关键实现点：

1. 新增 `_collect_caption_targets(model)`  
   收集 `_auto_generated=True` 且 `text==""` 的 caption，并带上：
   章节标题、前后正文、表格前三行预览、图片前后正文、caption_type。

2. 重写 `_build_phase_b_prompt()`  
   prompt 只要求返回：
   ```json
   {
     "schema_version": "1.0",
     "phase": "B",
     "decisions": [
       {
         "block_id": "b0012",
         "operation": "set_caption_text",
         "to": {"text": "全球部署架构"},
         "confidence": 0.85,
         "reason": "context_caption_generated"
       }
     ]
   }
   ```
   约束写死：不超过 30 字，不输出表格类型，不改已有题注，信息不足才返回空 decisions。

3. 修复 DOCX 直通渲染缺口  
   [scripts/docx_render.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/docx_render.py:432) 现在无题注表格直接插入空 `' '`，没有使用 LLM 生成结果。需要在 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:264) 从 normalized model 提取 `{源表格索引: caption_text}`，传给 `render_docx_direct()`，然后替换空白题注。

4. 保留 CLI 兼容  
   `abc` 和 `force-abc` 可以继续接受，但运行时只等价于新 `ab`，旧 C 不再执行机械结构审查。

验证重点：

1. 单测证明 Phase B 拒绝 `set_table_type`、`set_header_rows`。  
2. 单测证明已有题注不会被改。  
3. 单测证明 `_auto_generated` 空题注会被填入有意义文本。  
4. DOCX 直通渲染测试覆盖无题注表格插入生成题注文本，避免继续输出空 SEQ Table。  
5. 对“接口文档0708.docx”跑 `--llm-enhance ab`，报告里应看到约 204 条 `set_caption_text`，且输出文档不再是空题注。