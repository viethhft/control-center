from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
STATIC = ROOT / "static"
PROJECTS = ROOT / "projects"
RUNTIME = ROOT / "runtime"
RENDER_JOB_DIR = RUNTIME / "render_jobs"
ANALYSIS_JOB_DIR = RUNTIME / "analysis_jobs"
for directory in (STATIC, PROJECTS, RENDER_JOB_DIR, ANALYSIS_JOB_DIR):
    directory.mkdir(parents=True, exist_ok=True)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_STORY_MODEL", "qwen3.5:27b")
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
QWEN_IMAGE_MODEL = os.getenv(
    "QWEN_IMAGE_MODEL", "qwen_image_distill_full_fp8_e4m3fn.safetensors"
)
QWEN_IMAGE_EDIT_MODEL = os.getenv(
    "QWEN_IMAGE_EDIT_MODEL", "qwen_image_edit_2509_fp8_e4m3fn.safetensors"
)
QWEN_IMAGE_ENCODER = os.getenv(
    "QWEN_IMAGE_ENCODER", "qwen_2.5_vl_7b_fp8_scaled.safetensors"
)
QWEN_IMAGE_VAE = os.getenv("QWEN_IMAGE_VAE", "qwen_image_vae.safetensors")
COMFYUI_WORKFLOW = os.getenv("COMFYUI_WORKFLOW", "").strip()
COMFYUI_TIMEOUT = max(60, int(os.getenv("COMFYUI_TIMEOUT_SECONDS", "1800")))
COMFYUI_RETRIES = max(0, int(os.getenv("COMFYUI_RENDER_RETRIES", "2")))
COMFYUI_CONCURRENCY = max(1, int(os.getenv("COMFYUI_MAX_CONCURRENCY", "1")))
COMFYUI_POLL = max(0.25, float(os.getenv("COMFYUI_POLL_SECONDS", "1")))
COMFYUI_GATE = asyncio.Semaphore(COMFYUI_CONCURRENCY)
JOBS: dict[str, dict[str, Any]] = {}
ANALYSIS_TASKS: dict[str, asyncio.Task] = {}
RENDER_JOBS: dict[str, dict[str, Any]] = {}
RENDER_TASKS: dict[str, asyncio.Task] = {}
PROJECT_LOCKS: dict[str, asyncio.Lock] = {}
app = FastAPI(title="StoryFrame Studio", version="0.4.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/projects", StaticFiles(directory=PROJECTS), name="projects")


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


class AnalyzeRequest(BaseModel):
    title: str = "Truyện chưa đặt tên"
    story: str = Field(min_length=30)
    style: str = "cinematic Asian youth mystery drama"
    aspect_ratio: str = "16:9"
    image_count: int = Field(12, ge=1, le=80)
    language: str = "vi"

    @field_validator("title", "story", "style", mode="before")
    @classmethod
    def clean(cls, v: Any) -> str:
        return "" if v is None else str(v).replace("\x00", "").strip()


class ProjectPayload(BaseModel):
    project: dict[str, Any]


class GenerateRequest(BaseModel):
    project_id: str
    scene_id: str
    width: int = 1664
    height: int = 928
    steps: int = Field(15, ge=4, le=100)
    quality: Literal["draft", "final"] = "final"


class RenderAllRequest(BaseModel):
    project_id: str
    width: int = 1664
    height: int = 928
    steps: int = Field(15, ge=4, le=100)
    overwrite: bool = False
    quality: Literal["draft", "final"] = "draft"


class ReferenceSelectRequest(BaseModel):
    reference_url: str


@app.exception_handler(RequestValidationError)
async def invalid(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Dữ liệu không hợp lệ: "
            + "; ".join(f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors())
        },
    )


def safe(pid: str) -> Path:
    if not re.fullmatch(r"[\w-]+", pid):
        raise HTTPException(400, "Project ID không hợp lệ")
    return PROJECTS / pid


def project_lock(pid: str) -> asyncio.Lock:
    return PROJECT_LOCKS.setdefault(pid, asyncio.Lock())


def project_slug(title: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", title)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:48] or "story-project"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def analysis_file(jid: str, suffix: str = "job") -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", jid):
        raise HTTPException(400, "Job ID không hợp lệ")
    return ANALYSIS_JOB_DIR / f"{jid}.{suffix}.json"


def write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def normalize_render_fields(project: dict) -> bool:
    """Keep projects created before draft/final outputs were separated compatible."""
    changed = False
    for scene in project.get("scenes", []):
        legacy_url = scene.get("image_url")
        if legacy_url and not scene.get("draft_image_url") and not scene.get("final_image_url"):
            scene["final_image_url"] = legacy_url
            legacy_meta = dict(scene.get("render_meta") or {})
            legacy_meta.setdefault("quality", "final")
            legacy_meta.setdefault("legacy", True)
            scene.setdefault("renders", {})["final"] = legacy_meta
            changed = True
    return changed


def persist_analysis_job(jid: str) -> None:
    write_json_atomic(analysis_file(jid), JOBS[jid])


def save_analysis_state(jid: str, state: dict) -> None:
    state["saved_at"] = time.time()
    write_json_atomic(analysis_file(jid, "state"), state)
    JOBS[jid]["checkpoint"] = {
        "phase": state.get("phase", "unknown"),
        "outlines": len(state.get("outlines", {})),
        "details": len(state.get("detailed", {})),
        "ready_prompts": sum(
            bool(scene.get("prompt")) for scene in state.get("detailed", {}).values()
        ),
        "saved_at": state["saved_at"],
    }
    persist_analysis_job(jid)
    pid = state.get("project_id")
    if pid:
        folder = safe(pid)
        folder.mkdir(parents=True, exist_ok=True)
        if state.get("request"):
            write_json_atomic(folder / "analysis-input.json", state["request"])
        write_json_atomic(
            folder / "analysis-progress.json",
            {
                "project_id": pid,
                "project_name": state.get("request", {}).get("title", ""),
                "job_id": jid,
                "phase": state.get("phase", "unknown"),
                "checkpoint": JOBS[jid]["checkpoint"],
                "status": JOBS[jid].get("status"),
                "message": JOBS[jid].get("message"),
                "progress": JOBS[jid].get("progress", 0),
                "updated_at": state["saved_at"],
            },
        )


def load_analysis_state(jid: str) -> dict:
    path = analysis_file(jid, "state")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def update(jid: str, **v: Any):
    JOBS[jid].update(v, updated_at=time.time())
    if v.get("status") in ("failed", "completed", "cancelled"):
        persist_analysis_job(jid)


def launch_analysis_task(jid: str, req: AnalyzeRequest) -> asyncio.Task:
    """Start one tracked task so the API can cancel it safely."""
    existing = ANALYSIS_TASKS.get(jid)
    if existing and not existing.done():
        raise HTTPException(409, "Tiến trình vẫn đang chạy")
    task = asyncio.create_task(run_job_v2(jid, req), name=f"analysis:{jid}")
    ANALYSIS_TASKS[jid] = task

    def forget(done: asyncio.Task) -> None:
        if ANALYSIS_TASKS.get(jid) is done:
            ANALYSIS_TASKS.pop(jid, None)

    task.add_done_callback(forget)
    return task


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ValueError("AI không trả về JSON hợp lệ")
    return json.loads(text[a : b + 1])


def explain(exc: Exception, stage: str, n: int) -> tuple[str, str, str]:
    tech = f"{type(exc).__name__}: {str(exc) or repr(exc)}"
    if isinstance(exc, httpx.ConnectError):
        return "Không kết nối được Ollama.", tech, "Mở Ollama và kiểm tra cổng 11434."
    if isinstance(exc, httpx.TimeoutException):
        return (
            "Ollama phản hồi quá lâu.",
            tech,
            "Giảm số cảnh, rút ngắn truyện hoặc thử lại.",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:1000].strip()
        return (
            f"Ollama trả HTTP {exc.response.status_code}.",
            f"HTTP {exc.response.status_code} {exc.request.url}: {body or exc.response.reason_phrase}",
            "Kiểm tra model bằng lệnh ollama list.",
        )
    if isinstance(exc, json.JSONDecodeError):
        return (
            "AI trả JSON chưa hoàn chỉnh.",
            tech,
            f"Đã nhận {n:,} ký tự; hãy giảm số cảnh hoặc thử lại.",
        )
    if isinstance(exc, ValueError):
        return (
            str(exc) or "Kết quả AI không đạt yêu cầu.",
            tech,
            "Thử lại hoặc giảm số cảnh.",
        )
    return (
        str(exc).strip() or "Lỗi không xác định.",
        tech,
        f"Lỗi tại giai đoạn {stage}.",
    )


def model_profile(model_name: str, parameter_size: str = "") -> dict[str, int | str]:
    value = 0.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*[Bb]", parameter_size) or re.search(
        r":(\d+(?:\.\d+)?)b", model_name, re.IGNORECASE
    )
    if match:
        value = float(match.group(1))
    if value and value <= 4:
        return {
            "name": "small",
            "billions": value,
            "outline_batch": 4,
            "detail_batch": 1,
            "prompt_batch": 3,
            "retries": 2,
        }
    if value and value <= 9:
        return {
            "name": "medium",
            "billions": value,
            "outline_batch": 8,
            "detail_batch": 3,
            "prompt_batch": 5,
            "retries": 2,
        }
    if value and value <= 20:
        return {
            "name": "standard",
            "billions": value,
            "outline_batch": 20,
            "detail_batch": 8,
            "prompt_batch": 8,
            "retries": 1,
        }
    return {
        "name": "large-fast",
        "billions": value,
        "outline_batch": 40,
        "detail_batch": 16,
        "prompt_batch": 16,
        "retries": 1,
    }


def split_story(story: str, count: int) -> list[str]:
    words = story.split()
    if not words:
        return [""] * count
    return [
        " ".join(
            words[round(i * len(words) / count) : round((i + 1) * len(words) / count)]
        )
        for i in range(count)
    ]


