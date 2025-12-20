import requests
import json
from datetime import datetime

# URL API endpoint
url = "https://nonaccusing-dani-unreined.ngrok-free.dev/sensor_data"

sensor_data = {
    "suhu": 31.5,              # Suhu udara dalam Celsius
    "kelembapan": 25.2,        # Kelembapan dalam persen
    "tds": 222.7,              # TDS (Total Dissolved Solids) dalam ppm
    "suhu_air": 22.3,          # Suhu air dalam Celsius
    "status": "Online",        # Status sistem
    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Waktu saat ini
}

try:
    response = requests.post(url, json=sensor_data)
    
    print("Status Code:", response.status_code)
    print("Response:", response.json())
    
    if response.status_code == 200:
        print("\n✅ Data berhasil dikirim ke server!")
    else:
        print("\n❌ Gagal mengirim data.")
        
except Exception as e:
    print(f"Error: {e}")
    print("\nPastikan server Flask sudah berjalan di http://localhost:5000")
