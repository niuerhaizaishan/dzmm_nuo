"use strict";

// 未接服务的本地页面仍是预览；Gamefy 内验权失败时保持空白，不回退示例数据。
const apiBase=(window.GALLERY_API_BASE||"").replace(/\/$/,"");
const liveMode=Boolean(apiBase||window.dzmm?.user?.info);
let memberReady=!liveMode,canEdit=!liveMode,authenticated=!liveMode;
let accessMessage="正在加载画廊…";
const sections=["个人立绘","姐妹日常","高清美图","大开脑洞"];
const categoryCopy={"个人立绘":"换个造型，换个心情。","姐妹日常":"今天也一起做点可爱的小事。","高清美图":"好看的，当然要存一份。","大开脑洞":"不管合不合理，先快乐一下。"};
const authors=[{name:"桃桃",avatar:"桃",color:"#efd6c3"},{name:"眠眠",avatar:"眠",color:"#e4dce6"},{name:"小芽",avatar:"芽",color:"#d9e1cc"},{name:"糯米",avatar:"糯",color:"#e8dfc6"}];
const samples=[
  ["个人立绘","i","portrait","今天想换一个清清爽爽的头像",28,["i","a"],"35% 50%"],
  ["个人立绘","c","landscape","光刚好落在眼睛里的时候。",16,["c"],"50% 50%"],
  ["个人立绘","l","portrait","存一下这个元气配色～",32,["l","d"],"50% 50%"],
  ["个人立绘","g","square","温温柔柔的一张",19,["g"],"65% 50%"],
  ["个人立绘","a","wide","风吹过来的感觉",24,["a","i"],"40% 50%"],
  ["个人立绘","j","square","这个侧脸，先存了",12,["j"],"25% 50%"],
  ["个人立绘","d","wide","小小一只，也很有力量",21,["d","l"],"50% 50%"],
  ["个人立绘","b","landscape","一些可可爱爱的表情",13,["b","k"],"50% 50%"],
  ["姐妹日常","e","landscape","一起忙忙碌碌的下午",18,["e","b"],"50% 50%"],
  ["姐妹日常","b","wide","开饭啦，有没有人来蹭一口",23,["b"],"50% 50%"],
  ["姐妹日常","k","square","今天的快乐很简单！",11,["k","e"],"50% 50%"],
  ["姐妹日常","d","portrait","出发之前，再检查一下东西",15,["d"],"70% 50%"],
  ["高清美图","c","landscape","这个晚霞想看一万遍",30,["c","a"],"50% 50%"],
  ["高清美图","l","square","把夏天存进相册",26,["l"],"50% 50%"],
  ["高清美图","a","wide","晚风和远处的山",22,["a"],"50% 50%"],
  ["高清美图","i","portrait","喜欢这种绿意盈盈的感觉",17,["i","g"],"35% 50%"],
  ["大开脑洞","f","landscape","如果群聊消息都变成鱼……",35,["f"],"50% 50%"],
  ["大开脑洞","h","square","当你说「我有个大胆的想法」",20,["h","k"],"35% 50%"],
  ["大开脑洞","d","portrait","以这个体型上班，是不是很省工位",29,["d"],"70% 50%"],
  ["大开脑洞","k","wide","突然想到一个离谱但可行的办法",14,["k","b"],"50% 50%"],
];
const items=liveMode?[]:samples.map((row,index)=>({id:`sample-${index}`,category:row[0],cover:`assets/gallery/${row[1]}.jpg`,ratio:row[2],caption:row[3],title:row[3],saves:row[4],images:row[5].map(id=>`assets/gallery/${id}.jpg`),position:row[6],...authors[index%authors.length],order:samples.length-index,mine:false}));
const $=selector=>document.querySelector(selector);
const favorites=new Set(),objectURLs=new Set();
let view="shared",category=sections[0],current=null,imageIndex=0,selectedFiles=[],adding=false,toastTimer;
const sourceCache=new Map();
const pause=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
let playerRequest=null,playerValidUntil=0;
function currentPlayer(){
  // 同一批列表/图片共用一次平台身份请求；仅在内存短暂复用，后端仍逐次验权。
  if(playerRequest&&Date.now()<playerValidUntil)return playerRequest;
  playerValidUntil=Date.now()+30000;
  playerRequest=requestPlayer().catch(error=>{playerRequest=null;playerValidUntil=0;throw error;});
  return playerRequest;
}
async function requestPlayer(){
  // 宿主切换方向或重建 iframe 时，SDK 和身份服务可能晚于页面脚本就绪。
  for(let attempt=0;attempt<30&&!window.dzmm?.user?.info;attempt++)await pause(100);
  if(!window.dzmm?.user?.info)throw new Error("平台身份接口尚未就绪，请点重试。");
  let lastError;
  for(let attempt=0;attempt<3;attempt++){
    try{const player=await window.dzmm.user.info();if(player?.token)return player;}
    catch(error){lastError=error;}
    if(attempt<2)await pause(attempt===0?500:1500);
  }
  throw new Error(lastError?"平台身份初始化失败，请点重试。":"平台暂未签发身份凭证，请点重试；若仍失败，请重新登录 DZMM。");
}
const imageObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){imageObserver.unobserve(entry.target);setImage(entry.target,entry.target.dataset.source,false,true);}}),{root:$("#app-shell"),rootMargin:"400px 0px"});
async function api(path,options={},privateAction=false){
  if(!apiBase)throw new Error("图集服务还未接通，请稍后再来。");
  const headers={...options.headers};
  if(privateAction){const player=await currentPlayer();headers.Authorization=`Bearer ${player.token}`;}
  const response=await fetch(apiBase+path,{...options,credentials:"omit",headers});
  if(!response.ok){
    const message=(await response.text()).slice(0,200);
    if(privateAction&&(response.status===401||response.status===403)){
      playerRequest=null;playerValidUntil=0;canEdit=false;updatePermissions();
    }
    throw new Error(message||"图集暂时不可用，请稍后再试。");
  }
  return response;
}
function lockGallery(message){
  accessMessage=message;memberReady=false;items.length=0;render();
}
async function loadSession(){
  if(!liveMode)return;
  try{
    const result=await(await api("/me",{},true)).json();
    authenticated=true;canEdit=result.can_edit;
    favorites.clear();result.saved.forEach(id=>favorites.add(id));
    items.forEach(item=>{item.mine=result.mine.includes(item.id);});
  }catch{authenticated=false;canEdit=false;favorites.clear();items.forEach(item=>item.mine=false);}
  render();updatePermissions();
}
function updatePermissions(){
  $("#title-editor").hidden=!canEdit;
  $("#move-editor").hidden=!canEdit;
  $("#viewer-delete").hidden=!canEdit;
  $("#viewer-readonly").hidden=canEdit;
  if(current)updateSave();
}
async function sourceURL(source,thumbnail=false){
  if(!liveMode)return source;
  source=String(source);
  const key=`${source}:${thumbnail?"thumb":"original"}`;
  if(!sourceCache.has(key))sourceCache.set(key,(async()=>{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
    try{const response=await api(`/images/${source}${thumbnail?"?size=thumb":""}`,{signal:controller.signal});const url=URL.createObjectURL(await response.blob());objectURLs.add(url);return url;}
    finally{clearTimeout(timer);}
  })().catch(error=>{sourceCache.delete(key);throw error;}));
  return sourceCache.get(key);
}
function setImage(image,source,lazy=false,thumbnail=false){
  image.dataset.source=String(source);
  if(!liveMode){image.src=source;return;}
  if(lazy){imageObserver.observe(image);return;}
  const requestKey=`${source}:${thumbnail?"thumb":"original"}`;
  if(image.dataset.loading===requestKey)return;
  image.dataset.loading=requestKey;
  const button=image.closest(".card-image-button"),status=button?.querySelector(".image-status");
  if(status){status.hidden=false;status.textContent="图片加载中…";button.dataset.loadState="loading";}
  const matches=()=>image.isConnected&&memberReady&&image.dataset.source===String(source)&&image.dataset.loading===requestKey;
  const failed=()=>{
    sourceCache.delete(`${source}:${thumbnail?"thumb":"original"}`);
    if(!matches())return;
    image.dataset.loading="";
    if(status){status.hidden=false;status.textContent="加载失败，点击重试";button.dataset.loadState="error";}
    else showToast("图片加载失败，请重新打开重试。");
  };
  sourceURL(source,thumbnail).then(url=>{
    if(!matches())return;
    image.onload=()=>{if(!matches())return;image.dataset.loading="";if(status){status.hidden=true;button.dataset.loadState="ready";}};
    image.onerror=failed;image.src=url;
  }).catch(failed);
}
function loadVisibleImages(){
  const shell=$("#app-shell").getBoundingClientRect();
  document.querySelectorAll("#gallery img:not([src])").forEach(image=>{
    const rect=image.getBoundingClientRect();
    if(rect.bottom>=shell.top-400&&rect.top<=shell.bottom+400&&image.closest("button").dataset.loadState!=="error"){
      imageObserver.unobserve(image);setImage(image,image.dataset.source,false,true);
    }
  });
}
async function loadGallery(){
  const result=await (await api("/items")).json();memberReady=true;
  items.splice(0,items.length,...result.items.map(row=>({id:row.id,category:row.category,cover:row.id,images:[row.id],filenames:[row.filename],ratio:row.width/row.height<.9?"portrait":row.width/row.height>1.5?"wide":"landscape",caption:row.caption,title:row.title,mine:false,order:row.created,saves:row.saves})));
  favorites.clear();render();
}
async function refreshGallery(){
  const retry=$("#gallery-retry");if(retry.disabled)return;
  retry.disabled=true;retry.textContent="正在加载…";
  try{await loadGallery();await loadSession();}
  catch(error){lockGallery(error.message);showToast(error.message);}
  finally{retry.disabled=false;retry.textContent="重试加载";}
}

