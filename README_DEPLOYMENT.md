# hb-monitor Docker Deployment Guide

This guide explains how to deploy `hb-monitor` using Docker, similar to your Hummingbot deployment setup.

## Quick Start

### 1. Prerequisites

- Docker and Docker Compose installed
- Access to the server where Hummingbot is deployed
- EMQX MQTT broker running (from your `deploy` stack)

### 2. Setup on Server

```bash
# Create separate directory (keep it isolated from deploy/)
mkdir -p ~/hb-monitor
cd ~/hb-monitor

# Clone the repository
git clone <your-repo-url> .

# Copy and configure
cp config.example.yml config.yml
nano config.yml  # Edit with your Telegram bot token and chat ID
```

### 3. Configure MQTT Connection

In `config.yml`, set the MQTT host based on your deployment:

**Option A: Host Network Mode (Recommended)**
```yaml
mqtt:
  host: "localhost"  # or "127.0.0.1"
  port: 1883
```

**Option B: External Docker Network**
```yaml
mqtt:
  host: "emqx"  # Docker service name
  port: 1883
```
Then uncomment the network section in `docker-compose.yml`.

### 4. Deploy

```bash
# Build and start
docker compose up -d

# View logs
docker compose logs -f

# Check status
docker compose ps
```

### 5. Management Commands

```bash
# Start
docker compose up -d

# Stop
docker compose stop

# Restart
docker compose restart

# View logs
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

## Local Testing

### Test with SSH Tunnel

If testing from your local machine while EMQX is on the server:

```bash
# 1. Create SSH tunnel (in separate terminal)
ssh -L 1883:localhost:1883 user@your-server

# 2. Update config.local.yml
mqtt:
  host: "localhost"
  port: 1883

# 3. Run locally
CONFIG_PATH=config.local.yml python main.py
```

### Test with Docker Locally

```bash
# 1. Make sure EMQX is accessible (via tunnel or local)
# 2. Update config.yml with correct MQTT host
# 3. Build and run
docker compose up --build
```

## Network Configuration

### Option 1: Host Network (Current Default)

Uses `network_mode: host` to connect directly to `localhost:1883`. This works when:
- EMQX is exposed on host port 1883 (as in your deploy setup)
- You want simple, straightforward networking

### Option 2: External Docker Network

To connect via Docker network (more isolated):

1. Uncomment network section in `docker-compose.yml`:
```yaml
networks:
  emqx-bridge:
    external: true
    name: deploy_emqx-bridge
```

2. Remove `network_mode: host` line

3. Uncomment network section in service:
```yaml
networks:
  - emqx-bridge
```

4. Update `config.yml`:
```yaml
mqtt:
  host: "emqx"  # Docker service name
  port: 1883
```

## Troubleshooting

### Container won't start
```bash
# Check logs
docker compose logs

# Check if config file exists
ls -la config.yml

# Verify config syntax
python -c "import yaml; yaml.safe_load(open('config.yml'))"
```

### Can't connect to MQTT
```bash
# Test MQTT connection from container
docker compose exec hb-monitor python -c "
import socket
s = socket.socket()
s.connect(('localhost', 1883))
print('Connected!')
s.close()
"

# Check if EMQX is running
docker ps | grep emqx
```

### Permission errors
```bash
# Fix log directory permissions
chmod 755 logs
```

## Security Notes

- `config.yml` is mounted as read-only (`:ro`) for security
- Container runs as non-root user (`hbmonitor`)
- Config file should have restricted permissions: `chmod 600 config.yml`
- Never commit `config.yml` to git (already in `.gitignore`)

## Updating

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose down
docker compose build --no-cache
docker compose up -d

# Verify
docker compose logs -f
```

