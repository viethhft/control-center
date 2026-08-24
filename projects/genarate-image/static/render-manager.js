const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const activeStatuses=new Set(['queued','running']);
const tracked=new Map();
let managerProjectId='';

document.head.insertAdjacentHTML('beforeend',`<style>
.render-manager{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:14px}
.render-manager button,.render-auto{padding:9px 12px;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--ink);font-weight:750}
.render-manager button[data-quality="final"]{background:var(--dark);color:#fff}.render-manager button:disabled{opacity:.55;cursor:wait}
.render-auto{display:flex;align-items:center;gap:7px;font-size:10px}.render-auto input{margin:0}.render-job-note{width:100%;color:var(--muted);font-size:10px}
.scene .render-state{display:block;margin-top:7px;color:var(--muted);font-size:10px}.scene.is-rendering{outline:2px solid #bfd74d55}
.render-versions{display:flex;gap:6px;margin-top:8px}.render-versions span{padding:4px 7px;border-radius:7px;background:#eef1eb;color:var(--muted);font-size:9px}.render-versions .ready{background:#edf5d2;color:#627416}
</style>`);

async function requestJson(url,options){
 const response=await fetch(url,options),data=await response.json().catch(()=>({}));
 if(!response.ok)throw Error(data.detail||`HTTP ${response.status}`);return data;
}

async function refreshProject(){
 const current=window.currentStoryProject?.();if(!current)return;
 const latest=await requestJson(`/api/projects/${current.id}`);Object.assign(current,latest);window.render?.();
}

function jobLabel(job){
 const quality=job.quality==='final'?'cuối':'nháp';
 if(job.kind==='scene')return `${job.scene_id} · ${quality}`;
 if(job.kind==='bulk')return `Hàng loạt · ${quality}`;
 if(job.kind==='character_references')return `Nhân vật ${job.character_id}`;
 return 'Render';
}

function paintJobs(){
 const active=[...tracked.values()].filter(job=>activeStatuses.has(job.status));
 document.querySelectorAll('#characters .character').forEach((card,index)=>{
  const character=window.currentStoryProject?.()?.characters?.[index];if(!character)return;
  const busy=active.find(job=>job.character_id===character.id||(job.resource_keys||[]).includes(`character:${character.id}`)||(job.resource_keys||[]).includes('project:*'));
  card.classList.toggle('is-rendering',!!busy);card.querySelectorAll('button,input,label').forEach(control=>{if('disabled'in control)control.disabled=!!busy});
  const status=card.querySelector('.reference-status'),statusText=busy?`Đang được ${jobLabel(busy)} xử lý · ${busy.progress||0}%`:'';if(status&&busy&&status.textContent!==statusText)status.textContent=statusText;
 });
 document.querySelectorAll('#scenes .scene').forEach((card,index)=>{
  const scene=window.currentStoryProject?.()?.scenes?.[index];if(!scene)return;
  let versions=card.querySelector('.render-versions');if(!versions){versions=document.createElement('div');versions.className='render-versions';card.querySelector('.render-actions')?.after(versions)}
  const legacy=scene.image_url&&!scene.draft_image_url&&!scene.final_image_url;
  const versionHtml=`<span class="${scene.draft_image_url?'ready':''}">Nháp ${scene.draft_image_url?'đã có':'chưa có'}</span><span class="${scene.final_image_url?'ready':''}">Bản cuối ${scene.final_image_url?'đã có':'chưa có'}</span>${legacy?'<span class="ready">Ảnh phiên bản cũ</span>':''}`;
  if(versions.innerHTML!==versionHtml)versions.innerHTML=versionHtml;
  const job=active.find(item=>item.scene_id===scene.id||(item.kind==='bulk'));
  card.classList.toggle('is-rendering',!!job);let note=card.querySelector('.render-state');
  if(job){if(!note){note=document.createElement('small');note.className='render-state';card.appendChild(note)}const text=`${jobLabel(job)} · ${job.progress||0}% · ${job.message||'đang xử lý'}`;if(note.textContent!==text)note.textContent=text}
  else note?.remove();
  card.querySelectorAll('.render-actions button').forEach(button=>button.disabled=!!job);
 });
 const panel=document.querySelector('.render-manager'),note=panel?.querySelector('.render-job-note');
 if(note){const text=active.length?active.map(job=>`${jobLabel(job)} ${job.progress||0}%`).join(' · '):'Không có render đang chạy';if(note.textContent!==text)note.textContent=text}
 panel?.querySelectorAll('button').forEach(button=>button.disabled=active.some(job=>job.kind==='bulk'));
}

async function watchJob(id){
 if(tracked.get(id)?._watching)return;tracked.set(id,{...(tracked.get(id)||{}),id,_watching:true});
 let networkFailures=0;
 while(true){
  try{
   const job=await requestJson(`/api/generate/jobs/${id}`);job._watching=true;tracked.set(id,job);networkFailures=0;paintJobs();
   if(!activeStatuses.has(job.status)){
    job._watching=false;tracked.set(id,job);paintJobs();
    if(job.status==='completed'){await refreshProject();if(job.errors?.length)await uiAlert(`${job.errors.length} ảnh lỗi. Tiến trình và chi tiết đã được lưu.`, 'Render hoàn tất một phần')}
    else await uiAlert(job.error||job.message||'Render thất bại','Render gặp lỗi');
    return;
   }
   await sleep(1200);
  }catch(error){
   networkFailures++;paintJobs();
   if(networkFailures>=20){await uiAlert(`${error.message}. Job vẫn được giữ trên server.`,'Mất kết nối render');return}
   await sleep(Math.min(5000,1000*networkFailures));
  }
 }
}

