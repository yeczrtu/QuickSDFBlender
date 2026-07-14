from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.history import (
    DEFAULT_BYTE_BUDGET,
    DEFAULT_SOFT_BYTE_BUDGET,
    History,
    HistoryActionResult,
)


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

    def test_only_changed_pixels_are_restored(self) -> None:
        before = np.zeros((8, 9, 4), dtype=np.float32)
        after = before.copy()
        after[1, 1, 0] = 1.0
        after[6, 7, 0] = 1.0
        history = History()
        history.push("dots", {"image": before}, {"image": after})
        self.assertEqual(history.undo_keys, ("image",))

        current = after.copy()
        # This lies inside the old whole-image-sized bounding rectangle but was
        # not touched by the recorded stroke.
        current[3, 4] = 0.75
        unchanged_input = current.copy()
        restored = history.undo({"image": current})["image"]
        np.testing.assert_array_equal(restored[1, 1], before[1, 1])
        np.testing.assert_array_equal(restored[6, 7], before[6, 7])
        np.testing.assert_array_equal(restored[3, 4], current[3, 4])
        np.testing.assert_array_equal(current, unchanged_input)
        self.assertEqual(history.redo_keys, ("image",))

    def test_mixed_rgba_uint8_and_bool_planes(self) -> None:
        display_before = np.zeros((6, 7, 4), dtype=np.uint8)
        display_after = display_before.copy()
        display_after[2, 3] = 255
        grayscale_before = np.zeros((6, 7), dtype=np.uint8)
        grayscale_after = grayscale_before.copy()
        grayscale_after[1:3, 4] = 127
        coverage_before = np.zeros((6, 7), dtype=np.bool_)
        coverage_after = coverage_before.copy()
        coverage_after[5, 6] = True

        history = History()
        self.assertTrue(
            history.push(
                "mixed planes",
                {
                    "display": display_before,
                    "grayscale": grayscale_before,
                    "coverage": coverage_before,
                },
                {
                    "display": display_after,
                    "grayscale": grayscale_after,
                    "coverage": coverage_after,
                },
            )
        )
        restored = history.undo(
            {
                "display": display_after,
                "grayscale": grayscale_after,
                "coverage": coverage_after,
            }
        )
        np.testing.assert_array_equal(restored["display"], display_before)
        np.testing.assert_array_equal(restored["grayscale"], grayscale_before)
        np.testing.assert_array_equal(restored["coverage"], coverage_before)
        self.assertEqual(restored["grayscale"].dtype, np.uint8)
        self.assertEqual(restored["coverage"].dtype, np.bool_)

        reapplied = history.redo(restored)
        np.testing.assert_array_equal(reapplied["display"], display_after)
        np.testing.assert_array_equal(reapplied["grayscale"], grayscale_after)
        np.testing.assert_array_equal(reapplied["coverage"], coverage_after)

    def test_sparse_2d_plane_preserves_unrelated_current_pixels(self) -> None:
        before = np.zeros((32, 33), dtype=np.uint8)
        after = before.copy()
        after[1, 1] = 50
        after[-2, -2] = 200
        history = History(compression_level=1)
        self.assertTrue(history.push("two marks", {"plane": before}, {"plane": after}))
        self.assertLess(history.bytes_used, 256)

        current = after.copy()
        current[14, 15] = 99
        restored = history.undo({"plane": current})["plane"]
        self.assertEqual(restored[1, 1], 0)
        self.assertEqual(restored[-2, -2], 0)
        self.assertEqual(restored[14, 15], 99)

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

    def test_action_metadata_supports_bytes_and_is_independently_copied(self) -> None:
        before = np.zeros((2, 2), dtype=np.bool_)
        after = before.copy()
        after[0, 1] = True
        metadata = {
            "kind": "create_angle_key",
            "key": {
                "uuid": "new-key",
                "angle": 42.0,
                "side": "RIGHT",
                "base_blob": b"\x00\x01base",
                "coverage_blob": b"\x80coverage",
                "revisions": [1, 2],
            },
        }
        history = History()
        self.assertTrue(
            history.push("Auto Key", {"coverage": before}, {"coverage": after}, metadata=metadata)
        )

        # The input mapping is not retained by reference.
        metadata["key"]["uuid"] = "mutated-input"
        self.assertEqual(history.undo_metadata["key"]["uuid"], "new-key")
        peek = history.undo_metadata
        peek["key"]["uuid"] = "mutated-peek"
        self.assertEqual(history.undo_metadata["key"]["uuid"], "new-key")

        result = history.undo_action({"coverage": after})
        self.assertIsInstance(result, HistoryActionResult)
        assert result is not None
        self.assertEqual(result.label, "Auto Key")
        self.assertEqual(result.metadata["key"]["base_blob"], b"\x00\x01base")
        np.testing.assert_array_equal(result.images["coverage"], before)

        result.metadata["key"]["uuid"] = "mutated-result"
        self.assertEqual(history.redo_metadata["key"]["uuid"], "new-key")
        redone = history.redo_action({"coverage": before})
        assert redone is not None
        self.assertEqual(redone.metadata["key"]["uuid"], "new-key")
        np.testing.assert_array_equal(redone.images["coverage"], after)

    def test_metadata_only_structural_action(self) -> None:
        history = History()
        self.assertTrue(
            history.push(
                "Create key",
                {},
                {},
                metadata={"kind": "create_angle_key", "uuid": "key-1"},
            )
        )
        self.assertEqual(history.undo_keys, ())
        result = history.undo_action({})
        assert result is not None
        self.assertEqual(result.images, {})
        self.assertEqual(result.metadata, {"kind": "create_angle_key", "uuid": "key-1"})
        redone = history.redo_action({})
        assert redone is not None
        self.assertEqual(redone.metadata["uuid"], "key-1")

    def test_noop_without_metadata_remains_ignored(self) -> None:
        plane = np.zeros((2, 2), dtype=np.uint8)
        history = History()
        self.assertFalse(history.push("noop", {"plane": plane}, {"plane": plane.copy()}))
        self.assertFalse(history.can_undo)

    def test_sparse_pixels_do_not_store_the_random_bounding_rectangle(self) -> None:
        rng = np.random.default_rng(42)
        before = rng.random((256, 256, 4), dtype=np.float32)
        after = before.copy()
        after[1, 1] = 0.0
        after[-2, -2] = 1.0

        history = History(compression_level=1)
        self.assertTrue(history.push("two islands", {"a": before}, {"a": after}))
        # A bounding rectangle would retain roughly two MiB of random float
        # data. Sparse pixel storage needs only two indices and two RGBA pairs.
        self.assertLess(history.bytes_used, 1024)
        np.testing.assert_array_equal(history.undo({"a": after})["a"], before)

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

    def test_metadata_counts_toward_budget(self) -> None:
        history = History(byte_budget=32)
        self.assertFalse(
            history.push("blob", {}, {}, metadata={"base_blob": bytes(range(128))})
        )
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
        with self.assertRaisesRegex(TypeError, "bool or uint8"):
            History().push(
                "bad", {"a": np.zeros((2, 2), np.float32)}, {"a": np.ones((2, 2), np.float32)}
            )
        with self.assertRaisesRegex(ValueError, "shape"):
            History().push("bad", {"a": np.zeros((2,), np.uint8)}, {"a": np.ones((2,), np.uint8)})

    def test_metadata_validation(self) -> None:
        with self.assertRaisesRegex(TypeError, "metadata must be a mapping"):
            History().push("bad", {}, {}, metadata=["not", "mapping"])
        with self.assertRaisesRegex(TypeError, "mapping keys must be strings"):
            History().push("bad", {}, {}, metadata={1: "value"})
        with self.assertRaisesRegex(ValueError, "NaN or infinity"):
            History().push("bad", {}, {}, metadata={"angle": float("nan")})
        with self.assertRaisesRegex(TypeError, "unsupported set"):
            History().push("bad", {}, {}, metadata={"keys": {"one", "two"}})
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with self.assertRaisesRegex(ValueError, "reference cycle"):
            History().push("bad", {}, {}, metadata=cyclic)

    def test_empty_action_apis(self) -> None:
        history = History()
        self.assertIsNone(history.undo_action({}))
        self.assertIsNone(history.redo_action({}))
        self.assertIsNone(history.undo_metadata)
        self.assertIsNone(history.redo_metadata)

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

    def test_incremental_transaction_releases_callers_arrays(self) -> None:
        first_before = np.zeros((128, 129), dtype=np.uint8)
        first_after = first_before.copy()
        first_after[4:80, 7:91] = 183
        coverage_before = np.zeros((128, 129), dtype=np.bool_)
        coverage_after = coverage_before.copy()
        coverage_after[4:80, 7:91] = True

        history = History(compression_level=1)
        transaction = history.begin_transaction("streamed stroke")
        self.assertTrue(transaction.add_delta("display", first_before, first_after))
        self.assertTrue(history.add_delta("coverage", coverage_before, coverage_after))
        first_before[:] = 99
        coverage_before[:] = True
        self.assertFalse(transaction.needs_rollback)
        self.assertTrue(history.commit())
        self.assertIsNone(history.active_transaction)

        restored = history.undo(
            {"display": first_after, "coverage": coverage_after}
        )
        np.testing.assert_array_equal(restored["display"], 0)
        np.testing.assert_array_equal(restored["coverage"], False)

    def test_hard_cap_keeps_transaction_available_for_rollback(self) -> None:
        rng = np.random.default_rng(903)
        before = rng.integers(0, 256, (64, 64), dtype=np.uint8)
        after = rng.integers(0, 256, (64, 64), dtype=np.uint8)
        history = History(byte_budget=64, soft_byte_budget=32, compression_level=0)
        transaction = history.begin_transaction("too large")
        transaction.add_delta("display", before, after)
        self.assertTrue(transaction.needs_rollback)
        self.assertFalse(transaction.commit())
        self.assertIs(history.active_transaction, transaction)
        restored = history.rollback({"display": after})
        np.testing.assert_array_equal(restored["display"], before)
        self.assertIsNone(history.active_transaction)
        self.assertFalse(history.can_undo)

    def test_restore_before_supports_key_at_a_time_rollback(self) -> None:
        before_a = np.zeros((8, 8), dtype=np.uint8)
        after_a = np.full((8, 8), 17, dtype=np.uint8)
        before_b = np.zeros((8, 8), dtype=np.bool_)
        after_b = np.ones((8, 8), dtype=np.bool_)
        history = History()
        transaction = history.begin_transaction("stream")
        transaction.add_delta("a", before_a, after_a)
        transaction.add_delta("b", before_b, after_b)
        np.testing.assert_array_equal(
            transaction.restore_before("a", after_a), before_a
        )
        np.testing.assert_array_equal(
            transaction.restore_before("b", after_b), before_b
        )
        transaction.rollback()

    def test_soft_cap_evicts_old_actions_but_keeps_newest(self) -> None:
        rng = np.random.default_rng(919)
        zero = rng.integers(0, 256, (64, 64), dtype=np.uint8)
        one = rng.integers(0, 256, (64, 64), dtype=np.uint8)
        two = rng.integers(0, 256, (64, 64), dtype=np.uint8)
        history = History(
            byte_budget=64 * 1024,
            soft_byte_budget=1024,
            compression_level=0,
        )
        self.assertTrue(history.push("one", {"a": zero}, {"a": one}))
        self.assertGreater(history.bytes_used, history.soft_byte_budget)
        self.assertTrue(history.push("two", {"a": one}, {"a": two}))
        self.assertEqual(history.undo_count, 1)
        self.assertEqual(history.undo_label, "two")

    def test_dense_delta_selects_tile_bitmap_and_bool_values_are_bitpacked(self) -> None:
        before = np.zeros((256, 256), dtype=np.bool_)
        after = before.copy()
        after[32:224, 24:232] = True
        history = History(compression_level=1)
        self.assertTrue(history.push("dense", {"coverage": before}, {"coverage": after}))
        delta = history._undo[-1].images["coverage"]
        self.assertEqual(delta.locator_kind, "tiles64")
        self.assertEqual(delta.value_kind, "bits")
        restored = history.undo({"coverage": after})["coverage"]
        np.testing.assert_array_equal(restored, before)

    def test_default_history_has_128_mib_soft_and_256_mib_hard_limits(self) -> None:
        history = History()
        self.assertEqual(history.soft_byte_budget, DEFAULT_SOFT_BYTE_BUDGET)
        self.assertEqual(history.byte_budget, DEFAULT_BYTE_BUDGET)

    def test_transaction_validation_and_lifecycle(self) -> None:
        plane = np.zeros((2, 2), dtype=np.uint8)
        history = History()
        transaction = history.begin_transaction("test")
        with self.assertRaisesRegex(RuntimeError, "already active"):
            history.begin_transaction("nested")
        self.assertFalse(transaction.add_delta("noop", plane, plane.copy()))
        transaction.add_delta("changed", plane, np.ones_like(plane))
        with self.assertRaisesRegex(ValueError, "already contains"):
            transaction.add_delta("changed", plane, np.ones_like(plane))
        self.assertTrue(transaction.commit())
        with self.assertRaisesRegex(RuntimeError, "no longer active"):
            transaction.commit()


if __name__ == "__main__":
    unittest.main()
