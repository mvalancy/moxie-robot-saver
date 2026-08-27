const $ = s => document.querySelector(s);
let TOKEN = localStorage.getItem('moxie_token') || null;
let LAST = {};          // last prepare() result
let poll = null;

function show(id){ ['s-login','s-setup','s-qr','s-done'].forEach(s=>$('#'+s).classList.toggle('hidden', s!==id)); }
async function api(path, {method='GET', body, auth=true}={}){
  const h={'Content-Type':'application/json'};
  if(auth && TOKEN) h['Authorization']='Bearer '+TOKEN;
  const r=await fetch(path,{method,headers:h,body:body?JSON.stringify(body):undefined});
  if(!r.ok) throw new Error((await r.text())||r.status);
  const ct=r.headers.get('content-type')||''; return ct.includes('json')?r.json():r.text();
}

// ---- login ----
$('#btn-login').onclick = async () => {
  const email = $('#email').value.trim() || 'parent@home.lan';
  const res = await api('/local/quicklogin',{method:'POST',auth:false,body:{email,first_name:'Parent'}});
  TOKEN=res.token; localStorage.setItem('moxie_token',TOKEN);
  $('#who').textContent = res.email;
  await refreshState(); 
};

async function refreshState(){
  try{
    const st = await api('/local/state');
    $('#who').textContent = st.user.email||'';
    if(st.robots && st.robots.length){ renderDone(st.robots[0]); show('s-done'); return true; }
    if(st.children && st.children.length){ $('#child-name').value = st.children[0]['child-first-name']||''; }
    show('s-setup'); return false;
  }catch(e){ show('s-login'); return false; }
}

// ---- generate QR ----
$('#btn-qr').onclick = async () => {
  const name=$('#child-name').value.trim();
  if(name){ await api('/api/children',{method:'POST',body:{child:{'child-first-name':name}}}); }
  const body={ ssid:$('#ssid').value.trim(), password:$('#wifipass').value,
               band:$('#band').value, hidden:$('#hidden').checked };
  if(!body.ssid){ alert('Enter your Wi-Fi network name'); return; }
  LAST = await api('/local/pairing/prepare',{method:'POST',body});
  $('#qr-img').src = '/local/pairing/qr.png?payload='+encodeURIComponent(LAST.qr_payload);
  $('#phrase').textContent = LAST.recovery_phrase;
  $('#pair-status').classList.remove('ok');
  $('#pair-status').textContent = 'Waiting for Moxie to connect…';
  show('s-qr');
  startPolling();
};

function startPolling(){
  clearInterval(poll);
  poll=setInterval(async()=>{
    const st=await api('/local/state');
    if(st.robots && st.robots.length){
      clearInterval(poll);
      $('#pair-status').classList.add('ok');
      $('#pair-status').textContent='Moxie connected!';
      setTimeout(()=>{renderDone(st.robots[0]);show('s-done');},900);
    }
  },2000);
}

// ---- dev: simulate robot ----
$('#btn-sim').onclick = async () => {
  await api('/local/simulate-robot-scan',{method:'POST',auth:false,body:{qr_payload:LAST.qr_payload}});
  $('#pair-status').textContent='Simulated scan sent…';
};

// ---- done ----
function renderDone(r){
  $('#robot-card').innerHTML =
    `<div><strong>${r.name||'Moxie'}</strong></div>
     <div class="k">Serial: ${r.serial||r['embodied-robot-id']||'—'}</div>
     <div class="k">Wi-Fi: ${r['wifi-ssid']||'—'}</div>
     <div class="k">Status: ${r['pairing-status']||r.state||'paired'}</div>`;
  $('#btn-wake').onclick=()=>api(`/api/robots/${r.id}/wakeup`,{method:'POST'}).then(()=>flash('#btn-wake','Sent!'));
  $('#btn-reboot').onclick=()=>api(`/api/robots/${r.id}/reboot`,{method:'POST'}).then(()=>flash('#btn-reboot','Sent!'));
}
function flash(sel,txt){const b=$(sel),o=b.textContent;b.textContent=txt;setTimeout(()=>b.textContent=o,1200);}

$('#btn-restart').onclick=$('#btn-restart2').onclick=()=>{clearInterval(poll);refreshState();};

// boot
if(TOKEN) refreshState(); else show('s-login');
