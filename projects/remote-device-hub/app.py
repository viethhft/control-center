import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import platform
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "devices.json"
ENROLLMENT_TOKEN = os.getenv("DEVICE_HUB_TOKEN", "change-me-device-hub")
app = FastAPI(title="Remote Device Hub", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
agents: dict[str, WebSocket] = {}
viewers: dict[str, set[WebSocket]] = {}
last_frames: dict[str, bytes] = {}
h264_states: dict[str, dict] = {}
agent_processes: dict[str, subprocess.Popen] = {}
live_meta: dict[str, dict] = {}


def load_devices() -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_devices(devices: dict) -> None:
    DATA_FILE.write_text(json.dumps(devices, ensure_ascii=False, indent=2), encoding="utf-8")


def find_adb() -> str | None:
    direct = shutil.which("adb")
    if direct:
        return direct
    candidates = [
        ROOT / "platform-tools" / "adb.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path(os.getenv("ANDROID_HOME", "")) / "platform-tools" / "adb.exe",
        Path(os.getenv("ANDROID_SDK_ROOT", "")) / "platform-tools" / "adb.exe",
    ]
    return next((str(path) for path in candidates if str(path) and path.is_file()), None)


def start_android_agent(device: dict) -> None:
    process = agent_processes.get(device["id"])
    if process and process.poll() is None:
        return
    adb = find_adb()
    if not adb:
        raise HTTPException(503, "Chưa tìm thấy ADB")
    command = [sys.executable, str(ROOT / "agent.py"), "--server", "http://127.0.0.1:8040", "--device-id", device["id"], "--token", device["agent_token"], "--enrollment", ENROLLMENT_TOKEN, "--kind", "android", "--serial", device["adb_serial"], "--adb", adb]
    log_dir = ROOT / "logs"; log_dir.mkdir(exist_ok=True)
    log = (log_dir / f"agent-{device['id']}.log").open("ab")
    agent_processes[device["id"]] = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def start_desktop_agent(device: dict) -> None:
    process = agent_processes.get(device["id"])
    if process and process.poll() is None: return
    command = [sys.executable, "-u", str(ROOT / "agent.py"), "--server", "http://127.0.0.1:8040", "--device-id", device["id"], "--token", device["agent_token"], "--enrollment", ENROLLMENT_TOKEN, "--kind", "desktop", "--fps", "15", "--quality", "65"]
    log_dir = ROOT / "logs"; log_dir.mkdir(exist_ok=True)
    log = (log_dir / f"agent-{device['id']}.log").open("ab")
    agent_processes[device["id"]] = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@app.on_event("startup")
async def resume_local_android_agents():
    try:
        authorized = {item["serial"] for item in adb_devices() if item["authorized"]}
    except HTTPException:
        authorized = set()
    for device in load_devices().values():
        if device.get("kind") == "android" and device.get("adb_serial") in authorized:
            start_android_agent(device)
        elif device.get("kind") == "desktop" and device.get("local_machine"):
            start_desktop_agent(device)


def public_device(device: dict) -> dict:
    data = {k: v for k, v in device.items() if k != "agent_token"}
    data["online"] = device["id"] in agents
    data["viewers"] = len(viewers.get(device["id"], set()))
    data["stream"] = live_meta.get(device["id"], {}).get("stream")
    return data


def cache_h264(device_id: str, chunk: bytes) -> None:
    state = h264_states.setdefault(device_id, {"buffer": bytearray(), "sps": b"", "pps": b"", "gop": bytearray()})
    state["buffer"].extend(chunk)
    data = bytes(state["buffer"])
    starts = [match.start() for match in re.finditer(b"\x00\x00\x01", data)]
    if len(starts) < 2:
        if len(state["buffer"]) > 2_000_000: state["buffer"] = state["buffer"][-1024:]
        return
    for index, start in enumerate(starts[:-1]):
        nal = data[start:starts[index + 1]]
        if len(nal) < 4: continue
        nal_type = nal[3] & 0x1F
        if nal_type == 7: state["sps"] = nal
        elif nal_type == 8: state["pps"] = nal
        elif nal_type == 5:
            state["gop"] = bytearray(state["sps"] + state["pps"] + nal)
        elif state["gop"] and len(state["gop"]) < 3_000_000:
            state["gop"].extend(nal)
    state["buffer"] = bytearray(data[starts[-1]:])


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(desktop|android|ios)$")
    note: str = Field(default="", max_length=300)


class AndroidConnect(BaseModel):
    serial: str = Field(min_length=1, max_length=160)
    name: str = Field(default="", max_length=80)


class DesktopConnect(BaseModel):
    name: str = Field(default="", max_length=80)


def adb_devices() -> list[dict]:
    adb = find_adb()
    if not adb:
        raise HTTPException(503, "Chưa tìm thấy ADB. Hãy cài Android Platform Tools và thêm adb vào PATH.")
    try:
        result = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, timeout=8, check=True)
    except Exception as exc:
        raise HTTPException(503, f"Không thể chạy ADB: {exc}")
    found = []
    for line in result.stdout.splitlines()[1:]:
        if not line.strip():
            continue
        fields = line.split()
        serial, state = fields[0], fields[1] if len(fields) > 1 else "unknown"
        meta = dict(item.split(":", 1) for item in fields[2:] if ":" in item)
        found.append({"serial": serial, "state": state, "model": meta.get("model", "Android"), "product": meta.get("product", ""), "authorized": state == "device"})
    return found


