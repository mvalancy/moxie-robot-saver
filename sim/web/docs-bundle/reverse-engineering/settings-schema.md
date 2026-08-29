# ⚙️ SettingSchema — the robot's configuration surface

> Every runtime setting the robot exposes via `embodied::core::SettingSchema` (extracted from
> `libbo-logger`/`libbo-dispatch`, **v3.6.4-Zephyr / OTA v24.10.803**): **199 keys**. These are pushed
> from the backend (over MQTT config / `ServiceConfiguration`-adjacent channels) and read across the
> `bo-*` components. A revival server (goal #2) uses these to tune behavior; most have safe defaults.

## Cloud / brain / content

`BRAIN_BASE_TOPIC` · `BRAIN_BOTNAME` · `BRAIN_CONTENT_SCHEDULE` · `BRAIN_CONTEXTS_DOWNLOAD` ·
`BRAIN_ENTRANCES_AVAILABLE` · `BRAIN_MODULES_DOWNLOAD` · `BRAIN_RECOMMENDER` · `BRAIN_SCHEDULE_DOWNLOAD` ·
`BASE_CHAT_ID` · `RC_TOPIC` · `RC_EXIT_COUNT` · `REMOTE_CHAT_API` · `REMOTE_GLOBAL_COMMANDS` ·
`REMOTE_LINES_SUBKEY` · `REMOTE_TRANSITIONS` · `CLOUD_SCHEDULE_API` · `CLOUD_SCHEDULE_RESET_THRESH` ·
`CONTEXTS_KEY` · `CONTEXT_LINE_CLEAN` · `REPLACE_LOCAL_MODULES` · `STRICT_MODULES` ·
`MQTT_FILE_SYNC` · `MQTT_FILE_RECOVERY` · `ZMQ_BRIDGE` · `STARTUP_SYNC_TIMEOUT`

## LLM / multi-party chat (MP)

`FALLBACKS_GPT_MODEL` · `NO_GPT_BIAS` · `USE_WOLFRAM_ALPHA` · `USE_OPEN_CONVO` · `ENABLE_MPCHAT_EVERYWHERE` ·
`MP_ASYNC_TIMEOUT` · `MP_AUTOTARGET` · `MP_DEBUG_FALLBACK` · `MP_EOD_MAX_CONVERSATIONS` · `MP_FAST` ·
`MP_PROMPT_THINK_TIMEOUT` · `MP_SHORT_CONV_VOLLEYS` · `MP_STREAM` · `FALLBACK_NONSENSE_THRESHOLD` ·
`USE_DEFAULT_FALLBACK_CONTEXT` · `CHAT_ALTERNATES` · `CHAT_OG_LANGUAGE` · `DO_CS_GLEAN` · `CS_TIMING_ERROR_THRESHOLD`

## TTS (speech out)

`CLOUD_TTS` · `CLOUD_TTS_ENGINE` · `CLOUD_TTS_VOICE_ID` · `CLOUD_TTS_SPEECH_RATE` · `CLOUD_TTS_GAIN_DB` ·
`CLOUD_TTS_GAIN_NORM` · `CLOUD_TTS_US` · `TTS_GAIN_NORM` · `CLOUD_TRANSLATE`

## STT / ASR (speech in)

`ASR_LANGUAGE` · `ASR_MODEL` · `ASR_MOTOR_FILTER` · `STT_IMPL` · `LOCAL_STT` · `LOCAL_STT_THRESHOLD` ·
`USE_LOCAL_STT_QUANTIZED_MODEL` · `GOOGLE_STT_REGION` · `STT_CONNECT_TIMEOUT` · `STT_READ_TIMEOUT` ·
`STT_ERROR_THRESHOLD` · `STT_EOS_WARN` · `STT_ZMQ_WARN` · `STT_MAX_PARTIAL_AGE` · `STT_FUSION_PADDING` ·
`STT_KILL_ON_FUSION_PARTIAL_TIMEOUT` · `STT_KILL_ON_GOOGLE_VAD_END` · `STT_PROTO_LOGGING` ·
`TRANSLATE_PARTIALS` · `GROUP_BINARY`

> `STT_IMPL` + `LOCAL_STT` show the robot can run **on-device STT** (quantized model) as well as the
> cloud Deepgram path ([`perception-pipeline.md`](perception-pipeline.md)); `CLOUD_TTS_ENGINE`/`VOICE_ID`
> make TTS server-selectable.

## Audio DSP · VAD · wake (XMOS / WebRTC / Trill)

`AEC_ALLOW_DYNAMIC_CONFIG` · `AUDIO_INPUT_DEVICE_ID` · `ANDROID_VOLUME_MAX` · `DOA_RANGE` · `DOA_ZERO` ·
`DOA_ROBOT_SPEAKING_DELAY_MS` · `VAD_CONFIG_HIGH/LOW/OFF` · `VAD_FILTER_MOXIE_SPEECH` · `VAD_FOR_SPEECH_STATE` ·
`WEBRTC_VAD_AGGRESSIVENESS` · `WEBRTC_VAD_SPEECH_START` · `WEBRTC_VAD_SPEECH_STOP` ·
`TRILL_PREFIX/POSTFIX/THRESHOLD/VAD/WEBRTC_AGG/WEBRTC_TH` · `USE_TRILS_FEATS` ·
`XMOS_VARIANT` · `XMOS_RESET_TIME` · `XMOS_ENABLE_LOGGING` · `XMOS_DOA_BOOST_ENABLE/ANGLE_TH/START_TH/END_TH` ·
`XMOS_VAD_BOOST_ENABLE/SENSITIVITY/AGCTIME/WINDOW_TIME/START_TH/END_TH`

> `XMOS_VARIANT` selects which XMOS DSP firmware image is active (ties to the `xmosdfu` `.bin` set,
> [`perception-pipeline.md`](perception-pipeline.md)). "Trill" is the on-device wake/VAD feature path.

## Vision · fusion · tracking · face ID

`ADULT_FACE` · `AGGRESSIVE_TRACKING` · `FACE_TRACKING_THRESHOLD` · `HOLD_FACE_THRESHOLD` · `LONG_FACES_SETTING` ·
`ENGAGED_TARGETING_THRESHOLD` · `INSESSION_DISTANCE_THRESHOLD` · `TRACKER_TYPE` · `TWO_D_TRACKER_ON` ·
`WORLDSPACE_FACE_SEARCH` · `SEARCH_TO_FOCUS` · `TARGET_ALL` · `TARGET_LOST_TIMER_MS` · `MENTOR_AUTOTARGET` ·
`USE_FACE_ID_HISTORY` · `USE_SPEAKER_ID` · `MAX_ENROLL` · `MAX_ENROLLMENT_THRESHOLD` · `MIN_ENROLLMENT_THRESHOLD` ·
`EMBEDDING_DECAY` · `EMBEDDING_DECAY_THRESHOLD` · `MIN_DECAY_CAP` · `FUSER_RANK_WARN` · `FUSION_IS_SPEAKING_TIMEOUT` ·
`FUSION_MAXIMUM_DOA_ONLY_AGE` · `FUSION_MAXIMUM_DOA_ONLY_AGE_WHILE_ROBOT_SPEAKING` · `IMAGE_CAPTIONING` ·
`IMAGE_CAPTIONING_MODEL` · `IMAGE_CAPTIONING_TIMEOUT` · `IMAGE_CAPTION_BY_RB` · `ENHANCED_VISION_LOG` ·
**`GAZE_WEIGHT`** · **`GAZE_DECAY`**

> **Gaze / eye contact.** `GAZE_WEIGHT` and `GAZE_DECAY` tune how strongly a person's **gaze** counts
> toward attention/targeting and how fast that contribution fades — the robot scores who is looking at
> it (`gaze_score`, `eye_contact` in the fusion path) and uses it to pick whom to attend to, alongside
> `FACE_TRACKING_THRESHOLD`/`ENGAGED_TARGETING_THRESHOLD`. Content can also explicitly request eye
> contact via `LookAtMeRequest{user, bot}` ([`perception-pipeline.md`](perception-pipeline.md)). A
> revival server can leave these at defaults (attention is computed on-device) or tune them to make
> Moxie more/less eager to hold eye contact; a **[SIL](../architecture/sil-and-cicd.md)** can mirror the
> same idea in its idle-gaze behavior.

## Wake · sleep · session

`WAKE_BUTTON` · `WAKE_BUTTON_ENABLED` · `WAKE_ALARMS` · `WAKE_WITHOUT_NET` · `TOUCH_WAKE_ENABLED` ·
`TOUCH_WAKEUP` · `ENABLE_SMART_WAKEUP` · `VC_WAKE` · `AUDIO_WAKE_SET` · `AUDIO_ONLY_BEDTIME` ·
`EARMUFF_MINUTES` · `SLEEP_IN_ZERO` · `WAIT_STATE_TIMEOUT` · `WAITING_ROOM_VAD_DELAY` · `DYNAMIC_WAIT_SETTING` ·
`MIN_REPROMPT_TIMER` · `NO_REPROMPT` · `EARLY_BHT_POS` · `SIMULATE_MULTIPLE`

## Parent-app-controlled

`PARENT_APP_LANGUAGE_SUPPORT` · `PARENT_AUDIO_WAKE` · `PARENT_FACE_OPTIONS` · `PARENT_FAMILY_INFO` ·
`PARENT_SENSITIVE_CONVO` · `SYSTEM_LANGUAGE` · `DENIED_WORDS_LATEST_LONG` · `DENIED_WORDS_VIDEO` ·
`FILTER_LOCAL_NOTIFY`

## System · power · logging · recovery · feature flags

`BATTERY_ABORT_THRESHOLD` · `BATTERY_MON_MOTOR` · `BATTERY_SHUTDOWN_THRESHOLD` · `MOTOR_DISABLE` ·
`REBOOT_WINDOW` · `LOGGING_LEVEL` · `LOG_GCP_DISABLE` · `LOG_THROTTLE_ACTIVE` · `LOG_THROTTLE_IDLE` ·
`DISABLE_ANALYTICS` · `LONG_RUNNING_THRESHOLD` · `UNITY_TRACES` · `GC_FINAL_ONLY` · `ASSET_ROOTS` ·
`SYS_WIFI_QR_RECOVERY` · `SYS_WIFI_RESET_RECOVERY` · `FEA_DEBUG` · `FEA_PLAYZONE` · `FEA_QUIET_REBOOT` ·
`FTUE_ON_BOOT` · `DISABLE_FTUE` · `DEBUG_WHITEBOARD`

*(199 keys total; grouped for readability. The recommender weights —
`RECOMMENDATION_*` — and a few enum/index helpers round out the set.)*

## For revival (goal #2)

A server doesn't need to set most of these — defaults work. The high-value ones to control:
`CLOUD_TTS_ENGINE`/`VOICE_ID` (pick a TTS), `STT_IMPL`/`LOCAL_STT` (STT backend), `FALLBACKS_GPT_MODEL`
(LLM), `BRAIN_*_DOWNLOAD` + `REMOTE_CHAT_API` (content/chat endpoints), and `SYSTEM_LANGUAGE`. See
[`cloud-protocol.md`](cloud-protocol.md) for how config reaches the robot.

---
📖 [Cloud protocol](cloud-protocol.md) · [Content & conversation](content-and-conversation.md) · [Reverse-engineering index](README.md) · [Docs index](../README.md)
