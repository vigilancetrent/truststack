"""The :class:`Vault` -- the standard token-management layer for Meta apps.

``Vault`` is a :class:`~truststack.core.BaseTrustComponent` that wraps a
:class:`~meta_token_vault.stores.TokenStore` and provides storage, expiry
monitoring, automatic refresh, rotation, an audit trail, RBAC, and a failure
alert hook (webhook-style callback). It never talks to Meta's real API directly:
refresh logic is supplied by the caller as a :data:`TokenRefresher`.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from threading import Lock

from truststack.core import BaseTrustComponent, HealthState, HealthStatus
from truststack.events import EventBus, TrustEvent
from truststack.logging import get_logger
from truststack.observability import traced

from .encryption import Encryptor, NoopEncryptor
from .models import Action, AuditEntry, Role, Token
from .rbac import check_permission
from .rotation import RotationPolicy
from .stores import InMemoryTokenStore, TokenStore

#: User-supplied refresh workflow: given an (about-to-expire) token, return a new one.
TokenRefresher = Callable[[Token], Awaitable[Token]]

#: Failure alert hook: invoked with the action label and the error. May be sync or
#: async -- async hooks are awaited, sync hooks are called directly.
AlertHook = Callable[[str, Exception], None] | Callable[[str, Exception], Awaitable[None]]

#: Callback invoked by :meth:`Vault.monitor` for each token detected as expiring.
ExpiringCallback = Callable[[Token], None] | Callable[[Token], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Vault(BaseTrustComponent):
    """Token vault for Meta/WhatsApp applications.

    :param store: persistence backend (defaults to :class:`InMemoryTokenStore`).
    :param encryptor: encryptor used to derive an at-rest fingerprint for logs and
        passed through for store-level encryption awareness (defaults to
        :class:`NoopEncryptor`, which is DEV ONLY).
    :param refresher: optional async callback that mints a fresh token from an
        expiring one. When unset, auto-refresh is disabled.
    :param refresh_threshold_seconds: if an active token expires within this many
        seconds, :meth:`get_active_token` proactively refreshes it.
    :param rotation_policy: optional :class:`RotationPolicy` driving
        :meth:`rotate_due`; tokens older than its ``max_age_seconds`` are rotated.
    :param event_bus: optional :class:`EventBus`; lifecycle events are published
        when present.
    :param alert_hook: optional callback invoked on operation failures or detected
        expiry. May be synchronous or asynchronous (a webhook coroutine).
    """

    component_name = "meta-token-vault"
    component_version = "0.1.0"

    def __init__(
        self,
        store: TokenStore | None = None,
        encryptor: Encryptor | None = None,
        refresher: TokenRefresher | None = None,
        refresh_threshold_seconds: int = 3600,
        rotation_policy: RotationPolicy | None = None,
        event_bus: EventBus | None = None,
        alert_hook: AlertHook | None = None,
    ) -> None:
        super().__init__()
        self._store: TokenStore = store or InMemoryTokenStore()
        self._encryptor: Encryptor = encryptor or NoopEncryptor()
        self._refresher = refresher
        self._refresh_threshold_seconds = refresh_threshold_seconds
        self._rotation_policy = rotation_policy
        self._event_bus = event_bus
        self._alert_hook = alert_hook
        self._audit: list[AuditEntry] = []
        self._audit_lock = Lock()
        self._log = get_logger(__name__, component=self.component_name)

    # -- internal helpers -------------------------------------------------

    def _record(self, action: Action, app_id: str, token_id: str | None, actor: str) -> AuditEntry:
        entry = AuditEntry(action=action, app_id=app_id, token_id=token_id, actor=actor)
        with self._audit_lock:
            self._audit.append(entry)
        self.registry.increment(f"audit.{action.value}")
        return entry

    async def _emit(self, name: str, app_id: str, token_id: str | None) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.publish(
            TrustEvent(
                name=name,
                component=self.component_name,
                data={"app_id": app_id, "token_id": token_id},
            )
        )

    async def _alert(self, action: str, exc: Exception) -> None:
        self.registry.increment("alerts.fired")
        if self._alert_hook is None:
            return
        try:
            result = self._alert_hook(action, exc)
            if inspect.isawaitable(result):
                await result
        except Exception as hook_exc:
            self._log.warning("alert_hook_failed", action=action, error=str(hook_exc))

    # -- public API -------------------------------------------------------

    @traced("vault.store")
    async def store(self, token: Token, *, role: Role = Role.ADMIN, actor: str = "system") -> None:
        """Persist ``token`` and append a ``store`` audit entry.

        Requires a role permitted to STORE (admin/operator).
        """
        check_permission(role, Action.STORE)
        try:
            await self._store.put(token)
        except Exception as exc:
            await self._alert("store", exc)
            self._log.error("token_store_failed", app_id=token.app_id, error=str(exc))
            raise
        self._record(Action.STORE, token.app_id, token.id, actor)
        self.registry.increment("tokens.stored")
        self._log.info("token_stored", app_id=token.app_id, token_id=token.id, actor=actor)

    @traced("vault.get_active_token")
    async def get_active_token(
        self, app_id: str, *, role: Role = Role.VIEWER, actor: str = "system"
    ) -> Token:
        """Return the active token for ``app_id``, auto-refreshing if near expiry.

        :raises PermissionError: if ``role`` may not GET.
        :raises KeyError: if no active (non-expired) token exists and refresh is
            unavailable.
        """
        check_permission(role, Action.GET)
        token = await self._store.get_active(app_id)
        self._record(Action.GET, app_id, token.id if token else None, actor)

        if token is None:
            self.registry.increment("tokens.missing")
            await self._emit("token.expired", app_id, None)
            raise KeyError(f"No active token for app_id {app_id!r}")

        if self._should_refresh(token):
            token = await self._auto_refresh(token, actor=actor)

        self.registry.increment("tokens.served")
        return token

    @traced("vault.rotate")
    async def rotate(
        self, app_id: str, *, role: Role = Role.OPERATOR, actor: str = "system"
    ) -> Token:
        """Force a refresh of the current active token and persist the result.

        Requires a refresher and a current token. Emits ``token.refreshed``.

        :raises PermissionError: if ``role`` may not ROTATE.
        :raises RuntimeError: if no refresher is configured.
        :raises KeyError: if there is no token to rotate.
        """
        check_permission(role, Action.ROTATE)
        if self._refresher is None:
            raise RuntimeError("Cannot rotate: no TokenRefresher configured")
        current = await self._store.get_active(app_id)
        if current is None:
            # fall back to most recent token of any expiry state
            existing = await self._store.all(app_id)
            current = existing[0] if existing else None
        if current is None:
            raise KeyError(f"No token to rotate for app_id {app_id!r}")
        rotated = await self._refresh_and_store(current, Action.ROTATE, actor=actor)
        self.registry.increment("tokens.rotated")
        return rotated

    @traced("vault.rotate_due")
    async def rotate_due(
        self,
        app_id: str,
        *,
        role: Role = Role.OPERATOR,
        actor: str = "system",
        now: datetime | None = None,
    ) -> Token | None:
        """Rotate the active token only if the rotation policy says it is due.

        Returns the rotated token, or ``None`` if no rotation was needed (no active
        token, or the token is younger than the policy's ``max_age_seconds``).

        :raises PermissionError: if ``role`` may not ROTATE.
        :raises RuntimeError: if no :class:`RotationPolicy` is configured.
        """
        check_permission(role, Action.ROTATE)
        if self._rotation_policy is None:
            raise RuntimeError("Cannot rotate_due: no RotationPolicy configured")
        current = await self._store.get_active(app_id)
        if current is None or not self._rotation_policy.is_due(current, now):
            return None
        return await self.rotate(app_id, role=role, actor=actor)

    async def audit_trail(self) -> list[AuditEntry]:
        """Return a snapshot copy of the audit trail (oldest first)."""
        with self._audit_lock:
            return list(self._audit)

    # -- expiry monitoring ------------------------------------------------

    @traced("vault.monitor")
    async def monitor(
        self,
        app_id: str,
        interval: float,
        on_expiring: ExpiringCallback | None = None,
        *,
        iterations: int | None = None,
        threshold_seconds: float | None = None,
        actor: str = "system",
    ) -> int:
        """Periodically poll for an expiring/expired active token.

        On each tick the active token is examined; if it is within
        ``threshold_seconds`` of expiry (or already expired/missing), ``on_expiring``
        is invoked (awaited if it is a coroutine), the configured alert hook fires,
        and a ``token.expired`` event is emitted. If a refresher is configured the
        token is also auto-refreshed.

        The loop runs forever by default and is cancellable via task cancellation;
        pass ``iterations`` to bound it (useful for tests). Returns the number of
        expiring detections observed.

        :param interval: seconds to sleep between polls.
        :param threshold_seconds: expiry window (defaults to
            ``refresh_threshold_seconds``).
        """
        window = (
            threshold_seconds
            if threshold_seconds is not None
            else float(self._refresh_threshold_seconds)
        )
        detections = 0
        remaining = iterations
        try:
            while remaining is None or remaining > 0:
                detected = await self._monitor_tick(app_id, window, on_expiring, actor=actor)
                if detected:
                    detections += 1
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        break
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            self._log.info("monitor_cancelled", app_id=app_id, detections=detections)
            raise
        return detections

    async def _monitor_tick(
        self,
        app_id: str,
        window: float,
        on_expiring: ExpiringCallback | None,
        *,
        actor: str,
    ) -> bool:
        token = await self._store.get_active(app_id)
        if token is None:
            await self._on_expiry_detected(app_id, None, on_expiring)
            return True
        remaining = token.expires_in_seconds()
        if remaining is not None and remaining <= window:
            await self._on_expiry_detected(app_id, token, on_expiring)
            if self._refresher is not None:
                await self._auto_refresh(token, actor=actor)
            return True
        return False

    async def _on_expiry_detected(
        self,
        app_id: str,
        token: Token | None,
        on_expiring: ExpiringCallback | None,
    ) -> None:
        self.registry.increment("tokens.expiring_detected")
        self._record(Action.EXPIRE, app_id, token.id if token else None, "monitor")
        await self._emit("token.expired", app_id, token.id if token else None)
        if token is not None:
            await self._alert("expire", TokenExpiringError(app_id, token))
        if on_expiring is None or token is None:
            return
        try:
            result = on_expiring(token)
            if inspect.isawaitable(result):
                await result
        except Exception as cb_exc:
            self._log.warning("on_expiring_failed", app_id=app_id, error=str(cb_exc))

    # -- refresh internals ------------------------------------------------

    def _should_refresh(self, token: Token, now: datetime | None = None) -> bool:
        if self._refresher is None:
            return False
        remaining = token.expires_in_seconds(now)
        if remaining is None:
            return False
        return remaining <= self._refresh_threshold_seconds

    async def _auto_refresh(self, token: Token, *, actor: str) -> Token:
        try:
            return await self._refresh_and_store(token, Action.REFRESH, actor=actor)
        except Exception as exc:
            await self._alert("refresh", exc)
            self._log.error("token_refresh_failed", app_id=token.app_id, error=str(exc))
            # Degrade gracefully: serve the existing token if it is still valid.
            if not token.is_expired():
                return token
            await self._emit("token.expired", token.app_id, token.id)
            raise

    async def _refresh_and_store(self, token: Token, action: Action, *, actor: str) -> Token:
        assert self._refresher is not None  # guarded by callers
        refreshed = await self._refresher(token)
        await self._store.put(refreshed)
        self._record(action, refreshed.app_id, refreshed.id, actor)
        await self._emit("token.refreshed", refreshed.app_id, refreshed.id)
        self.registry.increment("tokens.refreshed")
        self._log.info(
            "token_refreshed",
            app_id=refreshed.app_id,
            old_token_id=token.id,
            new_token_id=refreshed.id,
            action=action.value,
            actor=actor,
        )
        return refreshed

    # -- health -----------------------------------------------------------

    async def _check_health(self) -> HealthStatus:
        # Degraded (but usable) when running without encryption in a real deployment.
        if isinstance(self._encryptor, NoopEncryptor):
            return HealthStatus(
                component=self.component_name,
                state=HealthState.DEGRADED,
                detail="NoopEncryptor in use (DEV ONLY); secrets are not encrypted at rest.",
            )
        return HealthStatus(component=self.component_name, state=HealthState.HEALTHY)


class TokenExpiringError(Exception):
    """Raised internally to signal a detected expiry to the alert hook."""

    def __init__(self, app_id: str, token: Token) -> None:
        super().__init__(f"Token {token.id} for app {app_id!r} is expiring or expired")
        self.app_id = app_id
        self.token = token


__all__ = ["AlertHook", "ExpiringCallback", "TokenExpiringError", "TokenRefresher", "Vault"]