def fallback_outline(scene_id: str, excerpt: str, characters: list[dict]) -> dict:
    mentioned = [
        c.get("id")
        for c in characters
        if c.get("name") and c["name"].lower() in excerpt.lower()
    ]
    return {
        "id": scene_id,
        "title": f'Cảnh {int(scene_id.split("_")[-1])}',
        "story_beat": excerpt,
        "characters": mentioned,
        "setting": "Bối cảnh theo đoạn truyện nguồn",
        "time_weather": "Theo ngữ cảnh truyện",
        "continuity": "Tiếp nối cảnh trước",
    }


def fallback_detail(outline: dict, characters: list[dict], style: str) -> dict:
    ids = set(outline.get("characters", []))
    chosen = [c for c in characters if c.get("id") in ids]
    identity = "; ".join(
        f"{c.get('name')}: {c.get('appearance')}; {c.get('wardrobe')}; {c.get('signature')}"
        for c in chosen
    )
    prompt = f"{style}. {outline.get('story_beat','')}. Setting: {outline.get('setting','')}. Characters: {identity}. cinematic composition, natural expression, no text"
    return {
        **outline,
        "action_expression": outline.get("story_beat", ""),
        "composition": "cinematic medium shot, clear visual storytelling",
        "lighting_color": "cinematic lighting matching the scene",
        "prompt": prompt,
        "negative_prompt": "text, subtitles, watermark, logo, inconsistent face, wrong clothes, extra fingers",
        "generation_warning": "Model không hoàn tất sau retry; cảnh được dựng từ outline và character bible.",
    }


def build_scene_prompt(
    scene: dict, characters: list[dict], style: str, ratio: str
) -> str:
    ids = set(scene.get("characters") or [])
    chosen = [character for character in characters if character.get("id") in ids]
    identity = "; ".join(
        f"{character.get('name','nhân vật')}: {character.get('appearance','')}; trang phục {character.get('wardrobe','')}; dấu hiệu {character.get('signature','')}"
        for character in chosen
    )
    must = "; ".join(str(item) for item in (scene.get("must_include") or []) if item)
    return ". ".join(
        filter(
            None,
            [
                f"Phong cách hình ảnh cố định: {style}",
                f"Khung hình kể chuyện tỉ lệ {ratio}",
                str(scene.get("story_beat", "")),
                f"Bối cảnh: {scene.get('setting','')}, {scene.get('time_weather','')}",
                f"Nhân vật: {identity}" if identity else "",
                f"Hành động và biểu cảm: {scene.get('action_expression','')}",
                f"Bố trí không gian: {scene.get('spatial_layout','')}",
                f"Máy quay và bố cục: {scene.get('composition','')}",
                f"Ánh sáng và màu sắc: {scene.get('lighting_color','')}",
                f"Chi tiết bắt buộc: {must}" if must else "",
                "Không có chữ, ký tự, logo hay watermark trong ảnh",
            ],
        )
    )


MOJIBAKE_MARKERS = ("Ã", "Ä", "Â", "â€", "áº", "á»")


def repair_text(value: str) -> str:
    current = value
    for _ in range(2):
        if not any(marker in current for marker in MOJIBAKE_MARKERS):
            break
        best = current
        best_score = sum(current.count(marker) for marker in MOJIBAKE_MARKERS)
        for encoding in ("cp1252", "latin1"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            score = sum(candidate.count(marker) for marker in MOJIBAKE_MARKERS)
            if score < best_score:
                best, best_score = candidate, score
        if best == current:
            break
        current = best
    return current


def repair_data(value: Any) -> Any:
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [repair_data(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_data(item) for key, item in value.items()}
    return value


def clean_character_refs(scene: dict, characters: list[dict]) -> list[str]:
    valid = {
        str(character.get("id")) for character in characters if character.get("id")
    }
    refs = []
    for ref in scene.get("characters") or []:
        ref = str(ref)
        if ref in valid and ref not in refs:
            refs.append(ref)
    return refs[:3]


async def ollama_json(
    jid: str,
    client: httpx.AsyncClient,
    system: str,
    prompt: str,
    stage: str,
    label: str,
    start: int,
    end: int,
    started: float,
) -> dict:
    text = ""
    base = int(JOBS[jid].get("generated_chars", 0))
    update(jid, status="running", stage=stage, message=label, progress=start)
    if stage == "batch" and "CHARACTER BIBLE:" in prompt:
        prompt = re.sub(
            r"Schema mỗi cảnh:.*?CHARACTER BIBLE:",
            "Schema mỗi cảnh CHỈ gồm: id,title,story_beat,characters,setting,time_weather,action_expression,spatial_layout,composition,lighting_color,continuity,must_include. Không viết prompt hoặc negative_prompt; không lặp ngoại hình nhân vật. Mỗi trường tối đa 1-2 câu, must_include gồm 3-6 cụm từ ngắn. Backend tự dựng prompt hoàn chỉnh. Giữ nguyên ID và không yêu cầu vẽ chữ hay logo.\nCHARACTER BIBLE:",
            prompt,
            flags=re.DOTALL,
        )
    budgets = {
        "characters": (24576, 3072),
        "outline": (8192, 2048),
        "batch": (12288, 4096),
        "prompt_editing": (8192, 4096),
    }
    num_ctx, num_predict = budgets.get(stage, (8192, 4096))
    body = {
        "model": OLLAMA_MODEL,
        "stream": True,
        "format": "json",
        "think": False,
        "keep_alive": "10m",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
    }
    async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=body) as response:
        if response.status_code == 404:
            raise RuntimeError(
                f"Không tìm thấy model {OLLAMA_MODEL}: {(await response.aread()).decode('utf-8','replace')}"
            )
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            part = json.loads(line)
            text += part.get("message", {}).get("content", "")
            ratio = min(1, len(text) / 3500)
            update(
                jid,
                progress=min(end - 1, start + int((end - start) * ratio)),
                generated_chars=base + len(text),
                elapsed=round(time.time() - started, 1),
                message=f"{label} · đã nhận {base+len(text):,} ký tự",
            )
    update(jid, generated_chars=base + len(text), progress=end)
    return extract_json(text)


async def release_ollama(client: httpx.AsyncClient) -> None:
    try:
        await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "keep_alive": 0},
            timeout=20,
        )
    except Exception:
        pass


