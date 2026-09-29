/* Parent console: 🧠 what Moxie remembers. Every durable fact, with the day and the
 * activity it came from, and every cut the runtime offers — one item (✕), one activity,
 * or everything — plus an inline edit; an edited item is pinned (📌) and never ages out. */
const MEM_KINDS={'fact':'Fact','preference':'Likes','open thread':'Follow-up','summary':'Summary'};
let memDevice=null;


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
  }catch(e){ if(s) s.textContent=oops(e,'that correction was refused'); }
  refreshMemory(deviceId);
}
