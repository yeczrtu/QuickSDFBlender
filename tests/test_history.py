from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.history import History


class HistoryTests(unittest.TestCase):
    def test_multi_image_action_undo_and_redo(self) -> None:
        face_before = np.zeros((6, 7, 4), dtype=np.uint8)
        side_before = np.full((3, 4, 4), 17, dtype=np.uint8)
        face_after = face_before.copy()
        side_after = side_before.copy()
        face_after[2:4, 3:6] = [255, 255, 255, 1]
        side_after[1, 2] = [0, 0, 0, 255]

        history = History()
        self.assertTrue(
            history.push(
                "Propagate stroke",
                {"face": face_before, "side": side_before},
                {"face": face_after, "side": side_after},
            )
        )
        self.assertEqual(history.undo_label, "Propagate stroke")
        restored = history.undo({"face": face_after, "side": side_after})
        np.testing.assert_array_equal(restored["face"], face_before)
        np.testing.assert_array_equal(restored["side"], side_before)
        self.assertTrue(history.can_redo)

        reapplied = history.redo(restored)
        np.testing.assert_array_equal(reapplied["face"], face_after)
        np.testing.assert_array_equal(reapplied["side"], side_after)
        self.assertTrue(history.can_undo)

    def test_only_changed_bbox_is_restored(self) -> None:
        before = np.zeros((8, 9, 4), dtype=np.float32)
        after = before.copy()
        after[3, 4, 0] = 1.0
        history = History()
        history.push("dot", {"image": before}, {"image": after})

        current = after.copy()
        current[0, 0] = 0.75  # unrelated change outside the stored 1x1 bbox
        unchanged_input = current.copy()
        restored = history.undo({"image": current})["image"]
        np.testing.assert_array_equal(restored[3, 4], before[3, 4])
        np.testing.assert_array_equal(restored[0, 0], current[0, 0])
        np.testing.assert_array_equal(current, unchanged_input)

    def test_push_copies_data_and_ignores_noop(self) -> None:
        before = np.zeros((2, 3, 4), dtype=np.uint16)
        after = before.copy()
        history = History()
        self.assertFalse(history.push("no change", {"a": before}, {"a": after}))
        self.assertFalse(history.can_undo)

        after[0, 1] = 42
        expected_before = before.copy()
        self.assertTrue(history.push("change", {"a": before}, {"a": after}))
        before[:] = 99
        after[:] = 100
        restored = history.undo({"a": after})["a"]
        np.testing.assert_array_equal(restored[0, 1], expected_before[0, 1])

    def test_new_push_clears_redo(self) -> None:
        zero = np.zeros((2, 2, 4), dtype=np.uint8)
        one = zero.copy()
        one[0, 0] = 1
        two = one.copy()
        two[1, 1] = 2
        history = History()
        history.push("one", {"a": zero}, {"a": one})
        history.undo({"a": one})
        self.assertTrue(history.can_redo)
        history.push("branch", {"a": zero}, {"a": two})
        self.assertFalse(history.can_redo)
        self.assertEqual(history.redo({"a": two}), {})

    def test_byte_budget_evicts_oldest_entry(self) -> None:
        baseline = np.zeros((16, 16, 4), dtype=np.uint8)
        first = baseline.copy()
        first[1:5, 1:5] = 31
        second = first.copy()
        second[8:14, 7:15] = 173

        probe = History(byte_budget=1_000_000)
        probe.push("first", {"a": baseline}, {"a": first})
        probe.push("second", {"a": first}, {"a": second})
        budget = probe.bytes_used - 1

        history = History(byte_budget=budget)
        history.push("first", {"a": baseline}, {"a": first})
        history.push("second", {"a": first}, {"a": second})
        self.assertLessEqual(history.bytes_used, budget)
        self.assertEqual(history.undo_count, 1)
        self.assertEqual(history.undo_label, "second")
        self.assertIn("a", history.undo({"a": second}))
        self.assertEqual(history.undo({"a": first}), {})

    def test_oversize_action_is_not_retained(self) -> None:
        before = np.zeros((4, 4, 4), dtype=np.uint8)
        after = np.ones_like(before)
        history = History(byte_budget=1, compression_level=0)
        self.assertFalse(history.push("too large", {"a": before}, {"a": after}))
        self.assertEqual(history.bytes_used, 0)
        self.assertFalse(history.can_undo)

    def test_clear(self) -> None:
        before = np.zeros((2, 2, 4), dtype=np.uint8)
        after = np.ones_like(before)
        history = History()
        history.push("change", {"a": before}, {"a": after})
        history.clear()
        self.assertEqual(history.undo_count, 0)
        self.assertEqual(history.redo_count, 0)
        self.assertEqual(history.bytes_used, 0)

    def test_validation(self) -> None:
        rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, "same image keys"):
            History().push("bad", {"a": rgba}, {"b": rgba})
        with self.assertRaisesRegex(ValueError, "shape"):
            History().push("bad", {"a": rgba}, {"a": np.zeros((2, 2, 3), np.uint8)})
        with self.assertRaisesRegex(TypeError, "dtype"):
            History().push("bad", {"a": rgba}, {"a": rgba.astype(np.float32)})

    def test_failed_undo_does_not_consume_entry(self) -> None:
        before = np.zeros((2, 2, 4), dtype=np.float32)
        after = np.ones_like(before)
        history = History()
        history.push("change", {"image": before}, {"image": after})
        with self.assertRaises(KeyError):
            history.undo({})
        self.assertEqual(history.undo_count, 1)
        with self.assertRaisesRegex(TypeError, "dtype"):
            history.undo({"image": after.astype(np.float64)})
        self.assertEqual(history.undo_count, 1)


if __name__ == "__main__":
    unittest.main()
