"""The API's description, which the web app's types are generated from."""

import json
from pathlib import Path

from repwise_web.api import create_app
from repwise_web.settings import Settings

EXPORTED = Path(__file__).parents[2] / "frontend" / "openapi.json"


def test_the_exported_description_is_the_current_one(settings: Settings) -> None:
    """A route changed without exporting it again leaves the page's types stale.

    To bring it up to date, from web/frontend:

        (cd ../backend && uv run repwise-web openapi) > openapi.json
        npm run types
    """
    current = create_app(settings).openapi()
    assert json.loads(EXPORTED.read_text()) == json.loads(json.dumps(current))
