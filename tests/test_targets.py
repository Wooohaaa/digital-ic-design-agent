from __future__ import annotations

import json
import unittest

from fpga_agent.targets import (
    BACKENDS,
    DEFAULT_TARGET,
    get_target,
    list_targets,
    resolve_target,
    validate_target,
)


class TargetProfileTests(unittest.TestCase):
    def test_builtin_targets_cover_all_backends(self):
        ids = set(list_targets())
        self.assertIn("anlogic-hx1p35a", ids)
        self.assertIn("lattice-ice40-hx1k", ids)
        self.assertIn("generic", ids)
        self.assertEqual({get_target(name)["backend"] for name in ids}, set(BACKENDS))

    def test_get_target_returns_a_copy_not_the_template(self):
        first = get_target("generic")
        first["capabilities"]["synthesis"] = False
        self.assertTrue(get_target("generic")["capabilities"]["synthesis"])

    def test_unknown_target_raises(self):
        with self.assertRaisesRegex(ValueError, "unknown target"):
            get_target("nope-x1")

    def test_validate_requires_capability_booleans(self):
        with self.assertRaisesRegex(ValueError, "backend"):
            validate_target({"id": "x", "vendor": "v", "backend": "bogus",
                             "capabilities": {"synthesis": True, "place_route": False, "bitstream": False}})
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_target({"id": "x", "vendor": "v", "backend": "generic"})
        with self.assertRaisesRegex(ValueError, "booleans"):
            validate_target({"id": "x", "vendor": "v", "backend": "generic",
                             "capabilities": {"synthesis": 1, "place_route": False, "bitstream": False}})

    def test_generic_target_declares_no_place_route_or_bitstream(self):
        capabilities = get_target("generic")["capabilities"]
        self.assertTrue(capabilities["synthesis"])
        self.assertFalse(capabilities["place_route"])
        self.assertFalse(capabilities["bitstream"])


class ResolveTargetTests(unittest.TestCase):
    def test_default_is_anlogic_when_manifest_has_no_target(self):
        self.assertEqual(resolve_target({})["id"], DEFAULT_TARGET)

    def test_manifest_string_target(self):
        profile = resolve_target({"target": "lattice-ice40-hx1k"})
        self.assertEqual(profile["vendor"], "Lattice")
        self.assertEqual(profile["backend"], "ice40")

    def test_manifest_legacy_anlogic_description_reads_as_default(self):
        profile = resolve_target({"target": "Anlogic PH1P35MDG324"})
        self.assertEqual(profile["id"], DEFAULT_TARGET)
        self.assertEqual(profile["backend"], "anlogic")

    def test_manifest_unknown_non_anlogic_string_still_raises(self):
        with self.assertRaisesRegex(ValueError, "unknown target"):
            resolve_target({"target": "lattice-ice40-hx1"})

    def test_manifest_object_target_merges_overrides(self):
        profile = resolve_target({"target": {"id": "lattice-ice40-hx1k", "package": "sg48",
                                             "device": "iCE40HX1K-SG48"}})
        self.assertEqual(profile["package"], "sg48")
        self.assertEqual(profile["device"], "iCE40HX1K-SG48")

    def test_explicit_argument_wins(self):
        profile = resolve_target({"target": "generic"}, target="lattice-ice40-hx1k")
        self.assertEqual(profile["backend"], "ice40")

    def test_manifest_object_target_must_have_id(self):
        with self.assertRaisesRegex(ValueError, "profile id"):
            resolve_target({"target": {"package": "tq144"}})

    def test_manifest_target_of_unknown_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "profile id or an object"):
            resolve_target({"target": 7})


if __name__ == "__main__":
    unittest.main()
