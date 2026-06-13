"""Basic role-based access control for vault operations.

This is intentionally small: it maps each :class:`~meta_token_vault.models.Role`
to the set of :class:`~meta_token_vault.models.Action` values it may perform and
exposes :func:`check_permission`, which raises :class:`PermissionError` for
unauthorised actions. Applications that need richer policies (per-app scoping,
attribute-based rules) should layer their own checks on top.
"""

from __future__ import annotations

from .models import Action, Role

#: Which actions each role is permitted to perform.
_ROLE_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.ADMIN: frozenset(Action),
    Role.OPERATOR: frozenset(
        {Action.STORE, Action.GET, Action.ROTATE, Action.REFRESH, Action.EXPIRE}
    ),
    Role.VIEWER: frozenset({Action.GET}),
}


def is_allowed(role: Role, action: Action) -> bool:
    """Return ``True`` if ``role`` may perform ``action``."""
    return action in _ROLE_PERMISSIONS.get(role, frozenset())


def check_permission(role: Role, action: Action) -> None:
    """Raise :class:`PermissionError` unless ``role`` may perform ``action``."""
    if not is_allowed(role, action):
        raise PermissionError(f"Role {role.value!r} is not permitted to perform {action.value!r}")


__all__ = ["check_permission", "is_allowed"]
