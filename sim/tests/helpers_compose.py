"""Pure parity helpers for the two compose files — no Docker, no I/O of their own.

The repo ships the same appliance twice:

  * `docker-compose.yml`        — builds the three images from a clone (hacking, ARM,
    the `voice` / `stt` profiles).
  * `docker-compose.images.yml` — self-contained, pulls our published images, and is
    downloaded on its own by an owner who never clones. Because it must stand alone it
    **copies** things instead of referencing them (the broker config is inlined), and
    it repeats the supervisor's whole environment block.

Copies drift: `MOXIE_ALLOW_UNVERIFIED_BOTS` was once forwarded by `docker-compose.yml`
only, each branch's smoke ran only the file it touched, and the prebuilt-image stack came
up refusing to pair.

Every function here takes already-parsed data and returns a list of human-readable
problems (empty == in sync), so the guards in `test_compose.py` can be pointed at the
real files *and* at tiny in-memory fixtures that prove they still bite. The exceptions
are the three readers under "where a published port listens", which resolve a file the
way `docker compose up` would, so a guard can assert where a port really listens.
"""
from __future__ import annotations

import difflib
import re

# `${VAR}` or `${VAR:-default}` — the only interpolation forms these files use.
_INTERP = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$", re.DOTALL)

# Interpolated knobs that legitimately live in ONE file only. Each entry is a claim the
# tests re-verify, not a blanket mute:
#
#   build-time — `docker-compose.yml` bakes extra wheels into the supervisor image via a
#   `build.args` entry. A prebuilt image cannot have wheels added after the fact, so the
#   images file has no counterpart and must not grow one.
BUILD_ONLY_KNOBS = frozenset({"MOXIE_SUPERVISOR_EXTRAS"})
#   registry — which image to pull and from where. Meaningless when you build locally.
IMAGE_ONLY_KNOBS = frozenset({
    "MOXIE_IMAGE_REGISTRY", "MOXIE_IMAGE_TAG", "MOXIE_IMAGE_PULL_POLICY",
})
#   profile — the `voice` / `stt` one-shots exist only in the build-from-clone file.
PROFILE_ONLY_KNOBS = frozenset({"MOXIE_PIPER_VOICE_URL"})


# ---- environment -------------------------------------------------------------------

def moxie_env(compose: dict, service: str) -> dict:
    """The `MOXIE_*` entries of one service's `environment:` mapping, values stringified.

    Only the *explicit* block: `env_file: .env` is a separate, symmetric mechanism that
    forwards whatever the owner wrote, and cannot drift between two files that both
    declare it identically.
    """
    svc = (compose.get("services") or {}).get(service) or {}
    env = svc.get("environment") or {}
    if isinstance(env, list):                     # `- KEY=value` form
        env = dict(item.split("=", 1) if "=" in item else (item, "") for item in env)
    return {k: "" if v is None else str(v) for k, v in env.items() if k.startswith("MOXIE_")}


def passthrough(value: str):
    """`"${MOXIE_APP:-content}"` → `("MOXIE_APP", "content")`; a literal → `(None, value)`.

    `${VAR}` with no default returns `(VAR, None)` — a *required* variable, which is a
    different contract from `${VAR:-}` (optional, empty default), so they must not
    compare equal.
    """
    m = _INTERP.match(value.strip())
    if not m:
        return None, value.strip()
    return m.group(1), m.group(2)


def env_parity(a: dict, b: dict, service: str, *, a_name: str, b_name: str,
               ignore=frozenset()) -> list:
    """Problems keeping `service`'s `MOXIE_*` environment identical across two composes."""
    ea, eb = moxie_env(a, service), moxie_env(b, service)
    problems = []
    for key in sorted((set(ea) | set(eb)) - set(ignore)):
        if key not in eb:
            problems.append(
                f"{service}: {key} is forwarded by {a_name} but MISSING from {b_name} "
                f"(a robot on the {b_name} stack would not see it)")
            continue
        if key not in ea:
            problems.append(
                f"{service}: {key} is forwarded by {b_name} but MISSING from {a_name} "
                f"(a robot on the {a_name} stack would not see it)")
            continue
        va, vb = passthrough(ea[key]), passthrough(eb[key])
        if va == vb:
            continue
        if va[0] and va[0] == vb[0]:
            problems.append(
                f"{service}: {key} passes through ${{{va[0]}}} in both files but the "
                f"DEFAULT differs — {a_name} {va[1]!r} vs {b_name} {vb[1]!r}")
        else:
            problems.append(
                f"{service}: {key} differs — {a_name} sets {ea[key]!r} but "
                f"{b_name} sets {eb[key]!r}")
    return problems