async def run_job(jid: str, req: AnalyzeRequest):
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
            bible = await ollama_json(
                jid,
                client,
                "Bạn là chuyên gia thiết kế nhân vật. Chỉ trả JSON.",
                f"""Đọc truyện và tạo character bible. JSON: {{"summary":"...","visual_direction":"...","characters":[{{"id":"char_01","name":"...","role":"...","age":"...","gender":"...","appearance":"mô tả nhận dạng bất biến rất chi tiết","wardrobe":"trang phục cố định","signature":"đặc điểm độc nhất","negative":"sai lệch cần tránh"}}]}}. Không tạo scenes.\nPHONG CÁCH: {req.style}\nTRUYỆN:\n{req.story}""",
                "characters",
                "Phân tích nhân vật và khóa nhận dạng…",
                5,
                20,
                started,
            )
            if not bible.get("characters"):
                raise ValueError("Không tạo được character bible")
            char_ids = [
                {"id": c.get("id"), "name": c.get("name")} for c in bible["characters"]
            ]
            outline = await ollama_json(
                jid,
                client,
                "Bạn là biên kịch storyboard. Chỉ trả JSON ngắn gọn.",
                f"""Chia toàn bộ truyện thành ĐÚNG {req.image_count} cảnh liên tiếp. JSON: {{"scenes":[{{"id":"scene_001","title":"...","story_beat":"diễn biến cụ thể","characters":["char_01"],"setting":"...","time_weather":"...","continuity":"..."}}]}}. ID phải từ scene_001 đến scene_{req.image_count:03d}, không bỏ số, mỗi cảnh ngắn gọn, không viết prompt ảnh. Nhân vật hợp lệ: {json.dumps(char_ids,ensure_ascii=False)}\nTRUYỆN:\n{req.story}""",
                "outline",
                f"Lập outline đúng {req.image_count} cảnh…",
                20,
                35,
                started,
            )
            outlines = outline.get("scenes", [])
            by_id = {s.get("id"): s for s in outlines}
            expected = [f"scene_{i:03d}" for i in range(1, req.image_count + 1)]
            missing = [x for x in expected if x not in by_id]
            if missing:
                fixed = await ollama_json(
                    jid,
                    client,
                    "Bạn bổ sung outline storyboard. Chỉ trả JSON ngắn gọn.",
                    f"""Outline hiện thiếu các ID: {", ".join(missing)}. Tạo đúng các cảnh thiếu với schema {{"scenes":[{{"id":"scene_...","title":"...","story_beat":"...","characters":["char_01"],"setting":"...","time_weather":"...","continuity":"..."}}]}}. Bám đúng truyện và đặt diễn biến vào vị trí hợp lý giữa các cảnh đã có. Nhân vật: {json.dumps(char_ids,ensure_ascii=False)}\nCẢNH ĐÃ CÓ:\n{json.dumps(outlines,ensure_ascii=False)}\nTRUYỆN:\n{req.story}""",
                    "outline_repair",
                    f"Bổ sung {len(missing)} outline còn thiếu…",
                    30,
                    35,
                    started,
                )
                for scene in fixed.get("scenes", []):
                    if scene.get("id") in missing:
                        by_id[scene["id"]] = scene
            missing = [x for x in expected if x not in by_id]
            if missing:
                raise ValueError(
                    f'Outline vẫn thiếu {len(missing)} cảnh: {", ".join(missing)}'
                )
            batches = [expected[i : i + 5] for i in range(0, len(expected), 5)]
            detailed: dict[str, dict] = {}
            bible_compact = json.dumps(bible["characters"], ensure_ascii=False)
            for index, ids in enumerate(batches):
                batch_outline = [by_id[x] for x in ids]
                p0 = 35 + round(45 * index / len(batches))
                p1 = 35 + round(45 * (index + 1) / len(batches))
                result = await ollama_json(
                    jid,
                    client,
                    "Bạn là art director. Chỉ trả JSON và tuân thủ chính xác các ID được giao.",
                    f"""Viết chi tiết ĐÚNG {len(ids)} cảnh: {", ".join(ids)}. JSON: {{"scenes":[{{"id":"...","title":"...","story_beat":"...","characters":["char_01"],"setting":"...","time_weather":"...","action_expression":"...","composition":"...","lighting_color":"...","continuity":"...","prompt":"prompt đầy đủ bằng tiếng Anh, tự chứa toàn bộ appearance/wardrobe/signature của nhân vật","negative_prompt":"text, watermark, sai nhận dạng..."}}]}}. Không tạo ID ngoài danh sách. Phong cách: {req.style}; tỉ lệ: {req.aspect_ratio}.\nCHARACTER BIBLE:\n{bible_compact}\nOUTLINE BATCH:\n{json.dumps(batch_outline,ensure_ascii=False)}""",
                    "batch",
                    f"Viết cảnh {index*5+1}–{min((index+1)*5,req.image_count)}…",
                    p0,
                    p1,
                    started,
                )
                for scene in result.get("scenes", []):
                    if scene.get("id") in ids:
                        detailed[scene["id"]] = scene
            missing = [x for x in expected if x not in detailed]
            if missing:
                update(
                    jid,
                    stage="repairing",
                    message=f"Đang bổ sung {len(missing)} cảnh còn thiếu…",
                    progress=82,
                )
                repair_outline = [by_id[x] for x in missing]
                repair = await ollama_json(
                    jid,
                    client,
                    "Bạn sửa storyboard thiếu. Chỉ trả JSON.",
                    "Tạo CHÍNH XÁC các scene ID còn thiếu, đầy đủ các trường title, story_beat, characters, setting, time_weather, action_expression, composition, lighting_color, continuity, prompt, negative_prompt. IDs: "
                    + json.dumps(missing)
                    + "\nCHARACTERS:"
                    + bible_compact
                    + "\nOUTLINE:"
                    + json.dumps(repair_outline, ensure_ascii=False),
                    "repairing",
                    f"Bổ sung {len(missing)} cảnh thiếu…",
                    82,
                    94,
                    started,
                )
                for scene in repair.get("scenes", []):
                    if scene.get("id") in missing:
                        detailed[scene["id"]] = scene
            missing = [x for x in expected if x not in detailed]
            if missing:
                raise ValueError(
                    f'Sau khi sửa vẫn thiếu {len(missing)} cảnh: {", ".join(missing)}'
                )
            update(
                jid,
                stage="validating",
                message="Kiểm tra thứ tự và tính nhất quán…",
                progress=95,
            )
            scenes = [detailed[x] for x in expected]
            pid = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
            project = {
                "id": pid,
                "title": req.title,
                "source_story": req.story,
                "style": req.style,
                "aspect_ratio": req.aspect_ratio,
                "summary": bible.get("summary", ""),
                "visual_direction": bible.get("visual_direction", ""),
                "characters": bible["characters"],
                "scenes": scenes,
            }
            folder = safe(pid)
            folder.mkdir()
            (folder / "project.json").write_text(
                json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            update(
                jid,
                status="completed",
                stage="completed",
                message=f"Hoàn tất {len(scenes)}/{req.image_count} cảnh",
                progress=100,
                result=project,
                elapsed=round(time.time() - started, 1),
            )
    except Exception as exc:
        stage = JOBS[jid].get("stage", "unknown")
        n = int(JOBS[jid].get("generated_chars", 0))
        msg, tech, hint = explain(exc, stage, n)
        update(
            jid,
            status="failed",
            stage="failed",
            message=msg,
            error=msg,
            error_type=type(exc).__name__,
            technical_detail=tech,
            suggestion=hint,
            failed_stage=stage,
            incident_id=uuid.uuid4().hex[:8].upper(),
            elapsed=round(time.time() - started, 1),
        )


async def run_job_v2(jid: str, req: AnalyzeRequest):
    started = time.time()
    state = load_analysis_state(jid)
    warnings: list[str] = list(state.get("warnings", []))
    state.setdefault("request", req.model_dump())
    state["resume_count"] = int(state.get("resume_count", 0))
    state["last_started_at"] = started
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
            parameter = ""
            try:
                tags = (
                    (await client.get(f"{OLLAMA_URL}/api/tags"))
                    .json()
                    .get("models", [])
                )
                item = next((x for x in tags if x.get("name") == OLLAMA_MODEL), {})
                parameter = item.get("details", {}).get("parameter_size", "")
            except Exception:
                pass
            profile = model_profile(OLLAMA_MODEL, parameter)
            update(
                jid,
                model_profile=profile,
                pipeline_config={
                    "outline_batch": profile["outline_batch"],
                    "detail_batch": profile["detail_batch"],
                    "prompt_batch": profile["prompt_batch"],
                    "thinking": False,
                },
                message=f"Đã chọn pipeline {profile['name']} cho model {parameter or OLLAMA_MODEL}",
            )
            bible = state.get("bible")
            if not bible:
                bible = await ollama_json(
                    jid,
                    client,
                    "Bạn là chuyên gia thiết kế nhân vật. Chỉ trả JSON.",
                    f"""Đọc truyện và tạo character bible. JSON: {{"summary":"...","visual_direction":"...","characters":[{{"id":"char_01","name":"...","role":"...","age":"...","gender":"...","appearance":"mô tả nhận dạng bất biến rất chi tiết","wardrobe":"trang phục cố định","signature":"đặc điểm độc nhất","negative":"sai lệch cần tránh"}}]}}. Không tạo scenes.\nPHONG CÁCH: {req.style}\nTRUYỆN:\n{req.story}""",
                    "characters",
                    "Phân tích nhân vật và khóa nhận dạng…",
                    5,
                    20,
                    started,
                )
                state.update(
                    phase="characters_completed", bible=bible, warnings=warnings
                )
                save_analysis_state(jid, state)
            else:
                update(
                    jid,
                    stage="characters",
                    message="Đã khôi phục character bible từ checkpoint",
                    progress=20,
                    resumed=True,
                )
            characters = bible.get("characters", [])
            if not characters:
                raise ValueError("Không tạo được character bible")
            expected = [f"scene_{i:03d}" for i in range(1, req.image_count + 1)]
            segments = split_story(req.story, req.image_count)
            outlines: dict[str, dict] = dict(state.get("outlines", {}))
            outline_size = int(profile["outline_batch"])
            outline_groups = [
                expected[i : i + outline_size]
                for i in range(0, len(expected), outline_size)
            ]
            for gi, ids in enumerate(outline_groups):
                pending_ids = [sid for sid in ids if sid not in outlines]
                if not pending_ids:
                    continue
                sources = [
                    {"id": sid, "source_excerpt": segments[int(sid.split("_")[-1]) - 1]}
                    for sid in pending_ids
                ]
                p0 = 20 + round(15 * gi / len(outline_groups))
                p1 = 20 + round(15 * (gi + 1) / len(outline_groups))
                try:
                    result = await ollama_json(
                        jid,
                        client,
                        "Bạn lập outline storyboard theo đúng các đoạn nguồn. Chỉ trả JSON.",
                        f"""Tạo đúng {len(pending_ids)} outline cho ID {", ".join(pending_ids)}. Mỗi ID tương ứng chính xác source_excerpt cùng vị trí. JSON: {{"scenes":[{{"id":"scene_001","title":"...","story_beat":"...","characters":["char_01"],"setting":"...","time_weather":"...","continuity":"..."}}]}}. Không thêm ID. Nhân vật: {json.dumps([{'id':c.get('id'),'name':c.get('name')} for c in characters],ensure_ascii=False)}\nNGUỒN:\n{json.dumps(sources,ensure_ascii=False)}""",
                        "outline",
                        f"Lập outline {pending_ids[0]}–{pending_ids[-1]}…",
                        p0,
                        p1,
                        started,
                    )
                    for scene in result.get("scenes", []):
                        if scene.get("id") in pending_ids:
                            outlines[scene["id"]] = scene
                except (ValueError, json.JSONDecodeError) as exc:
                    warnings.append(
                        f"Outline {ids[0]}–{ids[-1]} lỗi: {type(exc).__name__}"
                    )
                for sid in ids:
                    if sid not in outlines:
                        outlines[sid] = fallback_outline(
                            sid, segments[int(sid.split("_")[-1]) - 1], characters
                        )
                        warnings.append(f"{sid}: dùng outline fallback")
                state.update(
                    phase="outline", bible=bible, outlines=outlines, warnings=warnings
                )
                save_analysis_state(jid, state)
            for sid in expected:
                if sid not in outlines:
                    outlines[sid] = fallback_outline(
                        sid, segments[int(sid.split("_")[-1]) - 1], characters
                    )
                    warnings.append(
                        f"{sid}: khôi phục outline thiếu trước detail batch"
                    )
            detail_size = int(profile["detail_batch"])
            detail_groups = [
                expected[i : i + detail_size]
                for i in range(0, len(expected), detail_size)
            ]
            detailed: dict[str, dict] = dict(state.get("detailed", {}))
            bible_text = json.dumps(characters, ensure_ascii=False)
            for gi, ids in enumerate(detail_groups):
                p0 = 35 + round(55 * gi / len(detail_groups))
                p1 = 35 + round(55 * (gi + 1) / len(detail_groups))
                pending = {sid for sid in ids if sid not in detailed}
                if not pending:
                    continue
                for attempt in range(int(profile["retries"]) + 1):
                    if not pending:
                        break
                    wanted = [outlines[x] for x in ids if x in pending]
                    try:
                        result = await ollama_json(
                            jid,
                            client,
                            "Bạn là art director điện ảnh. Chỉ trả JSON và chỉ tạo các ID được giao.",
                            f"""Viết đầy đủ đúng {len(wanted)} cảnh theo outline. Schema mỗi cảnh: id,title,story_beat,characters,setting,time_weather,action_expression,spatial_layout,composition,lighting_color,continuity,must_include,prompt,negative_prompt. Prompt viết bằng tiếng Việt tự nhiên, chi tiết, không cắt bớt ngữ cảnh; mô tả rõ ai ở bên trái/phải/tiền cảnh/hậu cảnh, hành động, cảm xúc, môi trường, góc máy, tiêu cự, ánh sáng và chất liệu hình ảnh. must_include là danh sách 3-8 chi tiết bắt buộc phải xuất hiện. Không yêu cầu vẽ bất kỳ chữ, câu, con số, logo hoặc ký tự nào; nếu truyện nhắc tới nội dung chữ trên bảng/giấy/màn hình thì chỉ mô tả chính đồ vật với bề mặt trống. Giữ nguyên character ID hợp lệ, không tự đổi nhân vật. IDs: {json.dumps(sorted(pending))}. Phong cách: {req.style}; tỉ lệ: {req.aspect_ratio}.\nCHARACTER BIBLE:\n{bible_text}\nOUTLINE:\n{json.dumps(wanted,ensure_ascii=False)}""",
                            "batch",
                            f"Viết {ids[0]}–{ids[-1]} · lần {attempt+1}…",
                            p0,
                            p1,
                            started,
                        )
                        for scene in result.get("scenes", []):
                            sid = scene.get("id")
                            if sid in pending:
                                scene = repair_data(scene)
                                scene["characters"] = clean_character_refs(
                                    scene, characters
                                )
                                scene["prompt"] = build_scene_prompt(
                                    scene, characters, req.style, req.aspect_ratio
                                )
                                scene["negative_prompt"] = (
                                    "chữ, phụ đề, watermark, logo, sai nhân vật, sai trang phục, nhân vật trùng lặp, thừa tay chân, khuôn mặt biến dạng"
                                )
                                scene["prompt_language"] = "vi"
                                detailed[sid] = scene
                                pending.remove(sid)
                    except (ValueError, json.JSONDecodeError) as exc:
                        warnings.append(
                            f"Batch {ids[0]} lần {attempt+1}: {type(exc).__name__}"
                        )
                for sid in pending:
                    detailed[sid] = fallback_detail(
                        outlines[sid], characters, req.style
                    )
                    detailed[sid]["prompt"] = build_scene_prompt(
                        detailed[sid], characters, req.style, req.aspect_ratio
                    )
                    warnings.append(f"{sid}: dùng chi tiết fallback sau retry")
                state.update(
                    phase="details",
                    bible=bible,
                    outlines=outlines,
                    detailed=detailed,
                    warnings=warnings,
                )
                save_analysis_state(jid, state)
            for sid in expected:
                if sid not in detailed:
                    detailed[sid] = fallback_detail(
                        outlines[sid], characters, req.style
                    )
                    warnings.append(
                        f"{sid}: khôi phục detail thiếu trước prompt editing"
                    )
            bible = repair_data(bible)
            characters = bible.get("characters", characters)
            detailed = {sid: repair_data(scene) for sid, scene in detailed.items()}
            for sid in expected:
                detailed[sid]["prompt"] = build_scene_prompt(
                    detailed[sid], characters, req.style, req.aspect_ratio
                )
                detailed[sid]["prompt_language"] = "vi"
            state.update(
                phase="prompts_ready",
                bible=bible,
                outlines=outlines,
                detailed=detailed,
                warnings=warnings,
            )
            save_analysis_state(jid, state)
            update(
                jid,
                stage="validating",
                message=f"Kiểm tra đủ {req.image_count} cảnh và prompt tiếng Việt…",
                progress=97,
            )
            scenes = [detailed[x] for x in expected]
            pid = state.get("project_id") or project_slug(req.title)
            project = {
                "id": pid,
                "title": req.title,
                "source_story": req.story,
                "style": req.style,
                "aspect_ratio": req.aspect_ratio,
                "summary": bible.get("summary", ""),
                "visual_direction": bible.get("visual_direction", ""),
                "characters": characters,
                "scenes": scenes,
                "generation_meta": {
                    "model": OLLAMA_MODEL,
                    "profile": profile,
                    "warnings": warnings,
                },
            }
            folder = safe(pid)
            folder.mkdir(exist_ok=True)
            (folder / "project.json").write_text(
                json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            state.update(
                phase="completed",
                bible=bible,
                outlines=outlines,
                detailed=detailed,
                warnings=warnings,
                project_id=pid,
            )
            await release_ollama(client)
            update(
                jid,
                status="completed",
                stage="completed",
                message=f"Hoàn tất {len(scenes)}/{req.image_count} cảnh · đã giải phóng Ollama khỏi GPU",
                progress=100,
                result=project,
                warnings=warnings,
                elapsed=round(time.time() - started, 1),
                resumable=False,
            )
            save_analysis_state(jid, state)
    except asyncio.CancelledError:
        state["phase"] = JOBS[jid].get("stage", state.get("phase", "cancelled"))
        state["cancelled_at"] = time.time()
        update(
            jid,
            status="cancelled",
            stage="cancelled",
            message="Đã dừng theo yêu cầu · checkpoint được giữ lại",
            elapsed=round(time.time() - started, 1),
            resumable=True,
        )
        save_analysis_state(jid, state)
        raise
    except Exception as exc:
        stage = JOBS[jid].get("stage", "unknown")
        n = int(JOBS[jid].get("generated_chars", 0))
        msg, tech, hint = explain(exc, stage, n)
        update(
            jid,
            status="failed",
            stage="failed",
            message=msg,
            error=msg,
            error_type=type(exc).__name__,
            technical_detail=tech,
            suggestion=hint,
            failed_stage=stage,
            incident_id=uuid.uuid4().hex[:8].upper(),
            elapsed=round(time.time() - started, 1),
            resumable=bool(load_analysis_state(jid)),
        )
        save_analysis_state(jid, state)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.on_event("startup")
async def restore_analysis_jobs():
    for path in ANALYSIS_JOB_DIR.glob("*.job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            jid = str(job.get("id", ""))
            if not re.fullmatch(r"[a-f0-9]{32}", jid):
                continue
            JOBS[jid] = job
            if job.get("status") not in ("queued", "running"):
                continue
            state = load_analysis_state(jid)
            request_data = state.get("request")
            if not request_data:
                continue
            req = AnalyzeRequest.model_validate(request_data)
            job.update(
                status="queued",
                stage=state.get("phase", "queued"),
                message="Backend vừa khởi động lại · đang tiếp tục từ checkpoint…",
                resumed=True,
                resumable=True,
            )
            persist_analysis_job(jid)
            save_analysis_state(jid, state)
            launch_analysis_task(jid, req)
        except Exception:
            continue


@app.get("/api/health")
async def health():
    out = {
        "ollama_running": False,
        "ollama": False,
        "ollama_model": OLLAMA_MODEL,
        "comfyui": False,
        "comfyui_url": COMFYUI_URL,
        "image_backend": "qwen-image+qwen-image-edit-2509",
        "qwen_image_model": QWEN_IMAGE_MODEL,
        "qwen_image_edit_model": QWEN_IMAGE_EDIT_MODEL,
        "qwen_image_encoder": QWEN_IMAGE_ENCODER,
        "qwen_image_vae": QWEN_IMAGE_VAE,
        "qwen_models_ready": False,
        "qwen_edit_ready": False,
        "installed_models": [],
        "render_concurrency": COMFYUI_CONCURRENCY,
        "workflow_mode": "custom" if COMFYUI_WORKFLOW else "qwen-built-in",
        "queue_running": 0,
        "queue_pending": 0,
        "comfyui_device": None,
        "vram_total_gb": None,
    }
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            tags = (await client.get(f"{OLLAMA_URL}/api/tags")).json().get("models", [])
            out["ollama_running"] = True
            out["installed_models"] = [x.get("name") for x in tags]
            out["ollama"] = OLLAMA_MODEL in out["installed_models"]
        except Exception:
            pass
        try:
            info = (await client.get(f"{COMFYUI_URL}/object_info")).json()
            out["comfyui"] = all(
                node in info
                for node in (
                    "UNETLoader",
                    "CLIPLoader",
                    "VAELoader",
                    "ModelSamplingAuraFlow",
                    "EmptySD3LatentImage",
                )
            )
            unets = (
                info.get("UNETLoader", {})
                .get("input", {})
                .get("required", {})
                .get("unet_name", [[]])[0]
            )
            clips = (
                info.get("CLIPLoader", {})
                .get("input", {})
                .get("required", {})
                .get("clip_name", [[]])[0]
            )
            vaes = (
                info.get("VAELoader", {})
                .get("input", {})
                .get("required", {})
                .get("vae_name", [[]])[0]
            )
            out["qwen_models_ready"] = (
                QWEN_IMAGE_MODEL in unets
                and QWEN_IMAGE_ENCODER in clips
                and QWEN_IMAGE_VAE in vaes
            )
            out["qwen_edit_ready"] = (
                QWEN_IMAGE_EDIT_MODEL in unets and "TextEncodeQwenImageEditPlus" in info
            )
            try:
                queue = (await client.get(f"{COMFYUI_URL}/queue")).json()
                out["queue_running"] = len(queue.get("queue_running", []))
                out["queue_pending"] = len(queue.get("queue_pending", []))
                stats = (await client.get(f"{COMFYUI_URL}/system_stats")).json()
                device = (stats.get("devices") or [{}])[0]
                out["comfyui_device"] = device.get("name") or device.get("type")
                vram = device.get("vram_total")
                out["vram_total_gb"] = (
                    round(vram / 1073741824, 1)
                    if isinstance(vram, (int, float))
                    else None
                )
            except Exception:
                pass
        except Exception:
            pass
    return out


@app.post("/api/analyze/jobs", status_code=202)
async def start(req: AnalyzeRequest):
    jid = uuid.uuid4().hex
    pid = project_slug(req.title)
    JOBS[jid] = {
        "id": jid,
        "project_id": pid,
        "project_name": req.title,
        "status": "queued",
        "stage": "queued",
        "message": "Đang xếp hàng…",
        "progress": 1,
        "created_at": time.time(),
        "generated_chars": 0,
        "resumable": True,
    }
    save_analysis_state(
        jid,
        {
            "phase": "queued",
            "project_id": pid,
            "request": req.model_dump(),
            "warnings": [],
        },
    )
    launch_analysis_task(jid, req)
    return {"job_id": jid, "project_id": pid, "project_name": req.title}


@app.get("/api/analyze/jobs")
async def list_analysis_jobs():
    for path in ANALYSIS_JOB_DIR.glob("*.job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            jid = str(job.get("id", ""))
            if jid and jid not in JOBS:
                JOBS[jid] = job
        except Exception:
            continue
    fields = (
        "id",
        "project_id",
        "project_name",
        "status",
        "stage",
        "message",
        "progress",
        "created_at",
        "updated_at",
        "elapsed",
        "checkpoint",
        "resumable",
        "incident_id",
    )
    items = [
        {key: job.get(key) for key in fields if key in job} for job in JOBS.values()
    ]
    items.sort(
        key=lambda item: item.get("updated_at") or item.get("created_at") or 0,
        reverse=True,
    )
    return {"jobs": items[:50]}


@app.post("/api/analyze")
async def legacy_analyze(req: AnalyzeRequest):
    jid = uuid.uuid4().hex
    pid = project_slug(req.title)
    JOBS[jid] = {
        "id": jid,
        "project_id": pid,
        "project_name": req.title,
        "status": "queued",
        "stage": "queued",
        "message": "Đang xếp hàng…",
        "progress": 1,
        "created_at": time.time(),
        "generated_chars": 0,
    }
    save_analysis_state(
        jid,
        {
            "phase": "queued",
            "project_id": pid,
            "request": req.model_dump(),
            "warnings": [],
        },
    )
    await run_job_v2(jid, req)
    job = JOBS[jid]
    if job["status"] == "completed":
        return job["result"]
    detail = f"[{job.get('incident_id','NO-ID')}] {job.get('message','Phân tích thất bại')} | {job.get('technical_detail','')} | Gợi ý: {job.get('suggestion','')}"
    raise HTTPException(502, detail=detail)


@app.get("/api/analyze/jobs/{jid}")
async def status(jid: str):
    if jid not in JOBS:
        path = analysis_file(jid)
        if path.exists():
            JOBS[jid] = json.loads(path.read_text(encoding="utf-8"))
        else:
            raise HTTPException(404, "Không tìm thấy tiến trình")
    job = JOBS[jid]
    if job.get("status") == "completed" and job.get("project_id"):
        project_path = safe(str(job["project_id"])) / "project.json"
        if project_path.exists():
            job["result"] = json.loads(project_path.read_text(encoding="utf-8"))
    return job


@app.get("/api/analyze/jobs/{jid}/input")
async def analysis_input(jid: str):
    state = load_analysis_state(jid)
    request_data = state.get("request")
    if not request_data:
        job_path = analysis_file(jid)
        if not job_path.exists():
            raise HTTPException(404, "Không tìm thấy tiến trình")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        pid = job.get("project_id")
        input_path = safe(str(pid)) / "analysis-input.json" if pid else None
        if input_path and input_path.exists():
            request_data = json.loads(input_path.read_text(encoding="utf-8"))
    if not request_data:
        raise HTTPException(404, "Dự án chưa có dữ liệu đầu vào đã lưu")
    return {"job_id": jid, "input": request_data}


@app.post("/api/analyze/jobs/{jid}/cancel")
async def cancel_analysis(jid: str):
    if jid not in JOBS:
        path = analysis_file(jid)
        if not path.exists():
            raise HTTPException(404, "Không tìm thấy tiến trình")
        JOBS[jid] = json.loads(path.read_text(encoding="utf-8"))
    job = JOBS[jid]
    if job.get("status") not in ("queued", "running"):
        return {"job_id": jid, "status": job.get("status", "stopped")}
    task = ANALYSIS_TASKS.get(jid)
    if task and not task.done():
        job.update(message="Đang dừng yêu cầu hiện tại…", updated_at=time.time())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    else:
        update(
            jid,
            status="cancelled",
            stage="cancelled",
            message="Đã dừng theo yêu cầu · checkpoint được giữ lại",
            resumable=True,
        )
        state = load_analysis_state(jid)
        if state:
            state["cancelled_at"] = time.time()
            save_analysis_state(jid, state)
    return {"job_id": jid, "status": "cancelled", "resumable": True}


@app.post("/api/analyze/jobs/{jid}/retry", status_code=202)
async def retry_analysis(jid: str):
    restored_from_disk = False
    if jid not in JOBS:
        path = analysis_file(jid)
        if not path.exists():
            raise HTTPException(404, "Không tìm thấy tiến trình")
        JOBS[jid] = json.loads(path.read_text(encoding="utf-8"))
        restored_from_disk = True
    job = JOBS[jid]
    if job.get("status") in ("queued", "running") and not restored_from_disk:
        raise HTTPException(409, "Tiến trình vẫn đang chạy")
    if job.get("status") == "completed":
        return {"job_id": jid, "status": "completed"}
    state = load_analysis_state(jid)
    if not state.get("request"):
        raise HTTPException(409, "Checkpoint không có dữ liệu đầu vào để tiếp tục")
    req = AnalyzeRequest.model_validate(state["request"])
    state["resume_count"] = int(state.get("resume_count", 0)) + 1
    job.update(
        status="queued",
        stage=state.get("phase", "queued"),
        message="Đang tiếp tục từ checkpoint gần nhất…",
        error=None,
        technical_detail=None,
        progress=max(1, int(job.get("progress", 1))),
        resumed=True,
        resumable=True,
    )
    save_analysis_state(jid, state)
    launch_analysis_task(jid, req)
    return {"job_id": jid, "checkpoint": job.get("checkpoint")}


@app.post("/api/projects/{pid}")
async def save(pid: str, body: ProjectPayload):
    ensure_render_resources_available(pid, ["project:*"])
    folder = safe(pid)
    folder.mkdir(exist_ok=True)
    body.project["id"] = pid
    async with project_lock(pid):
        write_json_atomic(folder / "project.json", body.project)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    folder = safe(pid)
    project_existed = folder.exists()
    related_jobs: set[str] = set()
    for path in ANALYSIS_JOB_DIR.glob("*.job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            if str(job.get("project_id")) == pid:
                related_jobs.add(str(job.get("id")))
        except Exception:
            continue
    for jid, job in list(JOBS.items()):
        if str(job.get("project_id")) == pid:
            related_jobs.add(jid)
    for jid in related_jobs:
        task = ANALYSIS_TASKS.get(jid)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        JOBS.pop(jid, None)
        for path in ANALYSIS_JOB_DIR.glob(f"{jid}.*.json"):
            path.unlink(missing_ok=True)
    related_render_jobs: set[str] = set()
    for jid, render_job in list(RENDER_JOBS.items()):
        if str(render_job.get("project_id")) == pid:
            related_render_jobs.add(jid)
    for path in RENDER_JOB_DIR.glob("*.json"):
        try:
            render_job = json.loads(path.read_text(encoding="utf-8"))
            if str(render_job.get("project_id")) == pid:
                related_render_jobs.add(str(render_job.get("id") or path.stem))
        except Exception:
            continue
    for jid in related_render_jobs:
        task = RENDER_TASKS.get(jid)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        RENDER_JOBS.pop(jid, None)
        render_job_file(jid).unlink(missing_ok=True)
    if folder.exists():
        resolved = folder.resolve()
        if resolved.parent != PROJECTS.resolve():
            raise HTTPException(400, "Đường dẫn dự án không an toàn")
        shutil.rmtree(resolved)
    if not related_jobs and not project_existed:
        raise HTTPException(404, "Không tìm thấy dự án")
    return {
        "ok": True,
        "project_id": pid,
        "deleted_jobs": len(related_jobs) + len(related_render_jobs),
    }


@app.get("/api/projects/{pid}")
async def get_project(pid: str):
    path = safe(pid) / "project.json"
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy dự án")
    async with project_lock(pid):
        project = json.loads(path.read_text(encoding="utf-8"))
        if normalize_render_fields(project):
            write_json_atomic(path, project)
    return project


def suppress_visible_text(value: Any) -> str:
    text = repair_text(str(value or ""))
    text = re.sub(
        r'["“”‘’\']([^"“”‘’\']{1,120})["“”‘’\']',
        "một bề mặt trống không có ký tự",
        text,
    )
    text = re.sub(
        r"(?i)(dòng chữ|chữ viết|nội dung chữ|ghi chữ|hiển thị chữ|biển hiệu ghi|màn hình ghi|text|caption)\s*[:：-]?\s*[^,.;\n]{0,120}",
        "bề mặt trống không chữ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def compile_scene_prompt(project: dict, scene: dict) -> tuple[str, str]:
    character_map = {
        str(character.get("id")): character
        for character in project.get("characters", [])
    }
    visible = []
    for cid in scene.get("characters") or []:
        character = character_map.get(str(cid))
        if character:
            visible.append(
                f"IDENTITY LOCK {cid} — {character.get('name','Nhân vật')}: {suppress_visible_text(character.get('appearance',''))}; trang phục cố định: {suppress_visible_text(character.get('wardrobe',''))}; dấu hiệu nhận dạng cố định: {suppress_visible_text(character.get('signature',''))}. Không thay đổi khuôn mặt, tóc, tuổi, vóc dáng hay trang phục"
            )
    must_include = scene.get("must_include") or []
    if isinstance(must_include, str):
        must_include = [must_include]
    sections = [
        "CẤM TUYỆT ĐỐI MỌI CHỮ VIẾT VÀ KÝ TỰ NHÌN THẤY: không chữ Latin, không chữ Thái, không chữ Trung Quốc, không chữ Nhật, không số, không logo, không phụ đề; mọi bảng hiệu, giấy và màn hình đều để trống.",
        f"STYLE LOCK — áp dụng nguyên vẹn cho mọi ảnh trong dự án: {project.get('style','')}. Giữ cùng kỹ thuật tạo hình, mức độ chân thực, xử lý da, bảng màu, tương phản, film grain và ngôn ngữ điện ảnh; không đổi sang thể loại hình ảnh khác.",
        f"Tạo một khung hình kể chuyện điện ảnh duy nhất, tỉ lệ {project.get('aspect_ratio','16:9')}.",
        f"NỘI DUNG BẮT BUỘC: {suppress_visible_text(scene.get('story_beat',''))}",
        f"BỐI CẢNH: {suppress_visible_text(scene.get('setting',''))}; {suppress_visible_text(scene.get('time_weather',''))}",
        f"NHÂN VẬT XUẤT HIỆN: {' | '.join(visible) if visible else 'không có nhân vật chính'}",
        f"HÀNH ĐỘNG VÀ CẢM XÚC: {suppress_visible_text(scene.get('action_expression',''))}",
        f"VỊ TRÍ KHÔNG GIAN: {suppress_visible_text(scene.get('spatial_layout',''))}",
        f"MÁY QUAY VÀ BỐ CỤC: {suppress_visible_text(scene.get('composition',''))}",
        f"ÁNH SÁNG VÀ MÀU: {suppress_visible_text(scene.get('lighting_color',''))}",
        f"TÍNH LIÊN TỤC: {suppress_visible_text(scene.get('continuity',''))}",
        f"CHI TIẾT PHẢI CÓ: {'; '.join(suppress_visible_text(item) for item in must_include)}",
        f"MÔ TẢ BỔ SUNG: {suppress_visible_text(scene.get('prompt',''))}",
        "Giữ đúng số lượng nhân vật, đúng vị trí và quan hệ không gian. Không tự thêm sinh vật, địa điểm hay đồ vật ngoài mô tả. Tất cả bề mặt trong ảnh hoàn toàn không có chữ hoặc ký tự.",
    ]
    positive = "\n".join(
        repair_text(str(section)).strip()
        for section in sections
        if str(section).strip()
    )
    negative = ", ".join(
        filter(
            None,
            [
                str(scene.get("negative_prompt", "")).strip(),
                "chữ, phụ đề, watermark, logo, sai số lượng nhân vật, nhân vật trùng lặp, thừa tay chân, khuôn mặt biến dạng, sai bối cảnh",
            ],
        )
    )
    return positive, negative


def qwen_workflow(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    steps: int,
    seed: int,
    prefix: str,
) -> dict:
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": QWEN_IMAGE_MODEL, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": QWEN_IMAGE_ENCODER,
                "type": "qwen_image",
                "device": "default",
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_IMAGE_VAE}},
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": prompt},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": negative},
        },
        "6": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["1", 0], "shift": 3.0},
        },
        "7": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": max(4, min(steps, 20)),
                "cfg": 1.0,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["6", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["7", 0],
            },
        },
        "9": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["8", 0], "vae": ["3", 0]},
        },
        "10": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["9", 0]},
        },
    }


