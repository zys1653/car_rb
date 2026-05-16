#!/usr/bin/env python3
import serial
import json
import time
import paho.mqtt.client as mqtt

# ===== 串口配置 =====
GPS_PORT = "/dev/ttyUSB1"     # 改成你现在能读到GPS数据的串口
GPS_BAUD = 115200             # 如果读不到，试试 9600

# ===== MQTT配置 =====
MQTT_HOST = "47.94.209.246"
MQTT_PORT = 1883
MQTT_USER = "zys"
MQTT_PASS = "zys-041122"
MQTT_TOPIC = "vehicle/gps"


def nmea_to_decimal(value, direction):
    """
    NMEA格式：3906.532337,N -> 39.108872283
    """
    if not value or not direction:
        return None

    if direction in ["N", "S"]:
        deg_len = 2
    else:
        deg_len = 3

    degrees = float(value[:deg_len])
    minutes = float(value[deg_len:])
    decimal = degrees + minutes / 60.0

    if direction in ["S", "W"]:
        decimal = -decimal

    return decimal


def parse_gps(line):
    parts = line.split(",")

    # $GPRMC,104615.00,A,3906.532337,N,11709.189841,E,...
    if line.startswith("$GPRMC"):
        status = parts[2]
        if status != "A":
            return None

        lat = nmea_to_decimal(parts[3], parts[4])
        lon = nmea_to_decimal(parts[5], parts[6])

        return {
            "type": "GPRMC",
            "latitude": lat,
            "longitude": lon,
            "speed_kmh": float(parts[7]) * 1.852 if parts[7] else 0,
            "valid": True
        }

    # $GPGGA,104615.00,3906.532337,N,11709.189841,E,1,04,...
    if line.startswith("$GPGGA"):
        fix_quality = parts[6]
        if fix_quality == "0":
            return None

        lat = nmea_to_decimal(parts[2], parts[3])
        lon = nmea_to_decimal(parts[4], parts[5])

        return {
            "type": "GPGGA",
            "latitude": lat,
            "longitude": lon,
            "satellites": int(parts[7]) if parts[7] else 0,
            "altitude_m": float(parts[9]) if parts[9] else None,
            "valid": True
        }

    return None


def main():
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    ser = serial.Serial(GPS_PORT, GPS_BAUD, timeout=1)

    print("GPS服务启动，正在读取数据...")

    last_publish = 0

    while True:
        try:
            line = ser.readline().decode("ascii", errors="ignore").strip()

            if not line:
                continue

            print(line)

            gps_data = parse_gps(line)

            if gps_data:
                now = time.time()

                # 每2秒上传一次
                if now - last_publish >= 2:
                    payload = {
                        "timestamp": int(now),
                        "latitude": gps_data["latitude"],
                        "longitude": gps_data["longitude"],
                        "gps_type": gps_data["type"],
                        "valid": True
                    }

                    if "satellites" in gps_data:
                        payload["satellites"] = gps_data["satellites"]

                    if "altitude_m" in gps_data:
                        payload["altitude_m"] = gps_data["altitude_m"]

                    if "speed_kmh" in gps_data:
                        payload["speed_kmh"] = gps_data["speed_kmh"]

                    client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
                    print("已上传MQTT:", payload)

                    last_publish = now

        except KeyboardInterrupt:
            print("退出GPS服务")
            break

        except Exception as e:
            print("错误:", e)
            time.sleep(1)

    ser.close()
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()