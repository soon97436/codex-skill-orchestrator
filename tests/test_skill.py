import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "router" / "codex-skill-orchestrator"


class SkillMetadataTests(unittest.TestCase):
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
