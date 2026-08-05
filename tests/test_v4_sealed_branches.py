from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.probabilistic_acos import (
    DelayModel,
    PosteriorConfig,
    account_aov,
    cumulative_delay_from_rows,
    estimate_acos_posterior,
)
from amazon_ads_control.sealed_envelope import (
    envelope_hash,
    marker_shape_valid,
    policy_for,
    standing_authorized,
)
from amazon_ads_control.sealed_plan import validate_standing_plan
from amazon_ads_control.strategy import OptimizationEngine, StrategyPolicy
from amazon_ads_control.strategy_v4_controls import global_budget, lifecycle
from amazon_ads_control.strategy_v4_support import context, decision_row, hourly, row_index
from amazon_ads_control.transport_attestation import (
    REQUIRED_SP_OPERATIONS,
    attest_profile_capabilities,
    validate_attestation,
)
from helpers import Environment, one_target_snapshot
from scripts import check_unified_api_contract as unified


CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "source": "hermes-registry:na",
    "schema": {
        "description": "Create one Sponsored Products campaign",
        "parameters": {
            "type": "object",
            "required": ["campaigns"],
            "properties": {
                "campaigns": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "budget", "state", "adProduct"],
                        "properties": {
                            "name": {"type": "string"},
                            "budget": {"type": "number", "minimum": 1},
                            "state": {"type": "string"},
                            "adProduct": {"type": "string"},
                        },
                    },
                }
            },
        },
    },
}


def campaign_args(name: str = "HERMES-SP-P1-EXACT-001", budget: float = 20, state: str = "PAUSED", product: str = "SPONSORED_PRODUCTS"):
    return {"campaigns": [{"name": name, "budget": budget, "state": state, "adProduct": product}]}


class ProbabilisticBranchTests(unittest.TestCase):
    def test_delay_curve_normalization_interpolation_and_learning(self):
        default = DelayModel.from_value(None)
        self.assertEqual(default.maturity(None), 1.0)
        self.assertEqual(default.maturity(99), 1.0)
        learned = DelayModel.from_value({"curve": [0.2, 0.1, 0.8]})
        self.assertEqual(learned.cumulative, (0.2, 0.2, 0.8, 1.0))
        self.assertAlmostEqual(learned.maturity(1.5), 0.5)
        rows = [
            {"age_days": 0, "cumulative_fraction": 0.4},
            {"age_days": 0, "cumulative_fraction": 0.6},
            {"day": 2, "maturity": 0.9},
            {"day": "bad", "maturity": 1},
            {"day": 3, "maturity": 0},
        ]
        fitted = cumulative_delay_from_rows(rows)
        self.assertEqual(fitted.cumulative, (0.5, 0.5, 0.9, 1.0))
        self.assertIsInstance(cumulative_delay_from_rows([]), DelayModel)

    def test_aov_fallback_account_and_median_paths(self):
        self.assertEqual(account_aov({}, 17), 17)
        self.assertEqual(account_aov({"account": {"sales": 90, "orders": 3}}, 17), 30)
        snapshot = {
            "account": {},
            "targets": [
                {"sales": 100, "orders": 2},
                {"sales": 10, "orders": 1},
                {"sales": "bad", "orders": 2},
            ],
        }
        self.assertEqual(account_aov(snapshot, 17), 50)

    def test_posterior_handles_invalid_recent_and_deterministic_inputs(self):
        recent = estimate_acos_posterior(
            {"clicks": "40", "orders": "2", "sales": "80", "spend": "30"},
            target_acos=30,
            max_acos=45,
            age_days=0,
            config=PosteriorConfig(uncertainty_multiplier=0),
        )
        self.assertGreater(recent.expected_final_sales, 0)
        self.assertGreater(recent.acos_high or 0, recent.acos_low or 0)
        invalid = estimate_acos_posterior(
            {"clicks": "nan", "orders": None, "sales": -1, "spend": 0},
            target_acos="bad",
            max_acos="bad",
            age_days=None,
        )
        self.assertIsNone(invalid.expected_acos)
        self.assertEqual(invalid.p_acos_over_target, 0)
        self.assertEqual(invalid.p_acos_under_target, 1)

    def test_policy_clamps_delay_flags_and_budget_threshold(self):
        policy = StrategyPolicy.from_mapping({
            "delay_curve": "0.2,0.1,0.7",
            "posterior_budget_scale_probability": 2,
            "posterior_min_confidence": -1,
            "enable_hourly_pacing": "off",
            "sealed_sp_namespace": "X" * 80,
            "sealed_sp_max_campaign_creates_per_day": 0,
        })
        self.assertEqual(policy.delay_curve, (policy.delay_curve[0], policy.delay_curve[0], policy.delay_curve[2], 1))
        self.assertEqual(policy.posterior_budget_scale_probability, policy.posterior_budget_scale_probability.__class__("0.95"))
        self.assertEqual(policy.posterior_min_confidence, 0)
        self.assertFalse(policy.enable_hourly_pacing)
        self.assertEqual(len(policy.sealed_sp_namespace), 40)
        self.assertEqual(policy.sealed_sp_max_campaign_creates_per_day, 2)


