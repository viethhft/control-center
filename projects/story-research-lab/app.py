import json, os, re, time, uuid
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent; DATA=ROOT/'projects'; DATA.mkdir(exist_ok=True)
OLLAMA=os.getenv('OLLAMA_URL','http://127.0.0.1:11434').rstrip('/'); MODEL=os.getenv('STORY_LAB_MODEL','qwen3.5:27b')
app=FastAPI(title='Story Research Lab',version='0.1.0'); app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
class Request(BaseModel):
 title:str=Field(min_length=1,max_length=150); source:str=Field(min_length=30,max_length=200000)
 genre:str='Tự chọn'; mode:str=Field(default='adapt',pattern='^(research|adapt|develop)$'); requirements:str=Field(default='',max_length=3000)
class EvaluationRequest(BaseModel):
 force:bool=False
def extract(text):
 a,b=text.find('{'),text.rfind('}');
 if a<0 or b<a: raise ValueError('Model không trả JSON hợp lệ')
 return json.loads(text[a:b+1])
@app.post('/api/process')
async def process(req:Request):
 system='Bạn là biên tập viên và nhà nghiên cứu cấu trúc truyện chuyên nghiệp. Chỉ trả JSON hợp lệ, không sao chép dài dòng nguyên tác.'
 task={'research':'phân tích sâu để học kỹ thuật kể chuyện, điểm mạnh/yếu, nhịp, nhân vật và độc giả','adapt':'đề xuất rồi viết một bản cải biên mới, giữ hạt nhân nhưng thay đổi sáng tạo để không sao chép','develop':'phát triển thành một truyện mới hấp dẫn theo đúng thể loại'}[req.mode]
 prompt=f'''Hãy {task}. Thể loại: {req.genre}. Yêu cầu: {req.requirements or "Tự quyết định hợp lý"}.
Trả JSON: {{"analysis":{{"premise":"","summary":"tóm tắt ngắn nội dung nguồn","themes":[],"strengths":[],"weaknesses":[],"characters":[],"story_structure":[]}},"development_plan":{{"new_premise":"","changes":[],"outline":[]}},"result":"bản truyện hoặc báo cáo hoàn chỉnh","evaluation":{{"score":0,"hook":0,"plot":0,"characters":0,"emotion":0,"originality":0,"audience":"","verdict":"","improvements":[]}}}}.
NGUỒN:\n{req.source}'''
 try:
  async with httpx.AsyncClient(timeout=httpx.Timeout(900,connect=10)) as client:
   r=await client.post(f'{OLLAMA}/api/chat',json={'model':MODEL,'stream':False,'format':'json','think':False,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}]}); r.raise_for_status(); result=extract(r.json()['message']['content'])
 except Exception as exc: raise HTTPException(502,f'Không thể xử lý bằng Ollama: {type(exc).__name__}: {exc}')
 pid=f'{int(time.time())}-{uuid.uuid4().hex[:6]}'; record={'id':pid,'title':req.title,'genre':req.genre,'mode':req.mode,'source':req.source,'requirements':req.requirements,'result':result,'created_at':time.time()}; (DATA/f'{pid}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8'); return record
@app.get('/api/projects')
def projects():
 out=[]
 for p in DATA.glob('*.json'):
  try:
   x=json.loads(p.read_text(encoding='utf-8')); item={k:x.get(k) for k in ('id','title','genre','mode','created_at')}; item['summary']=x.get('result',{}).get('analysis',{}).get('summary',''); item['score']=(x.get('evaluation') or x.get('result',{}).get('evaluation') or {}).get('score'); out.append(item)
  except: pass
 return sorted(out,key=lambda x:x.get('created_at',0),reverse=True)
@app.get('/api/projects/{pid}')
def get_project(pid:str):
 if not re.fullmatch(r'[\w-]+',pid): raise HTTPException(400,'ID không hợp lệ')
 p=DATA/f'{pid}.json'
 if not p.exists(): raise HTTPException(404,'Không tìm thấy project')
 return json.loads(p.read_text(encoding='utf-8'))
@app.post('/api/projects/{pid}/evaluate')
async def evaluate_project(pid:str, req:EvaluationRequest):
 p=DATA/f'{pid}.json'
 if not re.fullmatch(r'[\w-]+',pid): raise HTTPException(400,'ID không hợp lệ')
 if not p.exists(): raise HTTPException(404,'Không tìm thấy project')
 record=json.loads(p.read_text(encoding='utf-8'))
 if record.get('evaluation') and not req.force: return record
 story=record.get('result',{}).get('result') or record.get('source','')
 prompt=f'''Đánh giá truyện dưới góc độ biên tập viên độc lập. Không thiên vị, chỉ trả JSON hợp lệ.
Trả JSON: {{"summary":"tóm tắt 100-180 từ","score":0,"hook":0,"plot":0,"characters":0,"emotion":0,"originality":0,"audience":"độc giả phù hợp","verdict":"nhận định tổng quan","strengths":[],"weaknesses":[],"improvements":[]}}.
Mọi điểm số theo thang 100. Thể loại: {record.get('genre','Tự chọn')}.
TRUYỆN:\n{story[:120000]}'''
 try:
  async with httpx.AsyncClient(timeout=httpx.Timeout(900,connect=10)) as client:
   r=await client.post(f'{OLLAMA}/api/chat',json={'model':MODEL,'stream':False,'format':'json','think':False,'messages':[{'role':'system','content':'Bạn là hội đồng thẩm định truyện chuyên nghiệp. Chỉ trả JSON.'},{'role':'user','content':prompt}]}); r.raise_for_status(); record['evaluation']=extract(r.json()['message']['content'])
 except Exception as exc: raise HTTPException(502,f'Không thể đánh giá bằng Ollama: {type(exc).__name__}: {exc}')
 record['updated_at']=time.time(); p.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8'); return record
@app.delete('/api/projects/{pid}')
def delete_project(pid:str):
 if not re.fullmatch(r'[\w-]+',pid): raise HTTPException(400,'ID không hợp lệ')
 p=DATA/f'{pid}.json'
 if not p.exists(): raise HTTPException(404,'Không tìm thấy project')
 p.unlink(); return {'ok':True}
@app.get('/api/health')
def health(): return {'ok':True,'model':MODEL}
@app.get('/')
def index(): return FileResponse(ROOT/'static'/'index.html')
