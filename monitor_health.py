#!/usr/bin/env python3
"""
Health Monitoring Script for AI Wave Rider Drive Backend
Monitors system health and alerts on issues
"""

import requests
import json
import time
import logging
from datetime import datetime
import os

# Configuration
HEALTH_URL = "https://drive-backend.aiwaverider.com/health"
LOG_FILE = "health_monitor.log"
ALERT_THRESHOLDS = {
    "memory_percent": 80,
    "disk_percent": 85,
    "temp_chunks_count": 100
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

def check_health():
    """Check system health and return status"""
    try:
        response = requests.get(HEALTH_URL, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        logging.error(f"Health check failed: {e}")
        return {"status": "error", "error": str(e)}
    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON response: {e}")
        return {"status": "error", "error": "Invalid JSON response"}

def analyze_health(data):
    """Analyze health data and generate alerts"""
    alerts = []
    
    if data.get("status") != "healthy":
        alerts.append(f"CRITICAL: System status is {data.get('status')}")
        return alerts
    
    system = data.get("system", {})
    
    # Check memory usage
    memory_percent = system.get("memory_percent", 0)
    if memory_percent > ALERT_THRESHOLDS["memory_percent"]:
        alerts.append(f"WARNING: High memory usage: {memory_percent}%")
    
    # Check disk usage
    disk_percent = system.get("disk_percent", 0)
    if disk_percent > ALERT_THRESHOLDS["disk_percent"]:
        alerts.append(f"WARNING: High disk usage: {disk_percent}%")
    
    # Check temp chunks
    temp_chunks = system.get("temp_chunks_count", 0)
    if temp_chunks > ALERT_THRESHOLDS["temp_chunks_count"]:
        alerts.append(f"WARNING: High temp chunks count: {temp_chunks}")
    
    return alerts

def log_health_status(data):
    """Log current health status"""
    timestamp = data.get("timestamp", datetime.now().isoformat())
    status = data.get("status", "unknown")
    
    system = data.get("system", {})
    memory = system.get("memory_percent", "N/A")
    disk = system.get("disk_percent", "N/A")
    temp_chunks = system.get("temp_chunks_count", "N/A")
    
    logging.info(f"Health Check - Status: {status}, Memory: {memory}%, Disk: {disk}%, Temp Chunks: {temp_chunks}")

def main():
    """Main monitoring loop"""
    logging.info("Starting health monitoring...")
    
    while True:
        try:
            # Check health
            health_data = check_health()
            
            # Log status
            log_health_status(health_data)
            
            # Analyze for alerts
            alerts = analyze_health(health_data)
            
            # Log alerts
            for alert in alerts:
                logging.warning(alert)
            
            # Wait before next check
            time.sleep(30)  # Check every 30 seconds
            
        except KeyboardInterrupt:
            logging.info("Monitoring stopped by user")
            break
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            time.sleep(60)  # Wait longer on error

if __name__ == "__main__":
    main()


