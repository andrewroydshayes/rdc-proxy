"""GeneratorState ingest, snapshot, side channels, and handshake persistence."""

import json
import struct
from pathlib import Path

from rdc_proxy import state as state_mod
from rdc_proxy.state import GeneratorState, HANDSHAKE, handshake_path, load_handshake, save_handshake


def _build_frame_for_rpm(rpm):
    body = struct.pack("<HHI", 0x044C, 0x00C0, 4) + struct.pack("<i", rpm) + struct.pack("<H", 0)
    return struct.pack("<IHHI", 12 + len(body), 2, 1, 0) + body


def test_fresh_state_defaults(fresh_state):
    assert fresh_state.proxy_mode == "startup"
    assert fresh_state.rdc_connected is False
    assert fresh_state.cloud_connected is False
    assert fresh_state.get_side_channels() == {}


def test_ingest_updates_values(fresh_state):
    fresh_state.ingest_buffer(_build_frame_for_rpm(3600))
    snap = fresh_state.snapshot()
    assert snap["values"]["engineSpeedRpm"] == 3600


def test_display_mode_standby(fresh_state):
    fresh_state.update("engineSpeedRpm", 0)
    fresh_state.update("utilityVoltageV", 240.5)
    assert fresh_state.get_display_mode() == "standby"


def test_display_mode_exercise(fresh_state):
    # Engine running AND utility OK => exercise
    fresh_state.update("engineSpeedRpm", 3600)
    fresh_state.update("utilityVoltageV", 240.5)
    assert fresh_state.get_display_mode() == "exercise"


def test_display_mode_running(fresh_state):
    # Engine running AND utility dead => real outage
    fresh_state.update("engineSpeedRpm", 3600)
    fresh_state.update("utilityVoltageV", 0)
    assert fresh_state.get_display_mode() == "running"


def test_gen_start_event_on_utility_loss(fresh_state):
    fresh_state.update("utilityVoltageV", 240.5)
    fresh_state.update("utilityVoltageV", 0)
    assert fresh_state.gen_started_at is not None
    events = fresh_state.snapshot()["events"]
    assert any("Utility power lost" in e["msg"] for e in events)


def test_gen_stop_event_on_utility_return(fresh_state):
    fresh_state.update("utilityVoltageV", 240.5)
    fresh_state.update("utilityVoltageV", 0)
    fresh_state.update("utilityVoltageV", 240.5)
    assert fresh_state.gen_started_at is None
    events = fresh_state.snapshot()["events"]
    assert any("Utility restored" in e["msg"] for e in events)


def test_temp_f_derived_from_c(fresh_state):
    fresh_state.update("lubeOilTempC", 100)
    snap = fresh_state.snapshot()
    assert snap["values"]["oilTempF"] == 212.0


def test_side_channel_roundtrip(fresh_state):
    fresh_state.update_side_channel("unifi", {"rx": 123, "tx": 456})
    got = fresh_state.get_side_channels()
    assert got == {"unifi": {"rx": 123, "tx": 456}}


def test_side_channel_is_isolated_from_caller_mutation(fresh_state):
    data = {"rx": 1}
    fresh_state.update_side_channel("s", data)
    data["rx"] = 999
    assert fresh_state.get_side_channels()["s"]["rx"] == 1


def test_snapshot_includes_side_channels(fresh_state):
    fresh_state.update_side_channel("unifi", {"port": 9, "errors": 0})
    snap = fresh_state.snapshot()
    assert snap["side_channels"] == {"unifi": {"port": 9, "errors": 0}}


def test_oil_reset_zeros_counter(fresh_state):
    fresh_state.update("totalRuntimeHours", 100.0)
    fresh_state.reset_oil_check()
    snap = fresh_state.snapshot()
    assert snap["oil_runtime_since_check"] == 0.0
    assert any("Oil check" in e["msg"] for e in snap["events"])


def test_handshake_save_load_roundtrip(tmp_path, monkeypatch, reset_handshake):
    monkeypatch.setitem(state_mod.CFG, "config_dir", str(tmp_path))
    HANDSHAKE["cloud_greeting"] = b"\xde\xad\xbe\xef" * 144
    HANDSHAKE["rdc_response"] = b"\xca\xfe\xba\xbe" * 144
    HANDSHAKE["config_msg"] = bytes(36)
    save_handshake()

    assert Path(handshake_path()).exists()
    HANDSHAKE["cloud_greeting"] = None
    HANDSHAKE["rdc_response"] = None
    HANDSHAKE["config_msg"] = None
    assert load_handshake() is True
    assert HANDSHAKE["cloud_greeting"] == b"\xde\xad\xbe\xef" * 144
    assert HANDSHAKE["config_msg"] == bytes(36)


def test_handshake_have_requires_greeting_and_config(reset_handshake):
    from rdc_proxy.state import have_handshake
    assert have_handshake() is False
    HANDSHAKE["cloud_greeting"] = b"x" * 576
    assert have_handshake() is False
    HANDSHAKE["config_msg"] = b"y" * 36
    assert have_handshake() is True
