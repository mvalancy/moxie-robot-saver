/* Parent console: 📦 content packs and ✍️ the editor.
 * Packs: the REVIEW (per item, with a field-level diff) comes before anything is written;
 * items edited here pre-select "Keep mine"; and the file's own text is sent for both the
 * review and the install — never re-serialized, which would turn `1.0` into `1` and
 * break the digest. Content is fleet-level. */
let contentFile=null, contentReview=null, contentDecisions={};
//: The last inventory the card rendered — the ✏️ opens a row out of this rather
//: than re-fetching, so a click acts on exactly what the parent is looking at.
let contentInventory=[];
const CONTENT_KIND_GLYPH={conversation:'💬', global:'⚡', schedule:'📅'};
const CONTENT_STATE_TEXT={
  new:'New', upgrade:'Upgrade', conflict:'Replaces your edit', same:'Already installed',
  keep_local:'You edited this', fork:'Same version, different text',
  downgrade:'Older', downgrade_conflict:'Older, replaces your edit',
  invalid:'Cannot install'};

async function refreshContent(deviceId){
  // 💬 Try it (tryit.js) sits beside this card and needs no robot: refresh it either way.
  if(typeof refreshTryit==='function') refreshTryit(deviceId);
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
  contentInventory=v.items||[];
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
    const editable=(it.kind!=='schedule');
    const pencil=editable
      ? `<button class="ed-edit ghost" data-id="${escapeHtml(it.id)}" title="Edit this">✏️</button>`
      : '<span class="muted ed-locked" title="A schedule is the one kind of content that is'
        +' sent to the robot itself, and no Moxie has been given one that came from a pack">🔒</span>';
    return `<div class="packitem"><label class="packrow"><input type="checkbox" class="packpick" value="${escapeHtml(it.id)}" checked>
      <span class="packname">${CONTENT_KIND_GLYPH[it.kind]||'•'} ${escapeHtml(it.name)}</span>
      <span class="muted">v${it.source_version}${from}</span>
      ${flags.join('')}</label>${pencil}</div>`;
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
  }catch(e){ s.textContent=oops(e,'could not export'); }
}

async function reviewContentFile(file){
  const s=$('#content-status'); if(!s||!file) return;
  s.textContent='Reading the pack…';
  let text;
  try{ text=await file.text(); }
  catch(e){ s.textContent='⚠️ could not read that file'; return; }
  contentFile=text;
  try{
    const v=await postJson('/local/content/review', text);
    if(!v.ok){ s.textContent='⚠️ '+(v.error||'this file is not a content pack');
               hideContentReview(); return; }
    contentReview=v; contentDecisions={};
    (v.items||[]).forEach(it=>{ contentDecisions[it.id]=it.decision; });
    renderContentReview(v); s.textContent='';
  }catch(e){ s.textContent=oops(e,'could not read that pack'); }
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
    const v=await postJson('/local/content/import', {pack:contentFile, accept:accept,
                                  expect_digest:contentReview.expect_digest});
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
  }catch(e){ s.textContent=oops(e,'could not install'); }
}

async function undoContent(){
  const s=$('#content-status'); if(!s) return;
  s.textContent='Putting it back…';
  try{
    const v=await api('/local/content/undo',{method:'POST',auth:false,body:{}});
    s.textContent=v.ok ? '✅ Put back what the last import replaced.'
                       : '⚠️ '+(v.error||'nothing to undo');
    refreshContent(liveDevice);
  }catch(e){ s.textContent=oops(e,'could not undo'); }
}

// ---- ✍️ The editor ----
// Nothing is validated here: `packs.validate_item` runs in the supervisor route that
// writes. The default surface shows no JSON. `code` and `extension` are shown and never
// written — they round-trip byte-identical. No model call is made from this page.

