"""
sensor_service.py
===================

This module runs on a Raspberry Pi 5 and periodically reads a set of sensors
connected to the device.  Every two seconds it collects temperature,
humidity, air‑quality measurements, motion detection states and GPS position
information, packages everything into a dictionary and publishes it to an
MQTT broker.  It also writes the latest sensor readings to a local JSON
file so that another program can consume the data (for example to trigger
SMS notifications via the EC20 modem).

Hardware overview:

* **Raspberry Pi 5** running Raspberry Pi OS Bookworm.
* **DHT22** temperature and humidity sensor wired to GPIO4 (BCM numbering).
* **SGP30** TVOC/eCO₂ gas sensor using I²C on SDA GPIO2 and SCL GPIO3.
* **SR501 PIR motion sensor** connected to GPIO17.
* **Microwave radar motion sensor** connected to GPIO27.
* **Quectel EC20 4G modem** attached via USB; its GNSS output appears as
  a serial device (usually ``/dev/ttyUSB1`` at 115200 baud) that streams
  standard NMEA sentences.  According to Firefly’s documentation the EC20
  outputs one sentence per second via the USB NMEA interface and the
  default serial port is ``/dev/ttyUSB1``【810318060161890†L388-L394】.

The code tolerates occasional read errors: if a sensor fails to provide
readings the corresponding fields are set to ``None`` so that the program
continues running.  When the GPS module has not obtained a fix or is
unavailable, the ``gps`` field will contain the string ``"NO_GPS"``.

Before running this program make sure the following prerequisites are met:

1. Enable I²C and add your user to the ``gpio`` group.  On Raspberry Pi
   OS Bookworm the legacy ``RPi.GPIO`` library does not work on Pi 5;
   instead install the ``rpi‑lgpio`` package which provides a drop‑in
   replacement for ``RPi.GPIO``【960137720310995†L30-L40】.  Install it with
   ``sudo apt install python3-rpi-lgpio`` and reboot.
2. Install the Python dependencies listed in ``requirements.txt`` inside a
   virtual environment or with the ``--break-system-packages`` flag if
   installing system‑wide.  See the accompanying documentation for
   suggested commands.

Configure the MQTT settings below before running.  You can customise
``MQTT_BROKER``, ``MQTT_PORT``, ``MQTT_TOPIC`` and optional username or
password.  The script will automatically reconnect if the broker is
reachable.
"""

import json
import logging
import os
import time
from typing import Optional

import paho.mqtt.client as mqtt
import serial
import pynmea2

# Adafruit sensor libraries
import board
import busio
import adafruit_dht
import adafruit_sgp30

# GPIO library – provided by rpi‑lgpio which emulates RPi.GPIO on Pi 5
import RPi.GPIO as GPIO  # type: ignore


###############################################################################
# Configuration
###############################################################################

# MQTT broker configuration.  Replace these values with those provided by
# your EMQX or other MQTT broker.  The topic can be customised; here we
# assume a JSON payload is published under a single topic.
MQTT_BROKER = os.getenv("MQTT_BROKER", "47.94.209.246")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "zys") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "zys-041122") or None
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "vehicle/sensor")

# Local file where the most recent sensor sample is stored.  Another
# application (such as one that sends SMS alerts via the EC20) can read
# this file to obtain up‑to‑date sensor data.
SENSOR_DATA_FILE = os.getenv("SENSOR_DATA_FILE", "/tmp/sensor_data.json")

# GPS serial port and baud rate for the EC20.  If your EC20 appears at
# another port adjust this accordingly.  Firefly documentation specifies
# the default port and rate as ``/dev/ttyUSB1`` at 115200 baud【810318060161890†L388-L394】.
GPS_SERIAL_PORT = os.getenv("GPS_SERIAL_PORT", "/dev/ttyUSB1")
GPS_BAUDRATE = int(os.getenv("GPS_BAUDRATE", "115200"))
GPS_READ_TIMEOUT = float(os.getenv("GPS_READ_TIMEOUT", "0.2"))

