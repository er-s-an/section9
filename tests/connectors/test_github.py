import httpx
import pytest

from s9.connectors.github import GITHUB_API_VERSION, GitHubConfig, GitHubReadConnector


@pytest.mark.asyncio
async def test_github_repository_check_uses_fixed_read_routes_and_pins_commit():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer ghp-test-only"
        assert request.headers["x-github-api-version"] == GITHUB_API_VERSION == "2026-03-10"
        assert request.headers["accept"] == "application/vnd.github+json"
        if request.url.path == "/repos/acme/service":
            return httpx.Response(200, json={"full_name": "Acme/Service", "private": True,
                                             "default_branch": "release/v2"})
        if request.url.raw_path == b"/repos/acme/service/commits/release%2Fv2":
            return httpx.Response(200, json={"sha": "a" * 40})
        if request.url.path == "/repos/acme/service/contents":
            assert request.url.params["ref"] == "a" * 40
            return httpx.Response(200, json=[{"path": "src"}, {"path": "README.md"}])
        raise AssertionError(f"unexpected route: {request.url}")

    cfg = GitHubConfig(token="ghp-test-only", owner="acme", repo="service")
    async with GitHubReadConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))) as connector:
        result = await connector.read_repository()
    assert len(seen) == 3
    assert result == {
        "http_status": 200, "availability": "data", "full_name": "Acme/Service", "private": True,
        "default_branch": "release/v2", "commit_sha": "a" * 40,
        "files_count": 2, "coverage_complete": True,
    }
    assert "ghp-test-only" not in repr(cfg)
    assert "ghp-test-only" not in str(result)


@pytest.mark.asyncio
async def test_github_rate_limit_and_permission_errors_are_distinct():
    async def rate_limited(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})

    async with GitHubReadConnector(GitHubConfig(token="secret", owner="a", repo="b"),
                                   httpx.AsyncClient(transport=httpx.MockTransport(rate_limited))) as connector:
        result = await connector.read_repository()
    assert result == {"http_status": 403, "availability": "rate_limited"}

    async with GitHubReadConnector(GitHubConfig(token="secret", owner="a", repo="b"),
                                   httpx.AsyncClient(transport=httpx.MockTransport(
                                       lambda _: httpx.Response(401)))) as connector:
        denied = await connector.read_repository()
    assert denied == {"http_status": 401, "availability": "permission_denied"}


@pytest.mark.asyncio
async def test_github_does_not_follow_redirects_or_return_provider_error_body():
    async def redirect(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.invalid/collect"},
                              json={"message": "token-sensitive-error"})

    async with GitHubReadConnector(GitHubConfig(token="secret", owner="a", repo="b"),
                                   httpx.AsyncClient(transport=httpx.MockTransport(redirect))) as connector:
        result = await connector.read_repository()
    assert result["availability"] == "request_rejected"
    assert "token-sensitive-error" not in str(result)


@pytest.mark.asyncio
async def test_github_issue_page_is_fixed_host_bounded_and_detects_next_page():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.github.com"
        assert request.url.path == "/repos/acme/service/issues"
        assert request.url.params["state"] == "all"
        assert request.url.params["sort"] == "updated"
        assert request.url.params["direction"] == "asc"
        assert request.url.params["since"] == "2026-09-01T00:00:00Z"
        assert request.url.params["page"] == "2"
        assert request.url.params["per_page"] == "2"
        return httpx.Response(200, headers={
            "Link": '<https://api.github.com/repos/acme/service/issues?per_page=2&page=3>; rel="next"',
        }, json=[{"number": 3, "updated_at": "2026-09-02T00:00:00Z"}])

    cfg = GitHubConfig(token="ghp-test-only", owner="acme", repo="service")
    async with GitHubReadConnector(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))) as connector:
        result = await connector.read_issues(updated_since="2026-09-01T00:00:00Z", page=2, per_page=2)
    assert len(seen) == 1
    assert result["has_next"] is True
    assert result["rows"] == [{"number": 3, "updated_at": "2026-09-02T00:00:00Z"}]
