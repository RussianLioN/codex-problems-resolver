import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validate_skills_context


def prompt_payload(root: Path, visible_description: str) -> list[dict]:
    text = f"""### Skill roots
- `r0` = `{root}`
### Available skills
- example: Example description (file: r0/example/SKILL.md)
- shortened: {visible_description} (file: r0/shortened/SKILL.md)
</skills_instructions>"""
    return [{"content": [{"type": "input_text", "text": text}]}]


class ValidateSkillsContextTests(unittest.TestCase):
    def test_intact_catalog_has_no_shortened_descriptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root, "example", "Example description")
            self._write_skill(root, "shortened", "A complete description")

            result = validate_skills_context.validate_prompt(
                prompt_payload(root, "A complete description"), allowed_roots=[root]
            )

        self.assertEqual([], result.shortened)
        self.assertEqual([], result.errors)

    def test_prefix_shortening_is_reported_with_lost_character_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root, "example", "Example description")
            self._write_skill(root, "shortened", "A complete description")

            result = validate_skills_context.validate_prompt(
                prompt_payload(root, "A complete"), allowed_roots=[root]
            )

        self.assertEqual([("shortened", 12)], result.shortened)
        self.assertEqual([], result.errors)

    def test_description_with_colon_is_not_parsed_as_part_of_skill_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root, "example", "Описание: с двоеточием")
            self._write_skill(root, "shortened", "A complete description")
            payload = prompt_payload(root, "A complete description")
            payload[0]["content"][0]["text"] = payload[0]["content"][0]["text"].replace(
                "Example description", "Описание: с двоеточием"
            )

            result = validate_skills_context.validate_prompt(payload, allowed_roots=[root])

        self.assertEqual([], result.shortened)
        self.assertEqual([], result.errors)

    def test_path_outside_declared_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root, "example", "Example description")
            payload = prompt_payload(root, "A complete description")
            payload[0]["content"][0]["text"] = payload[0]["content"][0]["text"].replace(
                "r0/shortened/SKILL.md", "r0/../outside/SKILL.md"
            )

            result = validate_skills_context.validate_prompt(payload, allowed_roots=[root])

        self.assertEqual([], result.shortened)
        self.assertIn("shortened: путь навыка выходит за объявленный корень", result.errors)

    def test_root_outside_allowed_codex_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "outside"
            allowed_root = base / "allowed"
            self._write_skill(root, "example", "Example description")
            self._write_skill(root, "shortened", "A complete description")

            result = validate_skills_context.validate_prompt(
                prompt_payload(root, "A complete description"), allowed_roots=[allowed_root]
            )

        self.assertEqual([], result.shortened)
        self.assertIn(
            "example: корень навыков находится вне разрешённых каталогов", result.errors
        )

    def test_invalid_json_returns_usage_error(self):
        exit_code, output = validate_skills_context.run("{")

        self.assertEqual(2, exit_code)
        self.assertIn("некорректный JSON", output)

    @staticmethod
    def _write_skill(root: Path, name: str, description: str) -> None:
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n",
            encoding="utf-8",
        )
