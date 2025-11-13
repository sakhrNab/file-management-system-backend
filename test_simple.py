"""Simple test for public endpoints"""
import requests

BASE_URL = "http://localhost:8003"

print("=" * 60)
print("Testing Public Video Endpoint")
print("=" * 60)

# Test public endpoint
try:
    response = requests.get(f"{BASE_URL}/videos/public/public/test.mp4", timeout=5)
    print(f"\nStatus Code: {response.status_code}")
    print(f"Response: {response.text[:150]}")
    
    if response.status_code == 403:
        print("\n⚠️  Folder is not public (expected if PUBLIC_FOLDERS not set)")
        print("   Set PUBLIC_FOLDERS=videos/public and restart server")
    elif response.status_code == 404:
        print("\n✅ Endpoint works! (404 = file not found, which is expected)")
    elif response.status_code == 200:
        print("\n✅ Endpoint works! File found and served")
    else:
        print(f"\n⚠️  Unexpected status: {response.status_code}")
        
except Exception as e:
    print(f"\n❌ Error: {e}")

print("\n" + "=" * 60)
print("Test Complete")
print("=" * 60)

