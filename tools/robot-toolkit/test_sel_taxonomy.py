#!/usr/bin/env python3
"""The SEL taxonomy parsers in moxie_toolkit.cloud (embodied.robotbrain Tags / ModuleTag): a
SELTagInfo curriculum (Pillar→Skill→Goal→Level with weighted edges) and a ModuleTagData
tagging a module with SEL goals — the contract a revival server ships so the recommender can
rank its content. See docs/reverse-engineering/runtime/content-and-conversation.md (The SEL
taxonomy structure).

    python3 tools/robot-toolkit/test_sel_taxonomy.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.robotbrain import Tags_pb2 as T  # noqa: E402
    from embodied.robotbrain import ModuleTag_pb2 as M  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  SEL-taxonomy toolkit test skipped — {e}")
    sys.exit(0)

sti = T.SELTagInfo()
sti.allPillars.add(uuid="p1", name="Emotion")
sti.allSkills.add(uuid="s1", name="Awareness")
sti.allGoals.add(uuid="g1", name="Name feelings")
sti.allLevels.add(uuid="l1", name="L1")
sti.pillarsToSkills.add(parentUUID="p1", childUUID="s1", weighting=0.8)
sti.skillsToGoals.add(parentUUID="s1", childUUID="g1", weighting=0.6)
sti.goalsToLevels.add(parentUUID="g1", childUUID="l1", weighting=1.0)
ok(cloud.parse_sel_tag_info(sti.SerializeToString()).allGoals[0].name == "Name feelings",
   "parse_sel_tag_info wrong")

mti = M.ModuleTagInfo()
md = mti.module_tags.add(_module_id="m_feelings", _module_name="Feelings game",
                         _does_report_completion=True)
md._sel_tags.add(goal="g1", level="l1")
md._content_tags.add(tag_uuid="t_topic", source_uuid="m_feelings")
ok(cloud.parse_module_tag_info(mti.SerializeToString()).module_tags[0]._sel_tags[0].level == "l1",
   "parse_module_tag_info wrong")

report("SEL-taxonomy", "SELTagInfo (Pillars→Skills→Goals→Levels + weighted edges) + ModuleTagData "
       "fields and parsers")
