"""Versioned boundaries to artifact sources and the separately developed AI component."""

from dataclasses import dataclass
from typing import Literal, Protocol

from review_platform.contracts.workspace import (
    ReviewAssistEvent,
    ReviewAssistRequest,
    SelfReviewEvent,
    SelfReviewRequest,
)


@dataclass(frozen=True)
class PreparedBytes:
    content: bytes
    media_type: str
    filename: str
    source_kind: Literal["upload", "github", "google_docs"] = "upload"
    source_version: str = ""


class DefinitivePreparationFailure(Exception):
    def __init__(self, message: str, code: str = "invalid_artifact") -> None:
        super().__init__(message)
        self.code = code


class ArtifactPreparer(Protocol):
    async def prepare(self, artifact_url: str) -> PreparedBytes: ...


class StudentAIProvider(Protocol):
    async def submit(self, request: SelfReviewRequest) -> SelfReviewEvent | None: ...
    async def lookup(self, request: SelfReviewRequest) -> SelfReviewEvent | None: ...


class ReviewerAIProvider(Protocol):
    async def submit_assist(self, request: ReviewAssistRequest) -> ReviewAssistEvent | None: ...
    async def lookup_assist(self, request: ReviewAssistRequest) -> ReviewAssistEvent | None: ...
