# Save as debug_device.py
import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

TOKEN = os.getenv("SMARTTHINGS_TOKEN")
DEVICE_ID = os.getenv("SMARTTHINGS_DEVICE_ID")

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

print("📱 Fetching device info...")
print("=" * 60)

# Get device info
url = f"https://api.smartthings.com/v1/devices/{DEVICE_ID}"
response = requests.get(url, headers=headers)

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2))
    
    # Check device status
    device_status = data.get("deviceStatus", {})
    print("\n📊 Device Status:")
    print(json.dumps(device_status, indent=2))
else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)