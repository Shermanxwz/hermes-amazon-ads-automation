import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

postman=load("postman_sync",ROOT/"scripts/sync_official_contracts.py")
stream=load("stream_relay",ROOT/"integrations/marketing_stream_relay.py")


class IntegrationUtilityTests(unittest.TestCase):
    def test_postman_summary_detects_required_capabilities(self):
        folders=[]
        for name in ["Authentication OAuth","Profiles","Sponsored Products SP v3","Sponsored Brands SB v4","Sponsored Display","Reporting","Amazon Marketing Stream","Recommendations","Budget rules","Test accounts","Exports"]:
            folders.append({"name":name,"item":[{"name":"call","request":{"method":"GET","url":{"raw":"https://example/"+name}}}]})
        raw=json.dumps({
            "info":{"name":"Amazon Ads API","schema":"x"},
            "variable":[{"key":"accessToken"},{"key":"clientId"}],
            "item":folders,
        }).encode()
        summary=postman.summarize(raw,"fixture")
        self.assertTrue(all(summary["capabilities"].values())); self.assertEqual(summary["request_count"],11)


    def test_postman_authentication_detected_from_request_contract(self):
        raw=json.dumps({
            "info":{"name":"Amazon Ads API"},
            "item":[{
                "name":"Profiles",
                "item":[{"name":"List","request":{
                    "method":"GET",
                    "url":"https://example/profiles",
                    "auth":{"type":"bearer","bearer":[{"key":"token","value":"{{accessToken}}"}]},
                    "header":[{"key":"Amazon-Advertising-API-ClientId","value":"{{clientId}}"}],
                }}],
            }],
        }).encode()
        summary=postman.summarize(raw,"fixture")
        self.assertTrue(summary["capabilities"]["authentication"])
        self.assertNotIn("accessToken", json.dumps(summary.get("examples", [])))

    def test_postman_walk_nested(self):
        raw=json.dumps({"info":{},"item":[{"name":"A","item":[{"name":"B","item":[{"name":"C","request":{"method":"POST","url":"u"}}]}]}]}).encode()
        row=list(postman.walk_items(json.loads(raw)["item"]))[0]
        self.assertEqual(row["path"],"A / B / C"); self.assertEqual(row["method"],"POST")

    def test_marketing_stream_normalize_and_unwrap(self):
        event={"Records":[{"body":json.dumps({"detail":{"profileId":"p","datasetId":"budget-usage","campaignId":"c"},"id":"e1","time":"t"})}]}
        rows=stream._unwrap(event); normalized=stream.normalize(rows[0])
        self.assertEqual(normalized["profile_id"],"p"); self.assertEqual(normalized["dataset_id"],"budget-usage"); self.assertTrue(normalized["dedupe_key"])
