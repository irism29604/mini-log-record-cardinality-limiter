"""Tests for mini_log_record_cardinality_limiter.core."""

import unittest

from mini_log_record_cardinality_limiter.core import (
    CardinalityLimiter,
    LimitResult,
)


class TestConstruction(unittest.TestCase):
    def test_default_policy_is_drop(self):
        lim = CardinalityLimiter(5)
        self.assertEqual(lim.policy, "drop")
        self.assertEqual(lim.seen_count(), 0)

    def test_invalid_max_combinations_zero(self):
        with self.assertRaises(ValueError):
            CardinalityLimiter(0)

    def test_invalid_max_combinations_negative(self):
        with self.assertRaises(ValueError):
            CardinalityLimiter(-1)

    def test_invalid_max_combinations_bool(self):
        # bool is a subclass of int but must be rejected.
        with self.assertRaises(TypeError):
            CardinalityLimiter(True)

    def test_invalid_max_combinations_string(self):
        with self.assertRaises(TypeError):
            CardinalityLimiter("5")

    def test_invalid_policy(self):
        with self.assertRaises(ValueError):
            CardinalityLimiter(5, policy="evict")

    def test_default_sentinel(self):
        lim = CardinalityLimiter(5, policy="bucket")
        self.assertEqual(lim.sentinel, "_cardinality_bucket_")

    def test_custom_sentinel(self):
        lim = CardinalityLimiter(5, policy="bucket", sentinel="OTHER")
        self.assertEqual(lim.sentinel, "OTHER")


class TestDropPolicy(unittest.TestCase):
    def test_under_ceiling_tracks_and_returns_original(self):
        lim = CardinalityLimiter(3, policy="drop")
        r = lim.process({"a": 1, "b": 2})
        self.assertEqual(r.attributes, {"a": 1, "b": 2})
        self.assertFalse(r.dropped)
        self.assertFalse(r.bucketed)
        self.assertEqual(lim.seen_count(), 2)

    def test_already_seen_pair_does_not_consume_budget(self):
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": 1})
        lim.process({"a": 1})
        self.assertEqual(lim.seen_count(), 1)

    def test_at_ceiling_then_new_pair_dropped(self):
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": 1})
        lim.process({"b": 2})
        self.assertEqual(lim.seen_count(), 2)
        r = lim.process({"c": 3})
        self.assertEqual(r.attributes, {})
        self.assertTrue(r.dropped)
        self.assertFalse(r.bucketed)
        self.assertEqual(lim.seen_count(), 2)

    def test_drop_mixed_batch_some_seen_some_new_over_ceiling(self):
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": 1, "b": 2})
        r = lim.process({"a": 1, "c": 3})
        self.assertEqual(r.attributes, {"a": 1})
        self.assertTrue(r.dropped)
        self.assertFalse(r.bucketed)

    def test_drop_mixed_batch_under_ceiling_all_tracked(self):
        lim = CardinalityLimiter(5, policy="drop")
        lim.process({"a": 1})
        r = lim.process({"a": 1, "b": 2})
        self.assertEqual(r.attributes, {"a": 1, "b": 2})
        self.assertFalse(r.dropped)
        self.assertEqual(lim.seen_count(), 2)


class TestBucketPolicy(unittest.TestCase):
    def test_over_ceiling_new_pair_bucketed(self):
        lim = CardinalityLimiter(1, policy="bucket")
        lim.process({"a": 1})
        r = lim.process({"b": 2})
        self.assertEqual(r.attributes, {"b": "_cardinality_bucket_"})
        self.assertTrue(r.bucketed)
        self.assertFalse(r.dropped)

    def test_bucket_uses_custom_sentinel(self):
        lim = CardinalityLimiter(1, policy="bucket", sentinel="X")
        lim.process({"a": 1})
        r = lim.process({"b": 2})
        self.assertEqual(r.attributes, {"b": "X"})

    def test_bucket_already_seen_pair_returned_unchanged(self):
        lim = CardinalityLimiter(1, policy="bucket")
        lim.process({"a": 1})
        r = lim.process({"a": 1})
        self.assertEqual(r.attributes, {"a": 1})
        self.assertFalse(r.bucketed)

    def test_bucket_mixed_batch(self):
        lim = CardinalityLimiter(1, policy="bucket")
        lim.process({"a": 1})
        r = lim.process({"a": 1, "b": 2})
        self.assertEqual(r.attributes, {"a": 1, "b": "_cardinality_bucket_"})
        self.assertTrue(r.bucketed)


