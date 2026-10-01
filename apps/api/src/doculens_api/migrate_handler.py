"""AWS Lambda entrypoint that applies Alembic migrations (OQ-23).

Uses the API container image with ``image_config`` overriding the handler. Alembic
scripts are copied into the image at ``/opt/doculens/alembic``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config

from doculens.infrastructure.config import CoreSettings, load_settings

logger = logging.getLogger(__name__)

ALEMBIC_ROOT = Path("/opt/doculens/alembic")
ALEMBIC_INI = Path("/opt/doculens/alembic.ini")


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Run ``alembic upgrade head`` against ``DATABASE_URL`` from Secrets Manager."""
    del event, context
    settings = load_settings(CoreSettings, env_file=None)

    if not ALEMBIC_INI.is_file() or not ALEMBIC_ROOT.is_dir():
        message = (
            f"alembic assets missing (ini={ALEMBIC_INI.is_file()}, "
            f"scripts={ALEMBIC_ROOT.is_dir()}); rebuild the API image"
        )
        raise RuntimeError(message)

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_ROOT))
    config.set_main_option("doculens.database_url", settings.database_url.get_secret_value())
    alembic_upgrade(config, "head")
    logger.info("migrations applied to head")
    return {"status": "ok", "action": "upgrade", "revision": "head"}
