import urllib.request
import json
import time
import sys

sys.stdout.reconfigure(encoding='utf-8')

def benchmark_speed():
    base_url = "http://127.0.0.1:8000"
    
    # Generate a large 5,000 word test document
    paragraphs = [
        f"Chương {i+1}: Ngày xửa ngày xưa, tại một vùng đất trù phú và thanh bình, có một câu chuyện được lưu truyền qua nhiều thế hệ. Đỗ Gia Huy và Nguyễn Bảo An cùng nhau xây dựng lại rạp chiếu phim Ánh Trăng, vượt qua mọi thử thách của cuộc sống và tìm thấy tình yêu chân chính."
        for i in range(30)
    ]
    large_text = "\n\n".join(paragraphs)
    char_count = len(large_text)
    word_count = len(large_text.split())
    print(f"=== BENCHMARKING SPEED FOR {char_count:,} CHARS (~{word_count:,} WORDS) ===")

    payload = json.dumps({
        "text": large_text,
        "voice": "vi-VN-HoaiMyNeural",
        "rate": "+0%",
        "pitch": "+0Hz",
        "volume": "+0%"
    }).encode('utf-8')

    t0 = time.time()
    req = urllib.request.Request(f"{base_url}/api/tts", data=payload, headers={"Content-Type": "application/json"})
    res = urllib.request.urlopen(req)
    data = res.read()
    t1 = time.time()

    process_time = res.headers.get("X-Process-Time", "")
    chunks_count = res.headers.get("X-Chunks-Count", "")
    print(f"✅ Success! Generated {len(data):,} bytes of MP3 audio in {t1 - t0:.2f}s (Server internal: {process_time}, Chunks: {chunks_count})")
    print(f"⚡ Processing Speed: ~{word_count / (t1 - t0):.1f} words/second!")

if __name__ == "__main__":
    benchmark_speed()
