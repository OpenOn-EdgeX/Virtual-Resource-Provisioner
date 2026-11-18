import unittest
from fractions import Fraction
from itertools import product

from capacity import available_resources, can_place_pods


def vector(cpu, memory, gpu):
    return {"cpu": cpu, "memory": memory, "gpu": gpu}


class CapacityTests(unittest.TestCase):
    def test_usage_adjusted_headroom(self):
        result = available_resources(vector(8, 100, 4), vector(6, 80, 2),
                                     vector(1, 50, 1), vector(1, 20, 3))
        self.assertEqual(result, vector(5, 30, 0))
        self.assertEqual(available_resources(*[vector(1, 1, 1)] * 4), vector(0, 0, 0))

    def test_headroom_clamps_overreported_usage(self):
        self.assertEqual(available_resources(vector(1, 1, 1), vector(1, 1, 1),
                                             vector(2, 2, 2), vector(0, 0, 0)), vector(0, 0, 0))

    def test_per_node_fit_not_pool_totals(self):
        self.assertFalse(can_place_pods([vector(4, 8, 0), vector(1, 8, 2)], vector(2, 2, 1), 1))

    def test_fractional_cpu_and_unchanged_input(self):
        node = vector(0.3, 3, 0)
        request = vector(0.1, 1, 0)
        self.assertTrue(can_place_pods([node], request, 3))
        self.assertFalse(can_place_pods([node], request, 4))
        self.assertEqual(node, vector(0.3, 3, 0))
        self.assertEqual(request, vector(0.1, 1, 0))
        self.assertTrue(can_place_pods([vector(1, 1, 0)], vector(Fraction(1, 3), 0, 0), 3))

    def test_no_nodes(self):
        self.assertTrue(can_place_pods([], vector(1, 1, 0), 0))
        self.assertFalse(can_place_pods([], vector(0, 0, 0), 1))

    def test_invalid_quantities_and_count(self):
        for value in (-1, float("inf"), float("nan"), True, "100m"):
            with self.subTest(cpu=value), self.assertRaises(ValueError):
                can_place_pods([vector(value, 1, 0)], vector(1, 1, 0), 1)
        for value in (0.5, False):
            with self.subTest(gpu=value), self.assertRaises(ValueError):
                can_place_pods([], vector(0, 0, value), 0)
        for count in (-1, False, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                can_place_pods([], vector(0, 0, 0), count)

    def test_multiple_nodes_against_independent_slot_count(self):
        nodes = [vector(8, 2, 0), vector(1, 8, 2), vector(4, 4, 1)]
        for cpu, memory, gpu in product((1, 2, 4), (1, 2), (0, 1, 2)):
            req = vector(cpu, memory, gpu)
            slots = sum(min(node[k] // v for k, v in req.items() if v) for node in nodes)
            with self.subTest(request=req):
                self.assertTrue(can_place_pods(nodes, req, slots))
                self.assertFalse(can_place_pods(reversed(nodes), req, slots + 1))


if __name__ == "__main__":
    unittest.main()
