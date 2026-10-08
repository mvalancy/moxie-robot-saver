/* Parent console, part 1 of 7: the app shell — api(), tabs, login, pairing QRs, the
 * connection monitor, the live fleet poll that drives every card, 🔐 robot access and
 * ➕ Add to my account.
 * Plain classic scripts sharing one global scope, loaded in order by index.html. */
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
let TOKEN = localStorage.getItem('moxie_token') || null;
let LAST = {};
let poll = null;

async function api(path, {method='GET', body, auth=true}={}){
  const h={'Content-Type':'application/json'};
  if(auth && TOKEN) h['Authorization']='Bearer '+TOKEN;
  const r=await fetch(path,{method,headers:h,body:body?JSON.stringify(body):undefined});
  if(!r.ok){
    // A refusal carries a sentence for the parent (`reason`, else `error`): throw that.
    const text=await r.text();
    let msg=text||String(r.status);
    try{ const j=JSON.parse(text); msg=(j&&(j.reason||j.error))||msg; }catch(_){}
    throw new Error(msg);
  }
  const ct=r.headers.get('content-type')||''; return ct.includes('json')?r.json():r.text();
}

/** POST a JSON body (an object, or text already serialized) and return the parsed answer —
 *  a refusal included, so a card can show its `error`/`conflict` instead of a status code. */
async function postJson(path, body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
                            body:typeof body==='string'?body:JSON.stringify(body)});
  return r.json();
}

/** The status line for a caught error. */
function oops(e, fallback){ return '⚠️ '+(e&&e.message?e.message:fallback); }

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
{ const el=$('#broker-host'); if(el) el.addEventListener('change',()=>{brokerHost=el.value.trim();loadEndpointQR();}); }

// ---- connection monitor ----
async function pollMonitor(){
  let s; try{ s=await api('/local/broker/status',{auth:false}); }catch(e){ return; }
  let summaryHtml, ok=false;
  if(!s.ok){ summaryHtml='⚠️ MQTT supervisor not running (start it in mqtt/).'; }
  else if(s.robots && s.robots.length){
    const r=s.robots[0];
    summaryHtml=`✅ Moxie connected — <b>${escapeHtml(String(r.device_id).slice(0,16))}…</b>`+(r.firmware?` · firmware <b>${escapeHtml(r.firmware)}</b>`:''); ok=true;
  } else { summaryHtml=`Broker up · app: ${escapeHtml(s.app)} · waiting for Moxie…`; }
  const logHtml = (s.recent||[]).slice().reverse().map(e=>{
    const t=new Date(e.t*1000).toLocaleTimeString();
    const cls=e.kind==='error'?'e':e.kind==='robot'?'r':e.kind==='chat'?'c':'i';
    return `<div class="ln ${cls}"><span>${t}</span> ${escapeHtml(e.text)}</div>`;
  }).join('') || '<div class="muted">No activity yet — scan the codes above.</div>';
  ['#mon-summary','#mon-summary2'].forEach(sel=>{const el=$(sel);if(el){el.innerHTML=summaryHtml;el.classList.toggle('ok',ok);}});
  ['#mon-log','#mon-log2'].forEach(sel=>{const el=$(sel);if(el)el.innerHTML=logHtml;});
}
// Quotes too: the result is interpolated into attribute values (data-id="…", title="…"),
// and a device id comes from whatever connected to an anonymous broker.
function escapeHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

// ---- Moxie status ----
// The account's side of the last /local/state: its robot records, and the robots on the
// broker that no account has added (`unclaimed`). 🔐 Robot access reads it too.
let ACCOUNT={robots:[], unclaimed:[]};
async function refreshMoxie(){
  try{
    const st=await api('/local/state');
    ACCOUNT={robots:st.robots||[], unclaimed:st.unclaimed||[]};
    if(st.robots && st.robots.length){ renderRobot(st.robots[0]); }
    else { $('#moxie-none').classList.remove('hidden'); $('#moxie-card').classList.add('hidden');
           $('#memory-card').classList.add('hidden'); }
    renderClaims();
  }catch(e){}
  refreshLive();
}

