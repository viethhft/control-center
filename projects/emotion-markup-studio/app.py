import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "projects"
DATA.mkdir(exist_ok=True)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("EMOTION_MODEL", "qwen3.5:27b")
ALLOWED_CUES = {
    "normal", "laughs", "chuckles", "sighs", "whispers", "excited",
    "surprised", "angry", "sad", "fearful", "tender", "tense",
}

app = FastAPI(title="Emotion Markup Studio", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class AnalyzeRequest(BaseModel):
    title: str = Field(default="Văn bản mới", max_length=150)
    text: str = Field(min_length=2, max_length=200000)
    genre: str = Field(default="Tự động", max_length=80)
    intensity: str = Field(default="balanced", pattern="^(subtle|balanced|dramatic)$")
    context: str = Field(default="", max_length=2000)


def split_blocks(text: str) -> list[str]:
    return [part for part in re.split(r"(\n\s*\n)", text) if part]


def content_blocks(parts: list[str]) -> list[tuple[int, str]]:
    return [(i, part) for i, part in enumerate(parts) if part.strip()]


def extract_json(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Model không trả JSON hợp lệ")
    return json.loads(raw[start:end + 1])


async def analyze_batch(items: list[tuple[int, str]], req: AnalyzeRequest) -> dict[int, dict]:
    numbered = "\n\n".join(
        f"ID {idx}:\n{text[:6000]}" for idx, text in items
    )
    density = {"subtle": "rất tiết chế", "balanced": "tự nhiên, cân bằng", "dramatic": "rõ nét, giàu kịch tính"}[req.intensity]
    prompt = f"""Phân tích cảm xúc cho từng khối văn bản để AI TTS đọc truyền cảm.
Thể loại: {req.genre}. Bối cảnh: {req.context or 'tự suy luận từ văn bản'}.
Mức biểu cảm: {density}.
Chỉ chọn cue trong danh sách: {', '.join(sorted(ALLOWED_CUES))}.
Không viết lại nội dung. Với mỗi ID, chọn một cue mở đầu phù hợp và khoảng nghỉ sau khối (0, 250, 400, 600 hoặc 900 ms).
Không lạm dụng laughs/chuckles; chỉ dùng khi thật sự có tiếng cười hoặc sắc thái hài hước rõ.
Trả đúng JSON: {{"items":[{{"id":0,"cue":"normal","pause_ms":400,"reason":"mô tả ngắn"}}]}}.

VĂN BẢN:
{numbered}"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": MODEL,
                "stream": False,
                "format": "json",
                "think": False,
                "messages": [
                    {"role": "system", "content": "Bạn là đạo diễn giọng đọc tiếng Việt. Chỉ trả JSON hợp lệ."},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        if response.status_code == 404:
            installed = []
            try:
                tags = await client.get(f"{OLLAMA_URL}/api/tags")
                tags.raise_for_status()
                installed = [item.get("name", "") for item in tags.json().get("models", [])]
            except Exception:
                pass
            available = ", ".join(filter(None, installed)) or "không xác định"
            raise RuntimeError(
                f"Ollama không tìm thấy model '{MODEL}'. Model đang có: {available}. "
                f"Hãy chạy: ollama pull {MODEL}"
            )
        response.raise_for_status()
        parsed = extract_json(response.json()["message"]["content"])
    result = {}
    valid_ids = {idx for idx, _ in items}
    for item in parsed.get("items", []):
        idx = item.get("id")
        cue = str(item.get("cue", "normal")).lower().strip("[] ")
        if idx not in valid_ids or cue not in ALLOWED_CUES:
            continue
        pause = int(item.get("pause_ms", 400) or 0)
        result[idx] = {
            "cue": cue,
            "pause_ms": min((0, 250, 400, 600, 900), key=lambda value: abs(value - pause)),
            "reason": str(item.get("reason", ""))[:240],
        }
    return result


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    parts = split_blocks(req.text)
    blocks = content_blocks(parts)
    decisions: dict[int, dict] = {}
    try:
        for start in range(0, len(blocks), 12):
            decisions.update(await analyze_batch(blocks[start:start + 12], req))
    except Exception as exc:
        raise HTTPException(502, f"Không thể phân tích bằng Ollama: {type(exc).__name__}: {exc}")

    annotated = []
    previous_cue = None
    details = []
    for idx, part in enumerate(parts):
        if not part.strip():
            annotated.append(part)
            continue
        choice = decisions.get(idx, {"cue": "normal", "pause_ms": 400, "reason": "Nhịp trung tính"})
        cue = choice["cue"]
        prefix = f"[{cue}] " if cue != previous_cue else ""
        suffix = f" [pause={choice['pause_ms']}ms]" if choice["pause_ms"] else ""
        annotated.append(prefix + part.strip() + suffix)
        previous_cue = cue
        details.append({"index": len(details) + 1, "preview": part.strip()[:100], **choice})

    project_id = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    record = {
        "id": project_id,
        "title": req.title,
        "genre": req.genre,
        "intensity": req.intensity,
        "context": req.context,
        "source": req.text,
        "annotated_text": "".join(annotated),
        "segments": details,
        "created_at": time.time(),
    }
    (DATA / f"{project_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


@app.get("/api/health")
def health():
    return {"ok": True, "model": MODEL, "cues": sorted(ALLOWED_CUES)}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
