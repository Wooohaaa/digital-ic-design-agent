"""Data-driven target profiles for the vendor-neutral implementation layer.

A *target* describes what a project is being built for -- vendor, device family,
constraint conventions and which implementation backend can compile it -- as
plain data instead of hard-coded Anlogic logic. The flow resolves a target once
and every implementation backend reads the same profile, so the tool is no
longer tied to one vendor.

Honesty contract:

* Profiles only *describe* a target. A profile must never be read as a claim
  that the target is implemented, placed, routed or programmed.
* Capabilities are declared per profile: the ``generic`` profile synthesizes but
  has no place-and-route or bitstream step, and says so instead of pretending.
* Unknown target ids raise instead of silently falling back to a different
  vendor. One compatibility exception: a manifest ``target`` that records a
  legacy human-readable Anlogic device description (for example
  ``"Anlogic PH1P35MDG324"``) is read as the default Anlogic profile, because
  such a string names the Anlogic device rather than a profile id.
"""
from __future__ import annotations

TARGET_VERSION = 1

BACKENDS = ("anlogic", "ice40", "generic")

BUILTIN_TARGETS: dict[str, dict] = {
    "anlogic-hx1p35a": {
        "id": "anlogic-hx1p35a",
        "vendor": "Anlogic",
        "backend": "anlogic",
        "board": "HX1P35A",
        "device": "PH1P35MDG324",
        "family": "ph1",
        "tool": "Anlogic TD",
        "bitstream_extension": ".bit",
        "constraints": {"pins": "constraints/pins.adc", "timing": "constraints/clock.sdc"},
        "capabilities": {"synthesis": True, "place_route": True, "bitstream": True},
    },
    "lattice-ice40-hx1k": {
        "id": "lattice-ice40-hx1k",
        "vendor": "Lattice",
        "backend": "ice40",
        "board": "iCEstick",
        "device": "iCE40HX1K",
        "family": "ice40",
        "package": "tq144",
        "nextpnr_device": "hx1k",
        "tool": "Yosys + nextpnr-ice40 + icepack",
        "bitstream_extension": ".bin",
        "constraints": {"pins": "constraints/pins.pcf"},
        "capabilities": {"synthesis": True, "place_route": True, "bitstream": True},
    },
    "generic": {
        "id": "generic",
        "vendor": "vendor-neutral",
        "backend": "generic",
        "board": None,
        "device": "technology-independent",
        "family": "generic",
        "tool": "Yosys",
        "bitstream_extension": None,
        "constraints": {},
        "capabilities": {"synthesis": True, "place_route": False, "bitstream": False},
    },
}

DEFAULT_TARGET = "anlogic-hx1p35a"


def list_targets() -> list[str]:
    return list(BUILTIN_TARGETS)


def get_target(target_id: str) -> dict:
    if not isinstance(target_id, str) or target_id not in BUILTIN_TARGETS:
        raise ValueError(f"unknown target profile: {target_id!r}; known targets: {', '.join(list_targets())}")
    return {key: (dict(value) if isinstance(value, dict) else value)
            for key, value in BUILTIN_TARGETS[target_id].items()}


def validate_target(profile: dict) -> None:
    if not isinstance(profile, dict):
        raise ValueError("target profile must be an object")
    for key in ("id", "vendor", "backend", "capabilities"):
        if key not in profile:
            raise ValueError(f"target profile is missing {key!r}")
    if profile["backend"] not in BACKENDS:
        raise ValueError(f"target profile backend must be one of {', '.join(BACKENDS)}")
    capabilities = profile["capabilities"]
    if not isinstance(capabilities, dict) or not set(capabilities) >= {"synthesis", "place_route", "bitstream"}:
        raise ValueError("target capabilities must declare synthesis, place_route and bitstream")
    if any(type(value) is not bool for value in capabilities.values()):
        raise ValueError("target capabilities must be booleans")


def resolve_target(manifest: dict, *, target: str | None = None) -> dict:
    """Resolve the selected target from an explicit id, the manifest ``target`` key, or the default."""
    if target is not None and not isinstance(target, str):
        raise ValueError("target must be a profile id string")
    section = manifest.get("target")
    if target is None:
        if isinstance(section, str):
            # Pre-stage-2 manifests recorded a human-readable device description
            # (e.g. "Anlogic PH1P35MDG324") in `target`. An Anlogic description
            # is read as the default Anlogic profile so existing projects keep
            # working; any other unknown id still raises so a typo for a
            # non-Anlogic target cannot silently switch vendors.
            if section in BUILTIN_TARGETS:
                target = section
            elif "anlogic" in section.lower():
                target = DEFAULT_TARGET
            else:
                raise ValueError(
                    f"unknown target profile: {section!r}; known targets: {', '.join(list_targets())}")
        elif isinstance(section, dict):
            target = section.get("id")
            if target is None:
                raise ValueError("manifest target object is missing a profile id")
        elif section is None:
            target = DEFAULT_TARGET
        else:
            raise ValueError("manifest target must be a profile id or an object")
    profile = get_target(target)
    if isinstance(section, dict) and section.get("id") in (None, profile["id"]):
        for key in ("package", "device", "board", "device_family", "family"):
            value = section.get(key)
            if isinstance(value, str) and value.strip():
                profile[key] = value
    validate_target(profile)
    return profile
