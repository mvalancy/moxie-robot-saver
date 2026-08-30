#!/usr/bin/env python3
"""Round-trip test for the SEL taxonomy parsers in moxie_toolkit.cloud
(embodied.robotbrain.tags / ModuleTag). Builds a SELTagInfo curriculum (a Pillar→Skill
→Goal→Level chain with weighted edges) and a ModuleTagData tagging a module with SEL
goals, serializes + re-parses them — the tagging contract a revival server ships so the
recommender can rank its content. See
docs/reverse-engineering/content-and-conversation.md (The SEL taxonomy structure).

    python3 tools/robot-toolkit/test_sel_taxonomy.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.robotbrain import Tags_pb2 as T  # noqa: E402
    from embodied.robotbrain import ModuleTag_pb2 as M  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  SEL-taxonomy toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

# a four-level chain: Pillar 'Emotion' -> Skill 'Awareness' -> Goal 'Name feelings' -> Level 'L1'
sti = T.SELTagInfo()
sti.allPillars.add(uuid="p1", name="Emotion")
sti.allSkills.add(uuid="s1", name="Awareness")
sti.allGoals.add(uuid="g1", name="Name feelings")
sti.allLevels.add(uuid="l1", name="L1")
e1 = sti.pillarsToSkills.add(); e1.parentUUID = "p1"; e1.childUUID = "s1"; e1.weighting = 0.8
e2 = sti.skillsToGoals.add(); e2.parentUUID = "s1"; e2.childUUID = "g1"; e2.weighting = 0.6
e3 = sti.goalsToLevels.add(); e3.parentUUID = "g1"; e3.childUUID = "l1"; e3.weighting = 1.0

rt = cloud.parse_sel_tag_info(sti.SerializeToString())
ok(rt.allPillars[0].name == "Emotion" and rt.allGoals[0].name == "Name feelings", "tag levels lost")
ok(len(rt.pillarsToSkills) == 1 and rt.pillarsToSkills[0].parentUUID == "p1"
   and abs(rt.pillarsToSkills[0].weighting - 0.8) < 1e-6, "pillar→skill edge lost")
ok(rt.skillsToGoals[0].childUUID == "g1" and rt.goalsToLevels[0].childUUID == "l1",
   "skill→goal / goal→level edges lost")
# the four categories are all distinct repeated fields
ok(len(rt.allPillars) == 1 and len(rt.allSkills) == 1 and len(rt.allGoals) == 1 and len(rt.allLevels) == 1,
   "the four taxonomy categories must be independent")

# a module tagged into the taxonomy
mti = M.ModuleTagInfo()
md = mti.module_tags.add()
md._module_id = "m_feelings"; md._module_name = "Feelings game"; md._does_report_completion = True
gl = md._sel_tags.add(); gl.goal = "g1"; gl.level = "l1"
ct = md._content_tags.add(); ct.tag_uuid = "t_topic"; ct.source_uuid = "m_feelings"

rmt = cloud.parse_module_tag_info(mti.SerializeToString())
d = rmt.module_tags[0]
ok(d._module_id == "m_feelings" and d._does_report_completion is True, "module id/completion lost")
ok(d._sel_tags[0].goal == "g1" and d._sel_tags[0].level == "l1", "module SEL goal-at-level lost")
ok(d._content_tags[0].tag_uuid == "t_topic", "module content tag lost")

if fails:
    print("❌ SEL-taxonomy toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ SEL-taxonomy toolkit test OK — SELTagInfo (Pillars→Skills→Goals→Levels + weighted edges) + "
      "ModuleTagData (module → SEL goals + content tags) round-trip through embodied.robotbrain.tags")