function el(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function icon(name){const node=el("i",`ph ph-${name}`);node.setAttribute("aria-hidden","true");return node;}
function showToast(message){clearTimeout(toastTimer);$("#toast").textContent=message;$("#toast").hidden=false;toastTimer=setTimeout(()=>$("#toast").hidden=true,3200);}
function open(dialog){if(!dialog.open)dialog.showModal();}
function close(dialog){if(dialog.open)dialog.close();}
function avatar(item){const node=el("span","avatar",item.avatar);node.style.background=item.color;return node;}
function visibleItems(){
  const query=$("#search").value.trim().toLocaleLowerCase();
  return items.filter(item=>item.category===category&&(view!=="saved"||favorites.has(item.id))&&(view!=="mine"||item.mine)&&(!query||[item.caption,item.title].some(value=>(value||"").toLocaleLowerCase().includes(query)))).sort((a,b)=>$("#sort").value==="saved"?b.saves-a.saves:b.order-a.order);
}
function render(){
  imageObserver.disconnect();
  const matches=visibleItems();
  $("#gallery").replaceChildren(...matches.map(item=>{
    const card=el("article","image-card");card.dataset.id=item.id;
    const wrap=el("div","card-image-wrap"),button=el("button",`card-image-button ratio-${item.ratio}`);button.type="button";button.setAttribute("aria-label",`查看图片：${item.title}`);
    const image=el("img");image.dataset.source=String(item.cover);image.alt=item.title;image.style.objectPosition=item.position||"50% 50%";
    const status=el("span","image-status","图片加载中…");status.hidden=!liveMode;button.append(image,status);
    button.addEventListener("click",()=>{if(button.dataset.loadState==="error")setImage(image,item.cover,false,true);else openViewer(item);});wrap.append(button);
    if(item.images.length>1){const count=el("span","image-count");count.append(icon("images"),document.createTextNode(`${item.images.length} 张`));wrap.append(count);}
    const save=el("button",`card-save${favorites.has(item.id)?" saved":""}`);save.type="button";save.setAttribute("aria-label",`${favorites.has(item.id)?"取消收藏":"收藏"}${item.title}`);save.setAttribute("aria-pressed",String(favorites.has(item.id)));save.append(icon("heart"));save.addEventListener("click",()=>toggleSave(item));wrap.append(save);card.append(wrap);
    const bottom=el("div","card-bottom"),title=el("p","picture-title",item.title);
    const count=el("span","card-saves");count.append(icon("heart"),document.createTextNode(`${item.saves}`));
    bottom.append(title,count);card.append(bottom);return card;
  }));
  // Observe attached cards against our own scroller, independent of host iframe clipping.
  document.querySelectorAll("#gallery img").forEach(image=>setImage(image,image.dataset.source,true));
  if(liveMode)requestAnimationFrame(loadVisibleImages);
  $("#empty").hidden=matches.length>0;
  $("#empty-message").textContent=view!=="shared"&&!authenticated?"从 DZMM 登录后，可以查看你的收藏和上传。":view==="saved"?"在这个分区遇到喜欢的图，点一下爱心就留下啦。":view==="mine"?"这个分区还没有你上传的图。":"换个词找找，或者来传第一张。";
  $("#empty-upload").hidden=view==="saved"||!canEdit;
  $("#empty h2").textContent=liveMode&&!memberReady?"画廊暂时没加载出来":"这里还空着呢";
  $("#gallery-retry").hidden=!liveMode||memberReady;
  if(liveMode&&!memberReady){$("#empty-message").textContent=accessMessage;$("#empty-upload").hidden=true;}
  $("#category-description").textContent=categoryCopy[category];
  document.querySelectorAll(".category").forEach(node=>{const active=node.dataset.category===category;node.classList.toggle("active",active);node.setAttribute("aria-selected",String(active));});
}
function setCategory(next){category=next;render();$("#app-shell").scrollTo({top:0,behavior:"instant"});}
function setView(next){
  view=next;$("#search").value="";
  if(view==="saved"){const first=items.find(item=>favorites.has(item.id));if(first)category=first.category;}
  if(view==="mine"){const first=items.find(item=>item.mine);if(first)category=first.category;}
  $("#view-title").replaceChildren(document.createTextNode(view==="shared"?"群里的好图，":view==="saved"?"喜欢的图，":"我分享的，"),el("span","",view==="shared"?"都放这儿。":view==="saved"?"慢慢攒着。":"都在这儿。"));
  $("#view-description").textContent=view==="shared"?"你的立绘、我们的日常，还有一些奇奇怪怪的可爱。":view==="saved"?"不怕聊天刷过去，回头还能找到。":"一张也好，一组也行，好看的就一起分享。";
  document.querySelectorAll("[data-view]").forEach(node=>node.classList.toggle("active",node.dataset.view===view));render();$("#app-shell").scrollTo({top:0,behavior:"instant"});
}
async function toggleSave(item){
  const saved=!favorites.has(item.id);
  if(liveMode){try{await api(`/images/${item.id}/favorite`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({saved})},true);}catch(error){showToast(error.message);return;}}
  item.saves+=saved?1:-1;if(saved){favorites.add(item.id);showToast(liveMode?"已存到「收藏」":"已存到本页的「收藏」，刷新后清空");}else{favorites.delete(item.id);showToast("已从喜欢里移除");}render();updateSave();
}
function updateSave(){if(!current)return;const saved=favorites.has(current.id);$("#viewer-save").classList.toggle("saved",saved);$("#viewer-save").setAttribute("aria-pressed",String(saved));$("#viewer-save span").textContent=saved?"已经存好":"存到喜欢";}
function updateImage(){
  if(!current)return;
  const image=$("#viewer-image");setImage(image,current.images[imageIndex]);image.alt=`${current.title} · 第 ${imageIndex+1} 张`;
  $("#viewer-page").textContent=`${imageIndex+1} / ${current.images.length}`;$("#viewer-page").hidden=current.images.length===1;
  $("#previous").hidden=current.images.length===1;$("#next").hidden=current.images.length===1;
  $("#previous").disabled=imageIndex===0;$("#next").disabled=imageIndex===current.images.length-1;
  $("#image-scroll").classList.remove("zoomed");$("#viewer-zoom").setAttribute("aria-pressed","false");
  const download=$("#viewer-download"),source=current.images[imageIndex];download.removeAttribute("href");sourceURL(source).then(url=>{if(current?.images[imageIndex]===source&&memberReady)download.href=url;}).catch(error=>showToast(error.message));download.download=downloadFilename(current,imageIndex);
  $("#viewer-thumbs").hidden=current.images.length===1;
  $("#viewer-thumbs").replaceChildren(...current.images.map((src,index)=>{const button=el("button",`viewer-thumb${index===imageIndex?" active":""}`);button.type="button";button.setAttribute("aria-label",`查看第 ${index+1} 张`);button.setAttribute("aria-pressed",String(index===imageIndex));const thumb=el("img");setImage(thumb,src);thumb.alt="";button.append(thumb);button.addEventListener("click",()=>{imageIndex=index;updateImage();});return button;}));
}
function downloadFilename(item,index=0){
  const extension=(item.filenames?.[index]||"").match(/\.(png|jpe?g|webp)$/i)?.[0]||".jpg";
  return (item.title||"未命名图片").replace(/[\\/:*?"<>|\x00-\x1f\x7f]/g,"_").replace(/[ .]+$/g,"").slice(0,80)+extension;
}
function openViewer(item){
  current=item;imageIndex=0;$("#viewer-category").textContent=item.category;
  $("#viewer-name").textContent=item.title;$("#viewer-title-input").value=item.title;
  $("#viewer-id").textContent=liveMode?`图片编号：${item.id}`:"本地预览图片";
  $("#viewer-caption").textContent=item.caption||"";$("#title-error").hidden=true;$("#title-status").hidden=true;
  $("#viewer-category-select").value=item.category;$("#move-error").hidden=true;$("#move-status").hidden=true;updateMoveButton();
  $("#sample-source").hidden=liveMode||item.mine;updateImage();updateSave();updatePermissions();open($("#viewer"));$("#viewer").scrollTop=0;
}
async function renameCurrent(){
  if(!current||!canEdit||$("#title-submit").disabled)return;
  const item=current,title=$("#viewer-title-input").value.trim();
  $("#title-status").hidden=true;
  if(!title||title.length>80){$("#title-error").textContent="请输入 1 到 80 字的图片名称。";$("#title-error").hidden=false;return;}
  $("#title-submit").disabled=true;$("#title-submit").textContent="正在保存…";
  try{
    if(liveMode)await api(`/images/${item.id}/title`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({title})},true);
    item.title=title;render();$("#viewer-name").textContent=title;$("#viewer-download").download=downloadFilename(item,imageIndex);$("#viewer-image").alt=title;$("#title-error").hidden=true;$("#title-status").textContent="图片名称已保存";$("#title-status").hidden=false;showToast("图片名称已更新");
  }catch(error){$("#title-error").textContent=error.message;$("#title-error").hidden=false;}
  finally{$("#title-submit").disabled=false;$("#title-submit").textContent="保存名称";}
}
let moving=false;
function updateMoveButton(){
  $("#move-submit").disabled=moving||!current||$("#viewer-category-select").value===current.category;
  $("#viewer-category-select").disabled=moving;
}
async function moveCurrent(){
  if(!current||!canEdit||moving)return;
  const item=current,target=$("#viewer-category-select").value;
  if(target===item.category)return;
  moving=true;updateMoveButton();$("#move-submit").textContent="正在移动…";
  $("#move-status").hidden=true;$("#move-error").hidden=true;
  try{
    if(liveMode)await api(`/images/${item.id}/category`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({category:target})},true);
    item.category=target;category=target;render();
    if(current?.id===item.id){$("#viewer-category").textContent=target;$("#move-status").textContent=`已移到「${target}」`;$("#move-status").hidden=false;}
    showToast(liveMode?`已移到「${target}」`:`已移到「${target}」，本地预览刷新后重置`);
  }catch(error){if(current?.id===item.id){$("#move-error").textContent=error.message;$("#move-error").hidden=false;}}
  finally{moving=false;$("#move-submit").textContent="移动图片";updateMoveButton();}
}
$("#viewer-category-select").addEventListener("change",()=>{$("#move-status").hidden=true;$("#move-error").hidden=true;updateMoveButton();});
$("#move-submit").addEventListener("click",moveCurrent);
let deleteTarget=null,deleting=false;
function askDelete(){
  if(!current||!canEdit||deleting)return;
  deleteTarget={id:current.id,title:current.title};
  $("#delete-target").textContent=`「${current.title}」${liveMode?` · 编号 ${current.id}`:""}`;
  $("#delete-description").textContent=liveMode?`删除后，画廊、收藏和我的中都不再显示。误删可在妹子群发送 /恢复图片 ${current.id} 找回。`:"从本地预览中删除这张图，刷新页面后重置。";
  $("#delete-error").hidden=true;open($("#delete-dialog"));
}
async function deletePicture(){
  if(!deleteTarget||!canEdit||deleting)return;
  const target=deleteTarget;deleting=true;$("#delete-confirm").disabled=true;$("#delete-cancel").disabled=true;
  try{
    if(liveMode)await api(`/images/${target.id}`,{method:"DELETE"},true);
    const index=items.findIndex(item=>item.id===target.id);if(index>=0)items.splice(index,1);
    favorites.delete(target.id);
    close($("#delete-dialog"));if(current?.id===target.id){close($("#viewer"));current=null;}
    render();showToast(liveMode?`图片已删除；误删可发送 /恢复图片 ${target.id}`:"已从本地预览删除，刷新后重置");deleteTarget=null;
  }catch(error){$("#delete-error").textContent=error.message;$("#delete-error").hidden=false;}
  finally{deleting=false;$("#delete-confirm").disabled=false;$("#delete-cancel").disabled=false;}
}
$("#viewer-delete").addEventListener("click",askDelete);
$("#delete-confirm").addEventListener("click",deletePicture);
$("#delete-cancel").addEventListener("click",()=>{if(!deleting)close($("#delete-dialog"));});
$("#delete-dialog").addEventListener("cancel",event=>{if(deleting)event.preventDefault();});