@app.get("/api/devices")
def list_devices():
    return [public_device(item) for item in load_devices().values()]


@app.post("/api/devices")
def create_device(req: DeviceCreate):
    devices = load_devices()
    device_id = secrets.token_hex(6)
    agent_token = secrets.token_urlsafe(24)
    device = {
        "id": device_id, "name": req.name, "kind": req.kind, "note": req.note,
        "agent_token": agent_token, "created_at": time.time(), "last_seen": None,
        "capabilities": ["screen"] if req.kind == "ios" else ["screen", "pointer", "keyboard"],
    }
    devices[device_id] = device
    save_devices(devices)
    return {**public_device(device), "agent_token": agent_token, "enrollment_token": ENROLLMENT_TOKEN}


@app.get("/api/android/connected")
def connected_android():
    return {"devices": adb_devices()}


@app.post("/api/android/connect")
def connect_android(req: AndroidConnect):
    detected = {item["serial"]: item for item in adb_devices()}
    phone = detected.get(req.serial)
    if not phone:
        raise HTTPException(404, "Điện thoại không còn kết nối bằng USB/ADB")
    if not phone["authorized"]:
        message = "Hãy mở khóa điện thoại và nhấn Cho phép gỡ lỗi USB" if phone["state"] == "unauthorized" else f"ADB chưa sẵn sàng: {phone['state']}"
        raise HTTPException(409, message)
    devices = load_devices()
    existing = next((item for item in devices.values() if item.get("adb_serial") == req.serial), None)
    if existing:
        device = existing
    else:
        device_id = secrets.token_hex(6)
        device = {"id": device_id, "name": req.name.strip() or phone["model"].replace("_", " "), "kind": "android", "note": f"ADB · {req.serial}", "adb_serial": req.serial, "agent_token": secrets.token_urlsafe(24), "created_at": time.time(), "last_seen": None, "capabilities": ["screen", "pointer", "keyboard"]}
        devices[device_id] = device
        save_devices(devices)
    start_android_agent(device)
    return {"device": public_device(device), "message": "Đã xác nhận. Agent đang kết nối với điện thoại."}


@app.post("/api/desktop/connect-local")
def connect_local_desktop(req: DesktopConnect):
    devices = load_devices(); device = next((item for item in devices.values() if item.get("local_machine")), None)
    if not device:
        device_id = secrets.token_hex(6)
        device = {"id": device_id, "name": req.name.strip() or platform.node() or "Máy tính này", "kind": "desktop", "note": f"{platform.system()} {platform.release()} · local agent", "local_machine": True, "agent_token": secrets.token_urlsafe(24), "created_at": time.time(), "last_seen": None, "capabilities": ["screen", "pointer", "keyboard", "files"]}
        devices[device_id] = device; save_devices(devices)
    start_desktop_agent(device)
    return {"device": public_device(device), "message": "Đã tạo agent cho máy tính này"}


@app.get("/agent.py")
def download_agent():
    return FileResponse(ROOT / "agent.py", filename="remote-hub-agent.py", media_type="text/x-python")


@app.get("/agent-requirements.txt")
def download_agent_requirements():
    return FileResponse(ROOT / "agent-requirements.txt", filename="remote-hub-requirements.txt", media_type="text/plain")


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: str):
    devices = load_devices()
    if device_id not in devices:
        raise HTTPException(404, "Không tìm thấy thiết bị")
    socket = agents.pop(device_id, None)
    if socket:
        await socket.close(code=4001)
    process = agent_processes.pop(device_id, None)
    if process and process.poll() is None:
        process.terminate()
    del devices[device_id]
    save_devices(devices)
    last_frames.pop(device_id, None)
    return {"ok": True}


