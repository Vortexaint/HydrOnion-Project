from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_mail import Mail, Message
import mysql.connector
import json
from datetime import datetime
from flask import send_from_directory
import subprocess
import re
import sys
import os
import time
import requests
import paho.mqtt.client as mqtt
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import uuid
from pydantic import BaseModel, ValidationError
import traceback

# ==============================
# Utility Functions
# ==============================

def execute_query(query, params=None, fetch=False):
    """Utility function to execute a database query."""
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True if fetch else None)
        cursor.execute(query, params or ())
        if fetch:
            result = cursor.fetchall()
        else:
            db.commit()
            result = None
        return result
    except Exception as e:
        print(f"Database query error: {e}", file=sys.stderr)
        if db:
            try:
                db.rollback()
            except:
                pass
        return None
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if db:
            try:
                db.close()
            except:
                pass

# ==============================
# 1️⃣ Flask Initialization
# ==============================
app = Flask(__name__)
CORS(app)

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'off19132@gmail.com'  # Your email
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')  # Set via environment variable
app.config['MAIL_DEFAULT_SENDER'] = 'off19132@gmail.com'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAIL_SUPPRESS_SEND'] = not bool(os.environ.get('MAIL_PASSWORD'))  # Don't send if no password

try:
    mail = Mail(app)
    EMAIL_ENABLED = bool(os.environ.get('MAIL_PASSWORD'))
except Exception as e:
    print(f"Email not configured: {e}", file=sys.stderr)
    mail = None
    EMAIL_ENABLED = False


# Pydantic request models
class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

# ==============================
# Global Error Handlers
# ==============================

@app.errorhandler(Exception)
def handle_error(error):
    """Global error handler to return JSON instead of HTML"""
    print(f"Unhandled error: {error}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    return jsonify({
        "error": str(error),
        "message": "Internal server error"
    }), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

# ==============================
# 2️⃣ Database Initialization
# ==============================

def get_db_connection():
    try:
        return mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="hydronion",
            connect_timeout=10
        )
    except mysql.connector.Error as e:
        print(f"Database connection error: {e}", file=sys.stderr)
        raise


# Create sensor data table if it doesn't exist
def ensure_sensor_table():
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS `sensor_data` (
        `sensor_id` INT NOT NULL AUTO_INCREMENT,
        `tds` FLOAT NULL DEFAULT NULL,
        `suhu_air` FLOAT NULL DEFAULT NULL,
        `suhu` FLOAT NULL DEFAULT NULL,
        `kelembapan` FLOAT NULL DEFAULT NULL,
        `status` VARCHAR(50) NULL DEFAULT NULL COLLATE 'utf8mb4_0900_ai_ci',
        `timestamp` DATETIME NULL DEFAULT NULL,
        PRIMARY KEY (`sensor_id`) USING BTREE
    )
    COLLATE='utf8mb4_0900_ai_ci'
    ENGINE=InnoDB
    AUTO_INCREMENT=2
    ;
    """)
    db.commit()
    cursor.close()
    db.close()
ensure_sensor_table()

# Create users table with email verification
def ensure_users_table():
    """Create users table if it doesn't exist."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS `users` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `username` VARCHAR(255) NOT NULL UNIQUE,
            `password` VARCHAR(255) NOT NULL,
            `role` VARCHAR(50) DEFAULT 'user',
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`)
        )
        COLLATE='utf8mb4_0900_ai_ci'
        ENGINE=InnoDB;
        """)
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print('ensure_users_table error:', e, file=sys.stderr)

def migrate_users_table():
    """Add missing columns to existing users table."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        # Check which columns exist
        cursor.execute("SHOW COLUMNS FROM users")
        columns_info = cursor.fetchall()
        existing_columns = [col[0] for col in columns_info]
        
        # Check password column length
        password_col = [col for col in columns_info if col[0] == 'password']
        if password_col:
            col_type = password_col[0][1]  # Type like 'varchar(50)'
            # Extract length if it's varchar
            import re
            match = re.search(r'varchar\((\d+)\)', col_type, re.IGNORECASE)
            if match and int(match.group(1)) < 255:
                print(f"Expanding password column from {col_type} to VARCHAR(255)...")
                cursor.execute("ALTER TABLE users MODIFY COLUMN password VARCHAR(255) NOT NULL")
                db.commit()
        # Add created_at column if missing
        if 'created_at' not in existing_columns:
            print("Adding 'created_at' column to users table...")
            cursor.execute("ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            db.commit()
        
        cursor.close()
        db.close()
        print("User table migration completed successfully!")
    except Exception as e:
        print('migrate_users_table error:', e, file=sys.stderr)

ensure_users_table()
migrate_users_table()

# Ensure the client_details table exists

def ensure_client_details_table():
    """Create client_details table if it doesn't exist."""
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS `client_details` (
                `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                `mac_address` VARCHAR(255),
                `ip_address` VARCHAR(255),
                `user_agent` TEXT,
                `country` VARCHAR(255),
                `region` VARCHAR(255),
                `access_date` DATE,
                `access_time` TIME
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )
        db.commit()
        cur.close()
        db.close()
    except Exception as e:
        print('ensure_client_details_table error:', e, file=sys.stderr)

