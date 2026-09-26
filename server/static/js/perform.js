/* Parent console: 🎭 Be Moxie and 🎬 Rehearsal.
 * Be Moxie: a remote grown-up drives the body with Moxie's own brain switched off. The
 * moods and the intensity ceiling come from the supervisor, so the card cannot offer a
 * mood the robot's enum lacks. It rides refreshLive()'s cadence; no timer of its own. */
let thDevice=null, thMoodsBuilt='';
async function refreshTelehealth(deviceId){
  const card=$('#telehealth-card'); if(!card) return;
  thDevice=deviceId;
  if(!deviceId){ card.classList.add('hidden'); return; }
  let t;
  try{ t=await api(`/local/robots/${encodeURIComponent(deviceId)}/telehealth`,{auth:false}); }
  catch(e){ card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  renderTelehealth(t);
}
function renderTelehealth(t){
  const box=$('#th-controls'), en=$('#th-enable'), st=$('#th-state'), warn=$('#th-warn');
  if(!box) return;
  if(!t.ok){
    if(st) st.innerHTML=`<span class="warn">${escapeHtml(t.reason||t.error||'unavailable')}</span>`;
    if(en){ en.checked=false; en.disabled=true; }
    box.classList.add('off');
    return;
  }
  if(en && document.activeElement!==en){ en.checked=!!t.enabled; }
  if(en) en.disabled=false;
  // Build the mood picker once, from the supervisor's own list.
  const sel=$('#th-mood'), sig=(t.moods||[]).map(m=>m.id).join(',');
  if(sel && sig && thMoodsBuilt!==sig){
    thMoodsBuilt=sig;
    const keep=sel.value;
    sel.innerHTML=(t.moods||[]).map(m=>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`).join('');
    if(keep) sel.value=keep;
  }
  const inten=$('#th-intensity');
  if(inten) [...inten.options].forEach(o=>{ o.disabled = +o.value > (t.max_intensity||2); });
  // The robot's OWN reported state. Never invented: with nothing received it says so.
  const when=t.state_at?new Date(t.state_at*1000).toLocaleTimeString():'';
  const state = t.reported
    ? `Robot reports <b>${escapeHtml(t.state)}</b>${t.state_known?'':' (a state we do not know)'}${when?` · ${escapeHtml(when)}`:''}`
    : 'Robot state: <b>never reported</b>';
  const sess = t.in_session ? `session ${escapeHtml(t.session_id)}` : 'no session';
  if(st) st.innerHTML = `${t.online?'●':'○'} ${t.online?'online':'offline'} · ${state} · ${sess}`;
  if(warn){
    warn.classList.toggle('hidden', !t.in_bedtime);
    if(t.in_bedtime) warn.innerHTML='🌙 This robot is inside its bedtime window. '
      + 'We do not know whether Moxie plays a line during bedtime — it is sent either way.';
  }
  box.classList.toggle('off', !t.enabled || !t.online);
  const sb=$('#btn-th-session');
  if(sb) sb.textContent = t.in_session ? 'End session' : 'Start session';
  const log=$('#th-transcript');
  if(log) log.innerHTML=(t.transcript||[]).map(l=>{
    const tm=l.at?new Date(l.at*1000).toLocaleTimeString():'';
    const who=l.who==='operator'?'You (as Moxie)':'Your child';
    return `<div class="thln ${l.who==='operator'?'op':'child'}"><span>${escapeHtml(tm)}</span>`
      + `<b>${escapeHtml(who)}:</b> ${escapeHtml(l.text)}</div>`;
  }).join('');
  if(log) log.scrollTop=log.scrollHeight;
}
async function thPost(body, saying){
  if(!thDevice) return null;
  const s=$('#th-status'); if(s) s.textContent=saying||'Sending…';
  let r;
  try{
    r=await api(`/local/robots/${encodeURIComponent(thDevice)}/telehealth`,
                {method:'POST',auth:false,body});
  }catch(e){
    // The proxy keeps the supervisor's 400 (a safety block, or the mode being off) and
    // its reason travels in the body — show it, never swallow it.
    let reason=e.message||'failed';
    try{ const j=JSON.parse(reason); reason=j.reason||j.error||reason; }catch(_){}
    if(s) s.innerHTML=`⚠️ ${escapeHtml(reason)}`;
    refreshTelehealth(thDevice);
    return null;
  }
  if(s) s.textContent = r.ok ? (r.spoke ? `✅ Moxie said “${r.spoke}”`
                                        : '✅ Done.') : `⚠️ ${r.reason||r.error||'failed'}`;
  if(r.ok && r.flagged && r.flagged.length && s)
    s.textContent += ' (recorded in the Safety card)';
  renderTelehealth(r);
  return r;
}
{ const t=$('#th-enable'); if(t) t.onchange=async()=>{
    const r=await thPost({action:t.checked?'enable':'disable'},
                         t.checked?'Turning Be Moxie on…':'Turning Be Moxie off…');
    // Turning it on opens a session straight away — that is what a person means by
    // "let me talk through Moxie now" (backlog/telehealth.md §2.6).
    if(r && r.ok && t.checked && !r.in_session) await thPost({action:'start'}, 'Starting…');
    refreshLive();
  }; }
{ const b=$('#btn-th-session'); if(b) b.onclick=async()=>{
    const ending=b.textContent.indexOf('End')===0;
    await thPost({action: ending?'end':'start'}, ending?'Ending…':'Starting…');
  }; }
{ const b=$('#btn-th-interrupt'); if(b) b.onclick=()=>thPost({action:'interrupt'},'Interrupting…'); }
async function thSpeak(){
  const box=$('#th-text'); if(!box) return;
  const text=(box.value||'').trim(); if(!text) return;
  const r=await thPost({action:'speak', text,
                        mood:($('#th-mood')||{}).value||undefined,
                        intensity:+(($('#th-intensity')||{}).value||1)}, 'Speaking…');
  if(r && r.ok) box.value='';       // a refused line stays in the box to be rephrased
}
{ const b=$('#btn-th-speak'); if(b) b.onclick=thSpeak; }
{ const t=$('#th-text'); if(t) t.onkeydown=e=>{ if(e.key==='Enter'){ e.preventDefault(); thSpeak(); } }; }

// ---- 🎬 Rehearsal ----
// Staged by the behavior planner and published as an ORDINARY remote_chat — no brain, no
// history. It lists the staged beats exactly as published and shows every id the
// validator refused in red rather than dropping it silently. It does not poll.
let pvDevice=null;
function refreshPreview(deviceId){
  const card=$('#preview-card'); if(!card) return;
  pvDevice=deviceId;
  card.classList.toggle('hidden', !deviceId);
}
const PV_SLOTS=[['gesture','arm'],['tree','body'],['gaze','look'],['icon','screen'],
                ['sfx','sound'],['usel','voice'],['spurt','spurt']];
function renderPreview(r){
  const box=$('#pv-beats'), st=$('#pv-status');
  if(!box) return;
  if(!r || !r.ok){
    box.innerHTML='';
    if(st) st.innerHTML=`<span class="warn">${escapeHtml((r&&(r.reason||r.error))||'unavailable')}</span>`;
    return;
  }
  const p=r.performance||{}, beats=p.beats||[];
  const head=[`act <b>${escapeHtml(p.dialog_act||'—')}</b>`,
              `mood <b>${escapeHtml(String(p.mood??'—'))}</b>`,
              `feels <b>${escapeHtml(p.emotion||'—')}</b>`,
              `signal <b>${escapeHtml(p.signal||'—')}</b>`,
              `${beats.length} beat${beats.length===1?'':'s'}`].join(' · ');
  const rows=beats.map(b=>{
    const tags=PV_SLOTS.filter(([k])=>b[k]).map(([k,label])=>
      `${label}:${escapeHtml(String(b[k]))}`);
    if(b.mood!=null) tags.unshift(`face:${escapeHtml(String(b.mood))}`);
    if(b.break_after) tags.push(`pause:${escapeHtml(String(b.break_after))}s`);
    return `<div class="pv-beat"><span class="pv-words">${escapeHtml(b.text||'·')}</span>`
         + `<span class="pv-tags">${tags.join(' ')||'—'}</span></div>`;
  }).join('');
  const dropped=(r.dropped||[]).length
    ? `<div class="pv-dropped">Not played (Moxie has no such move): `
      + `${escapeHtml((r.dropped||[]).join(', '))}</div>` : '';
  box.innerHTML=`<div class="pv-head">${head}</div>${rows}${dropped}`;
  if(st) st.textContent = r.spoke ? 'Rehearsed and spoken.' : 'Rehearsed — watch the robot.';
}
async function pvRun(){
  const boxEl=$('#pv-text'); if(!boxEl || !pvDevice) return;
  const text=(boxEl.value||'').trim(); if(!text) return;
  const st=$('#pv-status'); if(st) st.textContent='Rehearsing…';
  let r;
  try{
    r=await api(`/local/robots/${encodeURIComponent(pvDevice)}/preview`,
                {method:'POST', auth:false,
                 body:{text, speak:!!($('#pv-speak')||{}).checked}});
  }catch(e){
    // The proxy keeps the supervisor's 400/404 and its reason travels in the body — a
    // safety block, an empty line, a robot still pending. Show it, never swallow it.
    let reason=(e && e.message) || 'failed';
    try{ const j=JSON.parse(reason); reason=j.reason||j.error||reason; }catch(_){}
    r={ok:false, reason};
  }
  renderPreview(r);
}
{ const b=$('#btn-pv-run'); if(b) b.onclick=pvRun; }
{ const t=$('#pv-text'); if(t) t.onkeydown=e=>{ if(e.key==='Enter'){ e.preventDefault(); pvRun(); } }; }
