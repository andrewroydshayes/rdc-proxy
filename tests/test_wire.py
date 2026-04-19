"""TLV decoder round-trip + error-recovery tests."""

import struct

from rdc_proxy.wire import PARAM_MAP, decode_value, parse_tlv_records


def _build_record(param_id, value_bytes):
    """One TLV record: [id:u16][type:u16][vlen:u32][value][pad:u16]."""
    return (
        struct.pack("<HHI", param_id, 0x00C0, len(value_bytes))
        + value_bytes
        + struct.pack("<H", 0)
    )


def _build_frame(records):
    body = b"".join(records)
    frame_len = 12 + len(body)
    header = struct.pack("<IHHI", frame_len, 2, len(records), 0)
    return header + body


def test_decode_value_raw_int():
    assert decode_value(struct.pack("<i", 3600), 4, "raw") == 3600


def test_decode_value_div10():
    # 2400 / 10.0 => 240.0 (generator voltage)
    assert decode_value(struct.pack("<i", 2400), 4, "div10") == 240.0


def test_decode_value_str_trims_nulls():
    assert decode_value(b"339TGVKM\x00\x00", 10, "str") == "339TGVKM"


def test_decode_value_rejects_bad_vlen():
    assert decode_value(b"\x01\x02\x03", 3, "raw") is None


def test_parse_single_record_engine_rpm():
    frame = _build_frame([_build_record(0x044C, struct.pack("<i", 3600))])
    records = list(parse_tlv_records(frame))
    assert len(records) == 1
    rid, name, value, units = records[0]
    assert rid == 0x044C
    assert name == "engineSpeedRpm"
    assert value == 3600
    assert units == "RPM"


def test_parse_div10_voltage():
    # 0x0536 = generatorVoltageV (div10). Wire=2405 -> 240.5V
    frame = _build_frame([_build_record(0x0536, struct.pack("<i", 2405))])
    records = list(parse_tlv_records(frame))
    assert records[0][2] == 240.5
    assert records[0][3] == "V"


def test_parse_multiple_records_in_one_frame():
    frame = _build_frame([
        _build_record(0x044C, struct.pack("<i", 3600)),
        _build_record(0x0453, struct.pack("<i", 128)),   # batteryVoltageV div10 = 12.8
    ])
    out = list(parse_tlv_records(frame))
    assert len(out) == 2
    names = {r[1] for r in out}
    assert names == {"engineSpeedRpm", "batteryVoltageV"}


def test_parse_ignores_unknown_param_id():
    # id 0xffff is not in PARAM_MAP -> should be skipped silently
    frame = _build_frame([
        _build_record(0xFFFF, struct.pack("<i", 42)),
        _build_record(0x044C, struct.pack("<i", 3600)),
    ])
    out = list(parse_tlv_records(frame))
    assert len(out) == 1
    assert out[0][1] == "engineSpeedRpm"


def test_parse_skips_garbage_prefix():
    # Garbage byte then a valid frame should still decode the valid frame
    frame = b"\xAA" + _build_frame([_build_record(0x044C, struct.pack("<i", 3600))])
    out = list(parse_tlv_records(frame))
    assert len(out) == 1


def test_parse_rejects_wrong_version():
    body = _build_record(0x044C, struct.pack("<i", 3600))
    # version=3 instead of 2 -> frame rejected
    bad = struct.pack("<IHHI", 12 + len(body), 3, 1, 0) + body
    assert list(parse_tlv_records(bad)) == []


def test_parse_rejects_truncated_frame():
    frame = _build_frame([_build_record(0x044C, struct.pack("<i", 3600))])
    # Cut last byte off
    assert list(parse_tlv_records(frame[:-1])) == []


def test_eight_byte_value_ok():
    # timestamp id 0x05DC holds an 8-byte value
    frame = _build_frame([_build_record(0x05DC, struct.pack("<q", 1234567890))])
    out = list(parse_tlv_records(frame))
    assert out[0][1] == "timestamp"
    assert out[0][2] == 1234567890


def test_param_map_has_critical_ids():
    # If someone drops these from PARAM_MAP we want to know
    for critical in (0x044C, 0x0453, 0x05F6, 0x09E6, 0x0536):
        assert critical in PARAM_MAP
