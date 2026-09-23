# Local provider connection setup

The Workspace v1 page stores only provider names, endpoints, non-secret credential references, declared capabilities, and selected resource scopes. Saving a connection does not contact the provider.

## Langfuse observed-application read

The connection check supports a read-only, one-observation sample from Langfuse Cloud (`cloud.langfuse.com`) or an explicitly configured loopback Langfuse service. It calls `GET /api/public/v2/observations` with a bounded one-hour time window and a row limit of one. Results distinguish missing credentials, permission denial, an authenticated empty window, and a returned sample. This check establishes project scope; importing data is a separate operator action. Arbitrary remote endpoints are rejected until an operator-managed egress allowlist is implemented.

Configure these values in the local, ignored `.env` file before starting Section9:

```dotenv
S9_OBSERVED_LANGFUSE_PUBLIC_KEY=...
S9_OBSERVED_LANGFUSE_SECRET_KEY=...
S9_OBSERVED_LANGFUSE_PROJECT_ID=...
```

In the Workspace page, use the reference `env://S9_OBSERVED_LANGFUSE` and bind the intended Langfuse project ID. The project ID from the local credential configuration must match the selected scope before an empty result can confirm that scope. Keep these customer-source values separate from `LANGFUSE_INIT_PROJECT_*`, which belong to Section9's own telemetry.

The v2 Observations API is documented for Langfuse v4 and Cloud. Self-hosted v3 requires its separate v1 endpoint adapter; it is not silently treated as supported. The local health endpoint alone is not an authentication or project-scope check.

After a successful scope check, the Workspace page can import a time window as neutral Signal inbox rows. Each request is limited to 1,000 observations and a 31-day window. The API follows at most ten pages per request and stores the provider cursor, window, coverage, and checkpoint revision in the same transaction as the imported Signals. An unfinished window can be resumed by its returned cursor or by retrying the same window without a cursor; repeated/invalid cursors and malformed source rows keep coverage incomplete. Observation names and prompt text are not copied into Signal summaries. A human selects Signals and supplies an Incident title/severity; import never creates an Incident automatically.

## GitHub repository read

The local check uses fixed `https://api.github.com` GET routes only: repository metadata, the default-branch commit, then that commit's root contents. It reports repository name, branch, commit SHA, root entry count, and whether the returned root listing could be complete. It never returns file bodies and pins the contents read to the commit SHA observed in the same check.

Configure a fine-grained token with read-only **Contents** access for only the intended repository, then add this to the ignored local `.env` before starting Section9:

```dotenv
S9_OBSERVED_GITHUB_TOKEN=...
```

Register `env://S9_OBSERVED_GITHUB`, bind a `owner/repository` resource, and run “只读验证”. The fine-grained token needs repository-scoped **Contents: read** for this check and **Issues: read** for issue/PR intake. A 404 is intentionally reported as not found or denied because GitHub does not reveal whether an inaccessible private repository exists. No token is returned or written into the registry or event log.

The connector uses GitHub API version `2026-03-10` and reads only one repository root page during connection verification. GitHub documents a 1,000-entry maximum for a directory contents response; if the response reaches that ceiling, Section9 reports incomplete root coverage.

After scope verification, the Workspace page can also import Issues and pull requests as neutral Signals over an operator-selected `updated_at` window of at most 31 days. Each request reads at most 1,000 items in ten pages, records page continuation and coverage, and uses source number plus `updated_at` for deduplication/versioning. The API does not copy issue/PR titles or bodies into Signal summaries. The GitHub API does not provide a stable snapshot for this scan; coverage is labeled best-effort over ascending updated-time order. Imports do not create Incidents automatically.

## Current limits

- The UI's “只读验证” action is an explicit outbound GET; this repository work did not call it with a real account.
- Credentials are resolved from process environment by the `S9_OBSERVED_` reference broker. A system Keychain-backed credential manager and token rotation flow remain unimplemented.
- Langfuse project discovery, scheduled/high-watermark polling, background sync/retry jobs, and complete monitoring/coverage dashboards remain unimplemented. The current manual import follows bounded cursor pages and persists per-window continuation checkpoints.
- GitHub connection and manual issue/PR Signal intake are implemented locally, but real account verification, recursive/read-history ingestion, scheduled/high-watermark sync, webhook support, source-health monitoring, and Signal correlation remain unimplemented.
- Remote self-hosted Langfuse hosts other than the documented Cloud origin are not enabled; loopback self-hosting is permitted for local development.
- A successful one-row read proves neither full-window completeness nor business acceptance.

References: [Langfuse Observations API](https://langfuse.com/docs/api-and-data-platform/features/observations-api), [Langfuse health/readiness endpoints](https://langfuse.com/self-hosting/configuration/health-readiness-endpoints), [GitHub REST API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions), [GitHub repository contents](https://docs.github.com/en/rest/repos/contents), [GitHub fine-grained token permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens).
