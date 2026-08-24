from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "control-center.db"
PROJECTS_PATH = ROOT / "projects.json"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
PID_DIR = ROOT / "runtime" / "pids"
PID_DIR.mkdir(parents=True, exist_ok=True)
SESSION_COOKIE = "mmo_session"
SESSION_SECONDS = 12 * 60 * 60
PROCESSES: dict[str, subprocess.Popen] = {}
PROCESS_LOCK = threading.Lock()

app = FastAPI(title="MMO Control Center", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{value.hex()}"


def password_ok(password: str, stored: str) -> bool:
    try:
        salt, expected = (bytes.fromhex(part) for part in stored.split(":"))
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
          allowed_ips TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS permissions(
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          project_id TEXT NOT NULL, feature_id TEXT NOT NULL DEFAULT '*',
          PRIMARY KEY(user_id, project_id, feature_id)
        );
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL,
          target TEXT NOT NULL, ip TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS banned_ips(
          ip TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '',
          created_by INTEGER REFERENCES users(id), created_at INTEGER NOT NULL
        );
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "status" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        if "ban_reason" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT NOT NULL DEFAULT ''")
        if not conn.execute("SELECT 1 FROM settings WHERE key='secret'").fetchone():
            conn.execute("INSERT INTO settings VALUES('secret', ?)", (secrets.token_hex(32),))
        if not conn.execute("SELECT 1 FROM users").fetchone():
            initial = os.getenv("CONTROL_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                ("admin", password_hash(initial), "admin", int(time.time())),
            )
            print(f"[Control Center] Tài khoản đầu tiên: admin / {initial}")


def projects() -> list[dict[str, Any]]:
    return json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))


def project(project_id: str) -> dict[str, Any]:
    item = next((p for p in projects() if p["id"] == project_id), None)
    if not item:
        raise HTTPException(404, "Không tìm thấy dự án")
    return item


def secret_key() -> bytes:
    with db() as conn:
        return conn.execute("SELECT value FROM settings WHERE key='secret'").fetchone()[0].encode()


def make_token(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time()) + SESSION_SECONDS}"
    signature = hmac.new(secret_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def current_user(request: Request) -> sqlite3.Row:
    token = request.cookies.get(SESSION_COOKIE, "")
    try:
        uid, expires, signature = token.split(":")
        payload = f"{uid}:{expires}"
        valid = hmac.compare_digest(signature, hmac.new(secret_key(), payload.encode(), hashlib.sha256).hexdigest())
        if not valid or int(expires) < time.time():
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(401, "Vui lòng đăng nhập")
    client_ip = request.client.host if request.client else ""
    with db() as conn:
        if conn.execute("SELECT 1 FROM banned_ips WHERE ip=?", (client_ip,)).fetchone():
            raise HTTPException(403, "IP này đã bị chặn vĩnh viễn")
        user = conn.execute("SELECT * FROM users WHERE id=? AND active=1 AND status='active'", (uid,)).fetchone()
    if not user:
        raise HTTPException(401, "Tài khoản không khả dụng")
    allowed = [x.strip() for x in user["allowed_ips"].split(",") if x.strip()]
    if allowed and client_ip not in allowed:
        raise HTTPException(403, "IP này không được phép")
    return user


def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Chỉ quản trị viên được thực hiện")
    return user


def permitted(user: sqlite3.Row, project_id: str, feature_id: str = "*") -> bool:
    if user["role"] == "admin":
        return True
    with db() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM permissions WHERE user_id=? AND project_id=? AND feature_id IN ('*',?)",
            (user["id"], project_id, feature_id),
        ).fetchone())


def audit(request: Request, user: sqlite3.Row, action: str, target: str) -> None:
    with db() as conn:
        conn.execute("INSERT INTO audit_log(user_id,action,target,ip,created_at) VALUES(?,?,?,?,?)",
                     (user["id"], action, target, request.client.host if request.client else "", int(time.time())))


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def project_endpoint(project_id: str) -> tuple[str, int]:
    item = project(project_id)
    parsed = urlparse(item.get("health_url") or item["url"])
    return parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 80)


