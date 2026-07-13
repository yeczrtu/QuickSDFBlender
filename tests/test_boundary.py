import unittest

from quick_sdf_blender.boundary import (
    curve_self_intersects,
    interpolate_curves,
    rasterize_closed_curve,
    resample_polyline,
    validate_curve,
)


class BoundaryTests(unittest.TestCase):
    def test_closed_square_rasterizes_inside(self):
        square = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]
        valid, message = validate_curve(square, closed=True)
        self.assertTrue(valid, message)
        mask = rasterize_closed_curve(square, 64, 64)
        self.assertEqual(len(mask), 64 * 64)
        self.assertGreater(sum(mask), 1000)
        self.assertLess(sum(mask), 1800)

    def test_self_intersection_is_rejected(self):
        bow = [(0.1, 0.1), (0.9, 0.9), (0.1, 0.9), (0.9, 0.1)]
        self.assertTrue(curve_self_intersects(bow, closed=True))
        valid, _message = validate_curve(bow, closed=True)
        self.assertFalse(valid)

    def test_resample_and_interpolate_preserve_count(self):
        a = resample_polyline([(0.0, 0.0), (1.0, 0.0)], 16)
        b = resample_polyline([(0.0, 1.0), (1.0, 1.0)], 16)
        middle = interpolate_curves(a, b, 0.5, count=16)
        self.assertEqual(len(middle), 16)
        self.assertAlmostEqual(middle[8][1], 0.5)


if __name__ == "__main__":
    unittest.main()
