document.head.insertAdjacentHTML('beforeend','<link rel="stylesheet" href="/static/wizard.css?v=6">');

const steps=[
 {id:1,title:'Nội dung & phong cách',hint:'Nhập truyện và cấu hình'},
 {id:2,title:'Phân tích câu chuyện',hint:'Nhân vật, outline và cảnh'},
 {id:3,title:'Khóa nhân vật',hint:'Chọn ảnh nhận dạng chuẩn'},
 {id:4,title:'Duyệt storyboard',hint:'Kiểm tra prompt và bối cảnh'},
 {id:5,title:'Render ảnh',hint:'Tạo và theo dõi kết quả'}
];
const setup=document.querySelector('#setup'),progress=document.querySelector('#progress'),workspace=document.querySelector('#workspace');
const sideList=document.querySelector('aside ol');
let flowStep=1,viewStep=1,following=true,hadProject=false,frame=0,savedJobs=[];

sideList.id='flowNav';
sideList.innerHTML=steps.map(s=>`<li data-step="${s.id}" data-tooltip="${s.title} — ${s.hint}"><button type="button" aria-label="${s.title}: ${s.hint}"><span>${String(s.id).padStart(2,'0')}</span><b>${s.title}</b><small>${s.hint}</small></button></li>`).join('');

const controls=document.createElement('div');
controls.id='wizardControls';
controls.innerHTML='<button type="button" id="wizardBack">← Quay lại</button><button type="button" id="savedProcesses">Dự án đã lưu</button><span id="wizardContext">Đang nhập dữ liệu</span><button type="button" id="followFlow" hidden>Theo tiến trình</button><button type="button" id="wizardNext">Tiếp theo →</button>';
document.querySelector('main').appendChild(controls);

const savedPanel=document.createElement('div');
savedPanel.id='savedProcessesModal';savedPanel.hidden=true;
savedPanel.innerHTML=`<div class="saved-backdrop" data-close-modal></div><section id="savedProcessesPanel" role="dialog" aria-modal="true" aria-labelledby="savedProcessesTitle"><header><div><small>THƯ VIỆN DỰ ÁN</small><h2 id="savedProcessesTitle">Dự án đã lưu</h2><p>Mở lại dự án hoặc tiếp tục chính xác từ checkpoint gần nhất khi tiến trình gặp lỗi.</p></div><button type="button" class="saved-close" data-close-modal aria-label="Đóng">×</button></header><div class="saved-toolbar"><label><span>Tìm dự án</span><input id="savedSearch" type="search" placeholder="Nhập tên dự án…"></label><label><span>Trạng thái</span><select id="savedFilter"><option value="all">Tất cả</option><option value="running">Đang chạy</option><option value="failed">Gặp lỗi</option><option value="completed">Hoàn tất</option></select></label><button type="button" id="reloadSaved">↻ Làm mới</button></div><div class="saved-summary" id="savedSummary"></div><div class="saved-list" aria-live="polite">Đang tải…</div></section>`;
document.body.appendChild(savedPanel);

