from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import paho.mqtt.client as mqtt
import mysql.connector
import json
from datetime import datetime
from flask import send_from_directory
import re
import sys

# ==============================
# 1️⃣ Inisialisasi Flask
# ==============================
app = Flask(__name__)
CORS(app)

# ==============================
# 2️⃣ Koneksi ke Database (global)
# ==============================
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Bakti123",
    database="hydronion"
)
cursor = db.cursor()

# Create sensor data table if it doesn't exist
cursor.execute("""
CREATE TABLE IF NOT EXISTS data_sensor (
    id INT AUTO_INCREMENT PRIMARY KEY,
    suhu FLOAT,
    humidity FLOAT,
    lux FLOAT,
    tds_ppm FLOAT,
    suhu_air FLOAT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()

# Global sensor data variables
sensor_data = {
    "suhu": None,
    "humidity": None,
    "lux": None,
    "tds_ppm": None,
    "suhu_air": None,
    "lamp_state": None,
    "pump_state": None,
    "humidifier_state": None
}

DEFAULT_PORT = 5000

# ==============================
# 3️⃣ MQTT Configuration
# ==============================
def on_connect(client, userdata, flags, rc):
    print("Terhubung ke MQTT Broker dengan kode:", rc)
    client.subscribe("esp32/hyrdonion/data")  # subscribe topic sensor

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        print("Pesan MQTT diterima:", payload)

        # Parse JSON dari payload MQTT
        data = json.loads(payload)
        sensor_data["suhu"] = float(data.get("suhu", 0))
        sensor_data["humidity"] = float(data.get("humidity", 0))
        sensor_data["lux"] = float(data.get("lux", 0))
        sensor_data["tds_ppm"] = float(data.get("tds_ppm", 0))
        sensor_data["suhu_air"] = float(data.get("suhu_air", 0))

        print("Data sensor diperbarui:", sensor_data)

        # ===== SIMPAN KE DATABASE =====
        sql = """
            INSERT INTO data_sensor (suhu, humidity, lux, tds_ppm, suhu_air)
            VALUES (%s, %s, %s, %s, %s)
        """
        val = (
            sensor_data["suhu"],
            sensor_data["humidity"],
            sensor_data["lux"],
            sensor_data["tds_ppm"],
            sensor_data["suhu_air"],
        )
        cursor.execute(sql, val)
        db.commit()

        print("Data berhasil disimpan ke database hydronion.data_sensor.")

    except Exception as e:
        print("Error parsing/saving message:", e)

# Setup MQTT Client
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()


# Serve the frontend index.html and other static assets from the `public` folder
@app.route('/')
def index():
    # Log client access (best-effort) for each request
    try:
        log_client_access(request)
    except Exception:
        pass

    # If the frontend requests an API action (e.g. ?action=fetch or ?action=history)
    action = request.args.get('action')
    if action:
        # lightweight API handler that returns latest record or history
        def find_best_table():
            # find best candidate table by matching expected sensor columns
            expected = {'tds','ec','ppm','suhu_air','suhu','temperature','kelembapan','humidity','timestamp','ts','time','device_id','signal','status','id'}
            cursor2 = db.cursor()
            cursor2.execute("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s", (db.database,))
            rows = cursor2.fetchall()
            tables = {}
            for tname, col in rows:
                tables.setdefault(tname, set()).add(col.lower())
            # score tables by number of matching expected columns
            best = None
            best_score = 0
            for t, cols in tables.items():
                score = len(cols & expected)
                if score > best_score:
                    best_score = score
                    best = (t, cols)
            return best

        def choose_timestamp_col(cols:set):
            for c in ('timestamp','ts','time','created_at'):
                if c in cols:
                    return c
            return None

        # action = fetch -> latest single row
        if action == 'fetch':
            candidate = find_best_table()
            if not candidate:
                return jsonify({'status':'error','message':'No candidate table found in database.'}), 500
            table, cols = candidate
            ts_col = choose_timestamp_col(cols)
            try:
                cur = db.cursor(dictionary=True)
                if ts_col:
                    q = f"SELECT * FROM `{table}` ORDER BY `{ts_col}` DESC LIMIT 1"
                else:
                    # fallback order by id if exists
                    if 'id' in cols:
                        q = f"SELECT * FROM `{table}` ORDER BY `id` DESC LIMIT 1"
                    else:
                        q = f"SELECT * FROM `{table}` LIMIT 1"
                cur.execute(q)
                row = cur.fetchone()
                if not row:
                    return jsonify({'status':'ok','sensor_data':None})
                return jsonify({'status':'success','sensor_data':row})
            except Exception as e:
                return jsonify({'status':'error','message':str(e)}), 500

        # action = history -> return recent rows; accepts range param (all, today, 7, 30)
        if action == 'history':
            rng = request.args.get('range','all')
            candidate = find_best_table()
            if not candidate:
                return jsonify([])
            table, cols = candidate
            ts_col = choose_timestamp_col(cols)
            try:
                cur = db.cursor(dictionary=True)
                if rng == 'all':
                    q = f"SELECT * FROM `{table}` ORDER BY `{ts_col or 'id'}` DESC LIMIT 2000" if ts_col or 'id' in cols else f"SELECT * FROM `{table}` LIMIT 2000"
                    cur.execute(q)
                    rows = cur.fetchall()
                    return jsonify(rows[::-1])
                if rng == 'today' and ts_col:
                    q = f"SELECT * FROM `{table}` WHERE DATE(`{ts_col}`)=CURDATE() ORDER BY `{ts_col}` ASC"
                    cur.execute(q)
                    return jsonify(cur.fetchall())
                # numeric days
                try:
                    days = int(rng)
                    if ts_col:
                        q = f"SELECT * FROM `{table}` WHERE `{ts_col}` >= NOW() - INTERVAL %s DAY ORDER BY `{ts_col}` ASC"
                        cur.execute(q, (days,))
                        return jsonify(cur.fetchall())
                except Exception:
                    pass
                # fallback: return last 200 rows
                q = f"SELECT * FROM `{table}` ORDER BY `{ts_col or 'id'}` DESC LIMIT 200"
                cur.execute(q)
                rows = cur.fetchall()
                return jsonify(rows[::-1])
            except Exception as e:
                return jsonify({'status':'error','message':str(e)}), 500

        # unknown action -> 400
        return jsonify({'status':'error','message':'unknown action'}), 400

    # otherwise serve the index.html located in the public/ directory
    return send_from_directory('public', 'index.html')


@app.route("/lamp", methods=["POST"])
def control_lamp():
    try:
        data = request.get_json()
        state = data.get("state")

        if state not in ["ON", "OFF"]:
            return jsonify({"error": "State harus 'ON' atau 'OFF'"}), 400

        # Publish perintah ke MQTT untuk lamp
        mqtt_client.publish("esp32/hyrdonion/relay/lamp", json.dumps({"lamp": state}))
        print(f"Perintah lamp dikirim ke MQTT: {state}")

        # Update status terakhir
        sensor_data["lamp_state"] = state
        return jsonify({"status": f"Lamp {state}"})
    except Exception as e:
        print("Error mengirim perintah lamp:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/pump", methods=["POST"])
def control_pump():
    try:
        data = request.get_json()
        state = data.get("state")

        if state not in ["ON", "OFF"]:
            return jsonify({"error": "State harus 'ON' atau 'OFF'"}), 400

        # Publish perintah ke MQTT untuk pump
        mqtt_client.publish("esp32/hyrdonion/relay/pump", json.dumps({"pump": state}))
        print(f"Perintah pump dikirim ke MQTT: {state}")

        # Update status terakhir
        sensor_data["pump_state"] = state
        return jsonify({"status": f"Pump {state}"})
    except Exception as e:
        print("Error mengirim perintah pump:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/humidifier", methods=["POST"])
def control_humidifier():
    try:
        data = request.get_json()
        state = data.get("state")

        if state not in ["ON", "OFF"]:
            return jsonify({"error": "State harus 'ON' atau 'OFF'"}), 400

        # Publish perintah ke MQTT untuk humidifier
        mqtt_client.publish("esp32/hyrdonion/relay/humidifier", json.dumps({"humidifier": state}))
        print(f"Perintah humidifier dikirim ke MQTT: {state}")

        # Update status terakhir
        sensor_data["humidifier_state"] = state
        return jsonify({"status": f"Humidifier {state}"})
    except Exception as e:
        print("Error mengirim perintah humidifier:", e)
        return jsonify({"error": str(e)}), 500

@app.route('/debug_clients')
def debug_clients():
    """Debug endpoint: return current rows from client_data for quick inspection.

    Note: This is a diagnostic endpoint. Remove or protect it in production.
    """
    try:
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM client_data ORDER BY client_id DESC LIMIT 200")
        rows = cur.fetchall()
        return jsonify({'status':'success','count': len(rows), 'rows': rows})
    except Exception as e:
        return jsonify({'status':'error','message': str(e)}), 500





if __name__ == '__main__':
    print(f"Starting HydrOnion Flask server at 0.0.0.0:{DEFAULT_PORT}")
    print("MQTT client connecting to broker.hivemq.com...")
    
    # Run Flask
    app.run(host='0.0.0.0', port=DEFAULT_PORT, debug=True)


# -----------------------------
# Client identification / logging helpers
# -----------------------------
def ensure_client_table():
    """Create client_data table if it doesn't exist."""
    try:
        cur = db.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS `client_data` (
                `client_id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                `device_id` VARCHAR(255) NOT NULL UNIQUE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )
        db.commit()
    except Exception as e:
        # avoid crashing the server for DB table creation issues
        print('ensure_client_table error:', e, file=sys.stderr)


def get_client_ip(req):
    """Return the best-effort client IP address from the Flask request.

    Checks X-Forwarded-For first (ngrok / proxies), then remote_addr.
    """
    # X-Forwarded-For can be a comma-separated list
    xff = req.headers.get('X-Forwarded-For') or req.headers.get('X-Real-Ip')
    if xff:
        # take the leftmost (original client)
        ip = xff.split(',')[0].strip()
        return ip
    # Flask's remote_addr
    return req.remote_addr


def get_mac_from_arp(ip: str) -> str | None:
    """Attempt to obtain MAC address for a local IP using the system ARP table (Windows).

    Returns MAC string if found, otherwise None.
    Note: This only works for machines on the same LAN and if an ARP entry exists.
    It cannot get MAC addresses for remote/internet clients.
    """
    if not ip:
        return None
    try:
        # Run `arp -a` and parse lines like:  192.168.1.10          00-11-22-33-44-55     dynamic
        out = subprocess.check_output(['arp', '-a'], text=True, stderr=subprocess.DEVNULL)
        # Find a line that starts with the IP (or contains it)
        for line in out.splitlines():
            if ip in line:
                # extract MAC-like token
                m = re.search(r'([0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2})', line)
                if m:
                    return m.group(1).lower()
        return None
    except Exception:
        return None


def log_client_access(req):
    """Detect client device id (MAC for local devices if available, else IP) and insert into client_data table.

    Behavior:
    - If client is on same LAN and MAC found via ARP, use MAC.
    - Otherwise use client IP.
    """
    try:
        ensure_client_table()
        ip = get_client_ip(req)
        device_id = None
        # Try get MAC only for private/local IPs (quick heuristic)
        if ip:
            # consider IPv4 private ranges
            if re.match(r'^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)', ip) or ip.startswith('127.'):
                mac = get_mac_from_arp(ip)
                if mac:
                    device_id = mac
        if not device_id:
            device_id = ip or req.headers.get('User-Agent', 'unknown')

        # Insert device_id if not exists
        cur = db.cursor()
        cur.execute("SELECT client_id FROM client_data WHERE device_id=%s", (device_id,))
        if not cur.fetchone():
            cur.execute("INSERT INTO client_data (device_id) VALUES (%s)", (device_id,))
            db.commit()
    except Exception as e:
        # Log the error but don't break the request
        print('log_client_access error:', e, file=sys.stderr)
