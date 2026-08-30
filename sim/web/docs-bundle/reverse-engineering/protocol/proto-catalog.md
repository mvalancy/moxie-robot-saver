# 📖 Protocol message catalog — every message & enum

> Auto-generated from the **120 recovered `.proto` files** (firmware **v3.6.4-Zephyr / OTA v24.10.803**).
> The browsable index of the on-robot + cloud protocol. Regenerate with `python3 tools/robot-toolkit/gen_catalog.py`.
> Field/enum numbers are wire-compatible with the firmware.

**382 messages · 84 enums · 2074 fields · 120 files.**


## `embodied.Robot`


### `embodied/unity/ConsoleCommandRequest.proto`

- **`ConsoleCommandRequest`**
  - `uint64 timestamp = 1`
  - `string command = 2`

## `embodied.TTSMarkupTool`


### `embodied/unity/MarkUpToolMessages.proto`

- **`MarkUpEditRequest`**
  - `uint64 timestamp = 1`
  - `string id = 2`
  - `string input = 3`
- **`MarkUpEditResponse`**
  - `uint64 timestamp = 1`
  - `string id = 2`
  - `string input = 3`
  - `string output = 4`
  - `uint64 revision = 5`
  - `bool ended = 6`
- **`MarkUpEditorClosedEvent`**
  - `uint64 timestamp = 1`
- **`MarkUpLineRequest`**
  - `uint64 timestamp = 1`
  - `bool forward = 2`
- **`MarkUpLineResponse`**
  - `uint64 timestamp = 1`
  - `bool valid = 2`

## `embodied.launcher`


### `embodied/launcher/ComponentState.proto`

- **enum `State`** — `UNKNOWN=0`, `Running=1`, `NotRunning=2`, `Fault=3`
- **`ComponentState`**
  - `double timestamp = 1`
  - `string name = 2`
  - `embodied.launcher.State state = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`SetComponentState`**
  - `double timestamp = 1`
  - `string name = 2`
  - `embodied.launcher.State state = 3`
  - `string software_version = 100`
  - `string module_name = 101`

## `embodied.lizzerface`


### `embodied/lizzerface/enums.proto`

- **enum `PowerRail`** — `POWER_12V=0`, `POWER_3V3=1`, `POWER_5V=2`, `POWER_LCOS=3`, `POWER_MUTE=4`, `POWER_SPEAKER=5`, `NUM_POWER_RAILS=6`
- **enum `LedrPattern`** — `F_BOOTUP_DEFAULT=0`, `F_RDY2LISTEN_BLUE=1`, `F_LISTEN_GREEN=2`, `F_PROCESS_YELLOW=3`, `F_LW_BAT=4`, `F_PRIV=5`, `NUM_PATTERNS=6`
- **enum `FirmwareControlID`** — `CONTROL_RESET_MOTOR_IC=0`, `NUM_CONTROL_IDS=1`
- **enum `Revision_Level`** — `REVISION_UNKNOWN=0`, `REVISION_D1_Bo=209`, `REVISION_D2_Bo=210`, `REVISION_D3_Karu1=211`, `REVISION_D4_Karu1Skel=212`, `REVISION_D5_MoxieP6L1=213`, `REVISION_D6_MoxieBlue=214`, `REVISION_D7_MoxieBlue=215`
- **enum `SensorPB`** — `FLAP_SENSOR=0`, `NUM_SENSORS=1`
- **enum `Motor`** — `L_ARM_UP_DN=0`, `L_ARM_IN_OUT=1`, `R_ARM_UP_RN=2`, `R_ARM_IN_OUT=3`, `HEAD_UP_DN=4`, `HEAD_L_R=5`, `HEAD_TILT=6`, `SQUISH=7`, `MOT0=8`, `MOT1=9`, `NUM_MOTORS=10`, `BASE_L_R=11`, `TORSO_F_B=12`, `UNKNOWN_MOTOR=13`
- **enum `ConfigParam`** — `CONFIG_MOTOR_RWD=0`, `CONFIG_KP=1`, `CONFIG_KI=2`, `CONFIG_KD=3`, `CONFIG_MAX_PWM=4`, `CONFIG_KI_LEAK=5`, `CONFIG_LIMIT=6`, `CONFIG_ADJ=7`, `NUM_CONFIG_PARAMS=9`, `CONFIG_MOTOR_FWD=8`, `CONFIG_WRITE=85`
- **enum `MpuEventID`** — `STABLE=0`, `NOT_STABLE=1`, `PICKED_UP=2`, `PUTDOWN=3`, `FORCE_PUTDOWN=4`, `TILT=5`, `UNKNOWN_MPU_EVENT=6`
- **enum `SwitchID`** — `SWITCH0=0`, `SWITCH1=1`, `SWITCH2=2`, `DC_PLUG=3`, `LEFT_ARM=16`, `RIGHT_ARM=17`
- **enum `TouchID`** — `BACK=0`, `TUMMY=1`, `UNUSED=2`, `LEFTHAND=3`, `RIGHTHAND=4`

### `embodied/lizzerface/lizzerfaceinput.proto`

- **`MotorSetPosEventPB`**
  - `embodied.lizzerface.Motor motor = 1`
  - `uint32 pos = 2`
  - `uint64 timestamp = 3`
- **`ConfigureMotorEventPB`**
  - `embodied.lizzerface.Motor motor = 1`
  - `embodied.lizzerface.ConfigParam param = 2`
  - `uint32 val = 3`
  - `uint64 timestamp = 4`
- **`RobotEchoEventPB`**
  - `string message = 1`
  - `uint64 timestamp = 2`
- **`PowerEnableEventPB`**
  - `embodied.lizzerface.PowerRail rail = 1`
  - `uint64 timestamp = 2`
- **`PowerDisableEventPB`**
  - `embodied.lizzerface.PowerRail rail = 1`
  - `uint64 timestamp = 2`
- **`SensorSetEnabledEventPB`**
  - `embodied.lizzerface.SensorPB sensor = 1`
  - `bool enabled = 2`
  - `uint64 timestamp = 3`
- **`SetLedrEventPB`**
  - `embodied.lizzerface.LedrPattern ledr = 1`
  - `bool inloop = 2`
  - `uint64 timestamp = 3`
- **`RobotControlFirmwareEventPB`**
  - `embodied.lizzerface.FirmwareControlID id = 1`
  - `uint64 timestamp = 2`

### `embodied/lizzerface/lizzerfaceoutput.proto`

- **`BangEventPB`**
- **`FlapEventPB`**
  - `int32 Amplitude = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`LightAdcDataEventPB`**
  - `int32 adcCounts = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`LightEventPB`**
  - `bool State = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MpuEventPB`**
  - `embodied.lizzerface.MpuEventID ID = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`LizardErrorEventPB`**
  - `embodied.lizzerface.LizardErrorEventPB.LizardErrorEventID id = 1`
  - `bool is_fixed = 2`
  - `string error_detail = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `LizardErrorEventPB.LizardErrorEventID`** — `UNKOWN=0`, `UNKNOWN_ERROR=999`, `BSTATE_ERROR=1000`, `BATTERY_OVER_TEMP=1001`, `DISCHARGE_OVER_CURRENT=1002`, `BATTERY_LOST=1003`, `MOTOR_IC_ALERT=1004`, `MOTOR_FAIL_BOOT=1005`, `IMU_LOST=1006`, `LED_IC_LOST=1007`, `HOST_MESSY_CMD=1008`, `FIRMWARE_DL_FAIL=1009`, `FIRMWARE_WORD_XFER_FAIL=1010`, `FIRMWARE_RCD_BAD_VER=1011`, `FIRMWARE_READ_VER_FAIL=1012`, `FIRMWARE_INVALID_ERASE_RESPONSE=1013`, `FIRMWARE_ERASE_ROBOT_FLASH_FAIL=1014`, `FIRMWARE_INVALID_ACK=1015`, `FIRMWARE_INVALID_PKT_LEN=1016`, `FIRMWARE_INVALID_SYSINFO_RESPONSE=1017`, `FIRMWARE_GET_SYSINFO_FAIL=1018`, `FIRMWARE_SERIALPORT_OPEN_FAIL=1019`, `FIRMWARE_UART_TX_FAIL=1020`, `FIRMWARE_UNKNOWN_CMD_RX=1021`, `FIRMWARE_JNI_ERROR=1022`, `FIRMWARE_LIZARD_CONNECT_ERROR=1023`, `FIRMWARE_DEBUG_BUILD_ERROR=1024`, `FIRMWARE_FILE_NOT_FOUND=1025`, `FIRMWARE_DOWNGRADE=1026`, `FIRMWARE_ERASE_LIZARD_FLASH_FAIL=1027`, `FIRMWARE_WRITE_LIZARD_FAIL=1028`, `IMU_UPDATE_TIMEOUT=1029`, `FIRMWARE_UPDATE_ABORT=1030`, `FIRMWARE_BUG=1031`, `BASE_MOTOR_SPIN=1032`, `BATTERY_PEC_ERR=1033`, `BODYTOUCH_ERR=1039`, `POWER_SET_STATE_FAIL=1042`, `FW_WATCHDOG_RESET=1043`, `MPU_IS_NOISY=1044`, `MOTOR_IS_STALLED=1045`, `MPU_EVENT=1046`, `FIRMWARE_UPDATED=1047`, `FIRMWARE_VERSION=1048`, `POWER_STATE_CHANGE=1049`, `CHARGING_EVENT=1050`, `WAKEUP_ANDROID_EVENT=1051`
- **`ServoPosFdbackEventPB`**
  - `string cservoName = 1`
  - `sint32 pos = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ServoStallEventPB`**
  - `embodied.lizzerface.Motor motor_id = 1`
  - `bool is_stalled = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`SwitchEventPB`**
  - `embodied.lizzerface.SwitchID ID = 1`
  - `bool State = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`TouchEventPB`**
  - `embodied.lizzerface.TouchID ID = 1`
  - `bool State = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`RevisionLevelEventPB`**
  - `embodied.lizzerface.Revision_Level level = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`BatteryEventPB`**
  - `sint32 Level = 1`
  - `bool ChargingState = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`PowerStateEventPB`**
  - `embodied.lizzerface.PowerStateEventPB.PowerState state = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `PowerStateEventPB.PowerState`** — `UNKOWN=0`, `ACTIVE=1`, `SLEEPING=2`, `STANDBY=3`, `SHUTDOWN=4`
- **`LizardWakeupEventPB`**
  - `embodied.lizzerface.LizardWakeupEventPB.LizardWakeupEventID id = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `LizardWakeupEventPB.LizardWakeupEventID`** — `UNKOWN=0`, `TOUCH_WAKEUP=1`, `PICKUP_WAKEUP=2`, `DCPLUG_WAKEUP=3`

## `embodied.logging`


### `embodied/logging/Backup.proto`

- **`BackupStageRequest`**
  - `uint64 timestamp = 1`
  - `string path = 2`
  - `uint64 end_timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`BackupDataUpdate`**
  - `uint64 timestamp = 1`
  - `string actor = 2`
  - `bool complete = 3`
  - `uint64 extend_timestamp = 4`
  - `repeated string files_added = 5`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/logging/Cloud.proto`

- **enum `MoxieMode`** — `DEFAULT_MODE=0`, `TELEHEALTH=1`
- **enum `CloudQuery`** — `QUERY_UNKNOWN=0`, `idf=1`, `license=2`, `schedule=3`, `contexts=4`, `context_store=5`, `mentor_behaviors=6`, `remote_lines=7`
- **`Packet`**
  - `embodied.logging.Packet.Model model = 1`
  - `uint32 version = 2`
  - `uint64 recorded_at = 3`
  - `string moxie_id = 4`
  - `string moxie_session_id = 5`
  - `string user_id = 6`
  - `string event_name = 7`
  - `bytes event_data = 8`
  - `uint64 timestamp = 9`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `Packet.Model`** — `UNKNOWN=0`, `SessionLog=1`, `Device=2`, `Event=3`, `Raw=4`