function moveImage(direction){if(!current)return;const next=imageIndex+direction;if(next>=0&&next<current.images.length){imageIndex=next;updateImage();}}
function uploadTab(group){$("#device-tab").classList.toggle("active",!group);$("#group-tab").classList.toggle("active",group);$("#upload-form").hidden=group;$("#group-guide").hidden=!group;}
function openUpload(group=false){if(liveMode&&!canEdit){loadSession().then(()=>{if(canEdit)openUpload(group);else showToast("所有人都能看图，上传和编辑仅限妹子群成员。请从 DZMM 登录后重试。");});return;}uploadTab(group);$("input[name='category'][value='"+category+"']").checked=true;open($("#upload-dialog"));}
function revoke(url){URL.revokeObjectURL(url);objectURLs.delete(url);}
async function addFiles(files){
  if(adding){showToast("图片还在读取，稍等一下～");return;}
  adding=true;$("#publish").disabled=true;const errors=[];
  try{
    for(const file of files){
      if(selectedFiles.length>=30){errors.push("预览一次最多放 30 张图。");break;}
      if(!["image/jpeg","image/png","image/webp"].includes(file.type)){errors.push(`${file.name}：换成 JPG、PNG 或 WebP 再试试。`);continue;}
      if(file.size>10*1024*1024){errors.push(`${file.name} 超过 10 MB，缩小一点再来。`);continue;}
      if(selectedFiles.reduce((total,item)=>total+item.file.size,0)+file.size>100*1024*1024){errors.push("这组图超过 100 MB 了，少放几张试试。");break;}
      const url=URL.createObjectURL(file);objectURLs.add(url);
      try{const image=new Image();image.src=url;await image.decode();if(image.naturalWidth*image.naturalHeight>40000000){revoke(url);errors.push(`${file.name} 尺寸太大，缩小后再试。`);continue;}selectedFiles.push({file,url,width:image.naturalWidth,height:image.naturalHeight});}
      catch{revoke(url);errors.push(`${file.name} 读不出来，换张图片试试。`);}
    }
  }finally{adding=false;$("#publish").disabled=false;$("#file-input").value="";}
  $("#upload-error").textContent=errors.join(" ");$("#upload-error").hidden=!errors.length;renderPreviews();
}
function renderPreviews(){
  $("#preview-heading").hidden=selectedFiles.length===0;$("#preview-count").textContent=`选了 ${selectedFiles.length} 张`;
  $("#upload-previews").replaceChildren(...selectedFiles.map((item,index)=>{
    const card=el("div","upload-preview"),image=el("img");image.src=item.url;image.alt=`第 ${index+1} 张：${item.file.name}`;const controls=el("div","page-controls");controls.append(el("span","",!liveMode&&index===0?"封面":`${index+1}`));
    for(const [direction,symbol,name]of [[-1,"←","前移"],[1,"→","后移"]]){const button=el("button","",symbol);button.type="button";button.disabled=index+direction<0||index+direction>=selectedFiles.length;button.setAttribute("aria-label",`${name}第 ${index+1} 张`);button.addEventListener("click",()=>{[selectedFiles[index],selectedFiles[index+direction]]=[selectedFiles[index+direction],selectedFiles[index]];renderPreviews();});controls.append(button);}
    const remove=el("button","remove","×");remove.type="button";remove.setAttribute("aria-label",`移除第 ${index+1} 张`);remove.addEventListener("click",()=>{revoke(item.url);selectedFiles.splice(index,1);renderPreviews();});controls.append(remove);card.append(image,controls);return card;
  }));
}

