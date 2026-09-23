"""Safe, read-only checks for product connection and resource-scope bindings."""

from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from s9.connectors.langfuse import LangfuseConfig, LangfuseConnector
from s9.connectors.github import GitHubConfig, GitHubReadConnector
from s9.product.credentials import LangfuseCredentials
from s9.product.credentials import GitHubCredentials


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _base_url(connection: dict[str, Any]) -> str:
    value = str(connection.get("endpoint") or "https://cloud.langfuse.com").rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("连接地址必须是 HTTP(S) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("连接地址不能包含用户信息或片段")
    hostname = parsed.hostname.rstrip(".").lower()
    local_host = hostname == "localhost"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        local_host = address.is_loopback
        if not local_host and (not address.is_global or address.is_link_local or address.is_multicast):
            raise ValueError("连接地址只能指向公开服务或明确的本机服务")
    elif not local_host:
        # Until an operator-managed egress allowlist exists, only Langfuse
        # Cloud's documented origin is accepted for non-local DNS names.
        if hostname != "cloud.langfuse.com":
            raise ValueError("当前只允许 Langfuse Cloud 或明确的本机服务地址")
        try:
            resolved = {ipaddress.ip_address(item[4][0].split("%", 1)[0])
                        for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                                       type=socket.SOCK_STREAM)}
        except (OSError, ValueError):
            raise ValueError("连接域名无法安全解析，未尝试连接") from None
        if not resolved or any(not item.is_global or item.is_link_local or item.is_multicast for item in resolved):
            raise ValueError("连接域名解析到了非公开地址")
    if parsed.scheme == "http" and not local_host:
        raise ValueError("非本机 Langfuse 服务必须使用 HTTPS")
    return value


async def check_langfuse_binding(
    connection: dict[str, Any],
    binding: dict[str, Any],
    credentials: LangfuseCredentials,
    *,
    connector_factory=LangfuseConnector,
) -> dict[str, Any]:
    """Authenticate with a one-row, bounded read and expose no source payload."""
    base_url = _base_url(connection)
    target_project = str(binding["external_resource_id"])
    config = LangfuseConfig(
        base_url=base_url,
        public_key=credentials.public_key,
        secret_key=credentials.secret_key,
        project_id=credentials.project_id or target_project,
        timeout_seconds=15.0,
    )
    async with connector_factory(config) as connector:
        response = await connector.observations(limit=1)

    status_code = response.get("status_code")
    rows = response.get("rows", []) if isinstance(response.get("rows"), list) else []
    returned_projects = {str(row.get("project_id")) for row in rows if row.get("project_id")}
    credential_project_matches = bool(credentials.project_id and credentials.project_id == target_project)
    row_project_matches = bool(returned_projects and returned_projects == {target_project})
    row_project_conflicts = bool(returned_projects and target_project not in returned_projects)
    scope_confirmed = credential_project_matches or row_project_matches
    timestamp = _checked_at()

    if status_code in {401, 403}:
        connection_status = "permission_denied"
        binding_status = "permission_denied"
        outcome = "permission_denied"
    elif status_code == 200:
        connection_status = "connected"
        binding_status = "confirmed" if scope_confirmed and not row_project_conflicts else "pending"
        outcome = "data" if rows else "empty"
        if row_project_conflicts:
            outcome = "scope_mismatch"
    else:
        connection_status = "degraded"
        binding_status = "pending"
        outcome = str(response.get("availability") or "unavailable")

    coverage = response.get("coverage", {}) if isinstance(response.get("coverage"), dict) else {}
    return {
        "checked_at": timestamp,
        "outcome": outcome,
        "connection_status": connection_status,
        "binding_status": binding_status,
        "http_status": status_code if isinstance(status_code, int) else None,
        "observed_count": len(rows),
        "watermark": response.get("watermark"),
        "coverage": {"from_start_time": coverage.get("from_start_time"),
                     "to_start_time": coverage.get("to_start_time"),
                     "complete": coverage.get("complete", False)},
        "scope_confirmed": scope_confirmed and not row_project_conflicts,
        "detail": (
            "连接已认证，所选范围内有近期观察记录"
            if outcome == "data" and binding_status == "confirmed" else
            "连接已认证，但所选时间窗内没有观察记录"
            if outcome == "empty" and binding_status == "confirmed" else
            "连接已认证；请确认该 API key 对应的项目 ID，再确认资源范围"
            if outcome == "empty" else
            "连接已认证，但返回记录不属于登记的项目范围"
            if outcome == "scope_mismatch" else
            "凭据没有读取该 Langfuse 项目的权限"
            if outcome == "permission_denied" else
            "读取检查未取得成功响应；连接状态保持降级"
        ),
    }