- **`Device`**
  - `string fw_version = 3`
  - `string sw_version = 4`
  - `uint64 timestamp = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`RobotStatus`**
  - `string embodied_robot_id = 1`
  - `string last_updated_at = 2`
  - `string robot_firmware_version = 3`
  - `string android_version = 4`
  - `float battery_level = 5`
  - `float audio_volume = 6`
  - `string mode = 7`
  - `string wifi_ssid = 8`
  - `string last_back_up_at = 9`
  - `bytes public_key = 10`
  - `bytes user_id_encrypted = 11`
  - `float screen_brightness = 12`
  - `bool ota_reboot_required = 13`
  - `uint64 timestamp = 14`
  - `embodied.logging.DeviceSettings settings = 15`
  - `string mac = 16`
  - `string software_version = 100`
  - `string module_name = 101`
- **`UserAuthConfirm`**
  - `string user_token = 1`
  - `bytes public_key = 2`
  - `string embodied_robot_id = 3`
  - `bytes secret_key_encrypted_with_public_key = 4`
  - `string secret_key_hash = 5`
  - `uint64 timestamp = 6`
  - `string robot_firmware_version = 7`
  - `embodied.logging.UserAuthConfirm.ErrorCondition error = 8`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `UserAuthConfirm.ErrorCondition`** — `NONE=0`, `ALREADY_PAIRED=1`
- **`TopicParam`**
  - `string embodied_robot_id = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`RobotCloudRequest`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ContentPreferences`**
  - `repeated string pos_tags = 1`
  - `repeated string neg_tags = 2`
  - `repeated embodied.logging.ContentPreferences.SELPreference sel_weights = 3`
  - `float shyness_weight = 4`
  - `float structure_weight = 5`
  - `string learning_focus_text = 6`
  - `repeated string activity_preferences = 7`
  - `repeated string learning_focus_topics = 8`
  - `string mentor_interests_text = 9`
  - **`ContentPreferences.SELPreference`**
    - `string sel_tag = 1`
    - `float weight = 2`
- **`WakeSchedule`**
  - `repeated embodied.logging.WakeSchedule.WakeEntry wakes = 1`
  - `bool enabled = 2`
  - **`WakeSchedule.WakeEntry`**
    - `repeated uint32 days = 1`
    - `string time = 2`
- **`SchedulePreferences`**
  - `repeated embodied.logging.SchedulePreferences.ParentRequest parent_requests = 1`
  - **`SchedulePreferences.ParentRequest`**
    - `string module_id = 1`
    - `uint64 scheduled_at = 2`
- **`ChildEncrypted`**
  - `bytes first_name_encrypted = 1`
  - `bytes last_name_encrypted = 2`
  - `bytes nickname_encrypted = 3`
  - `bytes birthday_encrypted = 4`
  - `bytes therapy_needs_encrypted = 5`
  - `bytes self_regulation_tools_preferences_encrypted = 6`
  - `bytes likes_imaginative_play_encrypted = 7`
  - `string checksum = 8`
  - `bytes volume_preference_encrypted = 9`
  - `bytes calendar_events_encrypted = 10`
  - `bool unlimited_time = 11`
  - `repeated string holiday_events = 12`
  - `string id = 13`
  - `bool grl_connect_enabled = 14`
  - `embodied.logging.ContentPreferences content_preferences = 15`
  - `repeated string face_options = 16`
  - `embodied.logging.FamilyInformation family = 17`
  - `float input_speed = 18`
  - `uint32 starbits = 19`
- **`ChildDecrypted`**
  - `string first_name = 1`
  - `string last_name = 2`
  - `string nickname = 3`
  - `string birthday = 4`
  - `uint64 birthday_ts = 5`
  - `repeated string therapy_needs = 6`
  - `repeated string self_regulation_tools_preferences = 7`
  - `bool likes_imaginative_play = 8`
  - `string checksum = 9`
  - `repeated string volume_preference = 10`
  - `repeated string calendar_events = 11`
  - `bool unlimited_time = 12`
  - `repeated string holiday_events = 13`
  - `string id = 14`
  - `bool grl_connect_enabled = 15`
  - `embodied.logging.ContentPreferences content_preferences = 16`
  - `repeated string face_options = 17`
  - `embodied.logging.FamilyInformation family = 18`
  - `float input_speed = 19`
  - `uint32 starbits = 20`
- **`OtaUpdate`**
  - `string id = 1`
  - `string version = 2`
- **`SwitchUserConfig`**
  - `embodied.logging.SwitchUserConfig.UserAction action = 1`
  - `string restore_id = 2`
  - `string child_id = 3`
  - `bool force = 4`
  - `string child_name = 5`
  - **enum `SwitchUserConfig.UserAction`** — `UNKNOWN=0`, `NEW=1`, `RESET=2`, `RESTORE=3`
- **`RobotCloudConfig`**
  - `bool privacy_mode_enabled = 1`
  - `bool weekday_bedtime_enabled = 2`
  - `string weekday_bedtime_starts_at = 3`
  - `string weekday_bedtime_ends_at = 4`
  - `bool weekend_bedtime_enabled = 5`
  - `string weekend_bedtime_starts_at = 6`
  - `string weekend_bedtime_ends_at = 7`
  - `string last_updated_at = 8`
  - `float audio_volume = 9`
  - `embodied.logging.ChildEncrypted child = 10`
  - `embodied.logging.ChildDecrypted child_pii = 11`
  - `bytes secret_key = 12`
  - `embodied.logging.OtaUpdate ota_update = 13`
  - `float screen_brightness = 14`
  - `string timezone_id = 15`
  - `string data_sharing = 16`
  - `string forbid_otaver = 17`
  - `uint64 timestamp = 18`
  - `string rc_topic = 19`
  - `bool grl_connected = 20`
  - `embodied.logging.MoxieMode moxie_mode = 21`
  - `embodied.logging.DeviceSettings settings = 22`
  - `embodied.logging.SwitchUserConfig switch_user_config = 23`
  - `embodied.logging.WakeSchedule alarms = 24`
  - `bool wake_button_enabled = 25`
  - `string audio_wake_set = 26`
  - `bool touch_wake_enabled = 27`
  - `embodied.logging.SchedulePreferences schedule_preferences = 28`
  - `uint32 num_children = 29`
  - `uint32 max_children = 30`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ActivityContext`**
  - `string module_id = 1`
  - `string content_id = 2`
  - `string mentor_present = 3`
- **`ActivityUpdate`**
  - `string embodied_robot_id = 1`
  - `string embodied_activity_id = 2`
  - `string weekly_theme = 3`
  - `string title = 4`
  - `string activity_type_id = 5`
  - `string description = 6`
  - `string started_at = 7`
  - `string ended_at = 8`
  - `bool hide_activity = 9`
  - `uint64 started_at_ts = 10`
  - `uint64 ended_at_ts = 11`
  - `embodied.logging.ActivityContext context = 12`
  - `uint64 timestamp = 13`
  - `embodied.robotbrain.MentorBehavior mentor_behavior = 14`
  - `string software_version = 100`
  - `string module_name = 101`
- **`EndpointConfiguration`**
  - `string endpoint = 1`
  - `string gcp_project = 2`
- **`ServiceConfiguration`**
  - `string gcp_project = 1`
  - `string webservice_root = 2`
  - `string webservice_pin = 3`
  - `bool disable_sync = 4`
  - `bool disable_log_upload = 5`
  - `string endpoint = 6`
  - `uint64 timestamp = 7`
  - `string mqtt_host = 8`
  - `embodied.logging.ServiceConfiguration.ConnectionType connection_type = 9`
  - `embodied.logging.IOTEndpoint endpoint_id = 10`
  - `uint32 override_port = 11`
  - `bool disable_verify = 12`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `ServiceConfiguration.ConnectionType`** — `GOOGLE_IOT=0`, `EMBODIED_IOT=1`, `EMBODIED_LOCAL=2`
- **`EndpointStore`**
  - `repeated embodied.logging.ServiceConfiguration endpoints = 1`
- **`PairingComplete`**
  - `bytes secret_key = 1`
  - `bool restore = 2`
  - `string restore_id = 3`
  - `string http_token = 4`
  - `uint64 timestamp = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`RestoreResult`**
  - `string embodied_robot_id = 1`
  - `string restore_id = 2`
  - `bool success = 3`
  - `uint64 timestamp = 4`
  - `string backup_id = 5`
  - `string child_id = 6`
  - `string software_version = 100`
  - `string module_name = 101`
- **`CloudQueryRequest`**
  - `uint64 timestamp = 1`
  - `string subtopic = 2`
  - `string auid = 3`
  - `embodied.logging.CloudQuery query = 4`
  - `string request_id = 5`
  - `string schedule_id = 6`
  - `string subkey = 7`
  - `uint32 api_version = 8`
  - `uint32 user_age = 9`
  - `string child_id = 10`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MetaDataResponse`**
  - `string log = 1`
  - `string text = 2`
- **`CloudQueryResponse`**
  - `uint64 timestamp = 1`
  - `embodied.logging.CloudQuery query = 2`
  - `string request_id = 3`
  - `repeated embodied.logging.CloudQueryResponse.IDFRecord idf_values = 4`
  - `repeated embodied.logging.CloudQueryResponse.LicenseRecord license_values = 5`
  - `embodied.robotbrain.ContentSchedule schedule = 6`
  - `embodied.robotbrain.Contexts contexts = 7`
  - `repeated string additional_contexts = 8`
  - `repeated embodied.logging.CloudQueryResponse.VersionedContextsEntry versioned_contexts = 9`
  - `repeated embodied.robotbrain.MentorBehavior mentor_behaviors = 10`
  - `embodied.logging.MetaDataResponse meta_data = 11`
  - `repeated embodied.logging.CloudQueryResponse.DynamicLine remote_lines = 12`
  - `embodied.logging.CloudQueryResponse.QueryResponseCode response_code = 99`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `CloudQueryResponse.LicenseID`** — `LICENSE_UNKNOWN=0`, `cereproc=1`, `google_speech=2`
  - **enum `CloudQueryResponse.QueryResponseCode`** — `QUERY_OK=0`, `QUERY_NO_CHANGE=1`, `QUERY_NETWORK_FAIL=2`
  - **`CloudQueryResponse.IDFRecord`**
    - `string module_id = 1`
    - `float score = 2`
  - **`CloudQueryResponse.LicenseRecord`**
    - `embodied.logging.CloudQueryResponse.LicenseID id = 1`
    - `string license = 2`
    - `bytes license_binary = 3`
  - **`CloudQueryResponse.VersionedContextsEntry`**
    - `string key = 1`
    - `uint32 value = 2`
  - **`CloudQueryResponse.DynamicLine`**
    - `string id = 1`
    - `string text = 2`
- **`GRLTokenRequest`**
  - `uint64 timestamp = 1`
  - `string subtopic = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`GRLTokenUpdate`**
  - `uint64 timestamp = 1`
  - `string token = 2`
  - `bool success = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/logging/CloudStatus.proto`

- **`CloudStatusRequest`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`CloudStatus`**
  - `uint64 timestamp = 1`
  - `bool connected = 2`
  - `uint32 user_state = 3`
  - `embodied.logging.IOTEndpoint endpoint = 4`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `CloudStatus.UserState`** — `UNKNOWN=0`, `NONE=1`, `PAIRED_PENDING=2`, `PAIRED_VALID=3`, `UNPAIR_REQUESTED=4`, `OTA_LOCK=5`, `UNPAIR_WITH_RFS=6`, `USER_DATA_UPDATE=7`

### `embodied/logging/Family.proto`

- **`FamilyInformation`**
  - `repeated string members = 1`
  - **enum `FamilyInformation.FamilyRoles`** — `guardian=0`, `mother=1`, `father=2`, `grandmother=3`, `grandfather=4`, `sister=5`, `brother=6`

### `embodied/logging/FileSync.proto`

- **`FileEntry`**
  - `string path = 1`
  - `string hash = 2`
- **`FileListQuery`**
  - `string root_name = 1`
  - `string current_version = 2`
- **`FileListResponse`**
  - `string root_name = 1`
  - `string current_version = 2`
  - `repeated embodied.logging.FileEntry files = 3`
- **`FileRead`**
  - `string root_name = 1`
  - `embodied.logging.FileEntry file = 2`
- **`FileResponse`**
  - `string root_name = 1`
  - `embodied.logging.FileEntry file = 2`
  - `bytes contents = 3`
- **`FileSyncState`**
  - `uint64 timestamp = 1`
  - `string root_name = 2`
  - `string local_path = 3`
  - `embodied.logging.FileSyncState.SyncState sync_state = 4`
  - `embodied.logging.FileSyncState.RootType root_type = 5`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `FileSyncState.SyncState`** — `SYNC_IDLE=0`, `SYNC_ACTIVE=1`, `SYNC_COMPLETE=2`
  - **enum `FileSyncState.RootType`** — `ROOT_TYPE_UNKNOWN=0`, `ASSETS=1`

### `embodied/logging/Log.proto`

- **`LogDevice`**
  - `double timestamp_deprecated = 1`
  - `string deviceUUID = 2`
  - `string eventArgsTypename = 3`
  - `bytes eventArgs = 4`
  - `uint64 timestamp = 5`
- **`LogUser`**
  - `double timestamp_deprecated = 1`
  - `string deviceUUID = 2`
  - `string userUUID = 3`
  - `string eventArgsTypename = 4`
  - `bytes eventArgs = 5`
  - `uint64 timestamp = 6`
- **`LogcatTrace`**
  - `string timestamp = 1`
  - `string level = 2`
  - `string tag = 3`
  - `uint32 pid = 4`
  - `string message = 5`
  - `uint32 tid = 6`
  - `uint32 bo_uid = 7`
- **`DeviceSettings`**
  - `repeated embodied.logging.DeviceSettings.PropsEntry props = 1`
  - **`DeviceSettings.PropsEntry`**
    - `string key = 1`
    - `string value = 2`
- **`DeviceSettingsUpdate`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ProtoSubscribe`**
  - `uint64 timestamp = 1`
  - `repeated string protos = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`Ping`**
  - `uint64 timestamp = 1`
  - `bool include_zmq = 2`
  - `string user_data = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/logging/LoggingState.proto`

- **`LoggingStateChangeRequest`**
  - `embodied.logging.LoggingState state = 1`
  - `string path = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`LoggingStateUpdate`**
  - `embodied.logging.LoggingState state = 1`
  - `string path = 2`
  - `string uuid = 3`
  - `uint64 timestamp = 4`
  - `string user_uuid = 5`
  - `string session_uuid = 6`
  - `embodied.logging.LoggingPolicy upload_policy = 7`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/logging/SELUpdate.proto`

- **`SELUpdate`**
  - `string goal_uuid = 1`
  - `string level_uuid = 2`
  - `uint64 timestamp = 3`
  - `string module_id = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`SELUpdateSet`**
  - `repeated embodied.logging.SELUpdate sel_updates = 1`

