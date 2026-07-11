当前沙箱是只读，`apply_patch` 被拒绝，所以我无法直接修改文件。需要改的点已经定位在 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:127) 和 CLI 参数区 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:450)。

建议改法如下：

```diff
- def _resolve_llm_call(args):
-     import os as _os, shutil, subprocess
+ def _llm_call_from_command(command: str):
+     import os as _os
+     import shlex
+     import subprocess
+     import tempfile
+
+     command = command.strip()
+     if not command:
+         return None
+
+     def _llm(prompt: str) -> str:
+         if "{prompt_file}" not in command:
+             r = subprocess.run(
+                 command,
+                 input=prompt,
+                 text=True,
+                 shell=True,
+                 capture_output=True,
+                 timeout=60,
+             )
+             if r.returncode != 0:
+                 raise RuntimeError((r.stderr or r.stdout or "").strip())
+             return r.stdout or ""
+
+         prompt_path = None
+         try:
+             with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".prompt.txt", delete=False) as f:
+                 f.write(prompt)
+                 prompt_path = f.name
+             r = subprocess.run(
+                 command.replace("{prompt_file}", shlex.quote(prompt_path)),
+                 text=True,
+                 shell=True,
+                 capture_output=True,
+                 timeout=60,
+             )
+             if r.returncode != 0:
+                 raise RuntimeError((r.stderr or r.stdout or "").strip())
+             return r.stdout or ""
+         finally:
+             if prompt_path:
+                 try:
+                     _os.unlink(prompt_path)
+                 except OSError:
+                     pass
+
+     return _llm
+
+
+ def _resolve_llm_call(args):
+     import os as _os
+
+     command = getattr(args, "llm_command", None) if args is not None else None
+     command = command or _os.environ.get("LLM_COMMAND")
+     command_call = _llm_call_from_command(command or "")
+     if command_call is not None:
+         return command_call
```

然后删除 `_resolve_llm_call` 里的 `shutil.which("codex")` fallback 整段，只保留 `ANTHROPIC_API_KEY` 和 `OPENAI_API_KEY` 两段。

CLI 参数区新增：

```python
parser.add_argument(
    "--llm-command",
    type=str,
    default=None,
    help=(
        "Shell command used for LLM enhancement. The prompt is sent to stdin "
        "and stdout is used as the response. Use {prompt_file} if the command "
        "expects a prompt file path."
    ),
)
```

这样调用顺序是：

1. `--llm-command`
2. `LLM_COMMAND`
3. `ANTHROPIC_API_KEY`
4. `OPENAI_API_KEY`
5. 无后端则返回 `None`

现有转换流程不需要改，`convert_md` 和 `convert_docx` 已经通过 `_resolve_llm_call(args)` 注入 callable。