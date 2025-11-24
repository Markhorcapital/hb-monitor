# HB-Monitor - Hummingbot MQTT Monitoring Service

## Overview
HB-Monitor is a Python service that monitors Hummingbot trading bot instances via MQTT and sends real-time alerts to Telegram for critical events like bot crashes, drawdowns, start/stop events, and errors.

## Architecture

### Components
1. **main.py** - Core monitoring service with MQTT client and event handlers
2. **alerts.py** - Telegram alert manager with message formatting
3. **logger.py** - Logging configuration
4. **config.yml** - Production configuration
5. **config.local.yml** - Local development configuration
6. **config.example.yml** - Template configuration

### MQTT Topics Monitored
The service subscribes to these Hummingbot MQTT topics:
- `hbot/+/log` - Log messages (INFO, WARNING, ERROR)
- `hbot/+/notify` - Notifications
- `hbot/+/status_updates` - Bot status changes (start/stop)
- `hbot/+/events` - Internal events
- `hbot/+/hb` - Heartbeat messages

## Critical Event Detection

### 1. Drawdown Events (TWO TYPES)

#### Global Drawdown (CRITICAL - ERROR Level)
**Source Message:** `"Global drawdown reached. Stopping the strategy."`
**Location in Hummingbot:** `hummingbot/scripts/v2_with_controllers.py:86`

**What it means:**
- Combined PnL of ALL controllers has exceeded max global drawdown threshold
- ENTIRE strategy is being shut down
- ALL controllers stop immediately
- Bot application stops

**Alert Format:**
```
🚨 GLOBAL DRAWDOWN REACHED

Container: PMM_GATE_200bp-20251113-0800
Level: CRITICAL
Type: Global Strategy Drawdown

⚠️ The entire strategy has reached max global drawdown.
All controllers are being stopped.

Details: Global drawdown reached. Stopping the strategy.

Source: agent/PMM_GATE_200bp-20251113-0800/log
Time: 2025-11-19 17:46:31
```

**Detection Logic (main.py:285-304):**
```python
elif "global drawdown reached" in message_lower:
    event_key = f"{bot_id}:global_drawdown"
    if not self._is_duplicate(event_key):
        # Send ERROR level alert
```

#### Controller Drawdown (WARNING Level)
**Source Message:** `"Controller {controller_id} reached max drawdown. Stopping the controller."`
**Location in Hummingbot:** `hummingbot/scripts/v2_with_controllers.py:67`

**What it means:**
- Individual controller has exceeded its max drawdown threshold
- ONLY that specific controller stops
- Other controllers continue running
- Bot remains active

**Alert Format:**
```
⚠️ Controller Drawdown Reached

Container: PMM_GATE_200bp-20251113-0800
Controller: bearish_gate_200bp_0.1
Level: WARNING
Type: Controller Drawdown

This controller has reached max drawdown and is being stopped.
Other controllers may continue running.

Details: Controller bearish_gate_200bp_0.1 reached max drawdown. Stopping the controller.

Source: agent/PMM_GATE_200bp-20251113-0800/log
Time: 2025-11-19 15:48:52
```

**Detection Logic (main.py:306-335):**
```python
elif "controller" in message_lower and "reached max drawdown" in message_lower:
    # Extract controller ID from message
    controller_id = message.split("Controller ")[1].split(" reached")[0].strip()
    event_key = f"{bot_id}:controller_drawdown:{controller_id}"
    # Send WARNING level alert
```

### 2. Bot Start/Stop Events

#### Bot Started (INFO Level)
**Source:** `hbot/{bot_id}/status_updates` channel
**Trigger:** Status message contains "online", "started", "running", or "booted"

**Alert Format:**
```
✅ Agent Started

Container: PMM_GATE_200-20251119-1747
Status: online
Type: availability

Agent is now running.

Source: agent/PMM_GATE_200-20251119-1747/status_updates
Time: 2025-11-19 17:47:12
```

**Detection Logic (main.py:456-459):**
```python
elif normalized_status == "online" and old_status != "online":
    should_alert = True
    severity = "INFO"
```