def qwen_edit_workflow(
    prompt: str,
    negative: str,
    reference_names: list[str],
    seed: int,
    prefix: str,
    width: int,
    height: int,
    steps: int = 15,
) -> dict:
    if not reference_names:
        raise ValueError("Cảnh chưa có ảnh reference nhân vật")
    workflow = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": QWEN_IMAGE_EDIT_MODEL, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": QWEN_IMAGE_ENCODER,
                "type": "qwen_image",
                "device": "default",
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_IMAGE_VAE}},
        "4": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["1", 0], "shift": 3.0},
        },
    }
    image_links = []
    for index, name in enumerate(reference_names[:3], start=1):
        node = str(10 + index)
        workflow[node] = {"class_type": "LoadImage", "inputs": {"image": name}}
        image_links.append([node, 0])
    positive = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "image1": image_links[0],
        "prompt": prompt,
    }
    negative_inputs = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "image1": image_links[0],
        "prompt": negative,
    }
    if len(image_links) > 1:
        positive["image2"] = image_links[1]
        negative_inputs["image2"] = image_links[1]
    if len(image_links) > 2:
        positive["image3"] = image_links[2]
        negative_inputs["image3"] = image_links[2]
    workflow.update(
        {
            "20": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": positive},
            "21": {
                "class_type": "TextEncodeQwenImageEditPlus",
                "inputs": negative_inputs,
            },
            "23": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "24": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": max(4, min(steps, 20)),
                    "cfg": 1.0,
                    "sampler_name": "res_multistep",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["20", 0],
                    "negative": ["21", 0],
                    "latent_image": ["23", 0],
                },
            },
            "25": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["24", 0], "vae": ["3", 0]},
            },
            "26": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": prefix, "images": ["25", 0]},
            },
        }
    )
    return workflow


