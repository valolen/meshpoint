"""Prepare a separate, privacy-filtered packet for all MQTT output formats."""
from copy import deepcopy
from dataclasses import replace
import math

from src.models.packet import Packet, Protocol

_LOCATION_FIELDS = {
    "latitude", "longitude", "latitude_i", "longitude_i", "altitude",
    "altitude_hae", "altitude_geoidal_separation", "precision_bits",
    "ground_speed", "ground_track",
}


def is_broadcast(packet: Packet) -> bool:
    destination = packet.destination_id
    if not isinstance(destination, str):
        return False
    destination = destination.lower()
    if packet.protocol == Protocol.MESHTASTIC:
        return destination == "ffffffff"
    if packet.protocol == Protocol.MESHCORE:
        return destination in {"ffff", "broadcast"}
    return False


def prepare_packet(packet: Packet, precision: str | int) -> Packet:
    # Invalid policies fail closed. Never edit the shared storage/UI packet.
    bits_cap = precision if type(precision) is int and 0 <= precision <= 32 else 15
    policy = ("none" if bits_cap == 0 else "exact" if bits_cap == 32 else "approximate") if type(precision) is int else (precision if precision in {"exact", "approximate", "none"} else "none")

    def filtered(value):
        if isinstance(value, list):
            return [filtered(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: filtered(item) for key, item in value.items()}
        if policy == "none":
            return {key: item for key, item in result.items() if key not in _LOCATION_FIELDS}
        lat, lon = result.get("latitude"), result.get("longitude")
        valid = (type(lat) in (int, float) and type(lon) in (int, float)
                 and math.isfinite(lat) and math.isfinite(lon)
                 and -90 <= lat <= 90 and -180 <= lon <= 180)
        if not valid:
            return {key: item for key, item in result.items() if key not in _LOCATION_FIELDS}
        if policy == "approximate":
            result = {key: item for key, item in result.items() if key not in _LOCATION_FIELDS}
            if packet.protocol == Protocol.MESHTASTIC:
                # Quantize to a Meshtastic coordinate grid, never claiming
                # greater precision than the received position.
                original_bits = value.get("precision_bits")
                bits = min(bits_cap, original_bits) if type(original_bits) is int and 0 < original_bits <= 32 else bits_cap
                if type(original_bits) is int and 0 < original_bits <= bits_cap:
                    result.update(latitude=lat, longitude=lon,
                                  precision_bits=original_bits)
                    return result
                step = 1 << (32 - bits)

                def quantize(coord, limit):
                    integer = round(coord * 1e7)
                    center = (integer // step) * step + step // 2
                    return max(-limit, min(limit, center / 1e7))

                result.update(latitude=quantize(lat, 90),
                              longitude=quantize(lon, 180),
                              precision_bits=bits)
            else:
                result.update(latitude=round(lat, 2), longitude=round(lon, 2))
        return result

    return replace(packet, decoded_payload=filtered(deepcopy(packet.decoded_payload)),
                   encrypted_payload=None, raw_app_payload=None, raw_radio_packet=None)
