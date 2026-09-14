# FU-3 收敛收口

## 实施摘要

T06 与 T07 的两轴审查留下三项未收口：apply 语义仍有两处手工实现、回滚分支的证明仍在 `checkpoint_store` 就地重装、`checkpoint_store` 与 `response_store` 运行时仍有函数内惰性导入构成的环。本工单把这三项一并收敛。所有文件格式、摘要值、错误 code 与消息、稳定公开名字与签名保持不变。

### 生产代码

- `errors.py`（新增）：承载 `CheckpointError`。它是低层模块，只依赖 `RuntimeError`。`checkpoint_store` 顶部改为 `from .errors import CheckpointError`，`__all__` 仍导出该名字，既有导入不变。
- `response_identity.py`（新增）：承载 response 身份规则与纯 `record_response`。内容为 `_APPLICATION_FIELDS`、`content_digest`、`request_digest`、`response_digest`、`_response_id` 与纯字典级 `record_response`。模块只依赖 `digest` 与 `errors`。
- `transaction.py`：新增 `_StageOperation` 数据类与 `_STAGE_OPERATIONS` 注册表，逐 op 保存 `apply` 与 `applied` 两个行为。原 `revision_applied` 的每个分支逐字拆成 `_applied_*` 纯判定函数，其中 `update_overlay` 与 `set_presentation_override` 共用一个 `_applied_snapshot_store`。`revision_applied` 改为按 operation 查表派发，`_apply_decision` 也改为同一张表派发，并保留“先校验 source precondition，再对未知 operation 抛 `TransactionConflict`”的顺序。apply 语义与判定语义因此同处一个注册表条目。
- `decision_ledger.py`：
  - `APPLICATION_FIELDS` 改为 `from .response_identity import _APPLICATION_FIELDS as APPLICATION_FIELDS`，常量单一来源落在 `response_identity`，同一对象仍由 `decision_ledger` 与 `response_store` 共享。
  - 新增证明 owner `verify_rolled_back_response_proof(checkpoint)`，逐字承接 `checkpoint_store` 回滚分支的响应与交易绑定校验，抛 `LedgerError`，code 与消息文本不变。
- `checkpoint_store.py`：
  - 删除本地 `CheckpointError` 定义，改从 `errors` 导入。
  - 顶部删除对 `response_store` 的惰性依赖，改为 `from .response_identity import record_response as _record_response, request_digest as _request_digest, response_digest as _response_digest`。`prepare_native_resume` 与 `validate_embedded_fingerprints` 的调用点改指向这三个别名。使用私有别名，`checkpoint_store` 不再暴露 `record_response`、`request_digest`、`response_digest`。
  - `validate_checkpoint_for_store` 的回滚分支删除就地重装，改为调用 `verify_rolled_back_response_proof`，并把 `LedgerError` 就地转成 `CheckpointError`，code 与消息文本不变。
  - 文件底部调用 `_register_checkpoint_saver(save_checkpoint)`，把提交入口注入 `request_append`。
- `request_append.py`：删除全部 `checkpoint_store` 与 `response_store` 的函数内导入。`CheckpointError` 改从 `errors` 导入，`request_digest` 改从 `response_identity` 导入。新增模块级 `_checkpoint_saver` 与 `register_checkpoint_saver`，`_append_native_waiting_requests_locked` 通过注入的提交入口保存 checkpoint。公开签名 `append_native_waiting_requests(checkpoint_path, checkpoint, requests, *, crash_at=None)` 不变。
- `response_store.py`：`record_response` 改为先做 store 级校验，再委托 `response_identity.record_response`，因此 `response_store.record_response.__module__` 仍为 `wxdf.response_store`。`content_digest` 保留本地薄封装，`__module__` 不变。`request_digest` 与 `response_digest` 从 `response_identity` 再导出。`apply_response` 与 `complete_pending_response` 逻辑不动。

### 测试

