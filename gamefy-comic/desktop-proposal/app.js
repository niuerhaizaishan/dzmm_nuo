"use strict";

// 未接服务的本地页面仍是预览；Gamefy 内验权失败时保持空白，不回退示例数据。
const apiBase=(window.GALLERY_API_BASE||"").replace(/\/$/,"");
const liveMode=Boolean(apiBase||window.dzmm?.user?.info);
let memberReady=!liveMode;
let accessMessage="正在核验妹子群成员身份…";
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
const items=liveMode?[]:samples.map((row,index)=>({id:`sample-${index}`,category:row[0],cover:`assets/gallery/${row[1]}.jpg`,ratio:row[2],caption:row[3],saves:row[4],images:row[5].map(id=>`assets/gallery/${id}.jpg`),position:row[6],...authors[index%authors.length],order:samples.length-index,mine:false}));
const $=selector=>document.querySelector(selector);
const favorites=new Set(),objectURLs=new Set();
let view="shared",category=sections[0],current=null,imageIndex=0,selectedFiles=[],adding=false,toastTimer;
const sourceCache=new Map();
const imageObserver=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){imageObserver.unobserve(entry.target);setImage(entry.target,entry.target.dataset.source);}}),{rootMargin:"400px"});
async function api(path,options={}){
  if(!apiBase)throw new Error("图集服务还未接通，请稍后再来。");
  const player=await window.dzmm?.user?.info();
  if(!player?.token)throw new Error("请在 DZMM 中登录后打开图集，仅限妹子群成员。");
  const response=await fetch(apiBase+path,{...options,credentials:"omit",headers:{...options.headers,Authorization:`Bearer ${player.token}`}});
  if(!response.ok){const message=(await response.text()).slice(0,200);if(response.status===401||response.status===403)lockGallery(message);throw new Error(message||"图集暂时不可用，请稍后再试。");}
  return response;
}
function lockGallery(message){
  accessMessage=message;
  memberReady=false;items.length=0;favorites.clear();sourceCache.clear();
  objectURLs.forEach(url=>URL.revokeObjectURL(url));objectURLs.clear();selectedFiles=[];
  document.querySelectorAll("dialog[open]").forEach(dialog=>dialog.close());
  render();$("#empty-message").textContent=message;$("#empty-upload").hidden=true;
}
async function sourceURL(source){
  if(!liveMode)return source;
  source=String(source);
  if(!sourceCache.has(source))sourceCache.set(source,(async()=>{const response=await api(`/images/${source}`);const url=URL.createObjectURL(await response.blob());objectURLs.add(url);return url;})().catch(error=>{sourceCache.delete(source);throw error;}));
  return sourceCache.get(source);
}
function setImage(image,source,lazy=false){
  image.dataset.source=String(source);
  if(!liveMode){image.src=source;return;}
  if(lazy){imageObserver.observe(image);return;}
  sourceURL(source).then(url=>{if(memberReady&&image.dataset.source===String(source))image.src=url;}).catch(error=>showToast(error.message));
}
async function loadGallery(){
  const result=await (await api("/items")).json();memberReady=true;
  items.splice(0,items.length,...result.items.map(row=>({id:row.id,category:row.category,cover:row.id,images:[row.id],filenames:[row.filename],ratio:row.width/row.height<.9?"portrait":row.width/row.height>1.5?"wide":"landscape",caption:row.caption,name:row.name,avatar:row.name.slice(0,1),color:"#e8dfc6",mine:row.uploader===result.uid,order:row.created,saves:row.saves-Number(row.saved)})));
  favorites.clear();result.items.filter(row=>row.saved).forEach(row=>favorites.add(row.id));render();
}

