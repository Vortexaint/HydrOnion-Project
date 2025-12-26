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

# Email Configuration (Gmail) - Optional, won't crash if not configured
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
            `email` VARCHAR(255) NOT NULL UNIQUE,
            `password` VARCHAR(255) NOT NULL,
            `role` VARCHAR(50) DEFAULT 'user',
            `email_verified` TINYINT(1) DEFAULT 0,
            `verification_token` VARCHAR(255) NULL,
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
        
        # Add email column if missing
        if 'email' not in existing_columns:
            print("Adding 'email' column to users table...")
            cursor.execute("ALTER TABLE users ADD COLUMN email VARCHAR(255) NULL AFTER username")
            db.commit()
        
        # Add email_verified column if missing
        if 'email_verified' not in existing_columns:
            print("Adding 'email_verified' column to users table...")
            cursor.execute("ALTER TABLE users ADD COLUMN email_verified TINYINT(1) DEFAULT 0 AFTER role")
            db.commit()
        
        # Add verification_token column if missing
        if 'verification_token' not in existing_columns:
            print("Adding 'verification_token' column to users table...")
            cursor.execute("ALTER TABLE users ADD COLUMN verification_token VARCHAR(255) NULL AFTER email_verified")
            db.commit()
        
        # Add created_at column if missing
        if 'created_at' not in existing_columns:
            print("Adding 'created_at' column to users table...")
            cursor.execute("ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            db.commit()
        
        # Make email unique if it exists but isn't unique yet
        if 'email' in existing_columns:
            try:
                cursor.execute("ALTER TABLE users ADD UNIQUE KEY unique_email (email)")
                db.commit()
                print("Added unique constraint to email column")
            except mysql.connector.Error as e:
                if e.errno != 1061:  # 1061 = Duplicate key name
                    print(f"Could not add unique constraint to email: {e}")
        
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
MQTT_BROKER = os.environ.get('MQTT_BROKER', '192.168.1.10')
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
    Returns True on success, False on failure."""
    try:
        client = mqtt.Client()
        client.connect(MQTT_BROKER, MQTT_PORT, 5)
        client.loop_start()
        client.publish(topic, payload, retain=retain)
        # give brief time for network IO
        time.sleep(0.1)
        client.loop_stop()
        client.disconnect()
        return True
    except Exception as e:
        print(f"MQTT publish error to {topic}: {e}", file=sys.stderr)
        return False


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
        ok = publish_mqtt(topic, payload)
        if not ok:
            return jsonify({'status': 'error', 'message': 'Failed to publish MQTT'}), 500

        return jsonify({'status': 'success', 'device': device, 'state': int(is_on)})
    except Exception as e:
        print('api_control error:', e, file=sys.stderr)
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
        data = request.json
        username = data.get("username")
        email = data.get("email")
        password = data.get("password")

        if not username or not email or not password:
            return jsonify({"message": "Data tidak lengkap (username, email, password)"}), 400

        # Validate email format
        if '@' not in email or '.' not in email:
            return jsonify({"message": "Format email tidak valid"}), 400

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Check if username exists
        cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"message": "Username sudah ada"}), 400

        # Check if email exists
        cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"message": "Email sudah terdaftar"}), 400

        # Hash password securely
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        # Generate verification token
        verification_token = str(uuid.uuid4())

        # Insert user
        cursor.execute(
            """INSERT INTO users (username, email, password, role, email_verified, verification_token) 
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (username, email, hashed_password, "user", 0, verification_token)
        )
        db.commit()
        user_id = cursor.lastrowid
        cursor.close()
        db.close()

        # Send verification email to user
        try:
            if EMAIL_ENABLED and mail:
                verification_link = f"http://localhost:{DEFAULT_PORT}/verify_email?token={verification_token}"
                msg = Message(
                    subject="Verifikasi Email HydrOnion",
                    recipients=[email],
                    body=f"""Halo {username},

Terima kasih telah mendaftar di HydrOnion!

Silakan klik link berikut untuk verifikasi email Anda:
{verification_link}

Jika Anda tidak mendaftar, abaikan email ini.

Salam,
Tim HydrOnion"""
                )
                mail.send(msg)
                print(f"Verification email sent to {email}")
            else:
                print(f"Email disabled - verification link: http://localhost:{DEFAULT_PORT}/verify_email?token={verification_token}")
        except Exception as e:
            print(f"Failed to send verification email: {e}", file=sys.stderr)

        # Send notification to admin
        try:
            if EMAIL_ENABLED and mail:
                admin_msg = Message(
                    subject="Pendaftaran User Baru HydrOnion",
                    recipients=["off19132@gmail.com"],
                    body=f"""User baru telah mendaftar:

Username: {username}
Email: {email}
User ID: {user_id}
Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Status: Menunggu verifikasi email"""
                )
                mail.send(admin_msg)
        except Exception as e:
            print(f"Failed to send admin notification: {e}", file=sys.stderr)

        return jsonify({
            "message": "Registrasi berhasil! Silakan cek email Anda untuk verifikasi.",
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
        data = request.json
        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return jsonify({"message": "Username dan password harus diisi"}), 400

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Fetch user data including hashed password
        cursor.execute(
            "SELECT id, username, email, password, role, email_verified FROM users WHERE username=%s",
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

        # Check email verification
        if not user['email_verified']:
            return jsonify({
                "message": "Email belum diverifikasi. Silakan cek email Anda.",
                "email_verified": False
            }), 403

        # Return user data without password
        return jsonify({
            "id": user['id'],
            "username": user['username'],
            "email": user['email'],
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




@app.route("/verify_email", methods=["GET"])
def verify_email():
    """Verify user email using token from the verification link."""
    token = request.args.get('token')
    
    if not token:
        return jsonify({"message": "Token verifikasi tidak ditemukan"}), 400

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Find user with this token
    cursor.execute(
        "SELECT id, username, email, email_verified FROM users WHERE verification_token=%s",
        (token,)
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        db.close()
        return jsonify({"message": "Token verifikasi tidak valid"}), 404

    if user['email_verified']:
        cursor.close()
        db.close()
        return jsonify({"message": "Email sudah diverifikasi sebelumnya"}), 200

    # Update user as verified
    cursor.execute(
        "UPDATE users SET email_verified=1, verification_token=NULL WHERE id=%s",
        (user['id'],)
    )
    db.commit()
    cursor.close()
    db.close()

    # Send notification to admin about successful verification
    try:
        if EMAIL_ENABLED and mail:
            admin_msg = Message(
                subject="Email Terverifikasi - HydrOnion",
                recipients=["off19132@gmail.com"],
                body=f"""User telah memverifikasi email mereka:

Username: {user['username']}
Email: {user['email']}
User ID: {user['id']}
Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

User sekarang dapat login ke sistem."""
            )
            mail.send(admin_msg)
    except Exception as e:
        print(f"Failed to send admin verification notification: {e}", file=sys.stderr)

    return jsonify({
        "message": "Email berhasil diverifikasi! Anda sekarang bisa login.",
        "username": user['username']
    })


@app.route("/resend_verification", methods=["POST"])
def resend_verification():
    """Resend verification email if user didn't receive it."""
    data = request.json
    email = data.get("email")
    
    if not email:
        return jsonify({"message": "Email harus diisi"}), 400

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        "SELECT id, username, email, email_verified, verification_token FROM users WHERE email=%s",
        (email,)
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        db.close()
        return jsonify({"message": "Email tidak ditemukan"}), 404

    if user['email_verified']:
        cursor.close()
        db.close()
        return jsonify({"message": "Email sudah diverifikasi"}), 200

    # Generate new token if needed
    if not user['verification_token']:
        new_token = str(uuid.uuid4())
        cursor.execute(
            "UPDATE users SET verification_token=%s WHERE id=%s",
            (new_token, user['id'])
        )
        db.commit()
        verification_token = new_token
    else:
        verification_token = user['verification_token']

    cursor.close()
    db.close()

    # Send verification email
    try:
        if EMAIL_ENABLED and mail:
            verification_link = f"http://localhost:{DEFAULT_PORT}/verify_email?token={verification_token}"
            msg = Message(
                subject="Verifikasi Email HydrOnion (Kirim Ulang)",
                recipients=[email],
                body=f"""Halo {user['username']},

Berikut link verifikasi email Anda:
{verification_link}

Jika Anda tidak meminta email ini, abaikan pesan ini.

Salam,
Tim HydrOnion"""
            )
            mail.send(msg)
            return jsonify({"message": "Email verifikasi telah dikirim ulang"})
        else:
            print(f"Email disabled - verification link: http://localhost:{DEFAULT_PORT}/verify_email?token={verification_token}")
            return jsonify({"message": "Email sistem tidak aktif. Silakan hubungi administrator."})
    except Exception as e:
        print(f"Failed to resend verification email: {e}", file=sys.stderr)
        return jsonify({"message": "Gagal mengirim email verifikasi"}), 500


@app.route("/admin/hash_passwords", methods=["POST"])
def admin_hash_passwords():
    """Admin endpoint to hash plain text passwords for users ID 1-4"""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        # Get users 1-4
        cursor.execute("SELECT id, username, password FROM users WHERE id BETWEEN 1 AND 4")
        users = cursor.fetchall()
        
        updated = []
        skipped = []
        
        for user in users:
            password = user['password']
            
            # Check if already hashed
            if password.startswith('pbkdf2:sha256:') or password.startswith('scrypt:'):
                skipped.append(f"ID {user['id']} ({user['username']}) - already hashed")
                continue
            
            # Hash the plain text password
            hashed = generate_password_hash(password, method='pbkdf2:sha256')
            
            # Update in database
            cursor.execute(
                "UPDATE users SET password=%s WHERE id=%s",
                (hashed, user['id'])
            )
            updated.append(f"ID {user['id']} ({user['username']}) - password hashed")
        
        db.commit()
        cursor.close()
        db.close()
        
        return jsonify({
            "message": "Password hashing completed",
            "updated": updated,
            "skipped": skipped
        })
        
    except Exception as e:
        print(f"Error in admin_hash_passwords: {e}", file=sys.stderr)
        return jsonify({"message": f"Error: {str(e)}"}), 500


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

    # Run Flask
    app.run(host='0.0.0.0', port=DEFAULT_PORT, debug=True)