def project_command(item: dict[str, Any]) -> list[str]:
    """Select the native project command without leaking OS path rules into the UI."""
    if os.name != "nt" and item.get("command_linux"):
        return list(item["command_linux"])
    return list(item["command"])


def port_is_open(project_id: str) -> bool:
    host, port = project_endpoint(project_id)
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def listener_pid(project_id: str) -> int | None:
    """Recover the owner when the manager restarted and lost its in-memory Popen."""
    _, port = project_endpoint(project_id)
    if os.name != "nt":
        return None
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pattern = re.compile(rf"^\s*TCP\s+\S*:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$", re.MULTILINE)
    match = pattern.search(result.stdout)
    return int(match.group(1)) if match else None


def process_status(project_id: str) -> str:
    with PROCESS_LOCK:
        proc = PROCESSES.get(project_id)
        if proc and proc.poll() is None:
            return "running"
        if proc:
            PROCESSES.pop(project_id, None)
        pid_path = PID_DIR / f"{project_id}.pid"
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="ascii").strip())
                if process_exists(pid):
                    return "running"
                pid_path.unlink(missing_ok=True)
            except ValueError:
                pid_path.unlink(missing_ok=True)
    return "running" if port_is_open(project_id) else "stopped"


class LoginBody(BaseModel):
    username: str
    password: str


