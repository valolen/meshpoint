"""Private-channel POSITION packet construction tests."""

from __future__ import annotations

import unittest

from meshtastic.protobuf.mesh_pb2 import Data, Position

from src.decode.crypto_service import CryptoService
from src.transmit.meshtastic_builder import MeshtasticPacketBuilder


class TestPrivatePositionBuilder(unittest.TestCase):
    def _decode_position(self, frame: bytes, *, source: int, packet_id: int, key: bytes) -> Position:
        crypto = CryptoService()
        plaintext = crypto.decrypt_meshtastic(
            frame[16:],
            packet_id,
            source,
            key=key,
        )
        self.assertIsNotNone(plaintext)
        data = Data.FromString(plaintext)
        self.assertEqual(data.portnum, 3)
        return Position.FromString(data.payload)

    def test_approximate_private_position_sets_bits_and_omits_altitude(self):
        crypto = CryptoService()
        key = crypto._expand_key(b"private-key-1234")
        source = 0x272436DC
        packet_id = 0x10203040
        frame = MeshtasticPacketBuilder(crypto).build_position(
            source_id=source,
            packet_id=packet_id,
            latitude=54.7006009,
            longitude=25.1565773,
            altitude=210,
            precision_bits=24,
            channel_key=key,
            channel_hash=0x42,
        )
        self.assertIsNotNone(frame)

        pos = self._decode_position(
            frame, source=source, packet_id=packet_id, key=key
        )
        self.assertEqual(pos.precision_bits, 24)
        self.assertEqual(pos.altitude, 0)
        self.assertNotEqual(pos.latitude_i, int(54.7006009 * 1e7))
        self.assertNotEqual(pos.longitude_i, int(25.1565773 * 1e7))

    def test_exact_private_position_keeps_coordinates_and_altitude(self):
        crypto = CryptoService()
        key = crypto._expand_key(b"private-key-1234")
        source = 0x272436DC
        packet_id = 0x10203041
        frame = MeshtasticPacketBuilder(crypto).build_position(
            source_id=source,
            packet_id=packet_id,
            latitude=54.7006009,
            longitude=25.1565773,
            altitude=210,
            precision_bits=32,
            channel_key=key,
            channel_hash=0x42,
        )
        self.assertIsNotNone(frame)

        pos = self._decode_position(
            frame, source=source, packet_id=packet_id, key=key
        )
        self.assertEqual(pos.precision_bits, 32)
        self.assertEqual(pos.latitude_i, int(54.7006009 * 1e7))
        self.assertEqual(pos.longitude_i, int(25.1565773 * 1e7))
        self.assertEqual(pos.altitude, 210)

    def test_zero_bits_is_not_a_valid_packet_precision(self):
        crypto = CryptoService()
        key = crypto._expand_key(b"private-key-1234")
        frame = MeshtasticPacketBuilder(crypto).build_position(
            source_id=0x272436DC,
            packet_id=1,
            latitude=54.7,
            longitude=25.15,
            precision_bits=0,
            channel_key=key,
            channel_hash=0x42,
        )
        self.assertIsNone(frame)


if __name__ == "__main__":
    unittest.main()
