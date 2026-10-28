"use strict";
const $ = id => document.getElementById(id);
const names = {planner:"方案规划员",reviewer:"方案审查员",evaluator:"结果评估员",strategist:"迭代策略员",scheduler:"全局调度员"};
let source, current="", cards=new Map(), callCount=0, tokenSum=0, usageCount=0, toolCount=0, trials=0;
let savedView={};try{const value=JSON.parse(sessionStorage.getItem("agent-reader-position")||"{}");if(value&&typeof value==="object")savedView=value;}catch{}
let pendingScroll=null, selectionVersion=0, trialLoading=false, trialLoaded=false, replayReady=false;
let trialCards=new Map();
if("scrollRestoration" in history)history.scrollRestoration="manual";
function savePosition(){
  if(!current||pendingScroll!==null)return;
  try{sessionStorage.setItem("agent-reader-position",JSON.stringify({run:current,role:$("role").value,y:window.scrollY}));}catch{}
}
function restorePosition(){
  if(pendingScroll===null)return;
  if(document.documentElement.scrollHeight-window.innerHeight>=pendingScroll){
    const y=pendingScroll;pendingScroll=null;window.scrollTo({top:y,behavior:"auto"});
  }
}
function element(tag, text, cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function updateStats(){ $("calls").textContent=callCount;$("tokens").textContent=usageCount?tokenSum.toLocaleString():"—";$("tools").textContent=toolCount;$("trials").textContent=trials; }
function filter(){document.querySelectorAll("#feed article").forEach(e=>{e.hidden=!!$("role").value && e.dataset.role!==$("role").value;});}
function card(event, title, cls=""){
  $("empty")?.remove();const node=element("article",undefined,cls);node.dataset.role=event.role||event.gate||"";
  const meta=element("div",undefined,"meta");meta.append(element("span",names[node.dataset.role]||"工作流","speaker"),element("span",title,"tag"),element("time",new Date(event.ts_utc).toLocaleTimeString(),"time"));
  const content=element("div",undefined,"content");node.append(meta,content);$("feed").append(node);filter();return {node,content,title:meta.children[1]};
}
function message(event){
  const key=event.call_id;let c=key?cards.get(key):null;
  if(!c){c=card(event,event.purpose==="compression"?"上下文压缩":"生成中 · 尚未提交");c.raw="";c.preview=element("pre","","preview");c.content.append(c.preview);if(key)cards.set(key,c);}
  return c;
}
function finalMessage(event){
  const c=message(event);c.raw=event.text||c.raw;c.preview.textContent=c.raw;
  c.title.textContent=event.response_status==="completed"?"完整输出 · 待流程校验":"未完成 · 不执行";
  if(event.purpose==="compression")c.title.textContent="上下文压缩输出";
  if(event.response_status!=="completed")c.node.classList.add("bad");
  let value;try{value=JSON.parse(c.raw);}catch{value=null;}
  if(value){const summary=value.reason||value.summary||value.objective|| (value.tool?`请求工具：${value.tool}`:value.action?`决定：${value.action}`:"");
    if(summary){const p=element("p",String(summary),"prose");const detail=element("details");detail.append(element("summary","完整 JSON"));c.preview.textContent=JSON.stringify(value,null,2);detail.append(c.preview);c.content.replaceChildren(p,detail);}}
  callCount++;const u=event.usage||{};if(Number.isInteger(u.total_tokens)){tokenSum+=u.total_tokens;usageCount++;}
}
function toolOutput(event, content){
  const r=event.result||{};const parts=[];
  if(Number.isInteger(r.returned_items))parts.push(`返回 ${r.returned_items} 条`);
  if(Number.isInteger(r.total_rows))parts.push(`总计 ${r.total_rows} 条`);
  if(Number.isInteger(r.total_lines))parts.push(`总计 ${r.total_lines} 行`);
  if(r.next_offset!==undefined)parts.push(r.next_offset===null?"已到末页":`下一页 offset=${r.next_offset}`);
  if(r.observation_too_large)parts.push("完整结果已存档，当前返回文件索引");
  if(r.repeated_identical_observation)parts.push("同角色已取得相同请求与返回，本次没有新证据");
  if(r.view==="structure")parts.push("返回原文件字段索引，请按 pointer 读取内容");
  if(parts.length)content.append(element("p",parts.join(" · "),"prose"));
  if(r.text_preview)content.append(element("pre",r.text_preview+(r.text_preview_truncated?"\n…（预览，展开查看完整返回）":""),"preview"));
  if(!event.tool_record_id){content.append(element("p","此事件无法关联完整返回，请查看原始工具日志。","prose"));return;}
  const d=element("details"),body=element("pre","展开后加载原始工具返回。","preview");d.append(element("summary","完整工具返回"),body);content.append(d);
  const selectedRun=current;let loaded=false,loading=false;
  d.addEventListener("toggle",async()=>{
    if(!d.open||loaded||loading)return;loading=true;body.textContent="正在读取原始返回…";
    try{const response=await fetch(`/api/tool-result?run=${encodeURIComponent(selectedRun)}&record=${encodeURIComponent(event.tool_record_id)}`);
      if(!response.ok)throw Error(`HTTP ${response.status}`);const value=await response.json();body.textContent=JSON.stringify(value.result,null,2);loaded=true;
    }catch(error){body.textContent=`读取失败：${error.message}。收起后展开可重试。`;}finally{loading=false;}
  });
}
const trialStatuses={running:"计算中",completed:"计算完成",completed_converged:"计算完成 · DIS 已收敛",completed_iteration_limit:"计算完成 · DIS 达迭代上限",completed_no_disentanglement_required:"计算完成 · 无需解纠缠",execution_failed:"计算失败",input_constraint_failed:"输入约束未满足"};
function figureLink(url,text){const a=element("a",text);a.href=url;a.target="_blank";a.rel="noopener noreferrer";return a;}
function numberText(n){return Number.isFinite(n)?n.toPrecision(4):"—";}
function trialCard(row){
  const box=element("details",undefined,"trial-card");box.id=`result-${row.trial}`;box.open=true;
  const summary=element("summary",undefined,"trial-heading");summary.append(element("strong",`第 ${row.number} 轮 · ${row.trial}`),element("span",trialStatuses[row.status]||row.status,"trial-status"));box.append(summary);
  const body=element("div",undefined,"trial-body");box.append(body);
  if(row.plan_id)body.append(element("p",`方案：${row.plan_id}`,"gallery-hint"));
  const metrics=row.metrics||{}, errors=metrics.matched_error_metrics||{}, counts=metrics.counts||{};
  if(metrics.matched_error_metrics){const pop=row.metric_population==="baseline_target"?"固定基线目标":"本轮目标";body.append(element("p",`${pop} · MAE ${numberText(errors.mae_eV)} eV · 最大误差 ${numberText(errors.max_abs_eV)} eV · 匹配 ${counts.matched_states??"—"}/${counts.expected_states??"—"} · 缺失 ${counts.missing_states??"—"}`,"trial-metrics"));}
  const grid=element("div",undefined,"trial-images");body.append(grid);
  for(const item of row.images||[]){
    const figure=element("figure",undefined,"trial-image"), caption=element("figcaption");caption.append(element("strong",item.title),figureLink(item.url,"查看原图"));if(item.pdf_url)caption.append(figureLink(item.pdf_url,"PDF"));figure.append(caption);
    const link=figureLink(item.url,undefined),img=element("img");img.alt=`第 ${row.number} 轮 · ${item.title}`;img.width=item.width;img.height=item.height;img.loading="lazy";img.decoding="async";img.src=item.url;
    img.addEventListener("error",()=>{if(!figure.querySelector(".image-error"))figure.append(element("p","图片读取失败，可点击“查看原图”重试。","image-error"));});link.append(img);figure.append(link);grid.append(figure);
  }
  if(!row.images?.length){const labels={pending:"图片尚未生成，计算结束后自动显示。",disabled:"本轮未启用绘图。",failed:"本轮绘图未完成。",unavailable:"本轮暂无可显示的图片。"};body.append(element("p",labels[row.image_status]||"本轮暂无图片。","gallery-hint"));}
  else if(row.image_status==="partial")body.append(element("p","部分图片暂不可用。","gallery-hint"));
  return box;
}
function tryRestoreView(){if(replayReady&&trialLoaded&&pendingScroll!==null){restorePosition();pendingScroll=null;}}
async function loadTrials(){
  if(!current||trialLoading)return;const selectedRun=current,version=selectionVersion;trialLoading=true;
  try{
    const response=await fetch(`/api/trials?run=${encodeURIComponent(selectedRun)}`);if(!response.ok)throw Error();const rows=await response.json();if(version!==selectionVersion)return;
    const keep=new Set();for(const row of rows){keep.add(row.trial);const signature=JSON.stringify(row),old=trialCards.get(row.trial);if(old?.signature===signature)continue;const node=trialCard(row);if(old){node.open=old.node.open;old.node.replaceWith(node);}else $("trial-gallery").append(node);trialCards.set(row.trial,{signature,node});}
    for(const [id,entry] of trialCards){if(!keep.has(id)){entry.node.remove();trialCards.delete(id);}}
    $("plots-status").textContent=rows.length?`${rows.length} 轮计算 · 可收起各轮图片` :"尚未开始计算；每轮结果生成后自动显示。";
    trialLoaded=true;tryRestoreView();
  }catch{if(version===selectionVersion)$("plots-status").textContent="图片列表读取失败，正在重试…";}
  finally{if(version===selectionVersion)trialLoading=false;}
}
function onEvent(e){
  if(e.kind==="model_start"){message(e);$("status").textContent=`${names[e.role]||e.role}正在${e.purpose==="compression"?"压缩上下文":"生成回答"}…`;}
  else if(e.kind==="model_delta"){const c=message(e);c.raw+=e.text;c.preview.textContent=c.raw;}
  else if(e.kind==="model_interrupted"){const c=message(e);c.title.textContent="传输中断 · 不执行";c.node.classList.add("bad");}
  else if(names[e.kind]||e.kind==="context_summary")finalMessage(e);
  else {
    const labels={tool:"工具",handoff:"角色交接",role_decision:"决定已通过协议校验",scheduler_decision:"调度决定已通过校验",execution_start:"开始计算",execution_finished:"计算返回",run_finished:"流程结束",run_start:"流程启动",output_truncated:"输出截断",length_recovery_completed:"截断恢复成功",length_recovery_failed:"截断恢复失败",compression_applied:"上下文压缩已应用",compression_failed:"上下文压缩失败",protocol_rejected:"协议校验未通过",scheduler_rejected:"调度协议未通过",command_start:"程序启动",command_finished:"程序结束",command_interrupted:"程序中断",transport_attempt_failed:"请求失败"};
    const title=labels[e.kind]||e.kind;const c=card(e,title,"event");
    let text=e.reason||e.detail||e.stop||e.label||"";
    if(e.kind==="tool"){toolCount++;text=`${e.tool}${e.result?.tool_error?" · "+e.result.tool_error:" · 已返回"}`;}
    if(e.kind==="handoff")text=`${names[e.from]||e.from} → ${names[e.to]||e.to}`;
    if(e.decision)text=e.decision.reason||e.decision.action;
    c.content.append(element("p",String(text),"prose"));
    if(e.kind==="tool")toolOutput(e,c.content);
    const d=element("details");d.append(element("summary","事件详情"),element("pre",JSON.stringify(e,null,2),"preview"));c.content.append(d);
    if(e.kind==="execution_finished"&&e.trial){const a=element("a",`查看 ${e.trial} 的拟合图与残差图`,"trial-jump");a.href=`#result-${e.trial}`;a.addEventListener("click",()=>{const target=$("result-"+e.trial);if(target)target.open=true;});c.content.append(a);}
    if(e.kind==="execution_start"){trials++;$("status").textContent=`${e.trial} 计算中…`;}
    if(e.kind==="run_finished")$("status").textContent=`流程结束：${e.stop}${e.detail?" · "+e.detail:""}`;
  }
  if(["execution_start","execution_finished","run_finished"].includes(e.kind))loadTrials();
  updateStats();
}
function selectRun(restore=false){
  if(source)source.close();selectionVersion++;trialLoading=false;trialLoaded=false;replayReady=false;trialCards=new Map();$("trial-gallery").replaceChildren();$("plots-status").textContent="读取每轮图片…";current=$("runs").value;cards=new Map();callCount=tokenSum=usageCount=toolCount=trials=0;$("feed").replaceChildren();updateStats();
  pendingScroll=restore&&Number.isFinite(savedView.y)?Math.max(0,savedView.y):null;
  if(!current)return;loadTrials();$("status").textContent="读取工作流事件…";source=new EventSource(`/api/events?run=${encodeURIComponent(current)}`);
  source.onopen=()=>{$("connection").textContent="实时连接";};source.onmessage=e=>{try{onEvent(JSON.parse(e.data));}catch(err){$("connection").textContent="事件读取失败";}};
  let previousCursor=null;
  source.addEventListener("cursor",e=>{if(e.lastEventId===previousCursor){replayReady=true;tryRestoreView();}previousCursor=e.lastEventId;});
  source.onerror=()=>{$("connection").textContent="重连中";};
}
async function refresh(){try{const response=await fetch("/api/runs");if(!response.ok)throw Error();const runs=await response.json();
  $("runs").replaceChildren();if(!runs.length){$("runs").append(new Option("等待工作流启动",""));$("connection").textContent="等待工作流";return;}
  runs.forEach(r=>$("runs").append(new Option(`${r.id} · ${r.stop||"运行中"}`,r.id)));$("runs").value=runs.some(r=>r.id===current)?current:!current&&runs.some(r=>r.id===savedView.run)?savedView.run:runs[0].id;if($("runs").value!==current)selectRun(!current&&$("runs").value===savedView.run);
  loadTrials();
}catch{$("connection").textContent="服务连接失败";}}
if(savedView.role&&names[savedView.role])$("role").value=savedView.role;
$("runs").addEventListener("change",()=>{savePosition();selectRun();savePosition();});
$("role").addEventListener("change",()=>{pendingScroll=null;filter();savePosition();});
$("latest").addEventListener("click",()=>{pendingScroll=null;window.scrollTo({top:document.documentElement.scrollHeight,behavior:"auto"});savePosition();});
for(const event of ["wheel","touchstart","pointerdown","keydown"])window.addEventListener(event,()=>{pendingScroll=null;},{passive:true});
window.addEventListener("scroll",savePosition,{passive:true});window.addEventListener("pagehide",savePosition);
refresh();setInterval(refresh,3000);
