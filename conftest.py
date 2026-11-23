"""Root conftest to make the repository root importable for tests."""

import socket

import pytest
import pytest_socket


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef: pytest.FixtureDef):  # type: ignore[valid-type]
    """Undo the global socket blocking that pytest-homeassistant-custom-component installs per test.

    The plugin blocks socket creation in its pytest_runtest_setup hook,
    which runs before fixtures are instantiated. On Windows the asyncio
    proactor event loop needs a local socketpair which is implemented with
    TCP sockets and would therefore be blocked. Restore the real socket
    class before any fixture (especially event_loop) is set up. The suite
    performs no real network I/O because all APIs are mocked.
    """
    socket.socket = pytest_socket._true_socket  # noqa: SLF001
    yield
