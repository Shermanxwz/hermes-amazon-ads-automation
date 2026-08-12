import unittest
from amazon_ads_control.schema_validation import validate_instance


class SchemaValidationTests(unittest.TestCase):
    def test_required_types_enum_and_range(self):
        schema={"parameters":{"type":"object","required":["id","bid","state"],"additionalProperties":False,"properties":{"id":{"type":"string","minLength":1},"bid":{"type":"number","minimum":.02,"maximum":10},"state":{"type":"string","enum":["ENABLED","PAUSED"]}}}}
        self.assertEqual(validate_instance({"id":"x","bid":1,"state":"ENABLED"},schema),[])
        errors=validate_instance({"id":"","bid":99,"state":"BAD","extra":1},schema)
        self.assertGreaterEqual(len(errors),4)

    def test_nested_array(self):
        schema={"type":"object","properties":{"items":{"type":"array","items":{"type":"object","required":["id"],"properties":{"id":{"type":"integer"}}}}}}
        self.assertEqual(validate_instance({"items":[{"id":1}]},schema),[])
        self.assertTrue(validate_instance({"items":[{"id":"1"}]},schema))

    def test_one_of(self):
        schema={"oneOf":[{"type":"string"},{"type":"integer"}]}
        self.assertEqual(validate_instance("x",schema),[]); self.assertTrue(validate_instance({},schema))

    def test_local_ref_allof_const_pattern_and_collection_bounds(self):
        schema={
            "type":"object","required":["items"],
            "$defs":{"id":{"type":"string","pattern":"^t-[0-9]+$"}},
            "properties":{"items":{"type":"array","minItems":1,"maxItems":1,"uniqueItems":True,
                "items":{"allOf":[{"type":"object","properties":{"id":{"$ref":"#/$defs/id"},"kind":{"const":"target"}},"required":["id","kind"],"additionalProperties":False}]}}}
        }
        self.assertEqual(validate_instance({"items":[{"id":"t-1","kind":"target"}]},schema),[])
        errors=validate_instance({"items":[{"id":"bad","kind":"campaign","extra":1},{"id":"bad","kind":"campaign","extra":1}]},schema)
        self.assertTrue(any("maxItems" in x for x in errors)); self.assertTrue(any("pattern" in x for x in errors)); self.assertTrue(any("const" in x for x in errors))

class AdvancedSchemaValidationTests(unittest.TestCase):
    def test_boolean_not_conditionals_and_siblings(self):
        self.assertTrue(validate_instance("x", False))
        schema={"oneOf":[{"type":"string"},{"type":"number"}],"type":"string","minLength":3}
        self.assertTrue(any("minLength" in e for e in validate_instance("x",schema)))
        self.assertTrue(any("forbidden" in e for e in validate_instance("admin",{"not":{"const":"admin"}})))
        conditional={"type":"object","if":{"required":["bid"]},"then":{"required":["targetId"]},"else":{"required":["campaignId"]}}
        self.assertTrue(validate_instance({"bid":1},conditional)); self.assertEqual(validate_instance({"bid":1,"targetId":"t"},conditional),[])

    def test_additional_dependent_property_names_and_contains(self):
        schema={"type":"object","properties":{"a":{"type":"number"}},"additionalProperties":{"type":"string"},"dependentRequired":{"a":["label"]},"propertyNames":{"pattern":"^[a-z]+$"}}
        errors=validate_instance({"a":1,"extra":2},schema)
        self.assertTrue(any("expected string" in e for e in errors)); self.assertTrue(any("label" in e for e in errors))
        array={"type":"array","prefixItems":[{"const":"head"}],"items":{"type":"number"},"contains":{"type":"number","minimum":5},"minContains":1,"maxContains":1,"uniqueItems":True}
        self.assertEqual(validate_instance(["head",1,5],array),[])
        self.assertTrue(validate_instance(["head",5,5],array))

    def test_nonfinite_refs_and_unsupported_assertions_fail_closed(self):
        self.assertTrue(any("finite" in e or "expected number" in e for e in validate_instance(float("nan"),{"type":"number"})))
        self.assertTrue(any("unresolved" in e for e in validate_instance({}, {"$ref":"#/missing"})))
        self.assertTrue(any("unsupported" in e for e in validate_instance({}, {"unevaluatedProperties":False})))
        ref={"$defs":{"id":{"type":"string","minLength":2}},"$ref":"#/$defs/id","maxLength":3}
        self.assertEqual(validate_instance("abc",ref),[]); self.assertTrue(validate_instance("a",ref)); self.assertTrue(validate_instance("abcd",ref))
