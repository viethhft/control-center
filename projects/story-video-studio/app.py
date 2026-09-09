from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from pipeline import DATA, ROOT, run, save

POOL = ThreadPoolExecutor(max_workers=1)
ACTIVE = set()
LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    for path in DATA.glob('*/status.json'):
        status = json.loads(path.read_text(encoding='utf-8'))
        if status['state'] in ('running', 'queued'):
            save(path, {'state': 'interrupted', 'message': 'Ứng dụng đã khởi động lại. Nhấn tiếp tục.', 'resumable': True})
    yield


app = FastAPI(title='Story Video Studio', lifespan=lifespan)

VISUAL_MODES = {
    'hand_drawn_whiteboard',
    'motion_comic',
    'cutout_animation',
    'cinematic_slideshow',
}


def folder(pid):
    if not re.fullmatch('[a-f0-9]{32}', pid) or not (DATA / pid / 'project.json').is_file():
        raise HTTPException(404, 'Không tìm thấy dự án')
    return DATA / pid


def enqueue(pid):
    with LOCK:
        if pid in ACTIVE:
            raise HTTPException(409, 'Dự án đang chạy')
        if len(ACTIVE) >= 10:
            raise HTTPException(429, 'Hàng đợi đã đầy')
        ACTIVE.add(pid)
    previous = json.loads((DATA / pid / 'status.json').read_text(encoding='utf-8')) if (DATA / pid / 'status.json').exists() else {}
    save(DATA / pid / 'status.json', {**previous, 'state': 'queued', 'message': 'Đang chờ GPU', 'progress': int(previous.get('progress', 0)), 'stage': previous.get('stage', 'queued'), 'updated_at': time.time()})
    def work():
        try:
            run(DATA / pid)
        finally:
            with LOCK:
                ACTIVE.discard(pid)
    try:
        POOL.submit(work)
    except Exception:
        with LOCK:
            ACTIVE.discard(pid)
        save(DATA / pid / 'status.json', {'state': 'failed', 'message': 'Không khởi động được worker', 'resumable': True})
        raise


@app.get('/')
def index():
    return FileResponse(ROOT / 'index.html')


@app.get('/favicon.svg')
def favicon():
    return FileResponse(ROOT / 'favicon.svg', media_type='image/svg+xml')


@app.get('/api/health')
def health():
    import os
    import urllib.request

    def service_ok(url):
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            return False

    ollama_url = os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434')
    comfy_url = os.getenv('COMFYUI_URL', 'http://127.0.0.1:8188')
    dependencies = {
        'ollama': service_ok(ollama_url + '/api/tags'),
        'comfyui': service_ok(comfy_url + '/system_stats'),
    }
    return {
        'status': 'ok' if all(dependencies.values()) else 'degraded',
        'ffmpeg': bool(shutil.which(os.getenv('FFMPEG', 'ffmpeg'))),
        'ffprobe': bool(shutil.which(os.getenv('FFPROBE', 'ffprobe'))),
        'dependencies': dependencies,
        'urls': {'ollama': ollama_url, 'comfyui': comfy_url},
    }


def request_control(pid, action):
    target = DATA / pid
    save(target / 'control.json', {'action': action})
    current_path = target / 'status.json'
    current = json.loads(current_path.read_text(encoding='utf-8')) if current_path.exists() else {}
    label = 'Đang tạm dừng…' if action == 'pause' else 'Đang hủy…'
    save(current_path, {**current, 'state': 'pausing' if action == 'pause' else 'cancelling', 'message': label, 'updated_at': time.time()})


@app.get('/api/projects')
def projects():
    result = []
    for path in sorted(DATA.glob('*/project.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        p = json.loads(path.read_text(encoding='utf-8'))
        status = path.parent / 'status.json'
        item = json.loads(status.read_text(encoding='utf-8')) if status.exists() else {'state': 'ready'}
        item.setdefault('progress', 0)
        item.setdefault('stage', 'queued' if item['state'] in ('queued', 'ready') else 'unknown')
        result.append({'id': path.parent.name, 'title': p['title'], **item})
    return result


async def upload(file, destination, limit=1024 * 1024 * 1024):
    size = 0
    try:
        with destination.open('wb') as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, 'Audio tối đa 1 GB')
                stream.write(chunk)
        if not size:
            raise HTTPException(400, 'File rỗng')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.post('/api/projects', status_code=201)
async def create(title: str = Form(...), story: str = Form(...), audio: UploadFile = File(...), music: UploadFile | None = File(None), alignment: str = Form('whisper'), aspect: str = Form('16:9'), shot_seconds: int = Form(30), visual_mode: str = Form('hand_drawn_whiteboard'), style: str = Form('cinematic illustration, consistent character design'), burn_subtitles: bool = Form(True)):
    if not 30 <= len(story.strip()) <= 500000 or not 1 <= len(title.strip()) <= 200:
        raise HTTPException(422, 'Truyện cần 30–500.000 ký tự; tiêu đề 1–200 ký tự.')
    if alignment not in ('whisper', 'estimate') or aspect not in ('16:9', '9:16') or visual_mode not in VISUAL_MODES or not 4 <= shot_seconds <= 60 or len(style) > 2000:
        raise HTTPException(422, 'Cấu hình không hợp lệ')
    pid = uuid.uuid4().hex
    target = DATA / pid
    target.mkdir()
    await upload(audio, target / 'narration.audio')
    has_music = music is not None and bool(music.filename)
    if has_music:
        await upload(music, target / 'music.audio')
    save(target / 'project.json', {'title': title.strip(), 'story': story.strip(), 'audio': 'narration.audio', 'music': 'music.audio' if has_music else None, 'alignment': alignment, 'width': 1280 if aspect == '16:9' else 720, 'height': 720 if aspect == '16:9' else 1280, 'shot_seconds': shot_seconds, 'visual_mode': visual_mode, 'style': style, 'burn_subtitles': burn_subtitles})
    enqueue(pid)
    return {'id': pid}


@app.post('/api/projects/{pid}/resume')
def resume(pid: str):
    target = folder(pid)
    current = json.loads((target / 'status.json').read_text(encoding='utf-8')) if (target / 'status.json').exists() else {}
    if current.get('state') in ('running', 'queued', 'pausing', 'cancelling'):
        raise HTTPException(409, 'Dự án vẫn đang chạy')
    (target / 'control.json').unlink(missing_ok=True)
    enqueue(pid)
    return {'state': 'queued'}


@app.post('/api/projects/{pid}/pause')
def pause(pid: str):
    target = folder(pid)
    current = json.loads((target / 'status.json').read_text(encoding='utf-8')) if (target / 'status.json').exists() else {}
    if current.get('state') not in ('running', 'queued'):
        raise HTTPException(409, 'Dự án không ở trạng thái có thể tạm dừng')
    request_control(pid, 'pause')
    return {'state': 'pausing'}


@app.post('/api/projects/{pid}/cancel')
def cancel(pid: str):
    target = folder(pid)
    current = json.loads((target / 'status.json').read_text(encoding='utf-8')) if (target / 'status.json').exists() else {}
    if current.get('state') not in ('running', 'queued', 'pausing'):
        raise HTTPException(409, 'Dự án không ở trạng thái có thể hủy')
    request_control(pid, 'cancel')
    return {'state': 'cancelling'}


@app.get('/api/projects/{pid}/files/{name}')
def download(pid: str, name: str):
    target = folder(pid)
    if name not in ('final.mp4', 'subtitles.srt', 'manifest.json', 'captions.json') and not re.fullmatch('[a-f0-9]{24}\.png', name):
        raise HTTPException(404)
    path = target / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)
