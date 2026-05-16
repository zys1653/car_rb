#!/usr/bin/env python3
import time
import json
import re
import serial
import paho.mqtt.client as mqtt

MQTT_HOST = "47.94.209.246"
MQTT_PORT = 1883
MQTT_USER = "zys"
MQTT_PASS = "zys-041122"
MQTT_TOPIC = "vehicle/pi/status"

AT_PORT = "/dev/ttyUSB2"   # 如果不对，改成 /dev/ttyUSB3
AT_BAUD = 115200

UPLOAD_INTERVAL = 30  # 30秒上传一次


def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def csq_to_dbm(csq):
    if csq is None or csq == 99:
        return None
    return -113 + 2 * csq


def get_ec20_signal():
    try:
        with serial.Serial(AT_PORT, AT_BAUD, timeout=1) as ser:
            ser.write(b"AT+CSQ\r\n")
            time.sleep(0.5)
            resp = ser.read_all().decode(errors="ignore")

        m = re.search(r"\+CSQ:\s*(\d+),(\d+)", resp)
        if not m:
            return {
                "csq": None,
                "dbm": None,
                "level": "UNKNOWN",
                "raw": resp.strip()
            }

        csq = int(m.group(1))
        dbm = csq_to_dbm(csq)

        if csq == 99:
            level = "NO_SIGNAL"
        elif csq >= 20:
            level = "GOOD"
        elif csq >= 10:
            level = "NORMAL"
        elif csq >= 2:
            level = "WEAK"
        else:
            level = "VERY_WEAK"

        return {
            "csq": csq,
            "dbm": dbm,
            "level": level,
            "raw": resp.strip()
        }

    except Exception as e:
        return {
            "csq": None,
            "dbm": None,
            "level": "ERROR",
            "raw": str(e)
        }


def main():
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    while True:
        signal = get_ec20_signal()

        payload = {
            "timestamp": int(time.time()),
            "cpu_temp_c": get_cpu_temp(),
            "ec20_signal": {
                "csq": signal["csq"],
                "dbm": signal["dbm"],
                "level": signal["level"]
            }
        }

        client.publish(MQTT_TOPIC, json.dumps(payload), qos=0, retain=False)
        print(json.dumps(payload, ensure_ascii=False))

        time.sleep(UPLOAD_INTERVAL)


if __name__ == "__main__":
    main()