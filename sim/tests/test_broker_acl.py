"""
Broker ACL tests — security-broker-auth.md §2 (P0), rows T1-T3 and T8's ACL half.

Pure: no broker, no Docker, no network. Two halves.

  * `render_acl` — the permit-derived ACL (§2.3), generated now and inert until P1.
    Byte-stable output, one `user d_<uuid>` block per permitted device, and — the
    property the whole floor rests on — **no bare `topic` line before the first `user`
    block**, so an anonymous client's only grants are its own `%c` subtree.
  * the SHIPPED broker files — `mqtt/broker/{acl,acl-robot,mosquitto.conf,
    compose-mosquitto.conf}`. A pure text read, but it is the read that catches the two
    ways this slice can silently become a no-op: an ACL that stops being loaded, and a
    `user` block on a listener with no `password_file` (where mosquitto matches a
    username nobody verified — proven against eclipse-mosquitto 2.0.20 while this was
    written, which is why there are two ACL files rather than one).

The end-to-end proof that a real broker enforces all this is `sim/run_acl_proof.sh`.
"""
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.broker_acl import PATTERN_FLOOR, render_acl   # noqa: E402

BROKER = os.path.join(REPO, "mqtt", "broker")


def read(name):
    with open(os.path.join(BROKER, name)) as fh:
        return fh.read()


def directives(text):
    """Config/ACL lines with comments and blanks stripped."""
    return [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]


# ---- T1: the pattern floor ---------------------------------------------------------

def test_pattern_floor_is_the_four_documented_lines():
    body = directives(render_acl({}))
    assert body[:4] == list(PATTERN_FLOOR)
    assert body[4:] == ["user supervisor",
                        "topic readwrite /devices/#",
                        "topic read      $SYS/#"]


def test_no_bare_topic_grant_before_the_first_user_block():
    """The property that makes an anonymous client empty-handed: every grant it can
    reach is a `%c` pattern. One stray `topic read /devices/#` above the first `user`
    would hand every LAN client the whole fleet, and nothing else in the file would
    look different."""
    for permits in ({}, {"devices": {"d_1": {}}},
                    {"allow_unverified_bots": True, "devices": {"d_1": {}}}):
        seen_user = False
        for line in directives(render_acl(permits)):
            if line.startswith("user "):
                seen_user = True
            elif line.startswith("topic "):
                assert seen_user, f"bare `{line}` before any user block: {permits}"


def test_sys_is_supervisor_only():
    body = render_acl({"devices": {"d_a": {}, "d_b": {}}})
    sys_lines = [l for l in directives(body) if "$SYS" in l]
    assert sys_lines == ["topic read      $SYS/#"]
    # ...and it sits inside the supervisor's block, not a device's.
    blocks = body.split("\nuser ")
    assert "$SYS" in blocks[1] and blocks[1].startswith("supervisor")
    assert not any("$SYS" in b for b in blocks[2:])


def test_supervisor_user_is_configurable():
    assert "user hivemind" in render_acl({}, supervisor_user="hivemind")
    assert "user supervisor" not in render_acl({}, supervisor_user="hivemind")


# ---- T2: permit-derived blocks -----------------------------------------------------

PERMITS = {
    "allow_unverified_bots": False,
    "devices": {
        "d_9f1b2c3d-0000-4000-8000-aabbccddeeff": {"permitted_at": 1788353318,
                                                   "label": "Sam's Moxie"},
        "d_0a1b2c3d-0000-4000-8000-112233445566": {"permitted_at": 1788353319,
                                                   "label": "Robin's Moxie"},
    },
}


def test_one_user_block_per_permitted_device():
    body = render_acl(PERMITS)
    for device_id in PERMITS["devices"]:
        assert f"user {device_id}\ntopic readwrite /devices/{device_id}/#" in body
    assert body.count("\nuser ") == 3            # supervisor + two devices


