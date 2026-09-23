"""Read-only GitHub repository metadata, default-branch SHA, and root listing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx


GITHUB_API_VERSION = "2026-03-10"


@dataclass(frozen=True)
class GitHubConfig:
    token: str = field(repr=False)
    owner: str
    repo: str
    timeout_seconds: float = 15.0


class GitHubReadConnector:
    """Use only fixed-host GitHub REST GET routes; never returns raw file bodies."""

    def __init__(self, config: GitHubConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client

    async def __aenter__(self) -> "GitHubReadConnector":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds, trust_env=False,
                                             follow_redirects=False)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.config.token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    async def read_repository(self) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("use connector as an async context manager")
        owner = quote(self.config.owner, safe="")
        repo = quote(self.config.repo, safe="")
        base = f"https://api.github.com/repos/{owner}/{repo}"
        repository_response = await self._client.get(base, headers=self._headers())
        if repository_response.status_code != 200:
            return self._response_failure(repository_response)
        try:
            repository = repository_response.json()
        except ValueError:
            return {"http_status": repository_response.status_code, "availability": "invalid_response"}
        if not isinstance(repository, dict):
            return {"http_status": repository_response.status_code, "availability": "invalid_response"}
        canonical = repository.get("full_name")
        expected = f"{self.config.owner}/{self.config.repo}"
        if not isinstance(canonical, str) or canonical.casefold() != expected.casefold():
            return {"http_status": repository_response.status_code, "availability": "scope_mismatch"}
        default_branch = repository.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            return {
                "http_status": repository_response.status_code,
                "availability": "empty",
                "full_name": canonical,
                "private": repository.get("private") is True,
                "default_branch": None,
                "commit_sha": None,
                "files_count": 0,
                "coverage_complete": True,
            }

        branch = quote(default_branch, safe="")
        commit_response = await self._client.get(f"{base}/commits/{branch}", headers=self._headers())
        if commit_response.status_code != 200:
            result = self._response_failure(commit_response)
            result["full_name"] = canonical
            return result
        try:
            commit = commit_response.json()
        except ValueError:
            return {"http_status": commit_response.status_code, "availability": "invalid_response"}
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not sha:
            return {"http_status": commit_response.status_code, "availability": "invalid_response"}

        contents_response = await self._client.get(f"{base}/contents", params={"ref": sha}, headers=self._headers())
        if contents_response.status_code != 200:
            result = self._response_failure(contents_response)
            result.update({"full_name": canonical, "default_branch": default_branch, "commit_sha": sha})
            return result
        try:
            contents = contents_response.json()
        except ValueError:
            return {"http_status": contents_response.status_code, "availability": "invalid_response"}
        if isinstance(contents, dict) and isinstance(contents.get("entries"), list):
            entries = contents["entries"]
        elif isinstance(contents, list):
            entries = contents
        else:
            return {"http_status": contents_response.status_code, "availability": "invalid_response"}
        return {
            "http_status": contents_response.status_code,
            "availability": "data" if entries else "empty",
            "full_name": canonical,
            "private": repository.get("private") is True,
            "default_branch": default_branch,
            "commit_sha": sha,
            "files_count": len(entries),
            "coverage_complete": len(entries) < 1000,
        }

    async def read_issues(
        self,
        *,
        updated_since: str,
        page: int = 1,
        per_page: int = 100,
    ) -> dict[str, Any]:
        """Read one bounded, oldest-first issue/PR page without returning titles in metadata."""
        if self._client is None:
            raise RuntimeError("use connector as an async context manager")
        if page < 1 or page > 100_000 or per_page < 1 or per_page > 100:
            raise ValueError("GitHub issue page must be bounded")
        owner = quote(self.config.owner, safe="")
        repo = quote(self.config.repo, safe="")
        response = await self._client.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            params={
                "state": "all",
                "since": updated_since,
                "sort": "updated",
                "direction": "asc",
                "per_page": per_page,
                "page": page,
            },
            headers=self._headers(),
        )
        if response.status_code != 200:
            return self._response_failure(response)
        try:
            rows = response.json()
        except ValueError:
            return {"http_status": response.status_code, "availability": "invalid_response"}
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return {"http_status": response.status_code, "availability": "invalid_response"}
        return {
            "http_status": response.status_code,
            "availability": "data" if rows else "empty",
            "rows": rows,
            "has_next": "next" in response.links,
            "page": page,
        }

    @staticmethod
    def _response_failure(response: httpx.Response) -> dict[str, Any]:
        code = response.status_code
        if code == 401:
            availability = "permission_denied"
        elif code == 403:
            availability = "rate_limited" if response.headers.get("Retry-After") or response.headers.get("X-RateLimit-Remaining") == "0" else "permission_denied"
        elif code == 404:
            availability = "not_found_or_denied"
        elif code == 429:
            availability = "rate_limited"
        elif code >= 500:
            availability = "unavailable"
        else:
            availability = "request_rejected"
        return {"http_status": code, "availability": availability}
