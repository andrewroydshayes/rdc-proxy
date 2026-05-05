"""MQTT publishing for Home Assistant ingestion."""

from datetime import datetime, timedelta
import json
import re

from rdc_proxy.wire import PARAM_MAP


FIELD_UNITS = {name: units for name, _transform, units in PARAM_MAP.values()}
HA_UNITS = {
    "C": "\u00b0C",
}

FIELD_META = {
    "batteryVoltageV": {"device_class": "voltage", "state_class": "measurement"},
    "controllerTempC": {"device_class": "temperature", "state_class": "measurement"},
    "engineFrequencyHz": {"device_class": "frequency", "state_class": "measurement"},
    "engineFrequencyHz_2": {"device_class": "frequency", "state_class": "measurement"},
    "engineSpeedRpm": {"state_class": "measurement"},
    "engineSpeedRpm_2": {"state_class": "measurement"},
    "generatorVoltageV": {"device_class": "voltage", "state_class": "measurement"},
    "generatorVoltageV_2": {"device_class": "voltage", "state_class": "measurement"},
    "generatorVoltageV_3": {"device_class": "voltage", "state_class": "measurement"},
    "generatorVoltageV_4": {"device_class": "voltage", "state_class": "measurement"},
    "lubeOilTempC": {"device_class": "temperature", "state_class": "measurement"},
    "maintHoursSinceLast": {"device_class": "duration", "state_class": "total_increasing"},
    "timestamp": {
        "device_class": "timestamp",
        "enabled_by_default": False,
        "entity_category": "diagnostic",
    },
    "totalOperationHours": {"device_class": "duration", "state_class": "total_increasing"},
    "totalOperationHours_2": {"device_class": "duration", "state_class": "total_increasing"},
    "totalRuntimeHours": {"device_class": "duration", "state_class": "total_increasing"},
    "utilityFrequencyHz": {"device_class": "frequency", "state_class": "measurement"},
    "utilityVoltageV": {"device_class": "voltage", "state_class": "measurement"},
    "utilityVoltageV_B": {"device_class": "voltage", "state_class": "measurement"},
}

DEVICE_FIELDS = {"modelCode", "serialNumber"}


def _slug(value):
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")


def _field_slug(field):
    return _slug(re.sub(r"(?<!^)(?=[A-Z])", "_", field))


def _friendly_name(field):
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", field).replace("_", " ")
    return (
        words.title()
        .replace(" Voltage V", " Voltage")
        .replace(" Temp C", " Temp")
        .replace(" Frequency Hz", " Frequency")
        .replace(" Rpm", " RPM")
    )


def _topic(*parts):
    return "/".join(str(p).strip("/") for p in parts if str(p).strip("/"))


def _timestamp_payload(value):
    # RDC timestamp values are .NET ticks holding the controller's local wall
    # clock time. Attach the host's local timezone so Home Assistant does not
    # interpret the wall-clock value as UTC.
    ts = datetime(1, 1, 1) + timedelta(microseconds=int(value) / 10)
    return ts.astimezone().isoformat()


def _field_payload(field, value):
    if field == "timestamp":
        try:
            return _timestamp_payload(value)
        except (OverflowError, TypeError, ValueError):
            return str(value)
    return str(value)