def test_revoking_removes_exactly_that_block():
    full = render_acl(PERMITS)
    revoked = dict(PERMITS, devices={k: v for k, v in PERMITS["devices"].items()
                                     if not k.startswith("d_9f1b")})
    partial = render_acl(revoked)
    assert "d_9f1b2c3d-0000-4000-8000-aabbccddeeff" not in partial
    assert "d_0a1b2c3d-0000-4000-8000-112233445566" in partial
    assert len(full.splitlines()) - len(partial.splitlines()) == 3   # blank + 2 lines


def test_output_is_byte_stable_and_order_independent():
    """A writer that re-renders on every permit change must be able to skip the write
    when nothing changed — which needs the same dict to produce the same bytes, whatever
    order the keys arrived in."""
    reversed_devices = dict(reversed(list(PERMITS["devices"].items())))
    assert render_acl(PERMITS) == render_acl(dict(PERMITS, devices=reversed_devices))
    assert render_acl(PERMITS) == render_acl(PERMITS)
    assert render_acl(PERMITS).endswith("\n")


def test_devices_are_sorted():
    ids = re.findall(r"^user (d_\S+)$", render_acl(PERMITS), re.M)
    assert ids == sorted(ids)


def test_unverified_bots_mode_is_recorded_but_still_confines():
    open_body = render_acl({"allow_unverified_bots": True, "devices": {"d_a": {}}})
    assert "allow_unverified_bots is ON" in open_body
    # The floor is identical: an open appliance is a SERVICE decision, not a bus one.
    assert directives(open_body)[:4] == list(PATTERN_FLOOR)
    assert directives(open_body) == directives(render_acl({"devices": {"d_a": {}}}))


def test_empty_and_malformed_permits_render_the_floor_alone():
    floor = render_acl({})
    for junk in (None, [], "nope", {"devices": None}, {"devices": []}):
        assert render_acl(junk) == floor


# ---- T3: injection -----------------------------------------------------------------


def test_a_hostile_device_id_cannot_forge_an_acl_line():
    """The permit record is written by an HTTP handler. A device id carrying a newline
    must not be able to add `topic readwrite /devices/#` to a file mosquitto parses line
    by line."""
    hostile = {
        "d_ok": {},
        "d_evil\ntopic readwrite /devices/#": {},
        "d_evil2\nuser supervisor": {},
        "d_sp ace": {},
        "": {},
        "x" * 200: {},
        "d_tab\there": {},
    }
    body = render_acl({"devices": hostile})
    assert "user d_ok" in body
    assert directives(body).count("topic readwrite /devices/#") == 1   # supervisor's own
    assert directives(body).count("user supervisor") == 1
    for line in directives(body):
        assert line.startswith(("pattern ", "user ", "topic ")), line
    assert "6 device id(s) omitted" in body


def test_non_string_device_ids_are_dropped():
    body = render_acl({"devices": {"d_ok": {}, 7: {}, None: {}}})
    assert "user d_ok" in body
    assert "2 device id(s) omitted" in body


# ---- the shipped broker files ------------------------------------------------------

CONFS = ("mosquitto.conf", "compose-mosquitto.conf")


def test_the_shipped_robot_acl_is_exactly_the_rendered_floor():
    """`acl-robot` is the strict floor and nothing else — the same four lines
    `render_acl` emits, so the P1 generator cannot drift from what ships today."""
    assert directives(read("acl-robot")) == list(PATTERN_FLOOR)


def test_the_shipped_console_acl_is_the_floor_plus_a_named_observer():
    body = directives(read("acl"))
    assert body[:4] == list(PATTERN_FLOOR)
    # The browser SIM is a LAN-visible observer, on purpose and only here.
    assert "topic read  /devices/#" in body
    assert "topic write /devices/d_sim/events/#" in body
    assert "user supervisor" in body
    # ...but it must never be able to drive a real robot or enumerate the fleet.
    assert not any(l.startswith("topic write /devices/#") for l in body)
    sys_lines = [l for l in body if "$SYS" in l]
    assert sys_lines == ["topic read      $SYS/#"]
    assert body.index("topic read      $SYS/#") > body.index("user supervisor")