// 以浏览器的真实全屏状态为准，Esc 或宿主退出后同步恢复按钮。
function syncFullscreen(){
  const active=Boolean(document.fullscreenElement||document.webkitFullscreenElement);
  const button=$("#desktop-mode");
  button.querySelector("span").textContent=active?"退出全屏":"电脑";
  button.setAttribute("aria-pressed",String(active));
  button.title=active?"退出全屏，回到竖屏图集":"全屏展开图集";
  if(liveMode)requestAnimationFrame(loadVisibleImages);
}
async function toggleFullscreen(){
  const button=$("#desktop-mode");button.disabled=true;
  try{
    if(document.fullscreenElement||document.webkitFullscreenElement){
      const exit=document.exitFullscreen||document.webkitExitFullscreen;
      if(!exit)throw new Error("unsupported");
      await exit.call(document);
    }else{
      const enter=document.documentElement.requestFullscreen||document.documentElement.webkitRequestFullscreen;
      if(!enter)throw new Error("unsupported");
      await enter.call(document.documentElement);
    }
  }catch{showToast("当前浏览器暂不能全屏，请在电脑浏览器中打开图集后重试。");}
  finally{button.disabled=false;syncFullscreen();}
}
$("#desktop-mode").addEventListener("click",toggleFullscreen);
document.addEventListener("fullscreenchange",syncFullscreen);
document.addEventListener("webkitfullscreenchange",syncFullscreen);

