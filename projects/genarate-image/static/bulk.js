const head=document.querySelector('.project-head');
const button=document.createElement('button');
button.id='renderAll';button.textContent='Render nháp toàn bộ';head.appendChild(button);

button.onclick=async()=>{
 const project=window.currentStoryProject?.();if(!project)return;
 if(!project.cast_locked){await uiAlert('Hãy tạo và chọn ảnh khóa cho tất cả nhân vật xuất hiện trước khi render hàng loạt.','Nhân vật chưa được khóa');return}
 button.disabled=true;
 try{
  const response=await fetch('/api/generate/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:project.id,quality:'draft',overwrite:false})});
  const data=await response.json();if(!response.ok)throw Error(data.detail);
  const poll=async()=>{try{
   const response=await fetch('/api/generate/jobs/'+data.job_id),job=await response.json();if(!response.ok)throw Error(job.detail||'Không đọc được tiến trình');
   button.textContent=`Render nháp ${job.completed||0}/${job.total||'?'} · ${job.progress||0}%`;
   if(job.status==='completed'){button.disabled=false;button.textContent='Render các ảnh nháp còn thiếu';return}
   if(job.status==='failed')throw Error(job.error||'Render thất bại');setTimeout(poll,1200)
  }catch(error){button.disabled=false;button.textContent='Thử render nháp lại';await uiAlert(error.message,'Render gặp lỗi')}};poll()
 }catch(error){button.disabled=false;button.textContent='Thử render nháp lại';await uiAlert(error.message,'Render gặp lỗi')}
};
