"""Transport-independent application command dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import (
    AgentActor,
    ApplicationCommand,
    UserActor,
)


class CommandBusError(RuntimeError):
    """Base class for typed command-dispatch failures."""


class InvalidCommand(CommandBusError):
    """The application command violates the shared command envelope."""


class CommandNotRegistered(CommandBusError):
    """No application handler is registered for this exact command name."""


class DuplicateCommandRegistration(CommandBusError):
    """A command name already has an explicitly registered handler."""


class CommandTypeMismatch(CommandBusError):
    """A route or registration was bound to a different command variant."""


class CommandActorMismatch(CommandBusError):
    """The server-enriched command actor differs from authenticated context."""


class CommandTargetMismatch(CommandBusError):
    """The route target, revision target, or tenant does not match the command."""


class RevisionConflict(CommandBusError):
    """The expected target revision is no longer current."""


class TransactionContext(Protocol):
    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None: ...


class TransactionManager(Protocol):
    """Begin an atomic unit whose successful context exit commits."""

    def begin(self) -> TransactionContext: ...


class RevisionStore(Protocol):
    """Lock a tenant target and assert its exact expected revision."""

    async def lock_and_check(
        self,
        *,
        organization_id: UUID,
        revision_target: str,
        target_id: UUID,
        expected_revision: int,
        transaction: object,
    ) -> None: ...


CommandHandler = Callable[[ApplicationCommand, RequestActor, object], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class CommandRegistration:
    revision_target: str
    policy: AuthorizationPolicy
    handler: CommandHandler

    def __post_init__(self) -> None:
        if not self.revision_target:
            raise InvalidCommand("registration revision target is required")


class CommandBus:
    """Validate and dispatch commands in one transaction with final auth locking."""

    def __init__(
        self,
        *,
        transactions: TransactionManager,
        revisions: RevisionStore,
        authorizer: Authorizer,
        registrations: Mapping[str, CommandRegistration] | None = None,
    ) -> None:
        self._transactions = transactions
        self._revisions = revisions
        self._authorizer = authorizer
        self._registrations: dict[str, CommandRegistration] = {}
        for command_name, registration in (registrations or {}).items():
            self.register(command_name, registration)

    def register(self, command_name: str, registration: CommandRegistration) -> None:
        """Add one explicit handler without permitting duplicate replacement."""

        if not command_name:
            raise InvalidCommand("registered command name is required")
        if command_name in self._registrations:
            raise DuplicateCommandRegistration(
                f"command {command_name!r} already has a registered handler"
            )
        self._registrations[command_name] = registration

    async def dispatch(
        self,
        command: ApplicationCommand,
        *,
        actor: RequestActor,
        route_command: str | None = None,
        path_target_id: UUID | None = None,
    ) -> object:
        """Dispatch from REST, MCP, worker, or operator through the same core."""

        registration = self._validate_command(
            command,
            actor=actor,
            route_command=route_command,
            path_target_id=path_target_id,
        )
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=command.organization_id,
            policy=registration.policy,
        )

        async with self._transactions.begin() as transaction:
            await self._revisions.lock_and_check(
                organization_id=command.organization_id,
                revision_target=command.revision_target,
                target_id=command.target_id,
                expected_revision=command.expected_revision,
                transaction=transaction,
            )
            result = await registration.handler(command, actor, transaction)
            # This is intentionally the final awaited application action before
            # successful context exit commits the unit of work.
            await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
            return result

    def _validate_command(
        self,
        command: ApplicationCommand,
        *,
        actor: RequestActor,
        route_command: str | None,
        path_target_id: UUID | None,
    ) -> CommandRegistration:
        if type(command) is not ApplicationCommand:
            raise CommandTypeMismatch("dispatch requires the exact ApplicationCommand model")
        try:
            # Rebuild from plain data so model_construct() cannot bypass the
            # frozen actor/transport policy or exact command variant validators.
            validated = ApplicationCommand.model_validate(command.model_dump(mode="python"))
        except ValidationError as exc:
            raise InvalidCommand("application command failed strict validation") from exc
        if validated != command:
            raise InvalidCommand("application command is not canonically validated")
        self._validate_actor_binding(command, actor)

        if not isinstance(command.request_id, UUID) or not isinstance(command.target_id, UUID):
            raise InvalidCommand("request and target identities must be UUID values")
        if not isinstance(command.organization_id, UUID):
            raise InvalidCommand("organization identity must be a UUID value")
        if not 16 <= len(command.idempotency_key) <= 128:
            raise InvalidCommand("idempotency key must contain 16 to 128 characters")
        if (
            not isinstance(command.expected_revision, int)
            or isinstance(command.expected_revision, bool)
            or command.expected_revision < 0
        ):
            raise InvalidCommand("expected revision must be a non-negative integer")
        if command.organization_id != actor.organization_id:
            raise CommandTargetMismatch("command tenant does not match server-owned actor tenant")
        if route_command is not None and command.command_name != route_command:
            raise CommandTypeMismatch("route command does not match application command")
        if path_target_id is not None and command.target_id != path_target_id:
            raise CommandTargetMismatch("path target does not match command target")

        registration = self._registrations.get(command.command_name)
        if registration is None:
            raise CommandNotRegistered(f"command {command.command_name!r} is not registered")
        if command.revision_target != registration.revision_target:
            raise CommandTargetMismatch("command revision target does not match registration")
        return registration

    @staticmethod
    def _validate_actor_binding(command: ApplicationCommand, actor: RequestActor) -> None:
        command_actor = command.actor
        if isinstance(command_actor, UserActor):
            command_identity: tuple[object, ...] = (
                "user",
                command_actor.user_id,
                command_actor.membership_revision,
                command_actor.auth_epoch,
            )
        elif isinstance(command_actor, AgentActor):
            command_identity = (
                "agent",
                command_actor.user_id,
                command_actor.membership_revision,
                command_actor.auth_epoch,
                command_actor.agent_id,
                command_actor.agent_authorization_id,
            )
        else:
            command_identity = (
                "installation_operator",
                command_actor.installation_operator_id,
                command_actor.reason,
            )

        if actor.actor_type == "user":
            context_identity: tuple[object, ...] = (
                "user",
                actor.user_id,
                actor.membership_revision,
                actor.auth_epoch,
            )
        elif actor.actor_type == "agent":
            context_identity = (
                "agent",
                actor.user_id,
                actor.membership_revision,
                actor.auth_epoch,
                actor.agent_id,
                actor.agent_authorization_id,
            )
        else:
            context_identity = (
                "installation_operator",
                actor.installation_operator_id,
                actor.reason,
            )
        if command_identity != context_identity:
            raise CommandActorMismatch(
                "application command actor does not match authenticated server context"
            )
