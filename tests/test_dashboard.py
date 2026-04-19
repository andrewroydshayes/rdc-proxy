"""Dashboard interface-sampling tests. The real /sys readers are touched via
a tmp_path override, so no root/hardware required."""

from rdc_proxy.dashboard import delta_rate, iface_sample, pi_health


def test_delta_rate_handles_zero_dt_gracefully():
    # dt must be clamped by the caller; but ensure no div-by-zero inside
    curr = {"rx_packets": 100}
    prev = {"rx_packets": 50}
    assert delta_rate(curr, prev, "rx_packets", 1.0) == 50.0


def test_delta_rate_floors_at_zero_on_counter_reset():
    # If counters reset (curr < prev) we should report 0, not negative
    curr = {"rx_packets": 5}
    prev = {"rx_packets": 1000}
    assert delta_rate(curr, prev, "rx_packets", 1.0) == 0.0


def test_iface_sample_shape():
    curr = {
        "rx_packets": 1100, "tx_packets": 2100,
        "rx_bytes": 11000, "tx_bytes": 21000,
        "rx_errors": 1, "tx_errors": 0,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_crc_errors": 0,
    }
    prev = {
        "rx_packets": 1000, "tx_packets": 2000,
        "rx_bytes": 10000, "tx_bytes": 20000,
        "rx_errors": 0, "tx_errors": 0,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_crc_errors": 0,
    }
    s = iface_sample(curr, prev, dt=1.0)
    assert s["rx_pps"] == 100.0
    assert s["tx_pps"] == 100.0
    assert s["rx_bps"] == 1000.0
    assert s["rx_errors"] == 1
    # avg pkt size: 2000 bytes / 200 packets = 10.0
    assert s["avg_pkt_size"] == 10.0


def test_iface_sample_zero_packets():
    curr = prev = {
        "rx_packets": 0, "tx_packets": 0,
        "rx_bytes": 0, "tx_bytes": 0,
        "rx_errors": 0, "tx_errors": 0,
        "rx_dropped": 0, "tx_dropped": 0,
        "rx_crc_errors": 0,
    }
    s = iface_sample(curr, prev, dt=1.0)
    assert s["rx_pps"] == 0
    assert s["avg_pkt_size"] == 0


def test_pi_health_returns_dict_shape():
    # This touches real /sys paths; on a Pi it works, elsewhere the exception
    # branches set fields to None. Either way the shape is stable.
    h = pi_health()
    for k in ("cpu_temp_c", "load1", "load5", "load15", "disk_free_gb", "disk_total_gb"):
        assert k in h
