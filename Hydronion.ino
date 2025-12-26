#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ======================
// WiFi & MQTT Config
// ======================
const char* ssid = "NAMA_WIFI";
const char* password = "PASSWORD_WIFI";
const char* mqtt_server = "192.168.1.10"; // IP Broker MQTT

WiFiClient espClient;
PubSubClient client(espClient);

// ======================
// DHT22 Sensor
// ======================
#define DHTPIN 13
#define DHTTYPE DHT22
DHT dht(DHTPIN, DHTTYPE);

// ======================
// DS18B20 Sensor
// ======================
#define ONE_WIRE_BUS 27
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

// ======================
// Relay Outputs (LOW = ON)
// ======================
#define RELAY_LAMP 12
#define RELAY_PUMP 14

// ======================
// Humidifier
// ======================
#define HUMID_PIN 25
#define HUMID_ON HIGH
#define HUMID_OFF LOW

unsigned long lastPublish = 0;

// ======================
// MQTT Callback
// ======================
void callback(char* topic, byte* message, unsigned int length) {
  String msg;
  for (int i = 0; i < length; i++) {
    msg += (char)message[i];
  }
  msg.toLowerCase();

  if (String(topic) == "esp32/hydronion/control/lamp") {
    digitalWrite(RELAY_LAMP, msg == "on" ? LOW : HIGH);
  }

  else if (String(topic) == "esp32/hydronion/control/pump") {
    digitalWrite(RELAY_PUMP, msg == "on" ? LOW : HIGH);
  }

  else if (String(topic) == "esp32/hydronion/control/humid") {
    digitalWrite(HUMID_PIN, msg == "on" ? HUMID_ON : HUMID_OFF);
  }
}

// ======================
// Reconnect MQTT
// ======================
void reconnect() {
  while (!client.connected()) {
    if (client.connect("ESP32_Hydronion")) {

      client.subscribe("esp32/hydronion/control/lamp");
      client.subscribe("esp32/hydronion/control/pump");
      client.subscribe("esp32/hydronion/control/humid");

    } else {
      delay(5000);
    }
  }
}

// ======================
// Setup
// ======================
void setup() {
  Serial.begin(115200);

  pinMode(RELAY_LAMP, OUTPUT);
  pinMode(RELAY_PUMP, OUTPUT);
  pinMode(HUMID_PIN, OUTPUT);

  digitalWrite(RELAY_LAMP, LOW);
  digitalWrite(RELAY_PUMP, LOW);
  digitalWrite(HUMID_PIN, HUMID_OFF);

  dht.begin();
  sensors.begin();

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }

  client.setServer(mqtt_server, 1883);
  client.setCallback(callback);

  Serial.println("System Ready + MQTT Connected");
}

// ======================
// Loop
// ======================
void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  unsigned long now = millis();
  if (now - lastPublish > 5000) {
    lastPublish = now;

    float hum = dht.readHumidity();
    float tempDHT = dht.readTemperature();
    sensors.requestTemperatures();
    float tempDS = sensors.getTempCByIndex(0);

    if (!isnan(hum) && !isnan(tempDHT)) {
      client.publish("esp32/hydronion/dht/temp", String(tempDHT).c_str());
      client.publish("esp32/hydronion/dht/hum",  String(hum).c_str());
    }

    if (tempDS != DEVICE_DISCONNECTED_C) {
      client.publish("esp32/hydronion/ds18b20/temp", String(tempDS).c_str());
    }

    Serial.println("Data Hydronion sent to MQTT");
  }
}
