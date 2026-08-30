# 🗺️ Architecture diagrams — the whole system, top to bottom

> A hierarchy of diagrams for **Moxie `v3.6.4-Zephyr` / OTA `v24.10.803`** (RK3288, Android 9), from
> the product ecosystem down to motor drivers and hardware buses. Every box is grounded in the
> reverse-engineering in this folder; each level links to its deep doc. GitHub renders these natively.

**Levels:** [L0 Product](#l0-product-ecosystem) · [L1 Software stack](#l1-on-robot-software-stack) ·
[L2 Component bus](#l2-on-device-component-bus) · [L3 Interaction loop](#l3-the-interaction-loop) ·
[L4 Cloud transport](#l4-cloud-transport) · [L5 Hardware topology](#l5-hardware-topology) ·
[L6 Actuation & sensing](#l6-actuation-sensing-the-lizard-mcu) · [L7 Boot chain](#l7-boot-chain-lifecycle)

---

## L0 — Product ecosystem

The four actors and how they connect.

```mermaid
flowchart TB
    child(["🧒 Child / mentor"])
    parent(["📱 Parent app<br/>(phone)"])
    subgraph robot["🤖 Moxie robot"]
        exp["Experience<br/>face · voice · motion"]
    end
    subgraph cloud["☁️ Cloud backend"]
        direction LR
        rest["REST<br/>client-service"]
        mqtt["MQTT broker"]
        stt["STT<br/>Deepgram"]
        brain["Conversation<br/>+ content"]
    end
    child <-->|"speak · touch · show QR"| robot
    parent -->|"pairing QR<br/>Wi-Fi + secret"| robot
    parent <-->|"account · controls"| rest
    robot <-->|"MQTT · REST · STT/WSS"| cloud
    mqtt -.- brain
    rest -.- brain
    stt -.- brain
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    classDef a fill:#fff3e0,stroke:#e08a3c,color:#4a2f10;
    class exp,rest,mqtt,stt,brain d;
    class child,parent a;
```

Deep docs: [`cloud-protocol.md`](protocol/cloud-protocol.md) · [`qr-format.md`](phone/qr-format.md) · [`rest-api.md`](phone/rest-api.md)

---

## L1 — On-robot software stack

Layers inside the robot, from AOSP up to the experience.

```mermaid
flowchart TB
    subgraph exp["Experience layer"]
        boa["bo-android<br/>the brain + Unity face"]
        bwf["bo-wifi<br/>setup · QR · pairing"]
        osu["OSUpdate / BoUpdater<br/>A/B OTA"]
        lch["Launcher3Robot<br/>+ me.embodied Launcher"]
    end
    subgraph emb["Embodied daemons"]
        led["ledctrld"]
        fan["projectorfanpid"]
        xmos["bo-xmos-wd / xmosdfu"]
        fw["bo-firmwareUpdate<br/>(MCU DFU)"]
    end
    subgraph hal["Vendor HAL (Rockchip)"]
        cam["camera@2.4"]
        aud["audio@2.0"]
        gfx["gralloc / composer"]
        wifi["wifi@1.0 · BT"]
        km["keymaster@3.0"]
    end
    subgraph os["AOSP 9 · system-as-root · A/B · AVB"]
        init["init + SELinux"]
        art["ART / zygote32"]
    end
    exp --> emb --> hal --> os
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    class boa,bwf,osu,lch,led,fan,xmos,fw,cam,aud,gfx,wifi,km,init,art d;
```

Deep docs: [`firmware-803-reference.md`](firmware/firmware-803-reference.md) · [`firmware-image.md`](firmware/firmware-image.md) · [`boot-and-launcher.md`](firmware/boot-and-launcher.md)

---

## L2 — On-device component bus

The `bo-*` components wired by a ZeroMQ pub/sub proxy (`libbo-dispatch`), plus the MCU and cloud bridges.
Each message is two frames: `[descriptor FullName][protobuf]`.

```mermaid
flowchart LR
    subgraph bus["ZeroMQ dispatch — XSUB :5678 / XPUB :6789"]
        broker(["ZMQEventBroadcaster"])
    end
    lizard["Lizard MCU<br/>(UART)"]
    fwup["bo-firmwareUpdate"]
    audio["BO_AUDIO<br/>XMOS · STT · TTS"]
    vision["BO_VISION<br/>faces · people · QR"]
    fusion["BO_FUSION"]
    brain["BO_BRAIN<br/>ChatScript · ML"]
    unity["BO_MAINAPP<br/>Unity face"]
    wifi["BO_CFGAPP<br/>bo-wifi"]
    logger["BO_LOGGER<br/>MQTT bridge"]
    sysmon["BO_SYSMON"]
    updater["BO_UPDATER"]
    cloud(["☁️ cloud (MQTT)"])

    lizard <-->|proto over UART| fwup
    fwup <--> broker
    audio <--> broker
    vision <--> broker
    fusion <--> broker
    brain <--> broker
    unity <--> broker
    wifi <--> broker
    sysmon <--> broker
    updater <--> broker
    broker <--> logger
    logger <-->|"/devices/{id}/…"| cloud
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    classDef b fill:#e8f0e3,stroke:#6a8f4a,color:#243218;
    class audio,vision,fusion,brain,unity,wifi,logger,sysmon,updater,fwup d;
    class broker b;
```

Deep docs: [`robot-ipc-protocol.md`](protocol/robot-ipc-protocol.md) · [`recovered-proto/`](protocol/recovered-proto/)

---

## L3 — The interaction loop

One conversational turn, end to end.

```mermaid
sequenceDiagram
    participant C as 🧒 Child
    participant X as XMOS DSP
    participant V as Vision
    participant B as Brain
    participant K as Cloud
    participant T as TTS
    participant M as Face + Motors
    C->>X: "Hey, Moxie" + speech
    X->>X: AEC · beamform · VAD · DOA
    X->>K: stream audio (Deepgram WSS)
    K-->>B: STTPartial/Final (text, speaker)
    V-->>B: FacesDetected · Gaze · engaged?
    B->>K: RemoteChatRequest (speech, context, user)
    K-->>B: ChatResponse (text + <mark cmd:…> markup)
    B->>T: CloudTTSRequest (markup)
    T-->>M: CloudTTSResponse (PCM audio + TTSMarks)
    B->>M: behaviour-tree / mood / gesture cmds
    M-->>C: speak + move + emote (lip-sync to marks)
```

Deep docs: [`perception-pipeline.md`](runtime/perception-pipeline.md) · [`content-and-conversation.md`](runtime/content-and-conversation.md) · [`behavior-markup.md`](runtime/behavior-markup.md)

---

## L4 — Cloud transport

MQTT topic structure (Google IoT-Core convention) + device auth.

```mermaid
flowchart TB
    subgraph robot["🤖 Robot (client_id = device path)"]
        keys["RS256 keypair<br/>(KeyMaker)"]
    end
    subgraph broker["MQTT broker (:8883, TLS)"]
        direction TB
        ev["/devices/{id}/events/{name}"]
        st["/devices/{id}/state"]
        cfg["/devices/{id}/config"]
        cmd["/devices/{id}/commands/{cmd}"]
        zmq["/devices/{id}/commands/zmq<br/>name:bytes → inject on bus"]
    end
    server["Backend<br/>subscribes /devices/+/events/#"]
    robot -->|"publish (JWT=RS256 pw)"| ev
    robot --> st
    cfg -->|"ServiceConfiguration"| robot
    cmd -->|"JSON commands"| robot
    zmq -->|"any embodied.* proto"| robot
    ev --> server
    st --> server
    server --> cfg
    server --> cmd
    server --> zmq
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    class ev,st,cfg,cmd,zmq,server,keys d;
```

Deep docs: [`cloud-protocol.md`](protocol/cloud-protocol.md) · [`network-trust.md`](protocol/network-trust.md)

---

## L5 — Hardware topology

The RK3288 SoC and every peripheral, from the device tree (`rk3288-robot`, see [`device-tree.md`](hardware/device-tree.md)).

```mermaid
flowchart TB
    subgraph soc["Rockchip RK3288 SoC (ARMv7 · Mali-T764 · Android 9)"]
        cpu["4× Cortex-A12"]
        emmc["eMMC (dwmmc)"]
        vop["VOP display ctrl"]
    end
    subgraph i2c["I²C buses"]
        rk808["RK808 PMIC<br/>i2c0 @0x1b"]
        cams["OV2710 @0x36<br/>GC2053 @0x37<br/>i2c3"]
        pca["PCA9635 @0x60<br/>i2c4 → 6× RGB LED"]
        dlpc["DLPC3430 @0x1b<br/>+ HX7027 @0x48<br/>i2c5"]
        rt5640["RT5640 codec"]
    end
    subgraph ser["UART"]
        lizard["Lizard STM32 MCU<br/>uart3 /dev/ttyS3<br/>motors·touch·IMU·LEDs·batt"]
        con["debug console<br/>uart2 ttyFIQ0"]
    end
    subgraph usb["USB"]
        xmos["XMOS DSP<br/>mic array · AEC · wakeword"]
    end
    mics["mic array"]
    spk["speaker"]
    face["projected DLP face"]

    cpu --- emmc
    cpu --- rk808
    cpu -->|RKISP1 ISP| cams
    cpu --- pca
    cpu --- dlpc
    cpu -->|I²S| rt5640
    cpu -->|"lizzerface proto"| lizard
    cpu --- con
    cpu --- xmos
    vop -->|"24-bit RGB parallel (simple-panel)"| dlpc
    dlpc -->|light engine| face
    xmos --- mics
    rt5640 --- spk
    rk808 -.->|"vdd_cpu/gpu/ddr/io · vcc_lcd · vcc_wl"| soc
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    classDef h fill:#f3e3ea,stroke:#a05070,color:#3a1424;
    class cpu,emmc,vop d;
    class rk808,cams,pca,dlpc,rt5640,lizard,con,xmos,mics,spk,face h;
```

Deep docs: [`hardware-map.md`](hardware/hardware-map.md) · [`firmware-803-reference.md`](firmware/firmware-803-reference.md)

---

## L6 — Actuation & sensing (the Lizard MCU)

What the MCU drives and reports over UART (the `embodied.lizzerface` protocol).

```mermaid
flowchart LR
    soc["RK3288 (Android)"] <-->|"UART · lizzerface proto"| mcu["Lizard MCU"]

    subgraph motors["Motors (set-position + per-motor PID)"]
        arms["L/R arm: up-dn · in-out"]
        head["head: up-dn · L-R · tilt"]
        body["squish · base L-R · torso F-B"]
    end
    subgraph sensors["Sensors → events"]
        touch["touch: BACK · TUMMY · L/R hand"]
        sw["switches: arm limits · DC_PLUG"]
        imu["IMU/MPU: picked_up · putdown · tilt"]
        misc["flap · light · battery"]
    end
    subgraph out["Actuators"]
        ledr["face LEDs (LedrPattern:<br/>bootup · listen · process · low-bat · privacy)"]
        rails["power rails: 12V · 3V3 · 5V · LCOS · mute · speaker"]
    end

    mcu -->|MotorSetPosEventPB<br/>ConfigureMotorEventPB| motors
    motors -->|"ServoPosFdback · ServoStall"| mcu
    sensors -->|"Touch/Switch/Mpu/Battery EventPB"| mcu
    mcu --> ledr
    mcu --> rails
    mcu -->|LizardErrorEventPB<br/>1000-1051| soc
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    classDef h fill:#f3e3ea,stroke:#a05070,color:#3a1424;
    class soc,mcu d;
    class arms,head,body,touch,sw,imu,misc,ledr,rails h;
```

Motor PID params (`ConfigParam`): `KP KI KD MAX_PWM KI_LEAK LIMIT ADJ MOTOR_FWD/RWD WRITE`.
Deep doc: [`hardware-map.md`](hardware/hardware-map.md)

---

## L7 — Boot chain & lifecycle

From power-on to the running experience, and the Launcher states.

```mermaid
flowchart LR
    mask["maskrom /<br/>loader"] --> ub["U-Boot<br/>(uboot.img)"]
    ub --> tr["Trust / OP-TEE<br/>(trust.img)"]
    tr --> avb{"AVB verify<br/>vbmeta (enforcing)"}
    avb -->|slot A/B| kern["kernel + ramdisk<br/>(boot.img)"]
    kern --> initrc["init · SELinux<br/>system-as-root"]
    initrc --> lnch["me.embodied Launcher"]
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    class mask,ub,tr,kern,initrc,lnch d;
```

```mermaid
stateDiagram-v2
    [*] --> STATE_INIT
    STATE_INIT --> STATE_STARTUP
    STATE_STARTUP --> STATE_CONFIG: not paired / offline
    STATE_STARTUP --> STATE_RUNNING: paired + online
    STATE_CONFIG --> STATE_RUNNING: paired via QR + online
    STATE_RUNNING --> STATE_CONFIG: lost internet (QR-reading)
    STATE_RUNNING --> STATE_RECOVERY: user-data recovery
    STATE_RUNNING --> STATE_TELEBRAIN: telehealth
    STATE_RUNNING --> STATE_SUSPEND
    STATE_SUSPEND --> STATE_LIGHT_SLEEP
    STATE_STARTUP --> STATE_SILENT_REBOOT: apply OTA
    STATE_RUNNING --> STATE_SHUTDOWN
```

Deep docs: [`boot-and-launcher.md`](firmware/boot-and-launcher.md) · [`firmware-image.md`](firmware/firmware-image.md) · [`ota-and-recovery.md`](firmware/ota-and-recovery.md)

---

📖 [Reverse-engineering index](README.md) · [Field guide](FIELD-GUIDE.md) · [Firmware reference](firmware/firmware-803-reference.md) · [Docs index](../README.md)
