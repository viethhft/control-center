"""Story and existing narration to a resumable, local illustrated video."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)


class TaskPaused(Exception):
    pass


class TaskCancelled(Exception):
    pass


def save(path, value):
    temp = path.with_name('.%s.%s.tmp' % (path.name, uuid.uuid4().hex))
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def checkpoint(folder):
    control = folder / 'control.json'
    if not control.exists():
        return
    action = json.loads(control.read_text(encoding='utf-8')).get('action')
    if action == 'pause':
        raise TaskPaused()
    if action == 'cancel':
        raise TaskCancelled()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def command(args, cwd=None, timeout=7200):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-2500:])
    return result.stdout


def duration(path):
    value = json.loads(command([os.getenv('FFPROBE', 'ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], timeout=30))
    if not any(s['codec_type'] == 'audio' for s in value['streams']):
        raise ValueError('File không có luồng audio.')
    seconds = float(value['format']['duration'])
    if not math.isfinite(seconds) or not 0 < seconds <= 14400:
        raise ValueError('Audio phải có thời lượng từ 0 đến 4 giờ.')
    return seconds


def sentences(text):
    return [m.group().strip() for m in re.finditer(r'[^\n.!?…]+(?:[.!?…]+[”’"\x27]*|(?=\n)|$)', text) if m.group().strip()]


def captions(folder, project):
    cached = folder / 'captions.json'
    if cached.exists():
        return json.loads(cached.read_text(encoding='utf-8'))
    if project['alignment'] == 'estimate':
        parts = sentences(project['story'])
        total = sum(len(p.split()) for p in parts)
        rows, cursor = [], 0.0
        for part in parts:
            end = cursor + project['duration'] * len(part.split()) / total
            rows.append(dict(start=cursor, end=end, text=part))
            cursor = end
    else:
        from faster_whisper import WhisperModel
        model = WhisperModel(os.getenv('WHISPER_MODEL', 'small'), device=os.getenv('WHISPER_DEVICE', 'cpu'), compute_type='int8')
        segments, _ = model.transcribe(str(folder / project['audio']), language='vi', vad_filter=True, word_timestamps=True)
        rows = []
        for segment in segments:
            words = list(segment.words or [])
            for i in range(0, len(words), 12):
                group = words[i:i + 12]
                rows.append(dict(start=group[0].start, end=group[-1].end, text=''.join(w.word for w in group).strip()))
        del model
    if not rows:
        raise ValueError('Không nhận diện được lời kể trong audio.')
    cleaned = []
    for row in rows:
        start = max(0.0, float(row['start']), cleaned[-1]['end'] if cleaned else 0.0)
        end = min(project['duration'], float(row['end']))
        if end > start and row['text'].strip():
            cleaned.append(dict(start=start, end=end, text=row['text']))
    rows = cleaned
    if not rows:
        raise ValueError('Timestamp lời kể không hợp lệ.')
    save(cached, rows)
    return rows


def llm(client, prompt):
    response = client.post(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/chat', json={
        'model': os.getenv('OLLAMA_MODEL', 'qwen3.5:27b'), 'stream': False, 'format': 'json', 'think': False,
        'messages': [{'role': 'system', 'content': 'Bạn là đạo diễn minh họa truyện Việt. Bám sát nguồn; không tự thêm sự kiện. Chỉ trả JSON.'}, {'role': 'user', 'content': prompt}],
        'options': {'temperature': 0, 'num_ctx': 16384, 'num_predict': 4096}, 'keep_alive': '5m'})
    response.raise_for_status()
    return json.loads(response.json()['message']['content'])


def plan(folder, p, rows, client, update):
    target = folder / 'manifest.json'
    if target.exists():
        return json.loads(target.read_text(encoding='utf-8'))
    bible_path = folder / 'bible.json'
    if bible_path.exists():
        bible = json.loads(bible_path.read_text(encoding='utf-8'))
    else:
        # Read all story chunks, retaining identities rather than truncating long stories.
        bible = {'characters': []}
        paragraphs = p['story'].splitlines()
        chunks, chunk = [], ''
        for paragraph in paragraphs:
            if len(chunk) + len(paragraph) > 10000 and chunk:
                chunks.append(chunk)
                chunk = ''
            chunk += paragraph + '\n'
        if chunk.strip():
            chunks.append(chunk)
        for index, chunk in enumerate(chunks):
            update('Phân tích nhân vật %d/%d' % (index + 1, len(chunks)))
            bible = llm(client, 'Cập nhật character bible tích lũy, giữ ID cũ và nhân vật cũ. Phân biệt người kể tôi theo đoạn, hồi tưởng và tuổi trưởng thành. characters: [{id,name,aliases,appearance_en}]. appearance_en chỉ ngoại hình nhìn thấy bằng tiếng Anh, không tính cách; ghi rõ tuổi/trang phục. Nếu nguồn thiếu ngoại hình, chọn thiết kế nhất quán.\nBIBLE: ' + json.dumps(bible, ensure_ascii=False) + '\nNGUỒN: ' + chunk)
            if not isinstance(bible.get('characters'), list):
                raise ValueError('Model trả character bible sai schema.')
        save(bible_path, bible)
    scenes, group = [], []
    for row in rows:
        group.append(row)
        if group[-1]['end'] - group[0]['start'] >= p['shot_seconds']:
            scenes.append(group)
            group = []
    if group:
        scenes.append(group)
    result = []
    for index, group in enumerate(scenes):
        update('Lập cảnh %d/%d' % (index + 1, len(scenes)))
        sid = 'scene_%04d' % index
        cache = folder / (sid + '.json')
        if cache.exists():
            scene = json.loads(cache.read_text(encoding='utf-8'))
        else:
            excerpt = ' '.join(x['text'] for x in group)
            scene = llm(client, 'Tạo một khung hình cho đoạn lời kể. JSON {"characters":["id"],"prompt_en":"mô tả cảnh cụ thể bằng tiếng Anh","motion":"push|pull|left|right"}. Tối đa 3 nhân vật nhìn thấy; giữ ID bible. Giải nghĩa thành ngữ tiếng Việt theo ngữ cảnh, không vẽ ẩn dụ theo nghĩa đen. Không vẽ chữ. Nhận diện hồi tưởng và trang phục đúng thời điểm.\nBIBLE: ' + json.dumps(bible, ensure_ascii=False) + '\nCẢNH TRƯỚC: ' + json.dumps(result[-1:] , ensure_ascii=False) + '\nLỜI KỂ: ' + excerpt)
            ids = {c['id'] for c in bible['characters']}
            if not isinstance(scene.get('characters'), list) or any(c not in ids for c in scene['characters']) or len(scene['characters']) > 3 or not scene.get('prompt_en'):
                raise ValueError('Cảnh %s có nhân vật/prompt không hợp lệ. Thử tiếp tục để tạo lại.' % sid)
            scene.update(id=sid, narration=excerpt)
            save(cache, scene)
        scene['start'] = 0 if index == 0 else result[-1]['end']
        scene['end'] = p['duration'] if index == len(scenes) - 1 else scenes[index + 1][0]['start']
        result.append(scene)
    manifest = dict(characters=bible['characters'], scenes=result, visual_mode=p.get('visual_mode', 'hand_drawn_whiteboard'), style=p['style'], width=p['width'], height=p['height'])
    save(target, manifest)
    return manifest


def workflow(prompt, references, width, height, seed):
    edit = bool(references)
    nodes = {
        '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': os.getenv('QWEN_EDIT_MODEL', 'qwen_image_edit_2509_fp8_e4m3fn.safetensors') if edit else os.getenv('QWEN_IMAGE_MODEL', 'qwen_image_distill_full_fp8_e4m3fn.safetensors'), 'weight_dtype': 'default'}},
        '2': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': os.getenv('QWEN_ENCODER', 'qwen_2.5_vl_7b_fp8_scaled.safetensors'), 'type': 'qwen_image', 'device': 'default'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': os.getenv('QWEN_VAE', 'qwen_image_vae.safetensors')}},
        '4': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['1', 0], 'shift': 3}},
        '7': {'class_type': 'EmptySD3LatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '8': {'class_type': 'KSampler', 'inputs': {'seed': seed, 'steps': 12, 'cfg': 1.0, 'sampler_name': 'res_multistep', 'scheduler': 'simple', 'denoise': 1, 'model': ['4', 0], 'positive': ['5', 0], 'negative': ['6', 0], 'latent_image': ['7', 0]}},
        '9': {'class_type': 'VAEDecode', 'inputs': {'samples': ['8', 0], 'vae': ['3', 0]}},
        '10': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'story_video', 'images': ['9', 0]}}
    }
    for number, text in [('5', prompt), ('6', 'text, watermark, duplicate person, distorted face')]:
        inputs = {'clip': ['2', 0], 'prompt' if edit else 'text': text}
        if edit:
            inputs['vae'] = ['3', 0]
            for i, ref in enumerate(references):
                node = str(20 + i)
                nodes[node] = {'class_type': 'LoadImage', 'inputs': {'image': ref}}
                inputs['image' + str(i + 1)] = [node, 0]
        nodes[number] = {'class_type': 'TextEncodeQwenImageEditPlus' if edit else 'CLIPTextEncode', 'inputs': inputs}
    return nodes


def render_image(client, folder, prompt, refs, width, height):
    settings = [os.getenv(k, '') for k in ('QWEN_IMAGE_MODEL', 'QWEN_EDIT_MODEL', 'QWEN_ENCODER', 'QWEN_VAE')]
    key = digest([prompt, [hashlib.sha256(r.read_bytes()).hexdigest() for r in refs], width, height, settings])
    path = folder / (key + '.png')
    if path.exists():
        return path
    url = os.getenv('COMFYUI_URL', 'http://127.0.0.1:8188')
    receipt = folder / (key + '.receipt.json')
    if receipt.exists():
        pid = json.loads(receipt.read_text())['prompt_id']
    else:
        names = []
        for ref in refs:
            with ref.open('rb') as stream:
                response = client.post(url + '/upload/image', files={'image': (ref.name, stream, 'image/png')})
            response.raise_for_status()
            names.append(response.json()['name'])
        response = client.post(url + '/prompt', json={'prompt': workflow(prompt, names, width, height, int(key[:8], 16))})
        response.raise_for_status()
        payload = response.json()
        if payload.get('node_errors'):
            raise ValueError(str(payload['node_errors']))
        pid = payload['prompt_id']
        save(receipt, {'prompt_id': pid})
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        checkpoint(folder)
        response = client.get(url + '/history/' + pid)
        response.raise_for_status()
        entry = response.json().get(pid, {})
        if entry.get('status', {}).get('status_str') == 'error':
            receipt.unlink(missing_ok=True)
            raise RuntimeError('ComfyUI không render được ảnh: ' + str(entry.get('status'))[:1000])
        for output in entry.get('outputs', {}).values():
            if output.get('images'):
                response = client.get(url + '/view', params=output['images'][0])
                response.raise_for_status()
                temporary = path.with_suffix('.part')
                temporary.write_bytes(response.content)
                from PIL import Image
                with Image.open(temporary) as img:
                    img.verify()
                temporary.replace(path)
                return path
        time.sleep(1)
    raise TimeoutError('ComfyUI quá 60 phút; receipt được giữ để tiếp tục, không gửi render trùng.')


def stamp(t):
    ms = round(t * 1000)
    return '%02d:%02d:%02d,%03d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def compose(folder, p, manifest, rows, update):
    fps, w, h = 24, p['width'], p['height']
    clips = []
    for i, scene in enumerate(manifest['scenes']):
        checkpoint(folder)
        update('Dựng chuyển động %d/%d' % (i + 1, len(manifest['scenes'])))
        # Round absolute boundaries to avoid accumulating timing drift across hundreds of scenes.
        frames = max(1, round(scene['end'] * fps) - round(scene['start'] * fps))
        motion = scene.get('motion', 'push')
        z = '1.10-0.10*on/%d' % frames if motion == 'pull' else '1+0.10*on/%d' % frames
        x = '(iw-iw/zoom)/2'
        if motion in ('left', 'right'):
            z = '1.10'
            x = '(iw-iw/zoom)*' + ('on/%d' % frames if motion == 'right' else '(1-on/%d)' % frames)
        image = folder / scene['image']
        key = digest([hashlib.sha256(image.read_bytes()).hexdigest(), frames, motion, w, h, fps])
        clip = folder / (key + '.mp4')
        if not clip.exists():
            vf = "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,zoompan=z='%s':x='%s':y='(ih-ih/zoom)/2':d=%d:s=%dx%d:fps=%d,setsar=1,format=yuv420p" % (w * 2, h * 2, w * 2, h * 2, z, x, frames, w, h, fps)
            command([os.getenv('FFMPEG', 'ffmpeg'), '-y', '-v', 'error', '-i', image.name, '-vf', vf, '-frames:v', str(frames), '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', key + '.part.mp4'], cwd=folder)
            (folder / (key + '.part.mp4')).replace(clip)
        clips.append(clip)
    (folder / 'concat.txt').write_text(''.join("file '%s'\n" % c.name for c in clips), encoding='utf-8')
    (folder / 'subtitles.srt').write_text('\n'.join('%d\n%s --> %s\n%s\n' % (i + 1, stamp(r['start']), stamp(r['end']), r['text'].replace('\n', ' ')) for i, r in enumerate(rows)), encoding='utf-8')
    update('Ghép audio, phụ đề và xuất MP4')
    args = [os.getenv('FFMPEG', 'ffmpeg'), '-y', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', 'concat.txt', '-i', p['audio']]
    if p.get('music'):
        args += ['-stream_loop', '-1', '-i', p['music'], '-filter_complex', '[1:a]asplit=2[voice][side];[2:a]volume=0.12[bg];[bg][side]sidechaincompress=threshold=0.025:ratio=8[duck];[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]', '-map', '0:v', '-map', '[a]']
    else:
        args += ['-map', '0:v', '-map', '1:a']
    if p['burn_subtitles']:
        args += ['-vf', "subtitles=subtitles.srt:force_style='FontName=Arial,FontSize=22,Outline=1,MarginV=24'", '-c:v', 'libx264', '-preset', 'fast', '-crf', '20']
    else:
        args += ['-c:v', 'copy']
    args += ['-c:a', 'aac', '-b:a', '192k', '-t', str(p['duration']), '-movflags', '+faststart', 'final.part.mp4']
    command(args, cwd=folder)
    (folder / 'final.part.mp4').replace(folder / 'final.mp4')


def run(folder):
    p = json.loads((folder / 'project.json').read_text(encoding='utf-8'))
    def update(message, progress=None, stage=None):
        previous = json.loads((folder / 'status.json').read_text(encoding='utf-8')) if (folder / 'status.json').exists() else {}
        status = {'state': 'running', 'message': message, 'updated_at': time.time(), **previous}
        status['state'], status['message'], status['updated_at'] = 'running', message, time.time()
        if progress is not None:
            status['progress'] = max(0, min(100, int(progress)))
        if stage:
            status['stage'] = stage
        save(folder / 'status.json', status)
        checkpoint(folder)
    try:
        update('Kiểm tra Ollama và ComfyUI', 2, 'prepare')
        with httpx.Client(timeout=15) as probe:
            for address in (os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/tags', os.getenv('COMFYUI_URL', 'http://127.0.0.1:8188') + '/system_stats'):
                try:
                    probe.get(address).raise_for_status()
                except httpx.HTTPError as exc:
                    service = 'Ollama' if '/api/tags' in address else 'ComfyUI'
                    raise RuntimeError(
                        f'Không kết nối được {service} tại {address}. '
                        'Khởi động dịch vụ trước, kiểm tra COMFYUI_URL/OLLAMA_URL trong .env, '
                        'rồi nhấn Tiếp tục.'
                    ) from exc
        update('Đo audio và căn thời gian lời kể', 8, 'audio')
        p['duration'] = duration(folder / p['audio'])
        save(folder / 'project.json', p)
        rows = captions(folder, p)
        update('Hoàn tất căn thời gian lời kể', 18, 'analysis')
        with httpx.Client(timeout=900) as client:
            manifest = plan(folder, p, rows, client, update)
            update('Đã có storyboard và character bible', 30, 'references')
            try:
                client.post(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/generate', json={'model': os.getenv('OLLAMA_MODEL', 'qwen3.5:27b'), 'keep_alive': 0}, timeout=30)
            except httpx.HTTPError:
                pass
            refs = {}
            used = {cid for s in manifest['scenes'] for cid in s['characters']}
            used_characters = [char for char in manifest['characters'] if char['id'] in used]
            for index, char in enumerate(used_characters):
                update('Khóa ảnh nhân vật: ' + char['name'], 30 + 10 * (index + 1) / max(1, len(used_characters)), 'references')
                refs[char['id']] = render_image(client, folder, p['style'] + '. Single character reference portrait, neutral background. ' + char['appearance_en'], [], 768, 768)
            for i, scene in enumerate(manifest['scenes']):
                update('Tạo ảnh %d/%d' % (i + 1, len(manifest['scenes'])), 40 + 40 * (i + 1) / max(1, len(manifest['scenes'])), 'images')
                reference_paths = [refs[c] for c in scene['characters']]
                identity = ' '.join('Reference image %d is %s; preserve facial identity.' % (j + 1, next(c['name'] for c in manifest['characters'] if c['id'] == cid)) for j, cid in enumerate(scene['characters']))
                scene['image'] = render_image(client, folder, p['style'] + '. ' + identity + ' ' + scene['prompt_en'] + '. No text or watermark.', reference_paths, p['width'], p['height']).name
                save(folder / 'manifest.json', manifest)
        update('Dựng chuyển động và ghép audio', 85, 'video')
        compose(folder, p, manifest, rows, update)
        save(folder / 'status.json', {'state': 'completed', 'message': 'Video hoàn tất', 'video': 'final.mp4', 'progress': 100, 'stage': 'completed', 'updated_at': time.time()})
    except TaskPaused:
        current = json.loads((folder / 'status.json').read_text(encoding='utf-8')) if (folder / 'status.json').exists() else {}
        save(folder / 'status.json', {**current, 'state': 'paused', 'message': 'Đã tạm dừng. Có thể tiếp tục từ cache.', 'resumable': True, 'updated_at': time.time()})
    except TaskCancelled:
        current = json.loads((folder / 'status.json').read_text(encoding='utf-8')) if (folder / 'status.json').exists() else {}
        save(folder / 'status.json', {**current, 'state': 'cancelled', 'message': 'Đã hủy tiến trình.', 'resumable': False, 'updated_at': time.time()})
    except Exception as exc:
        save(folder / 'status.json', {'state': 'failed', 'message': str(exc), 'resumable': True, 'updated_at': time.time()})