class UserBody(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=200)
    role: str = "user"
    allowed_ips: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class UserUpdateBody(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    role: str = "user"
    allowed_ips: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class UserStatusBody(BaseModel):
    status: str
    reason: str = Field(default="", max_length=300)


class BanIPBody(BaseModel):
    ip: str
    reason: str = Field(default="", max_length=300)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    client_ip = request.client.host if request.client else ""
    with db() as conn:
        if conn.execute("SELECT 1 FROM banned_ips WHERE ip=?", (client_ip,)).fetchone():
            raise HTTPException(403, "IP này đã bị chặn vĩnh viễn")
        user = conn.execute("SELECT * FROM users WHERE username=? AND active=1 AND status='active'", (body.username,)).fetchone()
    if not user or not password_ok(body.password, user["password_hash"]):
        raise HTTPException(401, "Sai tài khoản hoặc mật khẩu")
    response.set_cookie(SESSION_COOKIE, make_token(user["id"]), httponly=True, samesite="strict",
                        max_age=SESSION_SECONDS)
    return {"username": user["username"], "role": user["role"]}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


@app.get("/api/projects")
def list_projects(user=Depends(current_user)):
    result = []
    for item in projects():
        features = [f for f in item.get("features", []) if permitted(user, item["id"], f["id"])]
        if user["role"] == "admin" or features or permitted(user, item["id"]):
            result.append({**item, "features": features, "status": process_status(item["id"])})
    return result


@app.post("/api/projects/{project_id}/start")
def start_project(project_id: str, request: Request, user=Depends(current_user)):
    item = project(project_id)
    if not permitted(user, project_id):
        raise HTTPException(403, "Bạn không có quyền chạy dự án")
    if port_is_open(project_id):
        recovered_pid = listener_pid(project_id)
        if recovered_pid:
            (PID_DIR / f"{project_id}.pid").write_text(str(recovered_pid), encoding="ascii")
        return {"status": "running", "pid": recovered_pid, "recovered": True}
    with PROCESS_LOCK:
        existing = PROCESSES.get(project_id)
        if existing and existing.poll() is None:
            return {"status": "running", "pid": existing.pid}
        cwd = (ROOT / item["directory"]).resolve()
        command = project_command(item)
        executable = Path(command[0])
        if not executable.is_absolute():
            command[0] = str((cwd / executable).resolve())
        if not cwd.is_dir():
            raise HTTPException(409, f"Thư mục dự án không tồn tại: {cwd}")
        if not Path(command[0]).is_file():
            raise HTTPException(409, "Dự án chưa có môi trường Python; hãy cài requirements trước")
        log = open(LOG_DIR / f"{project_id}.log", "a", encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=flags, start_new_session=os.name != "nt")
        PROCESSES[project_id] = proc
        (PID_DIR / f"{project_id}.pid").write_text(str(proc.pid), encoding="ascii")
    audit(request, user, "start", project_id)
    return {"status": "starting", "pid": proc.pid}


@app.post("/api/projects/{project_id}/stop")
def stop_project(project_id: str, request: Request, user=Depends(current_user)):
    project(project_id)
    if not permitted(user, project_id):
        raise HTTPException(403, "Bạn không có quyền dừng dự án")
    with PROCESS_LOCK:
        proc = PROCESSES.get(project_id)
        pid_path = PID_DIR / f"{project_id}.pid"
        pid = proc.pid if proc and proc.poll() is None else None
        if pid is None and pid_path.exists():
            try:
                candidate = int(pid_path.read_text(encoding="ascii").strip())
                if process_exists(candidate):
                    pid = candidate
                else:
                    pid_path.unlink(missing_ok=True)
            except ValueError:
                pid_path.unlink(missing_ok=True)
        if pid is None:
            pid = listener_pid(project_id)
        if pid is None:
            return {"status": "stopped"}
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        PROCESSES.pop(project_id, None)
        pid_path.unlink(missing_ok=True)
    audit(request, user, "stop", project_id)
    return {"status": "stopped"}


@app.get("/api/projects/{project_id}/health")
def health(project_id: str, user=Depends(current_user)):
    item = project(project_id)
    if not permitted(user, project_id):
        raise HTTPException(403, "Không có quyền")
    try:
        with urllib.request.urlopen(item["health_url"], timeout=2) as response:
            return {"online": response.status < 500, "status_code": response.status}
    except Exception:
        return {"online": False}


@app.get("/api/authorize/{project_id}/{feature_id}")
def authorize(project_id: str, feature_id: str, user=Depends(current_user)):
    if not permitted(user, project_id, feature_id):
        raise HTTPException(403, "Không có quyền dùng chức năng")
    return {"allowed": True, "user": user["username"]}


@app.get("/api/users")
def users(_: sqlite3.Row = Depends(admin)):
    with db() as conn:
        rows = conn.execute("SELECT id,username,role,allowed_ips,active,status,ban_reason,created_at FROM users ORDER BY id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["permissions"] = [
                f"{permission['project_id']}:{permission['feature_id']}"
                for permission in conn.execute(
                    "SELECT project_id,feature_id FROM permissions WHERE user_id=? ORDER BY project_id,feature_id",
                    (row["id"],),
                ).fetchall()
            ]
            result.append(item)
        return result


@app.post("/api/users")
def create_user(body: UserBody, request: Request, user=Depends(admin)):
    if body.role not in {"admin", "user"}:
        raise HTTPException(400, "Role không hợp lệ")
    try:
        with db() as conn:
            cursor = conn.execute("INSERT INTO users(username,password_hash,role,allowed_ips,created_at) VALUES(?,?,?,?,?)",
                                  (body.username, password_hash(body.password), body.role, ",".join(body.allowed_ips), int(time.time())))
            for value in body.permissions:
                project_id, _, feature_id = value.partition(":")
                conn.execute("INSERT INTO permissions VALUES(?,?,?)", (cursor.lastrowid, project_id, feature_id or "*"))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Tên đăng nhập đã tồn tại")
    audit(request, user, "create_user", body.username)
    return {"id": cursor.lastrowid, "username": body.username}


@app.put("/api/users/{user_id}")
def update_user(user_id: int, body: UserUpdateBody, request: Request, user=Depends(admin)):
    if body.role not in {"admin", "user"}:
        raise HTTPException(400, "Role không hợp lệ")
    if user_id == user["id"] and body.role != "admin":
        raise HTTPException(400, "Không thể tự gỡ quyền quản trị của mình")
    with db() as conn:
        target = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "Không tìm thấy người dùng")
        try:
            if body.password:
                conn.execute("UPDATE users SET username=?,role=?,allowed_ips=?,password_hash=? WHERE id=?",
                             (body.username, body.role, ",".join(body.allowed_ips), password_hash(body.password), user_id))
            else:
                conn.execute("UPDATE users SET username=?,role=?,allowed_ips=? WHERE id=?",
                             (body.username, body.role, ",".join(body.allowed_ips), user_id))
            conn.execute("DELETE FROM permissions WHERE user_id=?", (user_id,))
            for value in body.permissions:
                project_id, _, feature_id = value.partition(":")
                conn.execute("INSERT OR IGNORE INTO permissions VALUES(?,?,?)",
                             (user_id, project_id, feature_id or "*"))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Tên đăng nhập đã tồn tại")
    audit(request, user, "update_user", f"{user_id}:{body.username}")
    return {"ok": True}


