"""TCP proxy primitives: read_exactly + forward_and_tap + handshake replay."""

import asyncio

import pytest

from rdc_proxy import proxy as proxy_mod
from rdc_proxy import state as state_mod
from rdc_proxy.state import HANDSHAKE


class FakeReader:
    def __init__(self, data=b""):
        self.data = data
        self.pos = 0

    async def read(self, n):
        if self.pos >= len(self.data):
            return b""
        chunk = self.data[self.pos: self.pos + n]
        self.pos += len(chunk)
        return chunk


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()
        self.closed = False

    def write(self, data):
        self.buf.extend(data)

    async def drain(self):
        return

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return

    def get_extra_info(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_read_exactly_returns_full_bytes():
    r = FakeReader(b"hello world")
    got = await proxy_mod.read_exactly(r, 11, timeout=1)
    assert got == b"hello world"


@pytest.mark.asyncio
async def test_read_exactly_returns_none_on_eof():
    r = FakeReader(b"hi")
    got = await proxy_mod.read_exactly(r, 10, timeout=1)
    assert got is None


@pytest.mark.asyncio
async def test_forward_and_tap_calls_tap_and_forwards():
    src = FakeReader(b"ABCDE")
    dst = FakeWriter()
    taps = []
    await proxy_mod.forward_and_tap(src, dst, taps.append, label="t")
    assert bytes(dst.buf) == b"ABCDE"
    assert b"".join(taps) == b"ABCDE"


@pytest.mark.asyncio
async def test_local_mode_replays_handshake(reset_handshake, monkeypatch):
    """LOCAL mode sends cloud greeting, waits for 576-byte RDC response,
    then sends config_msg. Telemetry after is ingested."""
    HANDSHAKE["cloud_greeting"] = b"G" * 576
    HANDSHAKE["rdc_response"] = b"R" * 576
    HANDSHAKE["config_msg"] = b"C" * 36

    rdc_response_bytes = b"R" * 576
    telemetry_bytes = b"T" * 32  # not a valid TLV — ingest will parse 0 records, that's fine
    rdc_reader = FakeReader(rdc_response_bytes + telemetry_bytes)
    rdc_writer = FakeWriter()

    # Prevent the loop from trying to upgrade to PROXY mid-test
    state_mod.STATE.internet_up = False

    await proxy_mod.local_mode(rdc_reader, rdc_writer)

    # Writer received: greeting + config_msg
    assert bytes(rdc_writer.buf) == (b"G" * 576) + (b"C" * 36)
    # Mode was set
    assert state_mod.STATE.proxy_mode == "local"


@pytest.mark.asyncio
async def test_check_internet_offline_returns_false(monkeypatch):
    """check_internet should return False when socket.create_connection fails."""
    import socket

    def fake_create_connection(*a, **kw):
        raise OSError("nope")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    assert proxy_mod.check_internet() is False


@pytest.mark.asyncio
async def test_resolve_cloud_returns_empty_on_gaierror(monkeypatch):
    import socket

    def raise_it(*a, **kw):
        raise socket.gaierror("no resolve")

    monkeypatch.setattr(socket, "getaddrinfo", raise_it)
    # Ensure CFG has the expected keys
    from rdc_proxy.config import CFG
    CFG.setdefault("cloud_dns", "devices.kohler.com")
    CFG.setdefault("cloud_port", 5253)
    assert proxy_mod.resolve_cloud() == []