//: The closed chip list: only `{{ dotted.path }}` and `{% if dotted.path %}`, the forms
//: the dependency-free renderer resolves identically to the sandbox — so a chip-built
//: prompt renders the same everywhere by construction.
const ED_CHIPS = [
  {label:'the child’s name', insert:'{{ volley.config.child_pii.nickname }}',
   why:'What Moxie calls your child, as it is set up on this appliance.'},
  {label:'what Moxie remembers', insert:'{{ volley.persist_data.<ns>.facts }}',
   why:'The facts this chat has kept. Needs a memory namespace, under Advanced.'},
  {label:'someone is in the room', insert:'{% if presence.face_present %}\n\n{% endif %}',
   why:'Only include the lines inside when Moxie can see somebody.'},
  {label:'the chat has run long', insert:'{% if session.overflow %}\n\n{% endif %}',
   why:'Only include the lines inside once the conversation is ready to wrap up.'}
];

let edDraft=null;          // {kind, id, key, data, local_rev, created}
let edRenderTimer=null;

function edEl(id){ return $('#'+id); }
function edVal(id){ const e=edEl(id); return e?e.value:''; }
function edSet(id,v){ const e=edEl(id); if(e) e.value=(v===null||v===undefined)?'':v; }

function edShow(on){
  const p=$('#ed-panel'); if(p) p.classList.toggle('hidden', !on);
  const n=$('#content-new'); if(n) n.classList.toggle('hidden', on);
}

//: The form is generated from the kind, not from a hand-maintained field list — a global
//: has no prompt and a conversation has no phrases, and neither has a schedule form at all.
function edSurfaces(kind){
  const conv=(kind==='conversation');
  $('#ed-conv').classList.toggle('hidden', !conv);
  $('#ed-glob').classList.toggle('hidden', conv);
  $('#ed-adv-conv').classList.toggle('hidden', !conv);
  $('#ed-adv-glob').classList.toggle('hidden', conv);
  $('#ed-identity').classList.toggle('hidden', !conv);
}

function renderChips(){
  const box=$('#ed-chips'); if(!box) return;
  box.innerHTML=ED_CHIPS.map((c,i)=>
    `<button class="ed-chip ghost" data-chip="${i}" title="${escapeHtml(c.why)}">`
    +`${escapeHtml(c.label)}</button>`).join('')
    +'<div class="muted">These write the only two kinds of placeholder that mean the same '
    +'thing everywhere Moxie runs. You can type anything else by hand — the panel below '
    +'will tell you if it would come out thinner on another machine.</div>';
  box.querySelectorAll('.ed-chip').forEach(b=>{
    b.onclick=e=>{ e.preventDefault(); edInsertChip(ED_CHIPS[+b.dataset.chip]); };
  });
}

function edInsertChip(chip){
  const box=$('#ed-prompt'); if(!box||!chip) return;
  const ns=(edVal('ed-memory-ns')||'').trim();
  const text=chip.insert.replace('<ns>', ns || 'memory_chat');
  const at=box.selectionStart===null?box.value.length:box.selectionStart;
  box.value=box.value.slice(0,at)+text+box.value.slice(box.selectionEnd);
  box.focus(); box.selectionStart=box.selectionEnd=at+text.length;
  edQueueRender();
}

function edBlank(kind){
  return kind==='conversation'
    ? {name:'', module_id:'', content_id:'default', prompt:'', opener:'', model:null,
       max_tokens:200, temperature:0.8, max_history:40, max_volleys:40,
       code:'', memory:{}, extension:{}}
    : {name:'', pattern:'', entity_groups:'', action:0, code:'', extension:{}};
}

function openEditor(kind, item){
  edDraft={kind:kind, id:(item&&item.id)||'', key:(item&&item.key)||'',
           data:edBlank(kind), local_rev:'', created:!item};
  edSurfaces(kind);
  edShow(true);
  $('#ed-title').textContent = item ? 'Editing' : (kind==='conversation'
    ? 'New conversation' : 'New command');
  $('#ed-id').textContent = item ? item.id : '';
  $('#ed-status').textContent='';
  $('#ed-shadow').textContent='';
  $('#ed-render').classList.add('hidden');
  renderChips();
  if(item) edLoad(item); else { edFill(edDraft.data); edRenderCarries(); edRenderRaw(); }
  edIdentityNote();
}

