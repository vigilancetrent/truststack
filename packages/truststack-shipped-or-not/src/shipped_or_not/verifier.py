"""The :class:`DeploymentVerifier` — proof, not promises, that software shipped.

Agents routinely claim a deploy succeeded when the URL actually returns a 500,
DNS does not resolve, or the TLS certificate is invalid. This component turns a
claim into an evidence-backed :class:`VerificationResult`. It *fails toward
distrust*: any error, timeout, or invalid certificate yields ``UNVERIFIED``.

Beyond a single verdict it can:

* persist every result to an :class:`~shipped_or_not.audit.AuditStore`;
* capture evidence — response headers, the final redirected URL, wall-clock
  ``elapsed_ms``, and (best effort) TLS certificate facts;
* :meth:`monitor` a URL on an interval, firing a callback only when the verdict
  *changes*, with an optional webhook/notify hook.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shipped_or_not.models import (
    CheckResult,
    DeploymentStatus,
    RetryPolicy,
    TlsInfo,
    VerificationResult,
)
from truststack.core import BaseTrustComponent, HealthState, HealthStatus
from truststack.logging import get_logger
from truststack.observability import traced

if TYPE_CHECKING:
    import httpx

    from shipped_or_not.audit import AuditStore
    from truststack.events import EventBus

log = get_logger("shipped_or_not", component="shipped-or-not")

_EVENT_SHIPPED = "deployment.shipped"
_EVENT_UNVERIFIED = "deployment.unverified"

# Response headers worth keeping in the audit trail (lower-cased on capture).
_EVIDENCE_HEADERS = (
    "server",
    "content-type",
    "content-length",
    "date",
    "cache-control",
    "x-powered-by",
    "via",
    "strict-transport-security",
    "location",
)

#: Callback invoked when :meth:`DeploymentVerifier.monitor` observes a verdict change.
OnChange = Callable[[VerificationResult], Awaitable[None]]
#: Optional webhook/notify callback invoked on a verdict change.
Notify = Callable[[VerificationResult], Awaitable[None]]


class DeploymentVerifier(BaseTrustComponent):
    """Verify that a deployment is genuinely reachable and healthy.

    A claim is only ``SHIPPED`` when *all* applicable checks pass:

    * DNS resolves and the host is reachable (no transport error);
    * the HTTP status equals ``expect_status``;
    * for ``https`` URLs, the TLS certificate is valid;
    * if ``health_path`` is given, the health endpoint returns ``200``.

    Anything else — including exceptions, timeouts, and invalid certificates —
    produces ``UNVERIFIED`` with a human-readable ``detail``.

    :param retry: backoff policy for transient transport errors.
    :param timeout: per-request timeout in seconds.
    :param event_bus: optional bus to publish ``deployment.*`` trust events on.
    :param audit_store: optional store; when set, every result is persisted.
    """

    component_name = "shipped-or-not"
    component_version = "0.1.0"

    def __init__(
        self,
        retry: RetryPolicy | None = None,
        timeout: float = 10.0,
        event_bus: EventBus | None = None,
        audit_store: AuditStore | None = None,
    ) -> None:
        super().__init__()
        self.retry = retry or RetryPolicy()
        self.timeout = timeout
        self._event_bus = event_bus
        self._audit_store = audit_store

    @traced("shipped_or_not.verify")
    async def verify(
        self,
        url: str,
        health_path: str | None = None,
        expect_status: int = 200,
    ) -> VerificationResult:
        """Verify a deployment claim for ``url`` and return evidence.

        :param url: the deployed URL the agent claims is live.
        :param health_path: optional path (e.g. ``/healthz``) that must return
            ``200`` for the deployment to count as shipped.
        :param expect_status: the HTTP status that signals a healthy root
            response (default ``200``).
        """
        self.registry.increment("verifications")
        log.info("verify_start", url=url, health_path=health_path, expect_status=expect_status)

        # Lazy import keeps the package importable without httpx at module load.
        import httpx

        is_https = url.lower().startswith("https://")
        checks: list[CheckResult] = []
        response_code: int | None = None
        ssl_valid: bool | None = True if is_https else None
        health_passed: bool | None = None
        detail: str | None = None
        final_url: str | None = None
        headers: dict[str, str] = {}
        tls: TlsInfo | None = None

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                verify=True,
                follow_redirects=True,
            ) as client:
                response = await self._request_with_retry(client, "GET", url)
                response_code = response.status_code
                final_url = str(response.url)
                headers = _evidence_headers(response)
                tls = _extract_tls(response)

                dns_ok = True
                checks.append(CheckResult(name="dns", passed=dns_ok, detail="host resolved"))
                if is_https:
                    checks.append(CheckResult(name="ssl", passed=True, detail="certificate valid"))

                status_ok = response.status_code == expect_status
                checks.append(
                    CheckResult(
                        name="http_status",
                        passed=status_ok,
                        detail=f"got {response.status_code}, expected {expect_status}",
                    )
                )

                if health_path is not None:
                    health_passed = await self._check_health_endpoint(
                        client, url, health_path, checks
                    )

        except httpx.ConnectError as exc:
            elapsed_ms = _elapsed_ms(started)
            # DNS failure / refused connection / TLS handshake failure.
            if _looks_like_ssl_error(exc) and is_https:
                ssl_valid = False
                checks.append(CheckResult(name="ssl", passed=False, detail="invalid certificate"))
                detail = f"SSL certificate invalid: {exc}"
            else:
                checks.append(CheckResult(name="dns", passed=False, detail=str(exc)))
                detail = f"connection failed: {exc}"
            return await self._finalize(
                url,
                DeploymentStatus.UNVERIFIED,
                response_code,
                ssl_valid,
                health_passed,
                checks,
                detail,
                final_url=final_url,
                elapsed_ms=elapsed_ms,
                headers=headers,
                tls=tls,
            )
        except httpx.HTTPError as exc:
            elapsed_ms = _elapsed_ms(started)
            detail = f"request failed: {exc}"
            checks.append(CheckResult(name="http", passed=False, detail=str(exc)))
            return await self._finalize(
                url,
                DeploymentStatus.UNVERIFIED,
                response_code,
                ssl_valid,
                health_passed,
                checks,
                detail,
                final_url=final_url,
                elapsed_ms=elapsed_ms,
                headers=headers,
                tls=tls,
            )
        except Exception as exc:
            elapsed_ms = _elapsed_ms(started)
            detail = f"unexpected error: {exc}"
            checks.append(CheckResult(name="error", passed=False, detail=str(exc)))
            return await self._finalize(
                url,
                DeploymentStatus.UNVERIFIED,
                response_code,
                ssl_valid,
                health_passed,
                checks,
                detail,
                final_url=final_url,
                elapsed_ms=elapsed_ms,
                headers=headers,
                tls=tls,
            )

        elapsed_ms = _elapsed_ms(started)
        all_passed = all(check.passed for check in checks)
        status = DeploymentStatus.SHIPPED if all_passed else DeploymentStatus.UNVERIFIED
        if not all_passed:
            failed = ", ".join(c.name for c in checks if not c.passed)
            detail = f"failed checks: {failed}"
        return await self._finalize(
            url,
            status,
            response_code,
            ssl_valid,
            health_passed,
            checks,
            detail,
            final_url=final_url,
            elapsed_ms=elapsed_ms,
            headers=headers,
            tls=tls,
        )

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
    ) -> httpx.Response:
        """Issue ``method url`` with exponential backoff on transport errors.

        Only transport-level failures (connect/timeout/transport) are retried;
        an HTTP response — even a 500 — is returned immediately, since that is a
        verifiable verdict rather than a transient fault.
        """
        import httpx

        last_exc: BaseException | None = None
        for attempt in range(1, self.retry.attempts + 1):
            delay = self.retry.delay_for(attempt)
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                return await client.request(method, url)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TransportError) as exc:
                last_exc = exc
                log.info("verify_retry", url=url, attempt=attempt, error=str(exc))
        assert last_exc is not None
        raise last_exc

    async def _check_health_endpoint(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        health_path: str,
        checks: list[CheckResult],
    ) -> bool:
        """Probe the health endpoint; record a check and return whether it passed."""
        import httpx

        health_url = _join_url(base_url, health_path)
        try:
            health_resp = await self._request_with_retry(client, "GET", health_url)
            passed = health_resp.status_code == 200
            checks.append(
                CheckResult(
                    name="health",
                    passed=passed,
                    detail=f"{health_url} -> {health_resp.status_code}",
                )
            )
            return passed
        except httpx.HTTPError as exc:
            checks.append(CheckResult(name="health", passed=False, detail=f"{health_url} -> {exc}"))
            return False

    async def _finalize(
        self,
        url: str,
        status: DeploymentStatus,
        response_code: int | None,
        ssl_valid: bool | None,
        health_passed: bool | None,
        checks: list[CheckResult],
        detail: str | None,
        *,
        final_url: str | None = None,
        elapsed_ms: float | None = None,
        headers: dict[str, str] | None = None,
        tls: TlsInfo | None = None,
    ) -> VerificationResult:
        """Assemble the result, record metrics, log, persist, and emit a TrustEvent."""
        result = VerificationResult(
            status=status,
            url=url,
            response_code=response_code,
            ssl_valid=ssl_valid,
            health_passed=health_passed,
            checks=list(checks),
            detail=detail,
            final_url=final_url,
            elapsed_ms=elapsed_ms,
            headers=dict(headers or {}),
            tls=tls,
        )

        if status is DeploymentStatus.SHIPPED:
            self.registry.increment("shipped")
            log.info("deployment_shipped", url=url, response_code=response_code)
            await self._emit(_EVENT_SHIPPED, result)
        else:
            self.registry.increment("unverified")
            log.warning("deployment_unverified", url=url, detail=detail)
            await self._emit(_EVENT_UNVERIFIED, result)

        await self._persist(result)
        return result

    async def _persist(self, result: VerificationResult) -> None:
        """Persist ``result`` to the audit store, if one is configured.

        A failing audit backend must never turn a real verdict into a lie, so any
        persistence error is logged and swallowed (the verdict still returns).
        """
        if self._audit_store is None:
            return
        try:
            await self._audit_store.record(result)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("audit_persist_failed", url=result.url, error=str(exc))

    async def history(self, url: str) -> list[VerificationResult]:
        """Return the recorded verification history for ``url`` (oldest first).

        Requires an ``audit_store``; raises :class:`RuntimeError` otherwise.
        """
        if self._audit_store is None:
            raise RuntimeError("history requires an audit_store to be configured")
        return await self._audit_store.history(url)

    async def monitor(
        self,
        url: str,
        interval: float,
        on_change: OnChange,
        *,
        iterations: int | None = None,
        health_path: str | None = None,
        expect_status: int = 200,
        notify: Notify | None = None,
    ) -> VerificationResult | None:
        """Re-verify ``url`` every ``interval`` seconds, firing on verdict change.

        ``on_change`` is awaited whenever the status differs from the previously
        observed status — including the very first observation. The optional
        ``notify`` callback (a webhook/alert hook) is awaited on the same edges,
        after ``on_change``.

        The loop runs ``iterations`` times when given, otherwise forever (until the
        surrounding task is cancelled). Cancellation is honoured cleanly:
        :class:`asyncio.CancelledError` is re-raised after the current sleep is
        interrupted. Returns the last :class:`VerificationResult`, or ``None`` if no
        iterations ran.

        :param interval: seconds to sleep *between* verifications.
        :param on_change: awaited with the new result on every status change.
        :param iterations: cap on the number of checks (``None`` = unbounded).
        :param notify: optional secondary callback for the same change edges.
        """
        previous: DeploymentStatus | None = None
        last: VerificationResult | None = None
        count = 0
        while iterations is None or count < iterations:
            result = await self.verify(url, health_path=health_path, expect_status=expect_status)
            last = result
            if result.status is not previous:
                log.info(
                    "monitor_change",
                    url=url,
                    previous=previous.value if previous is not None else None,
                    current=result.status.value,
                )
                await on_change(result)
                if notify is not None:
                    await self._safe_notify(notify, result)
                previous = result.status
            count += 1
            if iterations is not None and count >= iterations:
                break
            await asyncio.sleep(interval)
        return last

    async def _safe_notify(self, notify: Notify, result: VerificationResult) -> None:
        """Invoke the webhook/notify callback, never letting it break the monitor."""
        try:
            await notify(result)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("monitor_notify_failed", url=result.url, error=str(exc))

    async def _emit(self, name: str, result: VerificationResult) -> None:
        """Publish a :class:`TrustEvent` if an event bus was injected."""
        if self._event_bus is None:
            return
        from truststack.events import TrustEvent

        await self._event_bus.publish(
            TrustEvent(
                name=name,
                component=self.component_name,
                data=result.to_report(),
            )
        )

    async def _check_health(self) -> HealthStatus:
        """The verifier itself is healthy as long as it can be constructed."""
        return HealthStatus(component=self.component_name, state=HealthState.HEALTHY)


def _join_url(base_url: str, path: str) -> str:
    """Join a base URL and a health path, tolerating leading/trailing slashes."""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _looks_like_ssl_error(exc: BaseException) -> bool:
    """Heuristically decide whether a connect error stems from TLS validation."""
    text = f"{type(exc).__name__} {exc}".lower()
    needles = ("ssl", "certificate", "cert", "tls", "hostname mismatch")
    return any(needle in text for needle in needles)


def _elapsed_ms(started: float) -> float:
    """Milliseconds elapsed since ``started`` (a :func:`time.perf_counter` value)."""
    return round((time.perf_counter() - started) * 1000.0, 3)


def _evidence_headers(response: httpx.Response) -> dict[str, str]:
    """Capture the audit-relevant subset of response headers (lower-cased keys)."""
    captured: dict[str, str] = {}
    for key in _EVIDENCE_HEADERS:
        value = response.headers.get(key)
        if value is not None:
            captured[key] = value
    return captured


def _extract_tls(response: httpx.Response) -> TlsInfo | None:
    """Best-effort extraction of TLS cert facts from the live connection.

    httpx exposes the peer certificate (as a parsed dict, OpenSSL-style) on the
    network stream when TLS was negotiated. This is unavailable for ``http`` URLs
    and for mocked transports, in which case ``None`` is returned. Any parsing
    failure degrades gracefully to ``None`` rather than corrupting the verdict.
    """
    try:
        extensions = response.extensions
    except Exception:  # pragma: no cover - defensive
        return None

    network_stream = extensions.get("network_stream") if extensions else None
    if network_stream is None:
        return None

    try:
        ssl_object = network_stream.get_extra_info("ssl_object")
    except Exception:  # pragma: no cover - defensive
        return None
    if ssl_object is None:
        return None

    try:
        cert = ssl_object.getpeercert()
    except Exception:  # pragma: no cover - defensive
        return None
    if not cert:
        return None

    return _parse_peer_cert(cert)


def _parse_peer_cert(cert: dict[str, object]) -> TlsInfo:
    """Translate an :mod:`ssl` ``getpeercert()`` dict into a :class:`TlsInfo`."""
    issuer = _rdn_to_str(cert.get("issuer"))
    subject = _rdn_to_str(cert.get("subject"))
    not_after = _parse_cert_time(cert.get("notAfter"))
    not_before = _parse_cert_time(cert.get("notBefore"))
    return TlsInfo(
        issuer=issuer,
        subject=subject,
        not_after=not_after,
        not_before=not_before,
    )


def _rdn_to_str(rdn: object) -> str | None:
    """Flatten an ssl RDN sequence (tuple of tuples of (key, value)) to a string."""
    if not isinstance(rdn, (tuple, list)):
        return None
    parts: list[str] = []
    for relative in rdn:
        if not isinstance(relative, (tuple, list)):
            continue
        for pair in relative:
            if isinstance(pair, (tuple, list)) and len(pair) == 2:
                key, value = pair
                parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else None


def _parse_cert_time(value: object) -> datetime | None:
    """Parse an ssl cert timestamp (e.g. ``'Jun  1 12:00:00 2030 GMT'``)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%b %d %H:%M:%S %Y %Z")
    except ValueError:  # pragma: no cover - defensive
        return None
    return parsed.replace(tzinfo=UTC)


__all__ = ["DeploymentVerifier", "Notify", "OnChange"]