def workflow_for(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    steps: int,
    seed: int,
    prefix: str,
) -> dict:
    values = {
        "{{PROMPT}}": prompt,
        "{{NEGATIVE_PROMPT}}": negative,
        "{{WIDTH}}": width,
        "{{HEIGHT}}": height,
        "{{STEPS}}": steps,
        "{{SEED}}": seed,
        "{{QWEN_IMAGE_MODEL}}": QWEN_IMAGE_MODEL,
        "{{QWEN_IMAGE_ENCODER}}": QWEN_IMAGE_ENCODER,
        "{{QWEN_IMAGE_VAE}}": QWEN_IMAGE_VAE,
        "{{FILENAME_PREFIX}}": prefix,
    }
    if not COMFYUI_WORKFLOW:
        return qwen_workflow(prompt, negative, width, height, steps, seed, prefix)
    path = Path(COMFYUI_WORKFLOW)
    path = path if path.is_absolute() else ROOT / path
    if not path.exists():
        raise RuntimeError(f"Khong tim thay workflow: {path}")

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        if isinstance(value, str):
            if value in values:
                return values[value]
            for key, replacement in values.items():
                value = value.replace(key, str(replacement))
        return value

    return replace(json.loads(path.read_text(encoding="utf-8")))


async def execute_comfy(
    client: httpx.AsyncClient, workflow: dict, client_id: str
) -> tuple[bytes, dict]:
    queued_at = time.time()
    queued = await client.post(
        f"{COMFYUI_URL}/prompt", json={"prompt": workflow, "client_id": client_id}
    )
    queued.raise_for_status()
    payload = queued.json()
    if payload.get("node_errors"):
        raise RuntimeError(
            f"Workflow ComfyUI không hợp lệ: {json.dumps(payload['node_errors'],ensure_ascii=False)[:1500]}"
        )
    prompt_id = payload["prompt_id"]
    deadline = time.time() + COMFYUI_TIMEOUT
    started_execution = None
    while time.time() < deadline:
        history = (await client.get(f"{COMFYUI_URL}/history/{prompt_id}")).json()
        if prompt_id in history:
            entry = history[prompt_id]
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(
                    f"ComfyUI render lỗi: {json.dumps(entry.get('status'),ensure_ascii=False)[:1500]}"
                )
            for output in entry.get("outputs", {}).values():
                if output.get("images"):
                    image = output["images"][0]
                    result = await client.get(
                        f"{COMFYUI_URL}/view",
                        params={
                            "filename": image["filename"],
                            "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output"),
                        },
                    )
                    result.raise_for_status()
                    finished = time.time()
                    return result.content, {
                        "prompt_id": prompt_id,
                        "total_seconds": round(finished - queued_at, 2),
                        "queue_seconds": round(
                            (started_execution or queued_at) - queued_at, 2
                        ),
                        "execution_seconds": round(
                            finished - (started_execution or queued_at), 2
                        ),
                    }
        else:
            queue = (await client.get(f"{COMFYUI_URL}/queue")).json()
            if any(
                item[1] == prompt_id
                for item in queue.get("queue_running", [])
                if len(item) > 1
            ):
                started_execution = started_execution or time.time()
        await asyncio.sleep(COMFYUI_POLL)
    raise TimeoutError(f"ComfyUI render quá {COMFYUI_TIMEOUT} giây")


