#!/usr/bin/env python3
"""
Hummingbot MQTT Monitor & Alert Service

Monitors Hummingbot instances via MQTT and sends alerts for critical events.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set
from collections import defaultdict

import aiomqtt
import yaml

from alerts import AlertManager
from logger import setup_logger

logger = logging.getLogger(__name__)


class HummingbotMonitor:
    """Main monitoring service that subscribes to MQTT and processes events."""
    
    def __init__(self, config: dict):
        self.config = config
        self.mqtt_config = config.get("mqtt", {})
        self.alert_manager = AlertManager(config.get("alerts", {}))
        self.filters = config.get("filters", {})
        self.monitoring = config.get("monitoring", {})
        # Pre-compile an optional regex used to further filter log-topic alerts.
        # This lets us keep subscriptions dynamic while suppressing noisy entries that
        # are not actionable (e.g. routine websocket reconnects).
        log_filter_cfg = self.filters.get("log_filter", {})
        pattern = log_filter_cfg.get("pattern", "")
        self.log_alert_pattern = re.compile(pattern, re.IGNORECASE) if pattern else None
        console_trade_cfg = self.monitoring.get("console_trade_filter", {})
        self.suppress_trade_console_logs = console_trade_cfg.get("suppress", True)
        trade_keywords = console_trade_cfg.get(
            "keywords",
            [
                "order",
                "trade",
                "filled",
                "position",
                "budget",
                "buy",
                "sell",
                "rate oracle",
                "user stream",
                "websocket",
                "Listen key",
                "Executor ID",
                "trading",
                "instruments",
                "Subscribed",

            ],
        )
        self.trade_console_keywords: Set[str] = {
            keyword.lower() for keyword in trade_keywords if isinstance(keyword, str) and keyword
        }
        pattern_str = console_trade_cfg.get("pattern")
        self.trade_console_pattern = (
            re.compile(pattern_str, re.IGNORECASE) if pattern_str else None
        )
        
        # Bot state tracking
        self.bot_heartbeats: Dict[str, float] = {}
        self.bot_statuses: Dict[str, str] = {}
        self.processed_events: Dict[str, float] = {}
        self.bot_offline_since: Dict[str, float] = {}
        self.heartbeat_alerted: Set[str] = set()
        
        # MQTT connection
        self.client: Optional[aiomqtt.Client] = None
        self.connected = False
        self.reconnect_interval = self.mqtt_config.get("reconnect_interval", 5)
        
        # Subscriptions
        default_subscriptions = [
            ("hbot/+/log", 1),
            ("hbot/+/notify", 1),
            ("hbot/+/status_updates", 1),
            ("hbot/+/events", 1),
            ("hbot/+/hb", 1),  # Heartbeat
        ]

        configured_subscriptions = self.config.get("subscriptions", [])
        if configured_subscriptions:
            parsed_subscriptions = []
            for entry in configured_subscriptions:
                if isinstance(entry, dict):
                    topic = entry.get("topic")
                    qos = entry.get("qos", 1)
                elif isinstance(entry, (list, tuple)) and len(entry) >= 1:
                    topic = entry[0]
                    qos = entry[1] if len(entry) > 1 else 1
                else:
                    topic = entry
                    qos = 1

                if topic:
                    parsed_subscriptions.append((str(topic), int(qos)))

            self.subscriptions = parsed_subscriptions or default_subscriptions
        else:
            self.subscriptions = default_subscriptions
        
    def _should_process_bot(self, bot_id: str) -> bool:
        """Check if we should process events for this bot."""
        allowed_bots = self.filters.get("bot_ids", [])
        if allowed_bots:
            return bot_id in allowed_bots
        return True

    def _normalize_timestamp(self, ts: Optional[float]) -> float:
        """Normalize timestamps to seconds."""
        if ts is None:
            return time.time()
        try:
            ts_float = float(ts)
        except (TypeError, ValueError):
            return time.time()
        if ts_float > 1e10:
            return ts_float / 1000.0
        return ts_float

    def _silence_after_stop(self, bot_id: str, timestamp: Optional[float] = None) -> bool:
        """Determine if messages for this bot should be silenced after stop."""
        silence_from = self.bot_offline_since.get(bot_id)
        if silence_from is None:
            return False
        message_ts = self._normalize_timestamp(timestamp)
        return message_ts >= silence_from

    def _record_offline(self, bot_id: str, timestamp: Optional[float] = None):
        """Mark bot as offline to silence future alerts until it restarts."""
        base_ts = self._normalize_timestamp(timestamp)
        grace = self.monitoring.get("post_stop_silence_grace", 0)
        self.bot_offline_since[bot_id] = base_ts + grace

    def _record_online(self, bot_id: str):
        """Clear offline state when bot restarts."""
        self.bot_offline_since.pop(bot_id, None)
        self.heartbeat_alerted.discard(bot_id)
        self.processed_events.pop(f"{bot_id}:heartbeat_timeout", None)
    
    def _passes_regex_filter(self, message: str) -> bool:
        """Check whether the configured regex (if any) allows this message."""
        if self.log_alert_pattern is None:
            return True
        if message is None:
            message = ""
        if not isinstance(message, str):
            message = str(message)
        return bool(self.log_alert_pattern.search(message))
    
    def _should_alert(self, message: str, level: str = "INFO", source: Optional[str] = None) -> bool:
        """Determine if a message should trigger an alert."""
        if message is None:
            message = ""
        if not isinstance(message, str):
            message = str(message)

        pattern_active = self.log_alert_pattern is not None
        if pattern_active and not self._passes_regex_filter(message):
            return False
        pattern_matched = pattern_active

        # Only enforce log level filtering for log channel payloads
        if source and source.endswith("/log"):
            log_levels = self.filters.get("log_levels", [])
            if log_levels and level not in log_levels:
                return False

            # If the regex allow-list matched, we do not require additional keywords
            if pattern_matched:
                return True

        # Check for alert keywords (works for all channels)
        alert_keywords = self.filters.get("alert_keywords", [])
        if not alert_keywords:
            # No keywords configured and (optionally) regex matched -> allow alert
            return True

        message_lower = message.lower()
        for keyword in alert_keywords:
            if keyword.lower() in message_lower:
                ignore_keywords = self.filters.get("ignore_keywords", [])
                if any(ignore.lower() in message_lower for ignore in ignore_keywords):
                    continue
                return True
        
        # If the regex matched but no keywords are configured that match, allow alert
        return pattern_matched

    def _is_trade_console_log(self, message: Optional[str]) -> bool:
        """Determine whether a log message should be suppressed for console output."""
        if not self.suppress_trade_console_logs:
            return False
        if not message:
            return False
        normalized = message if isinstance(message, str) else str(message)
        normalized_lower = normalized.lower()
        if self.trade_console_pattern and self.trade_console_pattern.search(normalized):
            return True
        if self.trade_console_keywords:
            return any(keyword in normalized_lower for keyword in self.trade_console_keywords)
        return False
    
    def _is_duplicate(self, event_key: str, custom_window: Optional[int] = None) -> bool:
        """Check if we've already processed this event recently."""
        window = custom_window if custom_window is not None else self.filters.get("deduplication_window", 300)
        current_time = time.time()
        
        if event_key in self.processed_events:
            if current_time - self.processed_events[event_key] < window:
                return True
        
        self.processed_events[event_key] = current_time
        # Cleanup old entries
        cutoff = current_time - window * 2
        self.processed_events = {
            k: v for k, v in self.processed_events.items() if v > cutoff
        }
        return False
    
    async def _handle_log(self, bot_id: str, data: dict, topic: str):
        """Handle log messages from bots - PRIMARY source for drawdown and error detection."""
        if not self._should_process_bot(bot_id):
            return
        
        try:
            # Parse log message
            if isinstance(data, dict):
                level = data.get("level_name", "INFO")
                message = data.get("msg", str(data))
                timestamp = data.get("timestamp", time.time())
            else:
                level = "INFO"
                message = str(data)
                timestamp = time.time()
            normalized_ts = self._normalize_timestamp(timestamp)

            if self._silence_after_stop(bot_id, normalized_ts):
                logger.debug(f"[{bot_id}] LOG suppressed post-stop: {message}")
                return
            
            # Bot ID is typically the container name
            container_name = bot_id
            
            # Check if this should trigger an alert
            if self._should_alert(message, level, source=topic):
                # Format alert message with container name
                message_lower = message.lower()

                # Treat clean shutdown logs as a status alert to keep formatting consistent
                if "strategy stopped successfully" in message_lower or "bot stopped" in message_lower:
                    event_key = f"{bot_id}:status:offline:stopped"
                    if not self._is_duplicate(event_key):
                        stop_message = (
                            "ℹ️ Agent Stopped\n\n"
                            f"Container: {container_name}\n"
                            "Status: offline\n"
                            "Detail: Strategy stopped successfully.\n"
                        )
                        await self.alert_manager.send_alert(
                            bot_id=container_name,
                            alert_type="status",
                            level="INFO",
                            message=stop_message,
                            timestamp=timestamp,
                            source=topic
                        )
                        self._record_offline(bot_id, normalized_ts)
                # Detect GLOBAL drawdown events (highest priority - stops entire strategy)
                elif "global drawdown reached" in message_lower:
                    event_key = f"{bot_id}:global_drawdown"
                    if not self._is_duplicate(event_key):
                        drawdown_message = (
                            "🚨 GLOBAL DRAWDOWN REACHED\n\n"
                            f"Container: {container_name}\n"
                            f"Level: CRITICAL\n"
                            "Type: Global Strategy Drawdown\n\n"
                            "⚠️ The entire strategy has reached max global drawdown.\n"
                            "All controllers are being stopped.\n\n"
                            f"Details: {message}"
                        )
                        await self.alert_manager.send_alert(
                            bot_id=container_name,
                            alert_type="global_drawdown",
                            level="ERROR",
                            message=drawdown_message,
                            timestamp=timestamp,
                            source=topic
                        )
                # Detect CONTROLLER drawdown events (individual controller stopped)
                elif "controller" in message_lower and "reached max drawdown" in message_lower:
                    # Extract controller ID from message like "Controller bearish_gate_200bp_0.1 reached max drawdown"
                    controller_id = "unknown"
                    try:
                        parts = message.split("Controller ")
                        if len(parts) > 1:
                            controller_id = parts[1].split(" reached")[0].strip()
                    except:
                        pass
                    
                    event_key = f"{bot_id}:controller_drawdown:{controller_id}"
                    if not self._is_duplicate(event_key):
                        drawdown_message = (
                            "⚠️ Controller Drawdown Reached\n\n"
                            f"Container: {container_name}\n"
                            f"Controller: {controller_id}\n"
                            f"Level: WARNING\n"
                            "Type: Controller Drawdown\n\n"
                            "This controller has reached max drawdown and is being stopped.\n"
                            "Other controllers may continue running.\n\n"
                            f"Details: {message}"
                        )
                        await self.alert_manager.send_alert(
                            bot_id=container_name,
                            alert_type="controller_drawdown",
                            level="WARNING",
                            message=drawdown_message,
                            timestamp=timestamp,
                            source=topic
                        )
                # Catch any other drawdown-related messages
                elif "drawdown" in message_lower and ("reached" in message_lower or "stopping" in message_lower):
                    event_key = f"{bot_id}:drawdown:{message[:50]}"
                    if not self._is_duplicate(event_key):
                        drawdown_message = (
                            "⚠️ Drawdown Event\n\n"
                            f"Container: {container_name}\n"
                            f"Level: {level}\n\n"
                            f"{message}"
                        )
                        await self.alert_manager.send_alert(
                            bot_id=container_name,
                            alert_type="drawdown",
                            level="WARNING",
                            message=drawdown_message,
                            timestamp=timestamp,
                            source=topic
                        )
                else:
                    alert_message = f"Container: {container_name}\nLevel: {level}\n\n{message}"
                    
                    event_key = f"{bot_id}:log:{message[:100]}"
                    if not self._is_duplicate(event_key):
                        await self.alert_manager.send_alert(
                            bot_id=container_name,
                            alert_type="log",
                            level=level,
                            message=alert_message,
                            timestamp=timestamp,
                            source=topic
                        )
            
            if not self._is_trade_console_log(message):
                logger.info(f"[{bot_id}] LOG {level}: {message}")
            else:
                logger.debug(f"[{bot_id}] Trade log suppressed from console: {message}")
            
        except Exception as e:
            logger.error(f"Error handling log from {bot_id}: {e}")
    
    async def _handle_notify(self, bot_id: str, data: dict, topic: str):
        """Handle notification messages."""
        if not self._should_process_bot(bot_id):
            return
        
        try:
            if isinstance(data, dict):
                message = data.get("msg", str(data))
                timestamp = data.get("timestamp", time.time())
            else:
                message = str(data)
                timestamp = time.time()

            if self._silence_after_stop(bot_id, timestamp):
                logger.debug(f"[{bot_id}] Notification suppressed post-stop: {message}")
                return
            
            # Notifications are usually important
            event_key = f"{bot_id}:notify:{message[:100]}"
            if not self._is_duplicate(event_key):
                await self.alert_manager.send_alert(
                    bot_id=bot_id,
                    alert_type="notification",
                    message=message,
                    timestamp=timestamp,
                    source=topic
                )
            
            logger.info(f"[{bot_id}] Notification: {message}")
            
        except Exception as e:
            logger.error(f"Error handling notify from {bot_id}: {e}")
    
    async def _handle_status(self, bot_id: str, data: dict, topic: str):
        """Handle status updates - PRIMARY source for bot start/stop events."""
        if not self._should_process_bot(bot_id):
            return
        
        try:
            if isinstance(data, dict):
                status_msg = data.get("msg", "")
                status_type = data.get("type", "")
                timestamp = data.get("timestamp", time.time())
            else:
                status_msg = str(data)
                status_type = "unknown"
                timestamp = time.time()
            
            normalized_msg = (status_msg or "").strip()
            status_lower = normalized_msg.lower()
            status_type_lower = (status_type or "").lower()

            # Derive a normalized status bucket for comparison (online/offline/other)
            offline_tokens = ("offline", "stopped", "stop", "shutdown", "terminated")
            online_tokens = ("online", "started", "running", "booted")
            if any(token in status_lower for token in offline_tokens) or status_type_lower in ("stopped", "offline"):
                normalized_status = "offline"
            elif any(token in status_lower for token in online_tokens) or status_type_lower in ("started", "online"):
                normalized_status = "online"
            else:
                normalized_status = status_lower or status_type_lower or "unknown"
            
            # Detect status changes
            old_status = self.bot_statuses.get(bot_id, "unknown")
            severity = "INFO"

            # Bot ID is typically the container name (e.g., "PMM_HTX_200bp-20251110-1317")
            container_name = bot_id
            
            # Detect critical status changes
            should_alert = False
            alert_message = ""
            
            # Bot went offline/stopped - CRITICAL EVENT, always alert
            if normalized_status == "offline" and old_status != "offline":
                should_alert = True
                severity = "WARNING"
                alert_message = f"🛑 Agent Stopped\n\nContainer: {container_name}\nStatus: {status_msg}\nType: {status_type}\n\nAgent is no longer running."
            
            # Bot came online/started - CRITICAL EVENT, always alert
            elif normalized_status == "online" and old_status != "online":
                should_alert = True
                severity = "INFO"
                alert_message = f"✅ Agent Started\n\nContainer: {container_name}\nStatus: {status_msg}\nType: {status_type}\n\nAgent is now running."
            
            # Other critical status changes
            elif any(keyword in status_lower for keyword in ["error", "failed", "crashed"]):
                should_alert = True
                severity = "ERROR"
                alert_message = f"⚠️ Agent Status Change\n\nContainer: {container_name}\nStatus: {status_msg}\nType: {status_type}\n\nCritical status change detected."
            
            if should_alert:
                # Status updates (start/stop) should NOT be filtered by log regex pattern
                # Only check for duplicates
                event_key = f"{bot_id}:status:{normalized_status}:{status_type}"
                if not self._is_duplicate(event_key):
                    await self.alert_manager.send_alert(
                        bot_id=container_name,  # Use container name in alert
                        alert_type="status",
                        message=alert_message,
                        level=severity,
                        timestamp=timestamp / 1000 if timestamp > 1e10 else timestamp,  # Convert ms to seconds if needed
                        source=topic
                    )
                else:
                    logger.debug(f"[{bot_id}] Duplicate status alert suppressed: {status_type} - {status_msg}")
            
            self.bot_statuses[bot_id] = normalized_status
            if normalized_status == "offline":
                self._record_offline(bot_id, timestamp)
            elif normalized_status == "online":
                self._record_online(bot_id)
            logger.info(f"[{bot_id}] Status update: {status_type} - {status_msg}")
            
        except Exception as e:
            logger.error(f"Error handling status from {bot_id}: {e}")
    
    async def _handle_heartbeat(self, bot_id: str, data: dict, topic: str):
        """Handle heartbeat messages."""
        if not self._should_process_bot(bot_id):
            return
        
        self.bot_heartbeats[bot_id] = time.time()
        self.heartbeat_alerted.discard(bot_id)
        self.processed_events.pop(f"{bot_id}:heartbeat_timeout", None)
        logger.debug(f"[{bot_id}] Heartbeat received")
    
    async def _handle_events(self, bot_id: str, data: dict, topic: str):
        """Handle internal events."""
        if not self._should_process_bot(bot_id):
            return
        
        try:
            if isinstance(data, dict):
                event_type = data.get("type", "unknown")
                event_data = data.get("data", {})
                timestamp = data.get("timestamp", time.time())
            else:
                event_type = "unknown"
                event_data = {}
                timestamp = time.time()

            if self._silence_after_stop(bot_id, timestamp):
                logger.debug(f"[{bot_id}] Event suppressed post-stop: {event_type}")
                return
            
            # Check if this is a critical event
            event_str = json.dumps(event_data, default=str)
            if self._should_alert(event_str):
                event_key = f"{bot_id}:event:{event_type}:{event_str[:100]}"
                if not self._is_duplicate(event_key):
                    await self.alert_manager.send_alert(
                        bot_id=bot_id,
                        alert_type="event",
                        message=f"Event: {event_type} - {event_str}",
                        timestamp=timestamp,
                        source=topic
                    )
            
            # Commented out to suppress verbose trade event logs in the console.
            # logger.info(f"[{bot_id}] EVENT {event_type}: {event_str}")
            
        except Exception as e:
            logger.error(f"Error handling event from {bot_id}: {e}")
    
    async def _check_heartbeats(self):
        """Periodically check for missing heartbeats - detects crashes."""
        timeout = self.monitoring.get("heartbeat_timeout", 300)
        current_time = time.time()
        
        for bot_id, last_heartbeat in list(self.bot_heartbeats.items()):
            if bot_id in self.heartbeat_alerted:
                continue
            if current_time - last_heartbeat > timeout:
                # Check if bot is marked as offline (crashed) or just network issue
                bot_status = self.bot_statuses.get(bot_id, "unknown")
                container_name = bot_id
                elapsed_seconds = int(current_time - last_heartbeat)
                elapsed_minutes = elapsed_seconds / 60
                if elapsed_minutes >= 1:
                    last_heartbeat_display = f"{elapsed_minutes:.1f} minutes ago"
                else:
                    last_heartbeat_display = f"{elapsed_seconds} seconds ago"

                if bot_status == "offline":
                    # Bot is offline and no heartbeat = likely crashed
                    alert_message = (
                        "💥 Agent Crashed (No Heartbeat)\n\n"
                        f"Container: {container_name}\n"
                        f"Last heartbeat: {last_heartbeat_display}\n"
                        "Status: Offline\n\n"
                        "Agent appears to have crashed or stopped unexpectedly."
                    )
                else:
                    # Bot might still be running but not sending heartbeats (network issue?)
                    alert_message = (
                        "⚠️ Agent Heartbeat Timeout\n\n"
                        f"Container: {container_name}\n"
                        f"Last heartbeat: {last_heartbeat_display}\n"
                        f"Status: {bot_status}\n\n"
                        "Agent may have crashed or network issue."
                    )
                
                event_key = f"{bot_id}:heartbeat_timeout"
                if not self._is_duplicate(event_key):
                    await self.alert_manager.send_alert(
                        bot_id=container_name,
                        alert_type="heartbeat_timeout",
                        message=alert_message,
                        timestamp=current_time,
                        source="hbot/+/hb (timeout)"
                    )
                    self.heartbeat_alerted.add(bot_id)
                    self._record_offline(bot_id, current_time)
                logger.warning(f"[{bot_id}] Heartbeat timeout")
    
    async def _process_message(self, message):
        """Process incoming MQTT message."""
        try:
            topic = str(message.topic)
            topic_parts = topic.split("/")
            
            # Parse topic: hbot/{bot_id}/{channel}
            if len(topic_parts) >= 3 and topic_parts[0] == "hbot":
                bot_id = topic_parts[1]
                channel = "/".join(topic_parts[2:])
                
                # Parse payload
                try:
                    payload = json.loads(message.payload.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = message.payload.decode("utf-8", errors="ignore")
                
                # Route to appropriate handler
                if channel == "log":
                    await self._handle_log(bot_id, payload, topic)
                elif channel == "notify":
                    await self._handle_notify(bot_id, payload, topic)
                elif channel == "status_updates":
                    await self._handle_status(bot_id, payload, topic)
                elif channel == "hb":
                    await self._handle_heartbeat(bot_id, payload, topic)
                elif channel == "events":
                    await self._handle_events(bot_id, payload, topic)
                else:
                    logger.debug(f"Unknown channel: {channel} from {bot_id}")
                    
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
    
    def _get_client(self):
        """Create and return MQTT client."""
        client_id = f"{self.mqtt_config.get('client_id_prefix', 'hb-monitor')}-{int(time.time())}"
        host = self.mqtt_config.get("host", "emqx")
        port = self.mqtt_config.get("port", 1883)
        username = self.mqtt_config.get("username", "")
        password = self.mqtt_config.get("password", "")
        keepalive = self.mqtt_config.get("keepalive", 60)
        
        if username and password:
            client = aiomqtt.Client(
                hostname=host,
                port=port,
                username=username,
                password=password,
                identifier=client_id,
                keepalive=keepalive,
            )
        else:
            client = aiomqtt.Client(
                hostname=host,
                port=port,
                identifier=client_id,
                keepalive=keepalive,
            )
        
        return client
    
    async def _monitor_loop(self):
        """Main monitoring loop with reconnection."""
        while True:
            try:
                async with self._get_client() as client:
                    self.client = client
                    self.connected = True
                    logger.info(f"Connected to MQTT broker at {self.mqtt_config.get('host')}:{self.mqtt_config.get('port')}")
                    
                    # Subscribe to topics
                    for topic, qos in self.subscriptions:
                        await client.subscribe(topic, qos=qos)
                        logger.info(f"Subscribed to {topic}")
                    
                    # Start heartbeat checker
                    heartbeat_task = asyncio.create_task(self._heartbeat_checker())
                    
                    try:
                        # Process messages
                        async for message in client.messages:
                            await self._process_message(message)
                    finally:
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass
                        
            except aiomqtt.MqttError as e:
                self.connected = False
                logger.error(f"MQTT connection error: {e}. Reconnecting in {self.reconnect_interval}s...")
                await asyncio.sleep(self.reconnect_interval)
            except Exception as e:
                self.connected = False
                logger.error(f"Unexpected error: {e}. Reconnecting in {self.reconnect_interval}s...", exc_info=True)
                await asyncio.sleep(self.reconnect_interval)
    
    async def _heartbeat_checker(self):
        """Periodically check for missing heartbeats."""
        check_interval = self.monitoring.get("heartbeat_check_interval", 60)
        while True:
            try:
                await asyncio.sleep(check_interval)
                await self._check_heartbeats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat checker: {e}")
    
    async def start(self):
        """Start the monitoring service."""
        logger.info("Starting Hummingbot MQTT Monitor...")
        await self._monitor_loop()


def load_config(config_path: str) -> dict:
    """Load configuration from YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def main():
    """Main entry point."""
    # Determine config path
    config_path = os.getenv("CONFIG_PATH", "config.yml")
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        logger.info("Please copy config.example.yml to config.yml and configure it")
        sys.exit(1)
    
    # Load configuration
    config = load_config(config_path)
    
    # Setup logging
    log_config = config.get("monitoring", {})
    setup_logger(
        log_file=log_config.get("log_file", "logs/hb-monitor.log"),
        log_level=log_config.get("log_level", "INFO")
    )
    
    # Create and start monitor
    monitor = HummingbotMonitor(config)
    
    try:
        asyncio.run(monitor.start())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

