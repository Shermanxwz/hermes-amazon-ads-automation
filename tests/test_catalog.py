import unittest
from amazon_ads_control.catalog import (
    REGISTERED_PREFIX, catalog_digest, descriptor_from_payload, infer_family, infer_risk,
    infer_semantic, native_name_from_registered, registered_name, stable_schema_hash,
)


class CatalogTests(unittest.TestCase):
    def test_registered_name_round_trip(self):
        native = "campaign_management-update_campaign"
        self.assertEqual(registered_name(native), REGISTERED_PREFIX + "campaign_management_update_campaign")
        self.assertEqual(native_name_from_registered(registered_name(native)), "campaign_management_update_campaign")

    def test_semantics_are_conservative(self):
        self.assertEqual(infer_semantic("campaign_management-query_campaign"), "read")
        self.assertEqual(infer_semantic("campaign_management-update_campaign"), "write")
        self.assertEqual(infer_semantic("mystery-do_thing", {}), "unknown")

    def test_report_creation_is_a_bounded_data_job(self):
        descriptor = descriptor_from_payload({
            "registered_name": "mcp_amazon_ads_reporting_create_report",
            "native_name": "reporting-create_report",
            "schema": {"description": "Create a reporting job", "parameters": {"type": "object"}},
        })
        self.assertEqual(descriptor.family, "report")
        self.assertEqual(descriptor.semantic, "job")
        self.assertEqual(descriptor.risk, "medium")

    def test_description_fallback(self):
        self.assertEqual(infer_semantic("x", {"description": "Create a campaign"}), "write")
        self.assertEqual(infer_semantic("x", {"description": "Return campaign details"}), "read")

    def test_family_and_risk(self):
        self.assertEqual(infer_family("campaign_management-update_target"), "target")
        self.assertEqual(infer_family("recommendations-apply_recommendation"), "recommendation")
        self.assertEqual(infer_risk("account_management-update_user", "write", "account_admin"), "critical")
        self.assertEqual(infer_risk("campaign_management-update_target", "write", "target"), "medium")

    def test_hash_and_digest_are_stable(self):
        a = {"b": 2, "a": 1}; b = {"a": 1, "b": 2}
        self.assertEqual(stable_schema_hash(a), stable_schema_hash(b))
        d1 = descriptor_from_payload({"registered_name": "mcp_amazon_ads_x_query_x", "native_name": "x-query_x", "schema": a})
        d2 = descriptor_from_payload({"registered_name": "mcp_amazon_ads_y_query_y", "native_name": "y-query_y", "schema": {}})
        self.assertEqual(catalog_digest([d1, d2]), catalog_digest([d2, d1]))

    def test_descriptor_rejects_invalid_semantic(self):
        with self.assertRaises(ValueError):
            descriptor_from_payload({"registered_name": "x", "semantic": "maybe"})
