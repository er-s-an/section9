"""Opt-in, durable, read-only polling for verified signal source bindings."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from types import SimpleNamespace
from typing import Any, Callable
import uuid

from s9.product.credentials import CredentialUnavailable, ProductCredentialBroker
from s9.product.registry import ProductError, ProductRegistry
from s9.product.v1_api import GitHubSignalImport, LangfuseSignalImport


_LOG = logging.getLogger(__name__)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SignalSyncScheduler:
    """Runs one bounded provider window at a time for explicitly enabled monitors.

    The import handlers remain the single normalization/idempotency path used by
    manual and scheduled reads. This worker calls them in-process with a local
    registry/credential context; no HTTP route is exposed for an agent to start it.
    """

    def __init__(
        self,
        registry: ProductRegistry,
        credentials: ProductCredentialBroker | None = None,
        *,
        worker_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        poll_seconds: float = 2.0,
    ):
        self.registry = registry
        self.credentials = credentials or ProductCredentialBroker()
        self.worker_id = worker_id or f"sync-worker-{uuid.uuid4().hex}"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()

    async def run_once(self) -> int:
        now = _timestamp(self.clock())
        due = self.registry.claim_due_signal_sync_monitors(
            self.worker_id, now=now, lease_seconds=180, limit=10,
        )
        for monitor in due:
            await self._run_monitor(monitor)
        return len(due)

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the local service available; details stay in logs
                _LOG.exception("signal sync scheduler iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def _run_monitor(self, monitor: dict[str, Any]) -> None:
        run_id = str(monitor["run_id"])
        started_at = _timestamp(self.clock())
        window = monitor.get("current_window") or {}
        from_time = str(window["from"])
        to_time = str(window["to"])
        cursor = window.get("cursor")
        scope = {
            "workspace_id": monitor["workspace_id"],
            "application_id": monitor["application_id"],
            "environment_id": monitor["environment_id"],
            "connection_id": monitor["connection_id"],
            "binding_id": monitor["binding_id"],
        }
        payload_cursor = str(cursor or "")
        idempotency_key = "signal-sync:" + hashlib.sha256(
            "|".join((monitor["id"], from_time, to_time, payload_cursor)).encode("utf-8")
        ).hexdigest()[:64]
        fake_request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(
                core=SimpleNamespace(product=SimpleNamespace(registry=self.registry)),
                product_credentials=self.credentials,
            )),
            state=SimpleNamespace(request_id=f"signal-sync-{run_id}"),
        )
        try:
            from s9.product import v1_api

            if monitor["provider"] == "langfuse":
                request_item = LangfuseSignalImport(
                    environment_id=monitor["environment_id"], from_start_time=from_time,
                    to_start_time=to_time, cursor=cursor, page_size=100, max_pages=5,
                )
                result = await v1_api.import_langfuse_signals(
                    scope["workspace_id"], scope["application_id"], scope["connection_id"],
                    scope["binding_id"], request_item, fake_request, idempotency_key,
                )
            elif monitor["provider"] == "github":
                request_item = GitHubSignalImport(
                    environment_id=monitor["environment_id"], from_updated_at=from_time,
                    to_updated_at=to_time, cursor=cursor, page_size=100, max_pages=5,
                )
                result = await v1_api.import_github_issue_signals(
                    scope["workspace_id"], scope["application_id"], scope["connection_id"],
                    scope["binding_id"], request_item, fake_request, idempotency_key,
                )
            else:
                raise ProductError("CONNECTOR_NOT_AVAILABLE", "该连接器没有自动信号适配器", 422)
            await self._finish_success(monitor, run_id, started_at, from_time, to_time, result)
        except asyncio.CancelledError:
            self._finish_failure(
                monitor, run_id, started_at, "SYNC_INTERRUPTED", retryable=True,
                message="本地服务停止时同步被中断；重启后会用同一窗口恢复",
            )
            raise
        except ProductError as exc:
            retryable = exc.status == 429 or exc.status >= 500
            self._finish_failure(monitor, run_id, started_at, exc.code, retryable=retryable,
                                 message=exc.message)
        except CredentialUnavailable as exc:
            self._finish_failure(monitor, run_id, started_at, exc.code, retryable=False,
                                 message=exc.message)
        except Exception as exc:  # noqa: BLE001 - persist only a stable code, not exception text
            _LOG.warning("signal sync failed (%s)", type(exc).__name__)
            self._finish_failure(monitor, run_id, started_at, "SYNC_WORKER_ERROR", retryable=True,
                                 message="同步未完成；不会把提供方响应正文写入运行记录")

    async def _finish_success(
        self, monitor: dict[str, Any], run_id: str, started_at: str,
        from_time: str, to_time: str, result: dict[str, Any],
    ) -> None:
        now_dt = self.clock().astimezone(timezone.utc)
        now = _timestamp(now_dt)
        coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
        cursor = coverage.get("next_cursor")
        next_window = ({"from": from_time, "to": to_time, "cursor": str(cursor)}
                       if cursor else None)
        complete = coverage.get("complete") is True
        coverage_error = coverage.get("error")
        coverage_gap = monitor.get("coverage_gap")
        blocked = coverage_error == "cursor_repeated"
        if next_window:
            state = "partial"
            watermark = None
            next_run_at = _timestamp(now_dt + timedelta(seconds=int(monitor["interval_seconds"])))
        else:
            state = "healthy" if complete and not coverage_gap else "partial"
            watermark = to_time
            next_run_at = (None if blocked else _timestamp(
                now_dt + timedelta(seconds=int(monitor["interval_seconds"]))))
        outcome = {
            "started_at": started_at,
            "created_count": int(result.get("created_count") or 0),
            "duplicate_count": int(result.get("duplicate_count") or 0),
            "auto_linked_count": int(result.get("auto_linked_count") or 0),
            "correlation_ambiguous_count": int(result.get("correlation_ambiguous_count") or 0),
            "coverage": {key: coverage.get(key) for key in (
                "from_start_time", "to_start_time", "complete", "continuation_required",
                "pages_read", "rows_seen", "invalid_rows", "error", "consistency",
            )},
        }
        if coverage_gap:
            outcome["coverage_gap"] = coverage_gap
        self.registry.finish_signal_sync_monitor_run(
            monitor["id"], self.worker_id, run_id, outcome=outcome,
            state="blocked" if blocked else state, next_run_at=next_run_at,
            current_window=None if blocked else next_window,
            last_successful_watermark=watermark,
            consecutive_failures=0, disable_monitor=blocked, now=now,
        )

    def _finish_failure(
        self, monitor: dict[str, Any], run_id: str, started_at: str,
        error_code: str, *, retryable: bool, message: str,
    ) -> None:
        now_dt = self.clock().astimezone(timezone.utc)
        now = _timestamp(now_dt)
        failures = int(monitor.get("consecutive_failures") or 0) + 1
        blocked = not retryable
        backoff = max(int(monitor["interval_seconds"]), min(3600, 15 * (2 ** min(failures - 1, 8))))
        next_run_at = None if blocked else _timestamp(now_dt + timedelta(seconds=backoff))
        window = monitor.get("current_window")
        outcome = {
            "started_at": started_at, "error_code": error_code,
            "retryable": retryable, "detail": message[:300],
            "window_from": window.get("from") if isinstance(window, dict) else None,
            "window_to": window.get("to") if isinstance(window, dict) else None,
        }
        self.registry.finish_signal_sync_monitor_run(
            monitor["id"], self.worker_id, run_id, outcome=outcome,
            state="blocked" if blocked else "degraded", next_run_at=next_run_at,
            current_window=window, last_successful_watermark=None,
            consecutive_failures=failures, disable_monitor=blocked, now=now,
        )
