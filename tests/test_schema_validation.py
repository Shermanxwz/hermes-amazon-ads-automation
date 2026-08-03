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
