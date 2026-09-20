"""Entry point: ``python -m patrearr``."""

from __future__ import annotations

import uvicorn

from patrearr.config import get_env_config


def main() -> None:
    env = get_env_config()
    uvicorn.run(
        "patrearr.app:create_app",
        factory=True,
        host=env.host,
        port=env.port,
        log_level=env.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
