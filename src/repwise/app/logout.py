"""Forget the Garmin session cached on this machine."""

import logging

from ..domain.models import GarminSettings
from ..errors import ExitCode
from ..garmin.client import forget

logger = logging.getLogger(__name__)


def run_logout(settings: GarminSettings) -> ExitCode:
    """Delete the cached tokens, so the next run has to log in again.

    Finding nothing to delete is not a failure. What the command promises is
    that afterwards this machine cannot reach the account without a password,
    and a token store that was already empty satisfies that as well as one this
    call emptied.

    No session is opened, and none is needed: the point is to stop being logged
    in, and asking Garmin's permission first would be an odd way to go about it.
    """
    deleted = forget(settings)
    if deleted is None:
        logger.info(f"No cached session in {settings.token_store}, so nothing to do.")
        return ExitCode.OK

    logger.info(f"Deleted {deleted}")
    logger.info("The next command that reaches Garmin will ask you to log in.")
    # Worth one line, because it is the part that surprises people: the file is
    # this machine's copy of the token, not the token itself.
    logger.info("This does not revoke the token at Garmin's end.")
    return ExitCode.OK
