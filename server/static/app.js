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
    else { $('#moxie-none').classList.remove('hidden'); $('#moxie-card').classList.add('hidden'); }
  }catch(e){}
  refreshLive();
}
// live runtime state (battery/volume/Wi-Fi/mode/telemetry) from the MQTT supervisor
let liveDevice=null;
async function refreshLive(){
  const box=$('#robot-live'); if(!box) return;
  let f; try{ f=await api('/local/fleet',{auth:false}); }catch(e){ return; }
  const cfgBox=$('#cfg-box');
  if(!f.ok || !f.robot_count){
    liveDevice=null;
    box.innerHTML = `<div class="live-off">● Live state: ${f.ok?'no robot connected':'supervisor offline'}</div>`;
    if(cfgBox) cfgBox.style.display='none';
    refreshInsights(null);
    return;
  }
  if(cfgBox) cfgBox.style.display='';
  liveDevice=f.robots[0].device_id;
  if(cfgBox && !cfgBox.open) prefillConfig(f.robots[0]);   // don't clobber active edits
  box.innerHTML = f.robots.map(r=>{
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
}

// telemetry insights (M6): the Packet events the runtime stored for this robot
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
  const hd=`<div class="insights-hd">📈 Insights · ${t.count} event${t.count===1?'':'s'}</div>`;
  if(!t.count){
    box.innerHTML=hd+'<div class="live-off">No events yet — Moxie hasn\'t reported any activity.</div>';
    return;
  }
  const counts=(t.by_event||[]).map(c=>
    `<div class="k"><span>${escapeHtml(c.event)}</span><b>${c.count}</b></div>`).join('');
  const rows=(t.events||[]).map(e=>{
    const when=e.recorded_at?new Date(e.recorded_at*1000).toLocaleString():'—';
    return `<div class="ev"><span>${escapeHtml(when)}</span> <b>${escapeHtml(e.event_name)}</b></div>`;
  }).join('');
  box.innerHTML=`${hd}<div class="livegrid">${counts}</div><div class="evlog">${rows}</div>`;
}
function prefillConfig(r){
  const ov=r.config_overrides||{};
  if(r.audio_volume!=null) $('#cfg-vol').value=Math.round(r.audio_volume*100);
  const bt=ov.weekday_bedtime;
  $('#cfg-bed-start').value = (bt&&bt[0])||'';
  $('#cfg-bed-end').value   = (bt&&bt[1])||'';
  $('#cfg-wake-btn').checked   = ov.wake_button_enabled!==false;
  $('#cfg-touch-wake').checked = ov.touch_wake_enabled!==false;
}
async function saveConfig(){
  if(!liveDevice){ return; }
  const s=$('#cfg-status'); s.textContent='Saving…';
  const start=$('#cfg-bed-start').value, end=$('#cfg-bed-end').value;
  const body={
    audio_volume: Number($('#cfg-vol').value),      // 0–100 → server clamps to 0–1
    wake_button_enabled: $('#cfg-wake-btn').checked,
    touch_wake_enabled: $('#cfg-touch-wake').checked,
    weekday_bedtime: (start&&end)? [start,end] : null,
  };
  try{
    const r=await api(`/local/robots/${encodeURIComponent(liveDevice)}/config`,
                      {method:'POST',auth:false,body});
    s.textContent = r.ok ? '✅ Saved — pushed to Moxie.' : `⚠️ ${r.error||'failed'}`;
  }catch(e){ s.textContent='⚠️ '+(e.message||'save failed'); }
}
function renderRobot(r){
  $('#moxie-none').classList.add('hidden');
  $('#moxie-card').classList.remove('hidden');
  $('#robot-card').innerHTML =
    `<div><strong>${r.name||'Moxie'}</strong></div>
     <div class="k">Serial: ${r.serial||r['embodied-robot-id']||'—'}</div>
     <div class="k">Wi-Fi: ${r['wifi-ssid']||'—'}</div>
     <div class="k">Status: ${r['pairing-status']||r.state||'paired'}</div>`;
  $('#btn-wake').onclick=()=>api(`/api/robots/${r.id}/wakeup`,{method:'POST'}).then(()=>flash('#btn-wake','Sent!'));
  $('#btn-reboot').onclick=()=>api(`/api/robots/${r.id}/reboot`,{method:'POST'}).then(()=>flash('#btn-reboot','Sent!'));
}
function flash(sel,txt){const b=$(sel),o=b.textContent;b.textContent=txt;setTimeout(()=>b.textContent=o,1200);}

// ---- settings ----
{ const b=$('#btn-cfg-save'); if(b) b.onclick=saveConfig; }

// ---- dev: simulate ----
$('#btn-sim').onclick = async () => {
  if(!LAST.qr_payload){ alert('Generate a Wi-Fi pairing QR first (Wi-Fi tab).'); return; }
  await api('/local/simulate-robot-scan',{method:'POST',auth:false,body:{qr_payload:LAST.qr_payload}});
  setTimeout(refreshMoxie,500);
};

// boot
if(TOKEN){ api('/local/state').then(()=>{$('#who').textContent='';enterApp();}).catch(()=>{}); }
