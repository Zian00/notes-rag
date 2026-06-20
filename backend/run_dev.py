"""Local development server launcher.

Why this exists instead of calling `uvicorn` directly:
the LangGraph Postgres checkpointer uses psycopg3, whose async mode REQUIRES a
SelectorEventLoop. On Windows, uvicorn installs a ProactorEventLoop (and overrides
any event-loop policy set beforehand), so a plain `uvicorn app.main:app` crashes at
startup with "Psycopg cannot use the 'ProactorEventLoop'". Here we hand uvicorn a
SelectorEventLoop ourselves (loop="none" + asyncio.run(loop_factory=...)).

On Linux/macOS the default loop already works with psycopg3, so we use the normal
uvicorn path WITH --reload for a better dev experience. (Windows runs WITHOUT reload:
uvicorn's reloader manages worker subprocesses itself and can't be handed our custom
loop. Restart manually after edits, or run the full stack via `make up`/Docker.)
"""

import asyncio
import selectors
import sys

from uvicorn import Config, Server

_HOST = "127.0.0.1"
_PORT = 8000
_APP = "app.main:app"


def main() -> None:
    if sys.platform == "win32":
        config = Config(_APP, host=_HOST, port=_PORT, log_level="info", loop="none")
        asyncio.run(
            Server(config).serve(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        # Non-Windows: default loop is selector-compatible; keep hot reload.
        import uvicorn

        uvicorn.run(_APP, host=_HOST, port=_PORT, log_level="info", reload=True)


if __name__ == "__main__":
    main()
