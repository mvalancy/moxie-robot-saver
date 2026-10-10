#!/usr/bin/env python3
"""
Headless runner for the (retired) QR rig; writes tools/qr-rig/findings.md.

- Keeps qr_rig.py alive (headless; relaunches if it dies, clearing the mic first).
- Every few minutes snapshots the rig's per-command statistics.
- Regenerates a findings file beside this script (tools/qr-rig/findings.md): what has
  been shown, and which codes made the robot pause *consistently* (statistically). The
  QR grammar is closed (docs/reverse-engineering/protocol/qr-commands.md): a long pause
  is one of the setup app's dwell timers, not a hidden command. The 2026-08-27 snapshot
  is kept as findings-2026-08-27.md, and docs/debugging/qr-command-findings.md is the
  record of what it meant.
- Commits and pushes the findings file periodically ONLY with --commit (default: off).
- Runs until --until HH:MM (default 07:00), then a final snapshot.

Nothing here names a bench network: --moxie-ip is required, and the rig reads the broker
address from MOXIE_BROKER_HOST (it builds the `om` code from it).

Chrome (the fullscreen display Moxie scans) is launched once and left alone; its page
polls the rig so it survives rig relaunches. Run this detached:
  DISPLAY=:1 MOXIE_BROKER_HOST=<broker-ip> nohup setsid python3 overnight.py --moxie-ip <robot-ip> > overnight.log 2>&1 < /dev/null &
"""
import subprocess, time, json, urllib.request, os, sys, argparse, datetime

RIG_DIR=os.path.dirname(os.path.abspath(__file__))
REPO=os.path.abspath(os.path.join(RIG_DIR,"..",".."))
FINDINGS=os.path.join(RIG_DIR,"findings.md")
BASE="http://127.0.0.1:8091"

def _get(path):
    try:
        with urllib.request.urlopen(BASE+path,timeout=4) as r: return json.load(r)
    except Exception: return None
def rig_up(): return _get("/current") is not None

def clear_mic():
    subprocess.run(["pkill","-9","arecord"],stderr=subprocess.DEVNULL,stdout=subprocess.DEVNULL)
    time.sleep(2)

