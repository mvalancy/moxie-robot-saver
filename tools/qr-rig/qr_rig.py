#!/usr/bin/env python3
"""Hacker QR fuzzing rig for a Moxie in front of the monitor.
Fullscreen neon display: big SPACE-to-FLAG bar, the command under test in large text,
and a dark-on-light QR (so Moxie scans it) framed bottom-right. Press SPACE (or click)
when Moxie reacts -> records recent command(s) as a "maybe". Phone control at / .
Settings persist to a config file. Run: DISPLAY=:1 python3 qr_rig.py --autofuzz"""
import argparse, http.server, io, json, socket, subprocess, threading, time, os, sys, shutil, random
try: import segno
except ImportError: sys.exit("pip install segno")

_DIAG=["diag","diagnostic","diagnostics","info","status","version","show","qr","state","health","hello","test","id","dump","report"]
_ACCESS=["adb","adbon","enableadb","enable_adb","usbdebug","dev","devmode","developer","debug","eng","engineering","unlock","shell","console","service","factory_mode","maintenance","support"]
_NET=["wifi","endpoint","cloud","server","mqtt","connect","relocate","reconnect","clear_endpoint","reset_endpoint","local","embodied_local"]
_FACTORY=["factory","factorytest","factory_test","factorymode","mfg","manufacturing","provision","provisioning","qa","qc","burnin","selftest","calibrate","calibration","jig","station","testmode","fct","ate","eol","diagmode","enroll","register","activate","cameratest","boot","recovery","fastboot","edl"]
_ENDPOINTS=["EMBODIED_LOCAL","EMBODIED_PRODUCTION","OPEN_MOXIE"]
def candidates():
    cmds=list(dict.fromkeys(_DIAG+_ACCESS+_NET+_FACTORY+[
        "om","moxie","embodied","rightpoint","openmoxie","bo","bowifi","wifiapp",
        "config","configure","setup","onboard","pair","pairing","bind","cert","ca","trust","tls","key","token","auth",
        "log","logs","loglevel","verbose","dumpsys","net","network","ping","dns","proxy","vpn","reset_wifi",
        "sn","serial","model","hw","board","soc","battery","screen","led","face","speaker","mic","camera","cam",
        "sleep","wake","power","shutdown","1","0","1234","0000","admin",
        "mode","get","set","enable","disable","on","off","start","stop","run","scan","echo","noop"]))
    shapes=[("dbg.cmd",lambda c:json.dumps({"debug":{"command":c}})),
            ("dbg.code",lambda c:json.dumps({"debug":{"code":c}})),
            ("top.cmd",lambda c:json.dumps({"command":c})),
            ("raw",lambda c:c)]
    out=[]
    for c in cmds:
        for sn,fn in shapes: out.append((f"{sn}:{c}",fn(c)))
    for e in _ENDPOINTS: out.append((f"ep:{e}",json.dumps({"debug":{"command":"om","endpoint":e}})))
    return out

STATE={"n":1,"label":"test","payload":json.dumps({"debug":{"command":"info"}}),
       "size":26,"right":6,"bottom":2,"fuzz":False,"mode":"brute","moxie":"10.42.0.79",
       "hits":[],"ports":[],"maybes":[],"recent":[],"dwell":9,"rms":0,"gap":0,"paced":True,"stats":{},"react_thresh":4.5}
LOCK=threading.Lock()
AUDIO={"rms":0.0,"thresh":4000.0,"last":0.0,"events":[]}
CFG="/tmp/claude-1000/-home-scubasonar-Code-moxie-robot/fda51c5f-7493-43cb-a455-9b123d95a8e6/scratchpad/qr_rig_cfg.json"
TIMELINE="/tmp/claude-1000/-home-scubasonar-Code-moxie-robot/fda51c5f-7493-43cb-a455-9b123d95a8e6/scratchpad/qr_timeline.txt"
def load_cfg():
    try:
        c=json.load(open(CFG))
        for k in ("size","right","bottom","dwell"):
            if k in c: STATE[k]=c[k]
    except Exception: pass