const escapeHtml=value=>{const node=document.createElement('span');node.textContent=String(value??'');return node.innerHTML};
const jobLabel=status=>({completed:'Hoàn tất',running:'Đang chạy',queued:'Đang chờ',failed:'Gặp lỗi',cancelled:'Đã dừng'}[status]||status||'Không rõ');
function formatSavedTime(value){if(!value)return 'Chưa rõ thời gian';const date=new Date(Number(value)*1000);return Number.isNaN(date.getTime())?'Chưa rõ thời gian':new Intl.DateTimeFormat('vi-VN',{dateStyle:'short',timeStyle:'short'}).format(date)}
function renderSavedProcesses(){
 const list=savedPanel.querySelector('.saved-list'),query=document.querySelector('#savedSearch').value.trim().toLocaleLowerCase('vi'),filter=document.querySelector('#savedFilter').value;
 const jobs=savedJobs.filter(job=>(filter==='all'||job.status===filter)&&String(job.project_name||job.project_id||'').toLocaleLowerCase('vi').includes(query));
 document.querySelector('#savedSummary').innerHTML=`<span><b>${jobs.length}</b> dự án hiển thị</span><span><b>${savedJobs.filter(j=>['running','queued'].includes(j.status)).length}</b> đang xử lý</span><span><b>${savedJobs.filter(j=>j.status==='failed').length}</b> cần tiếp tục</span>`;
 list.innerHTML=jobs.length?jobs.map(job=>{const value=Math.max(0,Math.min(100,Number(job.progress)||0)),checkpoint=job.checkpoint||{},action=job.status==='completed'?'Mở dự án':['failed','cancelled'].includes(job.status)?'Tiếp tục checkpoint':'Theo dõi';return `<article class="saved-job" data-status="${escapeHtml(job.status)}"><div class="saved-job-top"><span class="saved-status">${escapeHtml(jobLabel(job.status))}</span><time>${escapeHtml(formatSavedTime(job.updated_at||job.created_at))}</time></div><h3>${escapeHtml(job.project_name||job.project_id||'Dự án chưa đặt tên')}</h3><p>${escapeHtml(job.message||'Chưa có mô tả tiến trình')}</p><div class="saved-progress"><i style="width:${value}%"></i></div><div class="saved-meta"><span><b>${value}%</b> hoàn thành</span><span>${checkpoint.details||0} cảnh chi tiết</span><span>${checkpoint.ready_prompts||0} prompt sẵn sàng</span></div><div class="saved-card-actions"><button type="button" class="saved-delete" data-delete-project="${job.project_id}" data-project-name="${escapeHtml(job.project_name||job.project_id)}">Xóa</button><button type="button" data-job-id="${job.id}" data-status="${job.status}">${action} <i>→</i></button></div></article>`}).join(''):'<div class="saved-empty"><b>Không tìm thấy dự án</b><p>Hãy thử từ khóa hoặc bộ lọc khác.</p></div>';
}
async function loadSavedProcesses(){
 const list=savedPanel.querySelector('.saved-list');list.innerHTML='<div class="saved-loading">Đang tải danh sách dự án…</div>';
 try{const response=await fetch('/api/analyze/jobs'),data=await response.json();if(!response.ok)throw Error(data.detail||'Không tải được dự án');savedJobs=data.jobs||[];renderSavedProcesses()}
 catch(error){list.innerHTML=`<div class="saved-empty"><b>Không tải được dữ liệu</b><p>${escapeHtml(error.message)}</p><button type="button" id="retrySaved">Thử lại</button></div>`;document.querySelector('#retrySaved').onclick=loadSavedProcesses}
}
function openSavedModal(){savedPanel.hidden=false;document.body.classList.add('modal-open');loadSavedProcesses();setTimeout(()=>document.querySelector('#savedSearch')?.focus(),50)}
function closeSavedModal(){savedPanel.hidden=true;document.body.classList.remove('modal-open')}
document.querySelector('#savedProcesses').onclick=openSavedModal;
savedPanel.querySelectorAll('[data-close-modal]').forEach(node=>node.onclick=closeSavedModal);
document.querySelector('#reloadSaved').onclick=loadSavedProcesses;document.querySelector('#savedSearch').oninput=renderSavedProcesses;document.querySelector('#savedFilter').onchange=renderSavedProcesses;
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!savedPanel.hidden)closeSavedModal()});
savedPanel.querySelector('.saved-list').onclick=async event=>{
 const deleteButton=event.target.closest('[data-delete-project]');if(deleteButton){const pid=deleteButton.dataset.deleteProject,name=deleteButton.dataset.projectName||pid,accepted=await uiConfirm({title:`Xóa dự án “${name}”?`,message:'Toàn bộ checkpoint, dữ liệu đầu vào, project và ảnh đã tạo sẽ bị xóa vĩnh viễn. Thao tác này không thể hoàn tác.',confirmText:'Xóa vĩnh viễn',danger:true});if(!accepted)return;deleteButton.disabled=true;deleteButton.textContent='Đang xóa…';try{const response=await fetch(`/api/projects/${encodeURIComponent(pid)}`,{method:'DELETE'}),data=await response.json();if(response.status===405)throw Error('Backend StoryFrame đang chạy phiên bản cũ. Hãy dừng và khởi động lại StoryFrame từ Control Center rồi thử lại.');if(!response.ok)throw Error(data.detail||'Không thể xóa dự án');await loadSavedProcesses()}catch(error){deleteButton.disabled=false;deleteButton.textContent='Xóa';await uiAlert(error.message,'Không thể xóa dự án')}return}
 const button=event.target.closest('[data-job-id]');if(!button)return;const jid=button.dataset.jobId,status=button.dataset.status,old=button.innerHTML;button.disabled=true;button.innerHTML='<span class="button-spinner"></span> Đang mở…';
 try{if(['failed','cancelled'].includes(status)){const response=await fetch(`/api/analyze/jobs/${jid}/retry`,{method:'POST'}),data=await response.json();if(!response.ok)throw Error(data.detail||'Không thể tiếp tục checkpoint')}window.prepareAnalysisJob?.(jid);closeSavedModal();following=true;showView(2,false);window.pollAnalysis?.(jid)}
 catch(error){button.innerHTML=old;button.disabled=false;await uiAlert(error.message,'Không thể mở dự án')}
};

