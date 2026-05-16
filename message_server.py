#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import logging
import serial
import paho.mqtt.client as mqtt

# ===================== MQTT 配置 =====================
MQTT_HOST = "47.94.209.246"
MQTT_PORT = 1883
MQTT_USERNAME = "zys"
MQTT_PASSWORD = "zys-041122"

TOPIC_SENSOR = "vehicle/sensor"
TOPIC_AI = "vehicle/ai/detect"
TOPIC_CONTROL = "vehicle/sms/control"
TOPIC_STATUS = "vehicle/sms/status"

# ===================== EC20 短信配置 =====================
AT_PORT = "/dev/ttyUSB2"       # 如果被占用，改成你的 AT 串口
AT_BAUDRATE = 115200

# 接收报警短信的手机号
ALERT_PHONE = "18231665003"    # 改成你的手机号

# ===================== 判断周期 =====================
CHECK_INTERVAL = 60            # 1分钟判断一次
TEMP_MUTE_SECONDS = 10 * 60    # 临时终止10分钟

# ===================== 日志 =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

latest_sensor = {}
latest_ai = {}

sms_enabled = True
permanent_muted = False
mute_until = 0


# ===================== EC20 短信类 =====================
class EC20SMS:
    def __init__(self, port, baudrate):
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=2
        )
        time.sleep(1)
        self.init_sms()

    def send_at(self, cmd, wait=0.5):
        self.ser.write((cmd + "\r").encode())
        time.sleep(wait)
        resp = self.ser.read_all().decode(errors="ignore")
        logging.debug("AT %s -> %s", cmd, resp.strip())
        return resp

    def init_sms(self):
        self.send_at("AT")
        self.send_at("ATE0")
        self.send_at("AT+CMGF=1")          # 文本短信模式
        self.send_at('AT+CSCS="UCS2"')      # 使用英文短信更稳定

    def send_sms(self, phone, text):
        logging.info("准备发送短信：%s", text)

        self.send_at("AT+CMGF=1")
        self.send_at('AT+CSCS="UCS2"')

        phone_ucs2 = phone.encode("utf-16-be").hex().upper()
        text_ucs2 = text.encode("utf-16-be").hex().upper()

        self.ser.write((f'AT+CMGS="{phone_ucs2}"\r').encode())
        time.sleep(1)

        self.ser.write(text_ucs2.encode())
        self.ser.write(bytes([26]))  # Ctrl+Z
        time.sleep(5)

        resp = self.ser.read_all().decode(errors="ignore")
        logging.info("短信发送返回：%s", resp.strip())
        return resp


# ===================== MQTT 回调 =====================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("MQTT连接成功")
        client.subscribe(TOPIC_SENSOR)
        client.subscribe(TOPIC_AI)
        client.subscribe(TOPIC_CONTROL)

        publish_status(client, "online")
    else:
        logging.error("MQTT连接失败，rc=%s", rc)


def on_message(client, userdata, msg):
    global latest_sensor, latest_ai
    global sms_enabled, permanent_muted, mute_until

    try:
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)

        if msg.topic == TOPIC_SENSOR:
            latest_sensor = data

        elif msg.topic == TOPIC_AI:
            latest_ai = data

        elif msg.topic == TOPIC_CONTROL:
            handle_control_command(client, data)

    except Exception as e:
        logging.error("处理MQTT消息失败：%s", e)

def wait_for_first_mqtt_data(timeout=30):
    logging.info("正在等待首包MQTT数据...")

    start_time = time.time()

    while True:
        sensor_ready = bool(latest_sensor)
        ai_ready = bool(latest_ai)

        if sensor_ready and ai_ready:
            logging.info("已收到首包MQTT数据，开始报警判断")
            return True

        if time.time() - start_time > timeout:
            logging.warning("等待MQTT首包数据超时，继续运行")
            return False

        time.sleep(1)



# ===================== 控制指令处理 =====================
def handle_control_command(client, data):
    """
    App发送到 vehicle/sms/control 的示例：

    临时关闭10分钟：
    {"cmd":"mute_10min"}

    永久关闭：
    {"cmd":"mute_forever"}

    恢复短信报警：
    {"cmd":"resume"}

    查询状态：
    {"cmd":"status"}
    """
    global sms_enabled, permanent_muted, mute_until

    cmd = data.get("cmd")

    if cmd == "mute_10min":
        sms_enabled = False
        permanent_muted = False
        mute_until = time.time() + TEMP_MUTE_SECONDS
        publish_status(client, "muted_10min")
        logging.info("短信报警已临时关闭10分钟")

    elif cmd == "mute_forever":
        sms_enabled = False
        permanent_muted = True
        mute_until = 0
        publish_status(client, "muted_forever")
        logging.info("短信报警已永久关闭，直到收到resume")

    elif cmd == "resume":
        sms_enabled = True
        permanent_muted = False
        mute_until = 0
        publish_status(client, "resumed")
        logging.info("短信报警已恢复")

    elif cmd == "status":
        publish_status(client, "status")

    else:
        logging.warning("未知控制指令：%s", data)


def publish_status(client, state):
    status = {
        "timestamp": int(time.time()),
        "state": state,
        "sms_enabled": sms_enabled,
        "permanent_muted": permanent_muted,
        "mute_until": int(mute_until)
    }
    client.publish(TOPIC_STATUS, json.dumps(status), qos=0, retain=False)


# ===================== 判断逻辑 =====================
def is_sms_allowed():
    global sms_enabled, permanent_muted, mute_until

    now = time.time()

    if permanent_muted:
        return False

    if not sms_enabled:
        if mute_until and now >= mute_until:
            sms_enabled = True
            mute_until = 0
            logging.info("临时关闭时间结束，短信报警自动恢复")
            return True
        return False

    return True


def judge_alarm():
    ai_person = bool(latest_ai.get("person_detected", False))
    pir = bool(latest_sensor.get("pir_detected", False))
    microwave = bool(latest_sensor.get("microwave_detected", False))

    if ai_person:
        return "person detected by video."

    if pir and microwave:
        return "suspected person detected by PIR and microwave sensors."

    return None


# ===================== 主程序 =====================
def main():
    sms = EC20SMS(AT_PORT, AT_BAUDRATE)

    client = mqtt.Client()
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()


    logging.info("message_server.py 已启动")

    wait_for_first_mqtt_data(timeout=10)

    logging.info("message_server.py 已启动")

    while True:
        try:
            if is_sms_allowed():
                alarm_text = judge_alarm()

                if alarm_text:
                    msg = f"ALARM：{alarm_text}。Please check the car immediately.。"
                    sms.send_sms(ALERT_PHONE, msg)

                    client.publish(
                        TOPIC_STATUS,
                        json.dumps({
                            "timestamp": int(time.time()),
                            "state": "sms_sent",
                            "message": alarm_text
                        }),
                        qos=0,
                        retain=False
                    )
                else:
                    logging.info("未检测到人员滞留，不发送短信")
            else:
                logging.info("短信报警当前处于关闭状态")

        except Exception as e:
            logging.error("主循环异常：%s", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()