/* Parent console: unpair and factory reset (docs/features/robot-lifecycle.md). Unpair is
 * DELETE /api/robots/{id}; a factory reset is the same call with ?rfs=1 and then the
 * restore_factory setup code, because no cloud-to-robot reset command is known. Nothing is
 * sent until the parent types the confirmation: UNPAIR (or the robot's name), or RESET plus
 * "cannot be undone" for a reset. The optional erase is the memory and insights cards' own
 * DELETE calls, made BEFORE the unpair (afterwards those cards are gone for this robot); the
 * child's profile is the existing DELETE /api/children/{id}, made AFTER it (the doc's order:
 * unpair first). There is no second erase path. */
const LC={robot:null, mode:'unpair', child:null, view:null, busy:false};

// Called by renderRobot with the paired robot's /local/state row.
function lcWire(robot){
  LC.robot=robot;
  const u=$('#btn-unpair'), f=$('#btn-factory-reset');
  if(u) u.onclick=()=>openLifecycle('unpair');
  if(f) f.onclick=()=>openLifecycle('reset');
}

// mode: 'unpair' | 'reset' (both need the robot's record) | 'code' (no record: only the
// reset code is shown, and nothing on this server changes).
async function openLifecycle(mode){
  const d=$('#lc-sheet'); if(!d || d.open) return;
  const robot = mode==='code' ? null : LC.robot;
  if(mode!=='code' && !robot) return;
  LC.mode=mode; LC.child=null; LC.view=null;
  if(robot && robot.child_id){
    try{
      const st=await api('/local/state');
      const k=(st.children||[]).find(c=>c.id===robot.child_id);
      if(k) LC.child={id:k.id, name:String(k['child-first-name']||'').trim()};
    }catch(e){}
  }
  if(mode!=='unpair'){
    try{ LC.view=await api('/local/factory-reset/payload',{auth:false}); }catch(e){}
  }
  lcFill(robot);
  d.showModal();
  $('#lc-confirm').focus();
}

function lcFill(robot){
  const reset=LC.mode!=='unpair', name=(robot&&robot.name)||'Moxie';
  const kid=(LC.child&&LC.child.name)||'';
  const whose=kid ? `${kid}’s` : 'Your child’s';
  $('#lc-title').textContent = LC.mode==='unpair' ? 'Unpair this robot'
    : LC.mode==='reset' ? 'Factory reset this robot' : 'Factory reset code';
  const what=[];
  if(robot){
    what.push(`${name} is removed from your account and this server stops serving it. `
      +'This step erases nothing on the robot itself.');
    what.push('Pairing codes you made before now stop working; you can make a new one any time.');
    what.push(`${whose} profile and what Moxie remembers are kept, unless you choose otherwise below.`);
  } else {
    what.push('This shows the code Moxie scans to reset itself. Nothing on this server '
      +'changes; if this server still serves that robot, revoke it in Robot access.');
  }
  if(reset && LC.view)
    what.push(`${LC.view.effect.text} (${LC.view.effect.basis})`, LC.view.limit);
  $('#lc-what').innerHTML=what.map(t=>`<li>${escapeHtml(t)}</li>`).join('');

  // The erase choice: the existing calls, offered only where they can name the right robot.
  const dev=String((robot&&robot['mqtt-device-id'])||'');
  $('#lc-erase').classList.toggle('hidden', !robot);
  const mem=$('#lc-erase-memory'), prof=$('#lc-erase-child');
  mem.checked=false; prof.checked=false;
  mem.disabled=!dev; prof.disabled=!(robot&&robot.child_id);
  $('#lc-erase-memory-text').textContent =
    `What Moxie remembers${kid?' about '+kid:''}, and the activity history on this server`;
  $('#lc-erase-child-text').textContent=`${whose} profile on this server`;
  $('#lc-erase-note').textContent = dev
    ? 'The same erase as in What Moxie remembers and Insights.'
    : 'This robot’s record does not name its robot on this server, so erase from '
      +'What Moxie remembers and Insights before you unpair.';

  $('#lc-ack-row').classList.toggle('hidden', !reset);
  $('#lc-ack').checked=false;
  $('#lc-confirm-label').textContent = reset ? 'Type RESET to confirm'
    : `Type UNPAIR or the robot’s name (${name}) to confirm`;
  $('#lc-confirm').value='';
  $('#lc-go').textContent = LC.mode==='unpair' ? 'Unpair'
    : LC.mode==='reset' ? 'Unpair and reset' : 'Show the code';
  $('#lc-ask').classList.remove('hidden');
  $('#lc-done').classList.add('hidden');
  $('#lc-code').classList.add('hidden');
  $('#lc-cancel').disabled=false;
  $('#lc-status').textContent = reset && !LC.view
    ? 'Could not load the reset code from this server.' : '';
  lcSync();
}

