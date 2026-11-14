"""`repwise-web api`, `repwise-web worker`, `repwise-web migrate`.

One image, started three ways.
"""

import argparse
import logging
import os

import uvicorn

from repwise_web import migrations, worker
from repwise_web.api import create_app
from repwise_web.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repwise-web")
    parser.add_argument("command", choices=["api", "worker", "migrate"])
    command = parser.parse_args(argv).command

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings.from_env()
    if command == "migrate":
        migrations.upgrade(settings.database_url)
    elif command == "worker":
        worker.main(settings)
    else:
        uvicorn.run(
            create_app(settings),
            # Localhost unless told otherwise: oauth2-proxy beside this in the
            # pod is the only way in, and what it says about who is signed in
            # is believed.
            host=os.environ.get("LISTEN_HOST", "127.0.0.1"),
            port=8080,
        )
    return 0