def test_every_listener_loads_an_acl():
    for name in CONFS:
        listeners = _listeners(read(name))
        assert listeners, name
        for port, body in listeners.items():
            assert any(l.startswith("acl_file ") for l in body), \
                f"{name}: listener {port} loads no acl_file — it would be wide open"


def test_a_user_block_is_only_ever_reachable_behind_a_password_file():
    """The reason there are two ACL files. On a listener with no `password_file`,
    mosquitto accepts ANY username unchecked and then matches it against the ACL's
    `user` blocks — so `user supervisor` on the robot listener would hand the fleet to
    anyone who typed the word. Verified against eclipse-mosquitto 2.0.20."""
    with_user = {name for name in ("acl", "acl-robot")
                 if any(l.startswith("user ") for l in directives(read(name)))}
    assert with_user == {"acl"}
    for name in CONFS:
        for port, body in _listeners(read(name)).items():
            acl = [l.split(None, 1)[1] for l in body if l.startswith("acl_file ")]
            has_pw = any(l.startswith("password_file ") for l in body)
            wants_auth = any(a.rsplit("/", 1)[-1] in with_user for a in acl)
            assert wants_auth <= has_pw, \
                (f"{name}: listener {port} loads {acl} (which has `user` blocks) with no "
                 f"password_file — the supervisor identity would be spoofable")


def test_the_robot_listener_never_carries_a_password_file():
    """A real Moxie's MQTT password is an RS256 JWT (cloud-protocol.md E3/E4) and its
    username is unknown (assumption A2). A `password_file` on 8883 refuses it — this is
    the assertion that stops a future 'tidy-up' from bricking a fleet."""
    for name in CONFS:
        body = _listeners(read(name))["8883"]
        assert not any(l.startswith("password_file ") for l in body), name
        assert "acl_file /mosquitto/config/acl-robot" in body, name


def test_security_is_per_listener():
    """`per_listener_settings true` is what makes the split above possible at all; it
    must come before the first listener or mosquitto reads the settings as global."""
    for name in CONFS:
        lines = directives(read(name))
        assert "per_listener_settings true" in lines, name
        assert lines.index("per_listener_settings true") < \
            min(i for i, l in enumerate(lines) if l.startswith("listener ")), name


def test_the_supervisor_credential_is_loaded_where_the_supervisor_connects():
    for name in CONFS:
        body = _listeners(read(name))["1883"]
        assert any(l.startswith("password_file ") for l in body), name
        assert "allow_anonymous true" in body, \
            f"{name}: the SIM and virtual_moxie are anonymous clients of 1883"


def test_the_plain_listener_is_loopback_bound_on_bare_metal():
    assert "listener 1883 127.0.0.1" in read("mosquitto.conf")


def _listeners(text):
    """`{port: [directive, ...]}` — the lines under each `listener` line."""
    out, current = {}, None
    for line in directives(text):
        if line.startswith("listener "):
            current = line.split()[1]
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


# ====================================================================================
# The supervisor credential (§2.2) — config.py knobs and what `_build_client` does
# with them. Pure: paho is never given a socket.
# ====================================================================================

import importlib                                             # noqa: E402

import pytest                                                # noqa: E402

sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

CRED_ENV = ("MOXIE_MQTT_USER", "MOXIE_MQTT_PASSWORD", "MOXIE_MQTT_PASSWORD_FILE")


@pytest.fixture
def fresh_config(monkeypatch):
    """Import `config` with a controlled credential environment (it caches at import)."""
    def _load(**env):
        for key in CRED_ENV:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        import config as _c
        return importlib.reload(_c)
    yield _load
    for key in CRED_ENV:
        monkeypatch.delenv(key, raising=False)
    import config as _c
    importlib.reload(_c)


def test_unset_credentials_mean_anonymous(fresh_config):
    """Today's behaviour, byte for byte: a bare-metal dev broker and the SIL harness
    have no password file, and must keep working."""
    c = fresh_config()
    assert (c.MQTT_USERNAME, c.MQTT_PASSWORD, c.MQTT_PASSWORD_FILE) == ("", "", "")
    assert c.broker_credentials() == ("", "")