#### Bot Stopped (WARNING Level)
**Source:** Two possible sources:
1. `hbot/{bot_id}/status_updates` - Status change to offline
2. `hbot/{bot_id}/log` - "Strategy stopped successfully" message

**Alert Format:**
```
🛑 Agent Stopped

Container: PMM_GATE_200bp-20251113-0800
Status: offline
Detail: Strategy stopped successfully.

Source: agent/PMM_GATE_200bp-20251113-0800/log
Time: 2025-11-19 17:46:31
```

**Detection Logic:**
- Status updates (main.py:450-453)
- Log messages (main.py:266-283)

### 3. Heartbeat Timeout (WARNING Level)

**Trigger:** No heartbeat received for > 300 seconds (5 minutes)
**Check Interval:** Every 60 seconds

**Alert Format:**
```
⚠️ Agent Heartbeat Timeout

Container: CRYPTO_BEARISH_800_500-20251114-1027
Last heartbeat: 5.5 minutes ago
Status: unknown

Agent may have crashed or network issue.

Source: agent/+/hb (timeout)
Time: 2025-11-18 10:03:01
```

**Detection Logic (main.py:541-590):**
```python
async def _check_heartbeats(self):
    timeout = self.monitoring.get("heartbeat_timeout", 300)
    for bot_id, last_heartbeat in list(self.bot_heartbeats.items()):
        if current_time - last_heartbeat > timeout:
            # Send timeout alert
```

## Key Fixes Applied (Nov 24, 2025)

### Issue 1: Status Updates Were Being Blocked
**Problem:** Regex filter was applied to status_updates channel, preventing start/stop alerts
**Solution:** Removed regex filter check from `_handle_status()` - status changes now ALWAYS alert

**Code Change (main.py:467-479):**
```python
# BEFORE: Status updates were filtered by regex
if self._passes_regex_filter(status_filter_payload):
    if not self._is_duplicate(event_key):
        await self.alert_manager.send_alert(...)

# AFTER: Status updates always alert (only check duplicates)
event_key = f"{bot_id}:status:{normalized_status}:{status_type}"
if not self._is_duplicate(event_key):
    await self.alert_manager.send_alert(...)
```

### Issue 2: Only One Type of Drawdown Was Detected
**Problem:** Code didn't distinguish between Global and Controller drawdown
**Solution:** Added separate detection logic for both types with distinct alert formats

### Issue 3: Deduplication Too Aggressive
**Problem:** 300-second window prevented rapid event detection
**Solution:** Reduced to 180 seconds (3 minutes)

### Issue 4: Event Keys Not Normalized
**Problem:** Inconsistent event keys caused duplicate detection to fail
**Solution:** Standardized event key format:
- Stop: `{bot_id}:status:offline:stopped`
- Start: `{bot_id}:status:online:started`
- Global Drawdown: `{bot_id}:global_drawdown`
- Controller Drawdown: `{bot_id}:controller_drawdown:{controller_id}`

## Configuration

### Critical Settings

#### filters.alert_keywords
Keywords that trigger alerts when found in messages:
```yaml
alert_keywords:
  - "drawdown"
  - "reached max drawdown"
  - "Global drawdown reached"
  - "Controller"  # IMPORTANT: Added to catch controller events
  - "stopping the strategy"
  - "stopping the controller"
  - "error"
  - "failed"
  - "exception"
  - "stopped"
  - "crashed"
```

#### filters.log_filter.pattern
Regex pattern for log channel filtering (case-insensitive):
```yaml
log_filter:
  pattern: "(drawdown|draw\\s*down|max drawdown|global drawdown|controller.*drawdown|stopping the strategy|stopping the controller|strategy stopped successfully|clock stopped successfully|bot stopped|bot started|error|failed|exception|crashed)"
```

**IMPORTANT:** This pattern ONLY applies to `hbot/+/log` channel. Status updates (`hbot/+/status_updates`) are NOT filtered by this pattern.