def save_cfg():
    try: json.dump({k:STATE[k] for k in ("size","right","bottom","dwell")},open(CFG,"w"))
    except Exception: pass
def qr_png(p):
    b=io.BytesIO(); segno.make(p or " ",error="m").save(b,kind="png",scale=14,border=2,dark="#0a0a0a",light="#f2fff6"); return b.getvalue()

DISPLAY=r"""<!doctype html><html><head><meta charset=utf-8><style>
@keyframes fl{0%,100%{opacity:1}50%{opacity:.8}}
html,body{margin:0;height:100%;width:100%;background:#000;overflow:hidden;font-family:'Courier New',monospace;color:#39ff14;cursor:none}
#flag{position:fixed;top:0;left:0;width:100vw;height:30vh;border:0;background:#020;color:#39ff14;
 font:800 4.5vw 'Courier New',monospace;text-shadow:0 0 14px #39ff14;border-bottom:3px solid #39ff14;box-shadow:inset 0 0 50px #060}
#flag:active{background:#062;color:#dfffdf}
#cur{position:fixed;top:32vh;left:0;width:100vw;text-align:center;font-size:5vw;font-weight:800;text-shadow:0 0 18px #39ff14;animation:fl 2s infinite;word-break:break-all;padding:0 2vw;box-sizing:border-box}
#sub{position:fixed;top:44vh;left:0;width:100vw;text-align:center;font-size:2.2vw;color:#0b9}
#qrbox{position:fixed;background:#f2fff6;padding:1.2vh;border:3px solid #39ff14;box-shadow:0 0 45px #0f0,0 0 90px #060}
#qr{display:block;image-rendering:pixelated;width:100%;height:100%}</style></head>
<body><button id=flag>&#128993; FLAG &mdash; hit SPACE when Moxie reacts</button>
<div id=cur></div><div id=sub></div><div id=qrbox><img id=qr src="/qr.png?v=0"></div>
<script>let c=-1;const fb=document.getElementById('flag');
async function flag(){let r=await(await fetch('/mark')).json();fb.textContent='✔ FLAGGED: '+r.labels.join(', ');fb.style.background='#2e7d32';
 setTimeout(()=>{fb.innerHTML='&#128993; FLAG &mdash; hit SPACE when Moxie reacts';fb.style.background='#020';},1400);}
fb.onclick=flag;document.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();flag();}});
async function t(){try{let r=await(await fetch('/current')).json();let box=document.getElementById('qrbox');
 let vh=innerHeight/100,vw=innerWidth/100;box.style.height=(r.size*vh)+'px';box.style.width=(r.size*vh)+'px';box.style.right=(r.right*vw)+'px';box.style.bottom=(r.bottom*vh)+'px';
 document.getElementById('cur').textContent='> '+r.label;document.getElementById('sub').textContent='['+r.mode+']  '+r.maybes.length+' maybes  ·  mic rms '+r.rms+'  last gap '+r.gap+'s  ·  moxie '+JSON.stringify(r.ports);
 if(r.n!==c){c=r.n;document.getElementById('qr').src='/qr.png?v='+r.n;}}catch(e){}setTimeout(t,200);}t();
</script></body></html>"""