### `embodied/logging/SomethingHappened.proto`

- **`SomethingsNotRight`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/logging/SystemMetrics.proto`

- **`SystemState`**
  - `uint64 timestamp = 1`
  - `float CPULoad = 2`
  - `uint32 RAMFree = 3`
  - `float DiskFree = 4`
  - `uint64 Uptime = 5`
  - `uint32 Temperature = 6`
  - `uint32 Battery = 7`
  - `int32 WifiRssi = 8`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/logging/enums.proto`

- **enum `LoggingState`** — `START=0`, `STARTED=1`, `STOP=2`, `STOPPED=3`
- **enum `LoggingPolicy`** — `NO_DATA=0`, `NO_MEDIA=1`, `FULL=2`
- **enum `IOTEndpoint`** — `IOT_DEFAULT=0`, `GOOGLE_DEVELOP=1`, `GOOGLE_STAGING=2`, `GOOGLE_PRODUCTION=3`, `EMBODIED_DEVELOP=4`, `EMBODIED_STAGING=5`, `EMBODIED_PRODUCTION=6`, `EMBODIED_HIPAA=7`, `EMBODIED_LOCAL=8`, `EMBODIED_CHINA=9`, `EMBODIED_HK=10`, `OPEN_MOXIE=11`

## `embodied.perception.audio`


### `embodied/perception/audio/DOA.proto`

- **`DOA`**
  - `uint64 timestamp = 1`
  - `int32 doa = 2`
  - `int32 vad = 3`
  - `uint64 doa_timestamp = 4`
  - `int32 doa_ready = 5`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/audio/GoogleAccount.proto`

- **`GoogleAccount`**
  - `string project_id = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/audio/Interrupt.proto`

- **`Interrupt`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`AllowInterrupt`**
  - `bool allow = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`CutoffStatistics`**
  - `uint64 timestamp = 1`
  - `uint64 cutoffs_detected = 2`
  - `uint64 cutoffs_by_non_target = 3`
  - `bool cutoffs_enabled = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`CutoffDetected`**
  - `uint64 timestamp = 1`
  - `uint64 cutoff_duration = 2`
  - `string stt_uuid = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`NonTargetCutoff`**
  - `uint64 timestamp = 1`
  - `string stt_uuid = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/audio/SNR.proto`

- **`PoorSNR`**
  - `uint64 timestamp = 1`
  - `string event_id = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/audio/STT.proto`

- **`STTPartial`**
  - `embodied.perception.audio.Speaker speaker = 1`
  - `string speech = 2`
  - `float confidence = 3`
  - `uint64 timestamp = 4`
  - `uint64 endTimestamp = 5`
  - `uint64 startTimestamp = 6`
  - `string event_id = 7`
  - `repeated string alternatives = 9`
  - `string original_language = 10`
  - `string original_speech = 11`
  - `repeated string original_alternatives = 12`
  - `string language = 13`
  - `string software_version = 100`
  - `string module_name = 101`
- **`STTFinal`**
  - `embodied.perception.audio.Speaker speaker = 1`
  - `string speech = 2`
  - `float confidence = 3`
  - `uint64 timestamp = 4`
  - `uint64 endTimestamp = 5`
  - `uint64 startTimestamp = 6`
  - `string event_id = 7`
  - `repeated string alternatives = 8`
  - `string language = 9`
  - `string original_language = 10`
  - `string original_speech = 11`
  - `repeated string original_alternatives = 12`
  - `string software_version = 100`
  - `string module_name = 101`
- **`STTReady`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ASRAnalytics`**
  - `uint64 detected_speech_start = 1`
  - `uint64 detected_speech_end = 2`
  - `uint64 asr_detected_speech_start = 3`
  - `uint64 asr_detected_speech_end = 4`
  - `uint64 asr_first_response = 5`
  - `bool asr_speech_detected = 6`
  - `uint64 timestamp = 7`
  - `uint64 first_message_sent_timestamp = 8`
  - `uint64 total_send_time = 9`
  - `uint64 maximum_send_time = 10`
  - `uint64 minimum_send_time = 11`
  - `uint64 first_final_asr_response = 12`
  - `uint64 last_final_asr_response = 13`
  - `uint64 final_result_count = 14`
  - `uint64 number_of_writes = 15`
  - `uint64 number_of_reads = 16`
  - `string event_id = 17`
  - `uint64 asr_last_response = 18`
  - `repeated string error_message = 30`
  - `string software_version = 100`
  - `string module_name = 101`
- **`DeepgramResponse`**
  - `float duration = 1`
  - `float start = 2`
  - `bool is_final = 3`
  - `bool speech_final = 4`
  - `embodied.perception.audio.DeepgramResponse.Channel channel = 5`
  - **`DeepgramResponse.Channel`**
    - `repeated embodied.perception.audio.DeepgramResponse.Channel.Alternative alternatives = 1`
    - **`DeepgramResponse.Channel.Alternative`**
      - `string transcript = 1`
      - `float confidence = 2`
      - `repeated embodied.perception.audio.DeepgramResponse.Channel.Alternative.Word words = 3`
      - **`DeepgramResponse.Channel.Alternative.Word`**
        - `string word = 1`
        - `float start = 2`
        - `float end = 3`
        - `float confidence = 4`

### `embodied/perception/audio/Speaker.proto`

- **`Speaker`**
  - `string id = 1`
  - `float doa = 2`
  - `float id_confidence = 3`
  - `uint64 timestamp = 4`
  - `repeated float doa_observations = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`EnrollmentState`**
  - `string id = 1`
  - `embodied.perception.audio.EnrollmentState.State state = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `EnrollmentState.State`** — `STARTED=0`, `FINISHED=1`

### `embodied/perception/audio/Speech.proto`

- **`SpeechStateChanged`**
  - `int64 timestamp_deprecated = 1`
  - `bool state = 2`
  - `embodied.perception.audio.Speaker speaker = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`VoiceActivity`**
  - `uint64 timestamp = 1`
  - `embodied.perception.audio.VoiceActivity.VoiceActivityState state = 10`
  - `repeated int32 doa = 11`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `VoiceActivity.VoiceActivityState`** — `UNKNOWN=0`, `START_OF_SPEECH=1`, `SPEECH=2`, `END_OF_SPEECH=3`

### `embodied/perception/audio/Status.proto`

- **`Status`**
  - `uint64 timestamp = 1`
  - `repeated string users = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/audio/WakeWord.proto`

- **`WakeWordEvent`**
  - `bool wake_word_detected = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/audio/XmosConfig.proto`

- **`EchoSuppressConfig`**
  - `uint64 timestamp = 1`
  - `int32 level = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/audio/zmqSTT.proto`

- **`zmqSTTRequest`**
  - `uint64 timestamp = 1`
  - `embodied.perception.audio.zmqSTTRequest.VADState vad = 2`
  - `bytes audio_content = 3`
  - `string uuid = 4`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `zmqSTTRequest.VADState`** — `UNKNOWN=0`, `START_OF_SPEECH=1`, `SPEECH=2`, `END_OF_SPEECH=3`
- **`zmqSTTResponse`**
  - `uint64 timestamp = 1`
  - `embodied.perception.audio.zmqSTTResponse.ResponseType type = 2`
  - `string speech = 3`
  - `float confidence = 4`
  - `uint64 end_timestamp = 5`
  - `uint64 start_timestamp = 6`
  - `string uuid = 7`
  - `uint32 error_code = 8`
  - `string error_message = 9`
  - `string language = 10`
  - `repeated string alternatives = 11`
  - `string original_language = 12`
  - `string original_speech = 13`
  - `repeated string original_alternatives = 14`
  - `repeated float speaker_id = 15`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `zmqSTTResponse.ResponseType`** — `PARTIAL=0`, `FINAL=1`

## `embodied.perception.fusion`


### `embodied/perception/fusion/FusedPeople.proto`

- **enum `FusedPersonSpeakingSource`** — `UNKNOWN=0`, `STT=1`, `VAD=2`
- **`FusedPeoplePB`**
  - `double timestamp_double = 1`
  - `repeated embodied.perception.fusion.FusedPersonPB people = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonPB`**
  - `double timestamp_double = 1`
  - `uint64 id = 2`
  - `string name = 3`
  - `string fullname = 4`
  - `bool is_visible = 5`
  - `bool is_engaged = 6`
  - `float engagement = 7`
  - `embodied.perception.fusion.FusedFacePB face = 8`
  - `embodied.perception.fusion.FusedBodyPB body = 9`
  - `embodied.perception.fusion.FusedSpeechPB speech = 10`
  - `uint64 timestamp = 11`
  - `float confidence = 12`
  - `float world_x = 13`
  - `float world_y = 14`
  - `float world_z = 15`
  - `float world_width = 16`
  - `float world_height = 17`
  - `bool vad_speaking = 18`
  - `uint64 started_speaking = 29`
- **`FusedFacePB`**
  - `float world_x = 1`
  - `float world_y = 2`
  - `float world_z = 3`
  - `float world_width = 4`
  - `float world_height = 5`
  - `float screen_x = 6`
  - `float screen_y = 7`
  - `float screen_width = 8`
  - `float screen_height = 9`
  - `float roll = 10`
  - `float pitch = 11`
  - `float yaw = 12`
  - `float confidence = 13`
  - `bool is_smiling = 14`
  - `float smile_confidence = 15`
  - `double last_time_in_view_double = 16`
  - `double last_time_seen_double = 17`
  - `float world_left_eye_x = 18`
  - `float world_left_eye_y = 19`
  - `float world_right_eye_x = 20`
  - `float world_right_eye_y = 21`
  - `float screen_left_eye_x = 22`
  - `float screen_left_eye_y = 23`
  - `float screen_right_eye_x = 24`
  - `float screen_right_eye_y = 25`
  - `uint64 last_time_in_view = 26`
  - `uint64 last_time_seen = 27`
  - `uint64 face_tracker_id = 28`
- **`FusedBodyPB`**
  - `float world_x = 1`
  - `float world_y = 2`
  - `float world_z = 3`
  - `float world_width = 4`
  - `float world_height = 5`
  - `float screen_x = 6`
  - `float screen_y = 7`
  - `float screen_width = 8`
  - `float screen_height = 9`
  - `float confidence = 10`
  - `double last_time_in_view_double = 11`
  - `double last_time_seen_double = 12`
  - `uint64 last_time_in_view = 13`
  - `uint64 last_time_seen = 14`
- **`FusedSpeechPB`**
  - `float world_x = 1`
  - `float world_y = 2`
  - `float world_z = 3`
  - `float doa = 4`
  - `string utterance = 5`
  - `bool is_speaking = 6`
  - `float begin_timestamp_float = 7`
  - `float end_timestamp_float = 8`
  - `float confidence = 9`
  - `double last_time_in_view_double = 10`
  - `double last_time_heard_double = 11`
  - `uint64 begin_timestamp = 12`
  - `uint64 end_timestamp = 13`
  - `uint64 last_time_in_view = 14`
  - `uint64 last_time_heard = 15`
  - `string stt_event_id = 16`
  - `float doa_confidence = 17`
  - `repeated string alternate_utterances = 18`
  - `string language = 19`
  - `string original_language = 20`
  - `string original_utterance = 21`
  - `repeated string original_alternate_utterances = 22`
- **`FusedPersonAddedPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonRemovedPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonMovedPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonStartedSpeakingPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `embodied.perception.fusion.FusedPersonSpeakingSource source = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonStoppedSpeakingPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `embodied.perception.fusion.FusedPersonSpeakingSource source = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonSayingPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonSayingTimeoutPB`**
  - `uint64 timestamp = 2`
  - `uint64 start_timestamp = 3`
  - `uint64 end_timestamp = 4`
  - `string event_id = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonSaidPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonSmiledPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonEngagedPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`FusedPersonDisengagedPB`**
  - `double timestamp_double = 1`
  - `embodied.perception.fusion.FusedPersonPB person = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`

## `embodied.perception.vision`


### `embodied/perception/vision/BookId.proto`

- **`BookIdPB`**
  - `string bookname = 1`
  - `uint64 timestamp = 2`
  - `uint32 center_x = 3`
  - `uint32 center_y = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/DrawId.proto`

- **`DrawIdPB`**
  - `string drawname = 1`
  - `uint64 timestamp = 2`
  - `uint32 center_x = 3`
  - `uint32 center_y = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/Face.proto`

- **`Face`**
  - `float centre_x = 1`
  - `float centre_y = 2`
  - `float centre_z = 3`
  - `float pose_r = 4`
  - `float pose_p = 5`
  - `float pose_y = 6`
  - `float distance = 7`
  - `string id = 8`
  - `uint64 emotion = 9`
  - `float emotion_proba = 10`
  - `uint64 action_unit = 11`
  - `float action_unit_proba = 12`
  - `uint64 timestamp = 13`
  - `string gesture = 14`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/FaceIDEnrollment.proto`