class ControllerBranchTests(unittest.TestCase):
    def test_exact_placement_evidence_wins_over_campaign_aggregate(self):
        snapshot = {
            "campaigns": [{"campaign_id": "c1", "clicks": 10, "orders": 1, "sales": 20, "spend": 20}],
            "placements": [
                {"campaign_id": "c1", "placement": "TOP_OF_SEARCH", "clicks": 20, "orders": 3, "sales": 100, "spend": 10},
                {"campaign_id": "c1", "placement": "PRODUCT_PAGE", "clicks": 20, "orders": 1, "sales": 20, "spend": 20},
            ],
        }
        plan = OptimizationEngine()._placement(
            "p1", "c1", "PLACEMENT_TOP", 0, 10, 10,
            "ADS-PLACEMENT-TOS-SCALE", 60, "test", {"placement": "PLACEMENT_TOP"},
        )
        selected = decision_row(plan, row_index(snapshot))
        self.assertEqual(selected["sales"], 100)

    def test_lifecycle_quarantine_and_verified_recovery(self):
        policy = StrategyPolicy.from_mapping({})
        engine_context = context(one_target_snapshot(), policy)
        bad = one_target_snapshot()
        bad["targets"][0].update({"state": "ENABLED", "clicks": 180, "orders": 0, "sales": 0, "spend": 240})
        quarantined = lifecycle(bad, policy, context(bad, policy), 6)
        self.assertEqual(quarantined[0].rule_id, "ADS-LIFECYCLE-QUARANTINE")
        self.assertEqual(quarantined[0].payload["after"], "PAUSED")

        good = one_target_snapshot()
        good["targets"][0].update({
            "state": "PAUSED", "recovery_ready": True, "clicks": 180,
            "orders": 30, "sales": 1500, "spend": 180,
        })
        recovered = lifecycle(good, policy, context(good, policy), 6)
        self.assertEqual(recovered[0].rule_id, "ADS-LIFECYCLE-RECOVERY")
        self.assertTrue(recovered[0].payload["standing_authorization"]["verified_create"])
        self.assertEqual(engine_context[0].maturity(6), 1)

    def test_global_budget_transfer_requires_cap_and_is_exposure_neutral(self):
        policy = StrategyPolicy.from_mapping({"posterior_reduce_probability": 0.7, "posterior_scale_probability": 0.7})
        snapshot = one_target_snapshot()
        snapshot["account"].update({"daily_budget_cap": 200, "sales": 1600, "orders": 32})
        snapshot["campaigns"] = [
            {"campaign_id": "winner", "state": "ENABLED", "budget": 100, "clicks": 200, "orders": 30, "sales": 1500, "spend": 200, "budget_usage_percent": 95},
            {"campaign_id": "loser", "state": "ENABLED", "budget": 100, "clicks": 200, "orders": 2, "sales": 100, "spend": 300, "budget_usage_percent": 80},
        ]
        snapshot["budget_usage"] = [
            {"campaign_id": "winner", "budget_usage_percent": 95},
            {"campaign_id": "loser", "budget_usage_percent": 80},
        ]
        decisions = global_budget(OptimizationEngine(), snapshot, policy, context(snapshot, policy), 6, [])
        self.assertEqual({d.rule_id for d in decisions}, {"ADS-GLOBAL-BUDGET-ALLOCATE-WINNER", "ADS-GLOBAL-BUDGET-ALLOCATE-LOSER"})
        self.assertAlmostEqual(sum(d.payload["after"] - d.payload["before"] for d in decisions), 0, places=6)
        snapshot["account"].pop("daily_budget_cap")
        self.assertEqual(global_budget(OptimizationEngine(), snapshot, policy, context(snapshot, policy), 6, []), [])

    def test_hourly_pacing_emits_bounded_up_and_down_actions(self):
        policy = StrategyPolicy.from_mapping({"posterior_reduce_probability": 0.7, "posterior_scale_probability": 0.7})
        snapshot = one_target_snapshot()
        snapshot["account"].update({"sales": 500, "orders": 10})
        snapshot["targets"] = [
            {"target_id": "down", "campaign_id": "bad", "state": "ENABLED", "bid": 1, "clicks": 30, "orders": 0, "sales": 0, "spend": 60},
            {"target_id": "up", "campaign_id": "good", "state": "ENABLED", "bid": 1, "clicks": 30, "orders": 6, "sales": 300, "spend": 30},
        ]
        snapshot["hourly"] = [
            {"campaign_id": "bad", "hour": 5, "budget": 100, "clicks": 60, "orders": 0, "sales": 0, "spend": 60},
            {"campaign_id": "good", "hour": 18, "budget": 100, "clicks": 30, "orders": 6, "sales": 300, "spend": 10},
        ]
        decisions = hourly(OptimizationEngine(), snapshot, policy, context(snapshot, policy))
        self.assertEqual({d.rule_id for d in decisions}, {"ADS-INTRADAY-PACE-DOWN", "ADS-INTRADAY-PACE-UP"})
        self.assertTrue(all(d.payload["change_percent"] <= 8 for d in decisions))


class SealedEnvelopeBranchTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.plan(one_target_snapshot())
        self.tool = descriptor_from_payload(CREATE_CAMPAIGN).as_dict()
        self.env.store.sync_catalog([descriptor_from_payload(CREATE_CAMPAIGN)])
        self.policy = policy_for(self.env.store, "p1")
        self.marker = {
            "version": 1,
            "validated": True,
            "scope": "sealed-sp",
            "profile_id": "p1",
            "ad_product": "SPONSORED_PRODUCTS",
            "observed_in_ads": True,
            "purpose": "structural_maintenance",
            "desired_state": "PAUSED",
            "envelope_hash": envelope_hash("p1", self.policy),
        }

    def tearDown(self):
        self.env.close()

    def decision(self, action: str = "create_campaign", args=None, marker=None, family: str = "campaign"):
        return {
            "profile_id": "p1",
            "action_type": action,
            "expected_family": family,
            "payload": {
                "approved_args": args or campaign_args(),
                "ad_product": "SPONSORED_PRODUCTS",
                "standing_authorization": deepcopy(marker if marker is not None else self.marker),
            },
        }

    def test_valid_marker_and_core_denials(self):
        valid = self.decision()
        self.assertTrue(marker_shape_valid(valid))
        self.assertTrue(standing_authorized(self.env.store, valid, self.tool)[0])
        self.assertFalse(standing_authorized(self.env.store, {"profile_id": "p1", "payload": {}}, self.tool)[0])

        changed = self.decision()
        changed["payload"]["standing_authorization"]["envelope_hash"] = "stale"
        self.assertIn("scope changed", standing_authorized(self.env.store, changed, self.tool)[1])

        non_sp = self.decision(args=campaign_args(product="SPONSORED_BRANDS"))
        self.assertIn("Sponsored Products only", standing_authorized(self.env.store, non_sp, self.tool)[1])

        destructive = self.decision(action="delete_campaign")
        destructive_tool = {**self.tool, "native_name": "campaign_management-delete_campaign"}
        self.assertIn("permanently blocked", standing_authorized(self.env.store, destructive, destructive_tool)[1])

        mismatch = self.decision(family="target")
        self.assertIn("family", standing_authorized(self.env.store, mismatch, {**self.tool, "family": "target"})[1])

    def test_state_and_product_ad_require_verification(self):
        enable = self.decision(action="enable", args={"state": "ENABLED"}, family="target")
        self.assertIn("verified", standing_authorized(self.env.store, enable, {**self.tool, "family": "target", "native_name": "target-enable"})[1])

        ad_marker = deepcopy(self.marker)
        ad_marker["desired_state"] = "PAUSED"
        ad = self.decision(action="create_ad", args={"state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS"}, marker=ad_marker, family="ad")
        self.assertIn("ASIN", standing_authorized(self.env.store, ad, {**self.tool, "family": "ad", "native_name": "product_ad-create"})[1])

        self.env.store.update_settings({"sealed_sp_autonomy_enabled": False})
        self.assertIn("disabled", standing_authorized(self.env.store, self.decision(), self.tool)[1])

    def test_plan_validator_rejects_shape_schema_and_exposure(self):
        base = {
            "profile": {"profile_id": "p1"},
            "actions": [{
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "arguments": campaign_args(),
            }],
        }
        self.assertEqual(len(validate_standing_plan(self.env.service, base)), 1)
        for actions, message in (([], "1-50"), (["bad"], "object"), ([{"tool_name": "missing", "arguments": {}}], "live catalog")):
            payload = {**base, "actions": actions}
            with self.assertRaisesRegex(ValueError, message):
                validate_standing_plan(self.env.service, payload)

        invalid_schema = deepcopy(base)
        invalid_schema["actions"][0]["arguments"] = {"campaigns": []}
        with self.assertRaisesRegex(ValueError, "schema"):
            validate_standing_plan(self.env.service, invalid_schema)

        too_many = deepcopy(base)
        too_many["actions"] = [deepcopy(base["actions"][0]) for _ in range(3)]
        with self.assertRaisesRegex(ValueError, "creation limit"):
            validate_standing_plan(self.env.service, too_many)

        self.env.store.update_settings({"sealed_sp_max_new_budget_per_day": 30})
        over_budget = deepcopy(base)
        over_budget["actions"] = [deepcopy(base["actions"][0]) for _ in range(2)]
        with self.assertRaisesRegex(ValueError, "budget envelope"):
            validate_standing_plan(self.env.service, over_budget)


