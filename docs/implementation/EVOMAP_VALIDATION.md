# EvoMap 当前验证：2026-09-24

用户后续明确授权优先接通原生 EvoMap 并真实模型验证。以下结果更新 09-23 的阻塞状态；旧记录保留作为历史，不能再当作当前能力结论。

| 层次 | 当前证据与边界 |
| --- | --- |
| E0 模型推理 | 真实业务及治理调查多次完成，已知/未知用量保留 |
| E1 GEP assets | 原有固定 SDK 本地验证复用；本次未新增远端发布 |
| E2 原生协作 | 三个独立认证 node；create/join/message/context 实际成功；接收方回读完整分块并校验哈希；结果也需远端回读后才能结束本地任务；暂停/晚到拒绝/其他成员接管实跑通过 |
| E3 Hub 经验 | 未验收远端经验检索、发布、撤销，不计完成 |

最小复现及真实 session/run ID 见 [DEMO_ACCEPTANCE.md](DEMO_ACCEPTANCE.md)。源码 `s9/connectors/evomap.py`，用例 `tests/connectors/test_evomap_sessions.py`。私密 node secrets 存仓库外，接口只输出 node ID。

任务图、角色模型执行、预算/租约与人审仍由 Section9 管理；此实现没有使用 EvoMap 托管 PDRI、原生市场任务结算或公共动态成员发现。native task board 的读/新增有探测，状态更新未确认；session submit 需要有效 result_asset_id，本次没有为了凑成功而发布公共资产。HTTP 200 的 hello 仍需检查 acknowledged；大上下文真实 413 后改为内容寻址分块，缺片失败关闭。

本轮保留来源：https://evomap.ai/llms.txt 。协议为滚动接口，应以运行响应核验，不能从文档存在直接推断产品已实现。

---

## 历史快照：2026-09-23（后续已部分解除阻塞）

### 当时的 integration boundary

Checked: 2026-09-23 (public official documentation; no EvoMap account call was made)

## Four capabilities remain separate

| Layer | Local evidence | Current status |
| --- | --- | --- |
| E0 remote inference | Existing configured OpenAI-compatible EvoMap model endpoint and historical run records | `implemented`; a fresh paid request was not authorized in this task. Model access does not prove native collaboration. |
| E1 GEP assets | `integrations/gep` pins `@evomap/gep-sdk@1.14.0`; `asset.mjs` computes/verifies content IDs offline and has no network path | `local verified`; no Hub fetch, publish, receipt, or revoke. |
| E2 native collaboration | Current official public protocol index lists A2A sessions (`POST /a2a/session/create`), direct messages (`POST /a2a/dm`), agent directory, project task routes, and event streaming. The local repository has no authenticated native transport or two-node exchange evidence. | `blocked_external_authorization`; no node/session was created. |
| E3 Hub experience network | Official docs describe remote MCP and A2A memory, search, publish, identity, audit, and revoke operations. Local data never contacts Hub. | `blocked_external_authorization`; no remote read, publish, or receipt was attempted. |

## Version and protocol observations

- The installed Section9 GEP SDK is pinned to `1.14.0` in `integrations/gep/package.json` and its lockfile.
- The official `@evomap/gep-mcp-server` repository reports package version `1.7.0`. Its documented tools cover local/remote evolution memory and GEP assets; the repository README is not evidence that Section9's own two-node collaboration works.
- The live `https://evomap.ai/llms.txt` is a rolling, unversioned protocol index. It currently lists `POST /a2a/hello`, `POST /a2a/session/create`, `POST /a2a/dm`, project task APIs, and a hosted HTTP MCP endpoint. `hello` returns a node secret; the same index describes a node secret as the identity credential for mutating A2A operations. The page itself is therefore a discovery source, not a pinned client contract or an account grant.
- The separate EvoMap developer portal documents OAuth apps and recipe APIs. That is not interchangeable with an A2A node identity or a GEP Hub receipt.

## Authorization and next proof

No attempt was made to register a node, create a session, send a message, claim/complete a bounty, publish/revoke an asset, provision an account, or incur credits. These actions affect an external identity, session, task, or account. The user's request authorizes implementation and verification in the local checkout, but does not identify an EvoMap test account/node or approve creating external state.

The E2 acceptance needs an authorized test node and at least one independently authenticated peer. With those in place, the first test should use a synthetic task to record session scope, message ordering/deduplication, claim conflicts, reconnect cursor behavior, peer failure/takeover, and late-result handling. The E3 acceptance needs a test scope and approved read/write credentials to perform a bounded remote search, sandbox publish/readback, permission-negative test, and revoke/quarantine verification. Capture raw protocol responses with secrets redacted; do not upgrade either layer to `verified` from documentation or local emulation.

## Sources checked on 2026-09-23

- [EvoMap live protocol index (`llms.txt`)](https://evomap.ai/llms.txt)
- [EvoMap developer docs](https://evomap.ai/dev/docs)
- [Official `@evomap/gep-mcp-server` README](https://github.com/EvoMap/gep-mcp-server/blob/master/README.md)
- [Official `@evomap/gep-mcp-server` package manifest](https://github.com/EvoMap/gep-mcp-server/blob/master/package.json)
- [Official GEP SDK repository](https://github.com/EvoMap/gep-sdk-js)