async function submitBulk(quality,overwrite=false){
 const project=window.currentStoryProject?.();if(!project)return;
 if(!project.cast_locked){await uiAlert('Hãy khóa ảnh cho toàn bộ nhân vật xuất hiện trước.','Nhân vật chưa được khóa');return}
 try{
  const data=await requestJson('/api/generate/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:project.id,quality,overwrite})});
  tracked.set(data.job_id,{id:data.job_id,status:'queued',kind:'bulk',quality,project_id:project.id,progress:0});paintJobs();watchJob(data.job_id);
 }catch(error){await uiAlert(error.message,'Không thể bắt đầu render')}
}

function installManager(){
 const project=window.currentStoryProject?.(),head=document.querySelector('.project-head');if(!project||!head)return;
 if(managerProjectId!==project.id){managerProjectId=project.id;tracked.clear();loadSavedJobs(project.id)}
 if(head.querySelector('.render-manager')){paintJobs();return}
 const panel=document.createElement('div');panel.className='render-manager';
 panel.innerHTML=`<button type="button" data-quality="draft">Render nháp còn thiếu</button><button type="button" data-quality="final">Render cuối còn thiếu</button><button type="button" data-quality="final" data-overwrite="true">Render lại toàn bộ bản cuối</button><label class="render-auto"><input type="checkbox"> Tự động render cuối khi khóa đủ nhân vật</label><small class="render-job-note">Không có render đang chạy</small>`;
 head.appendChild(panel);
 panel.querySelectorAll('button').forEach(button=>button.onclick=()=>submitBulk(button.dataset.quality,button.dataset.overwrite==='true'));
 const auto=panel.querySelector('input'),key=`storyframe:auto-final:${project.id}`;auto.checked=localStorage.getItem(key)==='true';
 auto.onchange=()=>{localStorage.setItem(key,String(auto.checked));if(auto.checked&&project.cast_locked)submitBulk('final',false)};
 if(auto.checked&&project.cast_locked&&!project.scenes.every(scene=>scene.final_image_url))submitBulk('final',false);
 paintJobs();
}

async function loadSavedJobs(projectId){
 try{const jobs=await requestJson(`/api/generate/jobs?project_id=${encodeURIComponent(projectId)}`);jobs.forEach(job=>tracked.set(job.id,job));paintJobs();jobs.filter(job=>activeStatuses.has(job.status)).forEach(job=>watchJob(job.id))}catch{}
}

window.generate=async function(sceneId,button,quality='final'){
 const project=window.currentStoryProject?.();if(!project)return;button.disabled=true;
 try{
  let width=1664,height=928;if(project.aspect_ratio==='9:16'){width=928;height=1664}else if(project.aspect_ratio==='1:1')width=height=1328;else if(project.aspect_ratio==='4:3'){width=1472;height=1140}
  const data=await requestJson('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:project.id,scene_id:sceneId,width,height,quality})});
  tracked.set(data.job_id,{id:data.job_id,status:'queued',kind:'scene',scene_id:sceneId,quality,project_id:project.id,progress:0});paintJobs();watchJob(data.job_id);
 }catch(error){button.disabled=false;await uiAlert(error.message,'Không thể render cảnh')}
};

window.generateReferences=async function(characterId,button){
 const project=window.currentStoryProject?.();if(!project)return;button.disabled=true;button.textContent='Đang xếp hàng…';
 try{
  const data=await requestJson(`/api/projects/${project.id}/characters/${characterId}/reference-jobs`,{method:'POST'});
  tracked.set(data.job_id,{id:data.job_id,status:'queued',kind:'character_references',character_id:characterId,project_id:project.id,progress:0});paintJobs();
  let failures=0;
  while(true){
   try{
    const job=await requestJson(`/api/generate/jobs/${data.job_id}`);tracked.set(job.id,job);failures=0;button.textContent=`Tạo ảnh ${job.completed||0}/3 · ${job.progress||0}%`;paintJobs();
    if(job.status==='completed'){
     const character=project.characters.find(item=>item.id===characterId);character.reference_candidates=job.result.candidates;delete character.selected_reference;project.cast_locked=false;window.render?.();return;
    }
    if(job.status==='failed'){button.disabled=false;button.textContent='Thử tạo lại';await uiAlert(job.error||'Tạo ảnh nhân vật thất bại','Tạo ảnh nhân vật gặp lỗi');return}await sleep(1200);
   }catch(error){failures++;if(failures>=20)throw error;button.textContent='Đang nối lại tiến trình…';await sleep(Math.min(5000,failures*1000))}
  }
 }catch(error){button.disabled=false;button.textContent='Thử tạo lại';await uiAlert(`${error.message}. Job đã tạo vẫn được lưu trên server.`,'Tạo ảnh nhân vật gặp lỗi')}
};

let installScheduled=false;
new MutationObserver(()=>{if(installScheduled)return;installScheduled=true;requestAnimationFrame(()=>{installScheduled=false;installManager()})}).observe(document.querySelector('#workspace'),{subtree:true,childList:true});
installManager();
