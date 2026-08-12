from __future__ import annotations

import unittest

from amazon_ads_control.transport_attestation import REQUIRED_SP_OPERATIONS, attest_profile_capabilities, validate_attestation


class TransportAttestationV4Tests(unittest.TestCase):
    def test_direct_ads_api_fallback_seals_every_required_sp_operation(self):
        attestation = attest_profile_capabilities(profile_id="p1", region="na", tools=[])
        self.assertTrue(attestation.sealed)
        self.assertEqual({row.operation for row in attestation.routes}, set(REQUIRED_SP_OPERATIONS))
        self.assertEqual(validate_attestation(attestation.as_dict()), [])

    def test_live_mcp_is_primary_when_atomic_tool_exists(self):
        tools = [{"registered_name": "mcp_amazon_ads_campaign_management_create_campaign", "native_name": "campaign_management-create_campaign", "semantic": "write", "schema_hash": "abc", "source": "hermes-registry:na"}]
        attestation = attest_profile_capabilities(profile_id="p1", region="na", tools=tools)
        route = next(row for row in attestation.routes if row.operation == "campaign.create")
        self.assertIsNotNone(route.primary)
        self.assertIsNotNone(route.fallback)


if __name__ == "__main__":
    unittest.main()