class AttestationAndUnifiedBranchTests(unittest.TestCase):
    def test_attestation_mcp_only_missing_and_validation_errors(self):
        tools = [
            {"name": "profile-query", "annotations": {"readOnlyHint": True}},
            {"name": "campaign-create", "classification": {"authority": "planned_executor_only"}},
            {"name": "campaign-delete", "semantic": "write"},
            "ignored",
        ]
        attestation = attest_profile_capabilities(
            profile_id="p1",
            region="NA",
            tools=tools,
            direct_api_operations=[],
        )
        self.assertFalse(attestation.sealed)
        self.assertTrue(attestation.as_dict()["missing"])
        campaign = next(route for route in attestation.routes if route.operation == "campaign.create")
        self.assertEqual(campaign.primary, "campaign-create")
        self.assertIsNone(campaign.fallback)

        errors = validate_attestation({"version": 2, "profile_id": "", "region": "x", "routes": "bad", "sealed": True})
        self.assertIn("unsupported attestation version", errors)
        self.assertIn("routes must be an array", errors)

        partial = {"version": 1, "profile_id": "p1", "region": "na", "routes": [], "sealed": True}
        errors = validate_attestation(partial)
        self.assertGreaterEqual(len(errors), len(REQUIRED_SP_OPERATIONS))
        self.assertIn("sealed flag does not match", errors[-1])

    def test_unified_contract_error_and_local_main_paths(self):
        malformed = {
            "item": [
                "ignored",
                {"name": "Folder", "item": [
                    {"name": "Bad body", "request": {"method": "POST", "url": "https://example.com/not-unified", "body": {"raw": "{"}}},
                ]},
            ]
        }
        manifest = unified.summarize(json.dumps(malformed).encode(), "fixture")
        self.assertFalse(manifest["ok"])
        self.assertTrue(manifest["missing_ga_resources"])
        self.assertTrue(any("non-Unified" in error for error in manifest["errors"]))

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "unified.json"
            output = Path(td) / "manifest.json"
            source.write_text(json.dumps(malformed), encoding="utf-8")
            self.assertEqual(unified.load(str(source)), source.read_bytes())
            self.assertEqual(unified.main.__module__, unified.__name__)
            # Main must fail closed for a materially incomplete local collection.
            import sys
            old = sys.argv
            try:
                sys.argv = ["check_unified_api_contract.py", "--source", str(source), "--output", str(output), "--check"]
                self.assertEqual(unified.main(), 1)
            finally:
                sys.argv = old
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
