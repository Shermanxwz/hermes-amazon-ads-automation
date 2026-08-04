const renderClosedLoopBase=render;
render=function(){
  renderClosedLoopBase();
  const d=dashboard||{}, reports=d.reports||{}, counts=reports.counts||{};
  const pendingReports=["REQUESTED","SUBMITTED","IN_PROGRESS","SUCCEEDED","DOWNLOADED","VALIDATED"].reduce((sum,key)=>sum+Number(counts[key]||0),0);
  const failedReports=Number(counts.FAILED||0)+Number(counts.QUARANTINED||0);
  const reportPill=$("#report-pill");
  if(reportPill){reportPill.className=failedReports?"bad":pendingReports?"warn":"good";reportPill.textContent=`报告 ${pendingReports} 处理中 / ${failedReports} 异常`}
  const callbackPill=$("#callback-pill");
  if(callbackPill){callbackPill.className=d.pending_callbacks?"bad":"good";callbackPill.textContent=`${Number(d.pending_callbacks||0)} 个待确认回调`}
  const runtime=(d.runtime_status||[]).find(x=>x.component==="hermes-plugin")||d.runtime_status?.[0];
  const resources=runtime?.state?.resources||{};
  const resourcePill=$("#resource-pill");
  if(resourcePill){resourcePill.className=resources.defer_nonurgent_collection?"warn":"good";resourcePill.textContent=`资源 ${esc(resources.tier||"未知")} / ${fmt(resources.cpu_count)}C ${fmt(resources.memory_total_mb)}MB`}
  const reportsNode=$("#reports");
  if(reportsNode)reportsNode.innerHTML=table(["更新时间","Profile / 类型","窗口","状态","尝试","证据"],(reports.recent||[]).map(x=>`<tr><td>${esc(x.updated_at)}</td><td><span class="mono">${esc(x.profile_id)}</span><br>${esc(x.report_type)}</td><td>${esc(x.start_date)} → ${esc(x.end_date)}</td><td>${badge(x.status,statusKind(x.status))}</td><td>${fmt(x.attempt_count)}</td><td class="mono">${esc((x.normalized_hash||"").slice(0,12))}</td></tr>`));
  const runtimeNode=$("#runtime-status");
  if(runtimeNode)runtimeNode.innerHTML=table(["组件","更新时间","资源 / 队列","状态"],(d.runtime_status||[]).map(x=>{const r=x.state?.resources||{};return `<tr><td>${esc(x.component)}</td><td>${esc(x.updated_at)}</td><td>${esc(r.tier||"")} ${fmt(r.cpu_count)}C / ${fmt(r.memory_total_mb)}MB<br>Outbox ${fmt(x.state?.result_outbox_pending)}</td><td>${x.state?.result_outbox_pending?badge("待补投","warn"):badge("正常","good")}</td></tr>`}));
};
if(dashboard)render();
