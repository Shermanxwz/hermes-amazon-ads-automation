const renderClosedLoopBase=render;
function ensureApprovalTab(){
  if(!document.querySelector('[data-tab="approvals"]')){
    const button=document.createElement("button");button.dataset.tab="approvals";button.textContent="审批";
    $("#tabs")?.appendChild(button);
    button.onclick=()=>{$$('#tabs button,.tab').forEach(x=>x.classList.remove('active'));button.classList.add('active');$('#approvals')?.classList.add('active')};
  }
  if(!$("#approvals")){
    const section=document.createElement("section");section.id="approvals";section.className="tab";
    section.innerHTML='<p class="tab-help">高风险结构操作只有在您批准精确 Payload Hash 后才会由 Executor 执行。普通聊天回复不会授权。</p><article class="card"><h2>待审批与历史计划</h2><p class="section-help">请逐项核对 Profile、完整参数、预期状态、依赖、预算暴露、Hash 和过期时间。批准后每个决策只可消费一次，任何参数变化都会自动失效。</p><div id="approval-list"></div></article>';
    $("#app")?.appendChild(section);
  }
}
function prettyJson(value){try{return esc(JSON.stringify(value??{},null,2))}catch(_){return esc(String(value??""))}}
function approvalActionDetails(action,index){
  const dependencies=(action.depends_on||[]).map(esc).join(", ")||"无";
  return `<details class="approval-action"><summary>${index+1}. ${esc(action.action_type)} · ${esc(action.entity_type)} · ${esc(action.entity_id)} · ${esc(action.tool_name)}</summary><p>Plan Key：<span class="mono">${esc(action.plan_key||"")}</span><br>依赖：<span class="mono">${dependencies}</span><br>单项预算暴露：${fmt(action.maximum_daily_budget)}</p><h4>批准参数</h4><pre>${prettyJson(action.arguments)}</pre><h4>独立验证预期</h4><pre>${prettyJson(action.expected_state)}</pre></details>`;
}
function approvalRows(items){return (items||[]).map(x=>{
  const plan=x.plan||{}, actions=plan.actions||[], hash=String(x.payload_hash||"");
  const tools=(x.tool_names||[]).join(", ");
  const details=actions.map(approvalActionDetails).join("");
  let controls="";
  if(x.status==="pending")controls=`<button class="approve-plan" data-id="${esc(x.id)}" data-hash="${esc(hash)}">批准</button> <button class="reject-plan ghost" data-id="${esc(x.id)}">拒绝</button>`;
  return `<tr><td>${badge(x.status,statusKind(x.status))}<br><span class="mono">${esc(x.id)}</span></td><td><strong>${esc(x.summary)}</strong><br>Profile <span class="mono">${esc(x.profile_id)}</span><br>${details||"—"}</td><td>${fmt(actions.length)} 个原子动作<br>${tools?`<span class="mono">${esc(tools)}</span>`:"—"}<br>计划日预算暴露上限 ${fmt(x.maximum_daily_budget)}</td><td><span class="mono">${esc(hash)}</span><br>请求 ${esc(x.requested_at)}<br>到期 ${esc(x.expires_at)}</td><td>${controls}</td></tr>`;
})}
function bindApprovalActions(){
  $$('.approve-plan').forEach(button=>button.onclick=()=>mutate(button,async()=>{
    const id=button.dataset.id, hash=button.dataset.hash, phrase=`APPROVE ${id} ${hash.slice(0,12)}`;
    const typed=window.prompt(`确认您已核对全部工具、参数、依赖、预期状态和预算暴露。请输入：\n${phrase}`)||"";
    if(typed!==phrase)throw new Error("确认文本不匹配，未批准");
    await api(`/api/approvals/${encodeURIComponent(id)}/approve`,{method:"POST",body:JSON.stringify({payload_hash:hash,confirmation:typed})});
    await refresh();
  },"精确计划已批准").catch(()=>{}));
  $$('.reject-plan').forEach(button=>button.onclick=()=>mutate(button,async()=>{
    const reason=window.prompt("请输入拒绝原因")||"operator rejected";
    await api(`/api/approvals/${encodeURIComponent(button.dataset.id)}/reject`,{method:"POST",body:JSON.stringify({reason})});
    await refresh();
  },"计划已拒绝").catch(()=>{}));
}
render=function(){
  renderClosedLoopBase();ensureApprovalTab();
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

  const approvals=d.approvals||{}, pendingApprovals=Number(approvals.pending||0);
  let approvalPill=$("#approval-pill");
  if(!approvalPill){approvalPill=document.createElement("span");approvalPill.id="approval-pill";document.querySelector(".status-strip")?.appendChild(approvalPill)}
  approvalPill.className=pendingApprovals?"warn":"good";approvalPill.textContent=`审批 ${pendingApprovals} 待处理 / ${Number(approvals.in_flight||0)} 执行中`;
  const approvalNode=$("#approval-list");
  if(approvalNode){approvalNode.innerHTML=table(["状态 / ID","完整计划","范围 / 暴露","Payload Hash / 时效","操作"],approvalRows(approvals.recent||[]));bindApprovalActions()}

  const storage=d.storage||{}, files=storage.files||{}, filesystem=storage.filesystem||{};
  const maintenance=storage.latest_maintenance||{}, pressure=maintenance.pressure||"normal";
  let storagePill=$("#storage-pill");
  if(!storagePill){storagePill=document.createElement("span");storagePill.id="storage-pill";document.querySelector(".status-strip")?.appendChild(storagePill)}
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
