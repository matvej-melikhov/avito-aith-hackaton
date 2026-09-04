"""Transactional OAuth, magic-link, session read, and logout orchestration."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from jsonschema import ValidationError as JsonSchemaValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.ports.providers import IdentityProvider, ProviderPayload
from review_platform.contracts.registry import CONTRACT_VERSION, ContractRegistry
from review_platform.domain.primitives import require_utc, sha256_digest, utc_now, uuid7
from review_platform.infrastructure.db.models.identity import (
    ExternalIdentity,
    Invitation,
    OAuthState,
    OrganizationMembership,
    Session,
    User,
)
from review_platform.infrastructure.db.repositories.identity import (
    ExternalCredentialRepository,
    InvitationRepository,
    OAuthStateRepository,
    OrganizationMembershipRepository,
    SessionRepository,
    UserIdentityRepository,
)


class AuthenticationError(RuntimeError):
    """Base typed identity-protocol failure."""


class InvalidCredentialBinding(AuthenticationError):
    """The exact provider credential generation is missing or inactive."""


class InvalidOAuthState(AuthenticationError):
    """OAuth state is missing, expired, malformed, or bound to another provider."""


class ProtocolReplayRejected(AuthenticationError):
    """A one-time invitation/state protocol identity was already consumed."""


class InvalidIdentityAssertion(AuthenticationError):
    """The provider returned a malformed, stale, or mismatched assertion."""


class IdentityProviderRejected(AuthenticationError):
    """The provider returned a typed failure instead of an identity assertion."""


class IdentityEmailMismatch(AuthenticationError):
    """The provider-verified email differs from the invitation recipient."""


class IdentityNotAdmitted(AuthenticationError):
    """The exact identity has no active membership in the organization."""


class SessionNotActive(AuthenticationError):
    """The current session is missing, expired, revoked, or stale."""


@dataclass(frozen=True, slots=True)
class OAuthStartResult:
    organization_id: UUID
    state_id: UUID
    credential_binding_id: UUID
    credential_binding_version: int
    expires_at: datetime
    state_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class MagicLinkStateResult:
    organization_id: UUID
    state_id: UUID
    credential_binding_id: UUID
    credential_binding_version: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionAuthenticationResult:
    organization_id: UUID
    session_id: UUID
    user_id: UUID
    membership_id: UUID
    membership_revision: int
    auth_epoch: int
    roles: tuple[str, ...]
    expires_at: datetime
    replayed: bool
    session_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class SessionView:
    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    roles: tuple[str, ...]
    membership_revision: int
    auth_epoch: int
    actor_type: str = "user"
    agent_id: None = None


@dataclass(frozen=True, slots=True)
class LogoutResult:
    organization_id: UUID
    session_id: UUID | None
    revoked: bool


class AuthenticationService:
    """Execute identity protocols in the caller's open SQL transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        identity_provider: IdentityProvider,
        registry: ContractRegistry | None = None,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        state_secret_factory: Callable[[], str] | None = None,
        session_secret_factory: Callable[[], str] | None = None,
        oauth_state_ttl: timedelta = timedelta(minutes=10),
        session_ttl: timedelta = timedelta(hours=8),
    ) -> None:
        if oauth_state_ttl <= timedelta(0) or session_ttl <= timedelta(0):
            raise ValueError("authentication TTLs must be positive")
        self._session = session
        self._provider = identity_provider
        self._registry = registry or ContractRegistry()
        self._id_factory = id_factory
        self._clock = clock
        self._state_secret_factory = state_secret_factory or (
            lambda: secrets.token_urlsafe(48)
        )
        self._session_secret_factory = session_secret_factory or (
            lambda: secrets.token_urlsafe(48)
        )
        self._oauth_state_ttl = oauth_state_ttl
        self._session_ttl = session_ttl
        self._states = OAuthStateRepository(session)
        self._sessions = SessionRepository(session)
        self._memberships = OrganizationMembershipRepository(session)
        self._identities = UserIdentityRepository(session)
        self._invitations = InvitationRepository(session)
        self._credentials = ExternalCredentialRepository(session)

    async def start_stepik_oauth(
        self,
        *,
        organization_id: UUID,
        credential_binding_id: UUID,
        credential_binding_version: int,
        redirect_uri: str,
        pkce_verifier_ciphertext: str,
    ) -> OAuthStartResult:
        await self._require_credential(
            organization_id=organization_id,
            credential_binding_id=credential_binding_id,
            credential_binding_version=credential_binding_version,
            provider="stepik",
        )
        if not redirect_uri or not pkce_verifier_ciphertext:
            raise InvalidOAuthState("redirect URI and encrypted PKCE verifier are required")
        state_secret = self._new_secret(self._state_secret_factory, kind="OAuth state")
        now = self._now()
        state = OAuthState(
            state_id=self._id_factory(),
            organization_id=organization_id,
            state_digest=sha256_digest(state_secret),
            provider="stepik",
            credential_binding_id=credential_binding_id,
            credential_binding_version=credential_binding_version,
            redirect_uri=redirect_uri,
            pkce_verifier_ciphertext=pkce_verifier_ciphertext,
            expires_at=now + self._oauth_state_ttl,
            consumed_at=None,
            resulting_session_id=None,
        )
        await self._states.add(state)
        return OAuthStartResult(
            organization_id=organization_id,
            state_id=state.state_id,
            credential_binding_id=credential_binding_id,
            credential_binding_version=credential_binding_version,
            expires_at=state.expires_at,
            state_secret=state_secret,
        )

    async def complete_stepik_oauth(
        self,
        *,
        organization_id: UUID,
        state_secret: str,
        authorization_response: str,
    ) -> SessionAuthenticationResult:
        state_digest = sha256_digest(self._require_secret(state_secret, kind="OAuth state"))
        preliminary = await self._states.get_by_state_digest(organization_id, state_digest)
        if preliminary is None:
            raise InvalidOAuthState("OAuth state was not found")
        if preliminary.consumed_at is not None:
            return await self._oauth_replay(preliminary)
        self._validate_state(preliminary, provider="stepik", state_digest=state_digest)
        await self._require_credential_for_state(preliminary)
        assertion = await self._verify_provider(
            state=preliminary,
            authorization_response=authorization_response,
        )

        preliminary_state_id = preliminary.state_id
        self._session.expire(preliminary)
        state = await self._states.get(
            organization_id,
            preliminary_state_id,
            for_update=True,
        )
        if state is None:
            raise InvalidOAuthState("OAuth state disappeared before consumption")
        if state.consumed_at is not None:
            return await self._oauth_replay(state)
        self._validate_state(state, provider="stepik", state_digest=state_digest)
        await self._require_credential_for_state(state, for_update=True)
        self._validate_assertion(assertion, state=state, require_email=False)
        identity = await self._identities.get_external_identity(
            provider=cast(str, assertion["provider"]),
            issuer=cast(str, assertion["issuer"]),
            subject=cast(str, assertion["subject"]),
            for_update=True,
        )
        if identity is None or identity.status != "active":
            raise IdentityNotAdmitted("Stepik identity is not admitted")
        user = await self._identities.get_user(identity.user_id, for_update=True)
        if user is None or user.status != "active":
            raise IdentityNotAdmitted("Stepik user is not active")
        membership = await self._memberships.get_by_user(
            organization_id,
            user.id,
            for_update=True,
        )
        self._require_active_membership(membership)
        result = await self._create_session(
            organization_id=organization_id,
            user=user,
            membership=cast(OrganizationMembership, membership),
        )
        consumed = await self._states.consume_once(
            organization_id,
            state.state_id,
            state_digest=state_digest,
            consumed_at=self._now(),
            resulting_session_id=result.session_id,
        )
        if not consumed:
            raise ProtocolReplayRejected("OAuth state was concurrently consumed")
        return result

    async def issue_reviewer_magic_link_state(
        self,
        *,
        organization_id: UUID,
        credential_binding_id: UUID,
        credential_binding_version: int,
        redirect_uri: str,
        state_authority_ciphertext: str,
    ) -> MagicLinkStateResult:
        """Persist the server-owned state paired with an invitation token."""

        await self._require_credential(
            organization_id=organization_id,
            credential_binding_id=credential_binding_id,
            credential_binding_version=credential_binding_version,
            provider="email_magic_link",
        )
        if not redirect_uri or not state_authority_ciphertext:
            raise InvalidOAuthState("redirect URI and encrypted state authority are required")
        now = self._now()
        state_id = self._id_factory()
        state = OAuthState(
            state_id=state_id,
            organization_id=organization_id,
            state_digest=sha256_digest(str(state_id)),
            provider="email_magic_link",
            credential_binding_id=credential_binding_id,
            credential_binding_version=credential_binding_version,
            redirect_uri=redirect_uri,
            pkce_verifier_ciphertext=state_authority_ciphertext,
            expires_at=now + self._oauth_state_ttl,
            consumed_at=None,
            resulting_session_id=None,
        )
        await self._states.add(state)
        return MagicLinkStateResult(
            organization_id=organization_id,
            state_id=state_id,
            credential_binding_id=credential_binding_id,
            credential_binding_version=credential_binding_version,
            expires_at=state.expires_at,
        )

    async def consume_reviewer_magic_link(
        self,
        *,
        organization_id: UUID,
        invitation_token: str,
        state_id: UUID,
    ) -> SessionAuthenticationResult:
        token_digest = sha256_digest(
            self._require_secret(invitation_token, kind="invitation token")
        )
        preliminary_invitation = await self._invitations.get_by_token_digest(
            organization_id,
            token_digest,
        )
        preliminary_state = await self._states.get(organization_id, state_id)
        if preliminary_invitation is None or preliminary_state is None:
            raise ProtocolReplayRejected("invitation token or state is invalid")
        self._validate_invitation(preliminary_invitation, token_digest=token_digest)
        state_digest = sha256_digest(str(state_id))
        self._validate_state(
            preliminary_state,
            provider="email_magic_link",
            state_digest=state_digest,
        )
        await self._require_credential_for_state(preliminary_state)
        assertion = await self._verify_provider(
            state=preliminary_state,
            authorization_response=invitation_token,
        )

        preliminary_invitation_id = preliminary_invitation.id
        preliminary_state_id = preliminary_state.state_id
        self._session.expire(preliminary_invitation)
        self._session.expire(preliminary_state)
        invitation = await self._invitations.get_by_token_digest(
            organization_id,
            token_digest,
            for_update=True,
        )
        if invitation is None or invitation.id != preliminary_invitation_id:
            raise ProtocolReplayRejected("invitation token is invalid")
        self._validate_invitation(invitation, token_digest=token_digest)
        state = await self._states.get(organization_id, state_id, for_update=True)
        if state is None or state.state_id != preliminary_state_id:
            raise ProtocolReplayRejected("invitation state is invalid")
        self._validate_state(
            state,
            provider="email_magic_link",
            state_digest=state_digest,
        )
        await self._require_credential_for_state(state, for_update=True)
        self._validate_assertion(assertion, state=state, require_email=True)
        verified_email = _normalize_email(cast(str, assertion["verified_email"]))
        if verified_email != _normalize_email(invitation.normalized_email):
            raise IdentityEmailMismatch("verified email does not match invitation recipient")

        user, _ = await self._resolve_or_create_asserted_identity(assertion)
        membership = await self._grant_invited_role(
            invitation=invitation,
            user=user,
        )
        result = await self._create_session(
            organization_id=organization_id,
            user=user,
            membership=membership,
        )
        now = self._now()
        invitation_consumed = await self._invitations.consume_once(
            organization_id,
            invitation.id,
            token_digest=token_digest,
            expected_revision=invitation.revision,
            consumed_by=user.id,
            consumed_at=now,
        )
        state_consumed = await self._states.consume_once(
            organization_id,
            state.state_id,
            state_digest=state_digest,
            consumed_at=now,
            resulting_session_id=result.session_id,
        )
        if not invitation_consumed or not state_consumed:
            raise ProtocolReplayRejected("invitation or state was concurrently consumed")
        return result

    async def current_session(
        self,
        *,
        organization_id: UUID,
        session_secret: str,
    ) -> SessionView:
        digest = sha256_digest(self._require_secret(session_secret, kind="session"))
        session_row = await self._sessions.get_by_token_digest(
            organization_id,
            digest,
            for_update=True,
        )
        if session_row is None:
            raise SessionNotActive("session was not found")
        if session_row.status != "active" or session_row.revoked_at is not None:
            raise SessionNotActive("session is not active")
        if _database_utc(session_row.expires_at) <= self._now():
            session_row.status = "expired"
            raise SessionNotActive("session has expired")
        membership = await self._memberships.get(
            organization_id,
            session_row.membership_id,
            for_update=True,
        )
        self._require_active_membership(membership)
        current = cast(OrganizationMembership, membership)
        if (
            current.user_id != session_row.user_id
            or current.revision != session_row.membership_revision
            or current.auth_epoch != session_row.auth_epoch
        ):
            await self._sessions.revoke_once(
                organization_id,
                session_row.id,
                revoked_at=self._now(),
            )
            raise SessionNotActive("session authorization epoch is stale")
        return SessionView(
            user_id=session_row.user_id,
            organization_id=organization_id,
            membership_id=session_row.membership_id,
            roles=tuple(sorted(current.roles)),
            membership_revision=current.revision,
            auth_epoch=current.auth_epoch,
        )

    async def logout_current_session(
        self,
        *,
        organization_id: UUID,
        session_secret: str,
    ) -> LogoutResult:
        digest = sha256_digest(self._require_secret(session_secret, kind="session"))
        session_row = await self._sessions.get_by_token_digest(
            organization_id,
            digest,
            for_update=True,
        )
        if session_row is None:
            return LogoutResult(
                organization_id=organization_id,
                session_id=None,
                revoked=False,
            )
        revoked = await self._sessions.revoke_once(
            organization_id,
            session_row.id,
            revoked_at=self._now(),
        )
        return LogoutResult(
            organization_id=organization_id,
            session_id=session_row.id,
            revoked=revoked,
        )

    async def _verify_provider(
        self,
        *,
        state: OAuthState,
        authorization_response: str,
    ) -> ProviderPayload:
        if (
            self._provider.contract_version != CONTRACT_VERSION
            or self._provider.schema_name != "identity-provider.schema.json"
        ):
            raise InvalidIdentityAssertion("identity provider uses an unsupported contract")
        request: ProviderPayload = {
            "contract_version": CONTRACT_VERSION,
            "provider": state.provider,
            "state_id": str(state.state_id),
            "credential_binding_id": str(state.credential_binding_id),
            "credential_binding_version": state.credential_binding_version,
            "authorization_response": self._require_secret(
                authorization_response,
                kind="authorization response",
            ),
        }
        self._registry.validate(
            request,
            "identity-provider.schema.json",
            definition="verification_request",
        )
        response = await self._provider.verify_identity(request)
        try:
            self._registry.validate(
                response,
                "identity-provider.schema.json",
                definition="identity_assertion",
            )
        except JsonSchemaValidationError as assertion_error:
            try:
                self._registry.validate(
                    response,
                    "identity-provider.schema.json",
                    definition="failure",
                )
            except JsonSchemaValidationError as failure_error:
                raise InvalidIdentityAssertion(
                    "identity provider returned a malformed response"
                ) from failure_error
            error = response.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            raise IdentityProviderRejected(
                str(code) if isinstance(code, str) else "identity_provider_rejected"
            ) from assertion_error
        return response

    def _validate_assertion(
        self,
        assertion: ProviderPayload,
        *,
        state: OAuthState,
        require_email: bool,
    ) -> None:
        if assertion.get("provider") != state.provider:
            raise InvalidIdentityAssertion("assertion provider does not match state")
        if assertion.get("state_id") != str(state.state_id):
            raise InvalidIdentityAssertion("assertion state identity does not match")
        issued_at = _parse_datetime(assertion.get("issued_at"), field_name="issued_at")
        expires_at = _parse_datetime(assertion.get("expires_at"), field_name="expires_at")
        now = self._now()
        if issued_at > now or expires_at <= now or issued_at >= expires_at:
            raise InvalidIdentityAssertion("identity assertion is not currently valid")
        if require_email and not isinstance(assertion.get("verified_email"), str):
            raise InvalidIdentityAssertion("magic-link assertion requires verified email")

    def _validate_state(
        self,
        state: OAuthState,
        *,
        provider: str,
        state_digest: str,
    ) -> None:
        if state.provider != provider or state.state_digest != state_digest:
            raise InvalidOAuthState("state provider or digest does not match")
        if state.consumed_at is not None:
            raise ProtocolReplayRejected("state was already consumed")
        if _database_utc(state.expires_at) <= self._now():
            raise InvalidOAuthState("state has expired")

    def _validate_invitation(self, invitation: Invitation, *, token_digest: str) -> None:
        if invitation.token_digest != token_digest or invitation.status != "active":
            raise ProtocolReplayRejected("invitation is not active")
        if _database_utc(invitation.expires_at) <= self._now():
            raise ProtocolReplayRejected("invitation has expired")
        if invitation.role not in {"methodologist", "reviewer"}:
            raise ProtocolReplayRejected("invitation role is not allowed")

    async def _require_credential_for_state(
        self,
        state: OAuthState,
        *,
        for_update: bool = False,
    ) -> None:
        await self._require_credential(
            organization_id=state.organization_id,
            credential_binding_id=state.credential_binding_id,
            credential_binding_version=state.credential_binding_version,
            provider=state.provider,
            for_update=for_update,
        )

    async def _require_credential(
        self,
        *,
        organization_id: UUID,
        credential_binding_id: UUID,
        credential_binding_version: int,
        provider: str,
        for_update: bool = False,
    ) -> None:
        if credential_binding_version < 1:
            raise InvalidCredentialBinding("credential binding version must be positive")
        credential = await self._credentials.get_exact(
            organization_id,
            credential_binding_id,
            credential_binding_version,
            status="active",
            for_update=for_update,
        )
        if credential is None or credential.provider != provider:
            raise InvalidCredentialBinding("exact active credential binding was not found")

    async def _oauth_replay(self, state: OAuthState) -> SessionAuthenticationResult:
        if state.resulting_session_id is None:
            raise ProtocolReplayRejected("consumed OAuth state has no resulting session")
        session_row = await self._sessions.get(
            state.organization_id,
            state.resulting_session_id,
        )
        if session_row is None:
            raise ProtocolReplayRejected("OAuth replay session no longer exists")
        membership = await self._memberships.get(
            state.organization_id,
            session_row.membership_id,
        )
        self._require_active_membership(membership)
        return _session_result(
            session_row=session_row,
            membership=cast(OrganizationMembership, membership),
            replayed=True,
            session_secret=None,
        )

    async def _resolve_or_create_asserted_identity(
        self,
        assertion: ProviderPayload,
    ) -> tuple[User, ExternalIdentity]:
        provider = cast(str, assertion["provider"])
        issuer = cast(str, assertion["issuer"])
        subject = cast(str, assertion["subject"])
        verified = assertion.get("verified_email")
        verified_email = _normalize_email(verified) if isinstance(verified, str) else None
        identity = await self._identities.get_external_identity(
            provider=provider,
            issuer=issuer,
            subject=subject,
            for_update=True,
        )
        if identity is not None:
            if identity.status != "active":
                raise IdentityNotAdmitted("asserted identity is revoked")
            user = await self._identities.get_user(identity.user_id, for_update=True)
            if user is None or user.status != "active":
                raise IdentityNotAdmitted("asserted identity user is not active")
            identity.verified_email = verified_email
            return user, identity

        user = User(
            id=self._id_factory(),
            display_name=verified_email or subject,
            status="active",
        )
        await self._identities.add_user(user)
        identity = ExternalIdentity(
            id=self._id_factory(),
            user_id=user.id,
            provider=provider,
            issuer=issuer,
            subject=subject,
            verified_email=verified_email,
            status="active",
        )
        await self._identities.add_external_identity(identity)
        return user, identity

    async def _grant_invited_role(
        self,
        *,
        invitation: Invitation,
        user: User,
    ) -> OrganizationMembership:
        membership = await self._memberships.get_by_user(
            invitation.organization_id,
            user.id,
            for_update=True,
        )
        if membership is None:
            membership = OrganizationMembership(
                id=self._id_factory(),
                organization_id=invitation.organization_id,
                user_id=user.id,
                roles=[invitation.role],
                status="active",
                revision=0,
                auth_epoch=0,
            )
            return await self._memberships.add(membership)

        roles = set(membership.roles)
        roles.add(invitation.role)
        changed = (
            roles != set(membership.roles)
            or membership.status != "active"
            or membership.revoked_at is not None
        )
        membership.roles = sorted(roles)
        membership.status = "active"
        membership.revoked_at = None
        membership.revoked_by = None
        if changed:
            membership.revision += 1
            membership.auth_epoch += 1
        return membership

    async def _create_session(
        self,
        *,
        organization_id: UUID,
        user: User,
        membership: OrganizationMembership,
    ) -> SessionAuthenticationResult:
        secret = self._new_secret(self._session_secret_factory, kind="session")
        now = self._now()
        session_row = Session(
            id=self._id_factory(),
            organization_id=organization_id,
            user_id=user.id,
            membership_id=membership.id,
            membership_revision=membership.revision,
            auth_epoch=membership.auth_epoch,
            token_digest=sha256_digest(secret),
            expires_at=now + self._session_ttl,
            revoked_at=None,
            status="active",
        )
        await self._sessions.add(session_row)
        return _session_result(
            session_row=session_row,
            membership=membership,
            replayed=False,
            session_secret=secret,
        )

    @staticmethod
    def _require_active_membership(
        membership: OrganizationMembership | None,
    ) -> None:
        if membership is None or membership.status != "active" or not membership.roles:
            raise IdentityNotAdmitted("active organization membership was not found")

    @staticmethod
    def _require_secret(value: str, *, kind: str) -> str:
        if not value:
            raise AuthenticationError(f"{kind} is required")
        return value

    @staticmethod
    def _new_secret(factory: Callable[[], str], *, kind: str) -> str:
        value = factory()
        if len(value.encode("utf-8")) < 32:
            raise AuthenticationError(f"{kind} factory returned fewer than 32 bytes")
        return value

    def _now(self) -> datetime:
        return require_utc(self._clock())