//: The editor opens an item by asking the supervisor for the pack it would export — the
//: ONE place that already serializes an installed item's normalized `data`. Reading it
//: back this way is what makes "`code` and `extension` survive a save untouched" true by
//: construction: the browser never reconstructs those fields, it round-trips them.
async function edLoad(item){
  const s=$('#ed-status'); s.textContent='Opening…';
  try{
    const r=await fetch('/local/content/export?'
                        + new URLSearchParams({items:item.id, name:'edit', id:'edit'}));
    if(!r.ok){ s.textContent='⚠️ could not open that item'; return; }
    const pack=await r.json();
    const row=(pack.items||[]).find(i=>i.kind===item.kind) || (pack.items||[])[0];
    if(!row){ s.textContent='⚠️ that item is no longer installed'; return; }
    edDraft.data=row.data||{}; edDraft.key=row.key||item.key;
    edFill(edDraft.data); edRenderCarries(); edRenderRaw();
    s.textContent='';
    if(edDraft.kind==='conversation') renderDraftPrompt();
  }catch(e){ s.textContent=oops(e,'could not open that item'); }
}

function edFill(d){
  edSet('ed-name', d.name);
  if(edDraft.kind==='conversation'){
    edSet('ed-module-id', d.module_id); edSet('ed-content-id', d.content_id);
    edSet('ed-opener', (d.opener||'').split('|').join('\n'));
    edSet('ed-prompt', d.prompt);
    edSet('ed-model', d.model||''); edSet('ed-max-tokens', d.max_tokens);
    edSet('ed-temperature', d.temperature); edSet('ed-max-history', d.max_history);
    edSet('ed-max-volleys', d.max_volleys);
    edSet('ed-memory-ns', (d.memory||{}).namespace||'');
    const on=$('#ed-memory-on'); if(on) on.checked=!!((d.memory||{}).namespace)
      && (d.memory||{}).summarize!==false;
  }else{
    edSet('ed-pattern', d.pattern);
    edSet('ed-entity-groups', d.entity_groups); edSet('ed-action', d.action||0);
    edSet('ed-phrases', (edPhrasesOf(d.pattern)||[]).join('\n'));
    const note=$('#ed-phrases-note');
    if(note) note.textContent=(d.pattern && !edPhrasesOf(d.pattern).length)
      ? 'This command was written as a regular expression by hand, so the phrase list '
        +'above cannot show it. Edit it under Advanced — and the phrase list will not '
        +'round-trip it back.'
      : '';
  }
}

