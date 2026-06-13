"""Command-line interface for the Meta token vault.

Operates against a local SQLite store::

    meta-token-vault list <app_id>
    meta-token-vault get <app_id>
    meta-token-vault rotate <app_id>

The default database path is ``./meta_token_vault.db``; override with ``--db``.
Rotation requires a refresher and is therefore not wired to Meta's API here -- it
reports that no refresher is configured rather than calling out to the network.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from .models import Token
from .stores import SqliteTokenStore
from .vault import Vault


def _format_token(token: Token) -> str:
    expires = token.expires_at.isoformat() if token.expires_at else "never"
    scopes = ",".join(token.scopes) or "-"
    return (
        f"{token.id}  app={token.app_id}  scopes={scopes}  "
        f"issued={token.issued_at.isoformat()}  expires={expires}"
    )


async def _list(db: str, app_id: str) -> int:
    store = SqliteTokenStore(db)
    tokens = await store.all(app_id)
    if not tokens:
        print(f"No tokens for app_id {app_id!r}.")
        return 0
    for token in tokens:
        print(_format_token(token))
    return 0


async def _get(db: str, app_id: str) -> int:
    vault = Vault(store=SqliteTokenStore(db))
    try:
        token = await vault.get_active_token(app_id)
    except KeyError:
        print(f"No active token for app_id {app_id!r}.", file=sys.stderr)
        return 1
    print(_format_token(token))
    return 0


async def _rotate(db: str, app_id: str) -> int:
    vault = Vault(store=SqliteTokenStore(db))
    try:
        token = await vault.rotate(app_id)
    except RuntimeError as exc:
        print(f"Cannot rotate: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Rotated -> {_format_token(token)}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meta-token-vault",
        description="Manage Meta/WhatsApp tokens stored in a local SQLite vault.",
    )
    parser.add_argument(
        "--db",
        default="meta_token_vault.db",
        help="Path to the SQLite database (default: meta_token_vault.db).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List all tokens for an app.")
    p_list.add_argument("app_id")

    p_get = sub.add_parser("get", help="Show the active token for an app.")
    p_get.add_argument("app_id")

    p_rotate = sub.add_parser("rotate", help="Rotate the active token for an app.")
    p_rotate.add_argument("app_id")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``meta-token-vault`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        return asyncio.run(_list(args.db, args.app_id))
    if args.command == "get":
        return asyncio.run(_get(args.db, args.app_id))
    if args.command == "rotate":
        return asyncio.run(_rotate(args.db, args.app_id))
    parser.error(f"Unknown command: {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