async def read_langfuse_observations(
    connection: dict[str, Any],
    binding: dict[str, Any],
    credentials: LangfuseCredentials,
    *,
    from_start_time: str,
    to_start_time: str,
    cursor: str | None = None,
    limit: int = 100,
    connector_factory=LangfuseConnector,
) -> dict[str, Any]:
    """Read one bounded page after proving the credential is bound to its project."""
    target_project = str(binding["external_resource_id"])
    if not credentials.project_id or credentials.project_id != target_project:
        return {"http_status": None, "availability": "scope_mismatch", "rows": [],
                "coverage": {"complete": False, "next_cursor": None}}
    config = LangfuseConfig(
        base_url=_base_url(connection),
        public_key=credentials.public_key,
        secret_key=credentials.secret_key,
        project_id=target_project,
        timeout_seconds=20.0,
    )
    async with connector_factory(config) as connector:
        result = await connector.observations(
            from_start_time=from_start_time,
            to_start_time=to_start_time,
            cursor=cursor,
            limit=limit,
        )
    rows = result.get("rows", []) if isinstance(result.get("rows"), list) else []
    returned_projects = {str(row.get("project_id")) for row in rows if row.get("project_id")}
    if returned_projects and returned_projects != {target_project}:
        result = {**result, "availability": "scope_mismatch", "rows": [],
                  "coverage": {**(result.get("coverage") or {}), "complete": False, "next_cursor": None}}
    return result


async def check_github_binding(
    connection: dict[str, Any],
    binding: dict[str, Any],
    credentials: GitHubCredentials,
    *,
    connector_factory=GitHubReadConnector,
) -> dict[str, Any]:
    if connection.get("endpoint") not in {None, "", "https://api.github.com"}:
        raise ValueError("GitHub 连接固定使用 api.github.com，不允许自定义 API 地址")
    target = str(binding.get("external_resource_id") or "")
    parts = target.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("GitHub 仓库范围必须是 owner/repository")
    owner, repo = parts
    result: dict[str, Any]
    async with connector_factory(GitHubConfig(token=credentials.token, owner=owner, repo=repo)) as connector:
        result = await connector.read_repository()

    availability = result.get("availability")
    status_code = result.get("http_status")
    timestamp = _checked_at()
    scope_confirmed = result.get("full_name", "").casefold() == target.casefold()
    if availability in {"data", "empty"} and scope_confirmed:
        connection_status = "connected"
        binding_status = "confirmed"
        outcome = availability
    elif availability in {"permission_denied", "not_found_or_denied", "scope_mismatch"}:
        connection_status = "permission_denied"
        binding_status = "permission_denied"
        outcome = "permission_denied" if availability != "scope_mismatch" else "scope_mismatch"
    else:
        connection_status = "degraded"
        binding_status = "pending"
        outcome = str(availability or "unavailable")
    return {
        "checked_at": timestamp,
        "outcome": outcome,
        "connection_status": connection_status,
        "binding_status": binding_status,
        "http_status": status_code if isinstance(status_code, int) else None,
        "observed_count": int(result.get("files_count") or 0),
        "watermark": None,
        "source_version": result.get("commit_sha"),
        "coverage": {"from_start_time": None, "to_start_time": None,
                     "complete": result.get("coverage_complete") is True},
        "scope_confirmed": scope_confirmed and binding_status == "confirmed",
        "detail": (
            "GitHub 仓库已读取并绑定到默认分支提交"
            if outcome == "data" else
            "GitHub 仓库可访问但没有默认分支文件"
            if outcome == "empty" else
            "GitHub 拒绝读取该仓库；请核对仓库名和 Contents 只读权限"
            if outcome == "permission_denied" else
            "GitHub 读取检查未完成；连接保持降级，范围保持待验证"
        ),
    }


async def read_github_issues(
    connection: dict[str, Any],
    binding: dict[str, Any],
    credentials: GitHubCredentials,
    *,
    updated_since: str,
    page: int,
    per_page: int,
    connector_factory=GitHubReadConnector,
) -> dict[str, Any]:
    """Read a single issue/PR page only from a previously bound GitHub repository."""
    if connection.get("endpoint") not in {None, "", "https://api.github.com"}:
        raise ValueError("GitHub 连接固定使用 api.github.com，不允许自定义 API 地址")
    target = str(binding.get("external_resource_id") or "")
    parts = target.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("GitHub 仓库范围必须是 owner/repository")
    async with connector_factory(GitHubConfig(token=credentials.token, owner=parts[0], repo=parts[1])) as connector:
        return await connector.read_issues(updated_since=updated_since, page=page, per_page=per_page)
