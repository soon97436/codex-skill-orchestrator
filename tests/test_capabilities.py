import json
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.capabilities import CAPABILITY_FINDING_IDS, analyze_capabilities
from skill_orchestrator.recommendations import analyze_and_recommend
from skill_orchestrator.validation import validate_registry


ROOT = Path(__file__).resolve().parents[1]


class CapabilityAnalysisTests(unittest.TestCase):
    def test_analyze_output_adds_registry_bounded_capability_analysis(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-capability-") as temporary:
            result = analyze_and_recommend(Path(temporary), ROOT)

        capability_analysis = result["capability_analysis"]
        self.assertEqual(capability_analysis["policy_mode"], "declarative-only")
        self.assertEqual(
            [item["skill"] for item in capability_analysis["skills"]],
            ["codex-skill-orchestrator"],
        )
        self.assertEqual(capability_analysis["skills"][0]["declaration_status"], "missing")

    def test_capabilities_are_registry_bounded(self) -> None:
        registry = {
            "registered-skill": {
                "id": "registered-skill",
                "capabilities": {
                    "schema_version": 1,
                    "filesystem": {"read": ["project"], "write": []},
                    "network": {"mode": "none"},
                    "process": {"mode": "commands", "commands": ["pytest"]},
                },
            }
        }

        result = analyze_capabilities(registry)

        self.assertEqual([item["skill"] for item in result["skills"]], ["registered-skill"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["skills"][0]["risk_classification"], "no-sensitive-request")
        self.assertNotIn("unregistered-skill", str(result))

    def test_unknown_declaration_is_explicit(self) -> None:
        registry = {
            "unknown-skill": {
                "id": "unknown-skill",
                "capabilities": {
                    "schema_version": 1,
                    "filesystem": {"read": [], "write": []},
                    "network": {"mode": "unknown"},
                    "process": {"mode": "none", "commands": []},
                },
            }
        }

        result = analyze_capabilities(registry)

        self.assertEqual(result["skills"][0]["declaration_status"], "unknown")
        self.assertEqual(result["skills"][0]["risk_classification"], "unknown")
        self.assertEqual(
            result["skills"][0]["finding_ids"],
            ["capability.declaration.unknown"],
        )
        self.assertEqual(
            result["findings"][0]["field_ref"]["identity"]["field"],
            "capabilities.network.mode",
        )
        self.assertEqual(result["findings"][0]["declared_value"], "unknown")

    def test_capability_limit_marks_analysis_incomplete(self) -> None:
        registry = {
            "alpha": {"id": "alpha"},
            "beta": {"id": "beta"},
        }

        result = analyze_capabilities(registry, max_skills_evaluated=1)

        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["registry"], {"skill_count": 2, "evaluated_count": 1})
        self.assertEqual([item["skill"] for item in result["skills"]], ["alpha"])
        self.assertIn(
            {
                "finding_id": "capability.analysis.limit",
                "field_ref": {
                    "source": "registry.skills",
                    "identity": {"field": "capabilities"},
                },
                "declared_value": "truncated",
            },
            result["findings"],
        )

    def test_each_capability_bound_fails_explicitly(self) -> None:
        declared = {
            "id": "declared",
            "capabilities": {
                "schema_version": 1,
                "filesystem": {"read": ["project", "workspace"], "write": []},
                "network": {"mode": "none"},
                "process": {"mode": "commands", "commands": ["pytest", "ruff"]},
            },
        }
        cases = (
            {"max_capability_entries": 2},
            {"max_declared_commands": 1},
            {"max_scope_entries": 1},
        )
        for limits in cases:
            with self.subTest(limits=limits):
                result = analyze_capabilities({"declared": declared}, **limits)
                self.assertEqual(result["status"], "incomplete")
                self.assertTrue(result["truncated"])
                self.assertEqual(result["skills"], [])
                self.assertEqual(
                    result["findings"][0]["finding_id"],
                    "capability.analysis.limit",
                )

        finding_limited = analyze_capabilities(
            {"alpha": {"id": "alpha"}, "beta": {"id": "beta"}},
            max_findings=1,
        )
        self.assertEqual(finding_limited["status"], "incomplete")
        self.assertEqual(
            [item["finding_id"] for item in finding_limited["findings"]],
            ["capability.analysis.limit"],
        )

    def test_capability_dedup_is_input_order_independent(self) -> None:
        def registry(reverse: bool):
            scopes = ["workspace", "project", "project"]
            commands = ["pytest", "ruff", "pytest"]
            if reverse:
                scopes.reverse()
                commands.reverse()
            return {
                "ordered-skill": {
                    "id": "ordered-skill",
                    "capabilities": {
                        "schema_version": 1,
                        "filesystem": {"read": scopes, "write": []},
                        "network": {"mode": "none"},
                        "process": {"mode": "commands", "commands": commands},
                    },
                }
            }

        first = analyze_capabilities(registry(False))
        second = analyze_capabilities(registry(True))

        self.assertEqual(first, second)
        declaration = first["skills"][0]["capabilities"]
        self.assertEqual(declaration["filesystem"]["read"], ["project", "workspace"])
        self.assertEqual(declaration["process"]["commands"], ["pytest", "ruff"])

    def test_capability_output_is_metadata_only_without_absolute_paths(self) -> None:
        secret = "prompt-contents-must-not-appear"
        registry = {
            "metadata-only": {
                "id": "metadata-only",
                "description": secret,
                "capabilities": {
                    "schema_version": 1,
                    "filesystem": {"read": ["project"], "write": ["workspace"]},
                    "network": {"mode": "localhost"},
                    "process": {"mode": "commands", "commands": ["pytest"]},
                },
            }
        }

        result = analyze_capabilities(registry)
        serialized = json.dumps(result, sort_keys=True)

        self.assertNotIn(secret, serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("C:\\\\Users\\\\", serialized)
        self.assertNotIn("\\\\\\\\server\\\\", serialized)
        self.assertNotIn("environment", serialized)
        self.assertNotIn("timestamp", serialized)

    def test_sensitive_capability_findings_are_deterministic(self) -> None:
        capabilities = {
            "schema_version": 1,
            "filesystem": {"read": ["unrestricted"], "write": ["project"]},
            "network": {"mode": "unrestricted"},
            "process": {"mode": "arbitrary", "commands": []},
        }
        registry = {
            "declared-skill": {"id": "declared-skill", "capabilities": capabilities}
        }

        first = analyze_capabilities(registry)
        second = analyze_capabilities(dict(reversed(list(registry.items()))))

        self.assertEqual(first, second)
        self.assertEqual(first["skills"][0]["declaration_status"], "declared")
        self.assertEqual(first["skills"][0]["risk_classification"], "sensitive-requested")
        self.assertEqual(first["skills"][0]["capabilities"], capabilities)
        self.assertEqual(
            first["skills"][0]["finding_ids"],
            [
                "capability.filesystem.unrestricted",
                "capability.network.unrestricted",
                "capability.process.arbitrary",
            ],
        )
        self.assertEqual(
            [(item["finding_id"], item["field_ref"]["identity"]["field"]) for item in first["findings"]],
            [
                ("capability.filesystem.unrestricted", "capabilities.filesystem.read"),
                ("capability.network.unrestricted", "capabilities.network.mode"),
                ("capability.process.arbitrary", "capabilities.process.mode"),
            ],
        )
        self.assertTrue(all(item["declared_value"] in {"unrestricted", "arbitrary"} for item in first["findings"]))

    def test_skill_capability_manifest_is_optional_and_missing_is_explicit(self) -> None:
        registry = validate_registry(ROOT)

        result = analyze_capabilities(registry)

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["policy_mode"], "declarative-only")
        self.assertEqual(result["enforcement_status"], "not-implemented")
        self.assertEqual(
            result["skills"],
            [
                {
                    "skill": "codex-skill-orchestrator",
                    "declaration_status": "missing",
                    "risk_classification": "unknown",
                    "finding_ids": ["capability.declaration.missing"],
                }
            ],
        )
        self.assertEqual(
            result["findings"],
            [
                {
                    "finding_id": "capability.declaration.missing",
                    "skill": "codex-skill-orchestrator",
                    "field_ref": {
                        "source": "registry.skills",
                        "identity": {
                            "skill": "codex-skill-orchestrator",
                            "field": "capabilities",
                        },
                    },
                    "declared_value": "missing",
                }
            ],
        )
        self.assertFalse(result["truncated"])

    def test_capability_finding_ids_are_allowlisted(self) -> None:
        registry = {
            "sensitive": {
                "id": "sensitive",
                "capabilities": {
                    "schema_version": 1,
                    "filesystem": {"read": ["unrestricted"], "write": []},
                    "network": {"mode": "unrestricted"},
                    "process": {"mode": "arbitrary", "commands": []},
                },
            },
            "missing": {"id": "missing"},
        }

        result = analyze_capabilities(registry)

        self.assertTrue(result["findings"])
        self.assertLessEqual(
            {item["finding_id"] for item in result["findings"]},
            CAPABILITY_FINDING_IDS,
        )


if __name__ == "__main__":
    unittest.main()
