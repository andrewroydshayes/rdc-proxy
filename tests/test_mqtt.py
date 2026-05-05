"""MQTT publisher config, Home Assistant discovery, and state fanout."""

import json

from rdc_proxy.mqtt import MqttPublisher, _timestamp_payload, start_mqtt_publisher


class FakeMqttClient:
    def __init__(self):
        self.published = []
        self.connected = None
        self.loop_started = False
        self.username = None
        self.will = None
        self.reconnect_delay = None
        self.on_connect = None

    def username_pw_set(self, username, password=None):
        self.username = (username, password)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delay = (min_delay, max_delay)

    def will_set(self, topic, payload=None, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def connect_async(self, host, port=1883, keepalive=60):
        self.connected = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True
        if self.on_connect:
            self.on_connect(self, None, None, 0, None)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def _cfg(**overrides):
    cfg = {
        "enabled": True,
        "host": "mqtt.local",
        "port": 1883,
        "client_id": "rdc-proxy",
        "base_topic": "rdc_proxy",
        "discovery_prefix": "homeassistant",
        "device_id": "",
        "device_name": "Generator",
        "retain": True,
        "qos": 0,
        "username": "",
        "password": "",
    }
    cfg.update(overrides)
    return cfg


def test_start_mqtt_publisher_returns_none_when_disabled(fresh_state):
    assert start_mqtt_publisher({"mqtt": {"enabled": False}}, fresh_state) is None


def test_publisher_starts_client_and_registers_listener(fresh_state):
    fake = FakeMqttClient()
    publisher = MqttPublisher(_cfg(username="user", password="secret"), lambda: fake)
    publisher.start(fresh_state)

    assert fake.username == ("user", "secret")
    assert fake.reconnect_delay == (1, 60)
    assert fake.will == ("rdc_proxy/status", "offline", 0, True)
    assert fake.connected == ("mqtt.local", 1883, 60)
    assert fake.loop_started is True
    assert ("rdc_proxy/status", "online", 0, True) in fake.published

    fresh_state.update("engineSpeedRpm", 3600)
    assert ("rdc_proxy/engineSpeedRpm", "3600", 0, True) in fake.published


def test_publish_update_waits_for_serial_before_home_assistant_discovery():
    fake = FakeMqttClient()
    publisher = MqttPublisher(_cfg(), lambda: fake)

    publisher.publish_update("batteryVoltageV", 12.8, 123.0)
    assert not [
        item for item in fake.published
        if item[0].startswith("homeassistant/sensor/")
    ]

    publisher.publish_update("serialNumber", "339TGVKM", 124.0)
    publisher.publish_update("modelCode", "PS-CH740-3345", 124.5)
    publisher.publish_update("batteryVoltageV", 12.9, 124.0)

    assert not [
        item for item in fake.published
        if item[0] in (
            "homeassistant/sensor/339tgvkm/serialNumber/config",
            "homeassistant/sensor/339tgvkm/modelCode/config",
        )
    ]

    discovery = [
        item for item in fake.published
        if item[0] == "homeassistant/sensor/339tgvkm/batteryVoltageV/config"
    ]
    assert len(discovery) == 2

    payload = json.loads(discovery[-1][1])
    assert payload["name"] == "Battery Voltage"
    assert payload["unique_id"] == "generator_339tgvkm_battery_voltage_v"
    assert payload["state_topic"] == "rdc_proxy/batteryVoltageV"
    assert payload["availability_topic"] == "rdc_proxy/status"
    assert payload["unit_of_measurement"] == "V"
    assert payload["device_class"] == "voltage"
    assert payload["state_class"] == "measurement"
    assert payload["device"]["identifiers"] == ["kohler_generator_339tgvkm"]
    assert payload["device"]["name"] == "Generator"
    assert payload["device"]["serial_number"] == "339TGVKM"
    assert payload["device"]["model"] == "PS-CH740-3345"

    assert ("rdc_proxy/batteryVoltageV", "12.8", 0, True) in fake.published
    assert ("rdc_proxy/batteryVoltageV", "12.9", 0, True) in fake.published
    assert ("rdc_proxy/serialNumber", "339TGVKM", 0, True) in fake.published
    assert ("rdc_proxy/modelCode", "PS-CH740-3345", 0, True) in fake.published


def test_configured_device_id_allows_discovery_before_serial():
    fake = FakeMqttClient()
    publisher = MqttPublisher(_cfg(device_id="standby-generator"), lambda: fake)

    publisher.publish_update("batteryVoltageV", 12.8, 123.0)

    discovery = [
        item for item in fake.published
        if item[0] == "homeassistant/sensor/standby_generator/batteryVoltageV/config"
    ]
    assert len(discovery) == 1
    payload = json.loads(discovery[0][1])
    assert payload["unique_id"] == "generator_standby_generator_battery_voltage_v"
    assert payload["device"]["identifiers"] == ["rdc_proxy_standby_generator"]


def test_device_metadata_updates_existing_discovery_configs():
    fake = FakeMqttClient()
    publisher = MqttPublisher(_cfg(device_id="standby-generator"), lambda: fake)

    publisher.publish_update("batteryVoltageV", 12.8, 123.0)
    publisher.publish_update("serialNumber", "339TGVKM", 124.0)
    publisher.publish_update("modelCode", "PS-CH740-3345", 125.0)

    discovery = [
        json.loads(item[1]) for item in fake.published
        if item[0] == "homeassistant/sensor/standby_generator/batteryVoltageV/config"
    ]
    assert len(discovery) == 3
    assert "serial_number" not in discovery[0]["device"]
    assert discovery[-1]["device"]["serial_number"] == "339TGVKM"
    assert discovery[-1]["device"]["model"] == "PS-CH740-3345"

    assert not [
        item for item in fake.published
        if item[0] in (
            "homeassistant/sensor/standby_generator/serialNumber/config",
            "homeassistant/sensor/standby_generator/modelCode/config",
        )
    ]


def test_timestamp_is_published_as_home_assistant_timestamp():
    fake = FakeMqttClient()
    publisher = MqttPublisher(_cfg(device_id="standby-generator"), lambda: fake)

    publisher.publish_update("timestamp", 639136073940000000, 123.0)

    timestamp_publish = [
        item for item in fake.published
        if item[0] == "rdc_proxy/timestamp"
    ][0]
    assert timestamp_publish[1].startswith("2026-05-05T19:49:54")
    assert timestamp_publish[2:] == (0, True)
    assert timestamp_publish[1] != "2026-05-05T19:49:54Z"
    assert (
        timestamp_publish[1].endswith("+00:00")
        or timestamp_publish[1][-6] in ("+", "-")
    )

    discovery = [
        item for item in fake.published
        if item[0] == "homeassistant/sensor/standby_generator/timestamp/config"
    ]
    payload = json.loads(discovery[0][1])
    assert payload["device_class"] == "timestamp"
    assert payload["enabled_by_default"] is False
    assert payload["entity_category"] == "diagnostic"
    assert "unit_of_measurement" not in payload


def test_timestamp_payload_falls_back_to_raw_value():
    payload = _timestamp_payload(639136073940000000)
    assert payload.startswith("2026-05-05T19:49:54")
    assert payload != "2026-05-05T19:49:54Z"


def test_celsius_units_use_home_assistant_temperature_unit():
    fake = FakeMqttClient()
    publisher = MqttPublisher(_cfg(device_id="standby-generator"), lambda: fake)

    publisher.publish_update("lubeOilTempC", 11, 123.0)

    discovery = [
        item for item in fake.published
        if item[0] == "homeassistant/sensor/standby_generator/lubeOilTempC/config"
    ]
    payload = json.loads(discovery[0][1])
    assert payload["device_class"] == "temperature"
    assert payload["unit_of_measurement"] == "\u00b0C"


def test_first_update_publishes_online_if_connect_callback_did_not():
    fake = FakeMqttClient()
    publisher = MqttPublisher(_cfg(), lambda: fake)

    publisher.publish_update("batteryVoltageV", 12.8, 123.0)
    publisher.publish_update("utilityVoltageV", 248.3, 124.0)

    online_publishes = [
        item for item in fake.published
        if item == ("rdc_proxy/status", "online", 0, True)
    ]
    assert len(online_publishes) == 1
