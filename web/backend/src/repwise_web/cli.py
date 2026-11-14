"""`repwise-web api`, `repwise-web worker`, `repwise-web migrate`.

One image, started three ways. A fourth command, `openapi`, runs nowhere but
on a developer's machine: it prints the API's contract, which the web app's
types are generated from.
"""

import argparse
import json
import logging
import os

import uvicorn
from cryptography.fernet import Fernet

from repwise_web import migrations, worker
from repwise_web.api import create_app
from repwise_web.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repwise-web")
    parser.add_argument("command", choices=["api", "worker", "migrate", "openapi"])
    command = parser.parse_args(argv).command

    if command == "openapi":
        # Describing the API needs neither a database nor a key, only values
        # of the right shape. Nothing is connected to.
        nowhere = Settings(
            database_url="postgresql+psycopg://",
            token_keys=(Fernet.generate_key().decode(),),
        )
        print(json.dumps(create_app(nowhere).openapi(), indent=2, sort_keys=True))
        return 0

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
