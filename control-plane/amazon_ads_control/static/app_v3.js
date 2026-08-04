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

  const storage=d.storage||{}, files=storage.files||{}, filesystem=storage.filesystem||{};
  const maintenance=storage.latest_maintenance||{}, pressure=maintenance.pressure||"normal";
  let storagePill=$("#storage-pill");
  if(!storagePill){
    storagePill=document.createElement("span");storagePill.id="storage-pill";
    document.querySelector(".status-strip")?.appendChild(storagePill);
  }
  storagePill.className=pressure==="hard"?"bad":pressure==="soft"?"warn":"good";
  storagePill.textContent=`存储 ${fmt(files.total_mb)}MB / 空闲 ${fmt(filesystem.free_mb)}MB`;

  const reportsNode=$("#reports");
  if(reportsNode)reportsNode.innerHTML=table(["更新时间","Profile / 类型","窗口","状态","尝试","证据"],(reports.recent||[]).map(x=>`<tr><td>${esc(x.updated_at)}</td><td><span class="mono">${esc(x.profile_id)}</span><br>${esc(x.report_type)}</td><td>${esc(x.start_date)} → ${esc(x.end_date)}</td><td>${badge(x.status,statusKind(x.status))}</td><td>${fmt(x.attempt_count)}</td><td class="mono">${esc((x.normalized_hash||"").slice(0,12))}</td></tr>`));

  const runtimeRows=(d.runtime_status||[]).map(x=>{
    const r=x.state?.resources||{}, box=x.state?.result_outbox||{};
    const pending=Number(box.pending??x.state?.result_outbox_pending??0);
    const boxState=box.over_limit?badge("Outbox 已达上限","bad"):pending?badge("待补投","warn"):badge("正常","good");
    return `<tr><td>${esc(x.component)}</td><td>${esc(x.updated_at)}</td><td>${esc(r.tier||"")} ${fmt(r.cpu_count)}C / ${fmt(r.memory_total_mb)}MB<br>Outbox ${fmt(pending)} / ${fmt(box.bytes)}B</td><td>${boxState}</td></tr>`;
  });
  runtimeRows.push(`<tr><td>control-storage</td><td>${esc(storage.generated_at||"")}</td><td>DB ${fmt(files.database_mb)}MB / WAL ${fmt(files.wal_mb)}MB<br>可回收 ${fmt(storage.sqlite?.reclaimable_mb)}MB</td><td>${pressure==="hard"?badge("硬阈值暂停","bad"):pressure==="soft"?badge("压力清理","warn"):badge("正常","good")}</td></tr>`);
  const runtimeNode=$("#runtime-status");
  if(runtimeNode)runtimeNode.innerHTML=table(["组件","更新时间","资源 / 队列","状态"],runtimeRows);

  let rollupNode=$("#alert-rollups");
  if(!rollupNode&&$("#alerts")){
    const article=document.createElement("article");article.className="card";
    article.innerHTML='<h2>历史异常汇总</h2><p class="section-help">对应任务已经归档的陈旧未解决异常会按月、代码和 Profile 汇总，保留数量、时间范围和哈希，而不是无限保存逐条大记录。</p><div id="alert-rollups"></div>';
    $("#alerts").appendChild(article);rollupNode=$("#alert-rollups");
  }
  if(rollupNode)rollupNode.innerHTML=table(["月份","级别 / 代码","Profile","数量","时间范围 / 样例"],(d.alert_rollups||[]).map(x=>`<tr><td>${esc(x.bucket_start)}</td><td>${badge(x.severity,statusKind(x.severity))}<br><span class="mono">${esc(x.code)}</span></td><td class="mono">${esc(x.profile_id||"全局")}</td><td>${fmt(x.alert_count)}</td><td>${esc(x.first_at)} → ${esc(x.last_at)}<br>${esc(x.sample_message||"")}</td></tr>`));
};
if(dashboard)render();