ensure_client_details_table()

# Global sensor data variables
sensor_data = {
    "suhu": None,
    "kelembapan": None,
    "tds": None,
    "suhu_air": None,
    "status": None,
    "timestamp": None
}

DEFAULT_PORT = 5000
MQTT_BROKER = os.environ.get('MQTT_BROKER', 'broker.hivemq.com')
MQTT_PORT = int(os.environ.get('MQTT_PORT', 1883))


# ==============================
# 3️⃣ REST API for Sensor Data Input
# ==============================

@app.route('/sensor_data', methods=['POST'])
def receive_sensor_data():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON payload received'}), 400

        # Parse and update global sensor_data
        for key in sensor_data.keys():
            sensor_data[key] = data.get(key, None)

        # Save to database
        sql = """
            INSERT INTO sensor_data (suhu, kelembapan, tds, suhu_air, status, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        result = execute_query(sql, (
            sensor_data["suhu"],
            sensor_data["kelembapan"],
            sensor_data["tds"],
            sensor_data["suhu_air"],
            sensor_data["status"],
            sensor_data["timestamp"]
        ))
        
        if result is None:
            # execute_query returns None on error when fetch=False
            # but it's also None on success, so we just log this
            print("Sensor data insert may have failed - check database", file=sys.stderr)

        return jsonify({'status': 'success', 'message': 'Sensor data received and stored.'})
    except Exception as e:
        print(f"receive_sensor_data error: {e}", file=sys.stderr)
        return jsonify({'error': str(e)}), 500


@app.route('/sensor_data', methods=['GET'])
def get_sensor_data():
    """GET endpoint to retrieve the latest sensor data from the database."""
    try:
        # Support optional `limit` query parameter. Default: 1 (latest row).
        # If limit > 1 the endpoint returns a list of rows (newest first).
        limit = request.args.get('limit', None)
        try:
            limit_int = int(limit) if limit is not None else 1
        except Exception:
            limit_int = 1

        # sanitize and cap the limit to avoid very large responses
        if limit_int < 1:
            limit_int = 1
        if limit_int > 10000:
            limit_int = 10000

        q = f"SELECT * FROM sensor_data ORDER BY timestamp DESC LIMIT {limit_int}"
        rows = execute_query(q, fetch=True)
        if rows and len(rows) > 0:
            if limit_int == 1:
                return jsonify({'status': 'success', 'sensor_data': rows[0]})
            else:
                return jsonify({'status': 'success', 'sensor_data': rows})
        else:
            # No DB rows: return in-memory snapshot for single-row requests,
            # or an empty list for multi-row requests.
            if limit_int == 1:
                return jsonify({'status': 'ok', 'sensor_data': sensor_data})
            else:
                return jsonify({'status': 'ok', 'sensor_data': []})
    except Exception as e:
        print(f"get_sensor_data error: {e}", file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# Serve the frontend index.html and other static assets from the `public` folder
@app.route('/')
def index():
    # Log client access (best-effort) for each request
    try:
        response = log_client_details(request)
        if response:
            return response
    except Exception:
        pass

    # If the frontend requests an API action (e.g. ?action=fetch or ?action=history)
    action = request.args.get('action')
    if action:
        # lightweight API handler that returns latest record or history
        def find_best_table():
            # find best candidate table by matching expected sensor columns
            expected = {'tds', 'suhu_air', 'suhu', 'kelembapan', 'temperature', 'timestamp', 'status'}
            db2 = get_db_connection()
            cursor2 = db2.cursor()
            cursor2.execute("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s", (db2.database,))
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
            cursor2.close()
            db2.close()
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
                db3 = get_db_connection()
                cur = db3.cursor(dictionary=True)
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
                cur.close()
                db3.close()
                # Add client IP address to the response
                client_ip = get_client_ip(request)
                if not row:
                    return jsonify({'status':'ok','sensor_data':{'ip_address': client_ip}})
                row['ip_address'] = client_ip
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
                db4 = get_db_connection()
                cur = db4.cursor(dictionary=True)
                if rng == 'all':
                    q = f"SELECT * FROM `{table}` ORDER BY `{ts_col or 'id'}` DESC LIMIT 2000" if ts_col or 'id' in cols else f"SELECT * FROM `{table}` LIMIT 2000"
                    cur.execute(q)
                    rows = cur.fetchall()
                    cur.close()
                    db4.close()
                    return jsonify(rows[::-1])
                if rng == 'today' and ts_col:
                    q = f"SELECT * FROM `{table}` WHERE DATE(`{ts_col}`)=CURDATE() ORDER BY `{ts_col}` ASC"
                    cur.execute(q)
                    rows = cur.fetchall()
                    cur.close()
                    db4.close()
                    return jsonify(rows)
                # numeric days
                try:
                    days = int(rng)
                    if ts_col:
                        q = f"SELECT * FROM `{table}` WHERE `{ts_col}` >= NOW() - INTERVAL %s DAY ORDER BY `{ts_col}` ASC"
                        cur.execute(q, (days,))
                        rows = cur.fetchall()
                        cur.close()
                        db4.close()
                        return jsonify(rows)
                except Exception:
                    pass
                # fallback: return last 200 rows
                q = f"SELECT * FROM `{table}` ORDER BY `{ts_col or 'id'}` DESC LIMIT 200"
                cur.execute(q)
                rows = cur.fetchall()
                cur.close()
                db4.close()
                return jsonify(rows[::-1])
            except Exception as e:
                return jsonify({'status':'error','message':str(e)}), 500

        # unknown action -> 400
        return jsonify({'status':'error','message':'unknown action'}), 400

    # otherwise serve the index.html located in the public/ directory
    return send_from_directory('public', 'index.html')


# Serve static files (CSS, JS, images) from the public folder
@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files from the public folder."""
    return send_from_directory('public', filename)