# Sensor pins (BCM numbering)
DHT_PIN = 4  # DHT22 data line on GPIO4
PIR_PIN = 17  # SR501 PIR sensor on GPIO17
MICROWAVE_PIN = 27  # Microwave radar sensor on GPIO27

# Interval between sensor polls in seconds
READ_INTERVAL = 2.0


def setup_logging() -> None:
    """Configure the root logger for informative console output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


class SensorService:
    """Encapsulates all hardware initialisation and periodic sampling."""

    def __init__(self) -> None:
        # Initialise I2C bus for the SGP30 sensor
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.sgp30 = adafruit_sgp30.Adafruit_SGP30(self.i2c)
        logging.info("SGP30 initialised; serial number %s", [hex(i) for i in self.sgp30.serial])

        # Initialise the DHT22 sensor on the specified pin
        self.dht_device = adafruit_dht.DHT22(getattr(board, f"D{DHT_PIN}"), use_pulseio=False)

        # Initialise GPIO for motion sensors
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(PIR_PIN, GPIO.IN)
        GPIO.setup(MICROWAVE_PIN, GPIO.IN)

        # Prepare GPS serial port; opened on demand for robustness
        self.serial_port: Optional[serial.Serial] = None

        # Initialise MQTT client
        self.mqtt_client = mqtt.Client()
        if MQTT_USERNAME:
            self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.connect_mqtt()

    def connect_mqtt(self) -> None:
        """Connect or reconnect the MQTT client to the broker."""
        try:
            logging.info("Connecting to MQTT broker %s:%s...", MQTT_BROKER, MQTT_PORT)
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            # Start a background thread to handle network traffic
            self.mqtt_client.loop_start()
        except Exception as exc:
            logging.error("Failed to connect to MQTT broker: %s", exc)

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc) -> None:
        """Handle successful connection to the MQTT broker."""
        if rc == 0:
            logging.info("Connected to MQTT broker with result code %s", rc)
        else:
            logging.warning("MQTT connection returned non‑zero code: %s", rc)

    def _on_disconnect(self, client: mqtt.Client, userdata, rc) -> None:
        """Handle MQTT disconnection events; attempt to reconnect."""
        logging.warning("MQTT client disconnected (rc=%s); attempting reconnection", rc)
        # Try reconnecting after a short delay
        time.sleep(5)
        self.connect_mqtt()

    def read_dht22(self) -> tuple[Optional[float], Optional[float]]:
        """Read temperature and humidity from the DHT22 sensor.

        The Adafruit library raises ``RuntimeError`` for occasional checksum
        errors.  Following the library's recommendations【203790003007695†L136-L146】,
        the reading is retried on the next poll interval rather than
        terminating the program.
        """
        try:
            temperature = self.dht_device.temperature
            humidity = self.dht_device.humidity
            return temperature, humidity
        except RuntimeError as exc:
            logging.warning("DHT22 transient error: %s", exc)
            return None, None
        except Exception as exc:  # pylint: disable=broad-except
            logging.error("Unexpected error reading DHT22: %s", exc)
            return None, None

    def read_sgp30(self) -> tuple[Optional[int], Optional[int]]:
        """Read TVOC and eCO₂ values from the SGP30 sensor.

        When the sensor is still warming up the first 10–20 readings
        will report 400 ppm CO₂eq and 0 ppb TVOC【603979486650955†L390-L404】; these
        values are returned as‑is.  Any exception results in ``None``.
        """
        try:
            tvoc = self.sgp30.TVOC  # parts per billion
            eco2 = self.sgp30.eCO2  # parts per million
            return tvoc, eco2
        except Exception as exc:  # pylint: disable=broad-except
            logging.error("Error reading SGP30: %s", exc)
            return None, None

    def read_motion_sensors(self) -> tuple[bool, bool]:
        """Return the state of the PIR and microwave motion sensors."""
        pir_state = bool(GPIO.input(PIR_PIN))
        microwave_state = bool(GPIO.input(MICROWAVE_PIN))
        return pir_state, microwave_state

    def _open_gps_serial(self) -> Optional[serial.Serial]:
        """Open the GPS serial port if not already open.

        Returns the serial instance or ``None`` on failure.  The EC20 GNSS
        interface uses 115200 baud on ``/dev/ttyUSB1`` by default【810318060161890†L388-L394】.
        """
        if self.serial_port and self.serial_port.is_open:
            return self.serial_port
        try:
            self.serial_port = serial.Serial(
                port=GPS_SERIAL_PORT,
                baudrate=GPS_BAUDRATE,
                timeout=GPS_READ_TIMEOUT,
            )
            logging.info("Opened GPS serial port %s", GPS_SERIAL_PORT)
            return self.serial_port
        except Exception as exc:  # pylint: disable=broad-except
            logging.warning("Unable to open GPS serial port %s: %s", GPS_SERIAL_PORT, exc)
            self.serial_port = None
            return None

    def read_gps(self) -> str:
        """Attempt to read a valid GPS fix from the EC20.

        This function reads NMEA sentences from the serial interface and looks
        for RMC (Recommended Minimum) sentences with a valid status.  If a fix
        cannot be obtained within the read timeout the string ``"NO_GPS"`` is
        returned.  The returned value is either ``"lat,lon"`` with decimal
        degrees or ``"NO_GPS"``.
        """
        ser = self._open_gps_serial()
        if ser is None:
            return "NO_GPS"
        try:
            # Read a few lines to find a valid RMC sentence
            for _ in range(5):
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("$") and ("RMC" in line):
                    try:
                        msg = pynmea2.parse(line)
                        # RMC messages have a status field: 'A' means valid fix
                        status = getattr(msg, "status", None)
                        if status != "A":
                            continue
                        lat = getattr(msg, "latitude", None)
                        lon = getattr(msg, "longitude", None)
                        if lat is not None and lon is not None:
                            return f"{lat:.6f},{lon:.6f}"
                    except pynmea2.ParseError:
                        continue
            return "NO_GPS"
        except Exception as exc:  # pylint: disable=broad-except
            logging.error("Error reading GPS: %s", exc)
            return "NO_GPS"

    def publish_data(self, payload: dict) -> None:
        """Publish sensor data to the MQTT broker.

        The payload is automatically serialised to JSON.  If publishing
        fails due to connectivity issues the error is logged and the client
        attempts reconnection on its own via the configured callbacks.
        """
        try:
            result = self.mqtt_client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logging.warning("MQTT publish returned non‑success code: %s", result.rc)
        except Exception as exc:  # pylint: disable=broad-except
            logging.error("Failed to publish MQTT message: %s", exc)

    def write_local_file(self, payload: dict) -> None:
        """Write the latest sensor readings to a local JSON file."""
        try:
            with open(SENSOR_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception as exc:  # pylint: disable=broad-except
            logging.error("Error writing sensor data to %s: %s", SENSOR_DATA_FILE, exc)

    def run(self) -> None:
        """Main loop: poll sensors, publish and persist data at regular intervals."""
        logging.info("Starting sensor service loop with interval %.1fs", READ_INTERVAL)
        try:
            while True:
                start_time = time.time()

                temperature, humidity = self.read_dht22()
                tvoc, eco2 = self.read_sgp30()
                pir_state, microwave_state = self.read_motion_sensors()
                gps_value = self.read_gps()

                payload = {
                    "timestamp": int(time.time()),
                    "temperature_c": temperature,
                    "humidity_percent": humidity,
                    "tvoc_ppb": tvoc,
                    "eco2_ppm": eco2,
                    "pir_detected": pir_state,
                    "microwave_detected": microwave_state,
                    "gps": gps_value,
                }

                # Persist locally and publish to MQTT
                self.write_local_file(payload)
                self.publish_data(payload)

                # Wait until the next interval
                elapsed = time.time() - start_time
                sleep_time = max(0.0, READ_INTERVAL - elapsed)
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            logging.info("Received KeyboardInterrupt, shutting down")
        finally:
            # Clean up resources
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            GPIO.cleanup()
            # The DHT device holds onto some resources; call exit() if available
            try:
                self.dht_device.exit()
            except Exception:
                pass


def main() -> None:
    setup_logging()
    service = SensorService()
    service.run()


if __name__ == "__main__":
    main()