document.querySelectorAll("[data-view]").forEach(node=>node.addEventListener("click",()=>setView(node.dataset.view)));
document.querySelectorAll("[data-category]").forEach(node=>node.addEventListener("click",()=>setCategory(node.dataset.category)));
$(".brand").addEventListener("click",event=>{event.preventDefault();category=sections[0];setView("shared");});
$("#search").addEventListener("input",render);$("#sort").addEventListener("change",render);
for(const id of ["upload-open","empty-upload"])$("#"+id).addEventListener("click",()=>openUpload());
$("#gallery-retry").addEventListener("click",refreshGallery);
// 平台 iframe 未开放原生 form 提交；保存使用按钮事件和 fetch。
$("#title-submit").addEventListener("click",renameCurrent);
$("#title-editor").addEventListener("submit",event=>event.preventDefault());
$("#viewer-title-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.isComposing){event.preventDefault();renameCurrent();}});
$("#group-open").addEventListener("click",()=>openUpload(true));
$("#device-tab").addEventListener("click",()=>uploadTab(false));$("#group-tab").addEventListener("click",()=>uploadTab(true));
$("#upload-close").addEventListener("click",()=>close($("#upload-dialog")));
$("#viewer-close").addEventListener("click",()=>close($("#viewer")));$("#viewer-save").addEventListener("click",()=>current&&toggleSave(current));
$("#previous").addEventListener("click",()=>moveImage(-1));$("#next").addEventListener("click",()=>moveImage(1));
$("#viewer-zoom").addEventListener("click",()=>{const zoomed=$("#image-scroll").classList.toggle("zoomed");$("#viewer-zoom").setAttribute("aria-pressed",String(zoomed));});
for(const id of ["info-open","footer-info"])$("#"+id).addEventListener("click",()=>open($("#info-dialog")));
document.querySelectorAll(".info-close").forEach(node=>node.addEventListener("click",()=>close($("#info-dialog"))));
document.querySelectorAll("dialog").forEach(dialog=>dialog.addEventListener("click",event=>{if(event.target===dialog&&!(dialog.id==="delete-dialog"&&deleting)){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}}));
$("#file-input").addEventListener("change",event=>addFiles([...event.target.files]));
for(const name of ["dragenter","dragover"])$("#dropzone").addEventListener(name,event=>{event.preventDefault();$("#dropzone").classList.add("dragging");});
for(const name of ["dragleave","drop"])$("#dropzone").addEventListener(name,event=>{event.preventDefault();$("#dropzone").classList.remove("dragging");if(name==="drop")addFiles([...event.dataTransfer.files]);});
async function uploadPictures(){
  if(adding)return;
  if(!selectedFiles.length){$("#upload-error").textContent="先选一张喜欢的图片吧。";$("#upload-error").hidden=false;return;}
  const selectedCategory=$("input[name='category']:checked").value,first=selectedFiles[0],ratio=first.width/first.height;
  if(liveMode){
    const form=new FormData();form.append("category",selectedCategory);form.append("caption",$("#caption-input").value.trim());form.append("title",$("#title-input").value.trim());selectedFiles.forEach(item=>form.append("images",item.file));
    adding=true;$("#publish").disabled=true;$("#publish").textContent="正在上传…";
    try{await api("/upload",{method:"POST",body:form},true);selectedFiles.forEach(item=>revoke(item.url));selectedFiles=[];$("#upload-form").reset();renderPreviews();close($("#upload-dialog"));category=selectedCategory;showToast("已保存到图集啦～");await refreshGallery();setView("shared");}
    catch(error){$("#upload-error").textContent=error.message;$("#upload-error").hidden=false;}
    finally{adding=false;$("#publish").disabled=false;$("#publish").textContent="放进图集";}return;
  }
  items.unshift({id:`upload-${Date.now()}`,category:selectedCategory,cover:first.url,images:selectedFiles.map(item=>item.url),filenames:selectedFiles.map(item=>item.file.name),ratio:ratio<.9?"portrait":ratio>1.5?"wide":ratio>1.1?"landscape":"square",caption:$("#caption-input").value.trim(),title:$("#title-input").value.trim()||first.file.name.replace(/\.[^.]+$/,""),saves:0,name:"小小糯",avatar:"糯",color:"#e8dfc6",mine:true,order:Date.now()});
  selectedFiles=[];$("#upload-form").reset();$("#upload-error").hidden=true;renderPreviews();close($("#upload-dialog"));category=selectedCategory;setView("shared");showToast("放进预览图集啦，刷新后会清空");
}
$("#publish").addEventListener("click",uploadPictures);
$("#upload-form").addEventListener("submit",event=>event.preventDefault());
$("#title-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.isComposing){event.preventDefault();uploadPictures();}});
document.addEventListener("keydown",event=>{
  if($("#viewer").open&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){if(event.key==="ArrowLeft"){event.preventDefault();moveImage(-1);}if(event.key==="ArrowRight"){event.preventDefault();moveImage(1);}}
  if(event.key==="/"&&!document.querySelector("dialog[open]")&&!["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName)){event.preventDefault();$("#search").focus();}
});
window.addEventListener("beforeunload",()=>objectURLs.forEach(url=>URL.revokeObjectURL(url)));
$("#app-shell").addEventListener("scroll",()=>{if(liveMode)requestAnimationFrame(loadVisibleImages);},{passive:true});
window.addEventListener("resize",()=>{if(liveMode)requestAnimationFrame(loadVisibleImages);});
render();
if(liveMode){
  $(".sample-badge").textContent="所有人可查看";
  $(".upload-footer span").textContent="保存到妹子群的共享图集";
  $("#footer-info").textContent="所有人可看 · 妹子群可传图";
  $("#info-open").setAttribute("aria-label","查看图集说明");
  $("#preview-heading span:last-child").textContent="每张图片分别收进所选分区";
  $("#info-dialog p:last-of-type").textContent="所有人可查看和保存原图；当前妹子群成员可上传、编辑图片名称和删除图片。大图中可查看图片编号，群里发送 /删除图片 编号，误删发送 /恢复图片 编号。群里回复原图发送 /保存 分区名 图片名称，名称可不填。";
  $("#empty-message").textContent="正在加载画廊…";
  refreshGallery();
}