def _normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized or "@" not in normalized:
        raise InvalidIdentityAssertion("verified email is invalid")
    return normalized


def _parse_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidIdentityAssertion(f"assertion {field_name} is not a date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidIdentityAssertion(
            f"assertion {field_name} is not a date-time"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidIdentityAssertion(f"assertion {field_name} is not timezone-aware")
    return parsed.astimezone(UTC)


def _database_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else require_utc(value)


def _session_result(
    *,
    session_row: Session,
    membership: OrganizationMembership,
    replayed: bool,
    session_secret: str | None,
) -> SessionAuthenticationResult:
    return SessionAuthenticationResult(
        organization_id=session_row.organization_id,
        session_id=session_row.id,
        user_id=session_row.user_id,
        membership_id=session_row.membership_id,
        membership_revision=session_row.membership_revision,
        auth_epoch=session_row.auth_epoch,
        roles=tuple(sorted(membership.roles)),
        expires_at=_database_utc(session_row.expires_at),
        replayed=replayed,
        session_secret=session_secret,
    )


__all__ = [
    "AuthenticationError",
    "AuthenticationService",
    "IdentityEmailMismatch",
    "IdentityNotAdmitted",
    "IdentityProviderRejected",
    "InvalidCredentialBinding",
    "InvalidIdentityAssertion",
    "InvalidOAuthState",
    "LogoutResult",
    "MagicLinkStateResult",
    "OAuthStartResult",
    "ProtocolReplayRejected",
    "SessionAuthenticationResult",
    "SessionNotActive",
    "SessionView",
]
