FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create logs directory with proper permissions
RUN mkdir -p logs && chmod 755 logs

# Run as non-root user for security
RUN useradd -m -u 1000 hbmonitor && chown -R hbmonitor:hbmonitor /app
USER hbmonitor

# Run the service
CMD ["python", "main.py"]

