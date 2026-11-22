"""Tests for the legacy cache migration."""

from datetime import UTC, datetime
import pickle

from CasambiBt._network import _NetworkSession
import pytest

from custom_components.casambi_bt.migration import (
    CACHE_VERSION_FILE,
    KEY_CACHE_FILE,
    MIGRATION_MARKER,
    SESSION_CACHE_FILE,
    TYPES_CACHE_FILE,
    migrate_legacy_cache,
)

from .conftest import TEST_UUID


def _make_legacy_cache(
    tmp_path, *, session_data=None, with_session=True, aware_session=False
):
    """Create a cache folder as written by casambi-bt 0.3.2."""
    cache_root = tmp_path / "casambi_bt"
    entry_dir = cache_root / TEST_UUID
    entry_dir.mkdir(parents=True)
    (cache_root / CACHE_VERSION_FILE).write_text("2", encoding="ascii")
    if with_session:
        if session_data is None:
            expires = datetime(2099, 1, 1)
            if aware_session:
                expires = expires.replace(tzinfo=UTC)
            session = _NetworkSession(
                session="abc.def.ghi",
                network="net",
                manager=False,
                keyID=1,
                expires=expires,
            )
            session_data = pickle.dumps(session)
        (entry_dir / SESSION_CACHE_FILE).write_bytes(session_data)
    (entry_dir / TYPES_CACHE_FILE).write_bytes(b"stale types")
    (entry_dir / KEY_CACHE_FILE).write_bytes(b"stale keys")
    return cache_root, entry_dir


def test_migrates_naive_session_and_bumps_version(tmp_path) -> None:
    """A 0.3.2 cache keeps its session and survives the version bump."""
    cache_root, entry_dir = _make_legacy_cache(tmp_path)

    migrate_legacy_cache(cache_root, TEST_UUID)

    session = pickle.loads((entry_dir / SESSION_CACHE_FILE).read_bytes())  # noqa: S301
    assert session.expires.tzinfo is not None
    assert not session.expired()
    assert not (entry_dir / TYPES_CACHE_FILE).exists()
    assert not (entry_dir / KEY_CACHE_FILE).exists()
    assert (entry_dir / MIGRATION_MARKER).exists()
    assert (cache_root / CACHE_VERSION_FILE).read_text(encoding="ascii") == "4"


def test_migration_is_idempotent(tmp_path) -> None:
    """A migrated cache is not touched again."""
    cache_root, entry_dir = _make_legacy_cache(tmp_path)
    migrate_legacy_cache(cache_root, TEST_UUID)

    (entry_dir / TYPES_CACHE_FILE).write_bytes(b"fresh types")
    migrate_legacy_cache(cache_root, TEST_UUID)

    assert (entry_dir / TYPES_CACHE_FILE).read_bytes() == b"fresh types"


def test_current_cache_is_left_alone(tmp_path) -> None:
    """A cache with an aware session is not stripped of its caches."""
    cache_root, entry_dir = _make_legacy_cache(tmp_path, aware_session=True)
    (cache_root / CACHE_VERSION_FILE).write_text("4", encoding="ascii")

    migrate_legacy_cache(cache_root, TEST_UUID)

    assert (entry_dir / TYPES_CACHE_FILE).exists()
    assert (entry_dir / KEY_CACHE_FILE).exists()
    # The marker is still written so the check runs only once.
    assert (entry_dir / MIGRATION_MARKER).exists()


def test_cachever4_with_naive_session_is_migrated(tmp_path) -> None:
    """A 0.3.2 session can coexist with a current version file.

    0.3.2 never rewrote an existing .cachever, so gating on the version
    file alone would miss exactly the state left behind by upgrading
    from the 0.2.2 integration.
    """
    cache_root, entry_dir = _make_legacy_cache(tmp_path)
    (cache_root / CACHE_VERSION_FILE).write_text("4", encoding="ascii")

    migrate_legacy_cache(cache_root, TEST_UUID)

    session = pickle.loads((entry_dir / SESSION_CACHE_FILE).read_bytes())  # noqa: S301
    assert session.expires.tzinfo is not None
    assert not session.expired()
    assert not (entry_dir / TYPES_CACHE_FILE).exists()
    assert not (entry_dir / KEY_CACHE_FILE).exists()


def test_unreadable_session_is_dropped(tmp_path) -> None:
    """A corrupt session pickle must not break the migration."""
    cache_root, entry_dir = _make_legacy_cache(tmp_path, session_data=b"garbage")

    migrate_legacy_cache(cache_root, TEST_UUID)

    assert not (entry_dir / SESSION_CACHE_FILE).exists()


def test_missing_entry_dir_is_noop(tmp_path) -> None:
    """Nothing is created for unknown networks."""
    cache_root = tmp_path / "casambi_bt"
    cache_root.mkdir()

    migrate_legacy_cache(cache_root, TEST_UUID)

    assert not (cache_root / TEST_UUID).exists()
    with pytest.raises(FileNotFoundError):
        (cache_root / CACHE_VERSION_FILE).read_text(encoding="ascii")
