"""One time migrations for caches written by older casambi-bt versions.

The casambi-bt library wipes the whole cache folder when its version file
does not match the version it expects. Upgrading from casambi-bt 0.3.2
(cache version 2) to 0.4.0b5 (cache version 4) therefore throws away the
reusable casambi cloud session and forces a fresh cloud login on the next
connection attempt.

In addition, 0.3.2 stored the session expiry as a naive datetime while the
pinned library compares it against an aware one, which raises a TypeError
when such a session is reused. Note that an old session can coexist with a
current version file because 0.3.2 never rewrote an existing ``.cachever``.

To keep the session across the upgrade, migrate it before the library
touches the cache. The unit type and key caches are pure caches pickled
with classes that changed in the library (e.g. the
``UnitControlType.UNKOWN`` rename), so they are dropped and refetched from
the cloud instead of being loaded into a crash.
"""

import contextlib
from datetime import UTC, datetime
import logging
from pathlib import Path
import pickle
from typing import Any, Final

from CasambiBt import CACHE_VERSION as LIBRARY_CACHE_VERSION

_LOGGER = logging.getLogger(__name__)

CACHE_VERSION_FILE: Final = ".cachever"
SESSION_CACHE_FILE: Final = "session.pck"
TYPES_CACHE_FILE: Final = "types.pck"
KEY_CACHE_FILE: Final = "keys.pck"
MIGRATION_MARKER: Final = ".migrated-v1"

# Cache version this migration produces. It must match the version of the
# installed casambi-bt, otherwise the migration is skipped.
TARGET_CACHE_VERSION: Final = 4


def _load_session(session_file: Path) -> Any | None:
    """Unpickle the session or drop the file if it cannot be read."""
    try:
        # The cache is written by the pinned casambi-bt library into our
        # own config dir, same trust level as the library loading it.
        return pickle.loads(session_file.read_bytes())  # noqa: S301
    except Exception:
        _LOGGER.warning(
            "Session cache %s is unreadable, dropping it.", session_file, exc_info=True
        )
        session_file.unlink(missing_ok=True)
        return None


def migrate_legacy_cache(cache_root: Path, uuid: str) -> None:
    """Make a cache written by an older casambi-bt version usable.

    Runs synchronously, call it via an executor job.
    """
    if LIBRARY_CACHE_VERSION != TARGET_CACHE_VERSION:
        # The migration writes the format of cache version 4. Marking a
        # cache with another version would make the library misread it.
        _LOGGER.debug(
            "Skipping the cache migration for cache version %i",
            LIBRARY_CACHE_VERSION,
        )
        return

    entry_dir = cache_root / uuid
    if not entry_dir.is_dir():
        return

    # Gate on the marker only: a legacy session may sit in a cache whose
    # version file already says current (see module docstring).
    if (entry_dir / MIGRATION_MARKER).exists():
        return

    stale_caches = False
    session_file = entry_dir / SESSION_CACHE_FILE
    if session_file.exists():
        session = _load_session(session_file)
        if session is None:
            stale_caches = True
        else:
            expires = getattr(session, "expires", None)
            if isinstance(expires, datetime) and expires.tzinfo is None:
                session.expires = expires.replace(tzinfo=UTC)
                session_file.write_bytes(pickle.dumps(session))
                stale_caches = True
                _LOGGER.info(
                    "Migrated legacy session cache for %s to aware expiry.", uuid
                )

    if stale_caches:
        # The cache was written by a library version whose pickled classes
        # changed. These files are pure caches and are refetched from the
        # cloud, so removing them is cheap while loading them may crash.
        for stale in (TYPES_CACHE_FILE, KEY_CACHE_FILE):
            (entry_dir / stale).unlink(missing_ok=True)

    (entry_dir / MIGRATION_MARKER).write_text("", encoding="ascii")

    version_file = cache_root / CACHE_VERSION_FILE
    version_text = ""
    with contextlib.suppress(OSError):
        version_text = version_file.read_text(encoding="ascii").strip()
    version = int(version_text) if version_text.isdigit() else 0
    if version < TARGET_CACHE_VERSION:
        version_file.write_text(str(TARGET_CACHE_VERSION), encoding="ascii")
        _LOGGER.info(
            "Upgraded casambi cache %s to version %i.", uuid, TARGET_CACHE_VERSION
        )


def restrict_cache_permissions(cache_root: Path, uuid: str) -> None:
    """Make the cache of a network accessible to the owner only.

    It holds the cloud session and the network keys. casambi-bt creates the
    cache root with mode 0700 but the per network directory with the
    default mode. Runs synchronously, call it via an executor job.
    """
    for path in (cache_root, cache_root / uuid):
        with contextlib.suppress(FileNotFoundError):
            path.chmod(0o700)
