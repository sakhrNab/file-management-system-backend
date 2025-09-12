#!/usr/bin/env python3
"""
Chunked Upload Client for Large Files
Supports uploading files up to 250MB by breaking them into 1MB chunks
"""
import os
import requests
import json
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BASE_URL = "https://drive-backend.aiwaverider.com"
UPLOAD_CHUNK_URL = f"{BASE_URL}/webhook/files/upload-chunk"
COMPLETE_UPLOAD_URL = f"{BASE_URL}/webhook/files/complete-chunked-upload"
CHUNK_SIZE = 1024 * 1024  # 1MB chunks
MAX_FILE_SIZE = 250 * 1024 * 1024  # 250MB limit

# Environment variables
AIWAVERIDER_DRIVE_TOKEN = os.getenv("AIWAVERIDER_DRIVE_TOKEN")

class ChunkedUploader:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}"
        }
    
    def upload_file(self, file_path: str, folder_path: str = "", progress_callback=None):
        """
        Upload a file using chunked upload method
        
        Args:
            file_path: Path to the file to upload
            folder_path: Target folder path
            progress_callback: Optional callback function for progress updates
                              Should accept (chunk_number, total_chunks, bytes_uploaded, total_bytes)
        
        Returns:
            dict: Upload result with file information
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"File too large: {file_size / (1024*1024):.2f}MB. Maximum: {MAX_FILE_SIZE / (1024*1024):.0f}MB")
        
        # Calculate number of chunks
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        upload_id = str(uuid.uuid4())
        filename = os.path.basename(file_path)
        
        print(f"🚀 Starting chunked upload...")
        print(f"📁 File: {file_path}")
        print(f"📊 Size: {file_size:,} bytes ({file_size / (1024*1024):.2f} MB)")
        print(f"📦 Chunks: {total_chunks} x {CHUNK_SIZE / (1024*1024):.1f}MB")
        print(f"🆔 Upload ID: {upload_id}")
        print(f"📂 Target folder: {folder_path}")
        
        try:
            with open(file_path, 'rb') as file:
                for chunk_number in range(1, total_chunks + 1):
                    # Read chunk data
                    chunk_data = file.read(CHUNK_SIZE)
                    if not chunk_data:
                        break
                    
                    # Prepare chunk upload
                    files = {
                        'file': (f"chunk_{chunk_number}", chunk_data, 'application/octet-stream')
                    }
                    data = {
                        'upload_id': upload_id,
                        'chunk_number': chunk_number,
                        'total_chunks': total_chunks,
                        'folder_path': folder_path
                    }
                    
                    # Upload chunk
                    print(f"📤 Uploading chunk {chunk_number}/{total_chunks}...")
                    
                    response = requests.post(
                        UPLOAD_CHUNK_URL,
                        headers=self.headers,
                        files=files,
                        data=data,
                        timeout=60
                    )
                    
                    if response.status_code != 200:
                        raise Exception(f"Chunk upload failed: {response.status_code} - {response.text}")
                    
                    chunk_result = response.json()
                    print(f"✅ Chunk {chunk_number} uploaded successfully")
                    
                    # Call progress callback if provided
                    if progress_callback:
                        bytes_uploaded = chunk_number * CHUNK_SIZE
                        if chunk_number == total_chunks:
                            bytes_uploaded = file_size
                        progress_callback(chunk_number, total_chunks, bytes_uploaded, file_size)
                
                # Complete the upload
                print(f"🔗 Completing upload...")
                complete_data = {
                    'upload_id': upload_id,
                    'filename': filename,
                    'total_chunks': total_chunks,
                    'folder_path': folder_path
                }
                
                response = requests.post(
                    COMPLETE_UPLOAD_URL,
                    headers=self.headers,
                    json=complete_data,
                    timeout=120
                )
                
                if response.status_code != 200:
                    raise Exception(f"Complete upload failed: {response.status_code} - {response.text}")
                
                result = response.json()
                print(f"🎉 Upload completed successfully!")
                print(f"📋 Result: {json.dumps(result, indent=2)}")
                
                return result
                
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            raise

def progress_callback(chunk_number, total_chunks, bytes_uploaded, total_bytes):
    """Default progress callback"""
    percentage = (bytes_uploaded / total_bytes) * 100
    print(f"📊 Progress: {chunk_number}/{total_chunks} chunks ({percentage:.1f}%) - {bytes_uploaded:,}/{total_bytes:,} bytes")

def test_chunked_upload():
    """Test the chunked upload with the large video file"""
    print("🧪 Chunked Upload Test")
    print("=" * 60)
    
    # Test file path
    test_file = r"E:\AIWaverider\socialmedia\Transcripe-autoDetect-Video-upload-to-gDrive\finished_videos\01_seb_intel_DNqNzwMv_F6\01_seb_intel_DNqNzwMv_F6.mp4"
    folder_path = "/videos/instagram/ai.uprise"
    
    if not AIWAVERIDER_DRIVE_TOKEN:
        print("❌ AIWAVERIDER_DRIVE_TOKEN not found in environment")
        return False
    
    if not os.path.exists(test_file):
        print(f"❌ Test file not found: {test_file}")
        return False
    
    try:
        # Create uploader instance
        uploader = ChunkedUploader(AIWAVERIDER_DRIVE_TOKEN)
        
        # Upload the file
        result = uploader.upload_file(
            file_path=test_file,
            folder_path=folder_path,
            progress_callback=progress_callback
        )
        
        print("\n🎉 Upload successful!")
        print(f"📁 File: {result['data']['filename']}")
        print(f"📂 Path: {result['data']['path']}")
        print(f"📊 Size: {result['data']['size']:,} bytes")
        print(f"🔗 URL: {result['data']['url']}")
        print(f"📦 Chunks combined: {result['data']['chunks_combined']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return False

def test_small_file():
    """Test with a small file first"""
    print("🧪 Testing with small file first...")
    
    # Create a small test file
    test_content = b"This is a test file for chunked upload verification. " * 1000  # ~50KB
    test_file_path = "test_chunked_upload.txt"
    
    try:
        with open(test_file_path, 'wb') as f:
            f.write(test_content)
        
        print(f"📁 Created test file: {test_file_path}")
        
        uploader = ChunkedUploader(AIWAVERIDER_DRIVE_TOKEN)
        result = uploader.upload_file(
            file_path=test_file_path,
            folder_path="/test",
            progress_callback=progress_callback
        )
        
        print("✅ Small file upload successful!")
        print(f"📋 Result: {json.dumps(result, indent=2)}")
        return True
        
    except Exception as e:
        print(f"❌ Small file upload failed: {e}")
        return False
    finally:
        # Clean up test file
        if os.path.exists(test_file_path):
            os.remove(test_file_path)
            print(f"🗑️ Cleaned up test file: {test_file_path}")

def main():
    """Main test function"""
    print("🧪 AI Wave Rider Chunked Upload Test")
    print("=" * 60)
    
    if not AIWAVERIDER_DRIVE_TOKEN:
        print("❌ AIWAVERIDER_DRIVE_TOKEN not found in environment")
        print("Please set AIWAVERIDER_DRIVE_TOKEN in your .env file")
        return
    
    print(f"🔑 Token loaded: {len(AIWAVERIDER_DRIVE_TOKEN)} characters")
    
    # Test small file first
    print("\n" + "=" * 60)
    if not test_small_file():
        print("❌ Small file test failed, stopping")
        return
    
    # Test large file
    print("\n" + "=" * 60)
    if test_chunked_upload():
        print("\n🎉 All tests completed successfully!")
    else:
        print("\n💥 Large file upload failed!")

if __name__ == "__main__":
    main()
