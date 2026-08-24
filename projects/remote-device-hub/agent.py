import argparse
import asyncio
import io
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import websockets


def request_desktop_consent(server):
    """Require an explicit decision on the controlled computer before capture."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        allowed = messagebox.askyesno(
            "Remote Hub - Yêu cầu điều khiển",
            "Một máy chủ Remote Hub đang yêu cầu xem và điều khiển máy tính này.\n\n"
            f"Máy chủ: {server}\n\n"
            "Chỉ chọn Cho phép nếu bạn nhận ra yêu cầu này. Bạn có thể dừng quyền bằng "
            "cách kết thúc Remote Hub Agent trong Task Manager.",
            parent=root,
        )
        root.destroy()
        return allowed
    except Exception as exc:
        print(f"Không thể hiển thị hộp thoại xin quyền: {exc!a}", flush=True)
        return False


def hide_windows_console():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window = ctypes.windll.kernel32.GetConsoleWindow()
        if window:
            ctypes.windll.user32.ShowWindow(window, 0)
    except Exception:
        pass


def adb(*args, binary="adb", serial="", raw=False):
    command = [binary]
    if serial: command += ["-s", serial]
    return subprocess.run([*command, *args], capture_output=True, check=True, text=not raw).stdout


class DesktopDriver:
    def __init__(self, quality):
        import mss
        from PIL import Image
        self.mss_module = mss
        self.Image = Image
        with mss.mss() as capture:
            self.monitor = dict(capture.monitors[1])
        self.quality = quality

    def frame(self):
        # MSS keeps native Windows handles in thread-local storage.  frame() is
        # run through asyncio.to_thread(), so the capture context must be made
        # and used in that same worker thread.
        with self.mss_module.mss() as capture:
            monitor = dict(capture.monitors[1])
            shot = capture.grab(monitor)
        self.monitor = monitor
        image = self.Image.frombytes("RGB", shot.size, shot.rgb)
        output = io.BytesIO(); image.save(output, "JPEG", quality=self.quality, optimize=True)
        return output.getvalue()

    def input(self, event):
        import pyautogui
        width, height = self.monitor["width"], self.monitor["height"]
        if event["action"] == "click": pyautogui.click(event["x"] * width, event["y"] * height)
        elif event["action"] == "move": pyautogui.moveTo(event["x"] * width, event["y"] * height)
        elif event["action"] == "scroll": pyautogui.scroll(event.get("delta", 0))
        elif event["action"] == "key": pyautogui.press(event.get("key", ""))
        elif event["action"] == "text": pyautogui.write(event.get("text", ""), interval=0.01)


class AndroidDriver:
    def __init__(self, quality, adb_binary, serial, scrcpy_server):
        self.quality, self.adb_binary, self.serial = quality, adb_binary, serial
        self.size = self.read_display_size()
        self.scrcpy_server, self.video_socket, self.server_process, self.forward_port = Path(scrcpy_server), None, None, None
    def read_display_size(self):
        try:
            output = adb("shell", "wm", "size", binary=self.adb_binary, serial=self.serial)
            sizes = re.findall(r"(\d+)x(\d+)", output)
            return tuple(map(int, sizes[-1])) if sizes else (1080, 1920)
        except Exception: return (1080, 1920)
    def open_video(self):
        if not self.scrcpy_server.is_file(): return None
        adb("push", str(self.scrcpy_server), "/data/local/tmp/scrcpy-server-hub.jar", binary=self.adb_binary, serial=self.serial)
        probe=socket.socket(); probe.bind(("127.0.0.1",0)); self.forward_port=probe.getsockname()[1]; probe.close()
        adb("forward", f"tcp:{self.forward_port}", "localabstract:scrcpy", binary=self.adb_binary, serial=self.serial)
        command=[self.adb_binary,"-s",self.serial,"shell","CLASSPATH=/data/local/tmp/scrcpy-server-hub.jar","app_process","/","com.genymobile.scrcpy.Server","4.1","tunnel_forward=true","audio=false","control=false","cleanup=false","raw_stream=true","max_size=1080","video_bit_rate=4000000","max_fps=30","log_level=warn"]
        self.server_process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        time.sleep(.8)
        deadline=time.time()+8
        while time.time()<deadline:
            try:
                self.video_socket=socket.create_connection(("127.0.0.1",self.forward_port),timeout=2); self.video_socket.settimeout(None); return self.video_socket
            except OSError: time.sleep(.15)
        self.close_video(); return None
    def close_video(self):
        if self.video_socket:
            try: self.video_socket.close()
            except OSError: pass
        if self.server_process and self.server_process.poll() is None: self.server_process.terminate()
        if self.forward_port:
            try: adb("forward","--remove",f"tcp:{self.forward_port}",binary=self.adb_binary,serial=self.serial)
            except Exception: pass
        self.video_socket=None; self.server_process=None; self.forward_port=None
    def frame(self):
        raw = adb("exec-out", "screencap", "-p", binary=self.adb_binary, serial=self.serial, raw=True)
        if raw[:8] != b"\x89PNG\r\n\x1a\n": raise RuntimeError("ADB không trả về ảnh màn hình hợp lệ")
        width, height = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        self.size = (width, height)
        return raw
    def input(self, event):
        self.size = self.read_display_size()
        width, height = self.size
        if event.get("landscape") and height > width: width, height = height, width
        x, y = round(event.get("x", 0)*max(0,width-1)), round(event.get("y", 0)*max(0,height-1))
        if event["action"] == "click": adb("shell", "input", "tap", str(x), str(y), binary=self.adb_binary, serial=self.serial)
        elif event["action"] == "swipe": adb("shell", "input", "swipe", str(x), str(y), str(int(event["x2"]*width)), str(int(event["y2"]*height)), "250", binary=self.adb_binary, serial=self.serial)
        elif event["action"] == "key": adb("shell", "input", "keyevent", event.get("key", "BACK"), binary=self.adb_binary, serial=self.serial)
        elif event["action"] == "text": adb("shell", "input", "text", event.get("text", "").replace(" ", "%s"), binary=self.adb_binary, serial=self.serial)


async def run(args):
    driver = DesktopDriver(args.quality) if args.kind == "desktop" else AndroidDriver(args.quality, args.adb, args.serial, args.scrcpy_server)
    uri = f"{args.server.rstrip('/')}/ws/agent/{args.device_id}?token={args.token}&enrollment={args.enrollment}".replace("http://", "ws://").replace("https://", "wss://")
    while True:
        try:
            async with websockets.connect(uri, max_size=8_000_000) as socket:
                video_socket = await asyncio.to_thread(driver.open_video) if args.kind == "android" else None
                stream_format = "h264" if video_socket else "image"
                await socket.send(json.dumps({"type":"status","meta":{"kind":args.kind,"stream":stream_format}}))
                async def stream():
                    while True:
                        if video_socket:
                            frame = await asyncio.to_thread(video_socket.recv, 65536)
                            if not frame: raise ConnectionError("Luồng scrcpy đã đóng")
                            await socket.send(b"\x01"+frame)
                        else:
                            started = time.perf_counter(); frame = await asyncio.to_thread(driver.frame); await socket.send(b"\x00"+frame)
                            await asyncio.sleep(max(0, 1/args.fps - (time.perf_counter() - started)))
                async def control():
                    async for raw in socket:
                        if isinstance(raw, str):
                            event=json.loads(raw)
                            if event.get("type")=="restart_stream": raise RuntimeError("restart_stream")
                            if event.get("type")=="input":
                                try: await asyncio.to_thread(driver.input,event)
                                except Exception as exc: print(f"Input error: {exc!a}")
                await asyncio.gather(stream(), control())
        except Exception as exc:
            if args.kind == "android": await asyncio.to_thread(driver.close_video)
            if getattr(exc, "code", None) in (4000, 4001): return
            if str(exc) == "restart_stream": await asyncio.sleep(.15)
            else:
                print(f"Connection lost: {exc!a}. Retry in 3 seconds..."); await asyncio.sleep(3)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--server",default="http://127.0.0.1:8040"); parser.add_argument("--device-id",required=True); parser.add_argument("--token",required=True); parser.add_argument("--enrollment",required=True); parser.add_argument("--kind",choices=["desktop","android"],default="desktop"); parser.add_argument("--fps",type=int,default=8); parser.add_argument("--quality",type=int,default=65); parser.add_argument("--adb",default="adb"); parser.add_argument("--serial",default=""); parser.add_argument("--scrcpy-server",default=str(Path(__file__).resolve().parent/"scrcpy-server-v4.1")); args=parser.parse_args()
    if args.kind == "desktop":
        if not request_desktop_consent(args.server):
            print("Người dùng đã từ chối quyền điều khiển. Agent dừng.", flush=True)
            raise SystemExit(2)
        hide_windows_console()
    asyncio.run(run(args))
