"""Desktop executable entry point for the bundled FastAPI service."""

from __future__ import annotations

import os

import uvicorn

from app.main import app


def main() -> None:
    host = os.getenv("WORKBENCH_HOST", "127.0.0.1")
    port = int(os.getenv("WORKBENCH_PORT", "8787"))
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
