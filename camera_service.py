#!/usr/bin/env python3
import subprocess
import time
import signal
import sys
import os


# =======================
# 基本配置
# =======================

CAMERA_DEVICE = "/dev/video0"

# 改成你的公网服务器 IP 或域名
SERVER_IP = "47.94.209.246"

# RTMP 推流地址
RTMP_URL = f"rtmp://{SERVER_IP}/live/car_cam"

# 分辨率和帧率
WIDTH = 640
HEIGHT = 480
FPS = 20

# 码率，4G 网络建议不要太高
VIDEO_BITRATE = "800k"

# 是否设置曝光
ENABLE_CAMERA_CONTROL = True

# 你之前测试过曝光 10000 正常，可以先用这个
EXPOSURE_VALUE = 10000


def run_cmd(cmd):
    print("[CMD]", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def set_camera_params():
    """
    设置 USB 摄像头参数。
    不同摄像头支持的参数不同，如果报错不影响主流程。
    """

    if not ENABLE_CAMERA_CONTROL:
        return

    print("[INFO] Setting camera parameters...")

    commands = [
        # 关闭自动曝光，1 或 3 的含义不同摄像头可能不一样
        ["v4l2-ctl", "-d", CAMERA_DEVICE, "-c", "auto_exposure=1"],

        # 设置手动曝光
        ["v4l2-ctl", "-d", CAMERA_DEVICE, "-c", f"exposure_time_absolute={EXPOSURE_VALUE}"],

        # 可选：亮度、对比度、增益
        # ["v4l2-ctl", "-d", CAMERA_DEVICE, "-c", "brightness=128"],
        # ["v4l2-ctl", "-d", CAMERA_DEVICE, "-c", "contrast=128"],
        # ["v4l2-ctl", "-d", CAMERA_DEVICE, "-c", "gain=0"],
    ]

    for cmd in commands:
        result = run_cmd(cmd)
        if result.returncode != 0:
            print("[WARN]", result.stderr.strip())


def build_ffmpeg_cmd():
    """
    使用 ffmpeg 从 USB 摄像头采集画面并推送到 SRS。
    """

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",

        # 输入源
        "-f", "v4l2",
        "-framerate", str(FPS),
        "-video_size", f"{WIDTH}x{HEIGHT}",
        "-i", CAMERA_DEVICE,

        # 低延迟参数
        "-fflags", "nobuffer",
        "-flags", "low_delay",

        # 视频编码
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-b:v", VIDEO_BITRATE,
        "-maxrate", VIDEO_BITRATE,
        "-bufsize", "1600k",
        "-pix_fmt", "yuv420p",

        # GOP 设置，越小延迟越低，但码率压力更大
        "-g", str(FPS * 2),

        # 输出 RTMP
        "-f", "flv",
        RTMP_URL,
    ]

    return cmd


def main():
    print("===================================")
    print(" Raspberry Pi Camera Service")
    print("===================================")
    print(f"[INFO] Camera device: {CAMERA_DEVICE}")
    print(f"[INFO] RTMP URL: {RTMP_URL}")
    print(f"[INFO] Resolution: {WIDTH}x{HEIGHT}")
    print(f"[INFO] FPS: {FPS}")
    print("===================================")

    if not os.path.exists(CAMERA_DEVICE):
        print(f"[ERROR] Camera device not found: {CAMERA_DEVICE}")
        sys.exit(1)

    set_camera_params()

    process = None

    def stop_handler(signum, frame):
        print("\n[INFO] Stopping camera service...")
        if process and process.poll() is None:
            process.terminate()
            time.sleep(1)
            if process.poll() is None:
                process.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    while True:
        cmd = build_ffmpeg_cmd()
        print("[INFO] Starting ffmpeg...")
        process = subprocess.Popen(cmd)

        ret = process.wait()
        print(f"[WARN] ffmpeg exited with code {ret}")

        print("[INFO] Restarting in 3 seconds...")
        time.sleep(3)


if __name__ == "__main__":
    main()