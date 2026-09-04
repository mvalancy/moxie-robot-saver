/* cloud.js — the cloud-console mock: reads `fixtures/cloud.json` and renders the five
 * panels (overview, missions, conversations, robot, notifications).
 *
 * Lived inline in `cloud.html` until 2026-09-04 (128 lines); moved out for
 * `script-src 'self'` (see `sim/web/_headers`).
 */
(function(){
  "use strict";
  var TABS = [
    ["overview","Overview"], ["missions","Missions & rewards"],
    ["conversations","Conversations"], ["robot","Robot"], ["notifications","Notifications"]
  ];
  var esc = function(s){ return String(s==null?"":s).replace(/[&<>"]/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); };
  var age = function(y){ try{ return Math.floor((Date.now()-new Date(y).getTime())/3.156e10); }catch(e){ return "?"; } };
  var when = function(iso){ try{ var d=new Date(iso); return d.toLocaleDateString(undefined,{month:"short",day:"numeric"})+
    " "+d.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"}); }catch(e){ return iso; } };
  var incl = function(doc,type){ return (doc.included||[]).filter(function(r){return r.type===type;}); };

  function tabsBar(active){
    var t=document.getElementById("tabs"); t.innerHTML="";
    TABS.forEach(function(p){
      var b=document.createElement("button");
      b.className="tab"+(p[0]===active?" active":""); b.textContent=p[1];
      b.onclick=function(){ show(p[0]); }; t.appendChild(b);
    });
  }
  function show(name){
    tabsBar(name);
    document.querySelectorAll(".panel").forEach(function(el){
      el.classList.toggle("active", el.getAttribute("data-panel")===name); });
    try{ location.hash=name; }catch(e){}
  }

  function render(d){
    var child = incl(d.user,"children")[0] || {attributes:{}};
    var robot = incl(d.user,"robots")[0] || {attributes:{}};
    var setg  = incl(d.user,"robot-setting")[0] || {attributes:{}};
    var ca = child.attributes, ra = robot.attributes, sa = setg.attributes;
    var ins = d.insights||{}, rew=(d.rewards&&d.rewards.data)||{};

    // ---- Overview ----
    var missionsDone = (rew.missions||[]).filter(function(m){return m.status==="completed";}).length;
    document.querySelector('[data-panel="overview"]').innerHTML =
      '<div class="grid">'
      + card("Child", kv("Name", esc(ca.nickname||ca["first-name"]))
          + kv("Age", age(ca.birthday)+" yrs") + kv("Pronoun", esc(ca.pronoun))
          + kv("Content day", "#"+esc(ca["content-day"])))
      + card("Robot", kv("Status", ra.online?'<span class="pill on">online</span>':'<span class="pill off">offline</span>')
          + kv("Firmware", esc(ra["software-version"])) + kv("Endpoint", esc(ra.endpoint))
          + '<div class="kv"><span class="k">Battery</span><span class="v">'+Math.round((ra["battery-level"]||0)*100)
          + '%</span></div><div class="bar"><i style="width:'+Math.round((ra["battery-level"]||0)*100)+'%"></i></div>')
      + card("This week", '<div class="big">'+esc(ins["talk-minutes"])+'<span style="font-size:14px;color:var(--muted)"> min</span></div>'
          + '<div class="note" style="margin-top:2px">'+esc(ins.sessions)+' sessions · week of '+esc(ins["week-of"])+'</div>'
          + '<div style="margin-top:12px">'+ (ins["sel-themes"]||[]).map(function(t){
              return '<div class="theme"><span class="n">'+esc(t.theme)+'</span>'
                + '<div class="bar" style="flex:1"><i style="width:'+Math.round(t.weight*100)+'%"></i></div></div>'; }).join("")
          + '</div>')
      + card("Mood this week", '<div class="mood">'+(ins["mood-trend"]||[]).map(function(m){return '<span>'+esc(m)+'</span>';}).join("")+'</div>'
          + '<div class="note">Daily missions completed: '+missionsDone+'</div>')
      + '</div>'
      + '<p class="note">These summaries are what the real app showed parents — SEL themes and time, not raw transcripts by default.</p>';

    // ---- Missions & rewards ----
    var badges=(rew.badges||[]).map(function(b){
      return '<div class="badge'+(b.earned?" earned":"")+'"><span class="em">'+esc(b.icon)+'</span>'
        + '<div><div>'+esc(b.title)+'</div><div class="s" style="font-size:11px;color:var(--dim);font-family:var(--font-mono)">'
        + (b.earned?("earned "+esc(b["earned-on"])):"not yet")+'</div></div></div>'; }).join("");
    var pillCls={completed:"done",in_progress:"prog",locked:"lock"};
    var missions=(rew.missions||[]).map(function(m){
      return '<div class="mission"><div><div class="t">'+esc(m.title)+'</div>'
        + '<div class="s">'+esc(m.module_id)+'/'+esc(m.content_id)+' · '+esc(m["sel-skill"])+'</div></div>'
        + '<span class="pill '+(pillCls[m.status]||"")+'">'+esc(m.status.replace("_"," "))+'</span></div>'; }).join("");
    document.querySelector('[data-panel="missions"]').innerHTML =
      '<div class="grid">'
      + card("Daily Missions",missions+'<p class="note">MissionConfig{mission_id} · module_id <b>DM</b>. Progress reported as MentorBehavior over the activity log.</p>')
      + card("Badges",badges)
      + '</div>';

    // ---- Conversations ----
    var conv=d.conversation||{turns:[]};
    var turns=(conv.turns||[]).map(function(t){
      var meta = t.mood ? ('<div class="meta">mood: '+esc(t.mood)+(t.gesture?(" · "+esc(t.gesture)):"")+'</div>') : "";
      return '<div class="turn '+(t.speaker==="child"?"child":"moxie")+'">'
        + '<div class="who">'+(t.speaker==="child"?"Alex":"Moxie")+'</div>'
        + '<div class="bub">'+esc(t.text)+'</div>'+meta+'</div>'; }).join("");
    var acts=(d.activity&&d.activity.mentor_behaviors||[]).map(function(a){
      return '<div class="act">'
        + '<div class="act-l"><div class="act-mod">'+esc(a.module_id)+'<span class="act-cid">/'+esc(a.content_id)+'</span></div>'
        + '<div class="act-when">'+when(a.at)+'</div></div>'
        + '<div class="act-r"><span class="act-action">'+esc(a.action).replace(/_/g," ")+'</span><span class="act-min">'+esc(a.minutes)+'m</span></div>'
        + '</div>'; }).join("");
    document.querySelector('[data-panel="conversations"]').innerHTML =
      '<div class="grid">'
      + '<div class="card full"><h3>Recent conversation · '+esc(conv.module_id)+'/'+esc(conv.content_id)+'</h3>'+turns
        + '<p class="note">Delivered via remote-chat (backend=router, RemoteChatRequest/Response). Moxie turns carry mood + gesture, which the SIL/robot render as behavior markup.</p></div>'
      + card("Activity log",acts+'<p class="note">client-service-activity-log · subtopic mentor_behavior.</p>')
      + '</div>';

    // ---- Robot ----
    document.querySelector('[data-panel="robot"]').innerHTML =
      '<div class="grid">'
      + card("Device", kv("Name",esc(ra.name)) + kv("Serial",esc(ra.serial))
          + kv("Firmware",esc(ra["software-version"])) + kv("OTA",esc(d.otaStatus&&d.otaStatus.status))
          + kv("Wi-Fi",esc(ra["wifi-ssid"])) + kv("Last seen",when(ra["last-seen"])))
      + card("Settings", kv("Volume",Math.round((sa.volume||0)*100)+"%")
          + kv("Bedtime",esc(sa.bedtime)) + kv("Wake up",esc(sa.wakeup))
          + kv("Daily limit",esc(sa["daily-time-limit-min"])+" min")
          + kv("Sensitive topics",sa["sensitive-topics-enabled"]?'<span class="pill on">on</span>':'<span class="pill off">off</span>'))
      + card("Controls", '<p class="note" style="margin-top:0">A live console would expose these (they map to real endpoints):</p>'
          + '<div class="kv"><span class="k">Wake up</span><span class="v">POST /robots/{id}/wakeup</span></div>'
          + '<div class="kv"><span class="k">Reboot</span><span class="v">POST /robots/{id}/reboot</span></div>'
          + '<div class="kv"><span class="k">OTA status</span><span class="v">GET /robots/{id}/ota_status</span></div>')
      + '</div>';

    // ---- Notifications ----
    var noti=(d.notifications&&d.notifications.data||[]).map(function(n){
      var a=n.attributes;
      return '<div class="noti'+(a.read?" read":"")+'"><span class="u"></span><div>'
        + '<div class="tt">'+esc(a.title)+'</div><div class="bb">'+esc(a.body)+'</div>'
        + '<div class="at">'+when(a.at)+'</div></div></div>'; }).join("");
    document.querySelector('[data-panel="notifications"]').innerHTML =
      '<div class="notiwrap"><div class="card"><h3>Notifications · '+esc(d.notifications&&d.notifications.meta&&d.notifications.meta.unread)+' unread</h3>'
      + noti + '</div></div>';
  }
  function card(title,body){ return '<div class="card"><h3>'+title+'</h3>'+body+'</div>'; }
  function kv(k,v){ return '<div class="kv"><span class="k">'+k+'</span><span class="v">'+v+'</span></div>'; }

  fetch("fixtures/cloud.json").then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
    .then(function(d){ render(d);
      var h=(location.hash||"").replace("#",""); show(TABS.some(function(t){return t[0]===h;})?h:"overview"); })
    .catch(function(e){ document.getElementById("err").textContent="Could not load fixtures/cloud.json — "+e.message; tabsBar("overview"); });
})();
