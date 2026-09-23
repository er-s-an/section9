"""Resolve non-secret provider credential references for the local product.

This first local broker only accepts explicit, provider-specific environment
references. The reference itself is persisted; secret values never enter API
responses, registry records, events, or exception text. Names under
``S9_OBSERVED_`` deliberately keep customer-source credentials separate from
Section9's own ``LANGFUSE_INIT_*`` telemetry credentials.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


_REFERENCE = re.compile(r"^env://(S9_OBSERVED_[A-Z0-9_]{1,64})$")


class CredentialUnavailable(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class LangfuseCredentials:
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    project_id: str = ""


@dataclass(frozen=True)
class GitHubCredentials:
    token: str = field(repr=False)


class ProductCredentialBroker:
    """Resolve only explicitly configured local references; never guess fallbacks."""

    def __init__(self, environ: dict[str, str] | None = None):
        self._environ = os.environ if environ is None else environ

    @staticmethod
    def _prefix(reference: str | None) -> str:
        match = _REFERENCE.fullmatch(reference or "")
        if not match:
            raise CredentialUnavailable("CREDENTIAL_REFERENCE_UNSUPPORTED",
                                        "凭据引用格式不受当前本地凭据代理支持")
        return match.group(1)

    def langfuse(self, reference: str | None) -> LangfuseCredentials:
        prefix = self._prefix(reference)
        public_key = self._environ.get(f"{prefix}_PUBLIC_KEY", "")
        secret_key = self._environ.get(f"{prefix}_SECRET_KEY", "")
        if not public_key or not secret_key:
            raise CredentialUnavailable("CREDENTIAL_NOT_CONFIGURED",
                                        "Langfuse 凭据引用存在，但本地进程未配置对应的公开密钥和私钥")
        return LangfuseCredentials(public_key=public_key, secret_key=secret_key,
                                   project_id=self._environ.get(f"{prefix}_PROJECT_ID", ""))

    def github(self, reference: str | None) -> GitHubCredentials:
        prefix = self._prefix(reference)
        token = self._environ.get(f"{prefix}_TOKEN", "")
        if not token:
            raise CredentialUnavailable("CREDENTIAL_NOT_CONFIGURED",
                                        "GitHub 凭据引用存在，但本地进程未配置对应的只读访问令牌")
        return GitHubCredentials(token=token)