# The relay control endpoints are now disabled (no MQTT). If needed, implement REST-based relay control here.

@app.route('/debug_clients')
def debug_clients():
    """Debug endpoint: return current rows from client_data for quick inspection.

    Note: This is a diagnostic endpoint. Remove or protect it in production.
    """
    try:
        db5 = get_db_connection()
        cur = db5.cursor(dictionary=True)
        cur.execute("SELECT * FROM client_data ORDER BY client_id DESC LIMIT 200")
        rows = cur.fetchall()
        cur.close()
        db5.close()
        return jsonify({'status':'success','count': len(rows), 'rows': rows})
    except Exception as e:
        return jsonify({'status':'error','message': str(e)}), 500

# ==============================
# 4️⃣ REST API for Hydroponic Plant Recommendations
# ==============================
@app.route('/api/plant_rekomendasi', methods=['GET'])
def get_plant_rekomendasi():
    try:
        rows = execute_query("SELECT * FROM plant_rekomendasi ORDER BY id ASC", fetch=True)
        return jsonify({'status': 'success', 'data': rows})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def publish_mqtt(topic: str, payload: str, retain: bool = False) -> bool:
    """Publish a short message to the MQTT broker and disconnect.
    Returns tuple (success: bool, error_message: str).
    """
    try:
        client = mqtt.Client()
        client.connect(MQTT_BROKER, MQTT_PORT, 5)
        client.loop_start()
        (rc, mid) = client.publish(topic, payload, retain=retain)
        # give brief time for network IO
        time.sleep(0.1)
        client.loop_stop()
        client.disconnect()
        # rc == 0 indicates success for paho-mqtt publish
        if rc == 0:
            return True, ''
        else:
            return False, f'MQTT publish returned rc={rc}'
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"MQTT publish error to {topic}: {e}\n{tb}", file=sys.stderr)
        return False, str(e)


