from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.preview_cache import (
    ByteLRU,
    max_pool,
    resize_nearest,
    thumbnail_plane,
)
from quick_sdf_blender.review import (
    review_adjustment_heatmap,
    review_threshold_rgba16,
    review_violation_heatmap,
)


class ByteLRUTests(unittest.TestCase):
    def test_evicts_least_recently_used_entry_by_bytes(self) -> None:
        cache = ByteLRU(10)
        cache.put("first", object(), 4)
        second = object()
        cache.put("second", second, 4)
        self.assertIsNotNone(cache.get("first"))
        cache.put("third", object(), 4)
        self.assertIsNone(cache.get("second"))
        self.assertIsNotNone(cache.get("first"))
        self.assertEqual(cache.bytes_used, 8)

    def test_oversized_value_is_returned_but_not_retained(self) -> None:
        cache = ByteLRU(2)
        value = object()
        self.assertIs(cache.put("large", value, 3), value)
        self.assertIsNone(cache.get("large"))


class DerivedImageTests(unittest.TestCase):
    def test_nearest_proxy_preserves_aspect_and_bounds_long_side(self) -> None:
        values = np.arange(40 * 80, dtype=np.int32).reshape(40, 80)
        resized = resize_nearest(values, 20)
        self.assertEqual(resized.shape, (10, 20))
        self.assertEqual(int(resized[0, 0]), int(values[0, 0]))
        self.assertEqual(int(resized[-1, -1]), int(values[36, 76]))

    def test_thumbnail_crop_uses_bottom_up_uv_on_top_down_plane(self) -> None:
        values = np.zeros((4, 4), dtype=np.uint8)
        values[:2] = 9  # top half, UV v 0.5..1
        values[2:] = 3  # bottom half, UV v 0..0.5
        top = thumbnail_plane(values, (0.0, 0.5, 1.0, 1.0), width=2, height=2)
        bottom = thumbnail_plane(values, (0.0, 0.0, 1.0, 0.5), width=2, height=2)
        np.testing.assert_array_equal(top, 9)
        np.testing.assert_array_equal(bottom, 3)

    def test_max_pool_keeps_single_problem_pixel(self) -> None:
        values = np.zeros((1024, 2048), dtype=np.bool_)
        values[777, 1234] = True
        pooled = max_pool(values, 512)
        self.assertEqual(pooled.shape, (256, 512))
        self.assertEqual(int(np.count_nonzero(pooled)), 1)


class BoundedReviewTests(unittest.TestCase):
    def test_threshold_preview_is_bounded_before_rgba_expansion(self) -> None:
        texture = np.zeros((1024, 2048, 4), dtype=np.uint16)
        texture[..., 0] = 65535
        texture[..., 3] = 65535
        preview = review_threshold_rgba16(texture, 45.0)
        self.assertEqual(preview.shape, (256, 512, 4))
        self.assertEqual(preview.dtype, np.float32)

    def test_violation_and_adjustment_heatmaps_retain_small_defects(self) -> None:
        masks = np.ones((3, 1024, 2048), dtype=np.bool_)
        masks[1, 777, 1234] = False
        violation = review_violation_heatmap(masks, (0.0, 45.0, 90.0))
        self.assertEqual(violation.shape, (256, 512, 4))
        self.assertEqual(int(np.count_nonzero(violation[..., 0])), 1)

        changed = np.zeros((2, 1024, 2048), dtype=np.bool_)
        changed[1, 777, 1234] = True
        adjustment = review_adjustment_heatmap(changed)
        self.assertEqual(adjustment.shape, (256, 512, 4))
        self.assertEqual(adjustment.dtype, np.uint8)
        self.assertEqual(int(np.count_nonzero(adjustment[..., 0])), 1)


if __name__ == "__main__":
    unittest.main()