class TestValueTypes(unittest.TestCase):
    def test_none_is_tracked_as_distinct_value(self):
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": None})
        r = lim.process({"a": None})
        self.assertEqual(r.attributes, {"a": None})
        self.assertFalse(r.dropped)
        self.assertEqual(lim.seen_count(), 1)

    def test_none_and_string_are_distinct_pairs(self):
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": None})
        r = lim.process({"a": "None"})
        self.assertEqual(r.attributes, {"a": "None"})
        self.assertFalse(r.dropped)
        self.assertEqual(lim.seen_count(), 2)

    def test_int_and_float_distinct_when_json_differs(self):
        # json.dumps(1) -> '1', json.dumps(1.0) -> '1.0' -> distinct.
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": 1})
        r = lim.process({"a": 1.0})
        self.assertEqual(r.attributes, {"a": 1.0})
        self.assertFalse(r.dropped)
        self.assertEqual(lim.seen_count(), 2)

    def test_bool_and_int_distinct(self):
        # json.dumps(True) -> 'true', json.dumps(1) -> '1' -> distinct.
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": True})
        r = lim.process({"a": 1})
        self.assertEqual(r.attributes, {"a": 1})
        self.assertFalse(r.dropped)
        self.assertEqual(lim.seen_count(), 2)

    def test_non_scalar_value_passed_through_not_tracked(self):
        lim = CardinalityLimiter(1, policy="drop")
        r = lim.process({"a": [1, 2, 3], "b": {"x": 1}})
        self.assertEqual(r.attributes, {"a": [1, 2, 3], "b": {"x": 1}})
        self.assertFalse(r.dropped)
        self.assertEqual(lim.seen_count(), 0)

    def test_non_scalar_does_not_consume_budget_so_scalar_still_allowed(self):
        lim = CardinalityLimiter(1, policy="drop")
        lim.process({"meta": [1, 2]})  # not tracked
        r = lim.process({"a": 1})  # should be tracked and allowed
        self.assertEqual(r.attributes, {"a": 1})
        self.assertFalse(r.dropped)
        self.assertEqual(lim.seen_count(), 1)


class TestStateAndMisc(unittest.TestCase):
    def test_input_not_mutated(self):
        lim = CardinalityLimiter(1, policy="bucket")
        src = {"a": 1}
        lim.process(src)
        lim.process({"b": 2})  # triggers bucketing
        self.assertEqual(src, {"a": 1})

    def test_reset_clears_state(self):
        lim = CardinalityLimiter(2, policy="drop")
        lim.process({"a": 1, "b": 2})
        self.assertEqual(lim.seen_count(), 2)
        lim.reset()
        self.assertEqual(lim.seen_count(), 0)
        r = lim.process({"a": 1})
        self.assertEqual(r.attributes, {"a": 1})
        self.assertFalse(r.dropped)

    def test_process_rejects_non_dict(self):
        lim = CardinalityLimiter(2)
        with self.assertRaises(TypeError):
            lim.process([("a", 1)])  # list, not dict

    def test_limit_result_default_fields(self):
        r = LimitResult(attributes={"a": 1})
        self.assertEqual(r.attributes, {"a": 1})
        self.assertFalse(r.dropped)
        self.assertFalse(r.bucketed)

    def test_empty_attributes_returns_empty_result(self):
        lim = CardinalityLimiter(2, policy="drop")
        r = lim.process({})
        self.assertEqual(r.attributes, {})
        self.assertFalse(r.dropped)
        self.assertFalse(r.bucketed)
        self.assertEqual(lim.seen_count(), 0)

    def test_max_combinations_property(self):
        lim = CardinalityLimiter(7)
        self.assertEqual(lim.max_combinations, 7)


class TestPackageExports(unittest.TestCase):
    def test_package_exports_names(self):
        import mini_log_record_cardinality_limiter as pkg
        self.assertTrue(hasattr(pkg, "CardinalityLimiter"))
        self.assertTrue(hasattr(pkg, "LimitResult"))
        self.assertIn("CardinalityLimiter", pkg.__all__)
        self.assertIn("LimitResult", pkg.__all__)


if __name__ == "__main__":
    unittest.main()
