"""Periodic Meshtastic POSITION broadcaster."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Optional

from src.transmit.broadcast_interval import (
    BroadcastIntervalController,
    clamp_interval_minutes,
)
from src.transmit.tx_service import TxService

logger = logging.getLogger(__name__)


class PositionBroadcaster:
    """Schedules POSITION broadcasts when coordinates are available."""

    def __init__(
        self,
        tx_service: TxService,
        *,
        interval_minutes: int = 15,
        startup_delay_seconds: int = 180,
        coords_provider: Callable[[], tuple[float, float, float | None] | None],
        raw_coords_provider: Callable[[], tuple[float, float, float | None] | None] | None = None,
        mqtt_publisher=None,
    ):
        self._tx = tx_service
        self._coords_provider = coords_provider
        self._raw_coords_provider = raw_coords_provider
        self._mqtt_publisher = mqtt_publisher
        self._task: Optional[asyncio.Task] = None
        self._running = False
        interval_seconds = clamp_interval_minutes(
            interval_minutes,
            field_name="transmit.position.interval_minutes",
        ) * 60
        self._schedule = BroadcastIntervalController(
            startup_delay_seconds=startup_delay_seconds,
            interval_seconds=interval_seconds,
            field_name="transmit.position.interval_minutes",
        )

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def interval_seconds(self) -> int:
        return self._schedule.interval_seconds

    @property
    def startup_delay_seconds(self) -> int:
        return self._schedule.startup_delay_seconds

    @property
    def last_sent_at(self) -> Optional[datetime]:
        return self._schedule.last_sent_at

    @property
    def next_due_at(self) -> Optional[datetime]:
        return self._schedule.next_due_at(running=self._running)

    def set_interval(self, minutes: int) -> int:
        return self._schedule.set_interval(minutes)

    async def start(self) -> None:
        if self.is_running:
            return
        self._running = True
        self._schedule.begin()
        self._task = asyncio.create_task(self._loop(), name="position-broadcaster")
        logger.info(
            "Position broadcaster scheduled: first TX in %ds, interval %ds",
            self._schedule.startup_delay_seconds,
            self._schedule.interval_seconds,
        )

    async def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        finally:
            self._task = None

    async def _loop(self) -> None:
        try:
            await self._schedule.run_loop(
                is_running=lambda: self._running,
                on_due=self._broadcast_once,
                loop_name="Position broadcaster",
            )
        except Exception:
            logger.exception("Position broadcaster loop crashed")

    async def _broadcast_once(self) -> None:
        # Hidden/unavailable coordinates must still advance the attempt schedule.
        self._schedule.mark_attempt()
        sent_any = False

        coords = self._coords_provider()
        if coords is not None:
            lat, lon, alt = coords
            result = await self._tx.send_position(lat, lon, alt)
            if result.success:
                sent_any = True
                logger.info(
                    "Position broadcast OK: id=%s airtime=%dms",
                    result.packet_id,
                    result.airtime_ms,
                )
            else:
                logger.warning("Position broadcast skipped: %s", result.error)
        else:
            logger.debug("Public position broadcast skipped: no coordinates")

        private_count = self._publish_private_positions()
        if private_count:
            sent_any = True
            logger.info(
                "Private MQTT POSITION published on %d channel(s)",
                private_count,
            )

        if sent_any:
            self._schedule.mark_sent()

    def _publish_private_positions(self) -> int:
        """Generate this node's private-channel POSITION directly for MQTT."""
        mqtt = self._mqtt_publisher
        if mqtt is None or self._raw_coords_provider is None:
            return 0
        if not hasattr(mqtt, "private_position_targets"):
            return 0

        targets = mqtt.private_position_targets()
        if not targets:
            return 0

        coords = self._raw_coords_provider()
        if coords is None:
            logger.debug("Private MQTT POSITION skipped: no raw coordinates")
            return 0
        lat, lon, alt = coords

        if not hasattr(self._tx, "build_position_packet"):
            logger.warning(
                "Private MQTT POSITION skipped: TX service cannot build packets"
            )
            return 0

        published = 0
        for channel_index, channel_name, precision_bits in targets:
            built = self._tx.build_position_packet(
                lat,
                lon,
                alt,
                channel=channel_index,
                precision_bits=precision_bits,
            )
            if built is None:
                logger.warning(
                    "Private MQTT POSITION build failed: channel=%s",
                    channel_name,
                )
                continue
            raw_packet, _packet_id, _channel_hash = built
            if mqtt.publish_own_private_position(
                raw_packet,
                channel_name=channel_name,
                precision_bits=precision_bits,
            ):
                published += 1
        return published
