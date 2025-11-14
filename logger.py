"""
Logging configuration for the monitor service.
"""

import logging
import os
from pathlib import Path


def setup_logger(log_file: str = "logs/hb-monitor.log", log_level: str = "INFO"):
    """Setup logging configuration."""
    # Configure logging
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler (always available)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    
    # File handler (try to create, but don't fail if permissions are wrong)
    try:
        # Create logs directory if it doesn't exist
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Try to create file handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except (PermissionError, OSError) as e:
        # If we can't write to the log file, just log to console
        root_logger.warning(f"Could not create log file {log_file}: {e}. Logging to console only.")
    
    # Reduce noise from third-party libraries
    logging.getLogger("aiomqtt").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

