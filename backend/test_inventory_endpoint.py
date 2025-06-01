import requests

print("Testing inventory endpoint...")

# First login
response = requests.post(
    "http://localhost:8000/api/auth/login",
    json={
        "username": "admin",
        "password": "admin123"
    }
)

if response.status_code == 200:
    token = response.json()['access_token']
    print(f"Login successful! Token: {token[:20]}...")
    
    # Test inventory endpoint
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get("http://localhost:8000/api/inventory/items", headers=headers)
    
    print(f"\nInventory items status: {response.status_code}")
    if response.status_code == 200:
        print("Success! Inventory endpoint is working.")
        items = response.json()
        print(f"Number of items: {len(items)}")
        if items:
            print("First item:", items[0])
    else:
        print(f"Error: {response.text}")
else:
    print(f"Login failed: {response.text}") 