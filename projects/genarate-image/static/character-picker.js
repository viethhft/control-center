const charactersRoot=document.querySelector('#characters');

function enhanceCharacterPickers(){
 const project=window.currentStoryProject?.();if(!project)return;
 charactersRoot.querySelectorAll('.character').forEach((card,index)=>{
  if(card.querySelector('.reference-tools'))return;
  const character=project.characters[index];if(!character)return;
  const grid=card.querySelector('.reference-grid');
  const tools=document.createElement('div');tools.className='reference-tools';tools.innerHTML=`<div><b>Ảnh khóa nhân vật</b><small>${character.selected_reference?'Đã chọn ảnh dùng xuyên suốt các cảnh':'Chọn một ảnh AI tạo hoặc tải ảnh của bạn lên'}</small></div><label class="upload-reference">↑ Chọn ảnh từ máy<input type="file" accept="image/png,image/jpeg,image/webp"></label>`;
  card.insertBefore(tools,grid);
  tools.querySelector('input').onchange=event=>uploadReference(character.id,event.target.files?.[0],tools.querySelector('label'));
 });
}

async function uploadReference(characterId,file,label){
 if(!file)return;const project=window.currentStoryProject?.();if(!project)return;
 if(file.size>12*1024*1024){await uiAlert('Ảnh vượt quá giới hạn tải lên 12 MB.','Tệp quá lớn');return}
 const old=label.childNodes[0].textContent;label.classList.add('busy');label.childNodes[0].textContent=' Đang tải ảnh…';
 try{
  const form=new FormData();form.append('image',file);const response=await fetch(`/api/projects/${project.id}/characters/${characterId}/upload-reference`,{method:'POST',body:form}),data=await response.json();if(!response.ok)throw Error(data.detail||'Không tải được ảnh');Object.assign(project,data.project);
  const card=[...charactersRoot.querySelectorAll('.character')][project.characters.findIndex(item=>item.id===characterId)],grid=card.querySelector('.reference-grid');grid.querySelectorAll('.reference-option').forEach(item=>item.classList.remove('selected'));const option=document.createElement('button');option.className='reference-option selected';option.onclick=()=>window.selectReference?.(characterId,data.reference.url,option);option.innerHTML=`<img src="${data.reference.url}" alt="Ảnh khóa đã tải lên"><span>Đã khóa</span>`;grid.appendChild(option);card.querySelector('.reference-status').textContent='Đã chọn ảnh tải lên';card.querySelector('.reference-tools small').textContent='Đã chọn ảnh dùng xuyên suốt các cảnh';document.querySelector('#summary').textContent=(project.summary||project.visual_direction||'')+' · '+(data.cast_locked?'Đã khóa toàn bộ nhân vật':'Tiếp tục chọn ảnh cho các nhân vật còn lại');
 }catch(error){await uiAlert(error.message,'Không thể tải ảnh')}finally{label.classList.remove('busy');label.childNodes[0].textContent=old}
}

new MutationObserver(enhanceCharacterPickers).observe(charactersRoot,{childList:true,subtree:true});enhanceCharacterPickers();
