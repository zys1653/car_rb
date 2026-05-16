#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import serial
import time
import subprocess
import threading


AT_PORT = "/dev/ttyUSB3"      # EC20 AT 指令口，可能需要改
GPS_PORT = "/dev/ttyUSB1"     # EC20 GPS NMEA 输出口，可能需要改
BAUDRATE = 115200


class EC20:
    def __init__(self, at_port=AT_PORT, gps_port=GPS_PORT):
        self.at_port = at_port
        self.gps_port = gps_port
        self.ser = serial.Serial(
            at_port,
            BAUDRATE,
            timeout=1
        )

    def send_at(self, cmd, wait=0.5):
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r\n").encode())
        time.sleep(wait)

        data = self.ser.read_all().decode(errors="ignore")
        return data.strip()

    def init_module(self):
        print("检测模块：")
        print(self.send_at("AT"))

        print("关闭回显：")
        print(self.send_at("ATE0"))

        print("查看 SIM 卡：")
        print(self.send_at("AT+CPIN?"))

        print("查看信号：")
        print(self.send_at("AT+CSQ"))

        print("查看注册状态：")
        print(self.send_at("AT+CREG?"))
        print(self.send_at("AT+CEREG?"))

        print("设置短信文本模式：")
        print(self.send_at("AT+CMGF=1"))

        print("设置短信字符集 GSM：")
        print(self.send_at('AT+CSCS="GSM"'))

        print("开启 GPS：")
        print(self.send_at("AT+QGPS=1", wait=1))

    def send_sms(self, phone, message):
        print("发送短信中...")

        self.ser.reset_input_buffer()
        self.ser.write(b"AT+CMGF=1\r\n")
        time.sleep(0.5)

        self.ser.write(f'AT+CMGS="{phone}"\r\n'.encode())
        time.sleep(0.5)

        self.ser.write(message.encode())
        self.ser.write(bytes([26]))  # Ctrl+Z
        time.sleep(5)

        result = self.ser.read_all().decode(errors="ignore")
        print(result)
        return result

    def list_sms(self):
        print("读取短信：")
        result = self.send_at('AT+CMGL="ALL"', wait=2)
        print(result)
        return result

    def read_sms(self, index):
        result = self.send_at(f"AT+CMGR={index}", wait=1)
        print(result)
        return result

    def delete_sms(self, index):
        result = self.send_at(f"AT+CMGD={index}", wait=1)
        print(result)
        return result

    def get_gps_by_at(self):
        """
        通过 AT 指令读取 GPS。
        如果没定位，通常会返回 +QGPSLOC: 516 或 ERROR。
        """
        result = self.send_at("AT+QGPSLOC=2", wait=1)
        print(result)
        return result

    def read_gps_nmea(self):
        """
        从 GPS 串口读取 NMEA 原始数据。
        """
        try:
            gps_ser = serial.Serial(self.gps_port, BAUDRATE, timeout=1)
            print("开始读取 GPS NMEA 数据，Ctrl+C 退出")

            while True:
                line = gps_ser.readline().decode(errors="ignore").strip()
                if line:
                    print(line)

        except Exception as e:
            print("GPS 串口读取失败：", e)

    def check_internet(self):
        """
        检查树莓派是否已经可以上网。
        """
        try:
            result = subprocess.run(
                ["ping", "-c", "3", "8.8.8.8"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print("当前已经可以上网")
                return True
            else:
                print("当前不能上网")
                print(result.stdout)
                return False
        except Exception as e:
            print("检查网络失败：", e)
            return False

    def connect_internet_by_nmcli(self, apn="cmnet"):
        """
        使用 NetworkManager 创建 4G 拨号连接。
        移动/联通一般可以先试 cmnet 或 3gnet。
        电信可以试 ctnet。
        """
        print("尝试使用 nmcli 创建 EC20 4G 连接...")

        commands = [
            ["sudo", "nmcli", "con", "delete", "ec20-4g"],
            [
                "sudo", "nmcli", "con", "add",
                "type", "gsm",
                "ifname", "*",
                "con-name", "ec20-4g",
                "apn", apn
            ],
            ["sudo", "nmcli", "con", "up", "ec20-4g"]
        ]

        for cmd in commands:
            print("执行：", " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(result.stdout)
            print(result.stderr)

        time.sleep(5)
        return self.check_internet()


def menu():
    print("""
========== EC20 服务菜单 ==========
1. 初始化 EC20
2. 发送短信
3. 读取全部短信
4. 读取指定短信
5. 删除指定短信
6. AT方式读取GPS
7. NMEA方式持续读取GPS
8. 检查是否能上网
9. 使用EC20拨号上网
0. 退出
=================================
""")


def main():
    ec20 = EC20()

    while True:
        menu()
        choice = input("请输入选项：").strip()

        if choice == "1":
            ec20.init_module()

        elif choice == "2":
            phone = input("请输入手机号：").strip()
            msg = input("请输入短信内容，建议先用英文测试：").strip()
            ec20.send_sms(phone, msg)

        elif choice == "3":
            ec20.list_sms()

        elif choice == "4":
            index = input("请输入短信编号：").strip()
            ec20.read_sms(index)

        elif choice == "5":
            index = input("请输入短信编号：").strip()
            ec20.delete_sms(index)

        elif choice == "6":
            ec20.get_gps_by_at()

        elif choice == "7":
            ec20.read_gps_nmea()

        elif choice == "8":
            ec20.check_internet()

        elif choice == "9":
            apn = input("请输入 APN，移动默认 cmnet：").strip()
            if not apn:
                apn = "cmnet"
            ec20.connect_internet_by_nmcli(apn)

        elif choice == "0":
            break

        else:
            print("无效选项")


if __name__ == "__main__":
    main()