const project=()=>window.currentStoryProject?.()||null;
function selectTab(name){const button=document.querySelector(`#workspace nav button[data-tab="${name}"]`);if(button&&!button.classList.contains('active'))button.click()}
function showView(step,scroll=true){
 const data=project();viewStep=step;setup.classList.add('hidden');progress.classList.add('hidden');workspace.classList.add('hidden');
 if(step===1)setup.classList.remove('hidden');else if(!data)progress.classList.remove('hidden');else{workspace.classList.remove('hidden');selectTab(step===3?'characters':'scenes')}
 document.querySelectorAll('#flowNav li').forEach(item=>item.classList.toggle('viewing',Number(item.dataset.step)===viewStep));
 if(scroll)document.querySelector(step===5?'#renderAll':step===1?'#setup':step===3?'#characters':'#scenes')?.scrollIntoView({behavior:'smooth',block:'start'});updateControls();
}
function detectFlow(){const data=project();if(data){const rendering=[...document.querySelectorAll('#renderAll,.scene button')].some(button=>button.disabled&&/render/i.test(button.textContent));return rendering?5:4}const percent=parseInt(document.querySelector('#percent')?.textContent||'0',10)||0;return percent>0||!progress.classList.contains('hidden')?2:1}
function updateControls(){const unlocked=project()?5:Math.max(1,flowStep),back=document.querySelector('#wizardBack'),next=document.querySelector('#wizardNext'),follow=document.querySelector('#followFlow');back.disabled=viewStep<=1;next.disabled=viewStep>=unlocked;next.textContent='Tiếp theo →';follow.hidden=following||viewStep===flowStep;document.querySelector('#wizardContext').textContent=`Đang xem: ${steps[viewStep-1].title}`}
function sync(){frame=0;const data=project(),nextFlow=detectFlow(),appeared=!hadProject&&!!data;hadProject=!!data;flowStep=nextFlow;const failed=!document.querySelector('#diagnostic')?.classList.contains('hidden');document.querySelectorAll('#flowNav li').forEach(item=>{const n=Number(item.dataset.step);item.classList.toggle('active',n===flowStep);item.classList.toggle('done',n<flowStep);item.classList.toggle('error',failed&&n===flowStep);item.querySelector('button').disabled=n>(data?5:Math.max(1,flowStep))});if(following&&(viewStep!==flowStep||appeared))showView(flowStep,false);else updateControls()}
function schedule(){if(!frame)frame=requestAnimationFrame(sync)}
sideList.onclick=event=>{const item=event.target.closest('li[data-step]');if(!item||item.querySelector('button').disabled)return;const target=Number(item.dataset.step);following=target===flowStep;showView(target)};
document.querySelector('#wizardBack').onclick=()=>{if(viewStep>1){following=false;showView(viewStep-1)}};document.querySelector('#wizardNext').onclick=()=>{const unlocked=project()?5:flowStep;if(viewStep<unlocked){following=viewStep+1===flowStep;showView(viewStep+1)}else if(viewStep===1)document.querySelector('#analyze')?.scrollIntoView({behavior:'smooth'})};document.querySelector('#followFlow').onclick=()=>{following=true;showView(flowStep)};
document.querySelector('#back')?.addEventListener('click',()=>{following=false;showView(1)},true);
const retry=document.querySelector('#retry'),legacyRetry=retry?.onclick;if(retry)retry.onclick=async()=>{const jid=document.body.dataset.analysisJobId;if(!jid){legacyRetry?.();return}retry.disabled=true;retry.textContent='Đang khôi phục checkpoint…';try{const response=await fetch(`/api/analyze/jobs/${jid}/retry`,{method:'POST'}),data=await response.json();if(!response.ok)throw Error(data.detail||'Không thể tiếp tục checkpoint');following=true;document.querySelector('#diagnostic')?.classList.add('hidden');window.pollAnalysis?.(jid)}catch(error){document.querySelector('#diagMessage').textContent=error.message}finally{retry.disabled=false;retry.textContent='Thử lại từ checkpoint →'}};
document.querySelector('#analyze')?.addEventListener('click',()=>{following=true;schedule()},true);new MutationObserver(schedule).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['class','disabled']});sync();
