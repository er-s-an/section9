# EvoMap integration boundary and verification record

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