async def upload_reference(client: httpx.AsyncClient, path: Path) -> str:
    with path.open("rb") as handle:
        response = await client.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (path.name, handle, "image/png")},
            data={"type": "input", "overwrite": "true"},
        )
    response.raise_for_status()
    payload = response.json()
    return "/".join(
        filter(None, [payload.get("subfolder", ""), payload.get("name", path.name)])
    )


async def render_comfy(
    scene: dict, project: dict, width: int, height: int, steps: int
) -> tuple[bytes, int, str, str, dict]:
    project_id = project["id"]
    seed = int.from_bytes(project_id.encode(), "little") % 2147483647
    client_id = f"storyframe-{uuid.uuid4().hex}"
    prefix = f'StoryFrame/{project_id}/{scene["id"]}'
    positive, negative = compile_scene_prompt(project, scene)
    async with COMFYUI_GATE, httpx.AsyncClient(
        timeout=httpx.Timeout(COMFYUI_TIMEOUT, connect=8)
    ) as client:
        character_map = {
            str(character.get("id")): character
            for character in project.get("characters", [])
        }
        reference_paths = []
        for cid in (scene.get("characters") or [])[:3]:
            character = character_map.get(str(cid))
            url = character.get("selected_reference") if character else None
            if not url:
                raise ValueError(f"Nhân vật {cid} chưa chọn ảnh reference")
            relative = (
                str(url).split(f"/projects/{project_id}/", 1)[-1].split("?", 1)[0]
            )
            path = safe(project_id) / relative
            if not path.exists():
                raise ValueError(f"Mất file reference của {cid}: {path.name}")
            reference_paths.append(path)
        if reference_paths:
            names = [await upload_reference(client, path) for path in reference_paths]
            workflow = qwen_edit_workflow(
                positive, negative, names, seed, prefix, width, height, steps
            )
            backend = QWEN_IMAGE_EDIT_MODEL
            profile = "qwen-image-edit-2509"
        else:
            workflow = workflow_for(
                positive, negative, width, height, steps, seed, prefix
            )
            backend = QWEN_IMAGE_MODEL
            profile = "qwen-image"
        data, timing = await execute_comfy(client, workflow, client_id)
        return data, seed, backend, profile, timing


