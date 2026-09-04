"""Local-only executable for one-time installation bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from review_platform.application.services.bootstrap import (
    BootstrapError,
    BootstrapResult,
    BootstrapService,
)
from review_platform.contracts.commands import ApplicationCommand
from review_platform.domain.primitives import uuid7
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.settings import Settings, get_settings


class BootstrapCLIError(RuntimeError):
    """The local operator process is missing required safe configuration."""


def build_bootstrap_command(
    *,
    organization_id: UUID,
    operator_id: str,
    operator_reason: str,
    provider: str,
    issuer: str,
    subject: str,
    expected_revision: int = 0,
    request_id: UUID | None = None,
    trace_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> ApplicationCommand:
    request = request_id or uuid7()
    return ApplicationCommand.model_validate(
        {
            "request_id": request,
            "idempotency_key": idempotency_key or f"bootstrap-{request}",
            "command_name": "activate_bootstrap",
            "revision_target": "organization",
            "target_id": organization_id,
            "expected_revision": expected_revision,
            "payload": {
                "external_identity": {
                    "provider": provider,
                    "issuer": issuer,
                    "subject": subject,
                }
            },
            "organization_id": organization_id,
            "actor": {
                "type": "installation_operator",
                "installation_operator_id": operator_id,
                "reason": operator_reason,
            },
            "transport": "operator",
            "trace_id": trace_id or uuid7(),
        }
    )


async def execute_bootstrap_command(
    command: ApplicationCommand,
    *,
    session_factory: AsyncSessionFactory,
) -> BootstrapResult:
    async with session_scope(session_factory) as session:
        return await BootstrapService(session).activate(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m review_platform.bootstrap",
        description="Activate the exact first methodologist identity from the local host.",
    )
    parser.add_argument("--organization-id", required=True, type=UUID)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--expected-revision", type=int, default=0)
    parser.add_argument("--request-id", type=UUID)
    parser.add_argument("--trace-id", type=UUID)
    parser.add_argument("--idempotency-key")
    return parser


async def _run(args: argparse.Namespace, settings: Settings) -> BootstrapResult:
    if settings.database_url is None:
        raise BootstrapCLIError("REVIEW_PLATFORM_DATABASE_URL is required")
    command = build_bootstrap_command(
        organization_id=args.organization_id,
        operator_id=args.operator_id,
        operator_reason=args.reason,
        provider=args.provider,
        issuer=args.issuer,
        subject=args.subject,
        expected_revision=args.expected_revision,
        request_id=args.request_id,
        trace_id=args.trace_id,
        idempotency_key=args.idempotency_key,
    )
    engine = create_database_engine(settings.database_url)
    try:
        return await execute_bootstrap_command(
            command,
            session_factory=create_session_factory(engine),
        )
    finally:
        await engine.dispose()


def _render(result: BootstrapResult) -> str:
    return json.dumps(
        {
            "organization_id": str(result.organization_id),
            "user_id": str(result.user_id),
            "external_identity_id": str(result.external_identity_id),
            "membership_id": str(result.membership_id),
            "roles": list(result.roles),
            "organization_revision": result.organization_revision,
            "replayed": result.replayed,
        },
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args, get_settings()))
    except (BootstrapCLIError, BootstrapError, ValueError) as error:
        print(f"bootstrap rejected: {error}", file=sys.stderr)
        return 2
    print(_render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - executable entrypoint
    raise SystemExit(main())


__all__ = [
    "BootstrapCLIError",
    "build_bootstrap_command",
    "build_parser",
    "execute_bootstrap_command",
    "main",
]