- 新增 `tests/test_fu3_convergence.py`：
  - 注册表测试：断言 `_STAGE_OPERATIONS` 覆盖九个 operation 且每项都有 `apply` 与 `applied`。把 `update_role` 条目替换后，`revision_applied` 与 `_apply_decision` 都读同一条目，证明两个派发点只有一个来源。
  - 一致性电池：对九个 operation 分别构造未应用与已应用状态，断言判定函数在应用前为 False、经真实 apply 后为 True。任一操作改动 `_apply_*` 或改判定函数而不同步，电池即失败。
  - 证明 owner 测试：断言 `decision_ledger.verify_rolled_back_response_proof` 存在、`checkpoint_store` 不再含就地消息文本、`checkpoint_store` 已引用 owner。行为上覆盖一致 checkpoint 通过、篡改 binding、rejected response 携带元数据、多余 binding 三种拒绝，code 与消息文本锁定。
  - 模块图测试：对声明的七个 module 做 module 级有向图无环判定，断言目标边 `response_store -> checkpoint_store -> request_append -> response_identity/errors`，并断言 `request_append` 与 `checkpoint_store` 在所有层级都不再回指 `checkpoint_store`/`response_store`。子进程独立导入 `wxdf.request_append` 验证无环。
  - 归属测试：断言 `checkpoint_store.CheckpointError is errors.CheckpointError`、`response_identity` 拥有内容寻址 helper、`response_store` 再导出 `request_digest` 与 `response_digest`。
- `tests/test_modular_ac3.py`：`test_checkpoint_and_response_roles_have_separate_implementations` 中 `checkpoint_store.CheckpointError.__module__` 的断言改为断言 `checkpoint_store.CheckpointError is errors.CheckpointError` 且 owner 为 `wxdf.errors`。这是本工单唯一改动既有断言之处，原因是错误类型按架构定案迁入低层 `errors` 模块。`save_checkpoint`、`load_checkpoint`、`content_digest`、`record_response` 的归属断言保持原样。
- 新增文件已加入 `.gitignore` 白名单（`!tests/test_fu3_convergence.py`）。

### 未改变的接口与产物

- `run.json` checkpoint schema `2.0`、`llm_requests.jsonl`、`llm_responses.jsonl`、journal schema `1.0`、全部摘要值、全部错误 code 与消息文本保持不变。
- `CheckpointError`、`save_checkpoint`、`load_checkpoint`、`prepare_native_resume`、`complete_native_checkpoint`、`write_native_waiting_checkpoint`、`append_native_waiting_requests`、`record_response`、`apply_response`、`complete_pending_response`、`content_digest`、`request_digest`、`response_digest`、`revision_applied`、`apply_stage_batch` 等稳定名字与签名全部保留。
- `checkpoint_store.CheckpointError` 与 `request_append` 抛出的错误类型为同一对象，`except CheckpointError` 捕获行为不变。

## 红灯

新增 `tests/test_fu3_convergence.py`，断言三个目标。

命令：

```
scripts/.venv/bin/python3 -m pytest tests/test_fu3_convergence.py -q
```

实施前结果：

```
9 failed, 2 passed in 0.11s
```

九条分别失败于 `transaction._STAGE_OPERATIONS` 尚不存在、`decision_ledger.verify_rolled_back_response_proof` 尚不存在、回滚证明 owner 的四条行为断言因缺函数而失败、`wxdf.errors` 尚不存在、`wxdf.response_identity` 尚不存在、声明模块图源文件中缺 `errors.py` 导致解析失败。两条通过的是本工单的一致性电池与子进程独立导入断言：它们在基线即成立，作用是在后续改动中守住 apply 与判定不漂移、以及 `request_append` 不被导入环污染。

## 绿灯

定向命令与结果：

```
scripts/.venv/bin/python3 -m pytest tests/test_fu3_convergence.py -q
11 passed in 0.11s
```

## 导入方向证明

对声明的七个 module 解析 module 级与全层级 `from .` 边：

```
module-level edges (declared set):
  errors -> []
  response_identity -> ['errors']
  checkpoint_store -> ['decision_ledger', 'errors', 'request_append', 'response_identity']
  request_append -> ['errors', 'response_identity']
  response_store -> ['checkpoint_store', 'decision_ledger', 'response_identity']
  decision_ledger -> ['response_identity', 'transaction']
  transaction -> []
all-level edges into declared set:
  errors => []
  response_identity => ['errors']
  checkpoint_store => ['decision_ledger', 'errors', 'request_append', 'response_identity', 'transaction']
  request_append => ['errors', 'response_identity']
  response_store => ['checkpoint_store', 'decision_ledger', 'response_identity']
  decision_ledger => ['response_identity', 'transaction']
  transaction => []
ACYCLIC: no module-level cycle among the declared modules
NO upward runtime imports: request_append/checkpoint_store do not reach response_store/checkpoint_store
```

