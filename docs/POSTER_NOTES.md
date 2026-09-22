# Delivery poster notes

`scripts/build-poster.py` is a repeatable, dependency-free generator. It uses `urllib.request` with `ProxyHandler({})` to read the live local API at `127.0.0.1:9019`:

- `/api/scoreboard` supplies the authoritative condition-level scoreboard;
- the `cells` array inside each row supplies condition/scenario cells; no independent run listing is re-counted, so active/reset/round2 records cannot contaminate the poster;
- the generated poster preserves empty cells as `待测` and keeps `memory_jev` as `Jev disabled`.

Run:

```sh
./.venv/bin/python scripts/build-poster.py
```

Output: `docs/DELIVERY_POSTER.html`, formatted as A3 portrait with print CSS. It references `../artifacts/delivery/repo-qr.svg`; root will generate that QR for the user repository. The poster labels the source as the repository and this round as local-only/unpushed. It contains no guessed member names, competition identity, or future acceptance values.

The poster deliberately avoids performance claims such as “swarm is faster.” Budget, model, tools, and data are aligned for the matrix; provider cache is uncontrolled, so the small sample is observational only. The footer says `展示草稿，成员信息由用户补充`.

The poster's security boundary is documentary: OS sandbox, revision/generation/hash/lease fencing, and independent acceptance are described as separate controls. Remote EvoMap Luna inference, Jev disabled, Hub OAuth pendingauth, and local-only GEP memory remain explicit. Cold-start and provider-cache measurements are not silently filled in.
