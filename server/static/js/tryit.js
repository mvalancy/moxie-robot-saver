/* Parent console: 💬 Try it — talk to Moxie's real brain without a robot.
 * A preview: the supervisor runs the same brain, safety check and staging a robot's turn
 * runs, and publishes nothing (mqtt/supervisor/moxie_runtime/tryit.py). The session lives
 * here, in `tryHistory`, and travels with every send. The brain is reached from exactly
 * one place, `trySend`, bound to the Send button and the Enter key — never a timer and
 * never a keystroke in progress — because every send asks the brain once.
 * `trySession` names the session: Start over, a change of who answers and another robot
 * each begin a new one, and an answer that comes back for an older one is set aside. */
let tryOpts=null, tryDevice='', tryHistory=[], tryBusy=false, tryPick='', trySession=0;
const TRY_LATE=' The answer still on its way will be set aside.';

async function refreshTryit(deviceId){
  const card=$('#tryit-card'); if(!card) return;
  let v=null;
  try{ v=await api('/local/tryit'+(deviceId?'?device_id='+encodeURIComponent(deviceId):''),
                   {auth:false}); }
  catch(e){ v=null; }
  // No supervisor, or a robot this appliance will not serve: there is no brain to try.
  if(!v || !v.ok){ card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  const fresh=!tryOpts || (deviceId||'')!==tryDevice;
  if((deviceId||'')!==tryDevice){
    const late=tryBusy;
    tryDevice=deviceId||''; tryReset();
    const st=$('#try-status'); if(st && late) st.textContent='New session: another robot.'+TRY_LATE;
  }
  tryOpts=v;
  renderTryOptions(v, fresh);
}

//: `fresh`: the first fill, or another robot. Only then may the robot's current activity
//: fill an empty pick; any other refresh (a save, a permit) keeps what the parent picked,
//: "no particular activity" included.
function renderTryOptions(v, fresh){
  const sel=$('#try-brain'), mod=$('#try-module'), name=$('#try-name');
  if(!sel||!mod) return;
  const own=v.brain||{}, keep=sel.value, mkeep=mod.value;
  const whose=tryDevice ? 'This robot’s brain' : 'This appliance’s brain';
  sel.innerHTML=`<option value="">${escapeHtml(whose+': '+(own.label||own.id||'?'))}</option>`
    + (v.brains||[]).filter(b=>b.id!==own.id).map(b=>
        `<option value="${escapeHtml(b.id)}">${escapeHtml(b.label||b.id)}</option>`).join('');
  if(Array.from(sel.options).some(o=>o.value===keep)) sel.value=keep;
  mod.innerHTML='<option value="">— no particular activity —</option>'
    + (v.modules||[]).map(m=>`<option value="${escapeHtml(m.key)}">`
        + `${escapeHtml(m.name)} (${escapeHtml(m.key)})</option>`).join('');
  if(Array.from(mod.options).some(o=>o.value===mkeep) && (mkeep || !fresh)) mod.value=mkeep;
  else if(v.current_module) mod.value=v.current_module;
  if(name) name.placeholder=(v.child||{}).nickname||'';
  // A refresh that could not keep a pick (that brain or activity is gone) is a change too.
  tryChanged();
}

function tryBrainId(){
  const s=$('#try-brain');
  return (s&&s.value) || ((tryOpts||{}).brain||{}).id || '';
}

function trySelection(){
  return JSON.stringify([($('#try-brain')||{}).value||'', ($('#try-module')||{}).value||'',
                         (($('#try-name')||{}).value||'').trim()]);
}

function renderTryNote(){
  const v=tryOpts||{}, note=$('#try-note'), mod=$('#try-module');
  const reads=(v.module_brains||[]).includes(tryBrainId());
  if(mod) mod.disabled=!reads;
  if(!note) return;
  const bits=[];
  if(v.pin_note) bits.push(v.pin_note);
  if(!reads) bits.push('This brain does not use activities: it answers the same in every one.');
  const b=v.budget||{};
  if(b.per_hour) bits.push(`${b.remaining} of ${b.per_hour} tries left this hour.`);
  note.textContent=bits.join(' ');
}

function tryReset(){
  trySession++;              // an answer still on its way now answers an older session
  tryHistory=[];
  const log=$('#try-log'); if(log) log.innerHTML='';
  const st=$('#try-status'); if(st) st.textContent='';
}

//: A different brain, activity or name is a different conversation: start a new one,
//: even on the first line, when the only trace of the old one is an answer on its way.
function tryChanged(){
  renderTryNote();
  const now=trySelection();
  if(now===tryPick) return;
  tryPick=now;
  if(tryHistory.length || tryBusy){
    const late=tryBusy;
    tryReset();
    const st=$('#try-status');
    if(st) st.textContent='New session: who answers changed.'+(late?TRY_LATE:'');
  }
}

function tryActionText(a){
  if(a.type==='launch') return 'start '+(a.module_id||'an activity')
    + (a.content_id?'/'+a.content_id:'');
  if(a.type==='execute') return 'run '+(a.function||'a robot function');
  return {exit:'end this activity', exit_module:'end this activity', sleep:'go to sleep',
          enable_qr:'turn on QR scanning'}[a.type] || ('do '+a.type);
}

function trySafetyText(s){
  const who=s.stage==='input' ? 'What the child said' : 'What Moxie was going to say';
  const what=(s.labels&&s.labels.length?s.labels:s.categories||[]).join(', ');
  return s.action==='block'
    ? `${who} was blocked (${what}), so she says a safe line instead. A preview: nothing`
      + ' was added to the Safety card.'
    : `${who} was flagged (${what}). A preview: nothing was added to the Safety card.`;
}

//: One piece of her answer: the words, then what the markup makes the body do.
function tryChunkHtml(c){
  const p=c.perform||{}, sc=c.scored||{}, tags=[];
  const face=(p.faces||[]).map(f=>f.mood+(f.intensity?' ('+f.intensity+')':'')).join(' → ');
  if(face) tags.push('face: '+face);
  if((p.gestures||[]).length) tags.push('moves: '
    + p.gestures.map(g=>g.replace(/^Gesture_/,'')).join(', '));
  if((p.behaviours||[]).length) tags.push('body: '
    + p.behaviours.map(b=>b.replace(/^Bht_/,'')).join(', '));
  if((p.voice||[]).length) tags.push('voice: '+p.voice.join(', '));
  if((p.icons||[]).length) tags.push('screen: '+p.icons.join(', '));
  if((p.sounds||[]).length) tags.push('sound: '+p.sounds.join(', '));
  if(p.pauses) tags.push(`${p.pauses} pause${p.pauses===1?'':'s'}`);
  if(sc.dialog_act) tags.push('act: '+sc.dialog_act);
  const unknown=(p.unknown||[]).length
    ? `<div class="warn">Not in Moxie's catalog: ${escapeHtml(p.unknown.join(', '))}</div>` : '';
  return `<div class="try-line">${escapeHtml(c.text||'(nothing said)')}</div>`
    + `<div class="try-perf">${escapeHtml(tags.join(' · ')||'no face or moves')}</div>`
    + unknown
    + `<details class="try-raw"><summary>markup</summary>`
    + `<pre>${escapeHtml(c.markup||'')}</pre></details>`;
}

function renderTryTurn(speech, r){
  const log=$('#try-log'); if(!log) return;
  r=r||{};
  const reply=r.reply||{}, child=(r.child||{}).nickname;
  let moxie='';
  if(!r.ok){
    moxie+=`<div class="try-err warn">⚠️ ${escapeHtml(r.error||'The try did not go through.')}</div>`;
    const d=r.detail||{};
    const why=[d.type, d.status?'HTTP '+d.status:'', d.message].filter(Boolean).join(' · ');
    if(why) moxie+=`<div class="muted">${escapeHtml(why)}</div>`;
    if(reply.text) moxie+=`<div class="muted">She would have said: `
      + `<i>${escapeHtml(reply.text)}</i></div>`;
  }else{
    (reply.chunks||[]).forEach(c=>{ moxie+=tryChunkHtml(c); });
    (reply.actions||[]).forEach(a=>{
      moxie+=`<div class="try-act">↳ Moxie would ${escapeHtml(tryActionText(a))}</div>`; });
    if(reply.end_turn) moxie+='<div class="try-act">↳ and stop listening</div>';
  }
  (r.safety||[]).forEach(s=>{
    moxie+=`<div class="try-safety${s.action==='block'?' warn':''}">🛡️ `
      + `${escapeHtml(trySafetyText(s))}</div>`; });
  (r.notes||[]).forEach(n=>{ moxie+=`<div class="muted">${escapeHtml(n)}</div>`; });
  const label=(r.brain||{}).label;
  log.insertAdjacentHTML('beforeend', '<div class="try-turn">'
    + `<div class="try-you"><span>${escapeHtml('YOU'+(child?' (as '+child+')':''))}</span>`
    + ` ${escapeHtml(speech)}</div>`
    + `<div class="try-moxie"><span>${escapeHtml('MOXIE'+(label?' · '+label:''))}</span>`
    + `${moxie}</div></div>`);
  log.scrollTop=log.scrollHeight;
}

function tryReceipt(r){
  if(!r || !r.ok) return r && r.kind==='budget' ? 'No tries left this hour.' : '';
  const n=r.model_calls||0, pieces=((r.reply||{}).chunks||[]).length;
  return [`${n} model call${n===1?'':'s'}`, `${((r.elapsed_ms||0)/1000).toFixed(1)} s`,
          r.delivery==='stream' ? `streamed in ${pieces} piece${pieces===1?'':'s'}`
                                : 'one piece',
          'preview only: nothing was sent to a robot'].join(' · ');
}

async function trySend(){
  const box=$('#try-text'), st=$('#try-status'), btn=$('#btn-try-send');
  if(!box || tryBusy) return;
  const speech=(box.value||'').trim(); if(!speech) return;
  const mod=$('#try-module');
  const body={speech, history:tryHistory, device_id:tryDevice,
              brain:($('#try-brain')||{}).value||'',
              module:(mod&&!mod.disabled)?mod.value:'',
              nickname:(($('#try-name')||{}).value||'').trim()};
  const session=trySession;
  tryBusy=true; if(btn) btn.disabled=true;
  if(st) st.textContent='Asking Moxie’s brain…';
  let r;
  try{ r=await postJson('/local/tryit', body); }
  catch(e){ r={ok:false, kind:'unreachable', error:'The console could not reach the supervisor.'}; }
  tryBusy=false; if(btn) btn.disabled=false;
  if(r && r.budget && r.budget.per_hour && tryOpts){ tryOpts.budget=r.budget; renderTryNote(); }
  // Started over, or who answers changed, while this was on its way: it answers a session
  // that is gone, so it is neither shown nor carried into the new one.
  if(session!==trySession) return;
  renderTryTurn(speech, r);
  // Only a real answer moves the session on; a failed line stays in the box to resend.
  // A line typed while she was answering is the parent's next one: it stays.
  if(r && r.ok){ tryHistory=r.history||[]; if(box.value.trim()===speech) box.value=''; }
  if(st) st.textContent=tryReceipt(r);
}

{ const b=$('#btn-try-send'); if(b) b.onclick=trySend; }
// The Enter that commits an IME composition picks characters; it is not a send.
{ const t=$('#try-text'); if(t) t.onkeydown=e=>{
    if(e.key==='Enter' && !e.isComposing && e.keyCode!==229){ e.preventDefault(); trySend(); } }; }
{ const b=$('#btn-try-reset'); if(b) b.onclick=()=>{
    const late=tryBusy;
    tryReset(); const st=$('#try-status'); if(st) st.textContent='Started over.'+(late?TRY_LATE:''); }; }
['try-brain','try-module','try-name'].forEach(id=>{ const e=$('#'+id); if(e) e.onchange=tryChanged; });
