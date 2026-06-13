"""Runnable example: verify a deployment claim with evidence.

Run it::

    python examples/verify_deployment.py https://example.com --health /healthz

It prints a JSON audit report and exits 0 only if the deployment is SHIPPED.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from shipped_or_not import (
    DeploymentStatus,
    DeploymentVerifier,
    InMemoryAuditStore,
    RetryPolicy,
)
from truststack.events import EventBus, TrustEvent


async def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a deployment is truly live.")
    parser.add_argument("url", nargs="?", default="https://example.com")
    parser.add_argument("--health", dest="health_path", default=None)
    args = parser.parse_args()

    # Subscribe to the audit side-channel so failed claims are observable.
    bus = EventBus()

    async def on_event(event: TrustEvent) -> None:
        print(f"[event] {event.name} for {event.data['url']}")

    bus.subscribe("*", on_event)

    # Persist every verdict so the claim can be replayed later as evidence.
    audit = InMemoryAuditStore()

    verifier = DeploymentVerifier(
        retry=RetryPolicy(attempts=3, backoff_seconds=0.5, max_backoff=8.0, jitter=0.1),
        timeout=10.0,
        event_bus=bus,
        audit_store=audit,
    )

    result = await verifier.verify(args.url, health_path=args.health_path)

    print(json.dumps(result.to_report(), indent=2))

    snapshot = await verifier.metrics()
    print(f"[metrics] {snapshot.counters}")

    history = await verifier.history(args.url)
    print(f"[audit] recorded {len(history)} result(s) for {args.url}")

    return 0 if result.status is DeploymentStatus.SHIPPED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
