# hb-monitor Docker Deployment - Summary

## ✅ What Was Done

1. **Created `docker-compose.yml`** - Standalone Docker Compose configuration
   - Uses host network mode to connect to EMQX on `localhost:1883`
   - Mounts config file as read-only for security
   - Persistent logs directory
   - Auto-restart on failure

2. **Updated `Dockerfile`** - Production-ready container
   - Runs as non-root user (`hbmonitor`)
   - Proper permissions for logs directory
   - Security best practices

3. **Updated `config.example.yml`** - Better Docker instructions
   - Clear comments for Docker vs standalone usage

4. **Created `README_DEPLOYMENT.md`** - Comprehensive deployment guide
   - Step-by-step instructions
   - Troubleshooting section
   - Network configuration options

5. **Created `test-local.sh`** - Local testing script
   - Checks dependencies
   - Tests MQTT connection
   - Easy local testing

6. **Updated main `README.md`** - Added Docker quick start

## 🚀 Quick Test Locally

### Option 1: Test with SSH Tunnel (Recommended for Local Testing)

```bash
# Terminal 1: Start SSH tunnel to your server
ssh -L 1883:localhost:1883 user@your-server -N

# Terminal 2: Test locally
cd hb-monitor
./test-local.sh
```

### Option 2: Test with Docker Locally

```bash
# 1. Make sure you have SSH tunnel running (Terminal 1)
ssh -L 1883:localhost:1883 user@your-server -N

# 2. Configure for localhost
cp config.example.yml config.yml
# Edit config.yml: set mqtt.host: "localhost"

# 3. Build and run
docker compose up --build
```

## 📦 Deployment on Server

### Step 1: Setup on Server

```bash
# Create separate directory (isolated from deploy/)
mkdir -p ~/hb-monitor
cd ~/hb-monitor

# Clone repository
git clone <your-repo-url> .

# Configure
cp config.example.yml config.yml
nano config.yml
# Set:
#   mqtt.host: "localhost"  (since using host network mode)
#   alerts.telegram.bot_token: "your_token"
#   alerts.telegram.chat_id: "your_chat_id"
```

### Step 2: Deploy

```bash
# Build and start
docker compose up -d

# Verify it's running
docker compose ps

# View logs
docker compose logs -f
```

### Step 3: Verify Connection

```bash
# Check if container is running
docker ps | grep hb-monitor

# Check logs for MQTT connection
docker compose logs | grep -i "connected\|subscribed"

# You should see:
# "Subscribed to: hbot/+/log"
# "Subscribed to: hbot/+/events"
# etc.
```

## 🔧 Management Commands

```bash
# Start
docker compose up -d

# Stop
docker compose stop

# Restart
docker compose restart

# View logs (follow)
docker compose logs -f

# View last 100 lines
docker compose logs --tail=100

# Stop and remove
docker compose down

# Rebuild after code changes
docker compose down
docker compose build --no-cache
docker compose up -d
```

## 🔒 Security Features

- ✅ Config file mounted as read-only (`:ro`)
- ✅ Container runs as non-root user
- ✅ No sensitive data in Docker image
- ✅ Isolated from Hummingbot deployment
- ✅ No conflicts with existing services

## 🌐 Network Configuration

**Current Setup (Host Network Mode):**
- Container uses `network_mode: host`
- Connects to EMQX via `localhost:1883`
- Works because EMQX is exposed on host port 1883

**Alternative (External Network):**
If you prefer Docker network isolation:
1. Uncomment network section in `docker-compose.yml`
2. Remove `network_mode: host`
3. Set `mqtt.host: "emqx"` in config.yml

## ✅ Verification Checklist

Before deploying to production:

- [ ] Config file has correct Telegram bot token
- [ ] Config file has correct Telegram chat ID
- [ ] MQTT host is set correctly (`localhost` for host network)
- [ ] Logs directory exists and is writable
- [ ] Docker and Docker Compose are installed
- [ ] EMQX broker is running and accessible
- [ ] Tested locally with SSH tunnel
- [ ] Container starts successfully
- [ ] Logs show successful MQTT connection
- [ ] Logs show successful topic subscriptions

## 🐛 Troubleshooting

### Container won't start
```bash
docker compose logs
# Check for config file errors or missing dependencies
```

### Can't connect to MQTT
```bash
# Test from container
docker compose exec hb-monitor python -c "
import socket
s = socket.socket()
try:
    s.connect(('localhost', 1883))
    print('✅ MQTT broker is accessible')
except Exception as e:
    print(f'❌ Cannot connect: {e}')
s.close()
"
```

### No events received
```bash
# Check if Hummingbot instances are publishing
# On server, test with:
mosquitto_sub -h localhost -p 1883 -t 'hbot/+/events' -v
```

## 📝 Notes

- This deployment is **completely separate** from your `deploy/` directory
- No conflicts with Hummingbot deployment
- Can be updated independently
- Uses same EMQX broker (via host network)
- All logs persist in `./logs/` directory

