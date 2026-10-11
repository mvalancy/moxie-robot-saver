# 📁 testing

The [`embodied.testing`](../../../../../docs/reverse-engineering/protocol/proto-catalog.md#embodiedtesting) package: test-harness messages for the fusion and vision modules.
`InitialFusionState` is a starting state for the people model, and `FaceDescriptors` is one frame of face geometry and embedding vectors.
[Perception pipeline](../../../../../docs/reverse-engineering/runtime/perception-pipeline.md#vision-embodiedperceptionvision) explains `FaceDescriptor` in its face-recognition section.

| File | Defines |
|---|---|
| [`Fusion.proto`](Fusion.proto) | `InitialFusionState` |
| [`Vision.proto`](Vision.proto) | `Point`, `FaceDescriptor`, `FaceDescriptors` |

---
📖 [embodied](../README.md) · [Docs index](../../../../../docs/README.md) · [Back to top](../../../../../README.md)
