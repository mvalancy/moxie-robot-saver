"""
Cross-validate the recovered .proto schemas against another independently-derived set (OpenMoxie's
compiled _pb2 modules). Agreement on message/field/enum numbers is strong evidence the recovered
protocol is correct — the schema-level analogue of the QR byte-parity check in validate_qr.py.

Usage:
    python -m moxie_toolkit.validate_protos [path/to/openmoxie/site/hive/mqtt/protos]

Compares only the files present in BOTH sets (OpenMoxie ships a subset). Compares raw
FileDescriptorProtos so the two never share a descriptor pool. Exit 0 on full agreement.
"""
import sys, os, base64, subprocess, glob, importlib.util
from google.protobuf import descriptor_pb2 as dpb

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO_DIR = os.path.join(os.path.dirname(HERE), "proto")

def _mine_fdps():
    import tempfile
    out = os.path.join(tempfile.gettempdir(), "moxie_mine.desc")
    protos = glob.glob(os.path.join(PROTO_DIR, "**", "*.proto"), recursive=True)
    subprocess.run(["protoc", f"--proto_path={PROTO_DIR}", f"--descriptor_set_out={out}", *protos],
                   check=True, stderr=subprocess.DEVNULL)
    fds = dpb.FileDescriptorSet(); fds.ParseFromString(open(out, "rb").read())
    return {f.name: f for f in fds.file}

def _om_fdps(om_dir):
    # load each OpenMoxie _pb2 in this process is fine as long as we don't also load mine's _pb2
    found = {}
    sys.path.insert(0, om_dir)
    for path in glob.glob(os.path.join(om_dir, "**", "*_pb2.py"), recursive=True):
        rel = os.path.relpath(path, om_dir)
        spec = importlib.util.spec_from_file_location(rel.replace("/", "_"), path)
        m = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(m)
            found[m.DESCRIPTOR.name] = dpb.FileDescriptorProto.FromString(m.DESCRIPTOR.serialized_pb)
        except Exception:
            pass
    return found

def _msgmap(fdp):
    out = {}
    def walk(prefix, msgs):
        for m in msgs:
            fn = f"{prefix}.{m.name}"; out[fn] = {f.name: f.number for f in m.field}
            walk(fn, m.nested_type)
    walk(fdp.package, fdp.message_type); return out

def _enummap(fdp):
    return {f"{fdp.package}.{e.name}": {v.name: v.number for v in e.value} for e in fdp.enum_type}

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    om_dir = argv[0] if argv else os.path.join(
        os.path.dirname(HERE), "..", "..", "..", "work", "openmoxie", "site", "hive", "mqtt", "protos")
    om_dir = os.path.abspath(om_dir)
    if not os.path.isdir(om_dir):
        print(f"OpenMoxie protos not found at {om_dir}; pass the path as an argument. Skipping.")
        return 0
    mine = _mine_fdps(); om = _om_fdps(om_dir)
    tm = tf = te = tev = diffs = missing = ediff = 0
    for name, ofdp in sorted(om.items()):
        if name not in mine:
            print(f"[file only in OpenMoxie] {name}"); continue
        om_m, my_m = _msgmap(ofdp), _msgmap(mine[name])
        for msg, of in om_m.items():
            tm += 1
            if msg not in my_m:
                print(f"  [MSG MISSING] {msg}"); missing += 1; continue
            for fn, num in of.items():
                tf += 1
                if my_m[msg].get(fn) != num:
                    print(f"  [FIELD DIFF] {msg}.{fn}: OM={num} mine={my_m[msg].get(fn)}"); diffs += 1
        om_e, my_e = _enummap(ofdp), _enummap(mine[name])
        for en, ov in om_e.items():
            te += 1
            for vn, num in ov.items():
                tev += 1
                if my_e.get(en, {}).get(vn) != num:
                    print(f"  [ENUM DIFF] {en}.{vn}: OM={num} mine={my_e.get(en,{}).get(vn)}"); ediff += 1
    print(f"\nCross-validated {tm} messages / {tf} fields / {te} enums / {tev} enum values against OpenMoxie.")
    ok = diffs == missing == ediff == 0
    print("RESULT:", "PERFECT MATCH — recovered schema agrees with OpenMoxie's independent set ✅" if ok
          else f"{diffs} field / {missing} msg / {ediff} enum-value differences")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