def launch_rig(moxie_ip):
    clear_mic()
    env={**os.environ,"DISPLAY":os.environ.get("DISPLAY",":1")}
    subprocess.Popen(["python3","qr_rig.py","--no-chrome","--autofuzz","--moxie-ip",moxie_ip],
        cwd=RIG_DIR,env=env,stdin=subprocess.DEVNULL,
        stdout=open(os.path.join(RIG_DIR,"rig.log"),"a"),stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(15):
        time.sleep(1)
        if rig_up(): break

def launch_chrome():
    env={**os.environ,"DISPLAY":os.environ.get("DISPLAY",":1")}
    subprocess.run(["pkill","-9","-f","qrrig-chrome"],stderr=subprocess.DEVNULL,stdout=subprocess.DEVNULL)
    subprocess.run(["rm","-rf","/tmp/qrrig-chrome"])
    time.sleep(1)
    subprocess.Popen(["google-chrome","--kiosk","--start-fullscreen",f"{BASE}/display",
        "--user-data-dir=/tmp/qrrig-chrome","--no-first-run","--no-default-browser-check",
        "--disable-session-crashed-bubble"],env=env,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)

def now(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

def write_findings():
    cur=_get("/current") or {}
    stats=cur.get("stats",{})
    rows=[]
    for lab,st in stats.items():
        sh=st.get("shows",0); rc=st.get("react",0)
        gaps=st.get("gaps",[]); mg=sum(gaps)/len(gaps) if gaps else 0
        rows.append({"label":lab,"shows":sh,"react":rc,
                     "rate":round(rc/sh,2) if sh else 0,"mean_gap":round(mg,2),
                     "max_gap":round(max(gaps),2) if gaps else 0,"payload":st.get("payload","")})
    scanned=len(rows)
    consistent=sorted([r for r in rows if r["shows"]>=3 and r["rate"]>=0.5],
                      key=lambda r:(-r["rate"],-r["mean_gap"]))
    watch=sorted([r for r in rows if r["shows"]>=3 and 0.2<=r["rate"]<0.5],
                 key=lambda r:(-r["rate"],-r["mean_gap"]))[:25]
    allr=sorted(rows,key=lambda r:(-r["rate"],-r["mean_gap"]))
    def tbl(rs):
        out="| command | JSON shape | shows | reacts | rate | mean gap | max gap |\n|---|---|--:|--:|--:|--:|--:|\n"
        for r in rs:
            shape,cmd=(r["label"].split(":",1)+[""])[:2]
            out+=f"| `{cmd}` | {shape} | {r['shows']} | {r['react']} | {r['rate']} | {r['mean_gap']}s | {r['max_gap']}s |\n"
        return out
    md=f"""# QR command rig: findings

*Snapshot written by `tools/qr-rig/overnight.py` at **{now()}**. The QR grammar is closed, read from the
firmware (`docs/reverse-engineering/protocol/qr-commands.md`): a long scan→resume gap is one of the setup
app's dwell timers (2 s diagnostic screen, 20 s Wi-Fi connect, 30 s serial display), not a hidden command.
This file records what was shown and how the robot paced. The 2026-08-27 snapshot is kept as
`findings-2026-08-27.md`; `docs/debugging/qr-command-findings.md` is the record of what it meant.*

Each candidate is a QR the robot scans; the rig listens for its scan beep over a mic and measures the
**scan→resume gap**. Normal re-scan cadence is ~2s; a consistently **longer gap = the robot entered a
wait state** on that code. "One long gap" is noise — only codes that react across **many** randomized
repeats count.

- **Codes scanned at least 3×:** {sum(1 for r in rows if r['shows']>=3)}  ·  distinct scanned: {scanned}
- **Reaction threshold:** gap ≥ 4.5s  ·  **consistent =** ≥3 scans and ≥50% reaction rate

> Context: a pre-801 robot cannot be re-homed by any QR
> ([`live-hardware-debug.md`](../../docs/debugging/live-hardware-debug.md)); on 801+ the validated deck
> (`validated_codes.py`) is what to show.

## 🎯 Consistent reactors (≥3 scans, ≥50% reaction rate)
{tbl(consistent) if consistent else "_none yet — still gathering data_"}

## 👀 Worth watching (partial/intermittent, 20–50% rate)
{tbl(watch) if watch else "_none yet_"}

## 📋 Everything tried (ranked by reaction rate)
{tbl(allr[:120]) if allr else "_no scans recorded yet_"}
"""
    os.makedirs(os.path.dirname(FINDINGS),exist_ok=True)
    open(FINDINGS,"w").write(md)
    return scanned, len(consistent)

def commit(msg):
    # Only behind --commit. Stages the findings file alone and rebase-autostashes before
    # pushing the CURRENT branch, so it coexists with other commits made during the night.
    for args in (["add",FINDINGS],["commit","-m",msg],
                 ["pull","--rebase","--autostash"],["push","origin","HEAD"]):
        subprocess.run(["git","-C",REPO]+args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def main():
    ap=argparse.ArgumentParser(description="Headless runner for the retired QR rig; writes findings.md beside this script.")
    ap.add_argument("--moxie-ip",required=True,help="the robot's address on your network (required: no bench address is hard-coded here)")
    ap.add_argument("--until",default="07:00")
    ap.add_argument("--snap",type=int,default=300); ap.add_argument("--commit-every",type=int,default=3600)
    ap.add_argument("--commit",action="store_true",help="also git-commit findings.md and push the current branch every --commit-every seconds (default: only write the file)")
    ap.add_argument("--no-manage",action="store_true",help="don't launch/keepalive chrome+rig; just snapshot")
    a=ap.parse_args()
    if not a.no_manage and not os.environ.get("MOXIE_BROKER_HOST"):
        sys.exit("overnight.py: set MOXIE_BROKER_HOST to your broker's address; the rig builds the om code from it and no address is hard-coded here")
    hh,mm=map(int,a.until.split(":"))
    end=datetime.datetime.now().replace(hour=hh,minute=mm,second=0,microsecond=0)
    if end<=datetime.datetime.now(): end+=datetime.timedelta(days=1)
    print(f"overnight runner: until {end} snap={a.snap}s commit={'every %ds' % a.commit_every if a.commit else 'off'} no_manage={a.no_manage}",flush=True)
    if not a.no_manage: launch_chrome()
    last_commit=0
    while datetime.datetime.now()<end:
        if not rig_up():
            if a.no_manage: print(f"{now()} rig down (externally managed)",flush=True)
            else: print(f"{now()} rig down -> relaunch",flush=True); launch_rig(a.moxie_ip)
        try:
            scanned,cons=write_findings()
            print(f"{now()} scanned={scanned} consistent={cons}",flush=True)
        except Exception as e:
            print(f"{now()} findings error: {e}",flush=True)
        if a.commit and time.time()-last_commit>a.commit_every:
            commit(f"qr-findings: snapshot {now()}"); last_commit=time.time()
            print(f"{now()} committed",flush=True)
        time.sleep(a.snap)
    write_findings()
    if a.commit: commit(f"qr-findings: final {now()}")
    print(f"{now()} done",flush=True)

if __name__=="__main__": main()
