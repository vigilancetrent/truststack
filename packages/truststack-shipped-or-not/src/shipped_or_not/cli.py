"""Command-line interface for shipped-or-not.

Usage::

    shipped-or-not verify https://example.com [--health /healthz] [--json]

Exits ``0`` when the deployment is verifiably SHIPPED, ``1`` otherwise — making
it a drop-in trust gate for CI pipelines and agent tool calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from shipped_or_not.models import DeploymentStatus, VerificationResult
from shipped_or_not.verifier import DeploymentVerifier


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shipped-or-not",
        description="Verify that a deployment is genuinely live and healthy.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="verify a deployment claim for a URL")
    verify.add_argument("url", help="the deployed URL to verify")
    verify.add_argument(
        "--health",
        dest="health_path",
        default=None,
        help="health endpoint path that must return 200 (e.g. /healthz)",
    )
    verify.add_argument(
        "--expect-status",
        dest="expect_status",
        type=int,
        default=200,
        help="expected HTTP status for the root URL (default: 200)",
    )
    verify.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="per-request timeout in seconds (default: 10.0)",
    )
    verify.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit the full audit report as JSON",
    )
    return parser


def _render(result: VerificationResult, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(result.to_report(), indent=2)
    lines = [
        f"status:        {result.status.value.upper()}",
        f"url:           {result.url}",
        f"response_code: {result.response_code}",
        f"ssl_valid:     {result.ssl_valid}",
        f"health_passed: {result.health_passed}",
    ]
    for check in result.checks:
        mark = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{mark}] {check.name}: {check.detail}")
    if result.detail:
        lines.append(f"detail:        {result.detail}")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> VerificationResult:
    verifier = DeploymentVerifier(timeout=args.timeout)
    return await verifier.verify(
        args.url,
        health_path=args.health_path,
        expect_status=args.expect_status,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns ``0`` if SHIPPED, ``1`` otherwise."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    result = asyncio.run(_run(args))
    print(_render(result, as_json=args.as_json))
    return 0 if result.status is DeploymentStatus.SHIPPED else 1


if __name__ == "__main__":
    sys.exit(main())