function el(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
function icon(name){const node=el("i",`ph ph-${name}`);node.setAttribute("aria-hidden","true");return node;}
function showToast(message){clearTimeout(toastTimer);$("#toast").textContent=message;$("#toast").hidden=false;toastTimer=setTimeout(()=>$("#toast").hidden=true,3200);}
function open(dialog){if(!dialog.open)dialog.showModal();}
function close(dialog){if(dialog.open)dialog.close();}
function avatar(item){const node=el("span","avatar",item.avatar);node.style.background=item.color;return node;}
function visibleItems(){
  const query=$("#search").value.trim().toLocaleLowerCase();
  return items.filter(item=>item.category===category&&(view!=="saved"||favorites.has(item.id))&&(view!=="mine"||item.mine)&&(!query||[item.caption,item.name].some(value=>value.toLocaleLowerCase().includes(query)))).sort((a,b)=>$("#sort").value==="saved"?(b.saves+Number(favorites.has(b.id)))-(a.saves+Number(favorites.has(a.id))):b.order-a.order);
}
function render(){
  imageObserver.disconnect();
  const matches=visibleItems();
  $("#gallery").replaceChildren(...matches.map(item=>{
    const card=el("article","image-card");card.dataset.id=item.id;
    const wrap=el("div","card-image-wrap"),button=el("button",`card-image-button ratio-${item.ratio}`);button.type="button";button.setAttribute("aria-label",`查看${item.name}分享的图片：${item.caption||item.category}`);
    const image=el("img");setImage(image,item.cover,true);image.alt=item.caption||`${item.name}分享的${item.category}`;image.loading="lazy";image.style.objectPosition=item.position||"50% 50%";button.append(image);button.addEventListener("click",()=>openViewer(item));wrap.append(button);
    if(item.images.length>1){const count=el("span","image-count");count.append(icon("images"),document.createTextNode(`${item.images.length} 张`));wrap.append(count);}
    const save=el("button",`card-save${favorites.has(item.id)?" saved":""}`);save.type="button";save.setAttribute("aria-label",`${favorites.has(item.id)?"取消收藏":"收藏"}${item.caption||item.category}`);save.setAttribute("aria-pressed",String(favorites.has(item.id)));save.append(icon("heart"));save.addEventListener("click",()=>toggleSave(item));wrap.append(save);card.append(wrap);
    if(item.caption)card.append(el("p","image-caption",item.caption));
    const bottom=el("div","card-bottom"),person=el("span","card-person");person.append(avatar(item),document.createTextNode(item.name));const count=el("span","card-saves");count.append(icon("heart"),document.createTextNode(`${item.saves+Number(favorites.has(item.id))}`));bottom.append(person,count);card.append(bottom);return card;
  }));
  $("#empty").hidden=matches.length>0;
  $("#empty-message").textContent=view==="saved"?"在这个分区遇到喜欢的图，点一下爱心就留下啦。":view==="mine"?"这个分区还没传过图，来放第一张吧。":"换个词找找，或者来传第一张。";
  $("#empty-upload").hidden=view==="saved";
  $("#empty h2").textContent=liveMode&&!memberReady?"妹子群的专属图集":"这里还空着呢";
  if(liveMode&&!memberReady){$("#empty-message").textContent=accessMessage;$("#empty-upload").hidden=true;}
  $("#category-description").textContent=categoryCopy[category];
  document.querySelectorAll(".category").forEach(node=>{const active=node.dataset.category===category;node.classList.toggle("active",active);node.setAttribute("aria-selected",String(active));});
}
function setCategory(next){category=next;render();}
function setView(next){
  view=next;$("#search").value="";
  if(view==="saved"){const first=items.find(item=>favorites.has(item.id));if(first)category=first.category;}
  if(view==="mine"){const first=items.find(item=>item.mine);if(first)category=first.category;}
  $("#view-title").replaceChildren(document.createTextNode(view==="shared"?"群里的好图，":view==="saved"?"喜欢的图，":"我分享的，"),el("span","",view==="shared"?"都放这儿。":view==="saved"?"慢慢攒着。":"都在这儿。"));
  $("#view-description").textContent=view==="shared"?"你的立绘、我们的日常，还有一些奇奇怪怪的可爱。":view==="saved"?"不怕聊天刷过去，回头还能找到。":"一张也好，一组也行，好看的就一起分享。";
  document.querySelectorAll("[data-view]").forEach(node=>node.classList.toggle("active",node.dataset.view===view));render();window.scrollTo({top:0,behavior:"instant"});
}
async function toggleSave(item){
  const saved=!favorites.has(item.id);
  if(liveMode){try{await api(`/images/${item.id}/favorite`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({saved})});}catch(error){showToast(error.message);return;}}
  if(saved){favorites.add(item.id);showToast(liveMode?"已存到「我存的」":"已存到本页的「我存的」，刷新后清空");}else{favorites.delete(item.id);showToast("已从喜欢里移除");}render();updateSave();
}
function updateSave(){if(!current)return;const saved=favorites.has(current.id);$("#viewer-save").classList.toggle("saved",saved);$("#viewer-save").setAttribute("aria-pressed",String(saved));$("#viewer-save span").textContent=saved?"已经存好":"存到喜欢";}
function updateImage(){
  if(!current)return;
  const image=$("#viewer-image");setImage(image,current.images[imageIndex]);image.alt=`${current.caption||current.category} · 第 ${imageIndex+1} 张`;
  $("#viewer-page").textContent=`${imageIndex+1} / ${current.images.length}`;$("#viewer-page").hidden=current.images.length===1;
  $("#previous").hidden=current.images.length===1;$("#next").hidden=current.images.length===1;
  $("#previous").disabled=imageIndex===0;$("#next").disabled=imageIndex===current.images.length-1;
  $("#image-scroll").classList.remove("zoomed");$("#viewer-zoom").setAttribute("aria-pressed","false");
  const download=$("#viewer-download"),source=current.images[imageIndex];download.removeAttribute("href");sourceURL(source).then(url=>{if(current?.images[imageIndex]===source&&memberReady)download.href=url;}).catch(error=>showToast(error.message));download.download=current.filenames?.[imageIndex]||`姐妹图集-${current.id}-${imageIndex+1}.jpg`;
  $("#viewer-thumbs").hidden=current.images.length===1;
  $("#viewer-thumbs").replaceChildren(...current.images.map((src,index)=>{const button=el("button",`viewer-thumb${index===imageIndex?" active":""}`);button.type="button";button.setAttribute("aria-label",`查看第 ${index+1} 张`);button.setAttribute("aria-pressed",String(index===imageIndex));const thumb=el("img");setImage(thumb,src);thumb.alt="";button.append(thumb);button.addEventListener("click",()=>{imageIndex=index;updateImage();});return button;}));
}
function openViewer(item){current=item;imageIndex=0;$("#viewer-category").textContent=item.category;$("#viewer-name").textContent=item.name;$("#viewer-caption").textContent=item.caption||"随手分享一张喜欢的图。";$("#viewer-avatar").textContent=item.avatar;$("#viewer-avatar").style.background=item.color;$("#sample-source").hidden=liveMode||item.mine;updateImage();updateSave();open($("#viewer"));$("#viewer").scrollTop=0;}
function moveImage(direction){if(!current)return;const next=imageIndex+direction;if(next>=0&&next<current.images.length){imageIndex=next;updateImage();}}
function uploadTab(group){$("#device-tab").classList.toggle("active",!group);$("#group-tab").classList.toggle("active",group);$("#upload-form").hidden=group;$("#group-guide").hidden=!group;}
function openUpload(group=false){if(liveMode&&!memberReady){showToast("仅限当前妹子群成员，请从 DZMM 登录后打开。");return;}uploadTab(group);$("input[name='category'][value='"+category+"']").checked=true;open($("#upload-dialog"));}
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

