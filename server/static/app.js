const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
let TOKEN = localStorage.getItem('moxie_token') || null;
let LAST = {};
let poll = null;

async function api(path, {method='GET', body, auth=true}={}){
  const h={'Content-Type':'application/json'};
  if(auth && TOKEN) h['Authorization']='Bearer '+TOKEN;
  const r=await fetch(path,{method,headers:h,body:body?JSON.stringify(body):undefined});
  if(!r.ok) throw new Error((await r.text())||r.status);
  const ct=r.headers.get('content-type')||''; return ct.includes('json')?r.json():r.text();
}

// ---- tabs ----
let monTimer=null;
function activateTab(name){
  $$('.tab').forEach(t=>t.classList.toggle('active', t.dataset.tab===name));
  $$('.tabpanel').forEach(p=>p.classList.toggle('active', p.id==='tab-'+name));
  clearInterval(monTimer);
  if(name==='direct'){ loadDirect(); pollMonitor(); monTimer=setInterval(pollMonitor,2500); }
  if(name==='server'){ loadEndpointQR(); pollMonitor(); monTimer=setInterval(pollMonitor,2500); }
  if(name==='moxie') refreshMoxie();
}

// ---- Moxie Direct ----
async function loadDirect(){
  let d; try{ d=await api('/local/direct/info',{auth:false}); }catch(e){ return; }
  if(!d.ready){ $('#direct-notready').classList.remove('hidden'); return; }
  $('#direct-notready').classList.add('hidden');
  $('#direct-ssid').textContent=d.ssid;
  $('#direct-wifi-img').src='/local/direct/wifi_qr.png';
  $('#direct-wifi-info').innerHTML=`Moxie joins <b>${d.ssid}</b> (password is baked into the code — nothing to type).`;
  $('#direct-ep-img').src=`/local/endpoint/qr.png?host=${encodeURIComponent(d.host)}`;
  $('#direct-ep-info').textContent=`Points Moxie at ${d.host}:8883 (this computer).`;
}
$$('.tab').forEach(t=>t.onclick=()=>activateTab(t.dataset.tab));
document.addEventListener('click',e=>{
  const g=e.target.closest('[data-goto]'); if(g){ e.preventDefault(); activateTab(g.dataset.goto); }
});

// ---- login ----
$('#btn-login').onclick = async () => {
  const email = $('#email').value.trim() || 'parent@home.lan';
  const res = await api('/local/quicklogin',{method:'POST',auth:false,body:{email,first_name:'Parent'}});
  TOKEN=res.token; localStorage.setItem('moxie_token',TOKEN);
  $('#who').textContent = res.email;
  enterApp();
};

function enterApp(){
  $('#s-login').classList.add('hidden');
  $('#tabs').classList.remove('hidden');
  activateTab('direct');   // load the default tab's content (QRs + monitor) on entry
}

// ---- Wi-Fi pairing ----
$('#btn-qr').onclick = async () => {
  const name=$('#child-name').value.trim();
  if(name){ await api('/api/children',{method:'POST',body:{child:{'child-first-name':name}}}); }
  const body={ ssid:$('#ssid').value.trim(), password:$('#wifipass').value,
               band:$('#band').value, hidden:$('#hidden').checked };
  if(!body.ssid){ alert('Enter your Wi-Fi network name'); return; }
  LAST = await api('/local/pairing/prepare',{method:'POST',body});
  $('#qr-img').src = '/local/pairing/qr.png?payload='+encodeURIComponent(LAST.qr_payload);
  $('#phrase').textContent = LAST.recovery_phrase;
  $('#wifi-qr-card').classList.remove('hidden');
  $('#pair-status').classList.remove('ok');
  $('#pair-status').textContent = 'Waiting for Moxie to join Wi-Fi…';
  loadEndpointQR();
  $('#wifi-qr-card').scrollIntoView({behavior:'smooth'});
  startPolling();
};

function startPolling(){
  clearInterval(poll);
  poll=setInterval(async()=>{
    const st=await api('/local/state');
    if(st.robots && st.robots.length){
      clearInterval(poll);
      $('#pair-status').classList.add('ok');
      $('#pair-status').textContent='Moxie connected! See the 🤖 Moxie tab.';
      refreshMoxie();
    }
  },2000);
}

// ---- Server pairing (endpoint QR) ----
let brokerHost=null;
async function loadEndpointQR(){
  try{
    // default to the server's detected LAN IP (NOT the page host, which may be Tailscale)
    if(brokerHost===null){
      const def = await api('/local/endpoint/payload',{auth:false});
      brokerHost = $('#broker-host').value.trim() || def.default_host || def.mqtt_host;
      $('#broker-host').value = brokerHost;
    } else {
      brokerHost = $('#broker-host').value.trim() || brokerHost;
    }
    const q = `?host=${encodeURIComponent(brokerHost)}`;
    $('#endpoint-img').src = `/local/endpoint/qr.png${q}`;
    const info = await api(`/local/endpoint/payload${q}`,{auth:false});
    $('#endpoint-info').textContent = `Points Moxie at ${info.mqtt_host}:${info.mqtt_port} — make sure your Moxie can reach that address.`;
  }catch(e){ $('#endpoint-info').textContent='Could not build the endpoint QR.'; }
}
// regenerate when the user edits the broker host
document.addEventListener('DOMContentLoaded',()=>{});
setTimeout(()=>{ const el=$('#broker-host'); if(el) el.addEventListener('change',()=>{brokerHost=el.value.trim();loadEndpointQR();}); },0);

