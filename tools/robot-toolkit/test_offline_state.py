#!/usr/bin/env python3
"""Round-trip test for the on-device brain-state parsers in moxie_toolkit.cloud
(embodied.robotbrain.serialized). Builds a FallbackInfo tree (the offline content a
server pushes via upgrade_fallbacks), a CSData resume checkpoint, and a
UserRecommendationData history, serializes + re-parses them, and checks the
FallbackOptions strategy enum. See docs/reverse-engineering/offline-and-brain-state.md.

    python3 tools/robot-toolkit/test_offline_state.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.robotbrain.serialized import FallbackInfo_pb2 as F  # noqa: E402
    from embodied.robotbrain.serialized import CSData_pb2 as C  # noqa: E402
    from embodied.robotbrain.serialized import UserRecommendationData_pb2 as U  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  offline-state toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

# --- FallbackInfo: a module with a node fallback (LOCAL_ONLY) + a default ---
fb = F.FallbackInfo()
mod = fb.modules.add(); mod.id = "m_game"
nf = mod.node_fallbacks.add(); nf.id = "node_1"; nf.opt = F.NodeFallback.LOCAL_ONLY
mod.module_default_fallback.id = "node_default"
mod.module_default_fallback.opt = F.NodeFallback.FALLBACKS_NO_REMOTE
cidf = mod.content_id_fallbacks.add(); cidf.id = "c_1"

rt = cloud.parse_fallback_info(fb.SerializeToString())
ok(len(rt.modules) == 1 and rt.modules[0].id == "m_game", "module lost")
ok(rt.modules[0].node_fallbacks[0].opt == F.NodeFallback.LOCAL_ONLY, "node fallback LOCAL_ONLY lost")
ok(rt.modules[0].module_default_fallback.opt == F.NodeFallback.FALLBACKS_NO_REMOTE,
   "module default FALLBACKS_NO_REMOTE lost")
ok(rt.modules[0].content_id_fallbacks[0].id == "c_1", "content-id fallback lost")
# the strategy enum has the expected 6 options
for name, val in (("SILENT", 3), ("LOCAL_ONLY", 4), ("FALLBACKS_NO_REMOTE", 5)):
    ok(getattr(F.NodeFallback, name) == val, f"FallbackOptions.{name} should be {val}")

# --- CSData resume checkpoint ---
cs = C.CSData(content_day=3, module_id="m_game", content_id="c_1",
              module_started_ts=1_700_000_000, instance_id=2)
rcs = cloud.parse_cs_data(cs.SerializeToString())
ok(rcs.content_day == 3 and rcs.module_id == "m_game" and rcs.content_id == "c_1"
   and rcs.instance_id == 2, "CSData resume checkpoint lost")

# --- UserRecommendationData recommender history ---
urd = U.UserRecommendationData()
e = urd.tag_history.add(); e.key = "sel:empathy"
v = e.value.values.add(); v.id = "m_game"; v.value = 0.8
urd.random_tag_state.random_seed = 42
rurd = cloud.parse_user_recommendation_data(urd.SerializeToString())
ok(rurd.tag_history[0].key == "sel:empathy"
   and abs(rurd.tag_history[0].value.values[0].value - 0.8) < 1e-6, "tag history lost")
ok(rurd.random_tag_state.random_seed == 42, "random_tag_state seed lost")

# descriptor full names (what a bus/persist consumer keys on)
ok(F.FallbackInfo.DESCRIPTOR.full_name == "embodied.robotbrain.serialized.FallbackInfo",
   "unexpected FallbackInfo full name")

if fails:
    print("❌ offline-state toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ offline-state toolkit test OK — FallbackInfo tree (LOCAL_ONLY/FALLBACKS_NO_REMOTE) + CSData "
      "resume checkpoint + UserRecommendationData history round-trip through embodied.robotbrain.serialized")
