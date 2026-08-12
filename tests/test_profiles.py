import unittest
from pathlib import Path

from skill_orchestrator.engine import profile_catalog, route_task
from skill_orchestrator.errors import ValidationError
from skill_orchestrator.validation import load_profiles, resolve_profile


ROOT = Path(__file__).resolve().parents[1]


class ProfileTests(unittest.TestCase):
    def test_required_profiles_are_present_and_valid(self) -> None:
        profiles = load_profiles(ROOT)
        self.assertEqual(
            set(profiles),
            {
                "universal",
                "economy",
                "deep-reasoning",
                "small-project",
                "large-project",
                "research",
                "security",
                "custom",
            },
        )
        self.assertEqual(len(profile_catalog(source_root=ROOT)), 8)

    def test_aliases_and_inheritance_resolve(self) -> None:
        economy = resolve_profile(ROOT, "Economy / Save Usage")
        self.assertEqual(economy["id"], "economy")
        custom = resolve_profile(ROOT, "custom")
        self.assertIn("universal", custom["resolved_from"])
        self.assertIn("custom-workflow", {route["intent"] for route in custom["routes"]})
        self.assertIn("security", {route["intent"] for route in custom["routes"]})

    def test_unknown_profile_fails_closed(self) -> None:
        with self.assertRaises(ValidationError):
            resolve_profile(ROOT, "does-not-exist")

    def test_route_respects_profile_limit(self) -> None:
        result = route_task(
            "Debug a failing feature, review the diff, research sources, and audit a security dependency",
            "economy",
            source_root=ROOT,
        )
        self.assertLessEqual(len(result["selected_routes"]), 2)
        self.assertEqual(result["reasoning_hint"], "low")

    def test_unmatched_task_uses_host_fallback(self) -> None:
        result = route_task("Translate this sentence", "universal", source_root=ROOT)
        self.assertEqual(result["selected_routes"], [])
        self.assertEqual(result["fallback"], "host-builtins")


if __name__ == "__main__":
    unittest.main()
