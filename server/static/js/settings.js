/* Parent console: ⚙️ settings, the paired-robot card, 🎨 Moxie's look, 📅 Today's plan. */
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
  // The card's status line (#dev-status) is about the robot on the card, so a redraw of
  // that same robot keeps it. Add to my account writes its answer there (its ⚠️ says to
  // press Permit), and both the Wi-Fi tab's poll, on seeing the record a claim made, and
  // re-opening the tab redraw. A card shown again, or for another robot, starts empty.
  const rc=$('#robot-card'), id=String(r.id);
  const same=!$('#moxie-card').classList.contains('hidden') && rc.dataset.id===id;
  $('#moxie-none').classList.add('hidden');
  $('#moxie-card').classList.remove('hidden');
  $('#memory-card').classList.remove('hidden');
  rc.dataset.id=id;
  rc.innerHTML =
    `<div><strong>${escapeHtml(r.name||'Moxie')}</strong></div>
     <div class="k">Serial: ${escapeHtml(r.serial||r['embodied-robot-id']||'—')}</div>
     <div class="k">Wi-Fi: ${escapeHtml(r['wifi-ssid']||'—')}</div>
     <div class="k">Status: ${escapeHtml(r['pairing-status']||r.state||'paired')}</div>`;
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
  lcWire(r);       // Unpair / Factory reset (js/robot.js)
  if(!same) say('');
}
function flash(sel,txt){const b=$(sel),o=b.textContent;b.textContent=txt;setTimeout(()=>b.textContent=o,1200);}

// ---- 🎨 Moxie's look ----
// The chosen face rides down in `child_pii.face_options`; the catalog comes from the
// supervisor's `moxie_sdk.faces`, so no slot or option here can be one the SDK rejects. A
// slot with no recovered options renders an honest "we don't have these" line.
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

// ---- 📅 Today's plan ----
// Read-only: one plain sentence per entry saying why it is there (changing the plan is ⚙️
// Settings). An entry with no clock time shows "—", never an invented hour, and "no
// telemetry module signal" is stated plainly.
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
