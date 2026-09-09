"""
TTS Studio v5.0 - True Streaming Architecture for 30k-50k Word Texts

Key optimization: Instead of waiting for ALL chunks to finish then returning,
we use HTTP chunked transfer encoding to stream MP3 bytes to the browser
the moment each chunk is synthesized. User hears audio within 2-3 seconds
even for 50,000 word inputs.

Architecture:
- asyncio.Queue for ordered output
- Semaphore(4) controls max concurrent Edge-TTS connections  
- Each chunk is synthesized in parallel, but output is piped IN ORDER
- Browser starts receiving MP3 bytes immediately
"""

import io
import os
import asyncio
import hashlib
import re
import time
import unicodedata
import uuid
import threading
import json
import urllib.error
import urllib.request
import shutil
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import lameenc
import numpy as np
from vieneu import Vieneu

app = FastAPI(title="TTS Studio v5.0 - True Streaming", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_frontend_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith(("/static/js/", "/static/css/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ── Disk cache for repeated texts ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = os.path.join(os.path.dirname(__file__), "audio_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "generated_audio")
os.makedirs(OUTPUT_DIR, exist_ok=True)
CUSTOM_VOICE_DIR = BASE_DIR / "data" / "custom_voices"
CUSTOM_VOICE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_JOBS: Dict[str, Dict[str, Any]] = {}
PARSE_JOBS: Dict[str, Dict[str, Any]] = {}
VISUAL_JOBS: Dict[str, Dict[str, Any]] = {}
RAM_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
RAM_CACHE_BYTES = 0
MAX_RAM_CACHE_BYTES = 64 * 1024 * 1024

VOICES_CACHE: List[Dict[str, Any]] = []
VOICES_LOADING_LOCK = asyncio.Lock()
CUSTOM_VOICE_PROFILES: Dict[str, Dict[str, Any]] = {}
CUSTOM_VOICE_METADATA: Dict[str, Dict[str, Any]] = {}
CUSTOM_VOICE_REGISTRY_LOCK = threading.RLock()
CUSTOM_VOICE_ID_PATTERN = re.compile(r"^custom:([0-9a-f]{32})$")
MAX_CUSTOM_VOICE_FILES = 8
MAX_CUSTOM_VOICE_SAMPLES = 24
MAX_CUSTOM_VOICE_FILE_BYTES = 25 * 1024 * 1024
EDGE_CONNECTION_LIMIT = asyncio.Semaphore(1)
EDGE_RATE_LOCK = asyncio.Lock()
EDGE_CONNECT_TIMEOUT = 25
EDGE_RECEIVE_TIMEOUT = 120
EDGE_MAX_ATTEMPTS = 4
EDGE_MIN_START_INTERVAL = 0.75
EDGE_BATCH_SIZE = 18
EDGE_BATCH_COOLDOWN = 6.0
EDGE_NEXT_REQUEST_AT = 0.0
EDGE_REQUEST_COUNT = 0
EDGE_FAILURE_STREAK = 0
EDGE_CIRCUIT_OPEN_UNTIL = 0.0
EDGE_CIRCUIT_BREAK_SECONDS = 60.0
GTTS_CONNECTION_LIMIT = asyncio.Semaphore(1)
GTTS_HTTP_TIMEOUT = 15
GTTS_TOTAL_TIMEOUT = 45

def local_voice(voice_id: str, name: str, gender: str, region: str, style: str = "doc_truyen"):
    return {
        "id": voice_id, "name": name, "displayName": f"{name} ({region})",
        "gender": gender, "locale": "vi-VN", "languageName": f"Tiếng Việt ({region})",
        "flag": "🇻🇳", "gttsLang": "vi", "popular": True, "isVietnamese": True,
        "sampleText": "Đêm hôm ấy, câu chuyện bắt đầu dưới một bầu trời đầy sao.",
        "fullInfo": f"VieNeu v3 Turbo • {region} • {style}", "style": style,
    }


FALLBACK_VOICES = [
    local_voice("vieneu-ngoc-linh", "Ngọc Linh", "Female", "Bắc"),
    local_voice("vieneu-thanh-binh", "Thanh Bình", "Male", "Bắc"),
    local_voice("vieneu-thai-son", "Thái Sơn", "Male", "Nam"),
    local_voice("vieneu-thuc-doan", "Thục Đoan", "Female", "Nam"),
    local_voice("vieneu-my-duyen", "Mỹ Duyên", "Female", "Nam"),
    local_voice("vieneu-duc-tri", "Đức Trí", "Male", "Nam"),
    local_voice("vieneu-quynh-anh", "Quỳnh Anh", "Female", "Bắc"),
    local_voice("vieneu-kim-thanh", "Kim Thanh", "Female", "Nam"),
    local_voice("vieneu-pham-tuyen", "Phạm Tuyên", "Male", "Bắc", "tu_nhien"),
    local_voice("vieneu-truc-ly", "Trúc Ly", "Female", "Bắc", "tu_nhien"),
]

VIENEU_VOICE_NAMES = {voice["id"]: voice["name"] for voice in FALLBACK_VOICES}
VIENEU_VOICE_STYLES = {voice["id"]: voice["style"] for voice in FALLBACK_VOICES}
VIENEU_ENGINE: Optional[Vieneu] = None
VIENEU_LOAD_LOCK = threading.Lock()
VIENEU_SYNTH_LOCK = asyncio.Lock()

OUTPUT_SAMPLE_RATE = 48000


def _custom_voice_folder(voice_id: str) -> Path:
    match = CUSTOM_VOICE_ID_PATTERN.fullmatch(voice_id or "")
    if not match:
        raise ValueError("Mã giọng tùy chỉnh không hợp lệ")
    folder = (CUSTOM_VOICE_DIR / match.group(1)).resolve()
    if folder.parent != CUSTOM_VOICE_DIR.resolve():
        raise ValueError("Đường dẫn giọng tùy chỉnh không hợp lệ")
    return folder


def _custom_voice_public(meta: Dict[str, Any]) -> Dict[str, Any]:
    name = str(meta.get("name") or "Giọng tùy chỉnh")
    gender = str(meta.get("gender") or "Unknown")
    total_samples = int(meta.get("sourceFileCount", 1))
    used_samples = int(meta.get("usedSampleCount", total_samples))
    return {
        "id": meta["id"],
        "name": name,
        "displayName": f"{name} (Giọng của tôi)",
        "gender": gender,
        "locale": "vi-VN",
        "languageName": "Tiếng Việt (Giọng tùy chỉnh)",
        "flag": "🎙️",
        "gttsLang": "vi",
        "popular": True,
        "isVietnamese": True,
        "isCustom": True,
        "source": "custom",
        "sampleText": meta.get(
            "sampleText", "Xin chào, đây là giọng đọc tùy chỉnh vừa được tạo."
        ),
        "fullInfo": (
            f"VieNeu v3 Turbo • Tổng hợp {used_samples}/{total_samples} mẫu"
        ),
        "sampleCount": total_samples,
        "usedSampleCount": used_samples,
        "qualityScore": meta.get("qualityScore"),
        "createdAt": meta.get("createdAt"),
        "updatedAt": meta.get("updatedAt", meta.get("createdAt")),
    }


def load_custom_voice_registry() -> None:
    """Load app-owned custom voice profiles without modifying site-packages."""
    loaded_profiles: Dict[str, Dict[str, Any]] = {}
    loaded_metadata: Dict[str, Dict[str, Any]] = {}
    for meta_path in CUSTOM_VOICE_DIR.glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            voice_id = str(meta.get("id") or "")
            folder = _custom_voice_folder(voice_id)
            profile_path = folder / "profile.npz"
            if meta_path.parent.resolve() != folder or not profile_path.is_file():
                continue
            with np.load(profile_path, allow_pickle=False) as values:
                speaker_emb = np.asarray(values["speaker_emb"], dtype=np.float32)
                codes = np.asarray(values["codes"], dtype=np.int64)
            if speaker_emb.size == 0:
                continue
            loaded_profiles[voice_id] = {
                "speaker_emb": speaker_emb,
                "codes": codes if codes.size else None,
            }
            loaded_metadata[voice_id] = meta
        except Exception as exc:
            print(f"Skipping invalid custom voice {meta_path.parent.name}: {type(exc).__name__}")
    with CUSTOM_VOICE_REGISTRY_LOCK:
        CUSTOM_VOICE_PROFILES.clear()
        CUSTOM_VOICE_PROFILES.update(loaded_profiles)
        CUSTOM_VOICE_METADATA.clear()
        CUSTOM_VOICE_METADATA.update(loaded_metadata)


def _known_voice(voice_id: str) -> bool:
    with CUSTOM_VOICE_REGISTRY_LOCK:
        return voice_id in VIENEU_VOICE_NAMES or voice_id in CUSTOM_VOICE_PROFILES


def _voice_cache_identity(voice_id: str) -> str:
    with CUSTOM_VOICE_REGISTRY_LOCK:
        meta = CUSTOM_VOICE_METADATA.get(voice_id)
        return f"{voice_id}@{meta.get('revision', '1')}" if meta else voice_id


def _prepare_voice_sample(data: bytes, filename: str) -> Dict[str, Any]:
    """Decode, validate, trim and normalize one candidate reference clip."""
    import librosa
    import soundfile as sf

    try:
        with sf.SoundFile(io.BytesIO(data)) as audio_file:
            sample_rate = int(audio_file.samplerate)
            original_duration = len(audio_file) / float(sample_rate)
            if original_duration > 300:
                raise ValueError(f"{filename}: file mẫu không được dài quá 5 phút")
            # Only the beginning is useful for the eight-second VieNeu reference.
            waveform = audio_file.read(
                frames=min(len(audio_file), round(sample_rate * 30)),
                dtype="float32",
                always_2d=False,
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Không đọc được file {filename}. Hãy dùng WAV, MP3, FLAC hoặc OGG.") from exc
    if sample_rate < 8000 or sample_rate > 192000:
        raise ValueError(f"{filename}: tần số lấy mẫu không được hỗ trợ")
    waveform = np.asarray(waveform, dtype=np.float32)
    if waveform.ndim == 2:
        waveform = waveform.mean(axis=1)
    if waveform.ndim != 1 or waveform.size == 0 or not np.isfinite(waveform).all():
        raise ValueError(f"{filename}: dữ liệu âm thanh không hợp lệ")
    waveform, _ = librosa.effects.trim(waveform, top_db=30)
    duration = waveform.size / float(sample_rate)
    if duration < 3.0:
        raise ValueError(f"{filename}: cần ít nhất 3 giây có tiếng nói rõ ràng")

    # VieNeu v3 Turbo uses at most eight seconds for voice enrollment.
    waveform = waveform[: round(sample_rate * 8.0)]
    duration = waveform.size / float(sample_rate)
    rms = float(np.sqrt(np.mean(np.square(waveform), dtype=np.float64)))
    peak = float(np.max(np.abs(waveform)))
    clipped_ratio = float(np.mean(np.abs(waveform) >= 0.985))
    if rms < 0.002:
        raise ValueError(f"{filename}: âm lượng quá nhỏ hoặc phần lớn là khoảng lặng")
    if clipped_ratio > 0.05:
        raise ValueError(f"{filename}: âm thanh bị vỡ/clipping quá nhiều")

    # Keep natural dynamics while bringing unusually quiet/loud clips into a safe range.
    gain = min(3.0, max(0.5, 0.09 / rms))
    waveform = waveform * gain
    normalized_peak = float(np.max(np.abs(waveform)))
    if normalized_peak > 0.95:
        waveform = waveform * (0.95 / normalized_peak)
    if sample_rate != 44100:
        waveform = librosa.resample(waveform, orig_sr=sample_rate, target_sr=44100)
        sample_rate = 44100

    duration_score = min(duration / 8.0, 1.0) * 45.0
    level_score = max(0.0, 35.0 - abs(np.log10(max(rms, 1e-6) / 0.08)) * 22.0)
    clipping_score = max(0.0, 20.0 - clipped_ratio * 400.0)
    quality_score = float(round(min(100.0, duration_score + level_score + clipping_score), 1))
    return {
        "filename": Path(filename or "audio").name[:180],
        "waveform": np.asarray(waveform, dtype=np.float32),
        "sample_rate": sample_rate,
        "duration": round(duration, 2),
        "quality_score": quality_score,
    }


def _save_voice_candidates(
    folder: Path, candidates: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Persist normalized candidate clips and return their metadata records."""
    import soundfile as sf

    samples_folder = folder / "samples"
    samples_folder.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    records: List[Dict[str, Any]] = []
    written_paths: List[Path] = []
    try:
        for candidate in candidates:
            sample_id = uuid.uuid4().hex
            relative_path = f"samples/{sample_id}.wav"
            sample_path = folder / relative_path
            written_paths.append(sample_path)
            sf.write(
                sample_path,
                candidate["waveform"],
                candidate["sample_rate"],
                subtype="PCM_16",
            )
            records.append({
                "id": sample_id,
                "file": relative_path,
                "originalName": candidate["filename"],
                "duration": candidate["duration"],
                "qualityScore": candidate["quality_score"],
                "createdAt": created_at,
            })
    except Exception:
        for written_path in written_paths:
            try:
                written_path.unlink()
            except OSError:
                pass
        raise
    return records


def _voice_sample_path(folder: Path, record: Dict[str, Any]) -> Path:
    relative_path = str(record.get("file") or "")
    sample_path = (folder / relative_path).resolve()
    if not relative_path or (sample_path != folder and folder not in sample_path.parents):
        raise ValueError("Đường dẫn file mẫu không hợp lệ")
    if not sample_path.is_file():
        raise FileNotFoundError(relative_path)
    return sample_path


def _build_aggregated_voice_profile(
    folder: Path, records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Extract every speaker embedding, remove clear outliers, then aggregate."""

    engine = get_vieneu_engine()
    prepare_reference = getattr(getattr(engine, "engine", None), "prepare_reference", None)
    if prepare_reference is None:
        raise RuntimeError("Phiên bản VieNeu hiện tại không hỗ trợ đăng ký giọng")

    extracted: List[Dict[str, Any]] = []
    failed_ids = set()
    expected_size: Optional[int] = None
    for record in records:
        try:
            sample_path = _voice_sample_path(folder, record)
            speaker_value, codes_value = prepare_reference(
                str(sample_path), denoise=True, use_ref_codes=True
            )
            embedding = np.asarray(speaker_value, dtype=np.float32)
            if embedding.size == 0 or not np.isfinite(embedding).all():
                raise ValueError("Không trích xuất được đặc trưng")
            if expected_size is None:
                expected_size = embedding.size
            if embedding.size != expected_size:
                raise ValueError("Kích thước đặc trưng không đồng nhất")
            codes = (
                np.asarray(codes_value, dtype=np.int64)
                if codes_value is not None
                else np.array([], dtype=np.int64)
            )
            extracted.append({
                "record": record,
                "embedding": embedding,
                "flat": embedding.reshape(-1),
                "codes": codes,
                "path": sample_path,
            })
        except Exception as exc:
            failed_ids.add(str(record.get("id") or ""))
            print(
                f"Skipping custom voice sample {record.get('id')}: "
                f"{type(exc).__name__}: {exc}"
            )

    if not extracted:
        raise RuntimeError("VieNeu không trích xuất được đặc trưng người nói")

    matrix = np.stack([item["flat"] for item in extracted]).astype(np.float64)
    norms = np.linalg.norm(matrix, axis=1)
    normalized = matrix / np.maximum(norms[:, None], 1e-12)
    similarities = np.clip(normalized @ normalized.T, -1.0, 1.0)
    centrality = similarities.mean(axis=1)
    qualities = np.asarray([
        float(item["record"].get("qualityScore") or 0.0) / 100.0
        for item in extracted
    ])
    representative_index = int(np.argmax(qualities * 0.65 + ((centrality + 1.0) / 2.0) * 0.35))
    representative_similarities = similarities[representative_index]

    # A strongly dissimilar recording is likely another speaker or corrupted audio.
    accepted_mask = representative_similarities >= 0.25
    accepted_mask[representative_index] = True
    accepted = [item for index, item in enumerate(extracted) if accepted_mask[index]]
    accepted_similarities = representative_similarities[accepted_mask]
    accepted_qualities = qualities[accepted_mask]
    weights = (0.5 + accepted_qualities) * (0.5 + np.maximum(accepted_similarities, 0.0))
    accepted_matrix = np.stack([item["flat"] for item in accepted]).astype(np.float64)
    aggregate_flat = np.average(accepted_matrix, axis=0, weights=weights)
    target_norm = float(np.average(np.linalg.norm(accepted_matrix, axis=1), weights=weights))
    aggregate_norm = float(np.linalg.norm(aggregate_flat))
    if aggregate_norm > 1e-12 and target_norm > 0:
        aggregate_flat *= target_norm / aggregate_norm
    aggregate = aggregate_flat.reshape(accepted[0]["embedding"].shape).astype(np.float32)

    representative = extracted[representative_index]
    accepted_ids = {str(item["record"].get("id") or "") for item in accepted}
    similarity_by_id = {
        str(item["record"].get("id") or ""): round(float(representative_similarities[index]), 4)
        for index, item in enumerate(extracted)
    }
    updated_records = []
    for record in records:
        record_copy = dict(record)
        record_id = str(record.get("id") or "")
        record_copy["used"] = record_id in accepted_ids
        record_copy["similarity"] = similarity_by_id.get(record_id)
        if record_id in failed_ids:
            record_copy["used"] = False
        updated_records.append(record_copy)

    return {
        "speaker_emb": aggregate,
        "codes": representative["codes"],
        "reference_path": representative["path"],
        "representative": representative["record"],
        "records": updated_records,
        "used_count": len(accepted),
    }


def _persist_custom_voice_profile(
    folder: Path, profile: Dict[str, Any], meta: Dict[str, Any]
) -> Dict[str, Any]:
    """Replace the reusable profile and its metadata using same-folder temp files."""
    profile_path = folder / "profile.npz"
    reference_path = folder / "reference.wav"
    meta_path = folder / "meta.json"
    temporary_profile = folder / "profile.npz.part"
    temporary_reference = folder / "reference.wav.part"
    temporary_meta = folder / "meta.json.part"
    existing_files = (profile_path, reference_path, meta_path)
    backups = {
        existing_path: existing_path.read_bytes() if existing_path.is_file() else None
        for existing_path in existing_files
    }

    try:
        with temporary_profile.open("wb") as profile_file:
            np.savez_compressed(
                profile_file,
                speaker_emb=profile["speaker_emb"],
                codes=profile["codes"],
            )
        shutil.copyfile(profile["reference_path"], temporary_reference)
        meta = dict(meta)
        meta["revision"] = hashlib.sha256(temporary_profile.read_bytes()).hexdigest()[:16]
        temporary_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary_profile, profile_path)
        os.replace(temporary_reference, reference_path)
        os.replace(temporary_meta, meta_path)
        return meta
    except Exception:
        # Refinement must never leave a half-updated profile on disk.
        for existing_path, backup in backups.items():
            try:
                if backup is None:
                    existing_path.unlink(missing_ok=True)
                else:
                    rollback_path = existing_path.with_name(existing_path.name + ".rollback")
                    rollback_path.write_bytes(backup)
                    os.replace(rollback_path, existing_path)
            except Exception as rollback_exc:
                print(
                    f"Custom voice rollback failed for {existing_path.name}: "
                    f"{type(rollback_exc).__name__}"
                )
        raise
    finally:
        for temporary_path in (
            temporary_profile,
            temporary_reference,
            temporary_meta,
            *(path.with_name(path.name + ".rollback") for path in existing_files),
        ):
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _enroll_custom_voice(
    voice_id: str,
    name: str,
    gender: str,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Create a reusable profile from all consistent candidate recordings."""
    folder = _custom_voice_folder(voice_id)
    folder.mkdir(parents=False, exist_ok=False)
    records = _save_voice_candidates(folder, candidates)
    profile = _build_aggregated_voice_profile(folder, records)
    representative = profile["representative"]
    now = datetime.now(timezone.utc).isoformat()

    meta = {
        "id": voice_id,
        "name": name,
        "gender": gender,
        "sourceFileCount": len(records),
        "usedSampleCount": profile["used_count"],
        "selectedSample": representative["originalName"],
        "selectedSampleId": representative["id"],
        "selectedDuration": representative["duration"],
        "qualityScore": representative["qualityScore"],
        "samples": profile["records"],
        "createdAt": now,
        "updatedAt": now,
        "sampleText": "Xin chào, đây là giọng đọc tùy chỉnh vừa được tạo từ mẫu của tôi.",
    }
    return _persist_custom_voice_profile(folder, profile, meta)


def _refine_custom_voice(
    voice_id: str, candidates: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Append normalized recordings and rebuild an existing aggregate profile."""
    folder = _custom_voice_folder(voice_id)
    with CUSTOM_VOICE_REGISTRY_LOCK:
        current_meta = dict(CUSTOM_VOICE_METADATA.get(voice_id) or {})
    if not current_meta or not folder.is_dir():
        raise ValueError("Không tìm thấy giọng tùy chỉnh")

    records = [dict(record) for record in current_meta.get("samples", [])]
    if not records:
        # Profiles created by the earlier version only retained reference.wav.
        records = [{
            "id": "legacy-reference",
            "file": "reference.wav",
            "originalName": current_meta.get("selectedSample", "Mẫu cũ"),
            "duration": current_meta.get("selectedDuration"),
            "qualityScore": current_meta.get("qualityScore", 70.0),
            "createdAt": current_meta.get("createdAt"),
        }]
    if len(records) + len(candidates) > MAX_CUSTOM_VOICE_SAMPLES:
        raise ValueError(f"Mỗi profile được lưu tối đa {MAX_CUSTOM_VOICE_SAMPLES} mẫu")

    new_records = _save_voice_candidates(folder, candidates)
    try:
        all_records = records + new_records
        profile = _build_aggregated_voice_profile(folder, all_records)
        representative = profile["representative"]
        current_meta.update({
            "sourceFileCount": len(all_records),
            "usedSampleCount": profile["used_count"],
            "selectedSample": representative["originalName"],
            "selectedSampleId": representative["id"],
            "selectedDuration": representative.get("duration"),
            "qualityScore": representative.get("qualityScore"),
            "samples": profile["records"],
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        return _persist_custom_voice_profile(folder, profile, current_meta)
    except Exception:
        for record in new_records:
            try:
                _voice_sample_path(folder, record).unlink()
            except (FileNotFoundError, ValueError):
                pass
        raise

LANGUAGE_MAP = {
    "vi-VN": {"name": "Tiếng Việt (Việt Nam)", "flag": "🇻🇳", "popular": True, "gtts_lang": "vi",
               "sample": "Xin chào, tôi là giọng đọc tiếng Việt chuẩn AI."},
    # "en-US": {"name": "Tiếng Anh (Mỹ)", "flag": "🇺🇸", "popular": True, "gtts_lang": "en",
    #            "sample": "Hello! I am a neural AI voice from the United States."},
    # "en-GB": {"name": "Tiếng Anh (Anh)", "flag": "🇬🇧", "popular": True, "gtts_lang": "en",
    #            "sample": "Hello, this is a British English neural AI voice."},
    # "en-AU": {"name": "Tiếng Anh (Úc)", "flag": "🇦🇺", "popular": False, "gtts_lang": "en",
    #            "sample": "G'day! This is an Australian English neural voice."},
    # "ja-JP": {"name": "Tiếng Nhật", "flag": "🇯🇵", "popular": True, "gtts_lang": "ja",
    #            "sample": "こんにちは、こちらは日本語のAI音声です。"},
    # "ko-KR": {"name": "Tiếng Hàn", "flag": "🇰🇷", "popular": True, "gtts_lang": "ko",
    #            "sample": "안녕하세요! 이것은 한국어 인공지능 음성입니다."},
    # "zh-CN": {"name": "Tiếng Trung (Phổ thông)", "flag": "🇨🇳", "popular": True, "gtts_lang": "zh-CN",
    #            "sample": "你好！这是来自微软的中文神经网络语音。"},
    # "fr-FR": {"name": "Tiếng Pháp", "flag": "🇫🇷", "popular": True, "gtts_lang": "fr",
    #            "sample": "Bonjour, je suis une voix artificielle française."},
    # "de-DE": {"name": "Tiếng Đức", "flag": "🇩🇪", "popular": False, "gtts_lang": "de",
    #            "sample": "Hallo! Dies ist eine deutsche neuronale KI-Stimme."},
    # "es-ES": {"name": "Tiếng Tây Ban Nha", "flag": "🇪🇸", "popular": False, "gtts_lang": "es",
    #            "sample": "¡Hola! Esta es una voz neuronal en español."},
    # "ru-RU": {"name": "Tiếng Nga", "flag": "🇷🇺", "popular": False, "gtts_lang": "ru",
    #            "sample": "Привет, это естественный голос искусственного интеллекта."},
    # "th-TH": {"name": "Tiếng Thái", "flag": "🇹🇭", "popular": False, "gtts_lang": "th",
    #            "sample": "สวัสดีครับ นี่คือเสียงสังเคราะห์ AI ภาษาไทย"},
    # "id-ID": {"name": "Tiếng Indonesia", "flag": "🇮🇩", "popular": False, "gtts_lang": "id",
    #            "sample": "Halo, ini adalah suara kecerdasan buatan bahasa Indonesia."}
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def remove_vietnamese_accents(text: str) -> str:
    s1 = 'ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúýĂăĐđĨĩŨũƠơƯưẠạẢảẤấẦầẨẩẪẫẬậẮắẰằẲẳẴẵẶặẸẹẺẻẼẽẾếỀềỂểỄễỆệỈỉỊịỌọỎỏỐốỒồỔổỖỗỘộỚớỜờỞởỠỡỢợỤụỦủỨứỪừỬửỮữỰựỲỳỴỵỶỷỸỹ'
    s0 = 'AAAAEEEIIOOOOUUYaaaaeeeiioooouuyAaDdIiUuOoUuAaAaAaAaAaAaAaAaAaAaAaAaAaEeEeEeEeEeEeEeEeIiIiOoOoOoOoOoOoOoOoOoOoOoOoOoOoUuUuUuUuUuUuUuYyYyYyYy'
    return ''.join(s0[s1.find(c)] if s1.find(c) != -1 else c for c in text)


def cache_key(text: str, voice: str, rate: str, pitch: str, volume: str) -> str:
    return hashlib.md5(f"{text}|{voice}|{rate}|{pitch}|{volume}".encode()).hexdigest()


def get_cached(key: str) -> Optional[bytes]:
    if key in RAM_CACHE:
        RAM_CACHE.move_to_end(key)
        return RAM_CACHE[key]
    path = os.path.join(CACHE_DIR, f"{key}.pcm")
    if os.path.exists(path):
        data = open(path, "rb").read()
        add_to_ram_cache(key, data)
        return data
    return None


def save_cached(key: str, data: bytes):
    if not data:
        return
    add_to_ram_cache(key, data)
    try:
        with open(os.path.join(CACHE_DIR, f"{key}.pcm"), "wb") as f:
            f.write(data)
    except Exception:
        pass


def add_to_ram_cache(key: str, data: bytes):
    """Bound the hot cache so a long book cannot exhaust server memory."""
    global RAM_CACHE_BYTES
    old = RAM_CACHE.pop(key, None)
    if old:
        RAM_CACHE_BYTES -= len(old)
    RAM_CACHE[key] = data
    RAM_CACHE_BYTES += len(data)
    while RAM_CACHE and RAM_CACHE_BYTES > MAX_RAM_CACHE_BYTES:
        _, evicted = RAM_CACHE.popitem(last=False)
        RAM_CACHE_BYTES -= len(evicted)


async def persist_audio_stream(source: AsyncGenerator[bytes, None], job_id: str):
    """Stream audio to the client and atomically build one downloadable MP3."""
    part_path = os.path.join(OUTPUT_DIR, f"{job_id}.part")
    final_path = os.path.join(OUTPUT_DIR, f"{job_id}.mp3")
    OUTPUT_JOBS[job_id] = {"status": "processing", "path": final_path, "bytes": 0}
    try:
        with open(part_path, "wb") as output:
            async for data in source:
                if not data:
                    continue
                output.write(data)
                OUTPUT_JOBS[job_id]["bytes"] += len(data)
                yield data
        os.replace(part_path, final_path)
        OUTPUT_JOBS[job_id]["status"] = "ready"
    except (asyncio.CancelledError, GeneratorExit):
        OUTPUT_JOBS[job_id]["status"] = "cancelled"
        if os.path.exists(part_path):
            os.remove(part_path)
        raise
    except Exception as exc:
        OUTPUT_JOBS[job_id]["status"] = "failed"
        OUTPUT_JOBS[job_id]["error"] = type(exc).__name__
        if os.path.exists(part_path):
            os.remove(part_path)
        raise


def gtts_fallback(text: str, locale: str) -> bytes:
    text = clean_text_for_tts(text)
    if not text or not any(char.isalnum() for char in text):
        return b""
    lang = "vi" if locale.startswith("vi-") else locale.split("-")[0]
    try:
        tts = gTTS(text=text, lang=lang, timeout=GTTS_HTTP_TIMEOUT)
    except Exception:
        tts = gTTS(text=text, lang="en", timeout=GTTS_HTTP_TIMEOUT)
    fp = io.BytesIO()
    tts.write_to_fp(fp)
    return fp.getvalue()


async def run_gtts_fallback(text: str, locale: str) -> bytes:
    """Run at most one bounded gTTS request so fallback cannot stall workers."""
    async with GTTS_CONNECTION_LIMIT:
        return await asyncio.wait_for(
            asyncio.to_thread(gtts_fallback, text, locale),
            timeout=GTTS_TOTAL_TIMEOUT,
        )


async def wait_for_edge_capacity():
    """Pace new WebSockets globally to avoid Edge's burst throttling."""
    global EDGE_NEXT_REQUEST_AT, EDGE_REQUEST_COUNT
    async with EDGE_RATE_LOCK:
        now = time.monotonic()
        delay = max(0.0, EDGE_NEXT_REQUEST_AT - now)
        EDGE_REQUEST_COUNT += 1
        batch_pause = EDGE_BATCH_COOLDOWN if EDGE_REQUEST_COUNT % EDGE_BATCH_SIZE == 0 else 0.0
        EDGE_NEXT_REQUEST_AT = max(now, EDGE_NEXT_REQUEST_AT) + EDGE_MIN_START_INTERVAL + batch_pause
    if delay > 0:
        await asyncio.sleep(delay)


async def report_edge_success():
    global EDGE_FAILURE_STREAK
    async with EDGE_RATE_LOCK:
        EDGE_FAILURE_STREAK = max(0, EDGE_FAILURE_STREAK - 1)


async def edge_circuit_is_open() -> bool:
    async with EDGE_RATE_LOCK:
        return time.monotonic() < EDGE_CIRCUIT_OPEN_UNTIL


async def report_edge_failure():
    """Apply a shared circuit-breaker cooldown after empty/failed responses."""
    global EDGE_FAILURE_STREAK, EDGE_NEXT_REQUEST_AT, EDGE_CIRCUIT_OPEN_UNTIL
    async with EDGE_RATE_LOCK:
        EDGE_FAILURE_STREAK += 1
        penalty = min(20.0, 2.0 * (2 ** min(EDGE_FAILURE_STREAK - 1, 3)))
        EDGE_NEXT_REQUEST_AT = max(EDGE_NEXT_REQUEST_AT, time.monotonic() + penalty)
        if EDGE_FAILURE_STREAK >= 3:
            EDGE_CIRCUIT_OPEN_UNTIL = max(
                EDGE_CIRCUIT_OPEN_UNTIL,
                time.monotonic() + EDGE_CIRCUIT_BREAK_SECONDS,
            )
        return penalty


def split_oversized_parts(parts: List[str], max_chars: int) -> List[str]:
    result: List[str] = []
    for value in parts:
        value = value.strip()
        while len(value) > max_chars:
            cut = value.rfind(" ", 0, max_chars + 1)
            if cut <= 0:
                cut = max_chars
            result.append(value[:cut].strip())
            value = value[cut:].strip()
        if value:
            result.append(value)
    return result


def clean_text_for_tts(text: str) -> str:
    """Remove invisible/control markup that can make Edge return no audio."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"(?:\*\*|__|~~|`)", "", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", " ", text)
    text = re.sub(
        r"\[\s*(?:normal|neutral|laughs|chuckles|sighs|whispers?|excited|joyful|angry|sad|fearful|surprised|tender|tense|pause(?:\s*=\s*\d+\s*(?:ms)?)?)\s*\]",
        " ", text, flags=re.IGNORECASE,
    )
    return re.sub(r"[ \t]+", " ", text).strip()


def prepare_tts_chunks(text: str, max_chars: int = 1400) -> List[str]:
    """Normalize prose and create balanced, sentence-safe Edge requests."""
    text = clean_text_for_tts(text)
    if not text:
        return []
    raw_parts = re.split(r'(?<=[.!?…])\s+|\n+', text)
    safe_parts = split_oversized_parts(raw_parts, max_chars)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for part in safe_parts:
        if not any(char.isalnum() for char in part):
            continue
        extra = len(part) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0
        current.append(part)
        current_len += len(part) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append(" ".join(current))
    return chunks


async def get_edge_circuit_delay() -> float:
    async with EDGE_RATE_LOCK:
        return max(0.0, EDGE_CIRCUIT_OPEN_UNTIL - time.monotonic())


def split_into_chunks(text: str, max_chars: int = 3000) -> List[str]:
    """
    Split text into sentence-boundary-aware chunks.
    Larger chunks (3000 chars) = fewer API calls = faster total time.
    """
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, current, current_len = [], [], 0

    for para in paragraphs:
        if len(para) > max_chars:
            # Break long paragraph at sentence endings
            sentences = re.split(r'(?<=[.!?…])\s+', para)
            for sent in split_oversized_parts(sentences, max_chars):
                sent = sent.strip()
                if not sent:
                    continue
                if current_len + len(sent) > max_chars and current:
                    chunks.append(" ".join(current))
                    current, current_len = [sent], len(sent)
                else:
                    current.append(sent)
                    current_len += len(sent)
        else:
            if current_len + len(para) > max_chars and current:
                chunks.append("\n".join(current))
                current, current_len = [para], len(para)
            else:
                current.append(para)
                current_len += len(para)

    if current:
        chunks.append("\n".join(current))
    return chunks or [text]


# ── Core TTS worker ────────────────────────────────────────────────────────────

async def synthesize_chunk(
    sem: asyncio.Semaphore,
    idx: int,
    text: str,
    voice: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    auto_trans: bool = True,
) -> tuple[int, bytes]:
    """Synthesize one text chunk. Returns (idx, mp3_bytes)."""
    text = text.strip()
    if not text:
        return idx, b""

    # Normalise parameters
    rate = rate if rate not in ("+0%", "0%", "") else "+0%"
    pitch = pitch if pitch not in ("+0Hz", "0Hz", "") else "+0Hz"
    volume = volume if volume not in ("+0%", "0%", "") else "+0%"

    # Cache hit?
    key = cache_key(text, voice, rate, pitch, volume)
    cached = get_cached(key)
    if cached:
        print(f"  [cache hit] chunk {idx}")
        return idx, cached

    is_foreign = not voice.startswith("vi-")
    has_vi = bool(re.search(
        r'[àáâãèéêìíòóôõùúýăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]',
        text, re.IGNORECASE))
    speak_text = remove_vietnamese_accents(text) if (is_foreign and has_vi and auto_trans) else text
    speak_text = clean_text_for_tts(speak_text)
    if not speak_text or not any(char.isalnum() for char in speak_text):
        print(f"  chunk {idx}: skipped because it contains no speakable text")
        return idx, b""

    circuit_delay = await get_edge_circuit_delay()
    if circuit_delay > 0:
        print(f"  chunk {idx}: Edge cooling down for {circuit_delay:.1f}s")
        await asyncio.sleep(circuit_delay)

    for attempt in range(EDGE_MAX_ATTEMPTS):
        try:
            # The global limiter also protects Edge when several HTTP requests
            # (preview, standard and drama) are active at the same time.
            async with sem, EDGE_CONNECTION_LIMIT:
                await wait_for_edge_capacity()
                comm = edge_tts.Communicate(
                    speak_text,
                    voice,
                    rate=rate,
                    pitch=pitch,
                    volume=volume,
                    connect_timeout=EDGE_CONNECT_TIMEOUT,
                    receive_timeout=EDGE_RECEIVE_TIMEOUT,
                )
                parts = [c["data"] async for c in comm.stream() if c["type"] == "audio"]
                data = b"".join(parts)
                if data:
                    await report_edge_success()
                    save_cached(key, data)
                    return idx, data
        except Exception as e:
            cooldown = await report_edge_failure()
            if isinstance(e, edge_tts.exceptions.NoAudioReceived):
                if len(speak_text) > 700:
                    sub_chunks = prepare_tts_chunks(speak_text, max_chars=max(500, len(speak_text) // 2))
                    if len(sub_chunks) > 1:
                        print(f"  chunk {idx}: Edge returned no audio; splitting into {len(sub_chunks)} parts")
                        sub_audio = []
                        for part in sub_chunks:
                            _, part_data = await synthesize_chunk(
                                sem, idx, part, voice, rate, pitch, volume, False
                            )
                            if part_data:
                                sub_audio.append(part_data)
                        data = b"".join(sub_audio)
                        if data:
                            save_cached(key, data)
                            return idx, data
                if attempt + 1 < EDGE_MAX_ATTEMPTS:
                    print(f"  chunk {idx}: Edge returned no audio; cooling down before retry")
                    await asyncio.sleep(max(cooldown, 4.0))
                    continue
                print(f"  chunk {idx}: Edge returned no audio after retries")
                break
            delay = (1.5 * (2 ** attempt)) + ((idx % 5) * 0.1)
            print(
                f"  chunk {idx} attempt {attempt + 1}/{EDGE_MAX_ATTEMPTS} failed: "
                f"{type(e).__name__}; retry in {max(delay, cooldown):.1f}s"
            )
            if attempt + 1 < EDGE_MAX_ATTEMPTS:
                await asyncio.sleep(max(delay, cooldown))

    # Final fallback: Google TTS. It is blocking, so keep it off the event loop.
    print(f"  chunk {idx}: Edge unavailable, switching to gTTS fallback")
    locale = voice[:5] if len(voice) >= 5 else "vi-VN"
    try:
        data = await run_gtts_fallback(
            remove_vietnamese_accents(text) if is_foreign else text,
            locale,
        )
        if data:
            save_cached(key, data)
            return idx, data
    except Exception as exc:
        print(f"  chunk {idx}: gTTS fallback failed: {type(exc).__name__}")

    # A single unavailable segment must not abort the complete streaming job.
    print(f"  chunk {idx}: skipped after all TTS providers failed")
    return idx, b""


def get_vieneu_engine() -> Vieneu:
    global VIENEU_ENGINE
    with VIENEU_LOAD_LOCK:
        if VIENEU_ENGINE is None:
            print("Loading VieNeu v3 Turbo ONNX CPU (int8)...")
            VIENEU_ENGINE = Vieneu(backend="onnx", precision="int8")
        return VIENEU_ENGINE


def vieneu_synthesize_pcm(
    text: str, voice_id: str, rate: str, pitch: str, volume: str
) -> bytes:
    import librosa

    engine = get_vieneu_engine()
    with CUSTOM_VOICE_REGISTRY_LOCK:
        custom_profile = CUSTOM_VOICE_PROFILES.get(voice_id)
    if custom_profile is not None:
        voice_value: Any = custom_profile
    else:
        voice_value = VIENEU_VOICE_NAMES.get(voice_id)
    if voice_value is None:
        raise ValueError(f"Không tìm thấy giọng đọc: {voice_id}")
    audio = np.asarray(
        engine.infer(text, voice=voice_value), dtype=np.float32
    )

    rate_match = re.search(r"([+-]?\d+)", rate or "0")
    pitch_match = re.search(r"([+-]?\d+)", pitch or "0")
    volume_match = re.search(r"([+-]?\d+)", volume or "0")
    speed = max(0.5, min(2.0, 1.0 + (int(rate_match.group(1)) if rate_match else 0) / 100.0))
    pitch_steps = max(-5.0, min(5.0, (int(pitch_match.group(1)) if pitch_match else 0) / 10.0))
    gain = max(0.1, min(1.5, 1.0 + (int(volume_match.group(1)) if volume_match else 0) / 100.0))

    if abs(speed - 1.0) > 0.01:
        audio = librosa.effects.time_stretch(audio, rate=speed)
    if abs(pitch_steps) > 0.01:
        audio = librosa.effects.pitch_shift(audio, sr=OUTPUT_SAMPLE_RATE, n_steps=pitch_steps)
    audio = np.clip(audio * gain, -1.0, 1.0)
    return (audio * 32767.0).astype("<i2").tobytes()


async def synthesize_chunk(
    sem: asyncio.Semaphore,
    idx: int,
    text: str,
    voice: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    auto_trans: bool = True,
) -> tuple[int, bytes]:
    """Synthesize one chunk locally with VieNeu v3 Turbo storytelling voices."""
    del auto_trans
    speak_text = clean_text_for_tts(text)
    if not speak_text or not any(char.isalnum() for char in speak_text):
        return idx, b""
    if not _known_voice(voice):
        raise ValueError(f"Không tìm thấy giọng đọc: {voice}")
    key = cache_key(
        f"vieneu-v3|{_voice_cache_identity(voice)}|{speak_text}",
        voice,
        rate,
        pitch,
        volume,
    )
    cached = get_cached(key)
    if cached:
        print(f"  [VieNeu cache] chunk {idx}")
        return idx, cached
    async with sem, VIENEU_SYNTH_LOCK:
        data = await asyncio.to_thread(
            vieneu_synthesize_pcm, speak_text, voice, rate, pitch, volume
        )
    if not data:
        raise RuntimeError(f"VieNeu không tạo được audio cho chunk {idx}")
    save_cached(key, data)
    print(f"  [VieNeu] chunk {idx} completed")
    return idx, data


async def encode_pcm_to_mp3(source: AsyncGenerator[bytes, None]) -> AsyncGenerator[bytes, None]:
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(64)
    encoder.set_in_sample_rate(OUTPUT_SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    async for pcm in source:
        if pcm:
            encoded = encoder.encode(pcm)
            if encoded:
                yield bytes(encoded)
    tail = encoder.flush()
    if tail:
        yield bytes(tail)


# ── True Streaming Generator ───────────────────────────────────────────────────

async def stream_ordered_audio(
    chunks: List[str],
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    auto_trans: bool = True,
    max_concurrent: int = 4,
) -> AsyncGenerator[bytes, None]:
    """
    Starts up to `max_concurrent` synthesis tasks in parallel.
    Yields completed MP3 bytes IN ORDER so the browser receives a valid
    concatenated MP3 stream and can begin playback immediately.

    Flow:
        chunks [0..N]
        │
        ├─ asyncio.Semaphore(4) limits concurrent Edge-TTS connections
        │
        ├─ All tasks launched at once via asyncio.gather
        │
        └─ Results sorted by index → streamed in order to browser
    """
    total = len(chunks)
    sem = asyncio.Semaphore(max_concurrent)

    print(f"Streaming TTS: {total} chunks, voice={voice}, concurrency={max_concurrent}")
    t0 = time.time()

    work_queue: asyncio.Queue = asyncio.Queue(maxsize=max_concurrent * 2)
    result_queue: asyncio.Queue = asyncio.Queue(maxsize=max_concurrent * 2)

    async def producer():
        for item in enumerate(chunks):
            await work_queue.put(item)
        for _ in range(max_concurrent):
            await work_queue.put(None)

    async def worker():
        while True:
            item = await work_queue.get()
            if item is None:
                return
            idx, chunk = item
            try:
                result_idx, data = await synthesize_chunk(
                    sem, idx, chunk, voice, rate, pitch, volume, auto_trans
                )
                await result_queue.put((result_idx, data, None))
            except Exception as exc:
                await result_queue.put((idx, b"", exc))

    producer_task = asyncio.create_task(producer())
    workers = [asyncio.create_task(worker()) for _ in range(max_concurrent)]

    pending_results: Dict[int, bytes] = {}
    next_to_yield = 0
    received = 0

    while received < total:
        idx, data, error = await result_queue.get()
        received += 1
        if error is not None:
            producer_task.cancel()
            for task in workers:
                task.cancel()
            await asyncio.gather(producer_task, *workers, return_exceptions=True)
            raise error
        pending_results[idx] = data

        # Yield any consecutive completed chunks
        while next_to_yield in pending_results:
            chunk_data = pending_results.pop(next_to_yield)
            if chunk_data:
                yield chunk_data
            next_to_yield += 1
            elapsed = time.time() - t0
            print(f"  chunk {next_to_yield}/{total} streamed ({elapsed:.1f}s elapsed)")

    elapsed = time.time() - t0
    print(f"All {total} chunks done in {elapsed:.1f}s")


# ── Voice loading ──────────────────────────────────────────────────────────────

async def load_voices():
    global VOICES_CACHE
    try:
        raw = await asyncio.wait_for(edge_tts.list_voices(), timeout=12)
        result = []
        for v in raw:
            locale = v.get("Locale", "")
            short = v.get("ShortName", "")
            gender = v.get("Gender", "Unknown")
            parts = short.split("-")
            name = parts[-1].replace("Neural", "") if len(parts) >= 3 else short
            lang = LANGUAGE_MAP.get(locale, {
                "name": locale, "flag": "🌐", "popular": False,
                "gtts_lang": locale.split("-")[0], "sample": f"Hello from {name}."
            })
            is_vi = locale.startswith("vi-")
            result.append({
                "id": short, "name": name,
                "displayName": f"{name} ({'Nữ' if gender=='Female' else 'Nam'})",
                "gender": gender, "locale": locale,
                "languageName": lang["name"], "flag": lang["flag"],
                "gttsLang": lang.get("gtts_lang", "en"),
                "popular": lang.get("popular", False) or is_vi,
                "isVietnamese": is_vi,
                "sampleText": lang.get("sample", f"Hello from {name}"),
                "fullInfo": v.get("FriendlyName", short)
            })
        result.sort(key=lambda x: (not x["isVietnamese"], not x["popular"], x["locale"], x["name"]))
        VOICES_CACHE = result
        print(f"Loaded {len(VOICES_CACHE)} voices.")
    except Exception as e:
        print(f"Error loading voices: {e}")
        if not VOICES_CACHE:
            VOICES_CACHE = [dict(v) for v in FALLBACK_VOICES]


async def load_local_voices():
    global VOICES_CACHE
    await asyncio.to_thread(load_custom_voice_registry)
    with CUSTOM_VOICE_REGISTRY_LOCK:
        custom_voices = [
            _custom_voice_public(meta)
            for meta in sorted(
                CUSTOM_VOICE_METADATA.values(),
                key=lambda value: str(value.get("createdAt", "")),
                reverse=True,
            )
        ]
    VOICES_CACHE = custom_voices + [dict(voice) for voice in FALLBACK_VOICES]


@app.on_event("startup")
async def startup():
    await load_local_voices()


# ── API Endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "provider": "vieneu-v3-local",
        "voices": len(VOICES_CACHE),
        "custom_voices": len(CUSTOM_VOICE_METADATA),
        "models_loaded": 1 if VIENEU_ENGINE is not None else 0,
        "cached_chunks": len(RAM_CACHE),
    }


@app.get("/api/voices")
async def get_voices(
    locale: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    search: Optional[str] = Query(None)
):
    if not VOICES_CACHE:
        await load_local_voices()
    filtered = VOICES_CACHE
    if locale:
        filtered = [v for v in filtered if v["locale"].lower() == locale.lower()]
    if gender:
        filtered = [v for v in filtered if v["gender"].lower() == gender.lower()]
    if search:
        s = search.lower()
        filtered = [v for v in filtered
                    if s in v["name"].lower() or s in v["id"].lower()
                    or s in v["languageName"].lower() or s in v["locale"].lower()]
    return {"total": len(filtered), "voices": filtered}


@app.get("/api/custom-voices")
async def get_custom_voices():
    with CUSTOM_VOICE_REGISTRY_LOCK:
        voices = [_custom_voice_public(meta) for meta in CUSTOM_VOICE_METADATA.values()]
    voices.sort(key=lambda value: str(value.get("createdAt", "")), reverse=True)
    return {"total": len(voices), "voices": voices}


async def _read_custom_voice_uploads(
    files: List[UploadFile],
) -> tuple[List[Dict[str, Any]], List[str]]:
    if not files or len(files) > MAX_CUSTOM_VOICE_FILES:
        raise HTTPException(400, f"Mỗi lần hãy tải từ 1 đến {MAX_CUSTOM_VOICE_FILES} file")
    allowed_extensions = {".wav", ".mp3", ".flac", ".ogg"}
    candidates: List[Dict[str, Any]] = []
    rejected: List[str] = []
    for upload in files:
        filename = Path(upload.filename or "audio").name
        try:
            if Path(filename).suffix.lower() not in allowed_extensions:
                raise ValueError(f"{filename}: chỉ hỗ trợ WAV, MP3, FLAC hoặc OGG")
            data = await upload.read(MAX_CUSTOM_VOICE_FILE_BYTES + 1)
            if len(data) > MAX_CUSTOM_VOICE_FILE_BYTES:
                raise ValueError(f"{filename}: dung lượng vượt quá 25 MB")
            if not data:
                raise ValueError(f"{filename}: file rỗng")
            candidates.append(await asyncio.to_thread(_prepare_voice_sample, data, filename))
        except ValueError as exc:
            rejected.append(str(exc))
        finally:
            await upload.close()
    if not candidates:
        message = "\n".join(rejected[:5]) or "Không có file mẫu hợp lệ"
        raise HTTPException(400, message)
    return candidates, rejected


@app.post("/api/custom-voices", status_code=201)
async def create_custom_voice(
    name: str = Form(...),
    gender: str = Form("Unknown"),
    consent: bool = Form(...),
    files: List[UploadFile] = File(...),
):
    name = unicodedata.normalize("NFC", name).strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    if not consent:
        raise HTTPException(400, "Bạn cần xác nhận có quyền sử dụng giọng nói này")
    if not 1 <= len(name) <= 60:
        raise HTTPException(400, "Tên giọng phải có từ 1 đến 60 ký tự")
    if gender not in {"Female", "Male", "Unknown"}:
        raise HTTPException(400, "Giới tính giọng không hợp lệ")
    with CUSTOM_VOICE_REGISTRY_LOCK:
        if any(str(meta.get("name", "")).casefold() == name.casefold()
               for meta in CUSTOM_VOICE_METADATA.values()):
            raise HTTPException(409, "Tên giọng này đã tồn tại")

    candidates, rejected = await _read_custom_voice_uploads(files)

    voice_id = f"custom:{uuid.uuid4().hex}"
    folder = _custom_voice_folder(voice_id)
    try:
        async with VIENEU_SYNTH_LOCK:
            meta = await asyncio.to_thread(
                _enroll_custom_voice, voice_id, name, gender, candidates
            )
        await load_local_voices()
    except Exception as exc:
        if folder.is_dir():
            shutil.rmtree(folder)
        print(f"Custom voice enrollment failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            500,
            "Không thể tạo giọng. Hãy kiểm tra kết nối tải model lần đầu và chất lượng file mẫu.",
        ) from exc

    return {
        "voice": _custom_voice_public(meta),
        "selectedSample": meta["selectedSample"],
        "acceptedFiles": len(candidates),
        "rejectedFiles": rejected,
    }


@app.post("/api/custom-voices/{voice_id}/samples")
async def add_custom_voice_samples(
    voice_id: str,
    consent: bool = Form(...),
    files: List[UploadFile] = File(...),
):
    if not consent:
        raise HTTPException(400, "Bạn cần xác nhận có quyền sử dụng giọng nói này")
    try:
        folder = _custom_voice_folder(voice_id)
    except ValueError as exc:
        raise HTTPException(404, "Không tìm thấy giọng tùy chỉnh") from exc
    with CUSTOM_VOICE_REGISTRY_LOCK:
        current_meta = CUSTOM_VOICE_METADATA.get(voice_id)
    if current_meta is None or not folder.is_dir():
        raise HTTPException(404, "Không tìm thấy giọng tùy chỉnh")

    current_count = int(current_meta.get("sourceFileCount", 1))
    if current_count + len(files) > MAX_CUSTOM_VOICE_SAMPLES:
        raise HTTPException(
            400,
            f"Profile đang có {current_count} mẫu; chỉ được lưu tối đa "
            f"{MAX_CUSTOM_VOICE_SAMPLES} mẫu",
        )
    candidates, rejected = await _read_custom_voice_uploads(files)
    try:
        async with VIENEU_SYNTH_LOCK:
            meta = await asyncio.to_thread(_refine_custom_voice, voice_id, candidates)
        await load_local_voices()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        print(f"Custom voice refinement failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            500,
            "Không thể cập nhật profile. Dữ liệu giọng cũ vẫn được giữ nguyên.",
        ) from exc

    return {
        "voice": _custom_voice_public(meta),
        "selectedSample": meta["selectedSample"],
        "acceptedFiles": len(candidates),
        "rejectedFiles": rejected,
        "totalSamples": meta["sourceFileCount"],
        "usedSamples": meta["usedSampleCount"],
    }


@app.get("/api/custom-voices/{voice_id}/reference")
async def get_custom_voice_reference(voice_id: str):
    try:
        reference_path = _custom_voice_folder(voice_id) / "reference.wav"
    except ValueError as exc:
        raise HTTPException(404, "Không tìm thấy giọng tùy chỉnh") from exc
    if voice_id not in CUSTOM_VOICE_METADATA or not reference_path.is_file():
        raise HTTPException(404, "Không tìm thấy file mẫu")
    return FileResponse(reference_path, media_type="audio/wav", filename="voice-reference.wav")


@app.delete("/api/custom-voices/{voice_id}")
async def delete_custom_voice(voice_id: str):
    try:
        folder = _custom_voice_folder(voice_id)
    except ValueError as exc:
        raise HTTPException(404, "Không tìm thấy giọng tùy chỉnh") from exc
    with CUSTOM_VOICE_REGISTRY_LOCK:
        meta = CUSTOM_VOICE_METADATA.get(voice_id)
    if meta is None or not folder.is_dir():
        raise HTTPException(404, "Không tìm thấy giọng tùy chỉnh")

    async with VIENEU_SYNTH_LOCK:
        shutil.rmtree(folder)
    await load_local_voices()
    return {"deleted": True, "voice_id": voice_id}


# ── Pydantic models ────────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str = "vieneu-ngoc-linh"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    emotion: str = Field(default="neutral", pattern=r"^(neutral|joyful|sad|angry|fearful|surprised|tender|tense|whisper)$")
    emotion_intensity: float = Field(default=0.6, ge=0, le=1)
    auto_transliterate: bool = True


class DramaSegment(BaseModel):
    id: int
    text: str
    role: str = "narrator"
    speaker: str = "Người dẫn chuyện"
    voice: str = "vieneu-ngoc-linh"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    emotion: str = "neutral"
    emotion_intensity: float = Field(default=0.35, ge=0, le=1)
    delivery_hint: str = "Tự nhiên"
    pause_after_ms: int = Field(default=180, ge=0, le=2000)


class DramaTTSRequest(BaseModel):
    segments: List[DramaSegment]


class ParseRequest(BaseModel):
    text: str
    narrator_voice: str = "vieneu-ngoc-linh"
    male_voice: str = "vieneu-thanh-binh"
    female_voice: str = "vieneu-ngoc-linh"


EMOTION_PRESETS: Dict[str, Dict[str, Any]] = {
    "neutral": {"label": "Tự nhiên", "rate": 0, "pitch": 0, "volume": 0, "pause": 180},
    "joyful": {"label": "Vui vẻ", "rate": 10, "pitch": 10, "volume": 4, "pause": 140},
    "sad": {"label": "Buồn", "rate": -14, "pitch": -8, "volume": -6, "pause": 360},
    "angry": {"label": "Tức giận", "rate": 8, "pitch": 5, "volume": 12, "pause": 220},
    "fearful": {"label": "Sợ hãi", "rate": 7, "pitch": 12, "volume": -2, "pause": 260},
    "surprised": {"label": "Ngạc nhiên", "rate": 6, "pitch": 15, "volume": 5, "pause": 260},
    "tender": {"label": "Dịu dàng", "rate": -10, "pitch": 3, "volume": -5, "pause": 280},
    "tense": {"label": "Căng thẳng", "rate": -5, "pitch": 5, "volume": 2, "pause": 320},
    "whisper": {"label": "Thì thầm", "rate": -12, "pitch": -3, "volume": -18, "pause": 300},
}


def _expression_controls(emotion: str, intensity: float) -> Dict[str, Any]:
    """Convert a semantic emotion into conservative VieNeu post-processing controls."""
    preset = EMOTION_PRESETS.get(emotion, EMOTION_PRESETS["neutral"])
    strength = max(0.0, min(1.0, intensity))
    return {
        "emotion": emotion if emotion in EMOTION_PRESETS else "neutral",
        "rate": f'{round(preset["rate"] * strength):+d}%',
        "pitch": f'{round(preset["pitch"] * strength):+d}Hz',
        "volume": f'{round(preset["volume"] * strength):+d}%',
        "pause_after_ms": round(100 + (preset["pause"] - 100) * strength),
    }


def _merge_control(base: str, expression: str, suffix: str, low: int, high: int) -> str:
    base_match = re.search(r"[+-]?\d+", base or "0")
    expression_match = re.search(r"[+-]?\d+", expression or "0")
    value = (int(base_match.group()) if base_match else 0) + (
        int(expression_match.group()) if expression_match else 0
    )
    return f"{max(low, min(high, value)):+d}{suffix}"


INLINE_CUES: Dict[str, Dict[str, Any]] = {
    "normal": {"emotion": "neutral"}, "neutral": {"emotion": "neutral"},
    "laughs": {"emotion": "joyful", "vocal": "Ha ha ha!", "pause": 160},
    "chuckles": {"emotion": "joyful", "vocal": "Hừm... ha ha!", "pause": 130},
    "sighs": {"emotion": "sad", "vocal": "Ha...", "pause": 320},
    "whispers": {"emotion": "whisper"}, "whisper": {"emotion": "whisper"},
    "excited": {"emotion": "joyful"}, "joyful": {"emotion": "joyful"},
    "angry": {"emotion": "angry"}, "sad": {"emotion": "sad"},
    "fearful": {"emotion": "fearful"}, "surprised": {"emotion": "surprised"},
    "tender": {"emotion": "tender"}, "tense": {"emotion": "tense"},
}


def parse_inline_cues(text: str, default_emotion: str, intensity: float) -> List[Dict[str, Any]]:
    pattern = re.compile(r"\[\s*([a-z_]+)(?:\s*=\s*(\d+)\s*(?:ms)?)?\s*\]", re.IGNORECASE)
    segments: List[Dict[str, Any]] = []
    emotion, cursor = default_emotion, 0
    for match in pattern.finditer(text):
        prose = clean_text_for_tts(text[cursor:match.start()])
        if prose:
            segments.append({"text": prose, "emotion": emotion, "pause": 0})
        cue = match.group(1).lower()
        if cue == "pause":
            segments.append({"text": "", "emotion": emotion,
                             "pause": max(50, min(3000, int(match.group(2) or 500)))})
        elif cue in INLINE_CUES:
            spec = INLINE_CUES[cue]
            emotion = spec["emotion"]
            if spec.get("vocal"):
                segments.append({"text": spec["vocal"], "emotion": emotion,
                                 "pause": spec.get("pause", 0)})
        cursor = match.end()
    prose = clean_text_for_tts(text[cursor:])
    if prose:
        segments.append({"text": prose, "emotion": emotion, "pause": 0})
    if not segments:
        cleaned = clean_text_for_tts(text)
        if cleaned:
            segments.append({"text": cleaned, "emotion": default_emotion, "pause": 0})
    for segment in segments:
        segment["intensity"] = intensity
    return segments


class VisualScriptRequest(BaseModel):
    story: str = Field(..., min_length=20, max_length=120000)
    image_style: str = Field(default="cinematic digital illustration", max_length=500)
    image_count: int = Field(default=12, ge=1, le=100)
    aspect_ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16|1:1|4:3|3:4)$")
    language: str = Field(default="vi", pattern=r"^(vi|en)$")
    extra_requirements: str = Field(default="", max_length=2000)


MALE_MARKERS = {"anh", "ông", "bố", "ba", "cha", "chú", "cậu", "em trai", "con trai",
                "chàng", "hắn", "nam", "thầy", "luật sư", "hoàng tử", "vua"}
FEMALE_MARKERS = {"chị", "cô", "bà", "mẹ", "má", "dì", "nàng", "cô gái", "em gái",
                  "con gái", "nữ", "công chúa", "hoàng hậu"}
MALE_GIVEN_NAMES = {"huy", "khang", "minh", "bình", "sơn", "trí", "tùng", "dũng", "nam",
                    "phong", "quân", "tuấn", "long", "thành", "đức", "hiếu", "khôi"}
FEMALE_GIVEN_NAMES = {"an", "linh", "đoan", "duyên", "anh", "thanh", "ly", "lan", "mai",
                      "hương", "thảo", "trang", "ngọc", "vy", "nhi", "hà", "yến"}
SPEECH_VERBS = ("nói|hỏi|đáp|đọc|trả lời|lên tiếng|thì thầm|thốt lên|kêu|gọi|quát|hét|"
                "cười|tiếp lời|ngập ngừng|lẩm bẩm|nhắc|bảo")
QUOTE_RE = re.compile(r'([“\"])(.+?)(?:[”\"])')


def _clean_speaker(value: str) -> str:
    value = re.sub(r'^[\-–—\s]+|[\s,.:;!?\-–—]+$', '', value).strip()
    value = re.sub(rf'\s+(?:{SPEECH_VERBS})$', '', value, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', value)[:50]


def _infer_gender(speaker: str, context: str = "") -> Optional[str]:
    """Infer a voice class conservatively from Vietnamese names and address terms."""
    speaker_sample = unicodedata.normalize("NFC", speaker).casefold()
    context_sample = unicodedata.normalize("NFC", context).casefold()

    def contains(sample: str, marker: str) -> bool:
        return bool(re.search(rf'(?<!\w){re.escape(marker)}(?!\w)', sample, re.UNICODE))

    # The speaker label is stronger evidence than surrounding narration.
    for sample in (speaker_sample, context_sample):
        for marker in FEMALE_MARKERS:
            if contains(sample, marker):
                return "female"
        for marker in MALE_MARKERS:
            if contains(sample, marker):
                return "male"
    name_words = re.findall(r"[\wÀ-ỹ]+", speaker.casefold(), re.UNICODE)
    if name_words:
        given_name = name_words[-1]
        if given_name in FEMALE_GIVEN_NAMES and given_name not in MALE_GIVEN_NAMES:
            return "female"
        if given_name in MALE_GIVEN_NAMES and given_name not in FEMALE_GIVEN_NAMES:
            return "male"
    return None


def _speaker_near_quote(line: str, quote_start: int, quote_end: int) -> Optional[str]:
    """Find attribution on either side of a quote (Lan nói: “...” / “...”, Lan đáp)."""
    before, after = line[:quote_start], line[quote_end:]
    patterns = (
        rf'([\wÀ-ỹ][\wÀ-ỹ .]{{0,38}}?)\s+(?:{SPEECH_VERBS})\s*[:,]?\s*$',
        rf'^\s*[,;:—–-]*\s*([\wÀ-ỹ][\wÀ-ỹ .]{{0,38}}?)\s+(?:{SPEECH_VERBS})\b',
        rf'^\s*[,;:—–-]*\s*(?:{SPEECH_VERBS})\s+([\wÀ-ỹ][\wÀ-ỹ .]{{0,38}}?)\b',
    )
    for area, pattern in ((before, patterns[0]), (after, patterns[1]), (after, patterns[2])):
        match = re.search(pattern, area, re.IGNORECASE)
        if match:
            candidate = _clean_speaker(match.group(1))
            if candidate and len(candidate.split()) <= 5:
                return candidate
    return None


def _voice_for_role(role: str, req: ParseRequest) -> str:
    return req.male_voice if role == "male" else req.female_voice if role == "female" else req.narrator_voice


def _discover_cast(lines: List[str]) -> tuple[Dict[str, Dict[str, Optional[str]]], Optional[str]]:
    """Discover named characters, aliases and the likely first-person narrator."""
    cast: Dict[str, Dict[str, Optional[str]]] = {}
    title_pattern = re.compile(
        r'\b(anh|ông|chú|cậu|bố|ba|cha|thầy|cô|chị|bà|mẹ|má|dì|nàng)\s+'
        r'([\wÀ-ỹ]+(?:\s+[\wÀ-ỹ]+){0,3})', re.UNICODE
    )
    for line in lines:
        for title, name in title_pattern.findall(line):
            name_parts = []
            for part in name.split():
                if not part[0].isupper():
                    break
                name_parts.append(part)
            if not name_parts:
                continue
            full_name = _clean_speaker(" ".join(name_parts))
            role = _infer_gender(title)
            key = full_name.casefold()
            cast[key] = {"name": full_name, "role": role}
            # The final name is the common short alias in Vietnamese fiction.
            cast.setdefault(full_name.split()[-1].casefold(), cast[key])
            if len(full_name.split()) >= 2:
                cast.setdefault(" ".join(full_name.split()[-2:]).casefold(), cast[key])

    female_names = []
    male_names = []
    unique_people = set()
    for info in cast.values():
        identity = str(info["name"]).casefold()
        if identity in unique_people:
            continue
        unique_people.add(identity)
        (female_names if info["role"] == "female" else male_names).append(info)

    narrator_key: Optional[str] = None
    joined = "\n".join(lines[:80]).casefold()
    # In first-person romance prose, the named woman/man introduced in the
    # inheritance or self-reference context is usually the narrator.
    for key, info in cast.items():
        name = str(info["name"]).casefold()
        if (f"cô {name}" in joined or f"anh {name}" in joined) and re.search(r'\btôi\b', joined):
            if info["role"] == "female" and len(female_names) == 1:
                narrator_key = key
                break
            if info["role"] == "male" and not female_names and len(male_names) == 1:
                narrator_key = key
                break
    return cast, narrator_key


def _resolve_actor(line: str, cast: Dict[str, Dict[str, Optional[str]]],
                   narrator_key: Optional[str], last_named: Optional[str]) -> Optional[str]:
    """Resolve the grammatical actor of narration to a known cast member."""
    plain = line.strip().strip('“”"')
    folded = plain.casefold()
    for key in sorted(cast, key=len, reverse=True):
        if re.match(rf'^{re.escape(key)}(?=\W|$)', folded, re.UNICODE):
            return key
    generic = re.match(r'^(luật sư|bác sĩ|nhân viên|quản lý)\b', folded)
    if generic:
        key = generic.group(1)
        cast.setdefault(key, {"name": key.title(), "role": _infer_gender(key)})
        return key
    generic_speech = re.search(r'\b(luật sư|bác sĩ|quản lý)\b.*\b(?:' + SPEECH_VERBS + r')\b', folded)
    if generic_speech:
        key = generic_speech.group(1)
        cast.setdefault(key, {"name": key.title(), "role": "male" if key == "luật sư" else None})
        return key
    if "người đàn ông" in folded:
        male_people = [key for key, info in cast.items() if info.get("role") == "male" and " " in key]
        if male_people:
            return max(male_people, key=len)
    if line.rstrip().endswith((':', '：')) and re.search(r'\banh\b', folded):
        if last_named and cast.get(last_named, {}).get("role") == "male":
            return last_named
        male_people = [key for key, info in cast.items() if info.get("role") == "male" and " " in key]
        if male_people:
            return max(male_people, key=len)
    subject = re.match(r'^([\wÀ-ỹ]+(?:\s+[\wÀ-ỹ]+){0,3}?)(?:\s+|[:,])', plain, re.UNICODE)
    if not subject:
        return None
    candidate = _clean_speaker(subject.group(1)).casefold()
    if candidate == "tôi":
        return narrator_key
    if candidate in cast:
        return candidate
    first = candidate.split()[0]
    if first in {"anh", "hắn", "chàng", "ông"}:
        if last_named and cast.get(last_named, {}).get("role") == "male":
            return last_named
        return next((key for key, info in cast.items() if info.get("role") == "male"), None)
    if first in {"cô", "chị", "nàng", "bà"}:
        if last_named and cast.get(last_named, {}).get("role") == "female":
            return last_named
        return narrator_key or next((key for key, info in cast.items() if info.get("role") == "female"), None)
    return None


# ── /api/tts  (True Streaming for 30k–50k word texts) ─────────────────────────

@app.post("/api/tts")
async def generate_tts(req: TTSRequest):
    if not _known_voice(req.voice):
        raise HTTPException(400, "Giọng đọc đã chọn không tồn tại hoặc đã bị xóa")
    expressive_segments = parse_inline_cues(req.text, req.emotion, req.emotion_intensity)
    text = clean_text_for_tts(req.text)
    if not expressive_segments:
        raise HTTPException(400, "Văn bản không được để trống")

    chunks = [chunk for segment in expressive_segments
              for chunk in prepare_tts_chunks(segment["text"], max_chars=1400)]
    word_count = len(text.split())
    print(f"TTS request: {len(text):,} chars / ~{word_count:,} words -> {len(chunks)} chunks")

    # Determine concurrency: more chunks = slightly higher concurrency (capped at 5)
    concurrency = 2
    async def mp3_stream():
        async def expressive_pcm_stream():
            for segment in expressive_segments:
                segment_chunks = prepare_tts_chunks(segment["text"], max_chars=1400)
                if segment_chunks:
                    expression = _expression_controls(segment["emotion"], segment["intensity"])
                    rate = _merge_control(req.rate, expression["rate"], "%", -50, 100)
                    pitch = _merge_control(req.pitch, expression["pitch"], "Hz", -50, 50)
                    volume = _merge_control(req.volume, expression["volume"], "%", -50, 50)
                    async for pcm in stream_ordered_audio(
                        segment_chunks, req.voice, rate, pitch, volume,
                        req.auto_transliterate, max_concurrent=concurrency
                    ):
                        yield pcm
                if segment["pause"]:
                    yield b"\x00\x00" * round(OUTPUT_SAMPLE_RATE * segment["pause"] / 1000)
        pcm_stream = expressive_pcm_stream()
        async for mp3_bytes in encode_pcm_to_mp3(pcm_stream):
            yield mp3_bytes

    job_id = uuid.uuid4().hex

    return StreamingResponse(
        persist_audio_stream(mp3_stream(), job_id),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="tts_output.mp3"',
            "X-Chunks-Count": str(len(chunks)),
            "X-Word-Count": str(word_count),
            "X-Audio-Job-Id": job_id,
            "X-Emotion": req.emotion,
            "X-Emotion-Intensity": str(req.emotion_intensity),
            "X-Expression-Segments": str(len(expressive_segments)),
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
        }
    )


# ── /api/parse-dialogue ────────────────────────────────────────────────────────

@app.post("/api/parse-dialogue")
async def parse_dialogue(req: ParseRequest):
    text = req.text.strip()
    if not text:
        return {"total": 0, "segments": []}

    # Pass 1 builds a cast from the complete document before assigning voices.
    prepared_lines: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r'[-*_]{3,}', line):
            continue
        line = re.sub(r'^(?:\*\*|__)(.*)(?:\*\*|__)$', r'\1', line).strip()
        line = re.sub(r'^#{1,6}\s*', '', line).strip()
        if not line:
            continue
        prepared_lines.append(line)

    cast, narrator_key = _discover_cast(prepared_lines)
    for line in prepared_lines:
        label = re.match(r'^([^:“”"]{1,40}):\s*(.+)$', line)
        if label:
            speaker = _clean_speaker(label.group(1))
            key = narrator_key if speaker.casefold() == "tôi" else speaker.casefold()
            if key:
                cast.setdefault(key, {"name": speaker, "role": _infer_gender(speaker)})
        for quote in QUOTE_RE.finditer(line):
            speaker = _speaker_near_quote(line, quote.start(), quote.end())
            if speaker:
                key, role = speaker.casefold(), _infer_gender(speaker)
                cast.setdefault(key, {"name": speaker, "role": role})
                if role and not cast[key]["role"]:
                    cast[key]["role"] = role

    segments: List[Dict[str, Any]] = []
    last_speaker: Optional[str] = None
    last_named: Optional[str] = narrator_key
    pending_speaker: Optional[str] = None
    recent_speakers: List[str] = []

    def add_segment(content: str, role: str, speaker: str) -> None:
        content = content.strip(' \t“”"')
        if content:
            segments.append({"id": len(segments) + 1, "text": content, "speaker": speaker,
                             "role": role, "voice": _voice_for_role(role, req),
                             "rate": "+0%", "pitch": "+0Hz", "volume": "+0%",
                             "emotion": "neutral", "emotion_intensity": 0.35,
                             "delivery_hint": "Đọc tự nhiên", "pause_after_ms": 180})

    # Pass 2 resolves dialogue using both the complete cast and nearby context.
    for line in prepared_lines:
        label = re.match(r'^([^:“”"]{1,40}):\s*(.+)$', line)
        if label and not label.group(1).strip().lower().startswith(("http", "https")):
            speaker = _clean_speaker(label.group(1))
            key = narrator_key if speaker.casefold() == "tôi" else speaker.casefold()
            info = cast.get(key or "", {})
            role = info.get("role") or _infer_gender(speaker)
            if role:
                add_segment(label.group(2), role, str(info.get("name") or speaker))
                last_speaker = key
                last_named = key
                pending_speaker = key
                if last_speaker not in recent_speakers:
                    recent_speakers.append(last_speaker)
                continue

        quotes = list(QUOTE_RE.finditer(line))
        if quotes and all(len(q.group(2).split()) <= 4 for q in quotes):
            attributed = any(_speaker_near_quote(line, q.start(), q.end()) for q in quotes)
            whole_line_quote = len(quotes) == 1 and not line[:quotes[0].start()].strip() and not line[quotes[0].end():].strip()
            if not attributed and not whole_line_quote:
                quotes = []
        if not quotes:
            add_segment(line, "narrator", "Người dẫn chuyện")
            actor = _resolve_actor(line, cast, narrator_key, last_named)
            if actor:
                last_named = actor
                # A colon or speech verb strongly predicts the next paragraph's speaker;
                # an action sentence is a weaker but still useful focus cue.
                pending_speaker = actor
            continue

        cursor = 0
        for quote in quotes:
            if quote.start() > cursor:
                add_segment(line[cursor:quote.start()], "narrator", "Người dẫn chuyện")
            detected = _speaker_near_quote(line, quote.start(), quote.end())
            key = detected.casefold() if detected else pending_speaker
            if not key and last_speaker and len(recent_speakers) == 1:
                key = last_speaker
            elif not key and len(recent_speakers) >= 2:
                key = next((item for item in reversed(recent_speakers) if item != last_speaker), last_speaker)
            info = cast.get(key or "", {})
            role = info.get("role") or (_infer_gender(detected or "") if detected else None)
            speaker = str(info.get("name") or detected or "Nhân vật chưa xác định")
            add_segment(quote.group(2), role or "narrator", speaker)
            if key and role:
                last_speaker = key
                last_named = key
                pending_speaker = key
                if key not in recent_speakers:
                    recent_speakers.append(key)
                    recent_speakers = recent_speakers[-4:]
            cursor = quote.end()
        if cursor < len(line):
            add_segment(line[cursor:], "narrator", "Người dẫn chuyện")

    return {"total": len(segments), "segments": segments}


def _local_ai_assign_roles(batch: List[Dict[str, Any]], cast_context: str) -> List[Dict[str, Any]]:
    """Ask local Ollama to resolve speakers and direct each line's performance."""
    model = os.getenv("OLLAMA_ROLE_MODEL", "qwen3.5:27b").strip()
    ollama_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    compact = [{"id": s["id"], "text": s["text"], "suggested_speaker": s["speaker"],
                "suggested_role": s["role"]} for s in batch]
    prompt = (
        "Bạn là đạo diễn sách nói tiếng Việt. Hãy xác định người thực sự đọc từng đoạn dựa trên "
        "ngữ cảnh, hành động trước lời thoại, đại từ, lượt đối đáp và hồ sơ nhân vật. narrator chỉ dành "
        "cho lời kể; lời thoại phải thuộc nhân vật nếu đủ bằng chứng. Không sửa text hoặc id. "
        "role chỉ được là narrator, male, female. Nếu chưa chắc, giữ suggested_role. Đồng thời chọn đúng "
        "một sắc thái trong neutral, joyful, sad, angry, fearful, surprised, tender, tense, whisper; "
        "intensity từ 0 đến 1. delivery_hint là chỉ dẫn đọc tiếng Việt thật ngắn (tối đa 12 từ), không "
        "thêm lời thoại. Cảm xúc phải dựa vào ngữ cảnh, không phải chỉ một từ đơn lẻ.\n\n"
        f"Hồ sơ/lịch sử gần đây:\n{cast_context[-5000:]}\n\nCác đoạn cần phân vai:\n"
        + json.dumps(compact, ensure_ascii=False)
    )
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"assignments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "id": {"type": "integer"}, "speaker": {"type": "string"},
                "role": {"type": "string", "enum": ["narrator", "male", "female"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "emotion": {"type": "string", "enum": list(EMOTION_PRESETS)},
                "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                "delivery_hint": {"type": "string", "maxLength": 100}
            }, "required": ["id", "speaker", "role", "confidence", "emotion", "intensity", "delivery_hint"]
        }}}, "required": ["assignments"]
    }
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": "Phân vai truyện chính xác và chỉ trả JSON đúng schema."},
                     {"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.1, "num_ctx": 3072,
                    "num_predict": max(256, min(1200, len(batch) * 120))}
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{ollama_host}/api/chat", data=payload, method="POST",
        headers={"Content-Type": "application/json"}
    )
    try:
        timeout = max(30, int(os.getenv("OLLAMA_ROLE_TIMEOUT", "240")))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Không kết nối được Ollama tại localhost:11434") from exc
    except TimeoutError as exc:
        raise RuntimeError("Ollama phân tích quá thời gian cho phép") from exc
    output_text = body.get("message", {}).get("content")
    if not output_text:
        raise RuntimeError("Ollama không trả về kết quả phân vai")
    return json.loads(output_text)["assignments"]


async def _run_ai_parse_job(job_id: str, req: ParseRequest) -> None:
    job = PARSE_JOBS[job_id]
    try:
        heuristic = await parse_dialogue(req)
        base_segments = heuristic["segments"]
        job.update(progress=8, message="Đã tách văn bản, đang lập hồ sơ nhân vật...")
        # A 3B model is more reliable with short batches and rolling context.
        batch_size = max(1, min(16, int(os.getenv("OLLAMA_ROLE_BATCH_SIZE", "8"))))
        recent_context = ""
        for start in range(0, len(base_segments), batch_size):
            batch = base_segments[start:start + batch_size]
            assignments = await asyncio.to_thread(_local_ai_assign_roles, batch, recent_context)
            by_id = {item["id"]: item for item in assignments}
            context_lines = []
            for segment in batch:
                assignment = by_id.get(segment["id"])
                if assignment and assignment.get("confidence", 0) >= 0.45:
                    segment["speaker"] = assignment["speaker"].strip() or segment["speaker"]
                    segment["role"] = assignment["role"]
                    segment["voice"] = _voice_for_role(segment["role"], req)
                if assignment:
                    intensity = float(assignment.get("intensity", 0.35))
                    controls = _expression_controls(str(assignment.get("emotion", "neutral")), intensity)
                    segment.update(controls)
                    segment["emotion_intensity"] = max(0.0, min(1.0, intensity))
                    segment["delivery_hint"] = str(assignment.get("delivery_hint") or controls["emotion"])[:100]
                context_lines.append(f'{segment["speaker"]} ({segment["role"]}): {segment["text"][:100]}')
            recent_context = "\n".join(context_lines[-20:])
            done = min(start + len(batch), len(base_segments))
            job.update(progress=8 + int(88 * done / max(1, len(base_segments))),
                       message=f"AI đã phân tích {done}/{len(base_segments)} đoạn...")
        job.update(status="completed", progress=100, message="Phân vai AI hoàn tất",
                   result={"total": len(base_segments), "segments": base_segments,
                           "analysis_mode": "ai", "fallback": False})
    except Exception as exc:
        # A missing key, quota exhaustion, timeout or malformed response must never
        # prevent the user from continuing with the local parser.
        fallback = await parse_dialogue(req)
        fallback.update(analysis_mode="fast", fallback=True, fallback_reason=str(exc)[:300])
        job.update(status="completed", progress=100,
                   message="AI không khả dụng — đã tự động dùng chế độ Nhanh", result=fallback)


@app.post("/api/parse-dialogue/ai")
async def start_ai_parse(req: ParseRequest):
    job_id = uuid.uuid4().hex
    PARSE_JOBS[job_id] = {"status": "running", "progress": 1,
                          "message": "Đang chuẩn bị phân tích AI...", "result": None}
    asyncio.create_task(_run_ai_parse_job(job_id, req))
    return {"job_id": job_id}


@app.get("/api/parse-dialogue/ai/{job_id}")
async def get_ai_parse(job_id: str):
    job = PARSE_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy tác vụ phân tích")
    return job


# ── /api/tts/drama  (True Streaming for multi-role drama) ─────────────────────

def merge_same_voice(segments: List[DramaSegment], max_chars: int = 2500) -> List[Dict]:
    """Merge consecutive segments with the same voice to reduce API calls."""
    if not segments:
        return []
    merged, current = [], None
    for seg in segments:
        t = seg.text.strip()
        if not t:
            continue
        if (current and current["voice"] == seg.voice
                and current["rate"] == seg.rate
                and current["pitch"] == seg.pitch
                and current["volume"] == seg.volume
                and current["emotion"] == seg.emotion
                and current["pause_after_ms"] == 0
                and len(current["text"]) + len(t) < max_chars):
            current["text"] += f"\n{t}"
        else:
            if current:
                merged.append(current)
            current = {
                "voice": seg.voice, "role": seg.role, "speaker": seg.speaker,
                "text": t, "rate": seg.rate, "pitch": seg.pitch, "volume": seg.volume,
                "emotion": seg.emotion, "pause_after_ms": seg.pause_after_ms
            }
    if current:
        merged.append(current)
    expanded = []
    for group in merged:
        parts = prepare_tts_chunks(group["text"], max_chars=max_chars)
        for part in parts:
            expanded.append({**group, "text": part})
    return expanded


@app.post("/api/tts/drama")
async def generate_drama_tts(req: DramaTTSRequest):
    if not req.segments:
        raise HTTPException(400, "Danh sách đoạn kịch bản không được rỗng")
    missing_voices = sorted({segment.voice for segment in req.segments if not _known_voice(segment.voice)})
    if missing_voices:
        raise HTTPException(400, "Một hoặc nhiều giọng đọc không tồn tại hoặc đã bị xóa")

    # Smart merge consecutive same-voice lines → fewer API calls
    groups = merge_same_voice(req.segments, max_chars=1400)
    print(f"Drama TTS: {len(req.segments)} lines -> {len(groups)} voice groups")

    texts = [g["text"] for g in groups]
    voices = [g["voice"] for g in groups]
    rates = [g["rate"] for g in groups]
    pitches = [g["pitch"] for g in groups]
    volumes = [g["volume"] for g in groups]
    pauses = [g["pause_after_ms"] for g in groups]

    sem = asyncio.Semaphore(4)

    async def drama_stream():
        pending: Dict[int, bytes] = {}
        in_flight = set()
        next_launch = 0
        next_out = 0

        def launch_one(index: int):
            return asyncio.create_task(synthesize_chunk(
                sem, index, texts[index], voices[index], rates[index], pitches[index], volumes[index]
            ))

        while next_launch < len(groups) and len(in_flight) < 4:
            in_flight.add(launch_one(next_launch))
            next_launch += 1

        try:
            while in_flight:
                done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    idx, data = await task
                    pending[idx] = data
                    if next_launch < len(groups):
                        in_flight.add(launch_one(next_launch))
                        next_launch += 1
                while next_out in pending:
                    chunk_data = pending.pop(next_out)
                    if chunk_data:
                        yield chunk_data
                        pause_ms = pauses[next_out]
                        if pause_ms > 0:
                            # 48 kHz mono signed 16-bit PCM silence.
                            yield b"\x00\x00" * round(OUTPUT_SAMPLE_RATE * pause_ms / 1000)
                    next_out += 1
        finally:
            for task in in_flight:
                task.cancel()
            await asyncio.gather(*in_flight, return_exceptions=True)

    job_id = uuid.uuid4().hex
    return StreamingResponse(
        persist_audio_stream(encode_pcm_to_mp3(drama_stream()), job_id),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="drama_output.mp3"',
            "X-Original-Segments": str(len(req.segments)),
            "X-Merged-Groups": str(len(groups)),
            "X-Audio-Job-Id": job_id,
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
        }
    )


@app.get("/api/audio-jobs/{job_id}")
async def get_audio_job(job_id: str):
    job = OUTPUT_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy tác vụ âm thanh")
    return {"job_id": job_id, "status": job["status"], "bytes": job.get("bytes", 0)}


@app.get("/api/download/{job_id}")
async def download_audio(job_id: str):
    job = OUTPUT_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy file âm thanh")
    if job["status"] != "ready" or not os.path.isfile(job["path"]):
        raise HTTPException(409, "File MP3 vẫn đang được xử lý")
    return FileResponse(
        job["path"],
        media_type="audio/mpeg",
        filename=f"TTS_Audio_{job_id[:8]}.mp3",
    )


# ── Static files & root ────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
