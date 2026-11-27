"""Tests for MQTT publisher logging and gateway ID behavior."""

from __future__ import annotations

import base64
import json
import struct
import unittest
from unittest.mock import MagicMock

from src.config import MqttConfig
from src.models.packet import PacketType, Protocol
from src.relay.channel_resolver import _expand_key, _xor_hash
from src.relay.mqtt_formatter import MqttMessage
from src.relay.mqtt_publisher import HomeAssistantDiscovery, MqttPublisher, _generate_gateway_id


class TestGatewayId(unittest.TestCase):
    def test_gateway_id_is_deterministic_and_case_insensitive(self):
        lower = _generate_gateway_id("meshpoint-alpha")
        upper = _generate_gateway_id("MESHPOINT-ALPHA")
        self.assertEqual(lower, upper)
        self.assertTrue(lower.startswith("!"))
        self.assertEqual(len(lower), 9)

    def test_config_override_used_by_publisher(self) -> None:
        cfg = MqttConfig(enabled=True, gateway_id="aabbccdd")
        pub = MqttPublisher(cfg, device_name="ignored")
        self.assertEqual(pub.gateway_id, "!aabbccdd")

    def test_longturbo_requires_explicit_publish_channel(self) -> None:
        packet = MagicMock(
            protocol=Protocol.MESHTASTIC,
            destination_id="ffffffff",
            packet_type=PacketType.TEXT,
            decrypted=True,
            encrypted_payload=None,
            channel_hash=118,
            remote_channel_name=None,
        )
        default_pub = MqttPublisher(MqttConfig(enabled=True), "meshpoint")
        allowed_pub = MqttPublisher(
            MqttConfig(enabled=True, publish_channels=["LongTurbo"]),
            "meshpoint",
        )
        self.assertFalse(default_pub._passes_safety_gates(packet))
        self.assertTrue(allowed_pub._passes_safety_gates(packet))

    def test_usb_channel_name_drives_gate_and_topic(self) -> None:
        packet = MagicMock(
            protocol=Protocol.MESHTASTIC,
            destination_id="ffffffff",
            packet_type=PacketType.TEXT,
            decrypted=True,
            encrypted_payload=None,
            channel_hash=0,
            remote_channel_name="LongTurbo",
        )
        pub = MqttPublisher(
            MqttConfig(enabled=True, publish_channels=["LongTurbo"]),
            "meshpoint",
        )
        self.assertTrue(pub._passes_safety_gates(packet))
        self.assertEqual(pub._mt_formatter._resolve_channel(packet), "LongTurbo")



class TestOwnPrivatePosition(unittest.TestCase):
    def _publisher(self, bits=32, uplink=True):
        key_b64 = base64.b64encode(b"private-key-1234").decode()
        cfg = MqttConfig(
            enabled=True,
            channel_settings={
                "Family": {
                    "uplink_enabled": uplink,
                    "downlink_enabled": False,
                    "position_precision": bits,
                }
            },
        )
        pub = MqttPublisher(
            cfg,
            "meshpoint",
            channel_keys={"Family": key_b64},
            node_id=0x272436DC,
        )
        pub._connected = True
        pub._client = MagicMock()
        return pub, key_b64

    def test_private_position_targets_require_uplink_and_nonzero_bits(self):
        pub, _ = self._publisher(bits=32, uplink=True)
        self.assertEqual(pub.private_position_targets(), [(1, "Family", 32)])

        pub, _ = self._publisher(bits=0, uplink=True)
        self.assertEqual(pub.private_position_targets(), [])

        pub, _ = self._publisher(bits=32, uplink=False)
        self.assertEqual(pub.private_position_targets(), [])

    def test_private_position_publishes_only_encrypted_body(self):
        pub, key_b64 = self._publisher(bits=24, uplink=True)
        key = _expand_key(base64.b64decode(key_b64))
        channel_hash = _xor_hash("Family", key)
        flags = 3 | (3 << 5)
        raw = (
            struct.pack("<III", 0xFFFFFFFF, 0x272436DC, 0x12345678)
            + bytes([flags, channel_hash, 0, 0])
            + b"encrypted-position"
        )
        pub._mt_formatter._format_encrypted = MagicMock(
            return_value=MqttMessage("msh/EU_868/2/e/Family/!272436dc", b"wire")
        )
        pub._client.publish.return_value = MagicMock(rc=0)

        self.assertTrue(
            pub.publish_own_private_position(
                raw,
                channel_name="Family",
                precision_bits=24,
            )
        )
        packet = pub._mt_formatter._format_encrypted.call_args.args[0]
        self.assertIsNone(packet.decoded_payload)
        self.assertEqual(packet.encrypted_payload, b"encrypted-position")
        self.assertEqual(packet.source_id, "272436dc")
        self.assertEqual(packet.remote_channel_name, "Family")
        pub._client.publish.assert_called_once_with(
            "msh/EU_868/2/e/Family/!272436dc", b"wire", qos=1
        )


class TestHomeAssistantState(unittest.TestCase):
    def test_telemetry_publishes_retained_json_state(self):
        client = MagicMock()
        ha = HomeAssistantDiscovery(client, "!deadbeef")
        packet = MagicMock()
        packet.source_id = "abcd1234"
        packet.packet_type = PacketType.TELEMETRY
        packet.decoded_payload = {
            "battery_level": 85,
            "temperature": 22.5,
        }

        ha.publish_state(packet)

        client.publish.assert_called_once()
        topic, body = client.publish.call_args[0][:2]
        self.assertEqual(topic, "meshpoint/abcd1234/telemetry")
        self.assertEqual(json.loads(body)["battery_level"], 85)
        self.assertEqual(client.publish.call_args[1].get("retain"), True)

    def test_position_publishes_retained_json_state(self):
        client = MagicMock()
        ha = HomeAssistantDiscovery(client, "!deadbeef")
        packet = MagicMock()
        packet.source_id = "abcd1234"
        packet.packet_type = PacketType.POSITION
        packet.decoded_payload = {
            "latitude": 40.7,
            "longitude": -74.0,
            "altitude": 10,
        }

        ha.publish_state(packet)

        topic, body = client.publish.call_args[0][:2]
        self.assertEqual(topic, "meshpoint/abcd1234/position")
        parsed = json.loads(body)
        self.assertEqual(parsed["latitude"], 40.7)
        self.assertEqual(parsed["longitude"], -74.0)
        self.assertEqual(parsed["altitude"], 10)


class TestMqttPublisherLogging(unittest.TestCase):
    def test_on_connect_logs_start_message(self):
        pub = MqttPublisher(MqttConfig(enabled=True), device_name="meshpoint-alpha")
        with self.assertLogs("src.relay.mqtt_publisher", level="INFO") as logs:
            pub._on_connect(client=None, userdata=None, flags=None, rc=0)
        self.assertTrue(pub.connected)
        self.assertTrue(
            any("MQTT publisher started as !" in msg for msg in logs.output),
            logs.output,
        )

    def test_runtime_status_tracks_connect_and_disconnect(self) -> None:
        pub = MqttPublisher(MqttConfig(enabled=True), device_name="meshpoint-alpha")
        pub._on_connect(client=None, userdata=None, flags=None, rc=0)
        status = pub.get_runtime_status()
        self.assertTrue(status["connected"])
        self.assertIsNotNone(status["connected_since"])
        pub._on_disconnect(client=None, userdata=None, rc=7)
        status = pub.get_runtime_status()
        self.assertFalse(status["connected"])
        self.assertEqual(status["disconnect_count"], 1)
        self.assertEqual(status["last_disconnect_rc"], 7)


if __name__ == "__main__":
    unittest.main()