def probe_mqtt_messages(topic_filter: str = 'esp32/hydronion/#', timeout: float = 3.0):
    """Connect to MQTT broker, subscribe to `topic_filter` and collect messages for `timeout` seconds.
    Returns tuple (success: bool, messages: list(dict), error_message: str).
    Each message dict: { 'topic': str, 'payload': str, 'ts': ISO8601 }
    """
    messages = []
    try:
        found = {
            'messages': messages
        }

        def on_message(client, userdata, msg):
            try:
                payload = msg.payload.decode('utf-8', errors='replace')
            except Exception:
                payload = str(msg.payload)
            found['messages'].append({
                'topic': msg.topic,
                'payload': payload,
                'ts': datetime.utcnow().isoformat() + 'Z'
            })

        client = mqtt.Client()
        client.on_message = on_message
        client.connect(MQTT_BROKER, MQTT_PORT, 5)
        client.loop_start()
        client.subscribe(topic_filter)

        # wait up to timeout seconds or until at least one message received
        start = time.time()
        while time.time() - start < float(timeout):
            if found['messages']:
                break
            time.sleep(0.1)

        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass

        return True, found['messages'], ''
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"probe_mqtt_messages error: {e}\n{tb}", file=sys.stderr)
        return False, [], str(e)


@app.route('/api/control/<device>', methods=['POST'])
def api_control(device):
    """Control endpoint to send MQTT commands to ESP32 devices.

    Expects JSON body: { "state": 1 } or { "state": true }
    Supported device values (frontend uses): 'lampu', 'pompa', 'humidifier'
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        state = data.get('state', None)
        # Accept boolean or numeric values
        if state is None:
            return jsonify({'status': 'error', 'message': 'Missing state'}), 400

        # Normalize to boolean
        if isinstance(state, (int, float)):
            is_on = bool(int(state))
        elif isinstance(state, str):
            is_on = state.lower() in ('1', 'true', 'on', 'yes')
        else:
            is_on = bool(state)

        # Map device name to MQTT topic
        mapping = {
            'lampu': 'esp32/hydronion/control/lamp',
            'pompa': 'esp32/hydronion/control/pump',
            'humidifier': 'esp32/hydronion/control/humid',
            'humid': 'esp32/hydronion/control/humid'
        }
        topic = mapping.get(device.lower())
        if not topic:
            return jsonify({'status': 'error', 'message': 'Unknown device'}), 400

        payload = 'on' if is_on else 'off'
        ok, err = publish_mqtt(topic, payload)
        if not ok:
            msg = f'Failed to publish MQTT: {err}' if err else 'Failed to publish MQTT'
            print(msg, file=sys.stderr)
            return jsonify({'status': 'error', 'message': msg}), 500

        return jsonify({'status': 'success', 'device': device, 'state': int(is_on)})
    except Exception as e:
        print('api_control error:', e, file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/check_esp', methods=['GET'])
def api_check_esp():
    """Quick diagnostic: subscribe to ESP32 topics and report any messages seen within timeout seconds.

    Query params:
    - topic: MQTT topic filter to subscribe (default: esp32/hydronion/#)
    - timeout: seconds to wait (default: 3)
    """
    topic = request.args.get('topic', 'esp32/hydronion/#')
    try:
        timeout = float(request.args.get('timeout', '3'))
    except Exception:
        timeout = 3.0

    ok, msgs, err = probe_mqtt_messages(topic_filter=topic, timeout=timeout)
    if not ok:
        return jsonify({'status': 'error', 'message': f'MQTT probe failed: {err}'}), 500

    if not msgs:
        return jsonify({'status': 'ok', 'message': 'No messages seen within timeout', 'messages': []})

    return jsonify({'status': 'ok', 'message': f'{len(msgs)} message(s) seen', 'messages': msgs})


# ==============================
#  MQTT Subscriber: receive sensor messages from ESP32 and save to DB
# ==============================
MQTT_SUB_TOPICS = [
    ('esp32/hydronion/dht/temp', 0),
    ('esp32/hydronion/dht/hum', 0),
    ('esp32/hydronion/ds18b20/temp', 0),
    ('esp32/hydronion/tds/ppm', 0),
]


def mqtt_on_connect(client, userdata, flags, rc):
    try:
        print(f"Connected to MQTT broker (rc={rc})")
        for t, q in MQTT_SUB_TOPICS:
            client.subscribe(t, qos=q)
            print(f"Subscribed to {t}")
    except Exception as e:
        print('mqtt_on_connect error:', e, file=sys.stderr)


def mqtt_on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = msg.payload.decode('utf-8', errors='replace').strip()
        # try parse numeric payloads
        try:
            val = float(payload)
        except Exception:
            val = None

        # Update global sensor_data and insert a row when a relevant value arrives
        updated = False
        if topic.endswith('/dht/temp') and val is not None:
            sensor_data['suhu'] = val
            updated = True
        elif topic.endswith('/dht/hum') and val is not None:
            sensor_data['kelembapan'] = val
            updated = True
        elif topic.endswith('/ds18b20/temp') and val is not None:
            sensor_data['suhu_air'] = val
            updated = True
        elif topic.endswith('/tds/ppm') and val is not None:
            sensor_data['tds'] = val
            updated = True

        # If we've updated one of the main sensor fields, persist a snapshot to DB
        if updated:
            sensor_data['timestamp'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            try:
                sql = """
                    INSERT INTO sensor_data (suhu, kelembapan, tds, suhu_air, status, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                execute_query(sql, (
                    sensor_data.get('suhu'),
                    sensor_data.get('kelembapan'),
                    sensor_data.get('tds'),
                    sensor_data.get('suhu_air'),
                    sensor_data.get('status'),
                    sensor_data.get('timestamp')
                ))
                print(f"Saved sensor snapshot from MQTT topic {topic}: {payload}")
            except Exception as e:
                print('Error saving MQTT sensor snapshot:', e, file=sys.stderr)

    except Exception as e:
        print('mqtt_on_message error:', e, file=sys.stderr)


def start_mqtt_client():
    try:
        client = mqtt.Client()
        client.on_connect = mqtt_on_connect
        client.on_message = mqtt_on_message
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        print('Background MQTT client started')
        return client
    except Exception as e:
        print('Failed to start background MQTT client:', e, file=sys.stderr)
        return None


# -----------------------------
# Client identification / logging helpers
# -----------------------------
def ensure_client_table():
    """Create client_data table if it doesn't exist."""
    try:
        db = get_db_connection()
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
        cur.close()
        db.close()
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
    """Detect client IP address and insert into client_data table."""
    try:
        ensure_client_table()
        ip = get_client_ip(req)

        # Insert IP address as device_id if not exists
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("SELECT client_id FROM client_data WHERE device_id=%s", (ip,))
        if not cur.fetchone():
            cur.execute("INSERT INTO client_data (device_id) VALUES (%s)", (ip,))
            db.commit()
        cur.close()
        db.close()
    except Exception as e:
        # Log the error but don't break the request
        print('log_client_access error:', e, file=sys.stderr)


def log_client_details(req):
    """Log client details including MAC/IP, browser, and access time. Avoid duplicates using cookies."""
    try:
        ensure_client_details_table()
        ip = get_client_ip(req)
        mac = get_mac_from_arp(ip)
        user_agent = req.headers.get('User-Agent', 'unknown')

        # Check for existing cookie
        client_cookie = request.cookies.get('client_id')
        if client_cookie:
            print(f"Client already logged: {client_cookie}", file=sys.stderr)
            return

        # Fetch location details using iplocate.io API
        location_url = "https://iplocate.io/api/lookup"
        country, region = None, None
        try:
            response = requests.get(location_url, timeout=5)
            if response.status_code == 200:
                location_data = response.json()
                country = location_data.get('country')
                region = location_data.get('subdivision')
            else:
                print(f"Failed to fetch location data. Status code: {response.status_code}", file=sys.stderr)
        except Exception as e:
            print('Failed to fetch location data:', e, file=sys.stderr)

        # Get current date and time
        date = datetime.utcnow().date()
        time_now = datetime.utcnow().time()

        # Insert details into the database
        db = get_db_connection()
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO client_details (mac_address, ip_address, user_agent, country, region, access_date, access_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (mac, ip, user_agent, country, region, date, time_now)  
        )
        db.commit()
        cur.close()
        db.close()

        # Set a cookie to avoid duplicate entries
        response = jsonify({'status': 'success', 'message': 'Client logged successfully'})
        response.set_cookie('client_id', f'{ip}-{mac}', max_age=30 * 24 * 60 * 60)  # 30 days
        return response
    except Exception as e:
        print('log_client_details error:', e, file=sys.stderr)

@app.route("/register", methods=["POST"])
def register():
    try:
        # Accept JSON or form-encoded payloads (mobile clients often send form data)
        data = request.get_json(silent=True)
        if not data:
            # fallback to form data
            data = request.form.to_dict() if request.form else {}
        try:
            req = RegisterRequest(**data)
        except ValidationError as ve:
            return jsonify({"message": "Invalid input", "errors": ve.errors()}), 400

        username = req.username
        password = req.password

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Check if username exists
        cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"message": "Username sudah ada"}), 400

        # Hash password securely
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        # Insert user (no email/verification)
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            (username, hashed_password, "user")
        )
        db.commit()
        user_id = cursor.lastrowid
        cursor.close()
        db.close()

        return jsonify({
            "message": "Registrasi berhasil! Anda dapat langsung login.",
            "user_id": user_id
        })

    except mysql.connector.Error as db_error:
        print(f"Database error in register: {db_error}", file=sys.stderr)
        return jsonify({"message": f"Database error: {str(db_error)}"}), 500
    except Exception as e:
        print(f"Error in register: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Server error: {str(e)}"}), 500


