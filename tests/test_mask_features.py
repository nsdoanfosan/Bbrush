import unittest
from array import array
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "sculpt" / "mask_features.py"
SPEC = spec_from_file_location("bbrush_mask_features", MODULE_PATH)
MASK_FEATURES = module_from_spec(SPEC)
SPEC.loader.exec_module(MASK_FEATURES)
crease_boundary_mask = MASK_FEATURES.crease_boundary_mask


class CreaseBoundaryMaskTests(unittest.TestCase):
    def test_propagates_blender_squared_boundary_falloff(self):
        edge_vertices = array("i", [0, 1, 1, 2, 2, 3, 3, 4])
        crease_values = array("f", [0.0, 1.0, 0.0, 0.0])

        result = crease_boundary_mask(
            5, edge_vertices, crease_values, threshold=0.5, propagation_steps=3
        )

        self.assertAlmostEqual(result[0], (2.0 / 3.0) ** 2, places=6)
        self.assertAlmostEqual(result[1], 1.0, places=6)
        self.assertAlmostEqual(result[2], 1.0, places=6)
        self.assertAlmostEqual(result[3], (2.0 / 3.0) ** 2, places=6)
        self.assertAlmostEqual(result[4], (1.0 / 3.0) ** 2, places=6)

    def test_threshold_can_leave_mask_empty(self):
        result = crease_boundary_mask(
            2,
            array("i", [0, 1]),
            array("f", [0.49]),
            threshold=0.5,
            propagation_steps=1,
        )

        self.assertEqual(list(result), [0.0, 0.0])

    def test_rejects_mismatched_edge_arrays(self):
        with self.assertRaises(ValueError):
            crease_boundary_mask(
                2,
                array("i", [0, 1, 1, 0]),
                array("f", [1.0]),
                threshold=0.5,
                propagation_steps=1,
            )


if __name__ == "__main__":
    unittest.main()
