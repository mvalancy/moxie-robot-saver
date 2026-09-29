#!/usr/bin/env python3
"""Generate docs/reverse-engineering/protocol/proto-catalog.md from the recovered protos.
Run from tools/robot-toolkit:  python3 gen_catalog.py   (needs protoc + protobuf)."""
import argparse, glob, subprocess, tempfile, os, collections
from google.protobuf import descriptor_pb2 as dpb
HERE=os.path.dirname(os.path.abspath(__file__)); PROTO=os.path.join(HERE,"proto")
OUT=os.path.abspath(os.path.join(HERE,"..","..","docs","reverse-engineering","protocol","proto-catalog.md"))
FW="v3.6.4-Zephyr / OTA v24.10.803"
def main():
    ap=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out",default=OUT,help="output path (default: %(default)s)")
    out=ap.parse_args().out
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
    index=[]  # (pkg, files, messages, enums) for the package table at the top
    for pkg in sorted(by):
        m0,e0=st["m"],st["e"]
        body.append(f"\n## `{pkg}`\n")
        for f in by[pkg]:
            body.append(f"\n### `{f.name}`\n")
            for e in f.enum_type: renum(e)
            for m in f.message_type: rmsg(m)
        index.append((pkg,len(by[pkg]),st["m"]-m0,st["e"]-e0))
    slug=lambda t:"".join(c for c in t.lower() if c.isalnum() or c in "_- ").replace(" ","-")
    head=[f"# 📖 Protocol message catalog — every message & enum\n",
      f"Every message, enum and field in the {len(files)} recovered `.proto` files (firmware **{FW}**),",
      "grouped by package, then by file. Field and enum numbers are wire-compatible with the firmware.",
      "Nested types are listed under their parent as `Parent.Child`.\n",
      "> Generated — do not hand-edit. Regenerate with `python3 tools/robot-toolkit/gen_catalog.py`",
      "> (needs `protoc` + `protobuf`). The `.proto` sources live in [`recovered-proto/`](recovered-proto/);",
      "> the narrative docs in this folder explain what each package is for.\n",
      f"**{st['m']} messages · {st['e']} enums · {st['f']} fields · {len(files)} files.**\n",
      "| Package | Files | Messages | Enums |","|---|---:|---:|---:|",
      *[f"| [`{p}`](#{slug(p)}) | {nf} | {nm} | {ne} |" for p,nf,nm,ne in index],""]
    foot=["\n\n---\n📖 [Reverse-engineering index](../README.md) · [recovered-proto/](recovered-proto/) · [protoref tool](../../../tools/robot-toolkit/moxie_toolkit/protoref.py)"]
    open(out,"w").write("\n".join(head+body+foot))
    print(f"wrote {out}: {st['m']} messages, {st['e']} enums, {st['f']} fields")
if __name__=="__main__": main()