结论：目标链 `response_store -> checkpoint_store -> request_append -> response_identity/errors -> digest` 成立。`request_append` 在所有层级都不回指 `checkpoint_store` 或 `response_store`，`checkpoint_store` 在所有层级都不回指 `response_store`。剩余函数内导入只出现在 `kernel.py` 与 `native_stages.py` 这两个消费方，方向向下，不构成环。`checkpoint_store` 全层级出现的 `transaction` 来自既有函数内导入 `TransactionConflict` 与 `make_snapshot`，`transaction` 为叶子，不产生环。

独立导入验证：

```
scripts/.venv/bin/python3 -c "import sys; sys.path.insert(0,'scripts'); import wxdf.request_append; print('ok')"
ok
```

## 相关回归

账本、恢复、发布、native review 与 T04 至 T07 专项集合：

```
scripts/.venv/bin/python3 -m pytest tests/test_modular_ledger.py tests/test_modular_resume.py tests/test_modular_publication.py tests/test_t04_facade_removal.py tests/test_t05_digest_owner.py tests/test_t06_transaction_proof_owner.py tests/test_t06_cli_cutover.py tests/test_t07_checkpoint_split.py tests/test_modular_ac3.py tests/test_modular_contracts.py tests/test_modular_review.py tests/test_modular_native_review.py tests/test_fu3_convergence.py -q
290 passed in 12.70s
```

真实 CLI 驱动的 checkpoint 生成、resume、崩溃与竞态注入集合：

```
scripts/.venv/bin/python3 -m pytest tests/test_modular_file_protocol.py -q
64 passed in 31.57s
```

`test_modular_file_protocol.py` 覆盖 `record_response` 在 resume 路径上的行为、request-append 的崩溃恢复与锁粒度、以及回滚证明相关拒绝，全部通过。测试中的 `_replace_bytes` 与 `request_append.os.replace` 注入点仍在 `request_append`，注入提交入口后 journal 行为未变。

## 全量测试

```
scripts/.venv/bin/python3 -m pytest tests/ -q
1129 passed in 133.16s (0:02:13)
```

数量比 T07 的 1118 多 11，即本工单新增的 11 条 FU-3 断言。既有测试数量不变，`test_modular_ac3.py` 只改写了同一条断言的归属表述。

## 开放项

- 独立审查与复审由另一个智能体承担，尚未执行；本文件记录实现侧结果。
- `request_append` 通过 `register_checkpoint_saver` 接收 checkpoint 提交入口，`checkpoint_store` 在导入末尾注册。若脱离 `checkpoint_store` 直接调用 `append_native_waiting_requests`，会以 `checkpoint_store_not_registered` 拒绝。生产入口与既有测试都经 `checkpoint_store` 绑定该名字，因此该分支只在误用下触发。
- `request_append._path` 与 `checkpoint_store._path` 仍各有一份一行的同名助手。本工单未处理该小项，理由是归并会引入对 `checkpoint_store` 的反向依赖。
- `checkpoint_store` 仍保留若干函数内导入 `decision_ledger` 与 `publication`、`semantics` 等。这些导入不构成声明的三个 module 之间的环，不在本工单范围内。
- `response_store` 仍从 `decision_ledger` 导入 `APPLICATION_FIELDS`、`LedgerError`、`verify_response_transaction_proof`，方向向下且有环检测覆盖，保持现状。
- `.gitignore` 新增一行白名单，用于让红灯文件纳入版本。
- 未改动 `docs/execution-v5/status.json`，未提交，改动保留在工作区。未触碰 `scripts/wxdf/semantics/**`、`kernel.py`、`registry.py`、`run_result.py`、`digest.py`、`native_stages.py`、`semantic_modules.py`、`normalization.py`、`resolver.py`、`publication.py`、`validation.py`。
