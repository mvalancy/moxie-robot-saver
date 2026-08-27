"""
protoref — query the recovered Moxie protocol (120 .proto files, ~360 messages) without opening files.

    python -m moxie_toolkit.protoref QRCommand          # show a message's fields
    python -m moxie_toolkit.protoref IOTEndpoint         # show an enum's values
    python -m moxie_toolkit.protoref --grep endpoint     # find messages/enums/fields matching a term
    python -m moxie_toolkit.protoref --list              # list every message, grouped by package

Builds a descriptor set from tools/robot-toolkit/proto via protoc (or reuses a cached one).
"""
import sys, os, glob, subprocess, tempfile
from google.protobuf import descriptor_pb2 as dpb

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO_DIR = os.path.join(os.path.dirname(HERE), "proto")

def _load():
    out = os.path.join(tempfile.gettempdir(), "moxie_protoref.desc")
    protos = glob.glob(os.path.join(PROTO_DIR, "**", "*.proto"), recursive=True)
    subprocess.run(["protoc", f"--proto_path={PROTO_DIR}", f"--descriptor_set_out={out}", *protos],
                   check=True, stderr=subprocess.DEVNULL)
    fds = dpb.FileDescriptorSet(); fds.ParseFromString(open(out, "rb").read())
    return fds

TYPE = {1:"double",2:"float",3:"int64",4:"uint64",5:"int32",6:"fixed64",7:"fixed32",8:"bool",
        9:"string",12:"bytes",13:"uint32",15:"sfixed32",16:"sfixed64",17:"sint32",18:"sint64"}
def _ftype(f):
    if f.type in (11,14): return f.type_name.lstrip(".")
    return TYPE.get(f.type, f"type{f.type}")

def _index(fds):
    msgs, enums = {}, {}
    for f in fds.file:
        def walk(prefix, ms, es):
            for e in es: enums[f"{prefix}.{e.name}"] = (f.name, e)
            for m in ms:
                fn = f"{prefix}.{m.name}"; msgs[fn] = (f.name, m)
                walk(fn, m.nested_type, m.enum_type)
        walk(f.package, f.message_type, f.enum_type)
    return msgs, enums

def _show_msg(full, file, m):
    lab = {1:"optional",2:"required",3:"repeated"}
    print(f"message {full}   ({file})")
    for f in m.field:
        print(f"    {lab.get(f.label,''):8} {_ftype(f):40} {f.name} = {f.number}")
    for e in m.enum_type:
        print(f"    enum {e.name} {{ {', '.join(v.name+'='+str(v.number) for v in e.value)} }}")

def _show_enum(full, file, e):
    print(f"enum {full}   ({file})")
    for v in e.value: print(f"    {v.name} = {v.number}")

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    fds = _load(); msgs, enums = _index(fds)
    if not argv or argv[0] in ("--list", "-l"):
        by_file = {}
        for full,(file,_) in sorted(msgs.items()): by_file.setdefault(file, []).append(full)
        for file in sorted(by_file):
            print(f"\n# {file}")
            for full in by_file[file]: print(f"  {full}")
        print(f"\n{len(msgs)} messages, {len(enums)} enums, {len(fds.file)} files")
        return 0
    if argv[0] in ("--grep","-g") and len(argv) > 1:
        term = argv[1].lower(); hits = 0
        for full,(file,m) in sorted(msgs.items()):
            fieldhits = [f.name for f in m.field if term in f.name.lower()]
            if term in full.lower() or fieldhits:
                print(f"message {full}" + (f"   (fields: {', '.join(fieldhits)})" if fieldhits else "")); hits+=1
        for full,(file,e) in sorted(enums.items()):
            valhits = [v.name for v in e.value if term in v.name.lower()]
            if term in full.lower() or valhits:
                print(f"enum    {full}" + (f"   (values: {', '.join(valhits)})" if valhits else "")); hits+=1
        print(f"\n{hits} matches for '{argv[1]}'")
        return 0
    # exact-or-suffix lookup of a message/enum name
    name = argv[0]
    for full,(file,m) in msgs.items():
        if full == name or full.endswith("."+name): _show_msg(full, file, m); return 0
    for full,(file,e) in enums.items():
        if full == name or full.endswith("."+name): _show_enum(full, file, e); return 0
    print(f"'{name}' not found. Try --grep {name} or --list."); return 1

if __name__ == "__main__":
    sys.exit(main())