@app.post("/api/users/{user_id}/status")
def set_user_status(user_id: int, body: UserStatusBody, request: Request, user=Depends(admin)):
    if body.status not in {"active", "disabled", "banned"}:
        raise HTTPException(400, "Trạng thái không hợp lệ")
    if user_id == user["id"] and body.status != "active":
        raise HTTPException(400, "Không thể khóa hoặc ban chính mình")
    with db() as conn:
        cursor = conn.execute(
            "UPDATE users SET status=?,active=?,ban_reason=? WHERE id=?",
            (body.status, 1 if body.status == "active" else 0,
             body.reason if body.status == "banned" else "", user_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Không tìm thấy người dùng")
    audit(request, user, f"user_{body.status}", str(user_id))
    return {"ok": True, "status": body.status}


@app.delete("/api/users/{user_id}")
def disable_user(user_id: int, request: Request, user=Depends(admin)):
    if user_id == user["id"]:
        raise HTTPException(400, "Không thể khóa chính mình")
    with db() as conn:
        conn.execute("UPDATE users SET active=0,status='disabled' WHERE id=?", (user_id,))
    audit(request, user, "disable_user", str(user_id))
    return {"ok": True}


@app.get("/api/banned-ips")
def list_banned_ips(_: sqlite3.Row = Depends(admin)):
    with db() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT b.ip,b.reason,b.created_at,u.username AS created_by
               FROM banned_ips b LEFT JOIN users u ON u.id=b.created_by ORDER BY b.created_at DESC"""
        ).fetchall()]


@app.post("/api/banned-ips")
def ban_ip(body: BanIPBody, request: Request, user=Depends(admin)):
    try:
        normalized = str(ipaddress.ip_address(body.ip.strip()))
    except ValueError:
        raise HTTPException(400, "Địa chỉ IP không hợp lệ")
    client_ip = request.client.host if request.client else ""
    if normalized == client_ip:
        raise HTTPException(400, "Không thể chặn IP bạn đang sử dụng")
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO banned_ips(ip,reason,created_by,created_at) VALUES(?,?,?,?)",
                     (normalized, body.reason, user["id"], int(time.time())))
    audit(request, user, "ban_ip", normalized)
    return {"ok": True, "ip": normalized}


@app.delete("/api/banned-ips/{ip}")
def unban_ip(ip: str, request: Request, user=Depends(admin)):
    with db() as conn:
        conn.execute("DELETE FROM banned_ips WHERE ip=?", (ip,))
    audit(request, user, "unban_ip", ip)
    return {"ok": True}


@app.get("/api/audit")
def audit_list(_: sqlite3.Row = Depends(admin)):
    with db() as conn:
        rows = conn.execute("""SELECT a.id,u.username,a.action,a.target,a.ip,a.created_at
                             FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
                             ORDER BY a.id DESC LIMIT 200""").fetchall()
        return [dict(row) for row in rows]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7999)
