import json
import urllib.request


BASE_URL = "http://127.0.0.1:8000"


def request_json(path: str):
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_voice(voice_id: str, text: str):
    payload = json.dumps({"text": text, "voice": voice_id}).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}/api/tts",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        audio = response.read()
    if len(audio) < 1000:
        raise RuntimeError(f"Audio của {voice_id} quá ngắn: {len(audio)} bytes")
    print(f"[PASS] {voice_id}: {len(audio):,} MP3 bytes")


def main():
    health = request_json("/api/health")
    assert health["provider"] == "vieneu-v3-local"
    voices = request_json("/api/voices")["voices"]
    print(f"VieNeu v3 Local sẵn sàng, {len(voices)} giọng tiếng Việt")
    for voice in voices:
        test_voice(voice["id"], voice["sampleText"])


if __name__ == "__main__":
    main()