// ---- connection monitor ----
async function pollMonitor(){
  let s; try{ s=await api('/local/broker/status',{auth:false}); }catch(e){ return; }
  let summaryHtml, ok=false;
  if(!s.ok){ summaryHtml='⚠️ MQTT supervisor not running (start it in mqtt/).'; }
  else if(s.robots && s.robots.length){
    const r=s.robots[0];
    summaryHtml=`✅ Moxie connected — <b>${r.device_id.slice(0,16)}…</b>`+(r.firmware?` · firmware <b>${escapeHtml(r.firmware)}</b>`:''); ok=true;
  } else { summaryHtml=`Broker up · app: ${s.app} · waiting for Moxie…`; }
  const logHtml = (s.recent||[]).slice().reverse().map(e=>{
    const t=new Date(e.t*1000).toLocaleTimeString();
    const cls=e.kind==='error'?'e':e.kind==='robot'?'r':e.kind==='chat'?'c':'i';
    return `<div class="ln ${cls}"><span>${t}</span> ${escapeHtml(e.text)}</div>`;
  }).join('') || '<div class="muted">No activity yet — scan the codes above.</div>';
  ['#mon-summary','#mon-summary2'].forEach(sel=>{const el=$(sel);if(el){el.innerHTML=summaryHtml;el.classList.toggle('ok',ok);}});
  ['#mon-log','#mon-log2'].forEach(sel=>{const el=$(sel);if(el)el.innerHTML=logHtml;});
}
function escapeHtml(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

// ---- Moxie status ----
async function refreshMoxie(){
  try{
    const st=await api('/local/state');
    if(st.robots && st.robots.length){ renderRobot(st.robots[0]); }
    else { $('#moxie-none').classList.remove('hidden'); $('#moxie-card').classList.add('hidden');
           $('#memory-card').classList.add('hidden'); }
  }catch(e){}
  refreshLive();
}
// live runtime state (battery/volume/Wi-Fi/mode/telemetry) from the MQTT supervisor
let liveDevice=null;
async function refreshLive(){
  const box=$('#robot-live'); if(!box) return;
  let f; try{ f=await api('/local/fleet',{auth:false}); }catch(e){ return; }
  renderPermits(f);
  // A *pending* robot (reached the broker, not on the permit list) is deliberately NOT
  // the live robot: it has no child config to show and no settings to edit. It lives in
  // the 🔐 Robot access card until a grown-up permits it.
  const served=(f.robots||[]).filter(r=>!r.pending);
  const cfgBox=$('#cfg-box');
  if(!f.ok || !served.length){
    liveDevice=null;
    const why = !f.ok ? 'supervisor offline'
              : (f.pending_count ? `${f.pending_count} robot${f.pending_count===1?'':'s'} waiting to be permitted`
                                 : 'no robot connected');
    box.innerHTML = `<div class="live-off">● Live state: ${escapeHtml(why)}</div>`;
    if(cfgBox) cfgBox.style.display='none';
    { const fc=$('#face-card'); if(fc) fc.classList.add('hidden'); }
    refreshInsights(null);
    refreshSafety(null);
    refreshMemory(null);
    refreshTelehealth(null);
    refreshSchedule(null);
    refreshVoice(null);
    refreshContent(null);
    return;
  }
  if(cfgBox) cfgBox.style.display='';
  liveDevice=served[0].device_id;
  fillModulePicker(f.schedule_modules);
  if(cfgBox && !cfgBox.open) prefillConfig(served[0], f);  // don't clobber active edits
  renderFaceCard(f, served[0]);
  box.innerHTML = served.map(r=>{
    const rows=[
      ['Battery', r.battery_level==null?'—':`${r.battery_level}%`],
      ['Volume',  r.audio_volume==null?'—':r.audio_volume],
      ['Wi-Fi',   r.wifi_ssid||'—'],
      ['Mode',    r.mode||'—'],
      ['Firmware',r.firmware||'—'],
      ['Telemetry', `${r.telemetry_count} events`],
    ].map(([k,v])=>`<div class="k"><span>${k}</span><b>${escapeHtml(String(v))}</b></div>`).join('');
    const ov=Object.keys(r.config_overrides||{});
    const ovHtml = ov.length? `<div class="k"><span>Config overrides</span><b>${escapeHtml(ov.join(', '))}</b></div>`:'';
    return `<div class="live-hd">● Live${r.ota_reboot_required?' · <span class="warn">OTA reboot pending</span>':''}</div>
            <div class="livegrid">${rows}${ovHtml}</div>`;
  }).join('');
  refreshInsights(liveDevice);
  refreshSafety(liveDevice);
  refreshMemory(liveDevice);
  refreshTelehealth(liveDevice);
  refreshSchedule(liveDevice);
  refreshVoice(liveDevice);
  refreshContent(liveDevice);
}

// ---- 🔐 Robot access (the device allowlist / pairing gate) ----
// Our broker accepts anonymous connections, so "reached the port" must not mean "is my
// child's robot". A robot that is not on the permit list is PENDING: it gets a minimal
// config with no child_pii and is served nothing else. One click here lets it in.
function renderPermits(f){
  const card=$('#permits-card'), box=$('#permits-box'); if(!card||!box) return;
  const toggle=$('#permit-allowall'), warn=$('#permit-warn');
  const robots=(f&&f.robots)||[];
  const pending=robots.filter(r=>r.pending), permitted=robots.filter(r=>!r.pending);
  const open=!!(f&&f.allow_unverified_bots);
  // Hidden only when there is nothing to say: supervisor up, gate closed, nobody waiting.
  card.classList.toggle('hidden', !(f&&f.ok) || (!pending.length && !open && !permitted.length));
  if(toggle && document.activeElement!==toggle) toggle.checked=open;
  if(warn) warn.innerHTML = open
    ? '⚠️ <b>Open:</b> any robot that reaches this server is paired and receives your '
      + 'child\u2019s name and birthday. Leave this off unless you are testing.'
    : 'Off (recommended). New robots wait here until you permit them.';
  const row=(r,act)=>
    `<div class="ev"><span>${escapeHtml(r.device_id||'')}</span> `
    + `<b>${escapeHtml(r.permit_label||r.summary||'')}</b> ${act}</div>`;
  const parts=[];
  if(pending.length) parts.push('<div class="insights-hd">Waiting for you</div>'
    + pending.map(r=>row(r,
        `<button class="ghost permit-btn" data-id="${escapeHtml(r.device_id)}" data-permit="1">Permit</button>`)).join(''));
  if(permitted.length) parts.push('<div class="insights-hd">Allowed</div>'
    + permitted.map(r=>row(r,
        `<button class="ghost permit-btn" data-id="${escapeHtml(r.device_id)}" data-permit="0">Revoke</button>`)).join(''));
  if(!parts.length) parts.push('<div class="live-off">No robot has connected yet.</div>');
  box.innerHTML=parts.join('');
  box.querySelectorAll('.permit-btn').forEach(b=>{ b.onclick=()=>setPermit(b.dataset.id, b.dataset.permit==='1'); });
}
async function setPermit(deviceId, permitted){
  const s=$('#permit-status'); if(s) s.textContent = permitted?'Permitting…':'Revoking…';
  try{
    const r=await api(`/local/robots/${encodeURIComponent(deviceId)}/permit`,
                      {method:'POST',auth:false,body:{permitted}});
    if(s) s.textContent = r.ok
      ? (permitted?'✅ Permitted — Moxie is paired and has its settings.'
                  :'⛔ Revoked — that robot no longer receives your child\u2019s settings.')
      : `⚠️ ${r.error||'failed'}`;
  }catch(e){ if(s) s.textContent='⚠️ '+(e.message||'failed'); }
  refreshLive();
}
{ const t=$('#permit-allowall'); if(t) t.onchange=async()=>{
    const s=$('#permit-status'); if(s) s.textContent='Saving…';
    try{
      const r=await api('/local/fleet/permits',
                        {method:'POST',auth:false,body:{allow_unverified_bots:t.checked}});
      if(s) s.textContent = r.ok
        ? (t.checked?'⚠️ Open — any robot that connects is now served.'
                    :'🔒 Closed — new robots wait for your approval.')
        : `⚠️ ${r.error||'failed'}`;
    }catch(e){ if(s) s.textContent='⚠️ '+(e.message||'failed'); }
    refreshLive();
  }; }

// 📈 telemetry insights (M6) — a real history since telemetry became durable.
//
// The card used to be an event log over the supervisor's RAM: a restart erased it and
// "last week" had no answer. It now renders the store's own two records — a week of
// daily roll-ups (zero-filled, so a quiet day reads as a quiet day) over the newest
// packet envelopes — and it never implies data the store does not hold:
//   * `persisted:false` (LoggingPolicy NO_DATA) says nothing is being kept, instead of
//     showing an empty week as if the robot had been silent;
//   * the footer states the real retention window and where the counts come from, so a
//     lifetime total behind a sliding window is not mistaken for the window itself.
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
async function refreshInsights(deviceId){
  const box=$('#robot-insights'); if(!box) return;
  if(!deviceId){ box.innerHTML='<div class="live-off">📈 Insights: no robot connected</div>'; return; }
  let t;
  try{ t=await api(`/local/robots/${encodeURIComponent(deviceId)}/telemetry`,{auth:false}); }
  catch(e){ box.innerHTML='<div class="live-off">📈 Insights: supervisor offline</div>'; return; }
  if(!t.ok){
    box.innerHTML=`<div class="live-off">📈 Insights: ${escapeHtml(t.error||'unavailable')}</div>`;
    return;
  }
  const tot=t.totals||{}, ret=t.retention||{};
  const hd=`<div class="insights-hd">📈 Insights · ${t.count} event${t.count===1?'':'s'} kept`
    +`${tot.total>t.count?` · ${tot.total} all time`:''}</div>`;
  if(t.persisted===false){
    box.innerHTML=hd+'<div class="live-off">Data sharing is '
      +`${escapeHtml(t.policy||'NO_DATA')}, so nothing is being saved — this card can only `
      +'show what has arrived since the supervisor started, and a restart clears it.</div>'
      +(t.count?`<div class="evlog">${(t.events||[]).map(e=>{
          const when=e.recorded_at?new Date(e.recorded_at*1000).toLocaleString():'—';
          return `<div class="ev"><span>${escapeHtml(when)}</span> <b>${escapeHtml(e.event_name)}</b></div>`;
        }).join('')}</div>`:'');
    return;
  }
  if(!t.count && !(tot.total>0)){
    box.innerHTML=hd+'<div class="live-off">No events yet — Moxie hasn\'t reported any '
      +'activity. Once it does, this card keeps the history across restarts.</div>';
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
  box.innerHTML=`${hd}${week}<div class="livegrid">${counts}</div>`
    +`<div class="evlog">${rows}</div><p class="tnote">${note}</p>`;
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
// ---- 🧠 what Moxie remembers (audit BEYOND #4) ----
// The runtime writes a few durable facts per activity at the end of a conversation
// (moxie_sdk/store.py::MemoryStore), each stamped with a stable id, the day, the module
// and how many turns it came from, and reads them back into the next prompt. A memory a
// parent cannot read or erase is not acceptable on a child's device, so every item is
// listed here and every cut the runtime offers is on this card: one item (✕), one
// activity, or everything — plus an inline edit, because a summary is more often wrong in
// one word than worthless. An edited item is pinned (📌): it never ages out.
const MEM_KINDS={'fact':'Fact','preference':'Likes','open thread':'Follow-up','summary':'Summary'};
let memDevice=null;

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

async function refreshMemory(deviceId){
  const card=$('#memory-card'), box=$('#memory-box'), all=$('#btn-mem-forget-all');
  if(!card||!box) return;
  memDevice=deviceId;
  const hideAll=()=>{ if(all){ all.classList.add('hidden'); all.dataset.armed='';
                               all.classList.remove('mem-arm'); } };
  if(!deviceId){
    box.innerHTML='<div class="live-off">No robot connected — nothing is being remembered.</div>';
    hideAll(); return;
  }
  let m;
  try{ m=await api('/local/robots/'+encodeURIComponent(deviceId)+'/memory',{auth:false}); }
  catch(e){ box.innerHTML='<div class="live-off">Supervisor offline — memory unavailable.</div>';
            hideAll(); return; }
  if(!m.ok){
    box.innerHTML='<div class="live-off">'+escapeHtml(m.error||'unavailable')+'</div>';
    hideAll(); return;
  }
  // The privacy switch (LoggingPolicy). NO_DATA stops new memories being written; what
  // was stored before the switch was flipped is still shown, and still erasable.
  const off = m.writes_allowed===false
    ? '<p class="mem-note off">⛔ Remembering is OFF for this robot (data sharing is '
      + escapeHtml(m.policy||'NO_DATA') + '). Nothing new is written — anything '
      + 'listed here was stored before that, and can still be erased.</p>'
    : '';
  if(!m.total){
    box.innerHTML='<div class="live-off">Moxie hasn’t remembered anything yet.</div>'+off;
    hideAll(); return;
  }
  const sections=(m.namespaces||[]).filter(ns=>ns.counts.total>0).map(ns=>{
    const p=ns.last_learned||{};
    const sub=[p.date?('last learned '+p.date):'', p.module_id?('activity '+p.module_id):'',
               p.turns?(p.turns+' turn'+(p.turns===1?'':'s')):'',
               ns.summarized_through?('summarized through turn '+ns.summarized_through):'']
              .filter(Boolean).join(' · ');
    const rows=(ns.items||[]).map(it=>{
      const q=it.provenance||{};
      const when=[q.date||'', q.module_id||''].filter(Boolean).join(' · ');
      // No id means a memory.json written before ids existed: it can still be read and
      // the activity erased, but there is nothing for the runtime to delete or correct,
      // so the row honestly offers neither button.
      const id=it.id||'';
      const tools=id
        ? '<button class="linkish mem-edit" title="Correct this" data-ns="'
          + escapeHtml(ns.namespace) + '" data-id="' + escapeHtml(id) + '">✏️</button>'
          + '<button class="linkish mem-x" title="Forget just this" data-ns="'
          + escapeHtml(ns.namespace) + '" data-id="' + escapeHtml(id) + '">✕</button>'
        : '';
      return '<div class="ev mem-row" data-id="'+escapeHtml(id)+'">'
           + '<span class="kind">'+escapeHtml(MEM_KINDS[it.kind]||it.kind)+'</span>'
           + '<b>'+escapeHtml(it.text)+(it.pinned?' <span class="pin" title="You corrected '
           + 'this — Moxie keeps it as written and never ages it out">📌</span>':'')+'</b>'
           + '<span class="when">'+escapeHtml(when||'no date')+'</span>'+tools+'</div>';
    }).join('');
    return '<div class="mem-ns"><div class="insights-hd">'+escapeHtml(ns.namespace)
      + ' · '+ns.counts.total+' item'+(ns.counts.total===1?'':'s')+'</div>'
      + (sub?'<div class="muted mem-sub">'+escapeHtml(sub)+'</div>':'')
      + '<div class="evlog">'+rows+'</div>'
      + '<button class="ghost tiny mem-forget" data-ns="'+escapeHtml(ns.namespace)+'">'
      + 'Erase this activity’s memory</button></div>';
  }).join('');
  const through = m.summarized_through
    ? '<p class="mem-note">Summarized through turn '+m.summarized_through
      +' — later turns have not been written down.</p>' : '';
  box.innerHTML=sections+off+through
    +'<p class="mem-note">Moxie writes these itself, so one can be wrong — and a wrong '
    +'one sticks until you erase it. ✏️ corrects one line, ✕ forgets it.</p>';
  box.querySelectorAll('.mem-forget').forEach(b=>armErase(
    b, 'Click again to erase', ()=>eraseMemory(deviceId, b.dataset.ns)));
  box.querySelectorAll('.mem-x').forEach(b=>armErase(
    b, 'sure?', ()=>eraseMemory(deviceId, b.dataset.ns, b.dataset.id)));
  box.querySelectorAll('.mem-edit').forEach(b=>b.onclick=()=>openMemEdit(b));
  if(all){
    all.classList.remove('hidden');
    armErase(all, 'Click again to erase EVERYTHING', ()=>eraseMemory(deviceId, ''));
  }
}

async function eraseMemory(deviceId, namespace, item){
  const s=$('#memory-status'); if(s) s.textContent='Erasing…';
  let url='/local/robots/'+encodeURIComponent(deviceId)+'/memory';
  if(namespace) url+='/'+encodeURIComponent(namespace);
  if(namespace && item) url+='/'+encodeURIComponent(item);
  try{
    const r=await api(url,{method:'DELETE',auth:false});
    const what=item?'that one':(namespace?('“'+namespace+'”'):'everything');
    if(s) s.textContent = r.erased
      ? '🧽 Erased '+what+' — Moxie no longer remembers it.'
      : 'Nothing was stored to erase.';
  }catch(e){ if(s) s.textContent='⚠️ '+(e.message||'erase failed'); }
  refreshMemory(deviceId);
}

// Inline correction. The row becomes a text box; Save posts the new wording, which the
// supervisor re-checks (safety + "not the child's own words") before storing it pinned.
function openMemEdit(btn){
  const row=btn.closest('.mem-row'); if(!row || row.dataset.editing) return;
  row.dataset.editing='1';
  const current=row.querySelector('b'), was=current.textContent.replace(/\s*📌$/,'').trim();
  const box=document.createElement('div');
  box.className='mem-edit-box';
  box.innerHTML='<input type="text" class="mem-edit-input" maxlength="240">'
    + '<button class="primary tiny mem-save">Save</button>'
    + '<button class="ghost tiny mem-cancel">Cancel</button>';
  row.after(box);
  const input=box.querySelector('.mem-edit-input');
  input.value=was; input.focus(); input.select();
  const close=()=>{ box.remove(); delete row.dataset.editing; };
  box.querySelector('.mem-cancel').onclick=close;
  box.querySelector('.mem-save').onclick=()=>{
    const text=input.value.trim();
    if(!text || text===was){ close(); return; }
    close(); editMemory(memDevice, btn.dataset.ns, btn.dataset.id, text);
  };
  input.onkeydown=e=>{ if(e.key==='Enter') box.querySelector('.mem-save').click();
                       if(e.key==='Escape') close(); };
}

async function editMemory(deviceId, namespace, item, text){
  const s=$('#memory-status'); if(s) s.textContent='Saving…';
  const url='/local/robots/'+encodeURIComponent(deviceId)+'/memory/'
    + encodeURIComponent(namespace)+'/'+encodeURIComponent(item);
  try{
    await api(url,{method:'POST',auth:false,body:{text}});
    if(s) s.textContent='✏️ Corrected — Moxie remembers it as you wrote it (📌 pinned).';
  }catch(e){ if(s) s.textContent='⚠️ '+memError(e); }
  refreshMemory(deviceId);
}

// The console answers a refused edit with its own memory shape, so the reason is inside
// the JSON `api()` hands back as an error string — show that, not the raw payload.
function memError(e){
  let msg=(e&&e.message)||'that correction was refused';
  try{ const j=JSON.parse(msg); if(j&&j.error) msg=j.error; }catch(_){}
  return msg;
}

// Wake alarms (RobotCloudConfig.alarms = WakeSchedule). The index of each label IS the
// `WakeEntry.days` uint32 we send — it must stay in step with
// moxie_sdk/cloud_config.py::WAKE_DAY_NAMES (0 = Monday … 6 = Sunday).
const CFG_DAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
function buildDayBoxes(){
  const box=$('#cfg-alarm-days'); if(!box || box.dataset.built) return;
  box.dataset.built='1';
  box.innerHTML=CFG_DAYS.map((d,i)=>
    `<label class="day"><input type="checkbox" class="cfg-day" value="${i}"> ${d}</label>`).join('');
}
function fillModulePicker(modules){
  const sel=$('#cfg-pref-module'); if(!sel) return;
  const ids=(modules||[]);
  if(sel.dataset.filled===ids.join(',')) return;      // don't clobber a live selection
  sel.dataset.filled=ids.join(',');
  const keep=sel.value;
  sel.innerHTML='<option value="">— none —</option>'+
    ids.map(m=>`<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
  if(keep) sel.value=keep;
}
function prefillConfig(r,f){
  buildDayBoxes();
  const ov=r.config_effective||r.config_overrides||{};   // fleet ⊕ per-robot
  if(r.audio_volume!=null) $('#cfg-vol').value=Math.round(r.audio_volume*100);
  if(ov.audio_volume!=null) $('#cfg-vol').value=Math.round(ov.audio_volume*100);
  const bt=ov.weekday_bedtime;
  $('#cfg-bed-start').value = (bt&&bt[0])||'';
  $('#cfg-bed-end').value   = (bt&&bt[1])||'';
  $('#cfg-wake-btn').checked   = ov.wake_button_enabled!==false;
  $('#cfg-touch-wake').checked = ov.touch_wake_enabled!==false;
  const wake=(ov.alarms&&(ov.alarms.wakes||[])[0])||null;
  $('#cfg-alarm-time').value = (wake&&wake.time)||'';
  $('#cfg-alarm-on').checked = !!(ov.alarms&&ov.alarms.enabled!==false&&wake);
  const days=(wake&&wake.days)||[];
  document.querySelectorAll('.cfg-day').forEach(c=>{ c.checked=days.includes(Number(c.value)); });
  const pref=((ov.schedule_preferences||{}).parent_requests||[])[0]||null;
  $('#cfg-pref-module').value = (pref&&pref.module_id)||'';
  $('#cfg-pref-at').value = pref&&pref.scheduled_at ? isoLocal(pref.scheduled_at) : '';
  const src=r.config_sources||{};
  const house=Object.keys(src).filter(k=>src[k]==='fleet');
  const box=$('#cfg-layers');
  if(box) box.textContent = house.length
    ? `🏠 From the house rules (all robots): ${house.join(', ')}` : '';
}
// epoch seconds → the "YYYY-MM-DDTHH:MM" a datetime-local input wants, in local time
function isoLocal(sec){
  const d=new Date(sec*1000), p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
async function saveConfig(){
  const fleet=!!($('#cfg-fleet')&&$('#cfg-fleet').checked);
  if(!liveDevice && !fleet){ return; }
  const s=$('#cfg-status'); s.textContent='Saving…';
  const start=$('#cfg-bed-start').value, end=$('#cfg-bed-end').value;
  const days=Array.from(document.querySelectorAll('.cfg-day'))
                  .filter(c=>c.checked).map(c=>Number(c.value));
  const atime=$('#cfg-alarm-time').value;
  const mod=$('#cfg-pref-module').value, at=$('#cfg-pref-at').value;
  const body={
    audio_volume: Number($('#cfg-vol').value),      // 0–100 → server clamps to 0–1
    wake_button_enabled: $('#cfg-wake-btn').checked,
    touch_wake_enabled: $('#cfg-touch-wake').checked,
    weekday_bedtime: (start&&end)? [start,end] : null,
    // WakeSchedule: one entry for now; null clears the field
    alarms: (atime&&days.length)
      ? {wakes:[{days,time:atime}], enabled:$('#cfg-alarm-on').checked} : null,
    // SchedulePreferences.ParentRequest — epoch SECONDS from the local wall clock
    schedule_preferences: (mod&&at)
      ? [{module_id:mod, scheduled_at:Math.floor(new Date(at).getTime()/1000)}] : null,
  };
  const url = fleet ? '/local/fleet/config'
                    : `/local/robots/${encodeURIComponent(liveDevice)}/config`;
  try{
    const r=await api(url,{method:'POST',auth:false,body});
    s.textContent = r.ok
      ? (fleet ? '✅ Saved as house rules — pushed to every robot.'
               : '✅ Saved — pushed to Moxie.')
      : `⚠️ ${r.error||'failed'}`;
    refreshLive();
  }catch(e){ s.textContent='⚠️ '+(e.message||'save failed'); }
}
function renderRobot(r){
  $('#moxie-none').classList.add('hidden');
  $('#moxie-card').classList.remove('hidden');
  $('#memory-card').classList.remove('hidden');
  $('#robot-card').innerHTML =
    `<div><strong>${r.name||'Moxie'}</strong></div>
     <div class="k">Serial: ${r.serial||r['embodied-robot-id']||'—'}</div>
     <div class="k">Wi-Fi: ${r['wifi-ssid']||'—'}</div>
     <div class="k">Status: ${r['pairing-status']||r.state||'paired'}</div>`;
  // Wake up REALLY publishes the recovered `wakeup` command now, so the button reports
  // what happened instead of flashing "Sent!" at a no-op: published, or the reason it
  // was not. It never claims Moxie woke — the protocol has no acknowledgement.
  const say=t=>{ const s=$('#dev-status'); if(s) s.textContent=t; };
  $('#btn-wake').onclick=async()=>{
    say('Waking Moxie…');
    try{
      const res=await api(`/api/robots/${r.id}/wakeup`,{method:'POST'});
      if(res && res.published){ flash('#btn-wake','Sent'); say('⏰ '+(res.note||'Command sent to Moxie.')); }
      else{ say('⚠️ '+(res.reason||res.error||'could not send')); }
    }catch(e){ say('⚠️ '+((e&&e.message)||'could not reach the appliance')); }
  };
  // Reboot is NOT something we can do: no cloud→robot reboot command exists in the
  // recovered protocol (fleet.py::UNSUPPORTED_ACTIONS). A disabled button with the
  // reason beats a button that lies.
  const rb=$('#btn-reboot');
  if(rb){
    rb.disabled=true;
    rb.title='No remote reboot command has been recovered from Moxie\'s firmware — '
      +'turn Moxie off and on at the button.';
    rb.textContent='Reboot (not available)';
    rb.onclick=null;
  }
  say('');
}
function flash(sel,txt){const b=$(sel),o=b.textContent;b.textContent=txt;setTimeout(()=>b.textContent=o,1200);}

// ---- 🎭 Be Moxie (puppet / telehealth, audit ADOPT #7) ----
// A remote grown-up drives the body: Moxie's own brain is switched off and every line
// comes from this box. The vocabulary is NOT kept here — the mood list and the intensity
// ceiling come from the supervisor (`moxie_sdk/vocab.py`, the 11 recovered ePlaybackMood
// names and maxIntensity=2), so this card can never offer a mood the robot's enum does
// not have. Nothing polls on its own: it rides refreshLive()'s cadence like every other
// card, so the transcript updates without a second timer.
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

// ---- 📅 Today's plan (the recommender's "why this activity today", BEYOND #7) ----
// The supervisor plans the day the robot pulls and keeps one plain sentence per entry
// explaining why it is there. This card only reads it: nothing here changes the plan (that
// is ⚙️ Settings — bedtime and requested activities), so it offers no control it
// cannot honour. It rides refreshLive()'s cadence like every other card, plus a Refresh
// button because a parent who just changed a bedtime wants to see the day re-planned.
//
// Two honesty rules the card keeps:
//   * an entry with no clock time is a fixture or a chat break, and shows "—", never an
//     invented hour;
//   * "no telemetry module signal" is stated plainly — finish/abandon comes from the
//     robot's own reports, not from the telemetry stream.
let schedDevice=null;
async function refreshSchedule(deviceId){
  const card=$('#schedule-card'); if(!card) return;
  schedDevice=deviceId;
  if(!deviceId){ card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  let s;
  try{ s=await api(`/local/robots/${encodeURIComponent(deviceId)}/schedule`,{auth:false}); }
  catch(e){ renderSchedule({ok:false,error:'Supervisor unreachable'}); return; }
  renderSchedule(s);
}
function renderSchedule(s){
  const head=$('#sched-head'), list=$('#sched-list'), foot=$('#sched-foot');
  if(!list) return;
  s=s||{};
  if(!s.ok){
    if(head) head.textContent='';
    if(foot) foot.textContent='';
    const why=(s.error==='supervisor not reachable')?'Supervisor unreachable':(s.error||'unavailable');
    list.innerHTML=`<div class="live-off">${escapeHtml(why)}</div>`;
    return;
  }
  const who=s.child_name||(s.device_id?String(s.device_id).slice(0,16)+'\u2026':'this robot');
  if(head) head.innerHTML=`Planned for <b>${escapeHtml(who)}</b>, ${escapeHtml(s.day||'today')}`
    + (s.served?'':' \u00b7 not pulled by the robot yet');
  const rows=(s.entries||[]);
  if(!rows.length){
    list.innerHTML='<div class="live-off">No plan yet \u2014 the robot pulls its day when it '
      + 'wakes.</div>';
    if(foot) foot.textContent='';
    return;
  }
  list.innerHTML=rows.map(e=>{
    const t=e.time_local||'\u2014';
    const pin=e.pinned?'<span class="sched-pin" title="A parent asked for this">\u2691</span>':'';
    return `<div class="sched-row${e.fixture?' fixture':''}">`
      + `<span class="sched-at">${escapeHtml(t)}</span>`
      + `<div class="sched-body"><b>${escapeHtml(e.name||e.module_id||'')}</b>${pin}`
      + `<div class="sched-why">${escapeHtml(e.why||'')}</div></div></div>`;
  }).join('');
  if(foot){
    const c=s.constraints||{}, bed=c.bedtime||{}, pr=c.parent_request||{};
    const bits=[];
    if(bed.enabled){
      const w=`${bed.starts_at||''}\u2013${bed.ends_at||''}`;
      bits.push(`Bedtime ${escapeHtml(w)} honoured`
        + (s.dropped_for_bedtime?` (${s.dropped_for_bedtime} later slot`
            + `${s.dropped_for_bedtime===1?'':'s'} dropped)`:''));
    } else bits.push('No bedtime set');
    if(pr.count) bits.push(`${pr.count} parent request${pr.count===1?'':'s'} pinned `
      + `(${(pr.pinned||[]).map(p=>escapeHtml(p.module_id)+(p.at?' at '+escapeHtml(p.at):'')).join(', ')})`);
    if(!c.telemetry_signal) bits.push('No telemetry module signal \u2014 finish/abandon '
      + "comes from the robot's reports");
    foot.innerHTML=bits.join(' \u00b7 ');
  }
}
{ const b=$('#btn-sched-refresh'); if(b) b.onclick=()=>refreshSchedule(schedDevice||liveDevice); }

// ---- 🎨 Moxie's look (face customization, audit ADOPT #9) ----
// The child's chosen face rides down inside `child_pii` as `face_options` — a list of
// layer labels — and the pushed `child_pii.id` is re-derived from that list, which is
// what stops the robot compositing from a stale cached texture. The catalog is NOT kept
// here: it comes from the supervisor's `moxie_sdk.faces`, so this card can never offer a
// slot or an option the SDK would reject. Our recovered docs name all fourteen layers but
// list concrete choices for only two, so a slot with no options renders as a plain,
// honest "we don't have these" line rather than an empty picker.
let FACE_CATALOG=[];
function renderFaceCard(f, robot){
  const card=$('#face-card'), box=$('#face-box'); if(!card||!box) return;
  FACE_CATALOG=(f&&f.face_catalog)||[];
  if(!FACE_CATALOG.length){ card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  const chosen=((robot&&(robot.config_effective||robot.config_overrides))||{}).face||{};
  const sig=FACE_CATALOG.map(s=>s.id).join(',');
  if(box.dataset.built!==sig){
    box.dataset.built=sig;
    box.className='facebox';
    box.innerHTML=FACE_CATALOG.map(slot=>{
      const opts=slot.options||[];
      if(!opts.length){
        return `<div class="faceslot uncited"><div class="fs-hd">${escapeHtml(slot.label)}
          <span class="fs-note">${escapeHtml(slot.note||'')}</span></div>
          <div class="muted" style="font-size:12px">Not in our recovered documents — see
          “Advanced: layer names” below.</div></div>`;
      }
      const sel=`<select class="face-pick" data-slot="${escapeHtml(slot.id)}">`
        + '<option value="">— default —</option>'
        + opts.map(o=>`<option value="${escapeHtml(o.id)}">${escapeHtml(o.label)}</option>`).join('')
        + '</select>';
      const sw=opts.filter(o=>o.hex).map(o=>
        `<button type="button" class="sw" data-slot="${escapeHtml(slot.id)}"`
        + ` data-opt="${escapeHtml(o.id)}" title="${escapeHtml(o.label)}"`
        + ` aria-label="${escapeHtml(slot.label)}: ${escapeHtml(o.label)}"`
        + ` style="background:${escapeHtml(o.hex)}"></button>`).join('');
      return `<div class="faceslot" data-slot="${escapeHtml(slot.id)}">
        <div class="fs-hd"><span class="sw" data-preview="${escapeHtml(slot.id)}"></span>
        ${escapeHtml(slot.label)}<span class="fs-note">${escapeHtml(slot.note||'')}</span></div>
        ${sel}${sw?`<div class="faceswatches">${sw}</div>`:''}</div>`;
    }).join('');
    box.querySelectorAll('.face-pick').forEach(sel=>{ sel.onchange=()=>syncFacePreview(); });
    box.querySelectorAll('.faceswatches .sw').forEach(b=>{ b.onclick=()=>{
      const sel=box.querySelector(`.face-pick[data-slot="${b.dataset.slot}"]`);
      if(sel){ sel.value = (sel.value===b.dataset.opt) ? '' : b.dataset.opt; syncFacePreview(); }
    };});
  }
  // Don't clobber an edit in progress: only re-seed from the server when nothing is dirty.
  if(box.dataset.dirty!=='1') prefillFace(chosen, robot);
}
function prefillFace(chosen, robot){
  const box=$('#face-box'); if(!box) return;
  box.querySelectorAll('.face-pick').forEach(sel=>{ sel.value=chosen[sel.dataset.slot]||''; });
  const ta=$('#face-custom');
  if(ta){ ta.value=(chosen.custom||[]).join('\n');
          ta.oninput=()=>{ box.dataset.dirty='1'; }; }
  const adv=$('#face-advanced'); if(adv && (chosen.custom||[]).length) adv.open=true;
  syncFacePreview();
  box.dataset.dirty='0';        // seeded from the server, not yet edited by a grown-up
  const src=(robot&&robot.config_sources)||{};
  const line=$('#face-layers');
  if(line){
    const bits=[];
    if(src.face==='fleet') bits.push('🏠 This look comes from the house rules (all robots)');
    if(robot&&robot.face_cache_id) bits.push('texture key '+robot.face_cache_id.slice(0,8));
    line.textContent=bits.join(' · ');
  }
}
function syncFacePreview(){
  const box=$('#face-box'); if(!box) return;
  FACE_CATALOG.forEach(slot=>{
    const dot=box.querySelector(`.sw[data-preview="${slot.id}"]`);
    const sel=box.querySelector(`.face-pick[data-slot="${slot.id}"]`);
    const opt=(slot.options||[]).find(o=>sel&&o.id===sel.value);
    if(dot) dot.style.background=(opt&&opt.hex)||'var(--bg)';
    box.querySelectorAll(`.faceswatches .sw[data-slot="${slot.id}"]`).forEach(b=>{
      b.setAttribute('aria-pressed', String(!!(sel&&b.dataset.opt===sel.value)));
    });
  });
  box.dataset.dirty='1';
}
function readFaceSelection(){
  const box=$('#face-box'), face={};
  if(box) box.querySelectorAll('.face-pick').forEach(sel=>{
    if(sel.value) face[sel.dataset.slot]=sel.value;
  });
  const ta=$('#face-custom');
  const custom=(ta?ta.value:'').split('\n').map(x=>x.trim()).filter(Boolean);
  if(custom.length) face.custom=custom;
  return face;
}
// Saves ONLY `face`, to the very same config endpoint the ⚙️ form posts to. The
// supervisor merges overrides, so this cannot disturb volume/bedtime/alarms — and the
// ⚙️ form never sends `face`, so it cannot disturb the look either.
async function saveFace(reset){
  const fleet=!!($('#face-fleet')&&$('#face-fleet').checked);
  if(!liveDevice && !fleet) return;
  const s=$('#face-status'); s.textContent = reset?'Resetting…':'Saving…';
  const body={face: reset ? null : readFaceSelection()};
  const url = fleet ? '/local/fleet/config'
                    : `/local/robots/${encodeURIComponent(liveDevice)}/config`;
  try{
    const r=await api(url,{method:'POST',auth:false,body});
    s.textContent = r.ok
      ? (reset ? '✅ Back to the default look.'
               : (fleet ? '✅ Saved as house rules — every robot re-draws its face.'
                        : '✅ Saved — Moxie re-draws its face.'))
      : `⚠️ ${r.error||'failed'}`;
    if(r.ok){ const b=$('#face-box'); if(b) b.dataset.dirty='0'; }
    refreshLive();
  }catch(e){ s.textContent='⚠️ '+(e.message||'save failed'); }
}
{ const b=$('#btn-face-save'); if(b) b.onclick=()=>saveFace(false); }
{ const b=$('#btn-face-reset'); if(b) b.onclick=()=>saveFace(true); }

// ---- settings ----
{ const b=$('#btn-cfg-save'); if(b) b.onclick=saveConfig; }

// ---- dev: simulate ----
$('#btn-sim').onclick = async () => {
  if(!LAST.qr_payload){ alert('Generate a Wi-Fi pairing QR first (Wi-Fi tab).'); return; }
  // Pairing IS the parent saying "this robot is mine", so hand the pairing call the
  // robot's MQTT id when it is unambiguous (exactly one robot pending) — the server then
  // permits it as part of completing the pairing and no second click is needed.
  let device_id='';
  try{
    const f=await api('/local/fleet',{auth:false});
    if(f.ok && (f.pending||[]).length===1) device_id=f.pending[0];
  }catch(e){}
  await api('/local/simulate-robot-scan',
            {method:'POST',auth:false,body:{qr_payload:LAST.qr_payload, device_id}});
  setTimeout(refreshMoxie,500);
};

// ---- 🎚️ Voice (which voice speaks, which ears listen) ----
// The two dropdowns are populated from the SUPERVISOR, never from a list kept here: the
// gateway's audio models are discovered live and classified by name, and the local
// entries exist only when the engine that would run them is genuinely installed. So the
// card can never offer something that would fail on the way to a child's ears.
//
// Three honesty rules it keeps:
//   * "Discovering gateway models…" while the first listing is still in flight — the
//     local options render immediately rather than waiting on someone else's box;
//   * a gateway that is down says so beside the options it still has, and never blanks;
//   * the status line names the engine ACTUALLY installed, which is not always the pick
//     (a chosen engine that could not be built keeps the one already speaking).
let voiceDevice=null, voiceDirty=false;
const VOICE_GROUPS=['Gateway','Local','Built-in'];
async function refreshVoice(deviceId){
  const card=$('#voice-card'); if(!card) return;
  voiceDevice=deviceId;
  if(!deviceId){ card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  let v;
  try{ v=await api(`/local/robots/${encodeURIComponent(deviceId)}/voice`,{auth:false}); }
  catch(e){ renderVoiceCard({ok:false,error:'Supervisor unreachable'}); return; }
  renderVoiceCard(v);
}
function voiceOptions(sel, entries, selected){
  if(!sel) return;
  const by={};
  (entries||[]).forEach(e=>{ (by[e.group]=by[e.group]||[]).push(e); });
  const groups=VOICE_GROUPS.filter(g=>by[g]).concat(
    Object.keys(by).filter(g=>VOICE_GROUPS.indexOf(g)<0));
  sel.innerHTML=groups.map(g=>
    `<optgroup label="${escapeHtml(g)}">`
    + by[g].map(e=>`<option value="${escapeHtml(e.id)}"${e.id===selected?' selected':''}>`
        + `${escapeHtml(e.label||e.id)}${e.default?' — default':''}</option>`).join('')
    + '</optgroup>').join('');
  if(selected && !sel.querySelector(`option[value="${CSS.escape(selected)}"]`)){
    // A stored pick the gateway can no longer confirm stays in force — show it rather
    // than silently snapping the dropdown to something the parent never chose.
    sel.insertAdjacentHTML('afterbegin',
      `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}`
      + ' (not offered right now)</option>');
  }
  sel.value=selected||sel.value;
}
function renderVoiceCard(v){
  const note=$('#voice-note'), status=$('#voice-status');
  const speech=$('#voice-speech'), listening=$('#voice-listening');
  if(!speech||!listening) return;
  v=v||{};
  if(!v.ok){
    const why=(v.error==='supervisor not reachable')?'Supervisor unreachable':(v.error||'unavailable');
    speech.innerHTML=''; listening.innerHTML='';
    if(note) note.innerHTML=`<span class="live-off">${escapeHtml(why)}</span>`;
    if(status) status.textContent='';
    return;
  }
  if(!voiceDirty){
    voiceOptions(speech,(v.available||{}).speech,(v.selected||{}).speech);
    voiceOptions(listening,(v.available||{}).listening,(v.selected||{}).listening);
  }
  if(note){
    const bits=[];
    // The environment's pin comes first: it is the reason a dropdown looks short, and a
    // parent hunting for a missing gateway voice must not have to read past the rest.
    const pins=v.pin_notes||{};
    ['speech','listening'].forEach(k=>{ if(pins[k]) bits.push(escapeHtml(pins[k])); });
    if(v.discovering) bits.push('Discovering gateway models…');
    else if(v.gateway_error) bits.push('Gateway unreachable ('
      + escapeHtml(v.gateway_error)+') — local options only');
    const inst=v.installed||{};
    if(inst.speech) bits.push('Speaking with <b>'+escapeHtml(inst.speech)+'</b>');
    if(inst.listening) bits.push('Listening with <b>'+escapeHtml(inst.listening)+'</b>');
    else bits.push('Not listening (text turns still work)');
    note.innerHTML=bits.join(' · ');
  }
  if(status && !voiceDirty && !status.dataset.sticky) status.textContent='';
}
async function saveVoice(){
  const s=$('#voice-status'); if(!s) return;
  const body={speech:$('#voice-speech').value, listening:$('#voice-listening').value};
  s.dataset.sticky='1'; s.textContent='Saving…';
  try{
    const r=await api(`/local/robots/${encodeURIComponent(voiceDevice||liveDevice)}/voice`,
                      {method:'POST',auth:false,body});
    voiceDirty=false;
    if(r.ok){
      s.textContent='✅ Saved — the next thing Moxie says uses it.';
      renderVoiceCard(r);
    } else s.textContent='⚠️ '+(r.reason||r.error||'could not save');
  }catch(e){ s.textContent='⚠️ '+(e&&e.message?e.message:'could not save'); }
}
async function testVoice(){
  const s=$('#voice-status'); if(!s) return;
  s.dataset.sticky='1'; s.textContent='Speaking…';
  try{
    const r=await api(`/local/robots/${encodeURIComponent(voiceDevice||liveDevice)}/voice/test`,
                      {method:'POST',auth:false,body:{}});
    s.textContent=r.ok
      ? `✅ Played on ${String(voiceDevice||liveDevice).slice(0,16)}…: "${r.spoke||''}"`
      : '⚠️ '+(r.reason||r.error||'could not play');
  }catch(e){ s.textContent='⚠️ '+(e&&e.message?e.message:'could not play'); }
}
{
  const sp=$('#voice-speech'), ls=$('#voice-listening');
  if(sp) sp.onchange=()=>{ voiceDirty=true; };
  if(ls) ls.onchange=()=>{ voiceDirty=true; };
  const b=$('#btn-voice-save'); if(b) b.onclick=saveVoice;
  const t=$('#btn-voice-test'); if(t) t.onclick=testVoice;
  const r=$('#btn-voice-refresh'); if(r) r.onclick=()=>{
    voiceDirty=false;
    const s=$('#voice-status'); if(s){ delete s.dataset.sticky; s.textContent=''; }
    refreshVoice(voiceDevice||liveDevice);
  };
}

// ---- 📦 Content packs (backlog/content-packs.md) ----
// One JSON file that carries activities between machines. Three things this card does
// that a plain "import" button would not:
//   * it shows the REVIEW before anything is written — per item, with the field-level
//     diff, so installing a stranger's pack is never a leap of faith;
//   * it marks the items YOU edited here, and pre-selects "Keep mine" on them, so an
//     upgrade cannot quietly overwrite your own wording (upstream compares two version
//     integers and cannot see your edit at all);
//   * it never re-serializes the pack before sending it. The file's own text goes to the
//     supervisor for both the review and the install, so the digest the review checked is
//     the digest the install checks — a re-encode in the browser would change a `1.0`
//     into a `1` and turn a perfectly good file into a mismatch.
// Content is fleet-level (no device_id anywhere in these routes); the card follows the
// 🎚️ card's visibility rule only so the console has one idiom.
let contentFile=null, contentReview=null, contentDecisions={};
const CONTENT_KIND_GLYPH={conversation:'💬', global:'⚡', schedule:'📅'};
const CONTENT_STATE_TEXT={
  new:'New', upgrade:'Upgrade', conflict:'Replaces your edit', same:'Already installed',
  keep_local:'You edited this', fork:'Same version, different text',
  downgrade:'Older', downgrade_conflict:'Older, replaces your edit',
  invalid:'Cannot install'};

async function refreshContent(deviceId){
  const card=$('#content-card'); if(!card) return;
  if(!deviceId){ card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  let v;
  try{ v=await api('/local/content',{auth:false}); }
  catch(e){ v={ok:false,error:'Supervisor unreachable'}; }
  renderContentCard(v);
}

function renderContentCard(v){
  const head=$('#content-head'), list=$('#content-list'), packs=$('#content-packs');
  if(!head||!list) return;
  v=v||{};
  if(!v.ok){
    const why=(v.error==='supervisor not reachable')?'Supervisor unreachable':(v.error||'unavailable');
    head.innerHTML=`<span class="live-off">${escapeHtml(why)}</span>`;
    list.innerHTML=''; if(packs) packs.textContent='';
    const u=$('#btn-content-undo'); if(u) u.classList.add('hidden');
    return;
  }
  const c=v.counts||{};
  const bits=[`${c.total||0} item${(c.total||0)===1?'':'s'}`];
  if(c.from_packs) bits.push(`${c.from_packs} from packs`);
  if(c.edited) bits.push(`${c.edited} edited here`);
  head.textContent=bits.join(' · ');
  list.innerHTML=(v.items||[]).map(it=>{
    const flags=[];
    if(it.local_edited) flags.push('<span class="chip edited">edited here</span>');
    if(it.has_code) flags.push('<span class="chip warn" title="'
      + 'This appliance never runs a pack’s code">carries code</span>');
    if((it.pii||[]).length) flags.push('<span class="chip warn">mentions '
      + escapeHtml(it.pii.map(h=>h.name).join(', '))+'</span>');
    const from=it.origin==='pack'&&it.pack_id ? ' · from '+escapeHtml(it.pack_id)
             : (it.origin==='shipped' ? ' · built in' : '');
    return `<label class="packrow"><input type="checkbox" class="packpick" value="${escapeHtml(it.id)}" checked>
      <span class="packname">${CONTENT_KIND_GLYPH[it.kind]||'•'} ${escapeHtml(it.name)}</span>
      <span class="muted">v${it.source_version}${from}</span>
      ${flags.join('')}</label>`;
  }).join('') || '<div class="muted">Nothing installed yet.</div>';
  if(packs){
    packs.innerHTML=(v.packs||[]).length
      ? 'Installed packs: '+v.packs.map(p=>`<b>${escapeHtml(p.name||p.id)}</b> v${p.pack_version}`
          +` (${p.item_count} item${p.item_count===1?'':'s'})`).join(' · ')
      : '';
  }
  const undo=$('#btn-content-undo');
  if(undo){
    undo.classList.toggle('hidden', !v.undo_available);
    undo.title=v.undo_label||'';
  }
  renderContentPii();
}

function contentPicked(){
  return Array.from($$('.packpick')).filter(b=>b.checked).map(b=>b.value);
}

function renderContentPii(){
  const box=$('#content-pii'); if(!box) return;
  const picked=new Set(contentPicked());
  const names=new Set();
  Array.from($$('.packrow')).forEach(row=>{
    const box2=row.querySelector('.packpick');
    if(!box2||!picked.has(box2.value)) return;
    row.querySelectorAll('.chip.warn').forEach(chip=>{
      const m=/^mentions (.+)$/.exec(chip.textContent||'');
      if(m) m[1].split(', ').forEach(n=>names.add(n));
    });
  });
  box.innerHTML = names.size
    ? '⚠️ Some of the text you are exporting mentions '
      + escapeHtml(Array.from(names).join(', '))
      + '. Edit it first if you would rather it did not leave this house — this check only'
      + ' knows the names set up here, so read the prompts before you send them on.'
    : '';
}

async function exportContent(){
  const s=$('#content-status'); if(!s) return;
  const items=contentPicked();
  if(!items.length){ s.textContent='⚠️ Tick at least one item to export.'; return; }
  const name=($('#content-name').value||'').trim() || 'Moxie content';
  s.textContent='Building the pack…';
  const q=new URLSearchParams({items:items.join(','), name:name, id:name});
  try{
    const r=await fetch('/local/content/export?'+q.toString());
    if(!r.ok){ const b=await r.json().catch(()=>({}));
               s.textContent='⚠️ '+(b.error||'could not export'); return; }
    const text=await r.text();
    const url=URL.createObjectURL(new Blob([text],{type:'application/json'}));
    const a=document.createElement('a');
    a.href=url; a.download=(JSON.parse(text).id||'moxie-content')+'.moxiepack.json';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    s.textContent=`✅ Exported ${items.length} item${items.length===1?'':'s'}.`;
  }catch(e){ s.textContent='⚠️ '+(e&&e.message?e.message:'could not export'); }
}

async function reviewContentFile(file){
  const s=$('#content-status'); if(!s||!file) return;
  s.textContent='Reading the pack…';
  let text;
  try{ text=await file.text(); }
  catch(e){ s.textContent='⚠️ could not read that file'; return; }
  contentFile=text;
  try{
    const r=await fetch('/local/content/review',
                        {method:'POST',headers:{'Content-Type':'application/json'},body:text});
    const v=await r.json();
    if(!v.ok){ s.textContent='⚠️ '+(v.error||'this file is not a content pack');
               hideContentReview(); return; }
    contentReview=v; contentDecisions={};
    (v.items||[]).forEach(it=>{ contentDecisions[it.id]=it.decision; });
    renderContentReview(v); s.textContent='';
  }catch(e){ s.textContent='⚠️ '+(e&&e.message?e.message:'could not read that pack'); }
}

function renderContentReview(v){
  const box=$('#content-review'), head=$('#content-review-head'), list=$('#content-review-list');
  if(!box||!head||!list) return;
  box.classList.remove('hidden');
  const p=v.pack||{};
  const bits=[`<b>${escapeHtml(p.name||p.id||'this pack')}</b> v${p.pack_version}`];
  if(p.author) bits.push('by '+escapeHtml(p.author));
  bits.push(`${(v.items||[]).length} item${(v.items||[]).length===1?'':'s'}`);
  if(v.digest==='mismatch') bits.push('<span class="warn">this file was changed after it '
    +'was exported — nothing is pre-selected</span>');
  if(v.digest==='absent') bits.push('<span class="warn">no checksum — this pack was '
    +'written by hand</span>');
  head.innerHTML=bits.join(' · ');
  list.innerHTML=(v.items||[]).map(it=>{
    const chip=`<span class="chip st-${escapeHtml(it.state)}">`
      + escapeHtml(CONTENT_STATE_TEXT[it.state]||it.state)+'</span>';
    const warn=(it.warnings||[]).map(w=>`<div class="muted warn">⚠️ ${escapeHtml(w)}</div>`).join('');
    const why=(it.reasons||[]).map(w=>`<div class="muted warn">${escapeHtml(w)}</div>`).join('');
    const diff=(it.diff||[]).map(d=>d.kind==='text'
      ? `<details class="packdiff"><summary>${escapeHtml(d.field)}</summary>`
        + `<pre>${escapeHtml((d.diff||[]).join('\n'))}</pre></details>`
      : `<div class="packdiff scalar">${escapeHtml(d.field)}: `
        + `${escapeHtml(d.old)} → ${escapeHtml(d.new)}</div>`).join('');
    const pick=['accept','keep','skip'].map(d=>{
      const label={accept:'Accept', keep:'Keep mine', skip:'Skip'}[d];
      const off=(d==='accept'&&!it.installable)?' disabled':'';
      const on=(contentDecisions[it.id]===d)?' checked':'';
      return `<label class="packdec"><input type="radio" name="dec-${escapeHtml(it.id)}"`
        + ` value="${d}" data-item="${escapeHtml(it.id)}"${on}${off}> ${label}</label>`;
    }).join('');
    return `<div class="packreview">
      <div class="packreview-hd">${CONTENT_KIND_GLYPH[it.kind]||'•'} `
      + `<b>${escapeHtml(it.name)}</b> ${chip} <span class="muted">`
      + `${escapeHtml(it.label)}</span></div>${why}${warn}${diff}
      <div class="packdecs">${pick}</div></div>`;
  }).join('');
  list.querySelectorAll('input[type=radio]').forEach(r=>{
    r.onchange=()=>{ contentDecisions[r.dataset.item]=r.value; };
  });
}

function hideContentReview(){
  contentReview=null; contentFile=null; contentDecisions={};
  const box=$('#content-review'); if(box) box.classList.add('hidden');
  const f=$('#content-file'); if(f) f.value='';
}

async function importContent(){
  const s=$('#content-status'); if(!s||!contentReview||!contentFile) return;
  const accept=Object.keys(contentDecisions).filter(k=>contentDecisions[k]==='accept');
  if(!accept.length){ s.textContent='⚠️ Nothing is set to Accept — nothing to install.'; return; }
  s.textContent='Installing…';
  try{
    const r=await fetch('/local/content/import',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pack:contentFile, accept:accept,
                           expect_digest:contentReview.expect_digest})});
    const v=await r.json();
    if(!v.ok){
      s.textContent=v.conflict
        ? '⚠️ That file changed since you looked at it — open it again.'
        : '⚠️ '+(v.error||'could not install');
      return;
    }
    s.textContent=`✅ Installed ${v.count} item${v.count===1?'':'s'} — Moxie uses them from `
      + 'the next thing she says.';
    hideContentReview();
    refreshContent(liveDevice);
  }catch(e){ s.textContent='⚠️ '+(e&&e.message?e.message:'could not install'); }
}

async function undoContent(){
  const s=$('#content-status'); if(!s) return;
  s.textContent='Putting it back…';
  try{
    const v=await api('/local/content/undo',{method:'POST',auth:false,body:{}});
    s.textContent=v.ok ? '✅ Put back what the last import replaced.'
                       : '⚠️ '+(v.error||'nothing to undo');
    refreshContent(liveDevice);
  }catch(e){ s.textContent='⚠️ '+(e&&e.message?e.message:'could not undo'); }
}

{
  const ex=$('#btn-content-export'); if(ex) ex.onclick=exportContent;
  const f=$('#content-file');
  if(f) f.onchange=()=>{ if(f.files&&f.files[0]) reviewContentFile(f.files[0]); };
  const im=$('#btn-content-import'); if(im) im.onclick=importContent;
  const ca=$('#btn-content-cancel');
  if(ca) ca.onclick=()=>{ hideContentReview(); const s=$('#content-status'); if(s) s.textContent=''; };
  const un=$('#btn-content-undo'); if(un) un.onclick=undoContent;
  const list=$('#content-list');
  if(list) list.addEventListener('change', e=>{
    if(e.target && e.target.classList.contains('packpick')) renderContentPii();
  });
}

// boot
if(TOKEN){ api('/local/state').then(()=>{$('#who').textContent='';enterApp();}).catch(()=>{}); }