async def render_one(
    project: dict,
    path: Path,
    scene: dict,
    width: int,
    height: int,
    steps: int,
    quality: Literal["draft", "final"] = "final",
) -> dict:
    last: Exception | None = None
    for attempt in range(COMFYUI_RETRIES + 1):
        try:
            data, seed, checkpoint, profile, timing = await render_comfy(
                scene, project, width, height, steps
            )
            break
        except Exception as exc:
            last = exc
            if attempt >= COMFYUI_RETRIES:
                raise
            await asyncio.sleep(min(8, 2**attempt))
    else:
        raise last or RuntimeError("Render failed")
    images = path.parent / "images" / quality
    images.mkdir(parents=True, exist_ok=True)
    (images / f'{scene["id"]}.png').write_bytes(data)
    image_url = f'/projects/{project["id"]}/images/{quality}/{scene["id"]}.png?v={int(time.time())}'
    scene[f"{quality}_image_url"] = image_url
    if quality == "final" or not scene.get("final_image_url"):
        scene["image_url"] = image_url
    render_meta = {
        "backend": "comfyui",
        "checkpoint": checkpoint,
        "profile": profile,
        "seed": seed,
        "steps": steps,
        "timing": timing,
        "workflow": "custom" if COMFYUI_WORKFLOW else "built-in",
        "quality": quality,
    }
    scene.setdefault("renders", {})[quality] = render_meta
    scene["render_meta"] = render_meta
    # Merge only this scene's render fields into the latest project snapshot so a
    # character selection or another completed scene cannot be overwritten.
    async with project_lock(str(project["id"])):
        latest = json.loads(path.read_text(encoding="utf-8"))
        latest_scene = next(
            (item for item in latest.get("scenes", []) if item.get("id") == scene.get("id")),
            None,
        )
        if not latest_scene:
            raise ValueError(f'Mất cảnh {scene.get("id")} khi lưu kết quả render')
        latest_scene[f"{quality}_image_url"] = image_url
        if quality == "final" or not latest_scene.get("final_image_url"):
            latest_scene["image_url"] = image_url
        latest_scene.setdefault("renders", {})[quality] = render_meta
        latest_scene["render_meta"] = render_meta
        write_json_atomic(path, latest)
    return {
        "image_url": image_url,
        "quality": quality,
        "seed": seed,
        "checkpoint": checkpoint,
        "profile": profile,
        "timing": timing,
    }


def character_reference_prompt(project: dict, character: dict) -> tuple[str, str]:
    positive = "\n".join(
        [
            "ẢNH THAM CHIẾU NHÂN VẬT DUY NHẤT, chỉ một người, nền studio xám trung tính hoàn toàn không chữ.",
            f"STYLE LOCK: {project.get('style','')}",
            f"Tên: {character.get('name','')}; tuổi: {character.get('age','')}; giới tính: {character.get('gender','')}",
            f"Ngoại hình bắt buộc: {suppress_visible_text(character.get('appearance',''))}",
            f"Trang phục cố định: {suppress_visible_text(character.get('wardrobe',''))}",
            f"Dấu hiệu nhận dạng: {suppress_visible_text(character.get('signature',''))}",
            "Khung hình từ đầu đến đầu gối, nhìn rõ chính diện và khuôn mặt, biểu cảm trung tính tự nhiên, ánh sáng mềm đều, không đạo cụ che mặt, không chữ, không logo.",
        ]
    )
    return (
        positive,
        "nhiều người, collage, split screen, chữ, ký tự, watermark, logo, che mặt, khuôn mặt biến dạng, thừa tay chân, góc quay quá xa",
    )


async def character_reference_job(jid: str, pid: str, cid: str):
    try:
        folder = safe(pid)
        path = folder / "project.json"
        project = json.loads(path.read_text(encoding="utf-8"))
        character = next(
            (item for item in project.get("characters", []) if item.get("id") == cid),
            None,
        )
        if not character:
            raise ValueError(f"Không tìm thấy nhân vật {cid}")
        target = folder / "character-references" / cid
        target.mkdir(parents=True, exist_ok=True)
        positive, negative = character_reference_prompt(project, character)
        candidates = []
        set_render_job(
            jid,
            status="running",
            kind="character_references",
            project_id=pid,
            character_id=cid,
            total=3,
            completed=0,
            progress=0,
            message=f'Tạo ảnh gốc cho {character.get("name",cid)}',
        )
        async with COMFYUI_GATE, httpx.AsyncClient(
            timeout=httpx.Timeout(COMFYUI_TIMEOUT, connect=8)
        ) as client:
            for index in range(1, 4):
                seed = (
                    int.from_bytes(f"{pid}:{cid}:{index}".encode(), "little")
                    % 2147483647
                )
                prefix = f"StoryFrame/{pid}/references/{cid}_{index}"
                workflow = qwen_workflow(positive, negative, 768, 1024, 8, seed, prefix)
                data, timing = await execute_comfy(
                    client, workflow, f"reference-{uuid.uuid4().hex}"
                )
                filename = f"ref_{index}.png"
                (target / filename).write_bytes(data)
                url = f"/projects/{pid}/character-references/{cid}/{filename}?v={int(time.time())}"
                candidates.append({"url": url, "seed": seed, "timing": timing})
                set_render_job(
                    jid,
                    completed=index,
                    progress=round(index * 100 / 3),
                    message=f'Đã tạo {index}/3 ảnh cho {character.get("name",cid)}',
                )
        async with project_lock(pid):
            latest = json.loads(path.read_text(encoding="utf-8"))
            latest_character = next(
                (
                    item
                    for item in latest.get("characters", [])
                    if item.get("id") == cid
                ),
                None,
            )
            if not latest_character:
                raise ValueError(f"Không tìm thấy nhân vật {cid} khi lưu reference")
            latest_character["reference_candidates"] = candidates
            latest_character.pop("selected_reference", None)
            required = {
                str(ref)
                for scene in latest.get("scenes", [])
                for ref in (scene.get("characters") or [])
            }
            selected = {
                str(item.get("id"))
                for item in latest.get("characters", [])
                if item.get("selected_reference")
            }
            latest["cast_locked"] = required.issubset(selected)
            write_json_atomic(path, latest)
        set_render_job(
            jid,
            status="completed",
            result={"character_id": cid, "candidates": candidates},
            message="Hãy chọn một ảnh để khóa nhân vật",
        )
    except Exception as exc:
        set_render_job(
            jid,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            message="Tạo ảnh nhân vật thất bại",
        )


@app.post("/api/projects/{pid}/characters/{cid}/reference-jobs", status_code=202)
async def start_character_references(pid: str, cid: str):
    folder = safe(pid)
    path = folder / "project.json"
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy dự án")
    resources = [f"character:{cid}"]
    ensure_render_resources_available(pid, resources)
    jid = uuid.uuid4().hex
    RENDER_JOBS[jid] = {
        "id": jid,
        "status": "queued",
        "kind": "character_references",
        "project_id": pid,
        "character_id": cid,
        "request": {"project_id": pid, "character_id": cid},
        "resource_keys": resources,
        "progress": 0,
        "message": "Đang xếp hàng tạo nhân vật",
        "errors": [],
    }
    set_render_job(jid)
    launch_render_task(jid, character_reference_job(jid, pid, cid))
    return {"job_id": jid}


@app.post("/api/projects/{pid}/characters/{cid}/select-reference")
async def select_character_reference(pid: str, cid: str, body: ReferenceSelectRequest):
    folder = safe(pid)
    path = folder / "project.json"
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy dự án")
    ensure_render_resources_available(pid, [f"character:{cid}"])
    async with project_lock(pid):
        project = json.loads(path.read_text(encoding="utf-8"))
        character = next(
            (item for item in project.get("characters", []) if item.get("id") == cid),
            None,
        )
        if not character:
            raise HTTPException(404, "Không tìm thấy nhân vật")
        candidate = next(
            (
                item
                for item in character.get("reference_candidates", [])
                if str(item.get("url", "")).split("?")[0]
                == body.reference_url.split("?")[0]
            ),
            None,
        )
        if not candidate:
            raise HTTPException(400, "Ảnh được chọn không thuộc danh sách ứng viên")
        character["selected_reference"] = candidate["url"]
        required = {
            str(ref)
            for scene in project.get("scenes", [])
            for ref in (scene.get("characters") or [])
        }
        selected = {
            str(item.get("id"))
            for item in project.get("characters", [])
            if item.get("selected_reference")
        }
        project["cast_locked"] = required.issubset(selected)
        write_json_atomic(path, project)
    return {"ok": True, "cast_locked": project["cast_locked"], "project": project}


