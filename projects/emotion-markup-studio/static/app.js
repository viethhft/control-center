const $ = (selector) => document.querySelector(selector);
const text = $('#text');
const toast = (message) => { const node = $('#toast'); node.textContent = message; node.className = 'show'; clearTimeout(toast.timer); toast.timer = setTimeout(() => node.className = '', 2200); };
text.addEventListener('input', () => $('#count').textContent = `${text.value.length.toLocaleString('vi-VN')} ký tự`);
$('#analyze').onclick = async () => {
  const button = $('#analyze');
  $('#error').hidden = true;
  button.disabled = true;
  button.innerHTML = '<i></i> AI đang đạo diễn giọng đọc...';
  try {
    const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('#title').value, text: text.value, genre: $('#genre').value, intensity: $('#intensity').value, context: $('#context').value }) });
    const data = await response.json();
    if (!response.ok) throw Error(data.detail || 'Không thể xử lý văn bản');
    $('#output').value = data.annotated_text;
    $('#summary').textContent = `Đã đạo diễn ${data.segments.length} đoạn · Lưu với mã ${data.id}`;
    $('#result').hidden = false;
    $('#result').scrollIntoView({ behavior: 'smooth' });
  } catch (error) {
    $('#error').textContent = error.message;
    $('#error').hidden = false;
  } finally {
    button.disabled = false;
    button.innerHTML = 'Phân tích & gắn cảm xúc <b>→</b>';
  }
};
$('#copy').onclick = async () => { await navigator.clipboard.writeText($('#output').value); toast('Đã sao chép để dùng trong TTS Studio'); };
$('#download').onclick = () => { const blob = new Blob([$('#output').value], { type: 'text/plain;charset=utf-8' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `${($('#title').value || 'emotion-markup').replace(/[^\p{L}\p{N}-]+/gu, '-')}.txt`; link.click(); URL.revokeObjectURL(link.href); };
