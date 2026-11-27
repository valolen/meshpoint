from __future__ import annotations

import json

from fastapi import APIRouter, Query

from src.analytics.signal_analyzer import SignalAnalyzer
from src.analytics.traffic_monitor import TrafficMonitor
from src.storage.packet_repository import PacketRepository

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_signal_analyzer: SignalAnalyzer | None = None
_traffic_monitor: TrafficMonitor | None = None
_packet_repo: PacketRepository | None = None


def init_routes(
    signal_analyzer: SignalAnalyzer,
    traffic_monitor: TrafficMonitor,
    packet_repo: PacketRepository | None = None,
) -> None:
    global _signal_analyzer, _traffic_monitor, _packet_repo
    _signal_analyzer = signal_analyzer
    _traffic_monitor = traffic_monitor
    _packet_repo = packet_repo


@router.get("/traffic")
async def traffic_summary():
    return await _traffic_monitor.get_traffic_summary()


@router.get("/traffic/timeline")
async def traffic_timeline(minutes: int = 60, bucket_minutes: int = 5):
    return await _traffic_monitor.get_recent_activity(minutes, bucket_minutes)


@router.get("/signal/rssi")
async def rssi_distribution():
    return await _signal_analyzer.get_rssi_distribution()


@router.get("/signal/snr")
async def snr_distribution():
    return await _signal_analyzer.get_snr_distribution()


@router.get("/signal/summary")
async def signal_summary():
    return await _signal_analyzer.get_signal_summary()


@router.get("/topology")
async def network_topology(hours: int = Query(24, ge=1, le=168)):
    """Extract node-to-node links from NEIGHBORINFO packets."""
    if not _packet_repo:
        return []

    rows = await _packet_repo._db.fetch_all(
        """
        SELECT source_id, destination_id, packet_type, decoded_payload, rssi, snr, timestamp
        FROM packets
        WHERE packet_type IN ('neighborinfo', 'traceroute') AND decrypted = 1
          AND decoded_payload IS NOT NULL AND julianday(timestamp) >= julianday('now', ?)
        ORDER BY timestamp DESC
        """,
        (f"-{hours} hours",),
    )

    seen_links: dict[str, dict] = {}
    for row in rows:
        source = row["source_id"]
        try:
            payload = json.loads(row["decoded_payload"])
        except (json.JSONDecodeError, TypeError):
            continue

        if row["packet_type"] == "traceroute":
            if not payload.get("request_id"):
                continue
            def add_path(nodes, snrs):
                for i, (src, dst) in enumerate(zip(nodes, nodes[1:])):
                    if not all(isinstance(n, str) and len(n) == 8 and n != "ffffffff" and all(c in "0123456789abcdef" for c in n) for n in (src, dst)) or src == dst:
                        continue
                    if i >= len(snrs) or not isinstance(snrs[i], (int, float)) or snrs[i] == -128:
                        continue
                    key = f"{src}>{dst}"
                    if key not in seen_links:
                        seen_links[key] = {"source": src, "target": dst, "snr": snrs[i] / 4, "rssi": None, "last_seen": row["timestamp"], "evidence": "traceroute"}
            route = payload.get("route", [])
            back = payload.get("route_back")
            if isinstance(route, list):
                add_path([row["destination_id"], *route, source], payload.get("snr_towards") or [])
            if isinstance(back, list):
                add_path([source, *back, row["destination_id"]], payload.get("snr_back") or [])
            continue

        neighbors = payload.get("neighbors", [])
        if isinstance(neighbors, list):
            for neighbor in neighbors:
                nid = neighbor.get("node_id") or neighbor.get("id")
                if not nid:
                    continue
                nid = str(nid)
                link_key = f"{min(source, nid)}_{max(source, nid)}"
                if link_key not in seen_links:
                    seen_links[link_key] = {
                        "source": source,
                        "target": nid,
                        "rssi": None,
                        "snr": neighbor.get("snr"),
                        "evidence": "neighborinfo",
                        "last_seen": row["timestamp"],
                    }

    return list(seen_links.values())
