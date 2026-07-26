from procrastinate import App, PsycopgConnector

from app.core.config import get_settings


def build_app() -> App:
    settings = get_settings()
    # Reuses the same stripped (non-asyncpg) conninfo already used for the LangGraph
    # checkpointer (backend/app/core/config.py:78-80) — procrastinate's psycopg
    # connector takes a plain postgres:// DSN, not the asyncpg-prefixed SQLAlchemy one.
    # NOTE: PsycopgConnector (procrastinate 3.9.0) forwards unknown kwargs straight
    # to psycopg_pool.AsyncConnectionPool, whose first argument is `conninfo` — so
    # passing conninfo= here still works even though it's no longer a named param
    # on PsycopgConnector itself.
    connector = PsycopgConnector(conninfo=settings.checkpointer_conninfo)
    return App(connector=connector)


app = build_app()
