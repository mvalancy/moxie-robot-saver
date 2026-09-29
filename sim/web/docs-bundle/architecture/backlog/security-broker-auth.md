# Broker authentication: containment now, device identity later

**Status:** P0 (containment) is shipped. It lives in `mqtt/broker/{acl,acl-robot,compose-mosquitto.conf,mosquitto.conf,gen-passwd.sh,docker-certs-init.sh}`, `mqtt/moxie_sdk/broker_acl.py`, `mqtt/config.py` and both compose files. It is tested by `sim/tests/test_broker_acl.py` and `sim/tests/test_compose.py`, and proven against a real mosquitto by `sim/run_acl_proof.sh`. P1 (device credentials the broker verifies) and P2 (spoof refusal) are **proposals, blocked on hardware questions A1–A4**.

**P0 is containment, not authentication.** A client that calls itself `d_1234…` is still believed.
P0 confines every anonymous client to its own device subtree. That stops a LAN device from listing the
fleet or reading another child's config. It does not stop a device from impersonating a robot.

The pairing gate (the permit list, [`mqtt-and-conversation.md` §3.7](../mqtt-and-conversation.md)) sits
above the broker. It decides what a device is *served*, not whether it can *connect*. This page is about
the broker underneath.

**Clean-room.** How a real Moxie authenticates comes from our own RE pages:
[`cloud-protocol.md`](../../reverse-engineering/protocol/cloud-protocol.md),
[`network-trust.md`](../../reverse-engineering/protocol/network-trust.md) and
[`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md). OpenMoxie is prior art, cited by
path. Its `site/hive/mqtt/robot_credentials.py` mints robot JWTs only so a developer can *impersonate* a
robot, and its broker config is `allow_anonymous true` with the comment *"Anyone can login, beware!"*.
We port the verification idea it never built, and none of its code.

## 0. What our corpus establishes

### 0.1 How a real robot connects

| # | Fact | Source |
|---|---|---|
| E1 | On first boot the robot mints an RSA keypair at `…/PERSISTENT_DATA/rightpoint/RS256.key` (and `.key.pub`) | `cloud-protocol.md` |
| E2 | Pairing registers the public key with the backend | same |
| E3 | Every MQTT connect presents, as the password, a **JWT signed with that RS256 key**, with claims `{iat, exp, aud=project}` | same |
| E4 | The username is not meaningful ("anything") | `mqtt-and-conversation.md` |
| E5 | The client id on the wire is `d_<uuid>` (field-proven by the broker-log regexes a working server uses) | same |
| E7 | Server-cert trust is ordinary CA-chain validation with **no pinning**, and `disable_verify` can relax even that | `network-trust.md` |
| E9 | An anonymous broker never checks the JWT; a stricter one could verify it against the registered public key | `cloud-protocol.md` |
| E10 | A robot that boots offline has a wrong clock until NTP arrives, which breaks both TLS and the JWT's `iat`/`exp` | `network-trust.md` |

### 0.2 The endpoint QR cannot carry a credential

The `om` debug QR re-homes a robot by writing a `ServiceConfiguration2` (14 fields: `gcp_project`,
`webservice_root`, `mqtt_host`, `override_port`, `disable_verify`, …). **None of those fields holds an
MQTT username or password.** So no shared secret can be delivered to a stock robot by QR. The pairing
QR carries no broker credential either.

### 0.3 The one cable-free route to a robot's public key

The `report` QR command makes the robot build `QRDiagnosticData{robot_uuid, rsa_pub, …}` and POST it
through `RPTokenURL::post_diagnostics`. Our corpus does **not** say where that POST goes (A3).

### 0.4 Open questions that gate P1

| # | Question | Standing |
|---|---|---|
| A1 | Does an 803 robot present a TLS **client certificate**? Our RE pages disagree. If it does, `require_certificate true` + `use_identity_as_username` beats everything in §3. | open: settle before building P1 |
| A2 | What username string does the robot actually send? | assumed unusable, so a `password_file` cannot work for robots |
| A3 | Where does `report` POST `QRDiagnosticData`? If it follows `webservice_root`, key enrollment is two QR scans and no cable. | open: the highest-value hardware question |
| A4 | Does the JWT header carry the key (`jwk`/`x5c`)? | assumed absent, so trust-on-first-use of the key is impossible |

## 1. The broker as shipped

| Listener | Who uses it | Auth | ACL file |
|---|---|---|---|
| `8883` TLS | real robots | `allow_anonymous true`; **no** `password_file` | [`acl-robot`](../../../mqtt/broker/acl-robot): the `%c` floor only |
| `1883` plain | supervisor, SIL, tests | anonymous **or** `password_file` | [`acl`](../../../mqtt/broker/acl): floor, SIM observer, `user supervisor` |
| `9001` websockets | browser SIM, console live view | anonymous **or** `password_file` | `acl` |

`per_listener_settings true` makes security settings apply per listener. The config appears three times,
and all three are kept in step:

- [`compose-mosquitto.conf`](../../../mqtt/broker/compose-mosquitto.conf) (clone compose);
- [`mosquitto.conf`](../../../mqtt/broker/mosquitto.conf) (bare metal, `listener 1883 127.0.0.1`);
- inlined, with both ACLs, in [`docker-compose.images.yml`](../../../docker-compose.images.yml).
  `test_compose.py` asserts the inlined copies match the files byte-for-byte.

## 2. P0: shipped containment

### 2.1 A `%c` ACL, in two files

mosquitto's `pattern` lines substitute `%c`, the client id. Every client has a client id, whether or
not it authenticated, so per-device confinement is available with no credential:

```conf
pattern write /devices/%c/events/#
pattern write /devices/%c/state
pattern read  /devices/%c/config
pattern read  /devices/%c/commands/#
```

No bare `topic` line appears before the first `user` block, so an anonymous client gets nothing beyond
its own subtree.

**Why there are two files.** On a listener with no `password_file`, mosquitto 2.0.20 accepts **any
username unchecked** and then matches it against the ACL's `user` blocks. A `user supervisor` block on
the robot listener would therefore hand the fleet to anyone who typed the word. The robot listener
cannot carry a password file, because the robot's password is a JWT (E3). So:

- `acl-robot` (TLS `8883`) is the floor alone.
- `acl` (`1883` and `9001`, both of which load `password_file`) adds the SIM observer grant (§2.5) and:

  ```conf
  user supervisor
  topic readwrite /devices/#
  topic read      $SYS/#
  ```

**What this buys:**

1. **Fleet enumeration is closed.** `$SYS/broker/log` announces every `d_<uuid>` and is now
   supervisor-only.
2. **Cross-device access is closed on the robot listener.** A device cannot read another robot's config
   (which includes `child_pii`) or write another robot's state.
3. **It limits damage, not identity.** A spoofed `d_<uuid>` is confined to that robot's subtree, which
   is the subtree it wanted.

An ACL governs topics, not connections. It cannot refuse an unpermitted client id at CONNECT, and a
`user` block matches only a username the broker has verified.

### 2.2 The supervisor's credential

- [`gen-passwd.sh`](../../../mqtt/broker/gen-passwd.sh) is idempotent. It mints 32 random bytes, writes
  `passwd` (`mosquitto_passwd`, mode 0644) and `supervisor.pass` (the plaintext, mode 0600).
- `supervisor.pass` is best-effort `chown`ed to the supervisor image's uid 10001
  (`MOXIE_SUPERVISOR_UID`). If that fails it stays root-owned with mode 0600.
- In compose, the existing `certs` one-shot
  ([`docker-certs-init.sh`](../../../mqtt/broker/docker-certs-init.sh)) runs `gen-passwd.sh` into the
  `moxie-certs` volume. **`docker compose up` is still the whole install.**
- The `certs` init image ([`mqtt/broker/Dockerfile`](../../../mqtt/broker/Dockerfile)) installs
  `mosquitto` for one binary, `mosquitto_passwd`, which writes the PBKDF2-SHA512 hashes. The broker itself
  is still upstream `eclipse-mosquitto:2.0.20`.
- [`mqtt/config.py`](../../../mqtt/config.py) reads `MOXIE_MQTT_USER`, `MOXIE_MQTT_PASSWORD` and
  `MOXIE_MQTT_PASSWORD_FILE`. A literal password beats the file. Compose points the file at
  `/certs/supervisor.pass`, so the secret never appears in `docker inspect`. `broker_credentials()` reads
  them at connect time. If the user or password is unset, or the file is unreadable, the supervisor
  connects anonymously and logs a line, never echoing the secret. So a bare-metal dev broker keeps
  working.
- The supervisor keeps `client_id="supervisor"` and calls `username_pw_set` only when credentials exist
  ([`connection.py`](../../../mqtt/supervisor/moxie_runtime/connection.py)).
- A bare-metal `mosquitto.conf` **will not start without `keys/passwd`**. Run `gen-passwd.sh` once; see
  the [broker README](../../../mqtt/broker/README.md).

### 2.3 The permit-derived ACL: generated, inert until P1

[`broker_acl.render_acl(permits, *, supervisor_user="supervisor")`](../../../mqtt/moxie_sdk/broker_acl.py)
is pure and stdlib-only. It turns `fleet/permits.json` into the strict floor, the `user supervisor`
block, and one `user d_<uuid>` / `topic readwrite /devices/d_<uuid>/#` block per permitted device:

- Device blocks are sorted, so the output is byte-stable.
- Ids must match `SAFE_ID`, so a newline cannot forge an ACL line.
- A malformed record renders the floor alone.

In P0 no robot authenticates, so no `user d_…` block can match. **The file is documentation that
compiles.** It is reachable today only from the CLI
(`python3 -m moxie_sdk.broker_acl $MOXIE_DATA_DIR/fleet/permits.json`). Nothing in the runtime calls it
yet. Wiring it to the permit writers, with a SIGHUP to the broker, belongs to P1, when it becomes the
enforcement point.

### 2.4 The plain listener is loopback-only by default

Both compose files publish `1883` on `${MOXIE_BIND_HOST_PLAIN:-127.0.0.1}`. To drive it from another
machine, set `MOXIE_BIND_HOST_PLAIN=0.0.0.0`. `sim/compose-smoke.env` pins it to `127.0.0.1` explicitly.
`9001` stays on `MOXIE_BIND_HOST`, because that is how a phone or tablet loads the browser SIM.

### 2.5 The browser SIM: option (a) shipped, option (b) closes the residual

`bridge.js` is a console-side observer as well as a robot double. It subscribes to `/devices/+/…`
wildcards, which the `%c` floor denies. There were two options:

- **(a)** A fleet-wide *read* for the websocket listener. **This shipped.** `acl` grants anonymous
  `topic read /devices/#`, plus writes only as the fixed SIM id `d_sim`. A credential embedded in a page
  served to a browser would not be a secret, so the grant is anonymous. `bridge.js` is unchanged.
- **(b)** The SIM subscribes only to its own `d_sim` subtree, and real-robot mirroring moves to the
  console's HTTP API (`/local/*`). **Not built.** This is what closes the residual below.

**Residual exposure (deliberate and documented):** a LAN client on `1883` or `9001` can read
`/devices/#`, including a config push going by. The robot listener does not grant this. Until (b)
exists, the honest mitigation is to publish `9001` only when you use the browser SIM.

### 2.6 Known consequences of P0

- The compose healthcheck (`mosquitto_pub -t healthcheck -q 0`) still exits 0 under the ACL, because
  QoS 0 on MQTT 3.1.1 has no ack. A future MQTT v5 healthcheck would need a grant.
- The SIL-only motor path (`virtual_moxie.py --script` with motors) publishes to its own
  `commands/motor`, which the floor grants read, not write. It runs only against the unhardened SIL broker
  (`sim/broker/ci-mosquitto.conf`, untouched), so nothing breaks today. It is the first evidence for R6.
- The SIL smoke still runs against an open broker. The hardened path is proven by the dedicated
  [`run_acl_proof.sh`](../../../sim/run_acl_proof.sh) and
  [`tools/prove_broker_acl.py`](../../../sim/tools/prove_broker_acl.py), plus both modes of
  [`run_compose_smoke.sh`](../../../sim/run_compose_smoke.sh).

## 3. P1 proposal: device credentials the broker verifies

### 3.1 Options ruled out

| Option | Verdict |
|---|---|
| `password_file` per device | Impossible: the robot sends a JWT and an unknown username (E3, A2). It is correct only for the supervisor and the SIL doubles. |
| A secret in the endpoint QR | There is no field for one (§0.2) |
| TLS client certificates | **Blocked on A1.** If robots present one, this is the best option: no plugin and no enrollment. |
| Trust on first use | The JWT does not carry the key (A4), so there is nothing to trust |
| An ACL `deny` for unpermitted ids | ACLs cannot refuse connections |

### 3.2 Design: mosquitto asks the supervisor

A broker auth plugin in HTTP mode (for example `mosquitto-go-auth`) forwards CONNECT and ACL questions
to new supervisor endpoints, `POST /broker/auth` and `POST /broker/acl`. They are reachable only from the
broker, through a proxy port that is never published. All the logic lives in our code:

- **A new pure module**, `verify_device_jwt(token, pubkey_pem, *, now, leeway_s=300, audience=None)`.
  - The algorithm is pinned to RS256 **inside the verifier**, never taken from the token header.
  - `alg:none` and HS256-signed-with-the-public-key are rejected.
  - `leeway_s = 300` is deliberate: an offline-booted robot has a wrong clock (E10), and locking out a
    child's robot after a power cut is worse than a five-minute replay window.
- **`fleet/permits.json` stays the single source of truth.** It gains optional `pubkey_pem` and
  `require_key` fields per device.
- **Strictly additive.** A device with no enrolled key keeps connecting anonymously and stays
  service-gated exactly as today. Only a `require_key: true` device can be refused.
  `MOXIE_REQUIRE_DEVICE_AUTH=1` would flip the default for owners who have enrolled everything.

### 3.3 Getting a robot's public key

1. **The `report` QR** (no cable, if A3 holds). The robot POSTs `rsa_pub` itself. The receiver
   (`POST /permits/{id}/pubkey`) is cheap to build either way.
2. **The on-screen diagnostic.** It may or may not be machine-readable. Unestablished; do not build for
   it.
3. **ADB pull** of `RS256.key.pub` over USB. This works today but needs a laptop. We need only the
   *public* key; OpenMoxie pulls the private key because it impersonates robots.

### 3.4 Costs

- An auth plugin means a custom broker image, which ends the "broker is upstream mosquitto" property in
  [`RELEASING.md`](../../../RELEASING.md). Settle A1 first.
- A JWT verifier is a new dependency. Keep it to about 40 lines on `cryptography`, with no full JOSE
  stack.

## 4. P2 proposal: refuse a spoofed `d_<uuid>`

With `require_key: true`, `/broker/auth` returns 403 unless the JWT verifies against the enrolled key.
The spoofer is refused at CONNECT, so it never reaches a topic or `$SYS/broker/log` and never appears as
a pending robot.

This is also the only fix for the **client-id collision DoS**. Today anyone who knows a `d_<uuid>` can
repeatedly knock the real robot off the bus, because MQTT evicts the older session. P0 and P1 do not
change that.

The generated ACL (§2.3) becomes live. The console's access card gains a third state, **Verified**,
beside Permitted and Pending.

## 5. Tests

**Shipped (P0):**

| File | Asserts |
|---|---|
| [`sim/tests/test_broker_acl.py`](../../../sim/tests/test_broker_acl.py) | `render_acl` is exactly the four-line floor; no bare `topic` grant before the first `user`; `$SYS` is supervisor-only; one block per permitted device; output byte-stable and sorted; hostile ids cannot forge a line; shipped `acl-robot` equals the rendered floor; shipped `acl` is the floor plus the named observer grant; every listener loads an ACL; a `user` block is reachable only behind a password file; the robot listener never carries one; the plain listener is loopback on bare metal; credential loading (unset → anonymous, file read, literal wins, never echoed); `_build_client` authenticates only when configured |
| [`sim/tests/test_compose.py`](../../../sim/tests/test_compose.py) | The inlined broker config and ACLs match the files; `$` escaping in the inlined copy; both compose files mount the ACLs and the credential |
| [`sim/run_acl_proof.sh`](../../../sim/run_acl_proof.sh) + [`prove_broker_acl.py`](../../../sim/tools/prove_broker_acl.py) | Delivery-based checks against real `eclipse-mosquitto:2.0.20`: the supervisor authenticates and sees `/devices/#` and `$SYS`; wrong or missing passwords are refused; an anonymous robot connects and is confined; `username=supervisor` buys nothing on the robot listener; the SIM keeps its observer read and `d_sim` writes but cannot drive a robot |
| [`sim/run_compose_smoke.sh`](../../../sim/run_compose_smoke.sh) | The full stack comes up with the minted credential, in both compose modes |

**Proposed (P1/P2):** a JWT round-trip plus the negatives (wrong key, mangled signature, `alg:none`, HS256
confusion); the clock leeway (+4 min passes, +10 min fails); the `/broker/auth` and `/broker/acl` verdicts;
key enrollment surviving a restart, with malformed PEMs rejected; and a SIL spoof test in which a second
client reusing a verified id is refused while the original session survives.

## 6. What only a physical robot can answer

1. **A1**: does an 803 robot present a TLS client certificate? One `tcpdump` of a real CONNECT settles it.
2. **A2**: what username does the robot send?
3. **A3**: where does `post_diagnostics` send `QRDiagnosticData`? This decides between cable-free
   enrollment and a laptop per robot.
4. Is the on-screen diagnostic machine-readable?
5. **Does a real robot survive the P0 ACL?** (R6) Our doubles follow the documented topic map. A firmware
   that uses an unrecovered topic would be denied silently.

Until those are answered: P0 stands, P1 would ship as a mechanism with an honest enrollment caveat, and
P2 is a switch only owners who cleared that caveat could use. **No phase claims authentication it has not
performed.**

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [MQTT & conversation](../mqtt-and-conversation.md) · [Config & telemetry contract](../config-and-telemetry-contract.md) · [Network trust](../../reverse-engineering/protocol/network-trust.md) · [Cloud protocol](../../reverse-engineering/protocol/cloud-protocol.md) · [Docs index](../../README.md)
