"""truststack-meta-token-vault -- the standard token-management layer for Meta apps.

Every Meta/WhatsApp integration tends to rebuild token storage, rotation, expiry
monitoring, and auditing from scratch. This package provides that layer once, as
a Trust Stack component:

    >>> import asyncio
    >>> from datetime import UTC, datetime, timedelta
    >>> from meta_token_vault import Token, Vault
    >>> async def demo() -> str:
    ...     vault = Vault()
    ...     now = datetime.now(UTC)
    ...     await vault.store(
    ...         Token(value="EAAG...", app_id="123", expires_at=now + timedelta(days=60))
    ...     )
    ...     token = await vault.get_active_token("123")
    ...     return token.app_id
    >>> asyncio.run(demo())
    '123'

See the README for refresh, rotation, encryption, RBAC, and audit-trail usage.
"""

from __future__ import annotations

from .encryption import Encryptor, FernetEncryptor, NoopEncryptor
from .models import Action, AuditEntry, Role, Token
from .rbac import check_permission, is_allowed
from .rotation import RotationPolicy
from .stores import (
    AwsSecretsManagerTokenStore,
    AzureKeyVaultTokenStore,
    HashiCorpVaultTokenStore,
    InMemoryTokenStore,
    PostgresTokenStore,
    SqliteTokenStore,
    TokenStore,
)
from .vault import (
    AlertHook,
    ExpiringCallback,
    TokenExpiringError,
    TokenRefresher,
    Vault,
)

__version__ = "0.1.0"

__all__ = [
    "Action",
    "AlertHook",
    "AuditEntry",
    "AwsSecretsManagerTokenStore",
    "AzureKeyVaultTokenStore",
    "Encryptor",
    "ExpiringCallback",
    "FernetEncryptor",
    "HashiCorpVaultTokenStore",
    "InMemoryTokenStore",
    "NoopEncryptor",
    "PostgresTokenStore",
    "Role",
    "RotationPolicy",
    "SqliteTokenStore",
    "Token",
    "TokenExpiringError",
    "TokenRefresher",
    "TokenStore",
    "Vault",
    "__version__",
    "check_permission",
    "is_allowed",
]
