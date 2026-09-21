"""Entry point: ``python -m creeparr``."""

from __future__ import annotations

import uvicorn

from creeparr.config import get_env_config


def main() -> None:
    env = get_env_config()
    uvicorn.run(
        "creeparr.app:create_app",
        factory=True,
        host=env.host,
        port=env.port,
        log_level=env.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