@app.route("/login", methods=["POST"])
def login():
    try:
        # Accept JSON or form-encoded payloads
        data = request.get_json(silent=True)
        if not data:
            data = request.form.to_dict() if request.form else {}
        try:
            req = LoginRequest(**data)
        except ValidationError as ve:
            return jsonify({"message": "Invalid input", "errors": ve.errors()}), 400

        username = req.username
        password = req.password

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Fetch user data including hashed password
        cursor.execute(
            "SELECT id, username, password, role FROM users WHERE username=%s",
            (username,)
        )
        user = cursor.fetchone()

        cursor.close()
        db.close()

        if not user:
            return jsonify({"message": "Username atau password salah"}), 401

        # Verify password
        if not check_password_hash(user['password'], password):
            return jsonify({"message": "Username atau password salah"}), 401

        # Return user data without password
        return jsonify({
            "id": user['id'],
            "username": user['username'],
            "role": user['role'],
            "message": "Login berhasil"
        })
    
    except mysql.connector.Error as db_error:
        print(f"Database error in login: {db_error}", file=sys.stderr)
        return jsonify({"message": f"Database error: {str(db_error)}"}), 500
    except Exception as e:
        print(f"Error in login: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Server error: {str(e)}"}), 500







# Remove the /ban_client endpoint