- **`FaceIDEnrollmentState`**
  - `string id = 1`
  - `embodied.perception.vision.FaceIDEnrollmentState.State state = 2`
  - `string error = 3`
  - `uint64 timestamp = 4`
  - `uint64 frame_id = 5`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `FaceIDEnrollmentState.State`** — `STARTED=0`, `FINISHED=1`, `FAILED=3`, `SKIPPED=4`
- **`FaceIDEnrollmentInfo`**
  - `string uuid = 1`
  - `uint64 number_of_enrollments = 2`
- **`FaceIDEnrollmentsInfo`**
  - `uint64 timestamp = 1`
  - `repeated embodied.perception.vision.FaceIDEnrollmentInfo enrollments = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/FacesDetected.proto`

- **`DetectedFacePB`**
  - `float center_x = 1`
  - `float center_y = 2`
  - `float width = 3`
  - `float height = 4`
  - `float confidence = 5`
  - `float pitch = 6`
  - `float yaw = 7`
  - `float roll = 8`
  - `uint64 emotion = 9`
  - `float emotion_proba = 10`
  - `float left_eye_x = 11`
  - `float left_eye_y = 12`
  - `float right_eye_x = 13`
  - `float right_eye_y = 14`
  - `float occlusion = 15`
  - `string gesture = 16`
- **`FacesDetectedPB`**
  - `uint64 frame_id = 1`
  - `double timestamp_deprecated = 2`
  - `repeated embodied.perception.vision.DetectedFacePB faces = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/FacesRecognized.proto`

- **`RecognizedPersonPB`**
  - `float center_x = 1`
  - `float center_y = 2`
  - `float width = 3`
  - `float height = 4`
  - `float confidence = 5`
  - `string name = 6`
  - `float pitch = 7`
  - `float yaw = 8`
  - `float roll = 9`
  - `uint64 emotion = 10`
  - `float emotion_proba = 11`
  - `float left_eye_x = 12`
  - `float left_eye_y = 13`
  - `float right_eye_x = 14`
  - `float right_eye_y = 15`
  - `float occlusion = 16`
  - `string gesture = 17`
- **`FacesRecognizedPB`**
  - `uint64 frame_id = 1`
  - `double timestamp_deprecated = 2`
  - `repeated embodied.perception.vision.RecognizedPersonPB people = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/FacesTracked.proto`

- **`WorldPosition`**
  - `float center_x = 1`
  - `float center_y = 2`
  - `float center_z = 3`
  - `float width = 4`
  - `float height = 5`
- **`TrackedFacePB`**
  - `float center_x = 1`
  - `float center_y = 2`
  - `float width = 3`
  - `float height = 4`
  - `float confidence = 5`
  - `string name = 6`
  - `uint64 id = 7`
  - `float pitch = 8`
  - `float yaw = 9`
  - `float roll = 10`
  - `uint64 emotion = 11`
  - `float emotion_proba = 12`
  - `float left_eye_x = 13`
  - `float left_eye_y = 14`
  - `float right_eye_x = 15`
  - `float right_eye_y = 16`
  - `embodied.perception.vision.WorldPosition world_position = 17`
  - `float name_confidence = 18`
  - `float occlusion = 19`
  - `string gesture = 20`
  - `repeated uint64 id_history = 21`
  - `int64 last_detected = 22`
  - `float initial_confidence = 23`
- **`FacesTrackedPB`**
  - `uint64 frame_id = 1`
  - `double timestamp_deprecated = 2`
  - `repeated embodied.perception.vision.TrackedFacePB tracked_people = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/ImageToText.proto`

- **`ImageToTextPB`**
  - `string question = 1`
  - `string prompt = 2`
  - `string description = 3`
  - `string session_id = 4`
  - `uint64 timestamp = 5`
  - `uint64 frame_id = 6`
  - `float targeted_width = 7`
  - `float targeted_height = 8`
  - `float targeted_center_x = 9`
  - `float targeted_center_y = 10`
  - `bool is_mentor = 11`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/OcclusionDetected.proto`

- **`OcclusionPB`**
  - `bool occluded = 1`
  - `uint64 occlusion_percentage = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/OfflineFace.proto`

- **`ActionUnitPB`**
  - `float au01 = 1`
  - `float au02 = 2`
  - `float au04 = 3`
  - `float au06 = 4`
  - `float au12 = 5`
  - `float au25 = 6`
- **`HeadPosePB`**
  - `float x = 1`
  - `float y = 2`
  - `float z = 3`
  - `float pitch = 4`
  - `float yaw = 5`
  - `float roll = 6`
- **`AnalyzedFacePB`**
  - `uint32 face_x = 1`
  - `uint32 face_y = 2`
  - `uint32 face_width = 3`
  - `uint32 face_height = 4`
  - `embodied.perception.vision.HeadPosePB head_pose = 5`
  - `embodied.perception.vision.ActionUnitPB action_unit = 6`
  - `repeated float landmarks = 7`
- **`FacesAnalyzedPB`**
  - `uint64 timestamp = 1`
  - `uint64 frame_id = 2`
  - `repeated embodied.perception.vision.AnalyzedFacePB faces = 3`
- **`OfflineMediaPB`**
  - `sint32 version = 1`
  - `string path = 2`
  - `string session_uuid = 3`
  - `string user_uuid = 4`
  - `uint64 begin_timestamp = 5`
  - `repeated embodied.perception.vision.FacesAnalyzedPB records = 6`
- **`OfflineAnalysisReady`**
  - `uint64 timestamp = 1`
  - `string data_descriptor = 2`
  - `string path = 3`

### `embodied/perception/vision/PeopleDetected.proto`

- **`PersonPB`**
  - `float center_x = 1`
  - `float center_y = 2`
  - `float width = 3`
  - `float height = 4`
  - `float confidence = 5`
- **`PeopleDetectedPB`**
  - `uint64 frame_id = 1`
  - `double timestamp_deprecated = 2`
  - `repeated embodied.perception.vision.PersonPB people = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/Person.proto`

- **`Person`**
  - `embodied.perception.vision.Face face = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/PosesEstimated.proto`

- **`jointPosPB`**
  - `uint64 index = 1`
  - `float x = 2`
  - `float y = 3`
- **`PosePB`**
  - `repeated embodied.perception.vision.jointPosPB joints = 1`
  - `uint64 class_id = 2`
  - `uint64 new_pose_id = 3`
  - `float proba = 4`
- **`PosesEstimatedPB`**
  - `uint64 frame_id = 1`
  - `double timestamp_deprecated = 2`
  - `repeated embodied.perception.vision.PosePB people = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/QR.proto`

- **`QRPB`**
  - `uint64 timestamp = 2`
  - `string qrcode = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/RapidMotionDetected.proto`

- **`RapidMotionPB`**
  - `uint64 rapid_motion = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/perception/vision/ShowState.proto`

- **`ShowState`**
  - `uint64 timestamp = 1`
  - `embodied.perception.vision.ShowState.Type type = 2`
  - `embodied.perception.vision.ShowState.State state = 3`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `ShowState.Type`** — `TYPE_UNDEFINED=0`, `BOOK=1`, `DRAWING=2`, `ARUCO=3`, `FACE=4`
  - **enum `ShowState.State`** — `STATE_UNDEFINED=0`, `STARTED=1`, `FINISHED=2`

## `embodied.playspace`


### `embodied/playspace/PlaySpace.proto`

- **enum `Source`** — `ROBOT=0`, `PORTAL=1`
- **enum `ResponseCode`** — `OK=0`, `SYSTEM_ERROR=1`, `NO_GRL_USER=2`, `GAME_UNAVAILABLE=3`, `GAME_UNKNOWN=4`, `NO_CLIENT=5`, `WAITING_FOR_SYNC=6`
- **enum `MoxieState`** — `DONE_SPEAKING=0`, `DONE_MOVING=1`, `STARTED_SPEAKING=2`, `PAUSED=3`, `READY=4`
- **enum `TurnState`** — `UNKNOWN=0`, `USER=1`, `SYSTEM=2`
- **enum `ExitCode`** — `QUIT=0`, `ERROR=1`, `COMPLETE=2`
- **enum `AgeGroup`** — `AGE_0_4=0`, `AGE_5_6=1`, `AGE_7_8=2`, `AGE_9_10=3`, `AGE_11_PLUS=4`
- **enum `TriggerAction`** — `SET=0`, `CLEAR=1`, `CLEAR_ALL=2`
- **enum `TriggerDuration`** — `ONE_SHOT=0`, `PASSIVE=1`, `ACTIVE=2`
- **`PlaySpaceHeader`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
- **`PlaySpaceConnect`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string request_id = 100`
  - `embodied.playspace.AgeGroup age_group = 101`
- **`PlaySpaceQuery`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string request_id = 100`
  - `embodied.playspace.PlaySpaceQuery.Query query = 101`
  - **enum `PlaySpaceQuery.Query`** — `QUERY_UNSET=0`, `IS_LOGGED_IN=1`
- **`PlaySpaceStart`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string game = 100`
  - `repeated embodied.playspace.PlaySpaceStart.GameDataEntry game_data = 101`
  - `string request_id = 102`
  - **`PlaySpaceStart.GameDataEntry`**
    - `string key = 1`
    - `string value = 2`
- **`PlaySpaceEnd`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string game = 100`
  - `repeated embodied.playspace.PlaySpaceEnd.GameDataEntry game_data = 101`
  - `embodied.playspace.ExitCode reason = 102`
  - `string request_id = 103`
  - **`PlaySpaceEnd.GameDataEntry`**
    - `string key = 1`
    - `string value = 2`
- **`PlaySpaceDisconnect`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `embodied.playspace.ExitCode reason = 100`
- **`PlaySpaceResponse`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `embodied.playspace.ResponseCode code = 100`
  - `string message = 101`
  - `string request_id = 102`
- **`GameData`**
  - `int64 priority = 100`
  - `string value = 101`
- **`PlaySpaceState`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string game = 100`
  - `repeated embodied.playspace.PlaySpaceState.GameDataEntry game_data = 101`
  - `embodied.playspace.TurnState turn = 102`
  - **`PlaySpaceState.GameDataEntry`**
    - `string key = 1`
    - `embodied.playspace.GameData value = 2`
- **`PlaySpaceMoxieState`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `embodied.playspace.MoxieState state = 100`
  - `string event_id = 101`
- **`Output`**
  - `string line_id = 1`
  - `repeated string line_params = 2`
  - `string text = 3`
  - `string markup = 4`
  - `repeated string line_ids = 5`
- **`PlaySpaceOutput`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string game = 100`
  - `embodied.playspace.Output output = 101`
  - `string event_id = 102`
- **`PlaySpaceTrigger`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string game = 100`
  - `string trigger_id = 101`
  - `embodied.playspace.TriggerAction trigger_action = 102`
  - `repeated string intent_names = 103`
  - `repeated string raw_inputs = 104`
  - `embodied.playspace.TriggerDuration duration = 105`