def interpolated(text: str) -> set:
    """Every `MOXIE_*` variable name interpolated anywhere in a blob of compose text."""
    return set(re.findall(r"\$\{(MOXIE_[A-Z0-9_]+)", text))


def env_file_keys(text: str) -> set:
    """Keys declared in a dotenv-style file (`.env.example`)."""
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


# ---- the inlined broker config -----------------------------------------------------

def _normalize_conf(text: str) -> list:
    """Trailing whitespace and trailing blank lines are not signal; `$$` is compose's
    escape for a literal `$` (the config's `$SYS/broker/log` topic needs it)."""
    lines = [line.replace("$$", "$").rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def inlined_broker_conf(compose: dict, config_name: str = "mosquitto-conf") -> list:
    """The mosquitto config inlined into the images compose, normalized.

    Reads it through PyYAML's own block-scalar folding — deliberately a *different*
    extraction path from `sim/run_compose_smoke.sh`, which slices the literal block by
    indentation so it can stay dependency-free. Two independent readings of the same
    bytes agreeing is worth more than one reading twice.
    """
    entry = (compose.get("configs") or {}).get(config_name)
    if entry is None:
        raise AssertionError(f"no `configs:` entry named {config_name!r} — the images "
                             f"compose must inline the broker config to stand alone")
    if "content" not in entry:
        raise AssertionError(f"configs.{config_name} has no inline `content:` — a `file:`"
                             f" reference would break the download-one-file promise")
    return _normalize_conf(entry["content"])


def broker_conf_drift(compose: dict, on_disk: str, *,
                      inline_name: str = "docker-compose.images.yml",
                      file_name: str = "mqtt/broker/compose-mosquitto.conf",
                      config_name: str = "mosquitto-conf") -> str:
    """`""` when in sync, else a unified diff (on-disk → inlined).

    `config_name` selects the inlined block (`mosquitto-conf`, `mosquitto-acl`,
    `mosquitto-acl-robot` all drift the same way).
    """
    inlined = inlined_broker_conf(compose, config_name)
    disk = _normalize_conf(on_disk)
    if inlined == disk:
        return ""
    return "\n".join(difflib.unified_diff(disk, inlined, file_name, inline_name,
                                          lineterm=""))


# ---- service shape -----------------------------------------------------------------

def _named_volume_mounts(svc: dict) -> dict:
    """`{named volume: container path}`. Bind mounts (`./x:/y`) are skipped: the clone
    file bind-mounts the broker config that the images file inlines, which is the whole
    reason the two differ, and neither host path exists on the other side."""
    mounts = {}
    for entry in svc.get("volumes") or []:
        if not isinstance(entry, str):
            continue
        source, _, rest = entry.partition(":")
        if source.startswith(".") or source.startswith("/") or not rest:
            continue
        mounts[source] = rest
    return mounts


def service_shape(compose: dict, service: str) -> dict:
    """The facts about a service that must hold whichever file you brought the stack up
    with: is it healthchecked, what does it wait for, what does it publish, where does
    its state live, is it restarted."""
    svc = (compose.get("services") or {}).get(service) or {}
    hc = svc.get("healthcheck") or {}
    return {
        "healthcheck": hc.get("test"),
        "depends_on": {k: (v or {}).get("condition") if isinstance(v, dict) else None
                       for k, v in (svc.get("depends_on") or {}).items()},
        "ports": [str(p) for p in (svc.get("ports") or [])],
        "volumes": _named_volume_mounts(svc),
        "restart": svc.get("restart"),
        "profiles": list(svc.get("profiles") or []),
    }


_SHAPE_LABEL = {
    "healthcheck": "inline healthcheck",
    "depends_on": "depends_on conditions (startup order)",
    "ports": "published ports (host-port defaults)",
    "volumes": "named-volume container paths",
    "restart": "restart policy",
    "profiles": "profiles",
}


def shape_parity(a: dict, b: dict, services, *, a_name: str, b_name: str) -> list:
    """Problems keeping the given services shaped identically across two composes."""
    problems = []
    for service in services:
        in_a = service in (a.get("services") or {})
        in_b = service in (b.get("services") or {})
        if not in_a or not in_b:
            missing, present = (b_name, a_name) if in_a else (a_name, b_name)
            problems.append(f"service {service!r} is declared in {present} but MISSING "
                            f"from {missing}")
            continue
        sa, sb = service_shape(a, service), service_shape(b, service)
        for field in _SHAPE_LABEL:
            if sa[field] != sb[field]:
                problems.append(
                    f"{service}: {_SHAPE_LABEL[field]} differ — {a_name} has "
                    f"{sa[field]!r} but {b_name} has {sb[field]!r}")
    return problems


# ---- where a published port listens ------------------------------------------------

#: The interpolation forms these files use: `${VAR:-default}` (the default when VAR is
#: unset OR empty), `${VAR-default}` (only when unset) and `${VAR}`.
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-)([^}$]*))?\}")