def test_a_username_without_a_password_is_not_credentials(fresh_config):
    """Half a credential is worse than none: mosquitto refuses a known username with no
    password, so sending one would break a connection that anonymous would have made."""
    assert fresh_config(MOXIE_MQTT_USER="supervisor").broker_credentials() == ("", "")
    assert fresh_config(MOXIE_MQTT_PASSWORD="s3cret").broker_credentials() == ("", "")


def test_the_literal_password_is_used(fresh_config):
    c = fresh_config(MOXIE_MQTT_USER="supervisor", MOXIE_MQTT_PASSWORD="s3cret")
    assert c.broker_credentials() == ("supervisor", "s3cret")


def test_the_password_file_is_read(fresh_config, tmp_path):
    """How compose hands the minted secret over: a 0600 file in the shared volume, so
    the value never appears in `docker inspect` or in a process listing."""
    secret = tmp_path / "supervisor.pass"
    secret.write_text("minted-by-the-certs-one-shot\n")
    c = fresh_config(MOXIE_MQTT_USER="supervisor", MOXIE_MQTT_PASSWORD_FILE=str(secret))
    assert c.broker_credentials() == ("supervisor", "minted-by-the-certs-one-shot")


def test_an_explicit_literal_beats_the_file(fresh_config, tmp_path):
    secret = tmp_path / "supervisor.pass"
    secret.write_text("from-the-file")
    c = fresh_config(MOXIE_MQTT_USER="supervisor", MOXIE_MQTT_PASSWORD="from-the-env",
                     MOXIE_MQTT_PASSWORD_FILE=str(secret))
    assert c.broker_credentials() == ("supervisor", "from-the-env")


def test_a_missing_password_file_degrades_to_anonymous(fresh_config, tmp_path, capsys):
    """An unreadable file must not be fatal. A broker with no `password_file` expects an
    anonymous client anyway, and 'connection refused' is far harder to diagnose than a
    line saying which file could not be read."""
    c = fresh_config(MOXIE_MQTT_USER="supervisor",
                     MOXIE_MQTT_PASSWORD_FILE=str(tmp_path / "nope.pass"))
    assert c.broker_credentials() == ("", "")
    assert "MOXIE_MQTT_PASSWORD_FILE" in capsys.readouterr().out


def test_the_password_file_is_never_echoed(fresh_config, tmp_path, capsys):
    secret = tmp_path / "supervisor.pass"
    secret.write_text("do-not-print-me")
    fresh_config(MOXIE_MQTT_USER="supervisor",
                 MOXIE_MQTT_PASSWORD_FILE=str(secret)).broker_credentials()
    assert "do-not-print-me" not in capsys.readouterr().out


def _runtime_client(**env):
    """A real `MoxieRuntime._build_client()` under a controlled environment."""
    pytest.importorskip("paho.mqtt.client")
    import config, importlib as _il                           # noqa: E402
    from moxie_sdk.apps import EchoApp                        # noqa: E402
    from moxie_runtime import MoxieRuntime                    # noqa: E402
    saved = {k: os.environ.get(k) for k in CRED_ENV}
    try:
        for key in CRED_ENV:
            os.environ.pop(key, None)
        os.environ.update(env)
        _il.reload(config)
        return MoxieRuntime(EchoApp())._build_client()
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        _il.reload(config)


def test_build_client_stays_anonymous_when_unconfigured():
    client = _runtime_client()
    assert client._username is None and client._password is None


def test_build_client_authenticates_when_configured(tmp_path):
    secret = tmp_path / "supervisor.pass"
    secret.write_text("minted-secret")
    client = _runtime_client(MOXIE_MQTT_USER="supervisor",
                             MOXIE_MQTT_PASSWORD_FILE=str(secret))
    assert client._username == b"supervisor"
    assert client._password == b"minted-secret"


def test_build_client_still_uses_the_supervisor_client_id():
    """`$SYS/broker/log` names clients by id; the ACL's `user supervisor` block and the
    console's fleet view both assume this one has not moved."""
    assert _runtime_client()._client_id == b"supervisor"