- **`PlaySpaceInput`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string game = 100`
  - `repeated string trigger_ids = 101`
  - `string intent_name = 102`
  - `repeated embodied.playspace.PlaySpaceInput.EntitiesEntry entities = 103`
  - `repeated string matched_words = 104`
  - `repeated string matched_trigger_ids = 105`
  - **`PlaySpaceInput.EntitiesEntry`**
    - `string key = 1`
    - `string value = 2`
- **`PlaySpaceMetrics`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `string activity_session_id = 4`
  - `embodied.playspace.Source source = 5`
  - `string game = 100`
  - `repeated embodied.playspace.PlaySpaceMetrics.MetricsEntry metrics = 101`
  - **`PlaySpaceMetrics.MetricsEntry`**
    - `string key = 1`
    - `float value = 2`

## `embodied.power`


### `embodied/system/PowerEvents.proto`

- **`SystemSuspendPB`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`SystemResumePB`**
  - `uint64 timestamp = 1`
  - `uint32 cause = 2`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `SystemResumePB.ResumeCause`** — `RESUME_FIRST_START=0`, `RESUME_RECOVERY=1`, `RESUME_FROM_SUSPEND=2`, `RESUME_POWER_ONLY=3`, `RESUME_HIDDEN_REBOOT=4`, `RESUME_BRAIN_UPDATED=5`
- **`SystemRecoverRequest`**
  - `uint64 timestamp = 1`
  - `uint32 target = 2`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `SystemRecoverRequest.RecoveryTarget`** — `RESTART_NONE=0`, `RESTART_XMOS=1`
- **`PowerStatePB`**
  - `uint64 timestamp = 1`
  - `uint32 state = 2`
  - `uint32 prev_state = 3`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `PowerStatePB.State`** — `STATE_INIT=0`, `STATE_CONFIG=1`, `STATE_STARTUP=2`, `STATE_RUNNING=3`, `STATE_LIGHT_SLEEP=4`, `STATE_SUSPEND=5`, `STATE_DEMO=6`, `STATE_RECOVERY=7`, `STATE_TELEBRAIN=8`, `STATE_SILENT_REBOOT=9`, `STATE_SILENT_RECOVERY=10`
- **`PowerStayAwakePB`**
  - `uint64 timestamp = 1`
  - `bool busy = 2`
  - `string software_version = 100`
  - `string module_name = 101`

## `embodied.robotbrain`


### `embodied/robotbrain/BedTimeStatus.proto`

- **`BedTimeStatus`**
  - `uint64 timestamp = 1`
  - `bool status = 2`
  - `bool status_plus_20 = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/ChatResponse.proto`

- **enum `OutputType`** — `CATCH_ALL=0`, `FALLBACK=1`, `GLOBAL_COMMAND=2`, `NORMAL=3`, `EVENT_INPUT=4`, `BASE_CASE=5`, `STINGER=6`, `USER_EVENT=7`, `UNSET=8`, `GLOBAL_RESPONSE=9`, `SILENT=10`, `EMPTY=11`, `CONTEXTUAL_FALLBACK=12`, `REMOTE_DELAY=13`
- **enum `FallbackType`** — `FALLBACK_UNKNOWN=0`, `REPROMPT=1`, `CLARIFICATION=2`, `NEAR_GLOBAL_COMMAND=3`, `FALLBACK_LOCAL_RULE=4`, `FALLBACK_LOCAL_FALLBACK=5`, `FALLBACK_CONFIRMATION=6`, `FALLBACK_MOVE_ON=7`, `FALLBACK_USE_REMOTE=8`, `FALLBACK_NO_REMOTE=9`
- **enum `BlockedType`** — `NOT_BLOCKED=0`, `INTERRUPTION_FAILED=1`, `EMPTY_OUTPUT=2`, `TARGET_SPEAKING=3`, `TARGET_OUT_OF_VIEW=4`, `NOT_TARGETED=5`, `NOT_ENGAGED=6`, `NOT_MATCHED=7`, `THINKING=8`, `WAITING=9`, `MARKING_UP=10`, `SPEAKING=11`, `ASR_DENIED=12`
- **enum `ResponseSource`** — `LOCAL_RESPONSE=0`, `REMOTE_RESPONSE=1`
- **`ActivityUpdateData`**
  - `string activity_id = 1`
  - `embodied.robotbrain.MentorAction action = 2`
- **`ChatResponse`**
  - `string bot = 1`
  - `string user = 2`
  - `string response = 3`
  - `uint64 id = 4`
  - `float doa = 5`
  - `uint64 timestamp = 6`
  - `string input = 7`
  - `string input_id = 8`
  - `string star_goal_type = 9`
  - `string star_goal_prompt = 10`
  - `string star_goal_response = 11`
  - `string star_goal_category = 12`
  - `double output_time = 13`
  - `embodied.robotbrain.OutputType output_type = 14`
  - `string chat_topic = 15`
  - `string chat_module = 16`
  - `embodied.perception.fusion.FusedPersonPB person = 17`
  - `string chat_content_id = 18`
  - `embodied.robotbrain.BlockedType blocked_type = 19`
  - `uint64 response_sequence_id = 20`
  - `embodied.robotbrain.ResponseSource source = 21`
  - `embodied.robotbrain.OutputType unused_output_type = 22`
  - `embodied.robotbrain.FallbackType fallback_type = 23`
  - `bool interruption = 24`
  - `repeated int32 response_chunks = 25`
  - `repeated string activity_completion_ids = 26`
  - `repeated embodied.robotbrain.ActivityUpdateData activity_updates = 27`
  - `embodied.robotbrain.ChatResponse.Engagement engagement = 28`
  - `repeated embodied.robotbrain.ChatResponse.InputVarsEntry input_vars = 29`
  - `int32 remote_module_volley_count = 30`
  - `int32 remote_node_volley_count = 31`
  - `string software_version = 100`
  - `string module_name = 101`
  - **`ChatResponse.Engagement`**
    - `bool is_mentor = 1`
    - `bool is_target = 2`
  - **`ChatResponse.InputVarsEntry`**
    - `string key = 1`
    - `string value = 2`

### `embodied/robotbrain/ChatScriptError.proto`

- **`ChatScriptError`**
  - `uint64 timestamp = 1`
  - `string reason = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/ChatScriptState.proto`

- **`ChatScriptReady`**
  - `string user = 1`
  - `string bot = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ChatScriptException`**
  - `string message = 1`
  - `uint64 timestamp = 2`
  - `bool restore_default = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ChatbotListeningRequest`**
  - `string user = 1`
  - `string bot = 2`
  - `bool listening = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`AllowCutoffEvent`**
  - `uint64 timestamp = 1`
  - `bool allow = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/ContentMetaTags.proto`

- **`CognitiveTag`**
  - `string name = 1`
  - `string uuid = 2`
  - `uint64 value = 3`
- **`IntimacyTag`**
  - `string name = 1`
  - `string uuid = 2`
  - `uint64 order = 3`
- **`ContentMetaList`**
  - `repeated embodied.robotbrain.CognitiveTag cognitive_load = 1`
  - `repeated embodied.robotbrain.IntimacyTag intimacy_level = 2`
  - `repeated embodied.robotbrain.Tag topics = 3`
  - `repeated embodied.robotbrain.Tag genres = 4`

### `embodied/robotbrain/ContentModule.proto`

- **`ContentDetail`**
  - `string id = 1`
  - `string name = 2`
  - `string detail = 3`
  - `repeated string content_tags = 4`
  - `repeated embodied.robotbrain.tags.GoalLevel goal_levels = 5`
  - `repeated string properties = 6`
  - `string app_title = 7`
  - `string app_detail = 8`
  - `string set_id = 9`
  - `embodied.robotbrain.ModuleDetail.ContentSource source = 10`
  - `repeated embodied.robotbrain.ContentDetail.LegacyDataEntry legacy_data = 11`
  - `string version = 12`
  - `repeated string assets = 13`
  - **`ContentDetail.LegacyDataEntry`**
    - `string key = 1`
    - `string value = 2`
- **`ModuleDetail`**
  - `embodied.robotbrain.ContentDetail info = 1`
  - `bool recommendable = 2`
  - `bool reportable = 3`
  - `embodied.robotbrain.ModuleDetail.ContentRules rules = 4`
  - `embodied.robotbrain.ModuleDetail.FirstTimeRules ft_rules = 5`
  - `repeated embodied.robotbrain.ContentDetail content_infos = 6`
  - `embodied.robotbrain.ModuleDetail.ContentSource source = 7`
  - `embodied.robotbrain.ModuleDetail.ModuleCategory category = 8`
  - `string confirmation_line = 9`
  - `uint32 min_api_version = 10`
  - `string app_type = 11`
  - `repeated uint32 held_line_ids = 99`
  - **enum `ModuleDetail.ContentRules`** — `UNSPECIFIED=0`, `ORDERED=1`, `ORDERED_EXHAUST=2`, `RANDOM=3`, `RANDOM_EXHAUST=4`, `DAILY_MISSION=5`, `CALENDAR=6`, `ORDERED_EXHAUST_SEEN=7`, `RANDOM_EXHAUST_SEEN=8`
  - **enum `ModuleDetail.ContentSource`** — `LOCAL=0`, `REMOTE_CHAT=1`, `HYBRID=2`
  - **enum `ModuleDetail.FirstTimeRules`** — `FTUE_UNSPECIFIED=0`, `FTUE_ONCE=1`, `FTUE_REUSE=2`
  - **enum `ModuleDetail.ModuleCategory`** — `UNASSIGNED=0`, `CREATIVITY=1`, `REGULATION=2`, `MOVEMENT=3`, `READING=4`, `PLAYFUL_GAME=5`, `PUZZLE_GAME=6`, `FUN_TIDBIT=7`, `LISTENING=8`, `MISSION=9`, `CONVERSATION=10`, `UTILITY=11`, `OTHER=12`

### `embodied/robotbrain/ContentSchedule.proto`

- **`ContentModule`**
  - `string module_id = 1`
  - `bool allowed = 2`
  - `repeated string denied_ids = 3`
- **`TagList`**
  - `repeated string allowed = 1`
  - `repeated string denied = 2`
- **`ScheduleConfig`**
  - `repeated embodied.robotbrain.RecommendationContext.Recommendation day_one_schedule = 1`
  - `repeated embodied.robotbrain.RecommendationContext.Recommendation promoted_content = 2`
  - `string prompt_template = 3`
  - `string prompt_lm = 4`
- **`EndOfSessionConfig`**
  - `embodied.robotbrain.RecommendationContext.Recommendation chat_module = 1`
  - `embodied.robotbrain.RecommendationContext.Recommendation end_module = 2`
  - `uint32 chat_count = 3`
- **`RewardsConfig`**
  - `string module_id = 1`
  - `uint32 min_content_day = 2`
- **`MissionConfig`**
  - `string mission_id = 1`
- **`ContentSchedule`**
  - `repeated embodied.robotbrain.ContentModule restricted_modules = 1`
  - `embodied.robotbrain.TagList tags = 2`
  - `repeated embodied.robotbrain.RecommendationContext.Recommendation provided_schedule = 3`
  - `embodied.robotbrain.ScheduleConfig config = 4`
  - `embodied.robotbrain.EndOfSessionConfig end_of_session = 5`
  - `embodied.robotbrain.RecommendationContext.Recommendation chat_request = 7`
  - `embodied.robotbrain.RecommendationContext.Recommendation wake_module = 8`
  - `embodied.robotbrain.RewardsConfig rewards = 9`
  - `embodied.robotbrain.MissionConfig mission_config = 10`
  - `embodied.robotbrain.ContentSchedule.HubConfig hub_config = 11`
  - `embodied.robotbrain.RecommendationContext.Recommendation alarm_module = 12`
  - **`ContentSchedule.HubConfig`**
    - `repeated embodied.robotbrain.RecommendationContext.Recommendation hubs = 1`
    - `repeated string skipped_modules = 2`
- **`ScheduleStart`**
  - `repeated embodied.robotbrain.RecommendationContext.Recommendation schedule = 1`
  - `bool resumed = 2`
  - `string request_id = 3`
  - `string log = 4`
  - `uint64 timestamp = 99`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/ContentTags.proto`

- **`Tag`**
  - `string uuid = 1`
  - `string name = 2`
- **`ContentTag`**
  - `repeated embodied.robotbrain.Tag replaced = 1`
  - `repeated embodied.robotbrain.Tag finalized = 2`
  - `repeated embodied.robotbrain.Tag review = 3`

### `embodied/robotbrain/Contexts.proto`

- **`Context`**
  - `string id = 1`
  - `string text = 2`
- **`GlobalContext`**
  - `embodied.robotbrain.Context context = 1`
- **`EnvironmentContext`**
  - `embodied.robotbrain.Context context = 1`
- **`ConversationContext`**
  - `embodied.robotbrain.Context context = 1`
  - `repeated string content_tags = 3`
  - `repeated embodied.robotbrain.tags.GoalLevel goal_levels = 4`
  - `repeated string properties = 5`
  - `repeated string prompt = 6`
  - `uint32 prompt_line_id = 7`
- **`Contexts`**
  - `repeated embodied.robotbrain.GlobalContext global_contexts = 1`
  - `repeated embodied.robotbrain.EnvironmentContext environment_contexts = 2`
  - `repeated embodied.robotbrain.ConversationContext conversation_contexts = 3`
  - `string sha = 4`

### `embodied/robotbrain/DailySchedule.proto`

- **`DailySchedule`**
  - `string csv_day_name = 1`
  - `string featured_module = 2`
  - `repeated string modules = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/EnableBook.proto`

- **`EnableBook`**
  - `uint64 timestamp = 1`
  - `bool run = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/EnableDraw.proto`

- **`EnableDraw`**
  - `uint64 timestamp = 1`
  - `bool run = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/EnableICModule.proto`

- **`EnableICModule`**
  - `uint64 timestamp = 1`
  - `bool run = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/EnableQRCode.proto`

- **`EnableQRCode`**
  - `uint64 timestamp = 1`
  - `bool run = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/Fallback.proto`

- **`Fallback`**
  - `string topic = 1`
  - `string module = 2`
  - `string userInput = 3`
  - `uint64 timestamp = 4`
  - `string fallbackType = 5`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/IdleStateChange.proto`

- **`IdleStateChange`**
  - `string state = 1`
  - `string user = 2`
  - `string bot = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/Intent.proto`

- **`IntentPB`**
  - `string intent = 1`
  - `string input = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/LineStore.proto`

- **`LineStoreSerialState`**
  - `repeated embodied.robotbrain.LineStoreSerialState.LineStoreEntry lines = 1`
  - **`LineStoreSerialState.LineStoreEntry`**
    - `string name = 1`
    - `uint32 expressions = 2`
    - `repeated uint64 masks = 3`

### `embodied/robotbrain/LookAtMe.proto`

- **`LookAtMeRequest`**
  - `string user = 1`
  - `string bot = 2`
  - `uint64 timestamp = 3`
  - `uint64 id = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/MentorBehavior.proto`

