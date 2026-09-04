"""Local installation-operator recovery executable."""

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


class OperatorCLIError(RuntimeError):
    """The local recovery process is missing required authority/configuration."""


def build_recovery_command(
    *,
    organization_id: UUID,
    operator_id: str,
    operator_reason: str,
    provider: str,
    issuer: str,
    subject: str,
    recovery_reason: str,
    expected_revision: int,
    authority_key: str,
    request_id: UUID | None = None,
    trace_id: UUID | None = None,
) -> ApplicationCommand:
    request = request_id or uuid7()
    return ApplicationCommand.model_validate(
        {
            "request_id": request,
            "idempotency_key": authority_key,
            "command_name": "recover_methodologist",
            "revision_target": "organization",
            "target_id": organization_id,
            "expected_revision": expected_revision,
            "payload": {
                "organization_id": organization_id,
                "provider": provider,
                "issuer": issuer,
                "subject": subject,
                "reason": recovery_reason,
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


async def execute_recovery_command(
    command: ApplicationCommand,
    *,
    session_factory: AsyncSessionFactory,
) -> BootstrapResult:
    async with session_scope(session_factory) as session:
        return await BootstrapService(session).recover(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m review_platform.operator",
        description="Recover one exact methodologist identity from the local host.",
    )
    parser.add_argument("--organization-id", required=True, type=UUID)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--operator-reason", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--recovery-reason", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--authority-key", required=True)
    parser.add_argument("--request-id", type=UUID)
    parser.add_argument("--trace-id", type=UUID)
    return parser


async def _run(args: argparse.Namespace, settings: Settings) -> BootstrapResult:
    if settings.database_url is None:
        raise OperatorCLIError("REVIEW_PLATFORM_DATABASE_URL is required")
    command = build_recovery_command(
        organization_id=args.organization_id,
        operator_id=args.operator_id,
        operator_reason=args.operator_reason,
        provider=args.provider,
        issuer=args.issuer,
        subject=args.subject,
        recovery_reason=args.recovery_reason,
        expected_revision=args.expected_revision,
        authority_key=args.authority_key,
        request_id=args.request_id,
        trace_id=args.trace_id,
    )
    engine = create_database_engine(settings.database_url)
    try:
        return await execute_recovery_command(
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
    except (OperatorCLIError, BootstrapError, ValueError) as error:
        print(f"recovery rejected: {error}", file=sys.stderr)
        return 2
    print(_render(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - executable entrypoint
    raise SystemExit(main())


__all__ = [
    "OperatorCLIError",
    "build_parser",
    "build_recovery_command",
    "execute_recovery_command",
    "main",
]
