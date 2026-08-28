#!/usr/bin/env python3
"""Generate docs/reverse-engineering/proto-catalog.md from the recovered protos.
Run from tools/robot-toolkit:  python3 gen_catalog.py   (needs protoc + protobuf)."""
import glob, subprocess, tempfile, os, collections
from google.protobuf import descriptor_pb2 as dpb
HERE=os.path.dirname(os.path.abspath(__file__)); PROTO=os.path.join(HERE,"proto")
OUT=os.path.abspath(os.path.join(HERE,"..","..","docs","reverse-engineering","proto-catalog.md"))
FW="v3.6.4-Zephyr / OTA v24.10.803"
def main():
    desc=os.path.join(tempfile.gettempdir(),"catalog.desc")
    protos=glob.glob(f"{PROTO}/**/*.proto",recursive=True)
    subprocess.run(["protoc",f"--proto_path={PROTO}",f"--descriptor_set_out={desc}",*protos],check=True,stderr=subprocess.DEVNULL)
    fds=dpb.FileDescriptorSet(); fds.ParseFromString(open(desc,"rb").read())
    TYPE={1:"double",2:"float",3:"int64",4:"uint64",5:"int32",6:"fixed64",7:"fixed32",8:"bool",9:"string",12:"bytes",13:"uint32",15:"sfixed32",16:"sfixed64",17:"sint32",18:"sint64"}
    LAB={1:"",2:"required ",3:"repeated "}
    ftype=lambda f:(f.type_name.lstrip(".") if f.type in (11,14) else TYPE.get(f.type,f"type{f.type}"))
    files=sorted(fds.file,key=lambda f:f.name); by=collections.defaultdict(list)
    for f in files: by[f.package].append(f)
    st={"m":0,"e":0,"f":0}; body=[]
    def renum(e,ind="",pfx=""):
        st["e"]+=1; body.append(f"{ind}- **enum `{pfx}{e.name}`** — "+", ".join(f"`{v.name}={v.number}`" for v in e.value))
    def rmsg(m,ind="",pfx=""):
        st["m"]+=1; full=f"{pfx}{m.name}"; body.append(f"{ind}- **`{full}`**")
        for f in m.field: st["f"]+=1; body.append(f"{ind}  - `{LAB.get(f.label,'')}{ftype(f)} {f.name} = {f.number}`")
        for e in m.enum_type: renum(e,ind+"  ",full+".")
        for nm in m.nested_type: rmsg(nm,ind+"  ",full+".")
    for pkg in sorted(by):
        body.append(f"\n## `{pkg}`\n")
        for f in by[pkg]:
            body.append(f"\n### `{f.name}`\n")
            for e in f.enum_type: renum(e)
            for m in f.message_type: rmsg(m)
    head=[f"# 📖 Protocol message catalog — every message & enum\n",
      f"> Auto-generated from the **120 recovered `.proto` files** (firmware **{FW}**).",
      "> The browsable index of the on-robot + cloud protocol. Regenerate with `python3 tools/robot-toolkit/gen_catalog.py`.",
      "> Field/enum numbers are wire-compatible with the firmware.\n",
      f"**{st['m']} messages · {st['e']} enums · {st['f']} fields · {len(files)} files.**\n"]
    foot=["\n\n---\n📖 [Reverse-engineering index](README.md) · [recovered-proto/](recovered-proto/) · [protoref tool](../../tools/robot-toolkit/moxie_toolkit/protoref.py)"]
    open(OUT,"w").write("\n".join(head+body+foot))
    print(f"wrote {OUT}: {st['m']} messages, {st['e']} enums, {st['f']} fields")
if __name__=="__main__": main()