- **enum `MentorAction`** — `UNKNOWN=0`, `QUIT=1`, `REFUSED=2`, `COMPLETED=3`, `REQUESTED=4`, `PRESENTED=5`, `SCHEDULED=6`, `SUGGESTED=7`
- **enum `EndedReason`** — `REASON_UNKNOWN=0`, `USER_QUIT=1`, `USER_DISENGAGED=2`, `MOXIE_DISENGAGED=3`, `USER_REQUEST=4`, `TIME_LIMIT=5`, `MOXIE_ENDED=6`, `USER_SLEPT=7`, `REMOTE_LAUNCH=8`, `REMOTE_ABORT=9`
- **`MentorBehavior`**
  - `string module_id = 1`
  - `string content_id = 2`
  - `string content_day = 3`
  - `uint64 timestamp = 4`
  - `embodied.robotbrain.MentorAction action = 5`
  - `uint64 instance_id = 6`
  - `embodied.robotbrain.EndedReason ended_reason = 7`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MentorBehaviorSet`**
  - `repeated embodied.robotbrain.MentorBehavior mentor_behaviors = 1`

### `embodied/robotbrain/ModuleTag.proto`

- **`ModuleTagInfo`**
  - `repeated embodied.robotbrain.ModuleTagData module_tags = 1`
- **`ModuleTagData`**
  - `string _uuid = 1`
  - `string _module_id = 2`
  - `string _module_name = 3`
  - `repeated embodied.robotbrain.ContentInfo _index_table = 8`
  - `repeated embodied.robotbrain.tags.GoalLevel _sel_tags = 9`
  - `repeated embodied.robotbrain.ModuleTag _content_tags = 10`
  - `bool _does_report_completion = 11`
- **`ModuleTag`**
  - `string tag_uuid = 1`
  - `string source_uuid = 2`
- **`ContentInfo`**
  - `string _content_id = 1`
  - `embodied.robotbrain.ContentData _csv_dict = 2`
- **`ContentData`**
  - `string UUID = 1`
  - `string content_tags = 11`
  - `string sel_tags = 12`

### `embodied/robotbrain/PhraseHints.proto`

- **`PhraseHints`**
  - `string module = 1`
  - `repeated string hints = 2`
  - `uint64 timestamp = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`NameHints`**
  - `repeated string names = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`NativeHints`**
  - `repeated embodied.robotbrain.PhraseHints hints = 1`

### `embodied/robotbrain/PrimaryUserNameChange.proto`

- **`PrimaryUserNameChange`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/RemoteChat.proto`

- **`RemoteChatContext`**
  - `uint64 timestamp = 1`
  - `string text = 2`
  - `string context_type = 3`
- **`ExecuteReturn`**
  - `uint32 index = 1`
  - `string function_id = 2`
  - `string return = 3`
- **`RecommendationContext`**
  - `string context = 1`
  - `embodied.robotbrain.RecommendationContext.Urgency urgency = 2`
  - `repeated embodied.robotbrain.RecommendationContext.Recommendation exits = 3`
  - `repeated string restricted_modules = 4`
  - `repeated string holidays = 5`
  - **enum `RecommendationContext.Urgency`** — `UNSET_URGENCY=0`, `casual=1`, `normal=2`, `immediate=3`
  - **`RecommendationContext.Recommendation`**
    - `string module_id = 1`
    - `string content_id = 2`
    - `string entry_line = 3`
    - `string module_name = 4`
    - `string module_description = 5`
    - `bool seen = 6`
    - `bool skip_hub = 7`
- **`RemoteDataQuery`**
  - `embodied.robotbrain.RemoteDataQuery.Query query = 1`
  - `string key = 2`
  - `string subkey = 3`
  - `string current_version = 4`
  - **enum `RemoteDataQuery.Query`** — `UNSPECIFIED=0`, `contexts=1`, `modules=2`
- **`RemoteChatRequest`**
  - `uint64 timestamp = 1`
  - `string command = 2`
  - `uint32 sequence = 3`
  - `string speech = 4`
  - `float confidence = 5`
  - `string event_id = 6`
  - `repeated embodied.robotbrain.RemoteChatContext extra_lines = 7`
  - `string backend = 8`
  - `string session_id = 9`
  - `uint32 api_version = 10`
  - `uint32 rollback = 11`
  - `string module_id = 12`
  - `string content_id = 13`
  - `string user_id = 14`
  - `uint32 user_age = 15`
  - `bool upgrade_fallbacks = 16`
  - `embodied.logging.DeviceSettings settings = 17`
  - `embodied.robotbrain.RecommendationContext recommend = 18`
  - `bool allow_multiple = 19`
  - `embodied.robotbrain.Context global_context = 20`
  - `embodied.robotbrain.Context conversation_context = 21`
  - `embodied.robotbrain.Context prompt_context = 22`
  - `embodied.robotbrain.RemoteDataQuery query = 23`
  - `embodied.logging.FamilyInformation family = 24`
  - `bool stream_response = 25`
  - `string source_event_id = 26`
  - `repeated int32 response_chunks = 27`
  - `bool is_mentor = 28`
  - `string timezone_id = 29`
  - `repeated embodied.robotbrain.RemoteChatRequest.InputVarsEntry input_vars = 30`
  - `string nickname = 31`
  - `uint64 debug_timestamp = 32`
  - `repeated embodied.robotbrain.ExecuteReturn execute_returns = 33`
  - `bool no_llm = 34`
  - `embodied.robotbrain.ResponseSource notify_source = 35`
  - `repeated string speech_alternates = 36`
  - `string original_language = 37`
  - `string original_speech = 38`
  - `repeated string original_speech_alternates = 39`
  - `repeated string activity_ids = 40`
  - `string software_version = 100`
  - `string module_name = 101`
  - **`RemoteChatRequest.InputVarsEntry`**
    - `string key = 1`
    - `string value = 2`
- **`RemoteDialog`**
  - **enum `RemoteDialog.DialogAct`** — `DIALOG_ACT_UNKNOWN=0`, `abandon=1`, `apology=2`, `apology_response=3`, `appreciation=4`, `backchannelling=5`, `closing=6`, `complaint=7`, `opinion=8`, `statement_non_opinion=9`, `factual_question=10`, `opinion_question=11`, `hold=12`, `opening=13`, `yes_no_question=14`, `pos_answer=15`, `neg_answer=16`, `other_answers=17`, `command=18`, `comment=19`, `thanking=20`, `other=21`, `timeout=22`
  - **enum `RemoteDialog.EmotionState`** — `EMOTION_UNKNOWN=0`, `sadness=1`, `joy=2`, `love=3`, `anger=4`, `fear=5`, `surprise=6`, `neutral=7`
- **`RemoteSignals`**
  - `string single_signal = 1`
  - `string volley_signal = 2`
  - `embodied.robotbrain.RemoteSignals.MultiUtterSignals multi_utter_signals = 3`
  - **enum `RemoteSignals.Signal`** — `no_signal=0`, `closing=1`, `apology=2`, `interrupted_speech=3`, `complaint_clarification=4`, `confirmation_agreement=5`, `interest=6`, `non_interest=7`, `rejection_disagreement=8`
  - **`RemoteSignals.MultiUtterSignals`**
    - `float non_interest = 1`
- **`TagScore`**
  - `string name = 1`
  - `string uuid = 2`
  - `float score = 3`
- **`RemoteChatOutput`**
  - `string text = 1`
  - `string markup = 2`
  - `string mood = 3`
  - `float mood_intensity = 4`
  - `string dialog_act = 5`
  - `float dialog_act_score = 6`
  - `string emotion = 7`
  - `float emotion_score = 8`
  - `string sentiment = 9`
  - `float sentiment_score = 10`
  - `string single_signal = 11`
  - `string volley_signal = 12`
  - `float perplexity = 13`
  - `string source = 14`
  - `embodied.robotbrain.RemoteSignals signals = 15`
  - `repeated embodied.robotbrain.TagScore auto_tags = 16`
  - `string text_extended = 17`
- **`RemoteChatInput`**
  - `string emotion = 1`
  - `float emotion_score = 2`
  - `string dialog_act = 3`
  - `float dialog_act_score = 4`
  - `string sentiment = 5`
  - `float sentiment_score = 6`
  - `float perplexity = 7`
  - `string single_signal = 8`
  - `string volley_signal = 9`
  - `embodied.robotbrain.RemoteSignals signals = 10`
  - `string text = 11`
  - `embodied.robotbrain.RemoteChatInput.InputSafety safety = 12`
  - `repeated embodied.robotbrain.TagScore auto_tags = 13`
  - **`RemoteChatInput.InputSafety`**
    - `bool is_unsafe = 1`
    - `repeated string blocked_by = 2`
    - `repeated string intents = 3`
    - `int32 phrase_id = 4`
- **`RemoteConsistencyControl`**
  - `string prefix = 1`
  - `bool is_completed = 2`
  - `string extractor = 3`
- **`RemoteChatMetrics`**
  - `embodied.robotbrain.RemoteChatMetrics.HighLevel high_level_metrics = 1`
  - `embodied.robotbrain.RemoteChatMetrics.Numerics numerical_metrics = 2`
  - `embodied.robotbrain.RemoteChatMetrics.Classifications classification_metrics = 3`
  - **`RemoteChatMetrics.HighLevel`**
    - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity user = 1`
    - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity bot = 2`
    - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity both = 3`
    - **`RemoteChatMetrics.HighLevel.Entity`**
      - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity.PosNegSet emotionality = 1`
      - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity.PosNegSet sentimentality = 2`
      - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity.EngagementSet engagement = 3`
      - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity.RateSet informativity = 4`
      - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity.RateSet non_interest = 5`
      - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity.RateSet cognitive_load = 6`
      - `embodied.robotbrain.RemoteChatMetrics.HighLevel.Entity.RateSet nonsense = 7`
      - **`RemoteChatMetrics.HighLevel.Entity.PosNegSet`**
        - `float positive = 1`
        - `float negative = 2`
        - `float total = 3`
      - **`RemoteChatMetrics.HighLevel.Entity.RateSet`**
        - `float rate = 1`
      - **`RemoteChatMetrics.HighLevel.Entity.EngagementSet`**
        - `float opinion = 1`
        - `float question = 2`
        - `float total = 3`
  - **`RemoteChatMetrics.Numerics`**
    - `embodied.robotbrain.RemoteChatMetrics.Numerics.EntityCounts user = 1`
    - `embodied.robotbrain.RemoteChatMetrics.Numerics.EntityCounts bot = 2`
    - **`RemoteChatMetrics.Numerics.EntityCounts`**
      - `uint32 num_utters = 1`
      - `float avg_num_words = 2`
      - `float turn_balance = 3`
  - **`RemoteChatMetrics.Classifications`**
    - `float frustrated_rate = 1`
- **`EventSubscription`**
  - `bool clear = 1`
  - `repeated string active = 2`
  - `repeated string passive = 3`
- **`RemoteChatAction`**
  - `embodied.robotbrain.RemoteChatAction.ActionID action = 1`
  - `string module_id = 2`
  - `string content_id = 3`
  - `embodied.robotbrain.OutputType output_type = 4`
  - `bool is_remote_module = 5`
  - `embodied.robotbrain.EventSubscription event_subscription = 6`
  - `string function_id = 7`
  - `repeated string function_args = 8`
  - `bool requested = 9`
  - `repeated embodied.robotbrain.RemoteChatAction.ActionArgsEntry action_args = 10`
  - **enum `RemoteChatAction.ActionID`** — `UNSET_ACTION_ID=0`, `launch=1`, `launch_if_confirmed=2`, `exit_module=3`, `request_next=4`, `abort_module=5`, `execute=6`, `sleep=7`, `tangent=8`
  - **`RemoteChatAction.ActionArgsEntry`**
    - `string key = 1`
    - `string value = 2`
- **`IntentResult`**
  - `string matched_intent = 1`
  - `repeated embodied.robotbrain.IntentResult.EntitiesEntry entities = 2`
  - `float score = 3`
  - `repeated embodied.robotbrain.IntentResult.IntentRank ranking = 4`
  - **`IntentResult.EntitiesEntry`**
    - `string key = 1`
    - `string value = 2`
  - **`IntentResult.IntentRank`**
    - `string name = 1`
    - `float score = 2`
- **`RemoteDataBlock`**
  - `string version = 1`
  - `embodied.robotbrain.Contexts contexts = 2`
  - `repeated embodied.robotbrain.ModuleDetail modules = 3`
- **`FlowInfo`**
  - `string module_id = 1`
  - `string content_id = 2`
  - `string version = 3`
- **`RemoteChatResponse`**
  - `uint64 timestamp = 1`
  - `uint32 result = 2`
  - `uint32 sequence = 3`
  - `string event_id = 4`
  - `string input_speech = 5`
  - `embodied.robotbrain.RemoteChatOutput output = 6`
  - `uint64 processing_time = 7`
  - `string backend = 8`
  - `string input_sentiment = 9`
  - `repeated string input_intents = 10`
  - `uint64 server_timestamp = 11`
  - `embodied.robotbrain.RemoteChatAction response_action = 12`
  - `string worker_image = 13`
  - `embodied.robotbrain.IntentResult nlp_intent = 14`
  - `float relevancy_score = 15`
  - `float nonsense_score = 16`
  - `embodied.robotbrain.RemoteChatInput input = 17`
  - `embodied.robotbrain.RemoteConsistencyControl consistency_control = 18`
  - `embodied.robotbrain.RemoteChatMetrics metrics = 19`
  - `int32 gpt_status = 20`
  - `embodied.robotbrain.RemoteDataBlock query_data = 21`
  - `int32 chunk_num = 22`
  - `bool fallback = 23`
  - `int32 total_volleys = 24`
  - `int32 node_volleys = 25`
  - `repeated embodied.robotbrain.RemoteChatAction response_actions = 26`
  - `embodied.robotbrain.FlowInfo flow_info = 27`
  - `embodied.robotbrain.RemoteChatOutput original_output = 28`
  - `string output_speech = 99`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `RemoteChatResponse.ResultCode`** — `SUCCESS=0`, `ERROR_TIMEOUT=1`, `ERROR_STATE=2`, `ERROR_SERVICE=3`, `ERROR_OFFLINE=4`, `NOREPLY_INTERRUPT=5`, `NOREPLY_ACK=6`, `REPLY_FORCE_ANCHOR=7`, `REPLY_FORCE_QUIT=8`, `REPLY_PENDING=9`

### `embodied/robotbrain/RemoteResponseData.proto`

- **`RemoteResponseData`**
  - `uint64 instance_id = 1`
  - `float positive_emotion_score = 2`
  - `float negative_emotion_score = 3`
  - `float dialog_act_engagement_score = 4`
  - `float positive_sentiment_score = 5`
  - `float negative_sentiment_score = 6`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/Reset.proto`

