from __future__ import annotations

import unittest

from amazon_ads_control.write_batch_hardening import _violation


class WriteBatchHardeningTests(unittest.TestCase):
    def test_two_entities_are_blocked(self):
        violation = _violation(
            {"targets": [{"targetId": "1"}, {"targetId": "2"}]},
            1,
        )
        self.assertIn("entity batch", violation or "")

    def test_one_target_may_have_multiple_expression_clauses(self):
        payload = {
            "targets": [{
                "campaignId": "c1",
                "adGroupId": "g1",
                "expression": [
                    {"type": "asinSameAs", "value": "B001"},
                    {"type": "brandSameAs", "value": "Brand"},
                ],
            }]
        }
        self.assertIsNone(_violation(payload, 1))

    def test_unknown_multi_item_list_fails_closed(self):
        violation = _violation({"mystery": [1, 2]}, 1)
        self.assertIn("unrecognized multi-item list", violation or "")

    def test_multiple_filters_inside_one_entity_are_allowed(self):
        payload = {
            "campaigns": [{
                "name": "one",
                "filters": [
                    {"field": "marketplace", "value": "US"},
                    {"field": "state", "value": "ENABLED"},
                ],
            }]
        }
        self.assertIsNone(_violation(payload, 1))


if __name__ == "__main__":
    unittest.main()
