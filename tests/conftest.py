"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from .fake_agent import FakeAgent, start_agent, switch_table


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def allow_local_udp(socket_enabled):  # noqa: ANN001
    """The fake agent is a real UDP socket on 127.0.0.1."""
    return


@pytest.fixture
async def agent() -> AsyncIterator[tuple[FakeAgent, int]]:
    fake = FakeAgent(switch_table())
    transport, port = await start_agent(fake)
    try:
        yield fake, port
    finally:
        transport.close()