CONTROL=r"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Moxie QR Rig</title><style>
body{margin:0;font:16px 'Courier New',monospace;background:#000;color:#39ff14;padding:14px}
h2{text-shadow:0 0 10px #39ff14}label{display:block;margin:10px 0 3px}input[type=range]{width:100%}
button{font:15px 'Courier New',monospace;padding:12px 14px;margin:5px 4px 5px 0;border:1px solid #39ff14;border-radius:8px;background:#020;color:#39ff14}
button.p{background:#052}button.r{background:#200;border-color:#f66;color:#f88}button.g{background:#031}
select{width:100%;padding:10px;border-radius:8px;background:#010;color:#39ff14;border:1px solid #39ff14}
#status{margin-top:12px;padding:12px;background:#010;border:1px solid #063;border-radius:10px;white-space:pre-wrap;font-size:13px}</style></head>
<body><h2>&#128993; MOXIE QR RIG</h2>
<label>QR size <span id=sv></span>vh</label><input id=size type=range min=12 max=90 value=26>
<label>from right <span id=rv></span>vw</label><input id=right type=range min=0 max=70 value=6>
<label>from bottom <span id=bv></span>vh</label><input id=bottom type=range min=0 max=50 value=2>
<label>seconds/QR <span id=dv></span></label><input id=dwell type=range min=3 max=20 value=9>
<div><button class=p id=fz onclick=tf()>&#9654; start brute-force</button><button class=r onclick=flag()>&#128993; flag maybe</button></div>
<div><button class=g onclick=tr()>&#128260; retest maybes</button><button onclick=clr()>clear maybes</button></div>
<label>show one QR</label><select id=pick></select>
<div><button onclick=sp()>show selected</button><button onclick=so()>show "om"</button></div>
<div id=status>...</div>
<script>const g=id=>document.getElementById(id);
async function set(o){await fetch('/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});}
function upd(){g('sv').textContent=g('size').value;g('rv').textContent=g('right').value;g('bv').textContent=g('bottom').value;g('dv').textContent=g('dwell').value;
 set({size:+g('size').value,right:+g('right').value,bottom:+g('bottom').value,dwell:+g('dwell').value});}
['size','right','bottom','dwell'].forEach(i=>g(i).oninput=upd);
let fuzz=false;async function tf(){fuzz=!fuzz;await set({fuzz,mode:'brute'});g('fz').innerHTML=fuzz?'&#9632; stop':'&#9654; start brute-force';}
async function tr(){await set({fuzz:true,mode:'retest'});}
async function flag(){await fetch('/mark');}
async function clr(){await set({clear_maybes:true});}
async function sp(){await set({payload:g('pick').value,fuzz:false});}
async function so(){await set({om:true,fuzz:false});}
async function poll(){try{let s=await(await fetch('/current')).json();
 g('status').textContent='mode:'+s.mode+'  showing:'+s.label+'\nports:'+JSON.stringify(s.ports)+' hits:'+JSON.stringify(s.hits)+'\n\nMAYBES('+s.maybes.length+'):\n'+s.maybes.map(m=>' > '+m.label).join('\n');
 }catch(e){}setTimeout(poll,1500);}
(async()=>{let o='';for(const[l,p]of await(await fetch('/cands')).json())o+="<option value='"+p.replace(/'/g,'&#39;')+"'>"+l+"</option>";g('pick').innerHTML=o;})();
upd();poll();</script></body></html>"""

PROBE=[5555,5037,22,8022,23,7777,4444,5900,8080,9999]
def probe(ip):
    o=[]
    for p in PROBE:
        s=socket.socket();s.settimeout(0.4)
        if s.connect_ex((ip,p))==0:o.append(p)
        s.close()
    return o

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def _s(self,code,ct,b):
        self.send_response(code);self.send_header("Content-Type",ct);self.send_header("Cache-Control","no-store");self.end_headers()
        self.wfile.write(b if isinstance(b,bytes) else b.encode())
    def do_GET(self):
        p=self.path
        if p.startswith("/qr.png"):
            with LOCK: pay=STATE["payload"]
            self._s(200,"image/png",qr_png(pay));return
        if p.startswith("/current"):
            with LOCK: self._s(200,"application/json",json.dumps(STATE));return
        if p.startswith("/mark"):
            with LOCK:
                rec=list(STATE.get("recent",[]))[:3]
                for m in rec:
                    if m not in STATE["maybes"]: STATE["maybes"].append(m)
                labels=[m["label"] for m in rec]
            print(f"FLAG -> {labels}",flush=True)
            try: open(TIMELINE,"a").write(f"{time.time():.0f} {time.strftime('%H:%M:%S')} *** FLAG {labels} ***\n")
            except: pass
            self._s(200,"application/json",json.dumps({"ok":True,"labels":labels}));return
        if p.startswith("/stats"):
            with LOCK:
                rows=[]
                for lab,st in STATE["stats"].items():
                    sh=st["shows"];rate=st["react"]/sh if sh else 0
                    mg=sum(st["gaps"])/len(st["gaps"]) if st["gaps"] else 0
                    rows.append({"label":lab,"shows":sh,"react":st["react"],"rate":round(rate,2),"mean_gap":round(mg,2)})
                rows.sort(key=lambda r:(-r["rate"],-r["mean_gap"]))
                out=[r for r in rows if r["shows"]>=3 and r["rate"]>=0.5][:40]
            self._s(200,"application/json",json.dumps(out));return
        if p.startswith("/cands"): self._s(200,"application/json",json.dumps(candidates()));return
        if p.startswith("/display"): self._s(200,"text/html",DISPLAY);return
        self._s(200,"text/html",CONTROL)
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0)); d={}
        try: d=json.loads(self.rfile.read(n) or b"{}")
        except: pass
        with LOCK:
            changed=False
            for k in ("size","right","bottom","dwell"):
                if k in d: STATE[k]=float(d[k]);changed=True
            if changed: save_cfg()
            if d.get("clear_maybes"): STATE["maybes"]=[]
            if "mode" in d: STATE["mode"]=d["mode"]
            if d.get("om"):
                sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","pairing"))
                import moxie_endpoint_qr as meq
                h=os.environ.get("MOXIE_BROKER_HOST","10.42.0.1")
                STATE["payload"]=meq.build_endpoint_qr(h);STATE["label"]=f"om->{h}";STATE["n"]+=1
            elif "payload" in d: STATE["payload"]=d["payload"];STATE["label"]="manual";STATE["n"]+=1
            if "fuzz" in d: STATE["fuzz"]=bool(d["fuzz"])
        self._s(200,"application/json",b'{"ok":true}')

def audio_thread():
    import numpy as np
    chunk=640; med=[]; below=True
    while True:                                  # outer: restart arecord if it dies
        try:
            pr=subprocess.Popen(["arecord","-q","-f","S16_LE","-r","16000","-c","1","-t","raw"],stdout=subprocess.PIPE)
        except Exception as e:
            print("audio spawn fail:",e,flush=True); time.sleep(2); continue
        while True:
            b=pr.stdout.read(chunk)
            if not b or len(b)<chunk:
                try: pr.kill()
                except Exception: pass
                print("arecord ended, restarting",flush=True); time.sleep(1); break
            a=np.frombuffer(b,dtype=np.int16).astype(np.float32)
            rms=float(np.sqrt((a*a).mean())) if len(a) else 0.0
            med.append(rms); med=med[-300:]
            thr=max(2800.0,(sorted(med)[len(med)//2])*3.5)
            AUDIO["rms"]=rms; AUDIO["thresh"]=thr
            now=time.time()
            if rms>thr and below:
                below=False; AUDIO["last"]=now; AUDIO["events"]=([now]+AUDIO["events"])[:40]
            elif rms<thr*0.4:
                below=True
            STATE["rms"]=int(rms)

def fuzz_thread():
    allc=candidates();i=0;base=None;order=list(range(len(allc)));random.shuffle(order)
    while True:
        with LOCK:
            on=STATE["fuzz"];ip=STATE["moxie"];mode=STATE["mode"];dwell=STATE["dwell"]
            seq=[(m["label"],m["payload"]) for m in STATE["maybes"]] if mode=="retest" else [allc[order[k]] for k in range(len(order))]
        if not on or not seq: time.sleep(0.5);continue
        if base is None: base=probe(ip)
        if i>0 and i%len(seq)==0 and mode!="retest": random.shuffle(order)
        label,payload=seq[i%len(seq)];i+=1
        with LOCK:
            STATE["payload"]=payload;STATE["label"]=("RETEST " if mode=="retest" else "")+label;STATE["n"]+=1
            STATE["recent"]=([{"t":time.strftime("%H:%M:%S"),"label":label,"payload":payload}]+STATE["recent"])[:5]
        try: open(TIMELINE,"a").write(f"{time.time():.0f} {time.strftime('%H:%M:%S')} {label}\n")
        except: pass
        show=time.time(); scan_t=None; resume_t=None
        with LOCK: paced=STATE.get("paced",True)
        while True:
            time.sleep(0.08)
            with LOCK:
                if not STATE["fuzz"]: break
            if not paced:
                if time.time()-show>=dwell: break
                continue
            evs=sorted(e for e in AUDIO["events"] if e>show+0.05)
            if evs and scan_t is None: scan_t=evs[0]
            if scan_t and len(evs)>=2 and resume_t is None: resume_t=evs[1]
            now=time.time()
            if now-show<3.0: continue                                    # min stable hold so Moxie locks+scans
            if resume_t and now-max(AUDIO["last"],resume_t)>0.6: break   # got 2 beeps (gap measured) + quiet
            if scan_t and resume_t is None and now-scan_t>7: break       # scanned once, no re-beep in 7s = reaction
            if scan_t is None and now-show>10: break                     # gave 10s, never scanned -> skip
            if now-show>18: break                                        # hard cap
        gap=(resume_t-scan_t) if (scan_t and resume_t) else (time.time()-show if scan_t else 0)
        ports=probe(ip);new=[x for x in ports if x not in (base or [])]
        with LOCK:
            STATE["ports"]=ports; STATE["gap"]=round(gap,2)
            rt=STATE["react_thresh"]
            if gap>0.3:   # only count shows Moxie actually scanned
                st=STATE["stats"].setdefault(label,{"shows":0,"gaps":[],"react":0,"payload":payload})
                st["shows"]+=1; st["gaps"]=(st["gaps"]+[round(gap,2)])[-30:]
                if gap>=rt: st["react"]+=1
            if new:
                STATE["hits"].append({"label":label,"new_ports":new,"payload":payload})
                print(f"HIT {label} new_ports={new}",flush=True)
            if gap>=4.5:   # long scan->resume delay = a REACTION -> auto-flag
                m={"t":time.strftime("%H:%M:%S"),"label":label,"payload":payload,"gap":round(gap,2)}
                if not any(x["label"]==label for x in STATE["maybes"]): STATE["maybes"].append(m)
                print(f"AUTO-FLAG (gap={gap:.1f}s) {label}",flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--port",type=int,default=8091)
    ap.add_argument("--moxie-ip",default="10.42.0.79");ap.add_argument("--no-chrome",action="store_true");ap.add_argument("--autofuzz",action="store_true")
    a=ap.parse_args();STATE["moxie"]=a.moxie_ip
    if a.autofuzz: STATE["fuzz"]=True
    load_cfg()
    shutil.rmtree("/tmp/qrrig-chrome",ignore_errors=True)
    s=http.server.HTTPServer(("0.0.0.0",a.port),H)
    threading.Thread(target=s.serve_forever,daemon=True).start()
    threading.Thread(target=audio_thread,daemon=True).start()
    threading.Thread(target=fuzz_thread,daemon=True).start()
    print(f"control http://<host>:{a.port}/   display /display",flush=True)
    if not a.no_chrome:
        subprocess.Popen(["google-chrome","--kiosk","--start-fullscreen",f"http://127.0.0.1:{a.port}/display",
            "--user-data-dir=/tmp/qrrig-chrome","--no-first-run","--no-default-browser-check","--disable-session-crashed-bubble"],
            env={**os.environ,"DISPLAY":os.environ.get("DISPLAY",":1")},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    while True: time.sleep(3600)
if __name__=="__main__": main()