function lcConfirmed(){
  const v=String($('#lc-confirm').value||'').trim().toLowerCase();
  if(LC.mode==='unpair'){
    const name=String((LC.robot&&LC.robot.name)||'').trim().toLowerCase();
    return v==='unpair' || (!!name && v===name);
  }
  return !!LC.view && v==='reset' && $('#lc-ack').checked;
}
function lcSync(){ const g=$('#lc-go'); if(g) g.disabled=LC.busy || !lcConfirmed(); }

async function runLifecycle(){
  if(LC.busy || !lcConfirmed()) return;
  LC.busy=true; lcSync(); $('#lc-cancel').disabled=true;
  const say=t=>{ $('#lc-status').textContent=t; };
  const stop=msg=>{ say(msg); LC.busy=false; $('#lc-cancel').disabled=false; lcSync(); };
  const robot = LC.mode==='code' ? null : LC.robot;
  const extra=[];
  if(robot && $('#lc-erase-memory').checked){
    const base='/local/robots/'+encodeURIComponent(String(robot['mqtt-device-id']||''));
    say('Erasing what Moxie remembers…');
    try{
      const m=await api(base+'/memory',{method:'DELETE',auth:false});
      const t=await api(base+'/telemetry',{method:'DELETE',auth:false});
      extra.push({key:'erase', text:(m.erased||t.erased)
        ? 'What Moxie remembered and the activity history were erased from this server.'
        : 'There was nothing stored to erase.'});
    }catch(e){ return stop(oops(e,'erase failed')+' Nothing was unpaired; you can try again.'); }
  }
  let res={message:'', details:[], reset:LC.view};
  if(robot){
    say(LC.mode==='reset' ? 'Unpairing and resetting…' : 'Unpairing…');
    try{
      res=await api(`/api/robots/${encodeURIComponent(robot.id)}`
                    +(LC.mode==='reset'?'?rfs=1':''), {method:'DELETE'});
    }catch(e){ return stop(oops(e,'unpair failed')); }
    if($('#lc-erase-child').checked && res.child_id){
      const whose=(LC.child&&LC.child.name) ? `${LC.child.name}’s` : 'Your child’s';
      try{
        await api('/api/children/'+encodeURIComponent(res.child_id),{method:'DELETE'});
        res.details=(res.details||[]).filter(x=>x.key!=='child');
        extra.push({key:'child', text:`${whose} profile was deleted from this server.`});
      }catch(e){
        extra.push({key:'child', text:`${whose} profile could not be deleted `
          +`(${(e&&e.message)||'error'}), so it was kept.`});
      }
    }
  }
  LC.busy=false; say('');
  lcDone(res, extra);
  if(robot) refreshMoxie();
}

function lcDone(res, extra){
  $('#lc-ask').classList.add('hidden');
  $('#lc-done').classList.remove('hidden');
  $('#lc-message').textContent=res.message||'';
  $('#lc-details').innerHTML=[...(res.details||[]), ...extra]
    .map(x=>`<li>${escapeHtml(x.text)}</li>`).join('');
  const code=res.reset;
  $('#lc-code').classList.toggle('hidden', !code);
  if(code){
    $('#lc-qr').src=code.qr_png;
    $('#lc-steps').innerHTML=(code.steps||[]).map(s=>`<li>${escapeHtml(s.text)}`
      +`<span class="lc-basis">${escapeHtml(s.basis)}</span></li>`).join('');
    const fx=code.effect||{};
    $('#lc-effect').innerHTML=`${escapeHtml(fx.text||'')}`
      +`<span class="lc-basis">${escapeHtml(fx.basis||'')}</span>`;
    $('#lc-after').textContent=code.after||'';
    $('#lc-limit').textContent=code.limit||'';
  }
  // The answer replaces the question in place: start reading it from the top, not from
  // wherever the confirm box had scrolled the sheet to.
  $('#lc-sheet').scrollTop=0;
  $('#lc-title').focus();
}

{ const d=$('#lc-sheet');
  if(d) d.addEventListener('cancel', e=>{ if(LC.busy) e.preventDefault(); });
  const c=$('#lc-cancel'); if(c) c.onclick=()=>{ if(!LC.busy) d.close(); };
  const x=$('#lc-close'); if(x) x.onclick=()=>d.close();
  const g=$('#lc-go'); if(g) g.onclick=runLifecycle;
  const i=$('#lc-confirm');
  if(i){ i.oninput=lcSync; i.onkeydown=e=>{ if(e.key==='Enter') runLifecycle(); }; }
  const a=$('#lc-ack'); if(a) a.onchange=lcSync;
  const r=$('#btn-reset-code'); if(r) r.onclick=()=>openLifecycle('code'); }
