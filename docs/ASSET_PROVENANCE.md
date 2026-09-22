# 小智客服资产来源

这些文件从本地 AgentMED workload 原样复制，仅作为 section9 的可审计 workload 资产；运行时不会把 registry 中的旧模型标识当作新模型证据。

来源目录：`/Users/xiejiachen/Documents/ChatGPT/rebuild/AgentMED/workloads/xiaozhi-customer-service`

| 文件 | 用途 | SHA-256 |
|---|---|---|
| `assets/xiaozhi/prompts/system.v1.4.2.md` | 健康 prompt（7 天内激活后仍可退、商家承担运费） | `ac328249384d4ae6112120b6a6c7f0d77711b036b71d15ab2f935cbac2a25b3e` |
| `assets/xiaozhi/prompts/system.v1.4.3.b1.md` | 退化/坏 prompt 检测材料（人工审核、激活商品不支持退货） | `be2021555e553adcce1596c2fe9acf9fc29aad42ae3df59f1c8bd9890ad33ae9` |
| `assets/xiaozhi/kb.yaml` | 产品、物流与旧政策上下文（约 13 KB） | `ebd4f501f24415640645af30768b1ca4615829583058f95fa8b606fba1bfa3cb` |
| `assets/xiaozhi/registry.json` | 原 workload registry（约 140 KB，含旧 revision/model 记录） | `29b4456fc2f57c6444964202f91906373cfb523079b7ec490901df31a2674cd1` |

健康 prompt 的售后政策是 7 天内激活后仍可退且商家承担运费；坏 prompt 用于退化检测。产品验收使用知识库中的 X200 续航 30 小时事实。真实答案仍由注入的 `model.complete` 生成，模型失败返回错误状态，不使用硬编码答案兜底。