@app.post("/api/devices/{device_id}/upload")
async def upload_to_device(device_id: str, request: Request, filename: str = "file.bin"):
    devices = load_devices(); device = devices.get(device_id)
    if not device: raise HTTPException(404, "Không tìm thấy thiết bị")
    if device.get("kind") != "android": raise HTTPException(400, "Hiện chỉ hỗ trợ chuyển tệp trực tiếp sang Android")
    adb = find_adb()
    if not adb: raise HTTPException(503, "Chưa tìm thấy ADB")
    detected = {item["serial"]: item for item in adb_devices()}
    if not detected.get(device.get("adb_serial"), {}).get("authorized"):
        raise HTTPException(409, "Điện thoại chưa kết nối hoặc chưa cấp quyền ADB")
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > 2 * 1024 * 1024 * 1024: raise HTTPException(413, "Tệp vượt giới hạn 2 GB")
    extension = re.sub(r"[^a-zA-Z0-9.]", "", Path(filename).suffix)[:12]
    remote_name = f"remote-hub-{int(time.time())}-{secrets.token_hex(3)}{extension}"
    upload_dir = ROOT / "runtime" / "uploads"; upload_dir.mkdir(parents=True, exist_ok=True)
    local_path = upload_dir / remote_name; received = 0
    try:
        with local_path.open("wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > 2 * 1024 * 1024 * 1024: raise HTTPException(413, "Tệp vượt giới hạn 2 GB")
                output.write(chunk)
        if not received: raise HTTPException(400, "Tệp rỗng")
        serial = device["adb_serial"]; remote_dir = "/sdcard/Download/RemoteHub"; remote_path = f"{remote_dir}/{remote_name}"
        def push_file():
            subprocess.run([adb, "-s", serial, "shell", "mkdir", "-p", remote_dir], capture_output=True, check=True)
            subprocess.run([adb, "-s", serial, "push", str(local_path), remote_path], capture_output=True, check=True)
            subprocess.run([adb, "-s", serial, "shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote_path}"], capture_output=True)
        await asyncio.to_thread(push_file)
    except HTTPException: raise
    except subprocess.CalledProcessError as exc:
        raise HTTPException(502, f"ADB không thể chuyển tệp: {exc.stderr.decode(errors='ignore') if isinstance(exc.stderr, bytes) else exc.stderr}")
    finally:
        local_path.unlink(missing_ok=True)
    return {"ok": True, "filename": remote_name, "path": remote_path, "size": received, "message": "Đã đưa tệp vào Download/RemoteHub trên điện thoại"}


@app.websocket("/ws/agent/{device_id}")
async def agent_socket(websocket: WebSocket, device_id: str):
    devices = load_devices()
    device = devices.get(device_id)
    token = websocket.query_params.get("token", "")
    enrollment = websocket.query_params.get("enrollment", "")
    if not device or not secrets.compare_digest(token, device.get("agent_token", "")) or not secrets.compare_digest(enrollment, ENROLLMENT_TOKEN):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    old = agents.get(device_id)
    if old:
        await old.close(code=4000)
    agents[device_id] = websocket
    h264_states[device_id] = {"buffer": bytearray(), "sps": b"", "pps": b"", "gop": bytearray()}
    device["last_seen"] = time.time()
    devices[device_id] = device
    save_devices(devices)
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes"):
                frame = message["bytes"]
                if frame[:1] == b"\x00": last_frames[device_id] = frame
                elif frame[:1] == b"\x01": cache_h264(device_id, frame[1:])
                stale = []
                for viewer in viewers.get(device_id, set()).copy():
                    try:
                        await viewer.send_bytes(frame)
                    except Exception:
                        stale.append(viewer)
                for viewer in stale:
                    viewers.get(device_id, set()).discard(viewer)
            elif message.get("text"):
                payload = json.loads(message["text"])
                if payload.get("type") == "status":
                    device["meta"] = payload.get("meta", {})
                    live_meta[device_id] = device["meta"]
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if agents.get(device_id) is websocket:
            agents.pop(device_id, None)
            live_meta.pop(device_id, None)
        devices = load_devices()
        if device_id in devices:
            devices[device_id]["last_seen"] = time.time()
            save_devices(devices)


@app.websocket("/ws/view/{device_id}")
async def viewer_socket(websocket: WebSocket, device_id: str):
    if device_id not in load_devices():
        await websocket.close(code=4404)
        return
    await websocket.accept()
    if device_id in last_frames:
        await websocket.send_bytes(last_frames[device_id])
    viewers.setdefault(device_id, set()).add(websocket)
    if device_id in agents and live_meta.get(device_id, {}).get("stream") == "h264":
        try: await agents[device_id].send_text(json.dumps({"type": "restart_stream"}))
        except Exception: pass
    try:
        while True:
            message = await websocket.receive_text()
            payload = json.loads(message)
            if payload.get("type") == "input" and device_id in agents:
                await agents[device_id].send_text(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        viewers.get(device_id, set()).discard(websocket)


@app.get("/api/health")
def health():
    return {"ok": True, "online": len(agents), "registered": len(load_devices())}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
