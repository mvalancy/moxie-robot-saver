/* Parent console: 📈 insights (telemetry + the 🔌 broker connection) and the 🛡️ safety
 * review queue, plus armErase — the two-click destructive button every erase uses. */
// A destructive button that asks twice: the first click arms it (and disarms any other),
// the second one runs. Cheaper than a modal and impossible to hit by accident.
function armErase(btn, armedLabel, run){
  const original=btn.textContent;
  btn.onclick=()=>{
    if(btn.dataset.armed==='1'){ btn.dataset.armed=''; btn.textContent=original;
                                 btn.classList.remove('mem-arm'); run(); return; }
    document.querySelectorAll('#memory-card button[data-armed="1"]').forEach(o=>{
      o.dataset.armed=''; o.classList.remove('mem-arm');
      if(o.dataset.label) o.textContent=o.dataset.label;
    });
    btn.dataset.armed='1'; btn.dataset.label=original;
    btn.textContent=armedLabel; btn.classList.add('mem-arm');
    setTimeout(()=>{ if(btn.dataset.armed==='1'){ btn.dataset.armed='';
      btn.textContent=original; btn.classList.remove('mem-arm'); } }, 6000);
  };
}

// 📈 Insights: a week of zero-filled daily roll-ups over the newest packets. It never
// implies data the store lacks: under NO_DATA (`persisted:false`) it says nothing is kept,
// and the footer states the real retention window.
function weekBars(history){
  if(!(history||[]).length) return '';
  const bars=history.map(d=>{
    const label=(d.day||'').slice(5).replace('-','/');
    const h=Math.round(4+(d.share||0)*56);   // 4–60px: a zero day still shows its slot
    return `<div class="tday${d.count?'':' zero'}" title="${escapeHtml(d.day||'')}: `
      +`${d.count} event${d.count===1?'':'s'}${d.top_event?' · mostly '+escapeHtml(d.top_event):''}">
        <span class="tnum">${d.count||''}</span>
        <div class="tbar" style="height:${d.count?h:2}px"></div>
        <span class="tlabel">${escapeHtml(label)}</span>
      </div>`;
  }).join('');
  return `<div class="tweek">${bars}</div>`;
}
// 🔌 The appliance's own broker connection — one socket, so not per-robot. Rendered in
// every branch of the card, including "no robot connected": that is where it tells a
// parent whether the robot is off or the appliance lost its broker. "recovered" is
// deliberately not "healthy".
function connGaps(c){
  if(!c.gaps || !c.gaps.count) return '';
  const s=n=>n>=60?`${Math.round(n/60)}m`:`${n.toFixed(1)}s`;
  return ` · ${c.gaps.count} outage${c.gaps.count===1?'':'s'} totalling ${s(c.gaps.total_s)}`
    +` (longest ${s(c.gaps.max_s)})`;
}
function connectionStrip(c){
  if(!c || !c.ok){
    return '<div class="connstrip down">🔌 <b>Supervisor not reachable</b>'
      +' — this card cannot say anything about the connection.</div>';
  }
  const dot = c.state==='down' ? 'down' : (c.state==='recovered' ? 'warn' : 'ok');
  const bits=[];
  if(c.outages) bits.push(`${c.outages} outage${c.outages===1?'':'s'}`);
  if(c.refusals) bits.push(`${c.refusals} refused connection${c.refusals===1?'':'s'}`);
  if(c.drops) bits.push(`${c.drops} dropped message${c.drops===1?'':'s'}`);
  if(c.lock_timeouts) bits.push(`${c.lock_timeouts} refused save${c.lock_timeouts===1?'':'s'}`);
  // Every row carries its own timestamp, so a parent reads a sequence rather than a
  // count — which is the entire difference between this and the six scalars it replaces.
  const rows=(c.events||[]).map(e=>{
    const when=e.at?new Date(e.at*1000).toLocaleString():'—';
    const extra=[];
    if(e.gap_s!=null) extra.push(`after ${e.gap_s.toFixed(1)}s down`);
    if(e.waited_s!=null) extra.push(`waited ${e.waited_s.toFixed(1)}s`);
    if(e.device_id) extra.push(escapeHtml(e.device_id));
    if(e.reason) extra.push(escapeHtml(e.reason));
    return `<div class="ev"><span>${escapeHtml(when)}</span> <b>${escapeHtml(e.label)}</b>`
      +(extra.length?` <span>${extra.join(' · ')}</span>`:'')+'</div>';
  }).join('');
  const known=c.roster&&c.roster.known
    ? ` · ${c.roster.known} robot${c.roster.known===1?'':'s'} known to this box` : '';
  const note=c.count
    ? `<p class="tnote">Newest ${c.count} of the last ${c.retention.events} connection `
      +`events kept on this box${known}.</p>`
    : `<p class="tnote">Nothing recorded yet${known}.</p>`;
  return `<div class="connstrip ${dot}">🔌 <b>${escapeHtml(c.verdict||'')}</b>`
    +`${bits.length?' — '+bits.join(', '):''}${connGaps(c)}`
    +`${c.last_error?` <span>${escapeHtml(c.last_error)}</span>`:''}</div>`
    +(rows?`<div class="evlog conn">${rows}</div>`:'')+note;
}
async function refreshInsights(deviceId){
  const box=$('#robot-insights'); if(!box) return;
  // Fetched first and rendered in every branch — see the note above `connectionStrip`.
  let conn=null;
  try{ conn=await api('/local/connection',{auth:false}); }catch(e){ conn=null; }
  const strip=connectionStrip(conn);
  // Wiring the 🧽 button inside `render` rather than after each call: this function
  // returns early from four branches, and an erase button that works in three of them is
  // worse than none — a parent would learn it sometimes does nothing.
  const render=html=>{
    box.innerHTML=strip+html;
    const b=box.querySelector('#btn-telemetry-forget');
    if(b) armErase(b, 'Click again to erase', ()=>eraseTelemetry(deviceId));
  };
  if(!deviceId){ render('<div class="live-off">📈 Insights: no robot connected</div>'); return; }
  let t;
  try{ t=await api(`/local/robots/${encodeURIComponent(deviceId)}/telemetry`,{auth:false}); }
  catch(e){ render('<div class="live-off">📈 Insights: supervisor offline</div>'); return; }
  if(!t.ok){
    render(`<div class="live-off">📈 Insights: ${escapeHtml(t.error||'unavailable')}</div>`);
    return;
  }
  const tot=t.totals||{}, ret=t.retention||{};
  // 🧽 Only when there is something to erase — a button that always answers "nothing was
  // stored" teaches a parent to distrust it.
  const forget=(t.count||tot.total>0)
    ? '<span class="grow"></span><button id="btn-telemetry-forget" class="ghost tiny">'
      +'Erase history</button>' : '';
  const hd=`<div class="insights-hd">📈 Insights · ${t.count} event${t.count===1?'':'s'} kept`
    +`${tot.total>t.count?` · ${tot.total} all time`:''}${forget}</div>`;
  if(t.persisted===false){
    render(hd+'<div class="live-off">Data sharing is '
      +`${escapeHtml(t.policy||'NO_DATA')}, so nothing is being saved — this card can only `
      +'show what has arrived since the supervisor started, and a restart clears it.</div>'
      +(t.count?`<div class="evlog">${(t.events||[]).map(e=>{
          const when=e.recorded_at?new Date(e.recorded_at*1000).toLocaleString():'—';
          return `<div class="ev"><span>${escapeHtml(when)}</span> <b>${escapeHtml(e.event_name)}</b></div>`;
        }).join('')}</div>`:''));
    return;
  }
  if(!t.count && !(tot.total>0)){
    render(hd+'<div class="live-off">No events yet — Moxie hasn\'t reported any '
      +'activity. Once it does, this card keeps the history across restarts.</div>');
    return;
  }
  const week=weekBars(t.history||[]);
  const counts=(t.by_event||[]).map(c=>
    `<div class="k"><span>${escapeHtml(c.event)}</span><b>${c.count}</b></div>`).join('');
  const rows=(t.events||[]).map(e=>{
    const when=e.recorded_at?new Date(e.recorded_at*1000).toLocaleString():'—';
    return `<div class="ev"><span>${escapeHtml(when)}</span> <b>${escapeHtml(e.event_name)}</b></div>`;
  }).join('');
  const since=tot.first_day?`History since ${escapeHtml(tot.first_day)}`:'History starts today';
  const note=`${since}. Kept on this box: the newest ${ret.packets||0} events and `
    +`${ret.days||0} days of daily counts${tot.dropped_days?` (${tot.dropped_days} older `
    +'day'+(tot.dropped_days===1?'':'s')+' have aged out)':''}. `
    +`Data sharing is ${escapeHtml(t.policy||'NO_MEDIA')}`
    +`${t.policy==='NO_MEDIA'?', so event payloads are never written — only what happened and when':''}.`;
  render(`${hd}${week}<div class="livegrid">${counts}</div>`
    +`<div class="evlog">${rows}</div><p class="tnote">${note}</p>`);
}
// 🧽 Erase the stored activity history (packets, daily roll-up, finished-activity log).
// Two clicks via armErase, like the memory erases, because it cannot be undone.
async function eraseTelemetry(deviceId){
  let msg='';
  try{
    const r=await api('/local/robots/'+encodeURIComponent(deviceId)+'/telemetry',
                      {method:'DELETE',auth:false});
    msg = r.erased ? '🧽 Erased the stored activity history — the events, the daily '
                     +'counts and the finished-activity log are gone from this box.'
                   : 'Nothing was stored to erase.';
  }catch(e){ msg='⚠️ '+(e.message||'erase failed'); }
  await refreshInsights(deviceId);
  const box=$('#robot-insights');
  if(box) box.insertAdjacentHTML('beforeend',
                                 '<p class="tnote">'+escapeHtml(msg)+'</p>');
}

// safety review queue (ai-seam §2 InputSafety): what the classifier blocked or flagged,
// on either side of a turn. Excerpts arrive already redacted by the runtime.
async function refreshSafety(deviceId){
  const box=$('#robot-safety'); if(!box) return;
  if(!deviceId){ box.innerHTML='<div class="live-off">🛡️ Safety: no robot connected</div>'; return; }
  let s;
  try{ s=await api(`/local/robots/${encodeURIComponent(deviceId)}/safety`,{auth:false}); }
  catch(e){ box.innerHTML='<div class="live-off">🛡️ Safety: supervisor offline</div>'; return; }
  if(!s.ok){
    box.innerHTML=`<div class="live-off">🛡️ Safety: ${escapeHtml(s.error||'unavailable')}</div>`;
    return;
  }
  const unrev = s.unreviewed
    ? `<span class="warn">${s.unreviewed} to review</span>` : '<span>all reviewed</span>';
  const ack = s.unreviewed
    ? '<button id="btn-safety-ack" class="ghost tiny">Mark all reviewed</button>' : '';
  const hd=`<div class="safety-hd">🛡️ Safety · ${s.total} event${s.total===1?'':'s'}
              <span class="grow">${unrev}</span>${ack}</div>`;
  if(!s.enabled){
    box.innerHTML=hd+'<div class="live-off">Safety checking is OFF (MOXIE_SAFETY=0).</div>';
    return;
  }
  if(!s.total){
    box.innerHTML=hd+'<div class="live-off">Nothing flagged yet. Moxie checks every turn, '
      +'both what your child says and what Moxie is about to say.</div>';
    return;
  }
  const counts=(s.by_category||[]).map(c=>
    `<div class="k"><span>${escapeHtml(c.label)}</span><b>${c.count}</b></div>`).join('');
  const rows=(s.events||[]).map(e=>{
    const when=e.ts?new Date(e.ts*1000).toLocaleString():'—';
    const who=e.side==='moxie'?'Moxie':'child';
    const what=(e.labels||[]).join(', ')||'flagged';
    const ex=e.excerpt?`<span class="ex">“${escapeHtml(e.excerpt)}”</span>`:'';
    const seen=e.reviewed?'<span class="tag">reviewed</span>':'';
    return `<div class="ev${e.action==='block'?' blocked':''}">
              <span>${escapeHtml(when)}</span>
              <span class="tag">${escapeHtml(e.action)} · ${escapeHtml(who)}</span>
              <b>${escapeHtml(what)}</b> ${ex} ${seen}</div>`;
  }).join('');
  const note = s.detail
    ? 'Blocked turns never reached the AI (or were never spoken). Excerpts are redacted.'
    : `Data sharing is ${escapeHtml(s.policy||'NO_DATA')}, so only counts are kept — no excerpts, no event list.`;
  box.innerHTML=`${hd}<div class="livegrid">${counts}</div><div class="evlog">${rows}</div>`
    +`<p class="safety-note">${note}</p>`;
  const b=$('#btn-safety-ack');
  if(b) b.onclick=async()=>{
    b.disabled=true;
    try{ await api(`/local/robots/${encodeURIComponent(deviceId)}/safety`,
                   {method:'POST',auth:false,body:{}}); }catch(e){}
    refreshSafety(deviceId);
  };
}
