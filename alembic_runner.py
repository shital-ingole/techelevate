import os
from alembic.config import Config
from alembic import command


def run_migrations(message: str = "auto migration"):
    # Path to alembic.ini
    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))

    # 1. Generate new migration revision if needed
    command.revision(alembic_cfg, message=message, autogenerate=True)

    # 2. Apply migrations (upgrade to head)
    command.upgrade(alembic_cfg, "head")

    print("Database initialized and migrations applied successfully.")