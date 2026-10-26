"""Детерминированные проверки преобразования назначений iTerm2."""

import copy
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import iterm_shift_arrows

LEFT = "0xf702-0x220000"
RIGHT = "0xf703-0x220000"


def original_keymap():
    return {
        LEFT: {"Action": 10, "Text": "[D", "Extra": {"Values": [1, True]}},
        RIGHT: {"Action": 10, "Text": "[C"},
        "unrelated": {"Action": 42, "Text": "other", "Nested": [{"Keep": True}]},
    }


class KeymapTests(unittest.TestCase):
    def setUp(self):
        self.module = iterm_shift_arrows

    def assert_conflict(self, function, *inputs):
        before = copy.deepcopy(inputs)
        with self.assertRaises(self.module.MappingConflict) as caught:
            function(*inputs)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual(inputs, before)
        self.assertNotIn("sensitive-test-value", str(caught.exception))
        self.assertNotIn("sensitive-test-value", repr(caught.exception))

    def test_plan_replaces_both_sequences(self):
        original = original_keymap()
        before = copy.deepcopy(original)
        planned = self.module.plan_keymap(original)
        expected = copy.deepcopy(before)
        expected[LEFT]["Text"] = "[1;2D"
        expected[RIGHT]["Text"] = "[1;2C"
        self.assertEqual(planned, expected)
        self.assertEqual(original, before)

    def test_plan_accepts_partly_and_fully_corrected_state(self):
        for corrected in ((), (LEFT,), (RIGHT,), (LEFT, RIGHT)):
            with self.subTest(corrected=corrected):
                original = original_keymap()
                for key in corrected:
                    original[key]["Text"] = "[1;2D" if key == LEFT else "[1;2C"
                planned = self.module.plan_keymap(original)
                self.assertEqual(planned[LEFT]["Text"], "[1;2D")
                self.assertEqual(planned[RIGHT]["Text"], "[1;2C")
                self.assertEqual(self.module.plan_keymap(planned), planned)

    def test_plan_returns_deep_copy_of_all_fields(self):
        original = original_keymap()
        before = copy.deepcopy(original)
        planned = self.module.plan_keymap(original)
        planned[LEFT]["Extra"]["Values"].append("new")
        planned["unrelated"]["Nested"][0]["Keep"] = False
        self.assertEqual(original, before)

    def test_plan_preserves_other_binding_that_shares_target_record(self):
        original = original_keymap()
        original["same-record"] = original[LEFT]
        before = copy.deepcopy(original)
        planned = self.module.plan_keymap(original)
        self.assertEqual(planned["same-record"], before["same-record"])
        self.assertEqual(planned[LEFT]["Text"], "[1;2D")
        self.assertEqual(original, before)

    def test_plan_rejects_missing_or_invalid_target_records(self):
        for key in (LEFT, RIGHT):
            for value in (None, [], "sensitive-test-value", 10, True):
                with self.subTest(key=key, value_type=type(value).__name__):
                    original = original_keymap()
                    original[key] = value
                    self.assert_conflict(self.module.plan_keymap, original)
            original = original_keymap()
            del original[key]
            self.assert_conflict(self.module.plan_keymap, original)

    def test_plan_rejects_invalid_actions_and_texts(self):
        for key in (LEFT, RIGHT):
            for field, values in (
                ("Action", (None, True, False, 10.0, "10", 0, 11)),
                ("Text", (None, True, 1, [], "sensitive-test-value", "[A")),
            ):
                for value in values:
                    with self.subTest(key=key, field=field, value_type=type(value).__name__):
                        original = original_keymap()
                        original[key][field] = value
                        self.assert_conflict(self.module.plan_keymap, original)
                original = original_keymap()
                del original[key][field]
                self.assert_conflict(self.module.plan_keymap, original)

    def test_plan_rejects_swapped_directions(self):
        original = original_keymap()
        original[LEFT]["Text"] = "[C"
        original[RIGHT]["Text"] = "[D"
        self.assert_conflict(self.module.plan_keymap, original)

    def test_plan_rejects_non_dictionary(self):
        for value in (None, [], "sensitive-test-value", 10):
            self.assert_conflict(self.module.plan_keymap, value)

    def test_restore_reverts_only_our_changes_and_keeps_current_other_fields(self):
        original = original_keymap()
        applied = self.module.plan_keymap(original)
        current = copy.deepcopy(applied)
        current["unrelated"]["Nested"].append({"New": 2})
        current["new-binding"] = {"Action": 5, "Text": "new"}
        before = copy.deepcopy((current, original, applied))
        restored = self.module.restore_keymap(current, original, applied)
        expected = copy.deepcopy(current)
        expected[LEFT] = original[LEFT]
        expected[RIGHT] = original[RIGHT]
        self.assertEqual(restored, expected)
        self.assertEqual((current, original, applied), before)
        restored[LEFT]["Extra"]["Values"].append("new")
        restored["unrelated"]["Nested"].append("new")
        self.assertEqual((current, original, applied), before)

    def test_restore_accepts_partly_and_fully_reverted_state(self):
        original = original_keymap()
        applied = self.module.plan_keymap(original)
        for reverted in ((), (LEFT,), (RIGHT,), (LEFT, RIGHT)):
            with self.subTest(reverted=reverted):
                current = copy.deepcopy(applied)
                for key in reverted:
                    current[key] = copy.deepcopy(original[key])
                restored = self.module.restore_keymap(current, original, applied)
                self.assertEqual(restored, original)
                self.assertEqual(
                    self.module.restore_keymap(restored, original, applied), original
                )

    def test_restore_preserves_preexisting_corrected_binding_and_its_later_edits(self):
        for key in (LEFT, RIGHT):
            with self.subTest(key=key):
                original = original_keymap()
                original[key]["Text"] = "[1;2D" if key == LEFT else "[1;2C"
                applied = self.module.plan_keymap(original)
                current = copy.deepcopy(applied)
                current[key] = {"Action": 123, "Text": "later-edit"}
                expected = copy.deepcopy(original)
                expected[key] = copy.deepcopy(current[key])
                self.assertEqual(
                    self.module.restore_keymap(current, original, applied), expected
                )

    def test_restore_with_no_changes_preserves_current_mapping(self):
        original = self.module.plan_keymap(original_keymap())
        current = {"new-only": {"Nested": [1]}}
        restored = self.module.restore_keymap(current, original, original)
        self.assertEqual(restored, current)
        restored["new-only"]["Nested"].append(2)
        self.assertEqual(current["new-only"]["Nested"], [1])

    def test_restore_rejects_changed_target_text_action_extra_fields_or_missing_key(self):
        original = original_keymap()
        applied = self.module.plan_keymap(original)
        for key in (LEFT, RIGHT):
            for field, value in (
                ("Text", "sensitive-test-value"),
                ("Action", 11),
                ("Action", 10.0),
                ("Extra", {"sensitive-test-value": True}),
            ):
                with self.subTest(key=key, field=field, value_type=type(value).__name__):
                    current = copy.deepcopy(applied)
                    current[key][field] = value
                    self.assert_conflict(
                        self.module.restore_keymap, current, original, applied
                    )
            current = copy.deepcopy(applied)
            del current[key]
            self.assert_conflict(self.module.restore_keymap, current, original, applied)

    def test_restore_rejects_inconsistent_snapshots(self):
        original = original_keymap()
        applied = self.module.plan_keymap(original)
        variations = []
        for key, field, value in (
            (LEFT, "Text", "[D"),
            (RIGHT, "Action", 11),
            (LEFT, "Extra", {"sensitive-test-value": True}),
            ("unrelated", "Text", "sensitive-test-value"),
        ):
            altered = copy.deepcopy(applied)
            altered[key][field] = value
            variations.append(altered)
        altered = copy.deepcopy(applied)
        altered["new-binding"] = {}
        variations.append(altered)
        altered = copy.deepcopy(applied)
        del altered["unrelated"]
        variations.append(altered)
        altered = copy.deepcopy(applied)
        altered[LEFT]["Extra"]["Values"][0] = True
        variations.append(altered)
        for altered in variations:
            self.assert_conflict(self.module.restore_keymap, applied, original, altered)

    def test_restore_rejects_invalid_original_snapshot(self):
        original = original_keymap()
        applied = self.module.plan_keymap(original)
        original[LEFT]["Text"] = "sensitive-test-value"
        self.assert_conflict(self.module.restore_keymap, applied, original, applied)

    def test_restore_rejects_non_dictionary_inputs(self):
        original = original_keymap()
        applied = self.module.plan_keymap(original)
        for index in range(3):
            for value in (None, [], "sensitive-test-value"):
                inputs = [applied, original, applied]
                inputs[index] = value
                self.assert_conflict(self.module.restore_keymap, *inputs)


if __name__ == "__main__":
    unittest.main()
