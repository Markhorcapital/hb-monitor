#!/bin/bash
# Test script for local development
# This script helps test hb-monitor locally before deploying to server

set -e

echo "=== hb-monitor Local Test Script ==="
echo ""

# Check if config file exists
if [ ! -f "config.local.yml" ]; then
    echo "❌ config.local.yml not found!"
    echo "Creating from config.example.yml..."
    cp config.example.yml config.local.yml
    echo "✅ Created config.local.yml"
    echo "⚠️  Please edit config.local.yml with your Telegram bot token and chat ID"
    exit 1
fi

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found!"
    exit 1
fi

# Check if dependencies are installed
echo "Checking dependencies..."
if ! python3 -c "import aiomqtt, yaml, aiohttp" 2>/dev/null; then
    echo "⚠️  Dependencies not installed. Installing..."
    pip install -r requirements.txt
fi

# Check if MQTT broker is accessible
echo ""
echo "Testing MQTT connection..."
MQTT_HOST=$(python3 -c "import yaml; print(yaml.safe_load(open('config.local.yml'))['mqtt']['host'])" 2>/dev/null || echo "localhost")
MQTT_PORT=$(python3 -c "import yaml; print(yaml.safe_load(open('config.local.yml'))['mqtt']['port'])" 2>/dev/null || echo "1883")

echo "Attempting to connect to MQTT broker at $MQTT_HOST:$MQTT_PORT..."

if command -v nc &> /dev/null; then
    if nc -z -w 2 "$MQTT_HOST" "$MQTT_PORT" 2>/dev/null; then
        echo "✅ MQTT broker is accessible"
    else
        echo "⚠️  Cannot connect to MQTT broker at $MQTT_HOST:$MQTT_PORT"
        echo "   Make sure:"
        echo "   1. EMQX is running on the server"
        echo "   2. SSH tunnel is active: ssh -L 1883:localhost:1883 user@server"
        echo "   3. Or MQTT broker is running locally"
    fi
else
    echo "⚠️  'nc' (netcat) not found, skipping connection test"
fi

# Run the monitor
echo ""
echo "Starting hb-monitor..."
echo "Press Ctrl+C to stop"
echo ""

CONFIG_PATH=config.local.yml python3 main.py

