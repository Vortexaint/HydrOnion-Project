#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <EEPROM.h>
#include "GravityTDS.h"

// ======================
// WiFi & MQTT Config
// ======================
// const char* ssid = "Poenya_VOC";
// const char* password = "Rezkybesi123";
const char* ssid = "Berlin Auths";
const char* password = "12345678";
const char* mqtt_server = "broker.hivemq.com";

WiFiClient espClient;
PubSubClient client(espClient);

// ======================
// DHT11 Sensor
// ======================
#define DHTPIN 4        // GPIO connected to DATA
#define DHTTYPE DHT11   // DHT11

DHT dht(DHTPIN, DHTTYPE);

// ======================
// DS18B20 Sensor
// ======================
#define ONE_WIRE_BUS 27
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

// ======================
// Relay Outputs (HIGH = ON for NO relay)
// ======================
#define RELAY_LAMP 33
#define RELAY_PUMP 32
#define RELAY_ON_LEVEL HIGH

unsigned long lastPublish = 0;

// Relay helpers
bool readRelayState(int pin) {
  return digitalRead(pin) == RELAY_ON_LEVEL;
}

void publishRelayState(int pin, const char* topic) {
  if (!client.connected()) return;
  const char* state = readRelayState(pin) ? "on" : "off";
  client.publish(topic, state);
}

void setRelay(int pin, bool on) {
  int level = on ? RELAY_ON_LEVEL : (RELAY_ON_LEVEL == HIGH ? LOW : HIGH);
  digitalWrite(pin, level);
  
  // Small delay to ensure relay switching is stable
  delay(50);
  
  Serial.print("Relay Pin "); 
  Serial.print(pin); 
  Serial.print(": "); 
  Serial.print(on ? "ON" : "OFF");
  Serial.print(" (Level: ");
  Serial.print(level == HIGH ? "HIGH" : "LOW");
  Serial.println(")");
  
  if (client.connected()) {
    if (pin == RELAY_LAMP) {
      bool publishOk = client.publish("esp32/hydronion/state/lamp", on ? "on" : "off");
      Serial.print("Published lamp state: ");
      Serial.println(publishOk ? "SUCCESS" : "FAILED");
    }
    else if (pin == RELAY_PUMP) {
      bool publishOk = client.publish("esp32/hydronion/state/pump", on ? "on" : "off");
      Serial.print("Published pump state: ");
      Serial.println(publishOk ? "SUCCESS" : "FAILED");
    }
  } else {
    Serial.println("Warning: MQTT not connected, state not published");
  }
}

// ======================
// TDS Sensor (Total Dissolved Solids)
#define TdsSensorPin 35
GravityTDS gravityTds;
float waterTemp = 25, tdsValue = 0;

// ======================
// MQTT Callback
// ======================
void callback(char* topic, byte* message, unsigned int length) {
  String msg;
  for (int i = 0; i < length; i++) {
    msg += (char)message[i];
  }
  msg.toLowerCase();
  
  // Debug: print received MQTT message
  Serial.print("MQTT Received - Topic: ");
  Serial.print(topic);
  Serial.print(", Message: ");
  Serial.println(msg);

  if (String(topic) == "esp32/hydronion/control/lamp") {
    bool turnOn = (msg == "on");
    Serial.print("Setting LAMP to: ");
    Serial.println(turnOn ? "ON" : "OFF");
    setRelay(RELAY_LAMP, turnOn);
  }

  else if (String(topic) == "esp32/hydronion/control/pump") {
    bool turnOn = (msg == "on");
    Serial.print("Setting PUMP to: ");
    Serial.println(turnOn ? "ON" : "OFF");
    setRelay(RELAY_PUMP, turnOn);
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

      // publish current states after reconnect so subscribers (UI) can sync
      if (client.connected()) {
        publishRelayState(RELAY_LAMP, "esp32/hydronion/state/lamp");
        publishRelayState(RELAY_PUMP, "esp32/hydronion/state/pump");
      }

    } else {
      delay(5000);
    }
  }
}