//: The browser's half of `packs.compile_phrases` / `packs.phrases_of`. It is a *mirror*,
//: not an authority: what a parent typed travels to the save as `phrases` as well, and the
//: supervisor compiles and shadow-checks from that. If the two ever disagreed, the
//: supervisor's answer is the one that runs.
function edEscapePhrase(p){
  return String(p||'').trim().replace(/[()[\]{}?*+\-|^$\\.&~#\t\n\r\v\f]/g, m=>'\\'+m);
}
function edCompilePhrases(list){
  const parts=(list||[]).map(edEscapePhrase).filter(Boolean);
  return parts.length ? '('+parts.join('|')+')' : '';
}
function edPhrasesOf(pattern){
  const text=String(pattern||'');
  if(!(text.startsWith('(')&&text.endsWith(')'))) return [];
  const parts=text.slice(1,-1).split('|').map(p=>p.replace(/\\(.)/g,'$1'));
  return edCompilePhrases(parts)===text ? parts : [];
}

function edPhraseList(){
  return edVal('ed-phrases').split('\n').map(s=>s.trim()).filter(Boolean);
}

function edCollect(){
  const d=Object.assign({}, edDraft.data);
  d.name=edVal('ed-name').trim();
  if(edDraft.kind==='conversation'){
    d.module_id=edVal('ed-module-id').trim();
    d.content_id=edVal('ed-content-id').trim();
    d.opener=edVal('ed-opener').split('\n').map(s=>s.trim()).filter(Boolean).join('|');
    d.prompt=edVal('ed-prompt');
    d.model=edVal('ed-model').trim()||null;
    d.max_tokens=+edVal('ed-max-tokens')||200;
    d.temperature=parseFloat(edVal('ed-temperature'));
    if(isNaN(d.temperature)) d.temperature=0.8;
    d.max_history=+edVal('ed-max-history')||40;
    d.max_volleys=+edVal('ed-max-volleys')||40;
    const ns=edVal('ed-memory-ns').trim();
    const on=$('#ed-memory-on');
    d.memory = ns ? {namespace:ns, summarize:!!(on&&on.checked)} : {};
  }else{
    const phrases=edPhraseList();
    const typed=edVal('ed-pattern').trim();
    // The phrase list wins whenever it can round-trip the pattern in the box: an author
    // who edited the regex under Advanced keeps their regex, and one who typed phrases
    // gets them compiled. Neither silently discards the other.
    d.pattern = (phrases.length && (!typed || edPhrasesOf(typed).length))
      ? edCompilePhrases(phrases) : typed;
    d.entity_groups=edVal('ed-entity-groups').trim();
    d.action=+edVal('ed-action')||0;
  }
  return d;
}

function edIdentityNote(){
  const note=$('#ed-identity-note'); if(!note) return;
  const locked=!edDraft.created;
  ['ed-module-id','ed-content-id'].forEach(id=>{
    const e=edEl(id); if(e) e.readOnly=locked && edDraft.kind==='conversation';
  });
  note.textContent = (edDraft.kind!=='conversation') ? ''
    : (locked ? 'These two name the item, so they cannot change: saving under a different '
              + 'one would make a new conversation and leave this one where it is.'
              : 'These two name the conversation. After the first save they are fixed.');
}

function edRenderCarries(){
  const box=$('#ed-carries-body'); if(!box) return;
  const d=edDraft.data||{}, bits=[];
  if(d.code) bits.push('⚠️ It carries a <b>code</b> block (Python), which this appliance '
    +'never runs — see <b>extension</b> for behaviour this appliance can run. It is kept '
    +'exactly as it is when you save.');
  const ext=d.extension||{};
  if(ext && Object.keys(ext).length) bits.push('🧬 It carries a sandboxed <b>extension</b>. '
    +'This editor shows it and never changes it — writing one is the job of a different '
    +'surface (docs/architecture/backlog/sandboxed-extensions.md).');
  box.innerHTML = bits.length ? bits.map(b=>'<div>'+b+'</div>').join('')
    : '<div>Nothing but text: no code block, no extension.</div>';
}

function edRenderRaw(){
  const box=$('#ed-raw'); if(!box) return;
  box.value=JSON.stringify(edCollect(), null, 2);
  const note=$('#ed-raw-note');
  if(note) note.textContent = (edDraft.id ? edDraft.id+' · ' : '')
    + 'read-only. This is the item exactly as it is stored, after the appliance has thrown '
    + 'away anything that is not one of its own fields.';
}

//: Rung 1 — free, and the only feedback in P0 that is not a save. It never fires on a
//: keystroke *by itself*: the debounce below is 400 ms and the route it calls costs
//: nothing. The paid rung (a *Try it* that spends a brain call) is P1 and is deliberately
//: absent from this file, so no timer here can ever reach a model.
function edQueueRender(){
  if(edRenderTimer) clearTimeout(edRenderTimer);
  edRenderTimer=setTimeout(renderDraftPrompt, 400);
}

async function renderDraftPrompt(){
  if(!edDraft || edDraft.kind!=='conversation') return;
  const out=$('#ed-render'), note=$('#ed-render-note');
  if(!out) return;
  try{
    const v=await postJson('/local/content/render', {kind:'conversation', data:edCollect()});
    if(!v.ok){ note.textContent='⚠️ '+(v.error||'could not resolve this'); return; }
    out.classList.remove('hidden');
    out.textContent=v.prompt||'(nothing — this chat tells Moxie nothing yet)';
    const who=(v.context||{}).nickname||'your child';
    note.innerHTML = v.portable_identical
      ? escapeHtml('This is what Moxie’s brain is told, with '+who+' filled in.')
      : '<span class="warn">This is what Moxie’s brain is told here — but it uses '
        + 'something that only works on an appliance like this one, so it would come out '
        + 'thinner if you shared it with somebody running Moxie a simpler way.</span>';
  }catch(e){ note.textContent=oops(e,'could not resolve this'); }
}

async function saveItem(){
  if(!edDraft) return;
  const s=$('#ed-status'); s.textContent='Saving…';
  const body={kind:edDraft.kind, data:edCollect(), key:edDraft.key||'',
              local_rev:edDraft.local_rev||''};
  if(edDraft.kind==='global') body.phrases=edPhraseList();
  try{
    const v=await postJson('/local/content/item', body);
    if(!v.ok){
      s.textContent = v.conflict
        ? '⚠️ Somebody else saved this while you had it open — open it again before saving.'
        : '⚠️ '+(v.error||'could not save');
      return;
    }
    edDraft.id=v.id; edDraft.key=v.key; edDraft.created=false;
    edDraft.local_rev=v.local_rev; edDraft.data=edCollect();
    s.textContent='✅ Saved — Moxie uses it from the next thing she says. Undo puts back '
      + 'the version before this one, and there is only ever one step back.';
    edIdentityNote();
    renderShadow(v.shadow||[]);
    refreshContent(liveDevice);
  }catch(e){ s.textContent=oops(e,'could not save'); }
}

//: The shadow rule (§4.4), with its bound said in the same breath. A command's precedence
//: is alphabetical by its name, which is invisible and would otherwise be discovered by a
//: child saying something and getting the wrong answer.
function renderShadow(rows){
  const box=$('#ed-shadow'); if(!box) return;
  box.innerHTML = rows.length
    ? rows.map(r=>'<div class="warn">⚠️ '+escapeHtml(r.sentence)+'</div>').join('')
      + '<div>This only checks the phrases you typed — it cannot tell you about everything '
      + 'a child might say.</div>'
    : '';
}

{
  const nc=$('#btn-ed-new-conversation');
  if(nc) nc.onclick=()=>openEditor('conversation', null);
  const ng=$('#btn-ed-new-global');
  if(ng) ng.onclick=()=>openEditor('global', null);
  const sv=$('#btn-ed-save'); if(sv) sv.onclick=saveItem;
  const rd=$('#btn-ed-render'); if(rd) rd.onclick=e=>{ e.preventDefault(); renderDraftPrompt(); };
  const cx=$('#btn-ed-cancel');
  if(cx) cx.onclick=()=>{ edDraft=null; edShow(false); };
  const pr=$('#ed-prompt'); if(pr) pr.oninput=edQueueRender;
  ['ed-name','ed-opener','ed-phrases','ed-pattern','ed-memory-ns'].forEach(id=>{
    const e=edEl(id); if(e) e.oninput=edRenderRaw;
  });
  const list=$('#content-list');
  if(list) list.addEventListener('click', e=>{
    const b=e.target.closest && e.target.closest('.ed-edit');
    if(!b) return;
    e.preventDefault();
    const row=(contentInventory||[]).find(i=>i.id===b.dataset.id);
    if(row) openEditor(row.kind, row);
  });
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
