import requests

print("Testing authentication...")

# Test login
response = requests.post(
    "http://localhost:8000/api/auth/login",
    json={
        "username": "admin",
        "password": "admin123"
    }
)

print(f"Status: {response.status_code}")
if response.status_code == 200:
    print("Login successful!")
    data = response.json()
    print(f"Access token received: {data['access_token'][:20]}...")
else:
    print(f"Login failed: {response.text}") 