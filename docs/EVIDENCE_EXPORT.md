# Pair evidence export

`GET /api/pairs/{pair_id}/export.zip` creates a read-only, in-memory ZIP for a
completed or interrupted Pair. It does not call a model, modify execution authority or call inference. It refreshes the rebuildable journal projection from committed events; it does not enumerate the data directory. The archive contains the frozen `spec.json`,
pair metadata, one run/config/usage file per arm, and event pages of at most
500 events each.

`manifest.json` records per-arm event and usage counts, the page size, source
identity, and SHA-256 hashes for every other archive member. Recompute each
hash over the exact member bytes to verify the export. Unknown and failed
usage records remain present; credential fields (secret/password/authorization/API key/access token/token hash) are recursively redacted. Token counts, budgets and environment provenance remain present. Per-arm watermarks identify the exact event cut.

Download an archive from an existing local API without creating a run:

```sh
python3 scripts/export-evidence.py --pair-id PAIR_ID --output evidence.zip
```

Paged logbook: `GET /api/pairs/{id}/logbook?arm=swarm&after=0&limit=200`. Keep the returned `watermark` fixed on subsequent pages and set `after=next_after` until `has_more=false`. `as_of_sequence` and `range_end` describe the actual page, not an unseen future event. Snapshots project the complete fixed history but return only the most recent 200 events, with `log_page.view=tail`; use the logbook or ZIP for the complete record.