def dotenv_values(text: str) -> dict:
    """`{KEY: value}` of a dotenv-style file, read the way compose reads `.env`: one
    `KEY=value` per line, blank and `#` lines skipped, one pair of surrounding quotes
    stripped. (No trailing-comment rule: `.env.example` has none, and
    `test_env_example_has_no_trailing_comments` keeps it that way.)"""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def interpolate(value: str, env: dict) -> str:
    """`value` as compose resolves it against `env` (the shell plus `.env`). A `$` form
    other than the three above is refused, not guessed at: a guard built on this must not
    pass by misreading a file."""
    def resolve(m):
        name, op, default = m.groups()
        if op is None:
            return env.get(name, "")
        if op == ":-":
            return env.get(name) or default
        return env[name] if name in env else default

    out = _REF.sub(resolve, value.replace("$$", "\0"))
    if "$" in out:
        raise ValueError(f"cannot resolve {value!r}: a `$` form this reader does not know")
    return out.replace("\0", "$")


def published_binds(compose: dict, service: str, env: dict) -> dict:
    """`{container port: host interface}` for one service's published ports, resolved the
    way `docker compose up` resolves them against `env`. A port with no interface, or one
    that resolves empty, listens on every interface: reported as `0.0.0.0`."""
    svc = (compose.get("services") or {}).get(service) or {}
    binds = {}
    for entry in svc.get("ports") or []:
        if not isinstance(entry, str):
            raise ValueError(f"{service}: {entry!r} is the long port syntax, which this "
                             f"reader does not parse; teach it before trusting the guard")
        parts = interpolate(entry, env).split("/", 1)[0].rsplit(":", 2)
        interface = parts[0].strip("[]") if len(parts) == 3 else ""
        binds[parts[-1]] = interface or "0.0.0.0"
    return binds


def unescaped_dollars(compose: dict, config_name: str = "mosquitto-conf") -> list:
    """Lines of the inlined config where a literal `$` is not written `$$`.

    `broker_conf_drift` normalizes `$$` → `$` before comparing, which means a config that
    forgot to double its `$` compares EQUAL to the on-disk file while docker compose reads
    it as an interpolation and substitutes empty. The broker would then subscribe to
    `SYS/broker/log` instead of `$SYS/broker/log` and the supervisor would never see a
    robot connect or disconnect. The runtime guard in sim/run_compose_smoke.sh shares this
    blind spot; this closes it.
    """
    entry = (compose.get("configs") or {}).get(config_name) or {}
    return [line for line in (entry.get("content") or "").splitlines()
            if "$" in line.replace("$$", "")]