- **`SoftReset`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`HardReset`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/STARGoalState.proto`

- **`STARGoalStateChange`**
  - `uint64 timestamp = 1`
  - `string goal = 2`
  - `uint64 goal_level = 3`
  - `uint64 prompt_level = 4`
  - `bool activated = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`STARGoalSuccess`**
  - `uint64 timestamp = 1`
  - `string goal = 2`
  - `uint64 goal_level = 3`
  - `uint64 prompt_level = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`STARGoalFailure`**
  - `uint64 timestamp = 1`
  - `string goal = 2`
  - `uint64 goal_level = 3`
  - `uint64 prompt_level = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/SessionState.proto`

- **`SessionUser`**
  - `uint32 user_age = 1`
  - `uint32 num_children = 2`
  - `uint32 max_children = 3`
- **`SessionState`**
  - `bool inSession = 1`
  - `uint64 timestamp = 2`
  - `embodied.robotbrain.SessionState.RecordMode record_mode = 3`
  - `embodied.robotbrain.SessionUser user = 4`
  - `string outSessionReason = 5`
  - `embodied.logging.DeviceSettings settings = 6`
  - `bool prev_active = 7`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `SessionState.RecordMode`** — `NORMAL=0`, `EXTENDED=1`

### `embodied/robotbrain/Starbits.proto`

- **`StarBitsEarned`**
  - `int32 earned = 1`
  - `int32 total = 2`
  - `string latest_unlocked = 3`
  - `uint64 timestamp = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/System.proto`

- **`SystemVolumeModify`**
  - `uint64 timestamp = 1`
  - `sint32 volume = 2`
  - `bool relative = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`SystemVolumeState`**
  - `uint64 timestamp = 1`
  - `uint32 volume = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`SystemSlowInputModify`**
  - `uint64 timestamp = 1`
  - `bool slow_input = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/TargetUser.proto`

- **enum `AttentionState`** — `ATTENTION_UNKNOWN=0`, `TARGET_FOCUS=1`, `NO_TARGET_FOCUS=2`, `SEARCHING=3`
- **`TargetedUser`**
  - `uint64 timestamp = 1`
  - `uint64 targeted_user_id = 2`
  - `uint64 targeted_user_face_id = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`NoTargetedUser`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`WorldLocation`**
  - `uint64 id = 1`
  - `float x = 2`
  - `float y = 3`
  - `float z = 4`
- **`InterestPoint`**
  - `float weight = 1`
  - `uint64 person_id = 2`
  - `embodied.robotbrain.WorldLocation location = 3`
- **`Attention`**
  - `uint64 timestamp = 1`
  - `embodied.robotbrain.AttentionState state = 2`
  - `uint64 targeted_user = 3`
  - `repeated embodied.robotbrain.InterestPoint locations = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/TopicChange.proto`

- **`TopicChange`**
  - `string user = 1`
  - `string bot = 2`
  - `string newTopic = 3`
  - `uint64 timestamp = 4`
  - `string currentModule = 5`
  - `string currentContentID = 6`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/TurnTaking.proto`

- **enum `TurnOwner`** — `TURNOWNER_UNKNOWN=0`, `TURNOWNER_MENTOR=1`, `TURNOWNER_MOXIE=2`
- **enum `MentorState`** — `MENTORSTATE_UNKNOWN=0`, `MENTORSTATE_IDLE=1`, `MENTORSTATE_SPEAKING=2`, `MENTORSTATE_INTERRUPTED=3`
- **enum `MoxieState`** — `MOXIESTATE_UNKNOWN=0`, `MOXIESTATE_IDLE=1`, `MOXIESTATE_LISTENING=2`, `MOXIESTATE_THINKING=3`, `MOXIESTATE_SPEAKING=4`, `MOXIESTATE_INTERRUPTED=5`
- **enum `EngagementState`** — `ENGAGEMENTSTATE_UNKNOWN=0`, `ENGAGEMENTSTATE_EARMUFFS=1`, `ENGAGEMENTSTATE_ENGAGED=2`, `ENGAGEMENTSTATE_SEEKING=3`, `ENGAGEMENTSTATE_DISENGAGED=4`
- **enum `TurnTakingAssistanceState`** — `ASSISTANCESTATE_UNKNOWN=0`, `ASSISTANCESTATE_NONE=1`, `ASSISTANCESTATE_ADVANCED=2`
- **`TurnTakingState`**
  - `uint64 timestamp = 1`
  - `embodied.robotbrain.TurnOwner turn_owner = 2`
  - `embodied.robotbrain.MentorState mentor_state = 3`
  - `embodied.robotbrain.MoxieState moxie_state = 4`
  - `embodied.robotbrain.EngagementState engagement_state = 5`
  - `embodied.robotbrain.TurnTakingAssistanceState assistance_state = 6`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/robotbrain/UserRecognition.proto`

- **`LearnUserState`**
  - `uint64 timestamp = 1`
  - `string name = 2`
  - `embodied.robotbrain.LearnUserState.State state = 3`
  - `int64 id = 4`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `LearnUserState.State`** — `STARTING=0`, `LEARNING=1`, `FINISHED=2`

### `embodied/robotbrain/WaitTimeout.proto`

- **`WaitTimeout`**
  - `double time = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`

## `embodied.robotbrain.serialized`


### `embodied/robotbrain/EventsAndHolidaysTags.proto`

- **`EventsAndHolidaysData`**
  - `repeated embodied.robotbrain.serialized.Holiday holidays = 1`
- **`Holiday`**
  - `string event_uid = 1`
  - `string holiday_id = 2`
  - `string name = 3`
  - `string tag = 4`
  - `string uuid = 5`
  - `string date = 6`
  - `string region = 7`

### `embodied/robotbrain/serialized/CSData.proto`

- **`CSData`**
  - `uint32 content_day = 1`
  - `uint64 forced_sleep_ts = 2`
  - `string module_id = 3`
  - `string content_id = 4`
  - `uint64 module_started_ts = 5`
  - `uint32 instance_id = 6`

### `embodied/robotbrain/serialized/FallbackInfo.proto`

- **`NodeFallback`**
  - `string id = 1`
  - `embodied.robotbrain.Context context = 2`
  - `embodied.robotbrain.serialized.NodeFallback.FallbackOptions opt = 3`
  - **enum `NodeFallback.FallbackOptions`** — `UNKNOWN=0`, `DEFAULT=1`, `CONVERSATION=2`, `SILENT=3`, `LOCAL_ONLY=4`, `FALLBACKS_NO_REMOTE=5`
- **`ContentIDFallback`**
  - `string id = 1`
  - `embodied.robotbrain.Context context = 2`
- **`ModuleFallback`**
  - `string id = 1`
  - `embodied.robotbrain.Context context = 2`
  - `repeated embodied.robotbrain.serialized.NodeFallback node_fallbacks = 3`
  - `repeated embodied.robotbrain.serialized.ContentIDFallback content_id_fallbacks = 4`
  - `embodied.robotbrain.serialized.NodeFallback module_default_fallback = 5`
- **`FallbackInfo`**
  - `embodied.robotbrain.Context default_context = 1`
  - `repeated embodied.robotbrain.serialized.ModuleFallback modules = 2`

### `embodied/robotbrain/serialized/UserRecommendationData.proto`

- **`UserRecommendationData`**
  - `repeated embodied.robotbrain.serialized.UserRecommendationData.TagHistoryEntry tag_history = 1`
  - `embodied.robotbrain.serialized.UserRecommendationData.RandomTagState random_tag_state = 2`
  - **`UserRecommendationData.SparseValues`**
    - `float value = 1`
    - `string id = 2`
  - **`UserRecommendationData.TagHistory`**
    - `repeated embodied.robotbrain.serialized.UserRecommendationData.SparseValues values = 1`
  - **`UserRecommendationData.TagHistoryEntry`**
    - `string key = 1`
    - `embodied.robotbrain.serialized.UserRecommendationData.TagHistory value = 2`
  - **`UserRecommendationData.RandomTagState`**
    - `uint32 random_seed = 1`
    - `string update_state = 2`
    - `string weight_state = 3`

## `embodied.robotbrain.tags`


### `embodied/robotbrain/Tags.proto`

- **`Tag`**
  - `string uuid = 1`
  - `string name = 2`
- **`GoalLevel`**
  - `string goal = 1`
  - `string level = 2`
- **`Weight`**
  - `string parentUUID = 1`
  - `string childUUID = 2`
  - `float weighting = 3`
- **`SELTagInfo`**
  - `repeated embodied.robotbrain.tags.Tag allGoals = 1`
  - `repeated embodied.robotbrain.tags.Tag allLevels = 2`
  - `repeated embodied.robotbrain.tags.Tag allSkills = 3`
  - `repeated embodied.robotbrain.tags.Tag allPillars = 4`
  - `repeated embodied.robotbrain.tags.Weight goalsToLevels = 5`
  - `repeated embodied.robotbrain.tags.Weight skillsToGoals = 6`
  - `repeated embodied.robotbrain.tags.Weight pillarsToSkills = 7`

## `embodied.sys`


### `embodied/system/SystemEvents.proto`

- **`WifiConnectionState`**
  - `uint64 timestamp = 1`
  - `bool connected = 2`
  - `string ssid = 3`
  - `uint32 seconds_in_state = 4`
  - `bool wifi_connected = 5`
  - `bool inet_connected = 6`
  - `string software_version = 100`
  - `string module_name = 101`
- **`STTConnectionState`**
  - `uint64 timestamp = 1`
  - `bool healthy = 2`
  - `uint32 error_nr = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`OTAStatus`**
  - `uint64 timestamp = 1`
  - `uint32 update_status = 2`
  - `bool payload_complete = 3`
  - `float update_percent = 4`
  - `sint32 payload_result = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`WifiRecoverRequest`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`ShutdownRequest`**
  - `uint64 timestamp = 1`
  - `uint32 recover_type = 2`
  - `string source = 3`
  - `string reason = 4`
  - `string software_version = 100`
  - `string module_name = 101`
- **`SystemShutdown`**
  - `uint64 timestamp = 1`
  - `uint32 recover_type = 2`
  - `uint32 time_remaining = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`DebugConfigureRequest`**
  - `uint64 timestamp = 1`
  - `string target = 2`
  - `uint32 target_state = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`UnpairUserRequest`**
  - `uint64 timestamp = 1`
  - `uint32 time_remaining = 2`
  - `embodied.sys.UnpairUserRequest.DisengageReason reason = 3`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `UnpairUserRequest.DisengageReason`** — `UNPAIRING=0`, `TELEHEALTH=1`, `USER_DATA_UPDATE=2`
- **`UnpairUserReady`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/system/TimeEvents.proto`

- **`TimeZoneInfo`**
  - `uint64 timestamp = 1`
  - `string midnight_in_timezone = 2`
  - `string olson_id = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`UserAlarmRequest`**
  - `uint64 timestamp = 1`
  - `uint32 timer_id = 2`
  - `uint64 alarm_expires = 3`
  - `uint64 alarm_repeats = 4`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `UserAlarmRequest.ReservedTimers`** — `TIMER_ID_USER_WAKE=0`, `TIMER_ID_PARENT_APP=1`, `TIMER_ID_CUSTOM=100`
- **`UserAlarmTriggered`**
  - `uint64 timestamp = 1`
  - `uint32 timer_id = 2`
  - `string software_version = 100`
  - `string module_name = 101`

## `embodied.telehealth`


### `embodied/telehealth/TeleHealth.proto`

- **enum `Action`** — `UNKNOWN_ACTION=0`, `START_SESSION=1`, `PLAY_OUTPUT=2`, `END_SESSION=3`, `UPDATE_STATE=4`, `INTERRUPT=5`
- **enum `RobotState`** — `UNKNOWN_STATE=0`, `READY=1`, `IN_SESSION=2`, `EXITING=3`
- **`TelehealthStatus`**
  - `uint64 timestamp = 1`
  - `bool telehealth_active = 2`
  - `bool session_active = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`Output`**
  - `string line_id = 1`
  - `repeated string line_params = 2`
  - `string text = 3`
  - `string markup = 4`
- **`TelehealthMessage`**
  - `uint64 timestamp = 1`
  - `embodied.telehealth.Action action = 2`
  - `embodied.telehealth.Output output = 3`
  - `embodied.telehealth.RobotState state = 4`
  - `string session_id = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`TelehealthRobotCommand`**
  - `string command = 1`
  - `embodied.telehealth.TelehealthMessage message = 2`
