from frappe.tests.utils import FrappeTestCase

from batch_stock_guard.batch_stock_guard.compat import call_with_supported_kwargs, filter_kwargs


class TestCompat(FrappeTestCase):
    def test_filter_kwargs_keeps_only_supported_keys(self):
        def sample(batch_no=None, warehouse=None):
            return batch_no, warehouse

        filtered = filter_kwargs(
            sample,
            {"batch_no": "B-1", "warehouse": "Main - T", "ignore_reserved_stock": True},
        )

        self.assertEqual(filtered, {"batch_no": "B-1", "warehouse": "Main - T"})

    def test_call_with_supported_kwargs_ignores_unknown_parameters(self):
        captured = {}

        def sample(batch_no=None, qty=None):
            captured["batch_no"] = batch_no
            captured["qty"] = qty
            return "ok"

        result = call_with_supported_kwargs(
            sample,
            batch_no="B-2",
            qty=-5,
            warehouse="Stores - T",
            posting_datetime="2026-04-15 12:00:00",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(captured, {"batch_no": "B-2", "qty": -5})

    def test_call_with_supported_kwargs_supports_optional_newer_parameters(self):
        captured = {}

        def sample(batch_no=None, warehouse=None, qty=None):
            captured["batch_no"] = batch_no
            captured["warehouse"] = warehouse
            captured["qty"] = qty

        call_with_supported_kwargs(sample, batch_no="B-3", warehouse="WH-1", qty=-1)

        self.assertEqual(captured, {"batch_no": "B-3", "warehouse": "WH-1", "qty": -1})
