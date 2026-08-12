import unittest
from amazon_ads_control.outcome import parse_tool_outcome


class OutcomeTests(unittest.TestCase):
    def test_amazon_bulk_success(self):
        out = parse_tool_outcome({"success": [{"targetId": "1"}], "error": []})
        self.assertEqual(out.status, "success")
        self.assertTrue(out.terminal_success)

    def test_partial_bulk(self):
        out = parse_tool_outcome({"success": [{"id": "1"}], "error": [{"code": "bad"}]})
        self.assertEqual(out.status, "partial")
        self.assertEqual((out.success_count, out.error_count), (1, 1))

    def test_pending(self):
        self.assertEqual(parse_tool_outcome({"status": "processing", "reportId": "r"}).status, "pending")

    def test_explicit_failure(self):
        self.assertEqual(parse_tool_outcome({"status": "failed", "error": "x"}).status, "failure")

    def test_identifier_success_for_read_or_job(self):
        self.assertEqual(parse_tool_outcome({"campaignId": "c"}, operation="read").status, "success")
        self.assertEqual(parse_tool_outcome({"reportId": "r"}, operation="job").status, "success")

    def test_identifier_only_does_not_confirm_write(self):
        out = parse_tool_outcome({"targetId": "t"}, operation="write")
        self.assertEqual(out.status, "unknown")
        self.assertFalse(out.terminal_success)

    def test_unclassified_write_list_is_unknown(self):
        self.assertEqual(parse_tool_outcome([{"targetId": "t"}], operation="write").status, "unknown")

    def test_explicit_write_list_is_success(self):
        self.assertEqual(parse_tool_outcome([{"status": "success", "targetId": "t"}], operation="write").status, "success")

    def test_unstructured_is_unknown(self):
        self.assertEqual(parse_tool_outcome("all good").status, "unknown")

    def test_error_count_zero_is_not_false_failure(self):
        out = parse_tool_outcome({"status": "success", "errorCount": 0})
        self.assertEqual(out.status, "success")

    def test_error_count_is_structured_failure(self):
        out = parse_tool_outcome({"successCount": 2, "errorCount": 1})
        self.assertEqual(out.status, "partial")

    def test_plain_object_is_not_assumed_success(self):
        self.assertEqual(parse_tool_outcome({"message": "accepted maybe"}).status, "unknown")
