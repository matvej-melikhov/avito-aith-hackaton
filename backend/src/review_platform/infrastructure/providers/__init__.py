"""External provider adapters."""

from .github_artifacts import GitHubArtifactProvider
from .google_docs_artifacts import GoogleDocsArtifactProvider
from .mocks import (
    FixtureAIEventSource,
    FixtureArtifactProvider,
    FixtureCourseImportProvider,
    FixtureDeliveryProvider,
    FixtureEmailProvider,
    FixtureIdentityProvider,
)

__all__ = [
    "FixtureAIEventSource",
    "FixtureArtifactProvider",
    "FixtureCourseImportProvider",
    "FixtureDeliveryProvider",
    "FixtureEmailProvider",
    "FixtureIdentityProvider",
    "GitHubArtifactProvider",
    "GoogleDocsArtifactProvider",
]
