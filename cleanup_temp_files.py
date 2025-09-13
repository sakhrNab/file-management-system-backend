#!/usr/bin/env python3
"""
Cleanup script for temporary files
Removes old chunk files and temporary uploads
"""

import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
TEMP_CHUNKS_DIR = "./uploads/temp_chunks"
UPLOAD_DIR = "./uploads"
CLEANUP_AGE_HOURS = 24  # Remove files older than 24 hours
LOG_FILE = "cleanup.log"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

def cleanup_old_files(directory, age_hours, pattern="*"):
    """Clean up old files in a directory"""
    if not os.path.exists(directory):
        logging.info(f"Directory {directory} does not exist, skipping")
        return 0
    
    cutoff_time = time.time() - (age_hours * 3600)
    removed_count = 0
    
    for file_path in Path(directory).glob(pattern):
        if file_path.is_file():
            file_age = file_path.stat().st_mtime
            if file_age < cutoff_time:
                try:
                    file_path.unlink()
                    removed_count += 1
                    logging.info(f"Removed old file: {file_path}")
                except Exception as e:
                    logging.error(f"Failed to remove {file_path}: {e}")
    
    return removed_count

def cleanup_temp_chunks():
    """Clean up temporary chunk files"""
    logging.info("Cleaning up temp chunks...")
    removed = cleanup_old_files(TEMP_CHUNKS_DIR, CLEANUP_AGE_HOURS, "*_chunk_*")
    logging.info(f"Removed {removed} temp chunk files")
    return removed

def cleanup_temp_uploads():
    """Clean up temporary upload files"""
    logging.info("Cleaning up temp uploads...")
    removed = cleanup_old_files(UPLOAD_DIR, CLEANUP_AGE_HOURS, "*.tmp")
    logging.info(f"Removed {removed} temp upload files")
    return removed

def get_directory_size(directory):
    """Get total size of directory in MB"""
    if not os.path.exists(directory):
        return 0
    
    total_size = 0
    for file_path in Path(directory).rglob("*"):
        if file_path.is_file():
            total_size += file_path.stat().st_size
    
    return total_size / (1024 * 1024)  # Convert to MB

def main():
    """Main cleanup function"""
    logging.info("Starting cleanup process...")
    
    # Get initial sizes
    initial_chunks_size = get_directory_size(TEMP_CHUNKS_DIR)
    initial_uploads_size = get_directory_size(UPLOAD_DIR)
    
    logging.info(f"Initial temp chunks size: {initial_chunks_size:.2f} MB")
    logging.info(f"Initial uploads size: {initial_uploads_size:.2f} MB")
    
    # Clean up files
    chunks_removed = cleanup_temp_chunks()
    uploads_removed = cleanup_temp_uploads()
    
    # Get final sizes
    final_chunks_size = get_directory_size(TEMP_CHUNKS_DIR)
    final_uploads_size = get_directory_size(UPLOAD_DIR)
    
    # Log results
    logging.info(f"Cleanup completed:")
    logging.info(f"  - Temp chunks: {chunks_removed} files removed, {initial_chunks_size - final_chunks_size:.2f} MB freed")
    logging.info(f"  - Temp uploads: {uploads_removed} files removed, {initial_uploads_size - final_uploads_size:.2f} MB freed")
    logging.info(f"  - Final temp chunks size: {final_chunks_size:.2f} MB")
    logging.info(f"  - Final uploads size: {final_uploads_size:.2f} MB")

if __name__ == "__main__":
    main()
