# 🤖 Robot capability catalog

Everything **the Moxie robot itself** can do, as a single capability index (the parent-app features
are in [`feature-catalog.md`](feature-catalog.md)). Each row links to the deep reverse-engineering doc.
Grounded in firmware **v3.6.4-Zephyr / OTA v24.10.803**.

## 💬 Conversation
| Capability | Where |
|---|---|
| On-device **ChatScript** (offline/global commands) | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md) |
| Cloud **LLM chat** (RemoteChat) + content modules + `volley` code hooks | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md) |
| **Telehealth / live remote puppet** (operator drives speech+motion) | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#telehealth-remote-puppet-mode) |
| Context assembly (global/environment/conversation) + **holiday/event awareness** | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#context-assembly-topical-awareness) |
| NLU intents, fallbacks, idle-state | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md) |

## 🗣️ Speech I/O
| Capability | Where |
|---|---|
| **STT**: cloud Deepgram + **offline Kaldi** (nnet3+RNNLM) | [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md) |
| **TTS**: CereVoice (CereProc DNN); server CloudTTS returns PCM+marks | [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md) |
| **Wake-word** ("Hey Moxie") on XMOS + TRILLsson/WebRTC VAD | [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md#wake-word-vad-fully-on-device) |
| ASR **phrase-hint biasing**, translation | [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md) |
| Speaker ID, DOA, barge-in/interruption | [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md) |

## 👁️ Vision
| Capability | Where |
|---|---|
| Faces (detect/recognize/track), people, poses, gaze | [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md) |
| **User recognition/enrollment** (learn family by face) | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#session-sleep-lifecycle) |
| Camera activities: **book**, **draw/card**, **image→text (VQA)**, QR | [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md#camera-driven-activities-content-activates-these) |

## 🦾 Movement & embodiment
| Capability | Where |
|---|---|
| Motors (arms/head/squish/base/torso) + per-motor PID | [hardware-map](../reverse-engineering/hardware/hardware-map.md) |
| **Behavior markup** (24 `cmd:` verbs: gestures/mood/audio) woven into TTS | [behavior-markup](../reverse-engineering/runtime/behavior-markup.md) |
| Turn body to face a person / seek out-of-view target | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#embodiment-activity-runtime-playspace-turn-taking-orientation) |
| Pickup/shake reactions (IMU) + camera-shake | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#embodiment-activity-runtime-playspace-turn-taking-orientation) |
| Status LEDs (moods) + DLP-projected face | [hardware-map](../reverse-engineering/hardware/hardware-map.md) · [device-tree](../reverse-engineering/hardware/device-tree.md) |
| Touch (back/tummy/hands), switches, flap, light sensors | [hardware-map](../reverse-engineering/hardware/hardware-map.md) |

## 🎮 Activities, content & progression
| Capability | Where |
|---|---|
| Content modules (conversations, regex globals, schedules) | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md) |
| **Content days**, daily missions, day-one onboarding, hub | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#scheduling-progression-rewards-what-to-offer-next) |
| Recommender (parent/sentiment/random/SEL weights) | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md) |
| **STAR goals** (SEL curriculum) + **StarBits** rewards | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md) |
| Mentor-behavior history (what the child did) | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md) |

## 👨‍👩‍👧 Session & wellbeing
| Capability | Where |
|---|---|
| Sessions incl. **group (multi-child)**, turn-taking, **age-adaptation** | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#session-sleep-lifecycle) |
| **Bedtime/sleep schedule** (weekday/weekend windows), earmuffs | [content-and-conversation](../reverse-engineering/runtime/content-and-conversation.md#session-sleep-lifecycle) |
| Parental content gating (denied words/videos, tag allow/deny) | [settings-schema](../reverse-engineering/firmware/settings-schema.md) |

## 🔧 Setup, connectivity & system
| Capability | Where |
|---|---|
| QR pairing / Wi-Fi / VPN / **endpoint re-home** + debug/factory codes | [qr-commands](../reverse-engineering/protocol/qr-commands.md) |
| Wi-Fi support (Open/WPA2-PSK/hidden; + post-pairing push) | [qr-commands](../reverse-engineering/protocol/qr-commands.md#wi-fi-provisioning-support-what-networks-work) |
| Runtime config surface (199 settings) | [settings-schema](../reverse-engineering/firmware/settings-schema.md) |
| A/B OTA; **MCU (STM32)** + **XMOS DSP** firmware update | [ota-and-recovery](../reverse-engineering/firmware/ota-and-recovery.md) · [hardware-map](../reverse-engineering/hardware/hardware-map.md) · [perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md) |
| Backup/restore; health telemetry; time sync (NTP) | [cloud-protocol](../reverse-engineering/protocol/cloud-protocol.md) · [network-trust](../reverse-engineering/protocol/network-trust.md) |
| Boot/lifecycle states; recovery; factory test suite | [boot-and-launcher](../reverse-engineering/firmware/boot-and-launcher.md) · [factory-provisioning](../reverse-engineering/firmware/factory-provisioning.md) |

---
📖 [Feature catalog (parent app)](feature-catalog.md) · [Coverage matrix](../reverse-engineering/COVERAGE.md) · [Field guide](../reverse-engineering/FIELD-GUIDE.md) · [Docs index](../README.md)
