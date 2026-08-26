import copy
import json
import os
import socket
import subprocess
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from skill_orchestrator.capability_policy import (
    CAPABILITY_FAMILIES,
    CAPABILITY_REASON_IDS,
    evaluate_capability_policy,
    resolve_capability_policy,
    validate_capability_policy_document,
)
from skill_orchestrator.errors import ValidationError


ROOT = Path(__file__).resolve().parents[1]


def capabilities(*, read=None, write=None, network="none", process="none", commands=None):
    return {
        "schema_version": 1,
        "filesystem": {
            "read": list(read or []),
            "write": list(write or []),
        },
        "network": {"mode": network},
        "process": {
            "mode": process,
            "commands": list(commands or []),
        },
    }


def policy_document(*, floor=None, profile=None, profile_id="local-safe"):
    floor_value = copy.deepcopy(floor or capabilities(read=["project"]))
    profile_value = copy.deepcopy(profile or floor_value)
    return {
        "schema_version": 1,
        "operational_floor": floor_value,
        "default_profile": profile_id,
        "profiles": [{"id": profile_id, "capabilities": profile_value}],
    }


def safe_request():
    return capabilities(read=["project"])


class CapabilityPolicyTests(unittest.TestCase):
    def test_bundled_capability_profile_is_valid(self):
        document = json.loads(
            (ROOT / "security" / "capability_profiles.json").read_text(encoding="utf-8")
        )

        validated = validate_capability_policy_document(document)

        self.assertEqual(validated["schema_version"], 1)
        self.assertEqual(validated["default_profile"], "local-safe")
        self.assertEqual(len(validated["profiles"]), 1)

    def test_strict_top_level_keys(self):
        document = policy_document()
        document["unexpected"] = True

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(document)

    def test_strict_profile_keys(self):
        document = policy_document()
        document["profiles"][0]["unexpected"] = True

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(document)

    def test_strict_capability_keys(self):
        document = policy_document()
        document["operational_floor"]["unexpected"] = True

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(document)

    def test_duplicate_profile_ids_are_rejected(self):
        document = policy_document()
        document["profiles"].append(copy.deepcopy(document["profiles"][0]))

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(document)

    def test_exact_profile_selection_is_deterministic(self):
        floor = capabilities(read=["project"], network="localhost")
        strict = capabilities(read=["project"])
        document = policy_document(floor=floor, profile=strict)
        document["profiles"].append({"id": "strict", "capabilities": strict})

        resolved = resolve_capability_policy(document, profile_id="strict")

        self.assertEqual(resolved["profile_id"], "strict")
        self.assertEqual(resolved["capabilities"], strict)

    def test_unknown_profile_id_fails_without_fallback(self):
        with self.assertRaises(ValidationError):
            resolve_capability_policy(policy_document(), profile_id="missing")

    def test_profile_cannot_weaken_network_floor(self):
        floor = capabilities(read=["project"])
        profile = capabilities(read=["project"], network="localhost")

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(policy_document(floor=floor, profile=profile))

    def test_profile_cannot_weaken_filesystem_floor(self):
        floor = capabilities(read=["project"])
        profile = capabilities(read=["workspace"])

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(policy_document(floor=floor, profile=profile))

    def test_profile_cannot_weaken_process_floor(self):
        floor = capabilities(read=["project"])
        profile = capabilities(read=["project"], process="arbitrary")

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(policy_document(floor=floor, profile=profile))

    def test_unknown_policy_values_fail_closed(self):
        floor = capabilities(read=["project"], network="unknown")

        with self.assertRaises(ValidationError):
            validate_capability_policy_document(policy_document(floor=floor))

    def test_unknown_capability_request_is_invalid(self):
        request = {"schema_version": 1, "filesystem-read": ["project"]}

        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("capability.request.unknown", result["reasons"])
        self.assertNotIn("allowed", [item["status"] for item in result["decisions"]])

    def test_malformed_request_is_invalid(self):
        request = {"schema_version": 1, "filesystem": {"read": ["project"]}}

        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("capability.request.invalid", result["reasons"])

    def test_duplicate_request_values_are_invalid(self):
        request = capabilities(read=["project", "project"])

        result = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("capability.request.duplicate", result["reasons"])

    def test_empty_request_is_not_requested(self):
        result = evaluate_capability_policy(
            None,
            policy=policy_document(),
            requested_capabilities={},
            trust_status="rejected",
        )

        self.assertEqual(result["status"], "not-requested")
        self.assertEqual(
            [item["status"] for item in result["decisions"]],
            ["not-requested"] * len(CAPABILITY_FAMILIES),
        )
        self.assertIn("capability.request.empty", result["reasons"])

    def test_missing_declaration_with_request_is_unknown(self):
        result = evaluate_capability_policy(
            None,
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "unknown")
        self.assertIn("capability.declaration.missing", result["reasons"])
        self.assertNotIn("allowed", [item["status"] for item in result["decisions"]])

    def test_malformed_declaration_is_invalid(self):
        declaration = safe_request()
        declaration["process"] = {"mode": "unknown", "commands": []}

        result = evaluate_capability_policy(
            declaration,
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("capability.declaration.invalid", result["reasons"])

    def test_filesystem_read_within_scope_is_allowed(self):
        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "admissible")
        self.assertEqual(result["decisions"][0]["status"], "allowed")

    def test_filesystem_write_exceeding_profile_is_rejected(self):
        request = capabilities(read=["project"], write=["project"])

        result = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["decisions"][1]["status"], "denied")
        self.assertIn("capability.policy.exceeds-floor", result["reasons"])

    def test_network_mode_exceeding_profile_is_rejected(self):
        request = capabilities(read=["project"], network="localhost")

        result = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["decisions"][2]["status"], "denied")

    def test_process_mode_exceeding_profile_is_rejected(self):
        request = capabilities(read=["project"], process="commands", commands=["pytest"])

        result = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["decisions"][3]["status"], "denied")

    def test_process_command_subset_is_allowed(self):
        floor = capabilities(read=["project"], process="commands", commands=["pytest", "ruff"])
        request = capabilities(read=["project"], process="commands", commands=["pytest"])

        result = evaluate_capability_policy(
            request,
            policy=policy_document(floor=floor),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "admissible")
        self.assertEqual(result["decisions"][3]["status"], "allowed")

    def test_process_command_outside_policy_is_rejected(self):
        floor = capabilities(read=["project"], process="commands", commands=["pytest"])
        request = capabilities(read=["project"], process="commands", commands=["ruff"])

        result = evaluate_capability_policy(
            request,
            policy=policy_document(floor=floor),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["decisions"][3]["status"], "denied")

    def test_unknown_declaration_value_is_invalid(self):
        declaration = safe_request()
        declaration["network"]["mode"] = "unknown"

        result = evaluate_capability_policy(
            declaration,
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("capability.declaration.invalid", result["reasons"])

    def test_rejected_trust_blocks_positive_admission(self):
        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="rejected",
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(all(item["status"] != "allowed" for item in result["decisions"]))
        self.assertIn("capability.trust.rejected", result["reasons"])

    def test_unknown_trust_blocks_positive_admission(self):
        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="unknown",
        )

        self.assertEqual(result["status"], "unknown")
        self.assertTrue(all(item["status"] != "allowed" for item in result["decisions"]))
        self.assertIn("capability.trust.unknown", result["reasons"])

    def test_not_evaluated_trust_blocks_positive_admission(self):
        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="not-evaluated",
        )

        self.assertEqual(result["status"], "unknown")
        self.assertTrue(all(item["status"] != "allowed" for item in result["decisions"]))
        self.assertIn("capability.trust.required", result["reasons"])

    def test_admissible_trust_still_requires_policy_approval(self):
        request = capabilities(read=["project"], network="localhost")

        result = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "rejected")
        self.assertIn("capability.policy.exceeds-floor", result["reasons"])

    def test_decisions_use_canonical_family_order(self):
        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="admissible",
        )

        self.assertEqual(
            [item["capability"] for item in result["decisions"]],
            list(CAPABILITY_FAMILIES),
        )

    def test_mapping_insertion_order_does_not_change_result(self):
        request = safe_request()
        reversed_request = {
            "process": request["process"],
            "network": request["network"],
            "filesystem": request["filesystem"],
            "schema_version": request["schema_version"],
        }

        first = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )
        second = evaluate_capability_policy(
            reversed_request,
            policy=policy_document(),
            requested_capabilities=reversed_request,
            trust_status="admissible",
        )

        self.assertEqual(first, second)

    def test_repeated_runs_are_equal(self):
        results = [
            evaluate_capability_policy(
                safe_request(),
                policy=policy_document(),
                requested_capabilities=safe_request(),
                trust_status="admissible",
            )
            for _ in range(3)
        ]

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_reason_ids_are_allowlisted_and_limitation_is_fixed(self):
        result = evaluate_capability_policy(
            safe_request(),
            policy=policy_document(),
            requested_capabilities=safe_request(),
            trust_status="admissible",
        )

        self.assertTrue(set(result["reasons"]).issubset(set(CAPABILITY_REASON_IDS)))
        self.assertEqual(
            result["limitations"],
            ["capability.limit.enforcement-not-implemented"],
        )

    def test_result_is_metadata_only(self):
        raw_command = "TOP_SECRET_COMMAND"
        request = capabilities(read=["project"], process="commands", commands=[raw_command])
        result = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )
        serialized = json.dumps(result, sort_keys=True)

        self.assertNotIn(raw_command, serialized)
        self.assertNotIn("/" + "Users/", serialized)
        self.assertNotIn("C:" + "\\" + "Users" + "\\", serialized)
        self.assertNotIn("environment", serialized)
        self.assertNotIn("timestamp", serialized)

    def test_raw_command_is_not_disclosed(self):
        raw_command = "PRIVATE_COMMAND_123"
        request = capabilities(read=["project"], process="commands", commands=[raw_command])

        result = evaluate_capability_policy(
            request,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertNotIn(raw_command, repr(result))

    def test_path_and_secret_are_not_disclosed(self):
        secret = "/" + "Users/" + "private-user/.ssh/private-key"
        request = {"secret": secret}

        result = evaluate_capability_policy(
            None,
            policy=policy_document(),
            requested_capabilities=request,
            trust_status="admissible",
        )

        self.assertEqual(result["status"], "invalid")
        self.assertNotIn(secret, repr(result))

    def test_environment_values_are_not_read_or_disclosed(self):
        secret = "DO_NOT_DISCLOSE"
        with patch.dict(os.environ, {"CSO_TEST_SECRET": secret}, clear=False):
            result = evaluate_capability_policy(
                safe_request(),
                policy=policy_document(),
                requested_capabilities=safe_request(),
                trust_status="admissible",
            )

        self.assertNotIn(secret, repr(result))

    def test_no_external_effects_are_used(self):
        with patch("builtins.open", side_effect=AssertionError("filesystem access")), patch(
            "subprocess.run", side_effect=AssertionError("subprocess")
        ), patch("subprocess.Popen", side_effect=AssertionError("subprocess")), patch(
            "socket.socket", side_effect=AssertionError("network")
        ), patch(
            "urllib.request.urlopen", side_effect=AssertionError("network")
        ), patch("os.getenv", side_effect=AssertionError("environment")):
            result = evaluate_capability_policy(
                safe_request(),
                policy=policy_document(),
                requested_capabilities=safe_request(),
                trust_status="admissible",
            )

        self.assertEqual(result["status"], "admissible")

    def test_supported_trust_vocabulary_is_explicit(self):
        with self.assertRaises(ValueError):
            evaluate_capability_policy(
                safe_request(),
                policy=policy_document(),
                requested_capabilities=safe_request(),
                trust_status="maybe",
            )


if __name__ == "__main__":
    unittest.main()
