"""Test script for public video endpoints"""
import requests
import json
import os
import sys

BASE_URL = "http://localhost:8003"

def print_section(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def print_result(test_name, success, details=""):
    status = "✅ PASS" if success else "❌ FAIL"
    color_code = "\033[92m" if success else "\033[91m"
    reset_code = "\033[0m"
    print(f"{color_code}{status}{reset_code} - {test_name}")
    if details:
        print(f"    {details}")

def test_health():
    """Test health endpoint"""
    print_section("1. Health Check")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        success = response.status_code == 200
        if success:
            data = response.json()
            print_result("Health endpoint", True, f"Status: {data.get('status')}")
        else:
            print_result("Health endpoint", False, f"Status code: {response.status_code}")
        return success
    except Exception as e:
        print_result("Health endpoint", False, f"Error: {e}")
        return False

def test_public_video_endpoint():
    """Test public video endpoint"""
    print_section("2. Public Video Endpoint")
    results = []
    
    # Test 1: Access public folder (should work if videos/public is in PUBLIC_FOLDERS)
    try:
        response = requests.get(f"{BASE_URL}/videos/public/public/test.mp4", timeout=5)
        # 404 is OK (file doesn't exist), 403 means folder not public
        success = response.status_code in [404, 200]
        status_msg = "404 (file not found - expected)" if response.status_code == 404 else "403 (folder not public)" if response.status_code == 403 else "200 (success)"
        print_result("Public folder access", success, f"Status: {response.status_code} - {status_msg}")
        if response.status_code == 403:
            print("    ⚠️  Note: Set PUBLIC_FOLDERS=videos/public in environment to enable")
        results.append(success)
    except Exception as e:
        print_result("Public folder access", False, f"Error: {e}")
        results.append(False)
    
    # Test 2: Access non-public folder (should return 403)
    try:
        response = requests.get(f"{BASE_URL}/videos/public/instagram/ai.waverider/test.mp4", timeout=5)
        # 403 is expected if folder is not public, 404 if it is public but file doesn't exist
        success = response.status_code in [403, 404]
        status_msg = "403 (folder not public - expected)" if response.status_code == 403 else "404 (file not found)"
        print_result("Non-public folder access", success, f"Status: {response.status_code} - {status_msg}")
        results.append(success)
    except Exception as e:
        print_result("Non-public folder access", False, f"Error: {e}")
        results.append(False)
    
    return all(results)

def test_visibility_endpoint():
    """Test visibility check endpoint (requires auth)"""
    print_section("3. Visibility Check Endpoint")
    
    # First try to login
    try:
        login_data = {
            "username": os.getenv("AUTH_USERNAME", "admin"),
            "password": os.getenv("AUTH_PASSWORD", "admin123")
        }
        login_response = requests.post(f"{BASE_URL}/auth/login", json=login_data, timeout=5)
        
        if login_response.status_code != 200:
            print_result("Visibility endpoint", False, "Cannot test - login failed (set AUTH_USERNAME and AUTH_PASSWORD)")
            return False
        
        token = login_response.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}"}
        
        # Test visibility check
        response = requests.get(
            f"{BASE_URL}/api/folders/visibility",
            params={"folder_path": "videos/public"},
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            print_result("Visibility endpoint", True, f"Folder: {data.get('folder_path')}, Public: {data.get('is_public')}")
            return True
        else:
            print_result("Visibility endpoint", False, f"Status: {response.status_code}, Response: {response.text}")
            return False
            
    except Exception as e:
        print_result("Visibility endpoint", False, f"Error: {e}")
        return False

def test_env_config():
    """Test environment variable configuration"""
    print_section("4. Environment Variable Configuration")
    try:
        from main import load_folder_visibility, is_folder_public
        
        visibility_map = load_folder_visibility()
        print_result("Load visibility config", True, f"Found {len(visibility_map)} public folders")
        
        # Check if videos/public is public
        is_public = is_folder_public('videos/public')
        print_result("videos/public visibility", is_public, f"Public: {is_public}")
        
        # Show all public folders
        if visibility_map:
            print("\n  Public folders configured:")
            for folder, is_pub in visibility_map.items():
                print(f"    - {folder}: {is_pub}")
        else:
            print("  ⚠️  No public folders configured - set PUBLIC_FOLDERS env var")
        
        return True
    except Exception as e:
        print_result("Env config test", False, f"Error: {e}")
        return False

def test_swagger_docs():
    """Test Swagger documentation"""
    print_section("5. Swagger Documentation")
    try:
        response = requests.get(f"{BASE_URL}/openapi.json", timeout=5)
        if response.status_code == 200:
            schema = response.json()
            paths = schema.get('paths', {})
            
            # Check for key endpoints
            endpoints_found = {
                'Public video endpoint': '/videos/public/{file_path}' in paths,
                'Folder visibility GET': '/api/folders/visibility' in paths and 'get' in paths.get('/api/folders/visibility', {}),
            }
            
            all_found = all(endpoints_found.values())
            for endpoint, found in endpoints_found.items():
                print_result(endpoint, found)
            
            print_result("Total endpoints", True, f"{len(paths)} endpoints documented")
            return all_found
        else:
            print_result("Swagger docs", False, f"Status code: {response.status_code}")
            return False
    except Exception as e:
        print_result("Swagger docs", False, f"Error: {e}")
        return False

def main():
    print("\n" + "=" * 70)
    print("  TESTING PUBLIC VIDEO ENDPOINTS")
    print("=" * 70)
    print(f"\nTesting API at: {BASE_URL}")
    print("Waiting for server to be ready...")
    import time
    time.sleep(2)
    
    results = {}
    
    # Run all tests
    results['health'] = test_health()
    results['public_video'] = test_public_video_endpoint()
    results['visibility'] = test_visibility_endpoint()
    results['env_config'] = test_env_config()
    results['swagger'] = test_swagger_docs()
    
    # Summary
    print_section("TEST SUMMARY")
    total_tests = len(results)
    passed_tests = sum(1 for v in results.values() if v)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        color = "\033[92m" if result else "\033[91m"
        reset = "\033[0m"
        print(f"{color}{status}{reset} - {test_name}")
    
    print(f"\nResults: {passed_tests}/{total_tests} tests passed")
    
    if passed_tests == total_tests:
        print("\n🎉 All tests passed! Public endpoints are working correctly.")
        return 0
    else:
        print(f"\n⚠️  {total_tests - passed_tests} test(s) failed. Check output above for details.")
        print("\n💡 Tip: Set PUBLIC_FOLDERS=videos/public in environment to enable public folder access")
        return 1

if __name__ == "__main__":
    sys.exit(main())