- **`TelehealthRobotEvent`**
  - `string subtopic = 1`
  - `embodied.telehealth.TelehealthMessage message = 2`

## `embodied.testing`


### `embodied/testing/Fusion.proto`

- **`InitialFusionState`**
  - `uint64 timestamp = 1`
  - `embodied.perception.fusion.FusedPeoplePB people = 50`
  - `uint64 next_id = 51`
  - `bool robot_is_speaking = 100`
  - `embodied.unity.RobotPosition robot_position = 101`
  - `bool user_is_targeted = 150`
  - `uint64 targeted_user_id = 151`

### `embodied/testing/Vision.proto`

- **`Point`**
  - `float x = 1`
  - `float y = 2`
- **`FaceDescriptor`**
  - `embodied.testing.Point center = 1`
  - `float w = 2`
  - `float h = 3`
  - `float pitch = 4`
  - `float yaw = 5`
  - `float roll = 6`
  - `float blur = 7`
  - `embodied.testing.Point left_eye = 8`
  - `embodied.testing.Point right_eye = 9`
  - `embodied.testing.Point chin = 10`
  - `repeated float descriptors = 11`
  - `string image = 12`
  - `uint64 id = 13`
  - `float occlusion = 14`
- **`FaceDescriptors`**
  - `uint64 timestamp = 1`
  - `uint64 frame_id = 2`
  - `repeated embodied.testing.FaceDescriptor faces = 3`

## `embodied.unity`


### `embodied/unity/AssetBundle.proto`

- **`AssetBundleCache`**
  - `uint64 timestamp = 1`
  - `repeated string bundles = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`AssetBundleScan`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`AssetBundleRelease`**
  - `uint64 timestamp = 1`
  - `repeated string bundles = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`AssetBundleReload`**
  - `uint64 timestamp = 1`
  - `repeated string bundles = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/AudioNotif.proto`

- **`AudioNotifPauseEventPB`**
  - `float duration = 1`
  - `uint64 timestamp = 2`
- **`AudioNotifSpeedChangeEventPB`**
  - `float speed = 1`
  - `uint64 timestamp = 2`
- **`AudioNotifVolumeChangeEventPB`**
  - `float volume = 1`
  - `uint64 timestamp = 2`
- **`AudioIsFinishedEventPB`**
  - `uint64 timestamp = 1`
- **`AudioNotifResumeEventPB`**
  - `uint64 timestamp = 1`
- **`AudioNotifChatEventPB`**
  - `string chatEvent = 1`

### `embodied/unity/CloudTTS.proto`

- **enum `RequestSourceType`** — `ROBOT_TTS_REQUEST=0`, `REMOTECHAT_TTS_REQUEST=1`
- **`TTSMark`**
  - `uint32 time = 1`
  - `uint32 start = 2`
  - `uint32 end = 3`
  - `string type = 4`
  - `string value = 5`
- **`CloudTTSRequest`**
  - `string markup = 1`
  - `string event_id = 2`
  - `int32 chunk_num = 3`
  - `uint64 timestamp = 4`
  - `string user_id = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`AudioBuffer`**
  - `bytes buffer = 1`
  - `int32 channels = 2`
  - `int32 sample_rate = 3`
- **`CloudTTSResponse`**
  - `embodied.unity.RequestSourceType request_source = 1`
  - `embodied.unity.AudioBuffer audio = 2`
  - `repeated embodied.unity.TTSMark marks = 3`
  - `string event_id = 4`
  - `int32 chunk_num = 5`
  - `uint64 timestamp = 6`
  - `uint64 total_time = 7`
  - `uint64 synthesis_time = 8`
  - `string software_version = 100`
  - `string module_name = 101`
- **`CloudTTSSupplement`**
  - `uint64 timestamp = 1`
  - `string event_id = 2`
  - `int32 chunk_num = 3`
  - `string text = 4`
  - `string markup = 5`
  - `uint64 translation_time = 6`
  - `uint64 automarkup_time = 7`
  - `uint64 synthesis_time = 8`
  - `string tts_engine = 9`
  - `uint64 total_time = 20`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/EngagementScore.proto`

- **`EngagedEvent`**
  - `bool engaged = 1`
  - `uint64 timestamp = 2`

### `embodied/unity/Gaze.proto`

- **`Gaze`**
  - `uint64 idEyeTarget = 1`
  - `float eyeTargetX = 2`
  - `float eyeTargetY = 3`
  - `float eyeTargetZ = 4`
  - `uint64 idHeadTarget = 5`
  - `float headTargetX = 6`
  - `float headTargetY = 7`
  - `float headTargetZ = 8`
  - `uint64 timestamp = 9`

### `embodied/unity/MainAppShutdown.proto`

- **`MainAppShutdown`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/MainAppStatus.proto`

- **`MainAppStatus`**
  - `uint64 timestamp = 1`
  - `uint32 code = 2`

### `embodied/unity/MpuPickup.proto`

- **`MpuPickedUpEventPB`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MpuPickedUpShakenEventPB`**
  - `embodied.unity.MpuShakeDirection shakeDirection = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MpuPickUpStatusEventPB`**
  - `int32 pitch = 1`
  - `uint64 timestamp = 2`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MpuPutDownEventPB`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MpuTiltEventPB`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`
- **`MpuIsNoisyEventPB`**
  - `uint64 timestamp = 1`
  - `bool state = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/NetworkState.proto`

- **`NetworkState`**
  - `bool Connected = 1`
  - `int32 Ping = 2`
  - `uint64 timestamp = 3`

### `embodied/unity/PredictedMotorNoise.proto`

- **`PredictedMotorNoise`**
  - `uint64 timestamp = 1`
  - `float noiseLevel = 2`

### `embodied/unity/RobotCamera.proto`

- **`RobotCamera`**
  - `float center_x = 2`
  - `float center_y = 3`
  - `float center_z = 4`
  - `float target_x = 5`
  - `float target_y = 6`
  - `float target_z = 7`
  - `float up_x = 8`
  - `float up_y = 9`
  - `float up_z = 10`
  - `float fov = 11`
  - `float aspect = 12`
  - `float near = 13`
  - `float far = 14`
  - `uint64 timestamp = 15`

### `embodied/unity/RobotCameraShake.proto`

- **`RobotCameraShake`**
  - `uint64 timestamp = 1`
  - `bool shaking = 2`

### `embodied/unity/RobotEngageTurn.proto`

- **`RobotEngageTurn`**
  - `uint64 timestamp = 1`
  - `bool turning = 2`

### `embodied/unity/RobotPosition.proto`

- **`RobotPosition`**
  - `uint64 timestamp = 1`
  - `float camera_center_x = 2`
  - `float camera_center_y = 3`
  - `float camera_center_z = 4`
  - `float camera_target_x = 5`
  - `float camera_target_y = 6`
  - `float camera_target_z = 7`
  - `float camera_up_x = 8`
  - `float camera_up_y = 9`
  - `float camera_up_z = 10`

### `embodied/unity/RobotRequestChatPause.proto`

- **`RobotRequestChatPause`**
  - `uint64 timestamp = 1`
  - `bool pause = 2`

### `embodied/unity/RobotTurnToOutOfViewChatTarget.proto`

- **`RobotTurnToOutOfViewChatTarget`**
  - `uint64 timestamp = 1`
  - `bool is_turning = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/SFXPlayback.proto`

- **`SFXPlaybackState`**
  - `bool isPlaying = 1`
  - `uint64 timestamp = 2`
  - `string input_id = 3`
  - `string label = 4`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/SilentBootComplete.proto`

- **`SilentBootComplete`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/SoftwareVersion.proto`

- **`SoftwareVersion`**
  - `uint32 UnityVersion = 1`
  - `string CommitHash = 2`
  - `uint64 timestamp = 3`

### `embodied/unity/SpeechPlayback.proto`

- **`SpeechPlaybackState`**
  - `bool isPlaying = 1`
  - `uint64 timestamp = 2`
  - `string input_id = 3`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/Stats.proto`

- **`FPSStatsPB`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `float curr_deltatime = 4`
  - `float curr_fps = 5`
  - `float lowest_fps = 6`
  - `float avg_fps = 7`
  - `float highest_fps = 8`
- **`TTSAudioClipInfoPB`**
  - `string clip_name = 1`
  - `float clip_length = 2`
  - `float create_duration = 3`
  - `float create_timestamp = 4`
- **`TTSStatsPB`**
  - `uint64 timestamp = 1`
  - `string software_version = 2`
  - `string module_name = 3`
  - `uint64 id = 4`
  - `string input_id = 5`
  - `float doa = 6`
  - `string synth_speech = 7`
  - `float synth_in_queue_duration = 8`
  - `float synth_to_callback_duration = 9`
  - `float synth_to_output_duration = 10`
  - `float synth_total_duration = 11`
  - `float synth_to_playbackqueue_duration = 12`
  - `float audioclip_create_duration = 13`
  - `float synth_to_playback_duration = 14`
  - `repeated embodied.unity.TTSAudioClipInfoPB audioclips_info = 15`

### `embodied/unity/UserData.proto`

- **`UserPairingRequest`**
  - `uint64 timestamp = 1`
  - `string user_token = 2`
  - `string public_key = 3`
  - `uint32 request = 4`
  - `bytes secret_key = 5`
  - `bool is_staging = 6`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `UserPairingRequest.PairingRequest`** — `PAIR_UNPAIR_LEGACY=0`, `PAIR=1`, `UNPAIR_USER=2`, `UNPAIR_FULL=3`, `UNPAIR_RFS_ONLY=4`, `RECOVER_USER=5`, `RECOVER_USER_LOCAL=6`, `USER_DATA_UPDATE=7`
- **`UserDataStatus`**
  - `uint64 timestamp = 1`
  - `uint32 code = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/unity/enums.proto`

- **enum `MpuShakeDirection`** — `Up=0`, `Roll=1`, `Pitch=2`, `Yaw=3`, `LeftRight=4`, `ForwardBack=5`, `Invalid=6`

### `embodied/wifiapp/QRCommands.proto`

- **`QRCommand`**
  - `uint64 timestamp = 1`
  - `string code = 2`
  - `string param = 3`
  - `embodied.logging.IOTEndpoint endpoint = 4`
  - `string command = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`QRResponse`**
  - `uint64 timestamp = 1`
  - `uint32 response_code = 2`
  - `string response = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`QRDiagnosticData`**
  - `string robot_uuid = 1`
  - `string rsa_pub = 2`
  - `bool cloud_connected = 3`
  - `uint32 user_state = 4`
  - `string cloud_project = 5`
  - `string software_version = 100`
  - `string module_name = 101`
- **`StartPairingQR`**
  - `string ssid = 1`
  - `string password = 2`
  - `bool is_staging = 3`
  - `bytes secret_key = 4`
  - `bool wifi_only = 5`
  - `bool is_hidden = 6`
  - `embodied.unity.StartPairingQR.WifiBandSelect band_select = 7`
  - `embodied.logging.IOTEndpoint endpoint = 8`
  - **enum `StartPairingQR.WifiBandSelect`** — `ANY=0`, `ONLY_50G=1`, `ONLY_24G=2`
- **`WifiNetworkUpdate`**
  - `uint64 timestamp = 1`
  - `embodied.unity.StartPairingQR wifi_info = 2`
  - `bool add_only = 3`
  - `string software_version = 100`
  - `string module_name = 101`
- **`QRMultiDecoder`**
  - `embodied.unity.QRCommand debug = 1`
  - `bytes encoded_proto = 2`
- **`QRVPNConfig`**
  - `uint64 timestamp = 1`
  - `embodied.unity.QRVPNConfig.VPNCommand command = 2`
  - `string vpn_id = 3`
  - `string url = 4`
  - `string username = 5`
  - `string password = 6`
  - `bool connect = 7`
  - `string software_version = 100`
  - `string module_name = 101`
  - **enum `QRVPNConfig.VPNCommand`** — `UNKNOWN_VPN_COMMAND=0`, `VPN_DOWNLOAD=1`, `VPN_REVERT=2`, `VPN_CREDENTIALS=3`, `VPN_ACTIVATE=4`, `VPN_DEACTIVATE=5`

### `embodied/wifiapp/WifiAppBricked.proto`

- **`WifiAppBricked`**
  - `uint64 timestamp = 1`
  - `uint32 error_code = 2`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/wifiapp/WifiAppShutdown.proto`

- **`WifiAppShutdown`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/wifiapp/WifiAppSilentBoot.proto`

- **`WifiAppSilentBoot`**
  - `uint64 timestamp = 1`
  - `string software_version = 100`
  - `string module_name = 101`

### `embodied/wifiapp/WifiAppStatus.proto`

- **`WifiAppStatus`**
  - `uint64 timestamp = 1`
  - `uint32 code = 2`
  - `string software_version = 100`
  - `string module_name = 101`


---
📖 [Reverse-engineering index](../README.md) · [recovered-proto/](recovered-proto/) · [protoref tool](../../../tools/robot-toolkit/moxie_toolkit/protoref.py)