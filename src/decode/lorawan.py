"""Local-only passive LoRaWAN header inspection.

CRC_OK is an RF-layer check, not LoRaWAN MIC verification. Encrypted application
payloads and join accepts remain opaque. Frames failing structural checks are
retained as unknown observations rather than assigned invented device IDs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
MAX_FRAMES = 20_000
RETENTION_DAYS = 7
_TYPES = ("join_request", "join_accept", "unconfirmed_up", "unconfirmed_down",
          "confirmed_up", "confirmed_down", "rejoin_request", "proprietary")


def decode_header(payload: bytes) -> dict:
    unknown = {"frame_type": "unknown", "mic_verified": False}
    if not payload or payload[0] & 0x1F:
        return unknown
    mtype = payload[0] >> 5
    info = {"frame_type": _TYPES[mtype], "mic_verified": False}
    if mtype == 0:
        if len(payload) != 23:
            return unknown
        info.update(join_eui=payload[1:9][::-1].hex(),
                    dev_eui=payload[9:17][::-1].hex(),
                    dev_nonce=int.from_bytes(payload[17:19], "little"))
    elif mtype == 1:
        if len(payload) not in (17, 33):
            return unknown
    elif 2 <= mtype <= 5:
        if len(payload) < 12:
            return unknown
        opts_len = payload[5] & 15
        end = len(payload) - 4
        port_index = 8 + opts_len
        if port_index > end:
            return unknown
        info.update(dev_addr=payload[1:5][::-1].hex(),
                    fcnt16=int.from_bytes(payload[6:8], "little"),
                    fopts_len=opts_len)
        if port_index < end:
            info["fport"] = payload[port_index]
            info["encrypted_payload_size"] = end - port_index - 1
    elif mtype == 6:
        if len(payload) not in (19, 24):
            return unknown
        subtype = payload[1]
        if subtype in (0, 2) and len(payload) == 19:
            info.update(rejoin_type=subtype, dev_eui=payload[5:13][::-1].hex())
        elif subtype == 1 and len(payload) == 24:
            info.update(rejoin_type=subtype, dev_eui=payload[10:18][::-1].hex())
        else:
            return unknown
        # Both rejoin formats use a different counter; no counter decoding here.
    elif len(payload) < 5:
        return unknown
    return info


async def store_frame(db, raw) -> None:
    header = decode_header(raw.payload)
    signal = raw.signal
    await db.execute(
        """INSERT INTO lorawan_frames
           (timestamp, if_chain, frequency_mhz, spreading_factor, bandwidth_khz,
            coding_rate, rssi, snr, frame_type, header_json, payload)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (raw.timestamp.isoformat(), raw.if_chain, signal.frequency_mhz,
         signal.spreading_factor, signal.bandwidth_khz, signal.coding_rate,
         signal.rssi, signal.snr, header["frame_type"],
         json.dumps(header, separators=(",", ":")), raw.payload),
    )
    await db.commit()
    logger.info("PASSIVE LORAWAN if=%s freq=%.4f SF%s type=%s size=%d",
                raw.if_chain, signal.frequency_mhz, signal.spreading_factor,
                header["frame_type"], len(raw.payload))


async def prune_frames(db) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    await db.execute("DELETE FROM lorawan_frames WHERE timestamp < ?", (cutoff,))
    await db.execute(
        """DELETE FROM lorawan_frames WHERE id <=
           (SELECT id FROM lorawan_frames ORDER BY id DESC LIMIT 1 OFFSET ?)""",
        (MAX_FRAMES,),
    )
    await db.commit()