document.querySelectorAll("[data-view]").forEach(node=>node.addEventListener("click",()=>setView(node.dataset.view)));
document.querySelectorAll("[data-category]").forEach(node=>node.addEventListener("click",()=>setCategory(node.dataset.category)));
$(".brand").addEventListener("click",event=>{event.preventDefault();category=sections[0];setView("shared");});
$("#search").addEventListener("input",render);$("#sort").addEventListener("change",render);
for(const id of ["upload-open","empty-upload"])$("#"+id).addEventListener("click",()=>openUpload());
$("#group-open").addEventListener("click",()=>openUpload(true));
$("#device-tab").addEventListener("click",()=>uploadTab(false));$("#group-tab").addEventListener("click",()=>uploadTab(true));
$("#upload-close").addEventListener("click",()=>close($("#upload-dialog")));
$("#viewer-close").addEventListener("click",()=>close($("#viewer")));$("#viewer-save").addEventListener("click",()=>current&&toggleSave(current));
$("#previous").addEventListener("click",()=>moveImage(-1));$("#next").addEventListener("click",()=>moveImage(1));
$("#viewer-zoom").addEventListener("click",()=>{const zoomed=$("#image-scroll").classList.toggle("zoomed");$("#viewer-zoom").setAttribute("aria-pressed",String(zoomed));});
for(const id of ["info-open","footer-info"])$("#"+id).addEventListener("click",()=>open($("#info-dialog")));
document.querySelectorAll(".info-close").forEach(node=>node.addEventListener("click",()=>close($("#info-dialog"))));
document.querySelectorAll("dialog").forEach(dialog=>dialog.addEventListener("click",event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}}));
$("#file-input").addEventListener("change",event=>addFiles([...event.target.files]));
for(const name of ["dragenter","dragover"])$("#dropzone").addEventListener(name,event=>{event.preventDefault();$("#dropzone").classList.add("dragging");});
for(const name of ["dragleave","drop"])$("#dropzone").addEventListener(name,event=>{event.preventDefault();$("#dropzone").classList.remove("dragging");if(name==="drop")addFiles([...event.dataTransfer.files]);});
$("#upload-form").addEventListener("submit",async event=>{
  event.preventDefault();if(adding)return;
  if(!selectedFiles.length){$("#upload-error").textContent="先选一张喜欢的图片吧。";$("#upload-error").hidden=false;return;}
  const selectedCategory=$("input[name='category']:checked").value,first=selectedFiles[0],ratio=first.width/first.height;
  if(liveMode){
    const form=new FormData();form.append("category",selectedCategory);form.append("caption",$("#caption-input").value.trim());selectedFiles.forEach(item=>form.append("images",item.file));
    adding=true;$("#publish").disabled=true;
    try{await api("/upload",{method:"POST",body:form});selectedFiles.forEach(item=>revoke(item.url));selectedFiles=[];$("#upload-form").reset();renderPreviews();close($("#upload-dialog"));category=selectedCategory;await loadGallery();setView("shared");showToast("已保存到图集啦～");}
    catch(error){$("#upload-error").textContent=error.message;$("#upload-error").hidden=false;}
    finally{adding=false;$("#publish").disabled=false;}return;
  }
  items.unshift({id:`upload-${Date.now()}`,category:selectedCategory,cover:first.url,images:selectedFiles.map(item=>item.url),filenames:selectedFiles.map(item=>item.file.name),ratio:ratio<.9?"portrait":ratio>1.5?"wide":ratio>1.1?"landscape":"square",caption:$("#caption-input").value.trim(),saves:0,name:"小小糯",avatar:"糯",color:"#e8dfc6",mine:true,order:Date.now()});
  selectedFiles=[];$("#upload-form").reset();$("#upload-error").hidden=true;renderPreviews();close($("#upload-dialog"));category=selectedCategory;setView("shared");showToast("放进预览图集啦，刷新后会清空");
});
document.addEventListener("keydown",event=>{
  if($("#viewer").open&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){if(event.key==="ArrowLeft"){event.preventDefault();moveImage(-1);}if(event.key==="ArrowRight"){event.preventDefault();moveImage(1);}}
  if(event.key==="/"&&!document.querySelector("dialog[open]")&&!["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName)){event.preventDefault();$("#search").focus();}
});
window.addEventListener("beforeunload",()=>objectURLs.forEach(url=>URL.revokeObjectURL(url)));
render();
if(liveMode){
  $(".sample-badge").textContent="仅限妹子群成员";
  $(".upload-footer span").textContent="保存到妹子群的共享图集";
  $("#footer-info").textContent="妹子群专属 · 好图一起存";
  $("#info-open").setAttribute("aria-label","查看图集说明");
  $("#preview-heading span:last-child").textContent="每张图片分别收进所选分区";
  $("#info-dialog p:last-of-type").textContent="仅当前妹子群成员可查看、上传和保存图片。群里回复原图发送 /保存 分区名，也能收进同一图集。";
  $("#empty-message").textContent="正在核验妹子群成员身份…";
  loadGallery().catch(error=>{lockGallery(error.message);showToast(error.message);});
}
