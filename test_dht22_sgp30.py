import time
import board
import busio
import adafruit_dht
import adafruit_sgp30


# DHT22 接 GPIO4
dht = adafruit_dht.DHT22(board.D4, use_pulseio=False)

# SGP30 接 I2C：SDA GPIO2，SCL GPIO3
i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)
sgp30 = adafruit_sgp30.Adafruit_SGP30(i2c)

print("SGP30 serial:", [hex(i) for i in sgp30.serial])
print("开始测试 DHT22 和 SGP30，每 2 秒读取一次")
print("如果 SGP30 一开始 eCO2=400、TVOC=0/很低，属于预热正常现象")
print("-" * 50)

while True:
    print("----- 本次读取 -----")

    # 读取 DHT22
    try:
        temperature = dht.temperature
        humidity = dht.humidity

        print(f"DHT22 温度: {temperature} °C")
        print(f"DHT22 湿度: {humidity} %")

    except RuntimeError as e:
        print("DHT22 本次读取失败，这是比较常见的，可以继续观察")
        print("错误信息:", e)

    except Exception as e:
        print("DHT22 严重错误，可能是接线/库/引脚问题")
        print("错误信息:", e)

    # 读取 SGP30
    try:
        tvoc = sgp30.TVOC
        eco2 = sgp30.eCO2

        print(f"SGP30 TVOC: {tvoc} ppb")
        print(f"SGP30 eCO2: {eco2} ppm")

    except Exception as e:
        print("SGP30 读取失败")
        print("错误信息:", e)

    print("-" * 50)
    time.sleep(2)