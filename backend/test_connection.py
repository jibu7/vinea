import requests
import time

print("Testing server connection...")

# Try different approaches
try:
    # Test 1: Direct health check
    print("\n1. Testing direct health endpoint...")
    response = requests.get("http://localhost:8000/health", timeout=2)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")

try:
    # Test 2: API health check
    print("\n2. Testing API health endpoint...")
    response = requests.get("http://localhost:8000/api/health", timeout=2)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")

try:
    # Test 3: Root endpoint
    print("\n3. Testing root endpoint...")
    response = requests.get("http://localhost:8000/", timeout=2)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:100]}...")
except Exception as e:
    print(f"Error: {e}")

try:
    # Test 4: OpenAPI docs
    print("\n4. Testing OpenAPI docs...")
    response = requests.get("http://localhost:8000/docs", timeout=2)
    print(f"Status: {response.status_code}")
    print(f"Response length: {len(response.text)} bytes")
except Exception as e:
    print(f"Error: {e}")

print("\nTests completed.") 