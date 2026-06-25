from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from batch_stock_guard.batch_stock_guard import settings
from batch_stock_guard.batch_stock_guard.logic import valuation_guard


class TestSettingsAndValuationGuard(FrappeTestCase):
	def test_settings_defaults_enable_existing_features_when_doctype_missing(self):
		settings.clear_settings_cache()
		with patch.object(settings, "_doctype_available", return_value=False):
			self.assertTrue(settings.is_enabled("enable_total_stock_guard"))
			self.assertTrue(settings.is_enabled("enable_batch_bundle_override_logic"))

	def test_sales_invoice_guard_detects_corrupted_bin(self):
		doc = frappe._dict(
			doctype="Sales Invoice",
			name="SINV-TEST",
			update_stock=1,
			items=[
				frappe._dict(
					name="ROW-1",
					item_code="IMMUNITY 90",
					warehouse="Packaging Warehouse - SR",
					stock_qty=1,
					incoming_rate=28,
				)
			],
		)

		with (
			patch.object(valuation_guard, "_is_stock_item", return_value=True),
			patch.object(
				valuation_guard,
				"_get_bin",
				return_value=frappe._dict(
					actual_qty=8610,
					valuation_rate=-114996222.35,
					stock_value=-990117474510.43,
				),
			),
			patch.object(valuation_guard, "_get_bundle_rate", return_value=None),
			patch.object(
				valuation_guard,
				"get_float",
				side_effect=lambda fieldname: {
					"stock_value_block_limit": 990000000000,
					"stock_value_warning_limit": 900000000000,
					"max_allowed_valuation_rate": 1000000,
				}[fieldname],
			),
			patch.object(valuation_guard, "is_enabled", side_effect=lambda fieldname: fieldname != "allow_negative_valuation_rate"),
		):
			issues = valuation_guard.inspect_stock_valuation(doc)

		self.assertTrue(any(issue.severity == "block" for issue in issues))
		self.assertTrue(any(issue.item_code == "IMMUNITY 90" for issue in issues))

	def test_sales_invoice_guard_can_be_disabled(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-TEST", update_stock=1, items=[])

		with (
			patch.object(valuation_guard, "is_enabled", return_value=False),
			patch.object(valuation_guard, "inspect_stock_valuation") as inspect,
		):
			valuation_guard.validate_sales_invoice_stock_valuation(doc)

		inspect.assert_not_called()
