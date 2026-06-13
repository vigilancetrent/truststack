"""truststack-shipped-or-not — verify deployment claims with evidence.

Agents often claim software is deployed when the deploy actually failed (the URL
returns 500, DNS fails, or the TLS certificate is invalid). This package turns a
claim into an evidence-backed :class:`~shipped_or_not.models.VerificationResult`.

Quickstart::

    import asyncio
    from shipped_or_not import DeploymentVerifier

    async def main() -> None:
        verifier = DeploymentVerifier()
        result = await verifier.verify("https://example.com", health_path="/healthz")
        print(result.status, result.to_report())

    asyncio.run(main())
"""

from __future__ import annotations

from shipped_or_not.audit import (
    AuditStore,
    InMemoryAuditStore,
    SqliteAuditStore,
)
from shipped_or_not.models import (
    CheckResult,
    DeploymentStatus,
    RetryPolicy,
    TlsInfo,
    VerificationResult,
)
from shipped_or_not.verifier import DeploymentVerifier, Notify, OnChange

__version__ = "0.1.0"

__all__ = [
    "AuditStore",
    "CheckResult",
    "DeploymentStatus",
    "DeploymentVerifier",
    "InMemoryAuditStore",
    "Notify",
    "OnChange",
    "RetryPolicy",
    "SqliteAuditStore",
    "TlsInfo",
    "VerificationResult",
    "__version__",
]
