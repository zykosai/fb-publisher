from __future__ import annotations

from .db import Database


class PublicationPaused(RuntimeError):
    """Pause requested before Submit; the draft can safely be rebuilt."""


class OperationCancelled(RuntimeError):
    """A user cancelled a non-publication background operation."""


def check_running(db: Database) -> None:
    if db.get_setting("publication_paused", True):
        raise PublicationPaused("Публикация приостановлена")