// ======================
// Serial command handler
// ======================
void handleSerialCommand(String cmd) {
  cmd.trim();
  cmd.toLowerCase();

  if (cmd == "lamp on") {
    setRelay(RELAY_LAMP, true);
  }
  else if (cmd == "lamp off") {
    setRelay(RELAY_LAMP, false);
  }

  else if (cmd == "pump on") {
    setRelay(RELAY_PUMP, true);
  }
  else if (cmd == "pump off") {
    setRelay(RELAY_PUMP, false);
  }

  else if (cmd == "status") {
    Serial.println("=== STATUS HYDRONION ===");
    Serial.print("Lamp  : ");
    Serial.println(readRelayState(RELAY_LAMP) ? "ON" : "OFF");

    Serial.print("Pump  : ");
    Serial.println(readRelayState(RELAY_PUMP) ? "ON" : "OFF");
  }

  else if (cmd == "help") {
    Serial.println("=== COMMAND LIST ===");
    Serial.println("lamp on / lamp off");
    Serial.println("pump on / pump off");
    // humid commands removed from help
    Serial.println("status");
    Serial.println("help");
  }

  else {
    Serial.println("Command tidak dikenal. Ketik: help");
  }
}

// ======================
// Setup
// ======================
void setup() {
  Serial.begin(115200);

  pinMode(RELAY_LAMP, OUTPUT);
  pinMode(RELAY_PUMP, OUTPUT);
  // Set relays OFF by default (lamp and pump will be OFF at startup)
  setRelay(RELAY_LAMP, false);
  setRelay(RELAY_PUMP, false);

  // Initialize TDS sensor
  gravityTds.setPin(TdsSensorPin);
  gravityTds.setAref(3.3);  // ESP32 ADC reference voltage is 3.3V
  // Ensure proper ADC attenuation on ESP32 so probe voltages map correctly
  analogSetPinAttenuation(TdsSensorPin, ADC_11db);
  // Use 4095 as the ADC max value for scaling (12-bit: 0-4095)
  gravityTds.setAdcRange(4095);  // ESP32 has 12-bit ADC (0-4095)
  
  // Initialize EEPROM before begin() so library can read/write kValue
  EEPROM.begin(32);  // allocate 32 bytes for EEPROM emulation on ESP32
  
  gravityTds.begin();  // initialization (reads kValue from EEPROM)
  
  // Debug: print the kValue loaded from EEPROM
  Serial.print("TDS Sensor K-Value: ");
  Serial.println(gravityTds.getKvalue(), 4);

  // Enable internal pull-up for DS18B20 data pin
  pinMode(ONE_WIRE_BUS, INPUT_PULLUP);
  
  // DHT11 sensor initialization
  Serial.println("DHT11 Tester Starting...");
  dht.begin();
  
  sensors.begin();
  
  // Check if DS18B20 is connected
  Serial.print("DS18B20 devices found: ");
  Serial.println(sensors.getDeviceCount());
  if (sensors.getDeviceCount() == 0) {
    Serial.println("WARNING: No DS18B20 sensor detected! Check wiring:");
    Serial.println("  Red (VCC) -> 3.3V");
    Serial.println("  Black (GND) -> GND");
    Serial.println("  Yellow (Data) -> GPIO 27");
    Serial.println("  Note: Data line needs 4.7kΩ pull-up resistor to VCC");
  }

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

  // Handle simple serial commands from USB terminal
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    handleSerialCommand(command);
  }

  unsigned long now = millis();
  if (now - lastPublish > 5000) {
    lastPublish = now;

    Serial.println("\n=== SENSOR READINGS ===");
    
    // DHT11 sensor (needs at least 1 second between readings)
    float hum = dht.readHumidity();
    float tempDHT = dht.readTemperature(); // Celsius
    
    bool sensorOk = false;
    if (isnan(hum) || isnan(tempDHT)) {
      Serial.println("❌ DHT11 Failed to read!");
      Serial.println("Check wiring, power, or pull-up resistor.");
    } else {
      Serial.print("✅ DHT11 - Temperature: ");
      Serial.print(tempDHT);
      Serial.print(" °C  |  Humidity: ");
      Serial.print(hum);
      Serial.println(" %");
      sensorOk = true;
    }

    // DS18B20 water temperature
    sensors.requestTemperatures();
    float tempDS = sensors.getTempCByIndex(0);
    Serial.print("DS18B20 - Suhu Air: ");
    if (tempDS != DEVICE_DISCONNECTED_C) {
      Serial.print(tempDS);
      Serial.println("°C");
    } else {
      Serial.println("DISCONNECTED");
    }

    // TDS sensor with GravityTDS library
    // Use water temperature (DS18B20) for TDS compensation, not air temperature
    if (tempDS != DEVICE_DISCONNECTED_C) {
      waterTemp = tempDS;  // use water temperature for accurate TDS reading
    } else if (!isnan(tempDHT)) {
      waterTemp = tempDHT;  // fallback to air temperature if water sensor disconnected
    } else {
      waterTemp = 25;  // default 25°C if no sensors available
    }
    
    gravityTds.setTemperature(waterTemp);  // temperature compensation
    gravityTds.update();  // sample and calculate
    tdsValue = gravityTds.getTdsValue();  // get the TDS value
    
    // Read raw ADC/voltage for debug + get EC from library
    int rawAdc = analogRead(TdsSensorPin);
    float voltage4096 = rawAdc / 4096.0f * 3.3f;
    float voltage4095 = rawAdc / 4095.0f * 3.3f;
    float ecValue = gravityTds.getEcValue(); // µS/cm after temperature compensation

    Serial.print("TDS - Value: ");
    Serial.print(tdsValue, 0);
    Serial.print(" ppm (compensated at ");
    Serial.print(waterTemp, 1);
    Serial.println("°C)");
    Serial.print("  [ADC:"); Serial.print(rawAdc);
    Serial.print("  V(4096):"); Serial.print(voltage4096, 3);
    Serial.print(" V(4095):"); Serial.print(voltage4095, 3);
    Serial.print(" V  EC:"); Serial.print(ecValue, 1);
    Serial.println(" µS/cm]");
    if (ecValue == 0.0) {
      Serial.println("Note: EC==0 — check calibration/K-factor, probe immersion, or ADC scaling.");
    }

    // Publish to MQTT
    if (sensorOk) {
      bool dhtTempOk = client.publish("esp32/hydronion/dht/temp", String(tempDHT).c_str());
      bool dhtHumOk = client.publish("esp32/hydronion/dht/hum",  String(hum).c_str());
      Serial.print("MQTT DHT Temp: "); Serial.print(dhtTempOk ? "OK" : "FAIL");
      Serial.print(", Hum: "); Serial.println(dhtHumOk ? "OK" : "FAIL");
    }

    if (tempDS != DEVICE_DISCONNECTED_C) {
      bool dsOk = client.publish("esp32/hydronion/ds18b20/temp", String(tempDS).c_str());
      Serial.print("MQTT DS18B20: "); Serial.println(dsOk ? "OK" : "FAIL");
    }

    if (client.connected()) {
      char buf[16];
      snprintf(buf, sizeof(buf), "%.0f", tdsValue);
      bool tdsOk = client.publish("esp32/hydronion/tds/ppm", buf);
      Serial.print("MQTT TDS: "); Serial.println(tdsOk ? "OK" : "FAIL");

      // publish EC (uS/cm) as additional telemetry
      char buf2[16];
      snprintf(buf2, sizeof(buf2), "%.1f", ecValue);
      bool ecOk = client.publish("esp32/hydronion/tds/ec", buf2);
      Serial.print("MQTT EC: "); Serial.println(ecOk ? "OK" : "FAIL");
    }

    Serial.println("Data sent to MQTT");
    Serial.println("=======================\n");
  }
}

// Median filter helper used for TDS
int getMedianNum(int bArray[], int iFilterLen) {
  int bTab[iFilterLen];
  for (byte i = 0; i < iFilterLen; i++) bTab[i] = bArray[i];
  int i, j, bTemp;
  for (j = 0; j < iFilterLen - 1; j++) {
    for (i = 0; i < iFilterLen - j - 1; i++) {
      if (bTab[i] > bTab[i + 1]) {
        bTemp = bTab[i];
        bTab[i] = bTab[i + 1];
        bTab[i + 1] = bTemp;
      }
    }
  }
  if ((iFilterLen & 1) > 0)
    bTemp = bTab[(iFilterLen - 1) / 2];
  else
    bTemp = (bTab[iFilterLen / 2] + bTab[iFilterLen / 2 - 1]) / 2;
  return bTemp;
}