# Define ngrok path and default port
NGROK_PATH = r"C:\ngrok-v3-stable-windows-amd64\ngrok.exe"
NGROK_AUTHTOKEN = "2rFRohjWilTo8F3FkWMI8HrIf6E_2wnkYBs8LbJ72NVJA3JQs"
DEFAULT_PORT = 5000

def start_ngrok(port: int = DEFAULT_PORT, hostname: str | None = None, authtoken: str | None = None):
    """Start ngrok and return the public URL (or None on failure)."""
    if not os.path.isfile(NGROK_PATH):
        print(f"ngrok executable not found at {NGROK_PATH}. Please install ngrok and set NGROK_PATH accordingly.")
        return None

    cmd = [NGROK_PATH, 'http', str(port)]
    if hostname:
        cmd += ['--hostname', hostname]

    env = os.environ.copy()
    if authtoken:
        env['NGROK_AUTHTOKEN'] = authtoken

    print('Starting ngrok with command:', ' '.join(cmd))
    try:
        subprocess.Popen(cmd, env=env)
    except Exception as e:
        print('Failed to start ngrok process:', e)
        return None

    # Wait a short moment for ngrok to initialize its local API
    time.sleep(2)

    # Query the local ngrok API for the public tunnel URL
    try:
        res = requests.get('http://localhost:4040/api/tunnels', timeout=2.5)
        data = res.json()
        tunnels = data.get('tunnels') or []
        if not tunnels:
            return None
        # prefer an https public_url if present
        for t in tunnels:
            url = t.get('public_url')
            if url and url.startswith('https'):
                return url
        # fallback to first available
        return tunnels[0].get('public_url')
    except Exception:
        return None

# ==============================
# Main Entry Point
# ==============================
if __name__ == '__main__':
    print(f"🟡 Starting Flask server at 0.0.0.0:{DEFAULT_PORT}")
    print("🔄 Starting Ngrok tunnel...")

    ngrok_url = start_ngrok(DEFAULT_PORT)
    if ngrok_url:
        print(f"✅ Public URL (akses dari WiFi lain / internet): {ngrok_url}")
    else:
        print("❌ Ngrok gagal dijalankan! Pastikan ngrok terinstall & login pakai auth token.")

    # Start background MQTT subscriber (if broker reachable)
    try:
        mqtt_bg_client = start_mqtt_client()
    except Exception:
        mqtt_bg_client = None

    # Run Flask
    app.run(host='0.0.0.0', port=DEFAULT_PORT, debug=True)
