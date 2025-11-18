"""
Alert handlers for sending notifications via Telegram.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class AlertManager:
    """Manages sending alerts via Telegram."""
    
    def __init__(self, alert_config: dict):
        self.config = alert_config
        self.telegram_config = alert_config.get("telegram", {})
        self.source_aliases = self.telegram_config.get("source_aliases", {})

    def _alias_source(self, source: Optional[str]) -> str:
        """Apply optional source alias replacements."""
        if source is None:
            return "N/A"
        alias_source = str(source)
        if isinstance(self.source_aliases, dict):
            for original, replacement in self.source_aliases.items():
                if original and alias_source.startswith(original):
                    alias_source = replacement + alias_source[len(original):]
        return alias_source
        
    def _escape_markdown(self, text: str) -> str:
        """Escape characters that break Telegram Markdown (v1)."""
        if text is None:
            return ""
        replacements = {
            "\\": "\\\\",
            "_": "\\_",
            "*": "\\*",
            "[": "\\[",
            "]": "\\]",
            "(": "\\(",
            ")": "\\)",
            "`": "\\`",
        }
        escaped = []
        for ch in str(text):
            escaped.append(replacements.get(ch, ch))
        return "".join(escaped)

    def _format_message(
        self,
        bot_id: str,
        alert_type: str,
        message: str,
        level: str = "INFO",
        timestamp: Optional[float] = None,
        source: Optional[str] = None,
        use_markdown: bool = True,
    ) -> str:
        """Format alert message."""
        timestamp_str = datetime.fromtimestamp(timestamp or 0).strftime("%Y-%m-%d %H:%M:%S") if timestamp else "N/A"
        source_str = self._alias_source(source)
        bot_id_str = bot_id
        alert_type_str = alert_type
        message_str = message
        source_fmt = source_str
        if use_markdown:
            bot_id_str = self._escape_markdown(bot_id_str)
            alert_type_str = self._escape_markdown(alert_type_str)
            message_str = self._escape_markdown(message_str)
            source_fmt = self._escape_markdown(source_fmt)
        else:
            # keep original values for plain text output
            bot_id_str = bot_id
            alert_type_str = alert_type
            message_str = message
            source_fmt = source_str

        emoji_level_map = {
            "ERROR": "🔴",
            "WARNING": "🟡",
            "INFO": "ℹ️",
        }
        emoji_type_map = {
            "log": "📝",
            "status": "📊",
            "event": "⚡",
            "notification": "🔔",
            "heartbeat_timeout": "💔",
        }
        
        emoji = emoji_level_map.get(level) or emoji_type_map.get(alert_type) or "ℹ️"
        
        # If message already contains emoji at start, it's pre-formatted - use as-is
        if message_str.strip().startswith(("🛑", "✅", "⚠️", "💥", "🔴", "🟡", "ℹ️", "📝", "📊", "⚡", "🔔", "💔")):
            # Message is already formatted, just add source / timestamp if not present
            extras = []
            if source and "*Source:*" not in message_str:
                source_line = f"*Source:* `{source_fmt}`" if use_markdown else f"Source: {source_fmt}"
                extras.append(source_line)
            if timestamp_str not in message_str:
                extras.append(f"*Time:* {timestamp_str}")
            if extras:
                return f"{message_str}\n\n" + "\n".join(extras)
            return message_str
        
        title_map = {
            "ERROR": "Critical Alert",
            "WARNING": "Warning",
            "INFO": "Information",
        }
        type_title_map = {
            "event": "Event Alert",
            "notification": "Notification",
            "status": "Status",
            "heartbeat_timeout": "Heartbeat Timeout",
        }

        title = type_title_map.get(alert_type, title_map.get(level, "Alert"))

        if not use_markdown:
            sections = [
                f"{emoji} {title}",
                f"Agent: {bot_id_str}",
            ]
            if alert_type != "status":
                sections.append(f"Type: {alert_type_str}")
            if level:
                sections.append(f"Level: {level}")
            sections.append(f"Source: {source_fmt}")
            sections.append(f"Time: {timestamp_str}")
            sections.append(f"\nMessage:\n{message_str}")
            return "\n".join(sections)

        source_line = f"*Source:* `{source_fmt}`"
        header_lines = [
            f"{emoji} *{title}*",
            "",
            f"*Agent:* `{bot_id_str}`",
        ]
        if alert_type != "status":
            header_lines.append(f"*Type:* {alert_type_str}")
        if level:
            header_lines.append(f"*Level:* {level}")
        header_lines.extend([
            source_line,
            f"*Time:* {timestamp_str}",
            "",
            "*Message:*",
            message_str,
        ])

        return "\n".join(header_lines)
    
    async def _send_telegram(self, message: str):
        """Send alert via Telegram."""
        if not self.telegram_config.get("enabled", False):
            return
        
        bot_token = self.telegram_config.get("bot_token")
        chat_id = self.telegram_config.get("chat_id")
        use_markdown = self.telegram_config.get("use_markdown", True)
        
        if not bot_token or not chat_id:
            logger.warning("Telegram not configured properly")
            return
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        params = {
            "chat_id": chat_id,
            "text": message,
        }
        if use_markdown:
            params["parse_mode"] = "Markdown"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        logger.info("Telegram alert sent successfully")
                    else:
                        error_text = await response.text()
                        logger.error(f"Telegram API error: {response.status} - {error_text}")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
    
    async def send_alert(
        self,
        bot_id: str,
        alert_type: str,
        message: str,
        level: str = "INFO",
        timestamp: Optional[float] = None,
        source: Optional[str] = None,
    ):
        """Send alert via Telegram."""
        use_markdown = self.telegram_config.get("use_markdown", True)
        formatted_message = self._format_message(
            bot_id,
            alert_type,
            message,
            level,
            timestamp,
            source,
            use_markdown=use_markdown,
        )
        await self._send_telegram(formatted_message)
        logger.info(f"Alert sent for {bot_id}: {alert_type}")
