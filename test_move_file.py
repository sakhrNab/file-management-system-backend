#!/usr/bin/env python3
"""
Test script for file move functionality
Tests the PUT /api/files/move endpoint
"""

import requests
import json
import os
from pathlib import Path

# Configuration
BASE_URL = os.getenv("BASE_URL", "http://localhost:8003")
USERNAME = os.getenv("AUTH_USERNAME", "admin")
PASSWORD = os.getenv("AUTH_PASSWORD", "your_secure_password_here")

def login():
    """Login and get JWT token"""
    response = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": USERNAME, "password": PASSWORD}
    )
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        raise Exception(f"Login failed: {response.status_code} - {response.text}")

def create_test_file(token, folder_path, filename, content="Test file content"):
    """Create a test file"""
    import tempfile
    
    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write(content)
        test_file_path = f.name
    
    # Upload file
    try:
        with open(test_file_path, "rb") as f:
            files = {"file": (filename, f, "text/plain")}
            data = {"folder_path": folder_path}
            response = requests.post(
                f"{BASE_URL}/api/files/upload",
                headers={"Authorization": f"Bearer {token}"},
                files=files,
                data=data
            )
    finally:
        os.remove(test_file_path)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"File creation failed: {response.status_code} - {response.text}")

def move_file(token, file_path, destination_folder):
    """Move a file"""
    response = requests.put(
        f"{BASE_URL}/api/files/move",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={
            "file_path": file_path,
            "destination_folder": destination_folder
        }
    )
    return response

def get_folder_status(token, folder_path=""):
    """Get folder status"""
    response = requests.get(
        f"{BASE_URL}/api/folders/status",
        headers={"Authorization": f"Bearer {token}"},
        params={"folder_path": folder_path}
    )
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to get folder status: {response.status_code} - {response.text}")

def test_move_file():
    """Test moving a file"""
    print("=" * 60)
    print("Testing File Move Functionality")
    print("=" * 60)
    
    try:
        # Login
        print("\n1. Logging in...")
        token = login()
        print("   [OK] Login successful")
        
        # Create source folder
        print("\n2. Creating source folder...")
        source_folder = "test_move_source"
        folder_response = requests.post(
            f"{BASE_URL}/api/folders",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": source_folder, "parent_path": ""}
        )
        if folder_response.status_code in [200, 400]:  # 400 if already exists
            print(f"   [OK] Source folder '{source_folder}' ready")
        
        # Create destination folder
        print("\n3. Creating destination folder...")
        dest_folder = "test_move_dest"
        folder_response = requests.post(
            f"{BASE_URL}/api/folders",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": dest_folder, "parent_path": ""}
        )
        if folder_response.status_code in [200, 400]:  # 400 if already exists
            print(f"   [OK] Destination folder '{dest_folder}' ready")
        
        # Create test file
        print("\n4. Creating test file...")
        test_filename = "test_move_file.txt"
        file_result = create_test_file(token, source_folder, test_filename, "This is a test file for moving")
        print(f"   [OK] Test file '{test_filename}' created in '{source_folder}'")
        
        # Verify file exists in source
        print("\n5. Verifying file in source folder...")
        source_status = get_folder_status(token, source_folder)
        source_files = [f["name"] for f in source_status.get("files", [])]
        if test_filename in source_files:
            print(f"   [OK] File found in source folder")
        else:
            raise Exception(f"File not found in source folder: {source_files}")
        
        # Get file path
        file_path = None
        for f in source_status.get("files", []):
            if f["name"] == test_filename:
                file_path = f["path"]
                break
        
        if not file_path:
            raise Exception("Could not determine file path")
        
        print(f"   File path: {file_path}")
        
        # Move file
        print(f"\n6. Moving file from '{source_folder}' to '{dest_folder}'...")
        move_response = move_file(token, file_path, dest_folder)
        
        if move_response.status_code == 200:
            move_result = move_response.json()
            print(f"   [OK] File moved successfully!")
            print(f"   New path: {move_result.get('new_path', 'N/A')}")
            new_file_path = move_result.get('new_path', '')
        else:
            print(f"   [FAIL] Move failed: {move_response.status_code}")
            print(f"   Response: {move_response.text}")
            raise Exception(f"Move failed: {move_response.status_code}")
        
        # Verify file is in destination
        print("\n7. Verifying file in destination folder...")
        dest_status = get_folder_status(token, dest_folder)
        dest_files = [f["name"] for f in dest_status.get("files", [])]
        if test_filename in dest_files:
            print(f"   [OK] File found in destination folder")
        else:
            raise Exception(f"File not found in destination folder: {dest_files}")
        
        # Verify file is NOT in source
        print("\n8. Verifying file removed from source folder...")
        source_status_after = get_folder_status(token, source_folder)
        source_files_after = [f["name"] for f in source_status_after.get("files", [])]
        if test_filename not in source_files_after:
            print(f"   [OK] File removed from source folder")
        else:
            raise Exception(f"File still exists in source folder: {source_files_after}")
        
        # Test duplicate prevention
        print("\n9. Testing duplicate prevention...")
        # Try to move the file again to same destination (should fail due to duplicate)
        duplicate_move_response = move_file(token, new_file_path.lstrip('/'), dest_folder)
        if duplicate_move_response.status_code == 409:
            print(f"   [OK] Duplicate prevention working correctly")
        else:
            print(f"   [WARN] Duplicate prevention test: {duplicate_move_response.status_code}")
            print(f"   Response: {duplicate_move_response.text}")
        
        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_move_file()
    exit(0 if success else 1)

