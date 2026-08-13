import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "router" / "codex-skill-orchestrator"


class SkillMetadataTests(unittest.TestCase):
    def test_windows_entrypoints_share_python_discovery(self) -> None:
        helper = (ROOT / "installer" / "python-discovery.ps1").read_text(encoding="utf-8")
        self.assertLess(helper.index("Name = 'py'"), helper.index("Name = 'python3'"))
        self.assertLess(helper.index("Name = 'python3'"), helper.index("Name = 'python'"))
        for relative in ("installer/install.ps1", "installer/cso.ps1", "scripts/smoke.ps1"):
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("python-discovery.ps1", content)
                self.assertIn("Find-CsoPython", content)
                self.assertNotIn("$candidates =", content)

    def test_windows_smoke_covers_phase_two_cli_without_network(self) -> None:
        content = (ROOT / "scripts" / "smoke.ps1").read_text(encoding="utf-8")
        for command in ("analyze", "init", "doctor"):
            self.assertIn(f"'.\\installer\\cso.ps1' {command}", content)
        self.assertIn("$projectFixtureRoot = Join-Path $temporaryRoot ('cso-project-fixture-'", content)
        self.assertIn("$installerSmokeRoot = Join-Path $temporaryRoot ('cso-smoke-'", content)
        self.assertIn("$stateRoot = Join-Path $installerSmokeRoot 'state'", content)
        before_check = content.index("Installer smoke root existed before dry-run.")
        dry_run = content.index("-DryRun -Json")
        after_check = content.index("Dry-run created persistent files.")
        self.assertLess(before_check, dry_run)
        self.assertLess(dry_run, after_check)
        self.assertIn("'cso-project-fixture-'", content)
        self.assertIn("'cso-smoke-'", content)
        self.assertNotIn("Invoke-WebRequest", content)
        self.assertNotIn("Invoke-RestMethod", content)

    def test_skill_frontmatter_is_minimal_and_valid(self) -> None:
        content = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        self.assertIsNotNone(match)
        fields = {}
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            self.assertEqual(separator, ":")
            fields[key.strip()] = value.strip()
        self.assertEqual(set(fields), {"name", "description"})
        self.assertEqual(fields["name"], "codex-skill-orchestrator")
        self.assertRegex(fields["name"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertTrue(1 <= len(fields["description"]) <= 1024)
        self.assertNotIn("<", fields["description"])
        self.assertNotIn(">", fields["description"])

    def test_openai_metadata_has_required_interface_fields(self) -> None:
        content = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Codex Skill Orchestrator"', content)
        self.assertIn('short_description: "Choose lightweight agent skill profiles"', content)
        self.assertIn("$codex-skill-orchestrator", content)

    def test_router_is_lightweight_and_uses_one_level_references(self) -> None:
        lines = (SKILL / "SKILL.md").read_text(encoding="utf-8").splitlines()
        self.assertLess(len(lines), 100)
        self.assertTrue((SKILL / "references" / "profiles.md").is_file())


if __name__ == "__main__":
    unittest.main()