@app.post("/api/projects/{pid}/characters/{cid}/upload-reference")
async def upload_character_reference(pid: str, cid: str, image: UploadFile = File(...)):
    folder = safe(pid)
    path = folder / "project.json"
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy project")
    ensure_render_resources_available(pid, [f"character:{cid}"])
    allowed = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    if image.content_type not in allowed:
        raise HTTPException(415, "Chỉ hỗ trợ ảnh PNG, JPG hoặc WebP")
    data = await image.read(12 * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(400, "File ảnh trống")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "Ảnh vượt quá giới hạn 12 MB")
    target = folder / "character-references" / cid
    target.mkdir(parents=True, exist_ok=True)
    filename = f"upload_{uuid.uuid4().hex[:10]}{allowed[image.content_type]}"
    (target / filename).write_bytes(data)
    url = f"/projects/{pid}/character-references/{cid}/{filename}?v={int(time.time())}"
    candidate = {"url": url, "source": "upload", "filename": image.filename or filename}
    async with project_lock(pid):
        project = json.loads(path.read_text(encoding="utf-8"))
        character = next(
            (item for item in project.get("characters", []) if item.get("id") == cid),
            None,
        )
        if not character:
            raise HTTPException(404, "Không tìm thấy nhân vật")
        character.setdefault("reference_candidates", []).append(candidate)
        character["selected_reference"] = url
        required = {
            str(ref)
            for scene in project.get("scenes", [])
            for ref in (scene.get("characters") or [])
        }
        selected = {
            str(item.get("id"))
            for item in project.get("characters", [])
            if item.get("selected_reference")
        }
        project["cast_locked"] = required.issubset(selected)
        write_json_atomic(path, project)
    return {
        "ok": True,
        "cast_locked": project["cast_locked"],
        "project": project,
        "reference": candidate,
    }


@app.post("/api/generate", status_code=202)
async def generate(req: GenerateRequest):
    folder = safe(req.project_id)
    path = folder / "project.json"
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy dự án")
    project = json.loads(path.read_text(encoding="utf-8"))
    scene = next((x for x in project["scenes"] if x["id"] == req.scene_id), None)
    if not scene:
        raise HTTPException(404, "Không tìm thấy cảnh")
    resources = [f"scene:{req.scene_id}"] + [
        f"character:{cid}" for cid in (scene.get("characters") or [])
    ]
    ensure_render_resources_available(req.project_id, resources)
    jid = uuid.uuid4().hex
    RENDER_JOBS[jid] = {
        "id": jid,
        "status": "queued",
        "kind": "scene",
        "project_id": req.project_id,
        "scene_id": req.scene_id,
        "quality": req.quality,
        "request": req.model_dump(),
        "resource_keys": resources,
        "progress": 0,
        "message": f"Đang xếp hàng render {req.quality}",
        "errors": [],
    }
    set_render_job(jid)
    launch_render_task(jid, scene_render_job(jid, req))
    return {"job_id": jid, "status": "queued"}


def render_job_file(jid: str) -> Path:
    return RENDER_JOB_DIR / f"{jid}.json"


def set_render_job(jid: str, **values: Any) -> None:
    RENDER_JOBS[jid].update(values, updated_at=time.time())
    target = render_job_file(jid)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(RENDER_JOBS[jid], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(target)


def ensure_render_resources_available(project_id: str, resources: list[str]) -> None:
    wanted = set(resources)
    for job in RENDER_JOBS.values():
        if job.get("project_id") != project_id:
            continue
        if job.get("status") not in {"queued", "running"}:
            continue
        active = set(job.get("resource_keys") or [])
        if "project:*" in active or "project:*" in wanted or active & wanted:
            raise HTTPException(
                409,
                f"Tài nguyên đang được job {job.get('id')} xử lý: "
                + ", ".join(sorted(active & wanted or active or wanted)),
            )


def launch_render_task(jid: str, coroutine) -> asyncio.Task:
    existing = RENDER_TASKS.get(jid)
    if existing and not existing.done():
        raise HTTPException(409, "Tiến trình render vẫn đang chạy")
    task = asyncio.create_task(coroutine, name=f"render:{jid}")
    RENDER_TASKS[jid] = task

    def forget(done: asyncio.Task) -> None:
        if RENDER_TASKS.get(jid) is done:
            RENDER_TASKS.pop(jid, None)

    task.add_done_callback(forget)
    return task


async def scene_render_job(jid: str, req: GenerateRequest) -> None:
    try:
        folder = safe(req.project_id)
        path = folder / "project.json"
        project = json.loads(path.read_text(encoding="utf-8"))
        normalize_render_fields(project)
        scene = next((x for x in project["scenes"] if x["id"] == req.scene_id), None)
        if not scene:
            raise ValueError(f"Không tìm thấy cảnh {req.scene_id}")
        if req.quality == "draft":
            req.width, req.height = {
                "9:16": (576, 1024),
                "1:1": (768, 768),
                "4:3": (896, 672),
            }.get(project.get("aspect_ratio"), (1024, 576))
            req.steps = 6
        set_render_job(jid, status="running", progress=5, message=f"Đang render {req.scene_id}")
        result = await render_one(
            project, path, scene, req.width, req.height, req.steps, req.quality
        )
        set_render_job(
            jid,
            status="completed",
            progress=100,
            result=result,
            message=f"Đã render {req.quality} cho {req.scene_id}",
        )
    except Exception as exc:
        set_render_job(
            jid,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            message=f"Render {req.scene_id} thất bại",
        )


async def render_all_job(jid: str, req: RenderAllRequest):
    try:
        folder = safe(req.project_id)
        path = folder / "project.json"
        project = json.loads(path.read_text(encoding="utf-8"))
        normalize_render_fields(project)
        quality_field = f"{req.quality}_image_url"
        scenes = [
            s for s in project["scenes"]
            if req.overwrite or not s.get(quality_field)
        ]
        total = len(scenes)
        set_render_job(jid, status="running", total=total, completed=0, progress=0)
        required = {
            str(ref)
            for scene in project.get("scenes", [])
            for ref in (scene.get("characters") or [])
        }
        selected = {
            str(item.get("id"))
            for item in project.get("characters", [])
            if item.get("selected_reference")
        }
        missing = sorted(required - selected)
        if missing:
            raise ValueError(f'Chưa khóa ảnh reference cho: {", ".join(missing)}')
        if req.quality == "draft":
            width, height = {
                "9:16": (576, 1024),
                "1:1": (768, 768),
                "4:3": (896, 672),
            }.get(project.get("aspect_ratio"), (1024, 576))
            steps = 6
        else:
            width, height = {
                "9:16": (928, 1664),
                "1:1": (1328, 1328),
                "4:3": (1472, 1140),
            }.get(project.get("aspect_ratio"), (1664, 928))
            steps = req.steps
        for i, scene in enumerate(scenes):
            RENDER_JOBS[jid].update(
                current_scene=scene["id"],
                message=f'Render {scene["id"]} · {i+1}/{total}',
            )
            set_render_job(jid)
            try:
                await render_one(
                    project, path, scene, width, height, steps, req.quality
                )
            except Exception as exc:
                RENDER_JOBS[jid].setdefault("errors", []).append(
                    {"scene_id": scene["id"], "error": f"{type(exc).__name__}: {exc}"}
                )
            set_render_job(
                jid, completed=i + 1, progress=round((i + 1) * 100 / max(1, total))
            )
        RENDER_JOBS[jid].update(
            status="completed",
            message=f'Hoàn tất {total-len(RENDER_JOBS[jid].get("errors",[]))}/{total} ảnh',
        )
    except Exception as exc:
        RENDER_JOBS[jid].update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        set_render_job(jid)


@app.post("/api/generate/jobs", status_code=202)
async def start_render_all(req: RenderAllRequest):
    ensure_render_resources_available(req.project_id, ["project:*"])
    jid = uuid.uuid4().hex
    RENDER_JOBS[jid] = {
        "id": jid,
        "status": "queued",
        "kind": "bulk",
        "project_id": req.project_id,
        "quality": req.quality,
        "request": req.model_dump(),
        "resource_keys": ["project:*"],
        "progress": 0,
        "message": "Đang xếp hàng ComfyUI…",
        "errors": [],
    }
    set_render_job(jid)
    launch_render_task(jid, render_all_job(jid, req))
    return {"job_id": jid}


@app.get("/api/generate/jobs/{jid}")
async def render_status(jid: str):
    target = render_job_file(jid)
    if jid not in RENDER_JOBS and target.exists():
        RENDER_JOBS[jid] = json.loads(target.read_text(encoding="utf-8"))
    if jid not in RENDER_JOBS:
        raise HTTPException(404, "Không tìm thấy tiến trình render")
    return RENDER_JOBS[jid]


@app.get("/api/generate/jobs")
async def list_render_jobs(project_id: str | None = None):
    for target in RENDER_JOB_DIR.glob("*.json"):
        jid = target.stem
        if jid in RENDER_JOBS:
            continue
        try:
            RENDER_JOBS[jid] = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    rows = [
        job for job in RENDER_JOBS.values()
        if not project_id or job.get("project_id") == project_id
    ]
    return sorted(rows, key=lambda item: item.get("updated_at", 0), reverse=True)[:100]


@app.on_event("startup")
async def restore_render_jobs():
    for target in RENDER_JOB_DIR.glob("*.json"):
        jid = ""
        try:
            job = json.loads(target.read_text(encoding="utf-8"))
            jid = str(job.get("id") or target.stem)
            RENDER_JOBS[jid] = job
            if job.get("status") not in {"queued", "running"}:
                continue
            request = job.get("request") or {}
            kind = job.get("kind")
            set_render_job(
                jid,
                status="queued",
                message="Backend vừa khởi động lại · đang khôi phục hàng đợi render",
                resumed=True,
            )
            if kind == "scene":
                req = GenerateRequest.model_validate(request)
                launch_render_task(jid, scene_render_job(jid, req))
            elif kind == "bulk":
                req = RenderAllRequest.model_validate(request)
                launch_render_task(jid, render_all_job(jid, req))
            elif kind == "character_references":
                pid = str(request["project_id"])
                cid = str(request["character_id"])
                launch_render_task(jid, character_reference_job(jid, pid, cid))
            else:
                set_render_job(
                    jid,
                    status="failed",
                    error="Job cũ không có dữ liệu để khôi phục",
                )
        except Exception as exc:
            if jid and jid in RENDER_JOBS:
                set_render_job(
                    jid,
                    status="failed",
                    error=f"Không thể khôi phục: {type(exc).__name__}: {exc}",
                )
