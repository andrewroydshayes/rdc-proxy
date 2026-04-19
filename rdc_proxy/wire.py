"""TLV wire protocol decoder for the Kohler RDC.

Pure / stateless: takes raw bytes, yields decoded records. Safe to import from
any module without side effects.
"""

import struct

PARAM_MAP = {
    0x044C: ("engineSpeedRpm",        "raw",   "RPM"),
    0x0663: ("engineSpeedRpm_2",      "raw",   "RPM"),
    0x054E: ("engineFrequencyHz",     "div10", "Hz"),
    0x0A62: ("engineFrequencyHz_2",   "div10", "Hz"),
    0x0536: ("generatorVoltageV",     "div10", "V"),
    0x053C: ("generatorVoltageV_2",   "div10", "V"),
    0x0A4A: ("generatorVoltageV_3",   "div10", "V"),
    0x0A50: ("generatorVoltageV_4",   "div10", "V"),
    0x09E6: ("utilityVoltageV",       "div10", "V"),
    0x09EC: ("utilityVoltageV_B",     "div10", "V"),
    0x09FE: ("utilityFrequencyHz",    "div10", "Hz"),
    0x0453: ("batteryVoltageV",       "div10", "V"),
    0x045B: ("lubeOilTempC",          "raw",   "C"),
    0x045D: ("controllerTempC",       "raw",   "C"),
    0x05E0: ("totalOperationHours",   "div10", "h"),
    0x05F4: ("totalOperationHours_2", "div10", "h"),
    0x05F6: ("totalRuntimeHours",     "div10", "h"),
    0x05E2: ("maintHoursSinceLast",   "div10", "h"),
    0x05DC: ("timestamp",             "raw",   ""),
    0x058D: ("serialNumber",          "str",   ""),
    0x05A3: ("modelCode",             "str",   ""),
    0x0960: ("engineState",           "raw",   ""),
    0x0A92: ("engineState_2",         "raw",   ""),
    0x378C: ("loadShed_HVAC_A",       "raw",   ""),
    0x3796: ("loadShed_HVAC_B",       "raw",   ""),
    0x37A0: ("loadShed_Load_A",       "raw",   ""),
    0x37AA: ("loadShed_Load_B",       "raw",   ""),
    0x37B4: ("loadShed_Load_C",       "raw",   ""),
    0x37BE: ("loadShed_Load_D",       "raw",   ""),
}


def decode_value(raw_bytes, vlen, transform):
    if transform == "str":
        return raw_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
    if vlen == 4:
        raw = struct.unpack("<i", raw_bytes)[0]
    elif vlen == 8:
        raw = struct.unpack("<q", raw_bytes)[0]
    else:
        return None
    if transform == "div10":
        return round(raw / 10.0, 2)
    return raw


def parse_tlv_records(buf):
    """Yield (param_id, decoded_name, decoded_value, units) from a TLV buffer.

    Format (little-endian throughout):
      Header 12B: [len:u32][ver=2:u16][record_count:u16][rsvd:u32]
      Record:    [param_id:u16][type=0x00c0:u16][vlen:u32][value:vlen][pad=0:u16]
    """
    i, N = 0, len(buf)
    while i + 12 <= N:
        lf = struct.unpack_from("<I", buf, i)[0]
        ver = struct.unpack_from("<H", buf, i + 4)[0]
        if ver != 2 or lf < 14 or lf > 4096 or i + lf > N:
            i += 1
            continue
        count = struct.unpack_from("<H", buf, i + 6)[0]
        body = buf[i + 12: i + lf]
        off = 0
        ok = True
        recs = []
        for _ in range(count):
            if off + 8 > len(body):
                ok = False
                break
            rid = struct.unpack_from("<H", body, off)[0]
            vlen = struct.unpack_from("<I", body, off + 4)[0]
            if off + 8 + vlen + 2 > len(body):
                ok = False
                break
            val = body[off + 8: off + 8 + vlen]
            off += 8 + vlen + 2
            recs.append((rid, vlen, val))
        if not ok or off != len(body):
            i += 1
            continue
        for rid, vlen, val in recs:
            meta = PARAM_MAP.get(rid)
            if meta:
                name, transform, units = meta
                decoded = decode_value(val, vlen, transform)
                if decoded is not None:
                    yield rid, name, decoded, units
        i += lf
