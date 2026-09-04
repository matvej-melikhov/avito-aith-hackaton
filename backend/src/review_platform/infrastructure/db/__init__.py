"""SQLAlchemy persistence for tenant-owned review-platform state."""

from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
    SqlRevisionStore,
    SqlTransactionManager,
)
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
    test_transaction,
)

__all__ = [
    "AsyncSessionFactory",
    "Base",
    "SqlAppendOnlyAuditRepository",
    "SqlIdempotencyReceiptRepository",
    "SqlRevisionStore",
    "SqlTransactionManager",
    "create_database_engine",
    "create_session_factory",
    "session_scope",
    "test_transaction",
]
