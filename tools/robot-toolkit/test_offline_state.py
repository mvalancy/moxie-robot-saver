#!/usr/bin/env python3
"""The on-device brain-state parsers in moxie_toolkit.cloud (embodied.robotbrain.serialized):
the FallbackInfo tree a server pushes via upgrade_fallbacks and its FallbackOptions values,
the CSData resume checkpoint and UserRecommendationData history. See
docs/reverse-engineering/protocol/offline-and-brain-state.md.

    python3 tools/robot-toolkit/test_offline_state.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.robotbrain.serialized import FallbackInfo_pb2 as F  # noqa: E402
    from embodied.robotbrain.serialized import CSData_pb2 as C  # noqa: E402
    from embodied.robotbrain.serialized import UserRecommendationData_pb2 as U  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  offline-state toolkit test skipped — {e}")
    sys.exit(0)

for name, val in (("SILENT", 3), ("LOCAL_ONLY", 4), ("FALLBACKS_NO_REMOTE", 5)):
    ok(getattr(F.NodeFallback, name) == val, f"FallbackOptions.{name} should be {val}")
ok(F.FallbackInfo.DESCRIPTOR.full_name == "embodied.robotbrain.serialized.FallbackInfo",
   "unexpected FallbackInfo full name")

fb = F.FallbackInfo()
mod = fb.modules.add(id="m_game")
mod.node_fallbacks.add(id="node_1", opt=F.NodeFallback.LOCAL_ONLY)
mod.module_default_fallback.id = "node_default"
mod.module_default_fallback.opt = F.NodeFallback.FALLBACKS_NO_REMOTE
mod.content_id_fallbacks.add(id="c_1")
ok(cloud.parse_fallback_info(fb.SerializeToString()).modules[0].node_fallbacks[0].opt
   == F.NodeFallback.LOCAL_ONLY, "parse_fallback_info wrong")

cs = C.CSData(content_day=3, module_id="m_game", content_id="c_1",
              module_started_ts=1_700_000_000, instance_id=2)
ok(cloud.parse_cs_data(cs.SerializeToString()).instance_id == 2, "parse_cs_data wrong")

urd = U.UserRecommendationData()
urd.tag_history.add(key="sel:empathy").value.values.add(id="m_game", value=0.8)
urd.random_tag_state.random_seed = 42
ok(cloud.parse_user_recommendation_data(urd.SerializeToString()).random_tag_state.random_seed == 42,
   "parse_user_recommendation_data wrong")

report("offline-state", "FallbackOptions values + FallbackInfo/CSData/UserRecommendationData "
       "fields and parsers")
