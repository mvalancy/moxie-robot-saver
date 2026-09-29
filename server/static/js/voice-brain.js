/* Parent console: 🎚️ Voice and 🧠 Brain pickers. Both lists come from the supervisor,
 * never from a list kept here, so the card cannot offer something that would fail.
 * Voice: local options render at once while gateway models are still being discovered;
 * a down gateway is named beside what still works; the status line names the engine
 * ACTUALLY installed, which is not always the pick. */
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
  }catch(e){ s.textContent=oops(e,'could not save'); }
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
  }catch(e){ s.textContent=oops(e,'could not play'); }
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

// ---- 🧠 Brain ----
// The pin note comes FIRST (it is why the dropdown looks short); every robot's row names
// the LAYER that chose its brain; a save lands on the next turn.
let brainDevice=null, brainDirty=false, brainView=null;
const BRAIN_SOURCE_TEXT={robot:'this robot', fleet:'house rule',
                         default:'appliance default', pin:'pinned by MOXIE_APP'};
async function refreshBrain(deviceId){
  const card=$('#brain-card'); if(!card) return;
  brainDevice=deviceId;
  if(!deviceId){ card.classList.add('hidden'); return; }
  card.classList.remove('hidden');
  let b;
  try{ b=await api(`/local/robots/${encodeURIComponent(deviceId)}/brain`,{auth:false}); }
  catch(e){ renderBrainCard({ok:false,error:'Supervisor unreachable'}); return; }
  renderBrainCard(b);
}
function brainOptions(sel, entries, selected){
  if(!sel) return;
  const by={};
  (entries||[]).forEach(e=>{ (by[e.group||'Other']=by[e.group||'Other']||[]).push(e); });
  sel.innerHTML=Object.keys(by).map(g=>
    `<optgroup label="${escapeHtml(g)}">`
    + by[g].map(e=>`<option value="${escapeHtml(e.id)}"${e.id===selected?' selected':''}>`
        + `${escapeHtml(e.label||e.id)}${e.default?' — default':''}</option>`).join('')
    + '</optgroup>').join('');
  if(selected) sel.value=selected;
}
function renderBrainCard(b){
  const note=$('#brain-note'), rows=$('#brain-robots'), status=$('#brain-status');
  const pick=$('#brain-pick'), scope=$('#brain-scope');
  if(!pick) return;
  b=b||{};
  if(!b.ok){
    const why=(b.error==='supervisor not reachable')?'Supervisor unreachable':(b.error||'unavailable');
    pick.innerHTML='';
    if(note) note.innerHTML=`<span class="live-off">${escapeHtml(why)}</span>`;
    if(rows) rows.innerHTML='';
    return;
  }
  brainView=b;
  const mine=(b.robots||[]).find(r=>r.device_id===brainDevice)||{};
  const wanted=(scope && scope.value==='fleet') ? (b.fleet||b.default) : (mine.brain||b.default);
  if(!brainDirty) brainOptions(pick, b.available, wanted);
  renderBrainNote();
  if(rows){
    rows.innerHTML=(b.robots||[]).map(r=>{
      const why=BRAIN_SOURCE_TEXT[r.source]||r.source||'';
      const over=r.requested? ` <span class="warn">(${escapeHtml(r.requested)} was chosen here)</span>`:'';
      return `<div class="k"><span>${escapeHtml(r.child||r.device_id)}</span>`
        + `<b>${escapeHtml(r.label||r.brain)} — ${escapeHtml(why)}${over}</b></div>`;
    }).join('');
  }
  if(status && !brainDirty && !status.dataset.sticky) status.textContent='';
}
async function saveBrain(brain){
  const s=$('#brain-status'); if(!s) return;
  const scope=($('#brain-scope')||{}).value==='fleet'?'fleet':'robot';
  const body={brain: brain===null?null:$('#brain-pick').value, scope};
  s.dataset.sticky='1'; s.textContent='Saving…';
  try{
    const r=await api(`/local/robots/${encodeURIComponent(brainDevice||liveDevice)}/brain`,
                      {method:'POST',auth:false,body});
    brainDirty=false;
    if(r.ok){
      s.textContent=(scope==='fleet')
        ? '✅ Saved as the house rule — every robot without its own choice uses it next turn.'
        : '✅ Saved — the next thing your child says goes to this brain.';
      renderBrainCard(r);
    } else s.textContent='⚠️ '+(r.reason||r.error||'could not save');
  }catch(e){ s.textContent=oops(e,'could not save'); }
}
// The blurb and the "needs" line follow the DROPDOWN rather than the saved value, so a
// parent reads what a brain IS before committing to it. Only the note is redrawn on a
// change — re-rendering the whole card would fight the <select> the parent is using.
function renderBrainNote(){
  const note=$('#brain-note'), pick=$('#brain-pick'), b=brainView;
  if(!note||!pick||!b) return;
  const bits=[];
  // The pin comes first: it is the reason the dropdown looks short.
  if(b.pin_note) bits.push('<b>'+escapeHtml(b.pin_note)+'</b>');
  const chosen=(b.available||[]).find(e=>e.id===pick.value);
  if(chosen && chosen.blurb) bits.push(escapeHtml(chosen.blurb));
  if(chosen && (chosen.needs||[]).length)
    bits.push('Needs '+chosen.needs.map(n=>'<code>'+escapeHtml(n)+'</code>').join(' + '));
  bits.push('House rule: <b>'+escapeHtml(b.fleet||'—')+'</b>'
    + ' · appliance default: <b>'+escapeHtml(b.default||'—')+'</b>');
  note.innerHTML=bits.join(' · ');
}
{
  const pk=$('#brain-pick'); if(pk) pk.onchange=()=>{ brainDirty=true; renderBrainNote(); };
  const sc=$('#brain-scope'); if(sc) sc.onchange=()=>{ brainDirty=false;
    refreshBrain(brainDevice||liveDevice); };
  const b=$('#btn-brain-save'); if(b) b.onclick=()=>saveBrain();
  const c=$('#btn-brain-clear'); if(c) c.onclick=()=>saveBrain(null);
  const r=$('#btn-brain-refresh'); if(r) r.onclick=()=>{
    brainDirty=false;
    const s=$('#brain-status'); if(s){ delete s.dataset.sticky; s.textContent=''; }
    refreshBrain(brainDevice||liveDevice);
  };
}
