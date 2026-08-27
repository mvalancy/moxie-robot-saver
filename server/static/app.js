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
  if(name==='server'){ loadEndpointQR(); pollMonitor(); monTimer=setInterval(pollMonitor,2500); }
  if(name==='moxie') refreshMoxie();
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
  refreshMoxie();
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
  const sum=$('#mon-summary'), log=$('#mon-log');
  if(!s.ok){ sum.textContent='⚠️ MQTT supervisor not running (start it in mqtt/).'; sum.classList.remove('ok'); }
  else if(s.robots && s.robots.length){
    const r=s.robots[0];
    sum.innerHTML=`✅ Moxie connected — <b>${r.device_id.slice(0,16)}…</b>`+(r.firmware?` · firmware <b>${r.firmware}</b>`:'');
    sum.classList.add('ok');
  } else { sum.textContent=`Broker up · app: ${s.app} · waiting for Moxie…`; sum.classList.remove('ok'); }
  if(log && s.recent){
    log.innerHTML = s.recent.slice().reverse().map(e=>{
      const t=new Date(e.t*1000).toLocaleTimeString();
      const cls=e.kind==='error'?'e':e.kind==='robot'?'r':e.kind==='chat'?'c':'i';
      return `<div class="ln ${cls}"><span>${t}</span> ${escapeHtml(e.text)}</div>`;
    }).join('') || '<div class="muted">No activity yet.</div>';
  }
}
function escapeHtml(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

// ---- Moxie status ----
async function refreshMoxie(){
  try{
    const st=await api('/local/state');
    if(st.robots && st.robots.length){ renderRobot(st.robots[0]); }
    else { $('#moxie-none').classList.remove('hidden'); $('#moxie-card').classList.add('hidden'); }
  }catch(e){}
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

// ---- dev: simulate ----
$('#btn-sim').onclick = async () => {
  if(!LAST.qr_payload){ alert('Generate a Wi-Fi pairing QR first (Wi-Fi tab).'); return; }
  await api('/local/simulate-robot-scan',{method:'POST',auth:false,body:{qr_payload:LAST.qr_payload}});
  setTimeout(refreshMoxie,500);
};

// boot
if(TOKEN){ api('/local/state').then(()=>{$('#who').textContent='';enterApp();}).catch(()=>{}); }