// ---- ➕ Add to my account ----
// A robot that paired by scanning the codes reaches the broker with no account record, so
// it has no robot card. One click claims it (the server permits it too); nothing is ever
// claimed without that click. Offered only where it can work: an account has one robot.
function claimable(deviceId){ return !ACCOUNT.robots.length && ACCOUNT.unclaimed.includes(deviceId); }
function claimButton(deviceId){
  return `<button class="primary claim-btn" data-id="${escapeHtml(deviceId)}">Add to my account</button>`;
}
function wireClaims(box, statusSel){
  box.querySelectorAll('.claim-btn').forEach(b=>{ b.onclick=()=>claimRobot(b.dataset.id, statusSel); });
}
function renderClaims(){
  const box=$('#claim-box'), list=$('#claim-list'); if(!box||!list) return;
  const ids=ACCOUNT.unclaimed.filter(claimable);
  box.classList.toggle('hidden', !ids.length);
  list.innerHTML=ids.map(id=>`<div class="ev"><span>${escapeHtml(id)}</span> ${claimButton(id)}</div>`).join('');
  wireClaims(list, '#claim-status');
}
let claiming=false;
async function claimRobot(deviceId, statusSel){
  if(claiming) return;                       // one click, one claim
  claiming=true;
  $$('.claim-btn').forEach(b=>{ b.disabled=true; });
  const s=$(statusSel); if(s) s.textContent='Adding Moxie to your account…';
  let r=null;
  try{
    r=await api(`/local/robots/${encodeURIComponent(deviceId)}/claim`,{method:'POST'});
    if(s) s.textContent='';
  }catch(e){ if(s) s.textContent=oops(e,'could not add it'); }
  claiming=false;
  await refreshMoxie();
  const d=$('#dev-status');
  if(r && d) d.textContent = !r.created ? 'This robot is already on your account.'
    : r.permitted ? '✅ Added to your account. Moxie is let in and gets your settings.'
    : `⚠️ Added to your account, but this server could not let it in yet (${r.permit_error}). `
      + 'Press Permit in Robot access.';
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
    refreshPreview(null);
    refreshSchedule(null);
    refreshVoice(null);
    refreshBrain(null);
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
  refreshPreview(liveDevice);
  refreshSchedule(liveDevice);
  refreshVoice(liveDevice);
  refreshBrain(liveDevice);
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
  const adding=pending.some(r=>claimable(r.device_id));
  if(pending.length) parts.push('<div class="insights-hd">Waiting for you</div>'
    + (adding ? '<div class="muted">Add to my account lets it in and gives you its robot card; '
              + 'Permit only lets it in.</div>' : '')
    + pending.map(r=>row(r,
        `<button class="ghost permit-btn" data-id="${escapeHtml(r.device_id)}" data-permit="1">Permit</button>`
        + (claimable(r.device_id) ? ' '+claimButton(r.device_id) : ''))).join(''));
  if(permitted.length) parts.push('<div class="insights-hd">Allowed</div>'
    + permitted.map(r=>row(r,
        `<button class="ghost permit-btn" data-id="${escapeHtml(r.device_id)}" data-permit="0">Revoke</button>`)).join(''));
  if(!parts.length) parts.push('<div class="live-off">No robot has connected yet.</div>');
  box.innerHTML=parts.join('');
  box.querySelectorAll('.permit-btn').forEach(b=>{ b.onclick=()=>setPermit(b.dataset.id, b.dataset.permit==='1'); });
  wireClaims(box, '#permit-status');
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

// boot: a returning parent (stored token) goes straight in
document.addEventListener('DOMContentLoaded',()=>{
  if(TOKEN){ api('/local/state').then(()=>{$('#who').textContent='';enterApp();}).catch(()=>{}); }
});