class MqttPublisher:
    def __init__(self, cfg, client_factory=None):
        self.cfg = cfg
        self.base_topic = cfg.get("base_topic", "rdc_proxy")
        self.discovery_prefix = cfg.get("discovery_prefix", "homeassistant")
        self.configured_device_id = _slug(cfg.get("device_id", ""))
        self.device_name = cfg.get("device_name", "Generator")
        self.retain = bool(cfg.get("retain", True))
        self.qos = int(cfg.get("qos", 0))
        self.client = self._make_client(client_factory)
        self._discovered = set()
        self._pending_discovery = set()
        self._serial_number = None
        self._model_code = None
        self._availability_online = False

    def _make_client(self, client_factory):
        if client_factory:
            return client_factory()

        import paho.mqtt.client as mqtt

        client_id = self.cfg.get("client_id", "rdc-proxy")
        if hasattr(mqtt, "CallbackAPIVersion"):
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        return mqtt.Client(client_id=client_id)

    def start(self, state):
        username = self.cfg.get("username")
        password = self.cfg.get("password")
        if username:
            self.client.username_pw_set(username, password or None)
        self.client.on_connect = self._on_connect
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.will_set(
            _topic(self.base_topic, "status"),
            "offline",
            qos=self.qos,
            retain=True,
        )
        self.client.connect_async(
            self.cfg["host"],
            int(self.cfg.get("port", 1883)),
            keepalive=60,
        )
        self.client.loop_start()
        state.add_update_listener(self.publish_update)
        print(
            f"[mqtt] publishing to {self.cfg['host']}:{int(self.cfg.get('port', 1883))}",
            flush=True,
        )

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if reason_code == 0 or str(reason_code) == "Success":
            self.publish_availability(True)

    def publish_availability(self, online):
        payload = "online" if online else "offline"
        self.client.publish(
            _topic(self.base_topic, "status"),
            payload,
            qos=self.qos,
            retain=True,
        )
        self._availability_online = online

    def publish_update(self, field, value, _timestamp):
        if not self._availability_online:
            self.publish_availability(True)
        had_device_id = self._device_id() is not None
        device_metadata_changed = False
        if field == "serialNumber" and value:
            serial_number = str(value)
            device_metadata_changed = serial_number != self._serial_number
            self._serial_number = serial_number
        if field == "modelCode" and value:
            model_code = str(value)
            device_metadata_changed = model_code != self._model_code
            self._model_code = model_code
        if field not in DEVICE_FIELDS:
            self._publish_discovery(field)
        self.client.publish(
            _topic(self.base_topic, field),
            _field_payload(field, value),
            qos=self.qos,
            retain=self.retain,
        )
        if not had_device_id and self._device_id() is not None:
            self._publish_pending_discovery()
        elif device_metadata_changed and self._device_id() is not None:
            self._republish_discovery()

    def _device_id(self):
        if self.configured_device_id:
            return self.configured_device_id
        if self._serial_number:
            return _slug(self._serial_number)
        return None

    def _device_identifier(self):
        if self._serial_number:
            return f"kohler_generator_{_slug(self._serial_number)}"
        return f"rdc_proxy_{self._device_id()}"

    def _publish_pending_discovery(self):
        pending = sorted(self._pending_discovery)
        self._pending_discovery.clear()
        for field in pending:
            self._publish_discovery(field)

    def _republish_discovery(self):
        for field in sorted(self._discovered):
            self._publish_discovery(field, force=True)

    def _publish_discovery(self, field, force=False):
        device_id = self._device_id()
        if device_id is None:
            self._pending_discovery.add(field)
            return
        if field in self._discovered and not force:
            return
        self._discovered.add(field)

        object_id = f"generator_{device_id}_{_field_slug(field)}"
        payload = {
            "name": _friendly_name(field),
            "unique_id": object_id,
            "state_topic": _topic(self.base_topic, field),
            "availability_topic": _topic(self.base_topic, "status"),
            "device": {
                "identifiers": [self._device_identifier()],
                "name": self.device_name,
                "manufacturer": "Kohler",
            },
        }
        if self._serial_number:
            payload["device"]["serial_number"] = self._serial_number
        if self._model_code:
            payload["device"]["model"] = self._model_code
        unit = FIELD_UNITS.get(field)
        if unit:
            payload["unit_of_measurement"] = HA_UNITS.get(unit, unit)
        payload.update(FIELD_META.get(field, {}))

        self.client.publish(
            _topic(self.discovery_prefix, "sensor", device_id, field, "config"),
            json.dumps(payload, sort_keys=True),
            qos=self.qos,
            retain=True,
        )


def start_mqtt_publisher(cfg, state):
    mqtt_cfg = cfg.get("mqtt", {})
    if not mqtt_cfg.get("enabled"):
        return None
    publisher = MqttPublisher(mqtt_cfg)
    publisher.start(state)
    return publisher