#### filters.deduplication_window
Time window (seconds) to prevent duplicate alerts:
```yaml
deduplication_window: 180  # 3 minutes (reduced from 300)
```

#### monitoring.heartbeat_timeout
Seconds without heartbeat before timeout alert:
```yaml
heartbeat_timeout: 300  # 5 minutes
```

## Event Flow

### 1. Log Message Flow
```
Hummingbot Bot
  └─> MQTT Publish: hbot/{bot_id}/log
       └─> HB-Monitor: _handle_log()
            ├─> Check: _should_process_bot()
            ├─> Check: _silence_after_stop()
            ├─> Check: _should_alert() [includes regex filter]
            ├─> Detect: Global/Controller/Generic drawdown
            ├─> Detect: Strategy stopped
            ├─> Check: _is_duplicate()
            └─> Send: AlertManager.send_alert()
                 └─> Telegram API
```

### 2. Status Update Flow
```
Hummingbot Bot
  └─> MQTT Publish: hbot/{bot_id}/status_updates
       └─> HB-Monitor: _handle_status()
            ├─> Check: _should_process_bot()
            ├─> Normalize: online/offline/unknown
            ├─> Detect: Status change (offline→online or online→offline)
            ├─> Check: _is_duplicate() [NO regex filter!]
            └─> Send: AlertManager.send_alert()
                 └─> Telegram API
```

### 3. Heartbeat Flow
```
Hummingbot Bot
  └─> MQTT Publish: hbot/{bot_id}/hb (every 60s)
       └─> HB-Monitor: _handle_heartbeat()
            └─> Update: bot_heartbeats[bot_id] = current_time

Background Task: _heartbeat_checker() (every 60s)
  └─> For each bot:
       ├─> Check: current_time - last_heartbeat > 300s?
       ├─> Check: _is_duplicate()
       └─> Send: Timeout alert if exceeded
```

## State Management

### Bot State Tracking
```python
self.bot_heartbeats: Dict[str, float]      # Last heartbeat timestamp per bot
self.bot_statuses: Dict[str, str]          # Current status: online/offline/unknown
self.processed_events: Dict[str, float]    # Event deduplication tracker
self.bot_offline_since: Dict[str, float]   # Timestamp when bot went offline
self.heartbeat_alerted: Set[str]           # Bots that have heartbeat timeout alert
```

### Post-Stop Silence
When a bot stops, further alerts are silenced to prevent noise:
```python
def _silence_after_stop(self, bot_id: str, timestamp: Optional[float] = None) -> bool:
    silence_from = self.bot_offline_since.get(bot_id)
    if silence_from is None:
        return False
    message_ts = self._normalize_timestamp(timestamp)
    return message_ts >= silence_from
```

Grace period: `monitoring.post_stop_silence_grace` (default: 0 seconds)

## Alert Message Format

### Pre-Formatted Messages
Messages starting with emojis (🛑, ✅, ⚠️, 💥, 🚨, etc.) are considered pre-formatted and sent as-is with only source/timestamp appended.

### Auto-Formatted Messages
Messages without emojis get formatted with:
- Emoji based on level (ERROR=🔴, WARNING=🟡, INFO=ℹ️)
- Title based on alert_type
- Agent/Container name
- Level
- Source (with aliases applied: "hbot/" → "agent/")
- Timestamp
- Message content

