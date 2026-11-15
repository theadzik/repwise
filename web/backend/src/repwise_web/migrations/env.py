"""Run by Alembic. The connection is the one `upgrade()` already holds."""

from alembic import context

from repwise_web.db import Base

context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=Base.metadata,
)
with context.begin_transaction():
    context.run_migrations()
