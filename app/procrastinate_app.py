import os

from procrastinate import App, PsycopgConnector

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://deltacode:deltacode@localhost:5432/deltacode"
)

procrastinate_app = App(connector=PsycopgConnector(conninfo=DATABASE_URL))

# Importing tasks registers them on procrastinate_app; needed so the worker
# process (which only loads this module) knows about them.
from app.github_publishing import tasks as github_publishing_tasks  # noqa: E402,F401
from app.ingestion import tasks as ingestion_tasks  # noqa: E402,F401
from app.migration_generation import tasks as migration_generation_tasks  # noqa: E402,F401
from app.repository_intelligence import tasks as repository_intelligence_tasks  # noqa: E402,F401
from app.workspace_intelligence import tasks as workspace_intelligence_tasks  # noqa: E402,F401