### Markdown Escaping
When `use_markdown: true`, special characters are escaped:
- `\` → `\\`
- `_` → `\_`
- `*` → `\*`
- `[` → `\[`
- `]` → `\]`
- `(` → `\(`
- `)` → `\)`
- `` ` `` → `` \` ``

## Deployment

### Docker Deployment
```bash
cd /path/to/hb-monitor
docker-compose up -d
docker-compose logs -f hb-monitor
```

### Standalone Python
```bash
cd /path/to/hb-monitor
python main.py
```

### Environment Variables
- `CONFIG_PATH` - Path to config file (default: "config.yml")

## Testing

### Test Stop Event
```bash
# In hummingbot container
stop
# Expected: 🛑 Agent Stopped alert in Telegram
```

### Test Start Event
```bash
# In hummingbot container
start
# Expected: ✅ Agent Started alert in Telegram
```

### Test Heartbeat Timeout
```bash
# Stop MQTT bridge in hummingbot
mqtt stop
# Wait 5+ minutes
# Expected: ⚠️ Agent Heartbeat Timeout alert
```

### Test Controller Drawdown
```bash
# Wait for controller to hit max drawdown
# Expected: ⚠️ Controller Drawdown Reached alert
```

### Test Global Drawdown
```bash
# Wait for global PnL to hit max drawdown
# Expected: 🚨 GLOBAL DRAWDOWN REACHED alert
```

## Troubleshooting

### No Alerts Received

1. **Check MQTT Connection**
```bash
docker-compose logs hb-monitor | grep "Connected to MQTT"
```

2. **Check Telegram Config**
```yaml
alerts:
  telegram:
    enabled: true
    bot_token: "YOUR_BOT_TOKEN"
    chat_id: "YOUR_CHAT_ID"
```

3. **Check Subscriptions**
```bash
docker-compose logs hb-monitor | grep "Subscribed to"
```

4. **Check Filters**
- Verify `alert_keywords` includes relevant terms
- Verify `log_filter.pattern` includes event keywords
- Check `deduplication_window` isn't too long

### Duplicate Alerts

- Increase `deduplication_window` in config.yml
- Check event key generation in code

### Missing Drawdown Alerts

1. Verify keywords in config:
```yaml
alert_keywords:
  - "drawdown"
  - "reached max drawdown"
  - "Global drawdown reached"
  - "Controller"
```

2. Verify regex pattern:
```yaml
log_filter:
  pattern: "(drawdown|draw\\s*down|max drawdown|global drawdown|controller.*drawdown|...)"
```

3. Check log level:
```yaml
log_levels: ["ERROR", "WARNING", "INFO"]
```

### Missing Start/Stop Alerts

- Verify status_updates subscription is active
- Check that regex filter is NOT blocking status updates (fixed in Nov 24 update)
- Verify bot is publishing to `hbot/{bot_id}/status_updates`

## Code Review Checklist

### ✅ Event Detection
- [x] Global drawdown detection (line 285-304)
- [x] Controller drawdown detection (line 306-335)
- [x] Generic drawdown fallback (line 337-351)
- [x] Bot stop detection (line 266-283, 450-453)
- [x] Bot start detection (line 456-459)
- [x] Heartbeat timeout detection (line 541-590)

### ✅ Filtering Logic
- [x] Status updates NOT filtered by regex (line 467-479)
- [x] Log messages filtered by regex (line 261)
- [x] Post-stop silence (line 253, 319, 390, 518)
- [x] Deduplication (line 219-234)

### ✅ State Management
- [x] Heartbeat tracking (line 498-501)
- [x] Status tracking (line 483-487)
- [x] Offline tracking (line 144-148, 283, 485)
- [x] Online tracking (line 150-154, 487)

### ✅ Alert Formatting
- [x] Pre-formatted message detection (alerts.py:98)
- [x] Markdown escaping (alerts.py:34-51)
- [x] Source aliasing (alerts.py:23-32)
- [x] Timestamp formatting (alerts.py:64)

### ✅ Configuration
- [x] Reduced deduplication window to 180s
- [x] Added "Controller" to alert_keywords
- [x] Updated regex pattern for both drawdown types
- [x] Documented that log_filter only applies to log channel

## Green Light Status: ✅ APPROVED

All critical fixes have been applied and verified:
1. ✅ Status updates always alert (no regex blocking)
2. ✅ Both Global and Controller drawdown detected with distinct formats
3. ✅ Deduplication window optimized (180s)
4. ✅ Event keys normalized for consistent duplicate detection
5. ✅ All config files updated (config.yml, config.local.yml, config.example.yml)
6. ✅ Code reviewed and validated

**Ready for production deployment.**

