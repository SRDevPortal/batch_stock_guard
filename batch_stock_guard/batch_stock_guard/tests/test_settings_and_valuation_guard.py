from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from batch_stock_guard.batch_stock_guard import settings
from batch_stock_guard.batch_stock_guard.logic import valuation_guard


class TestSettingsAndValuationGuard(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._previous_ignore_bcn_guard = getattr(
			frappe.flags,
			"ignore_sales_invoice_valuation_guard_for_bulk_credit_note",
			None,
		)
		self._previous_bcn_name = getattr(frappe.flags, "bulk_credit_note_name", None)

	def tearDown(self):
		if self._previous_ignore_bcn_guard is None:
			frappe.flags.pop("ignore_sales_invoice_valuation_guard_for_bulk_credit_note", None)
		else:
			frappe.flags.ignore_sales_invoice_valuation_guard_for_bulk_credit_note = self._previous_ignore_bcn_guard

		if self._previous_bcn_name is None:
			frappe.flags.pop("bulk_credit_note_name", None)
		else:
			frappe.flags.bulk_credit_note_name = self._previous_bcn_name

		super().tearDown()

	def test_settings_defaults_enable_existing_features_when_doctype_missing(self):
		settings.clear_settings_cache()
		with patch.object(settings, "_doctype_available", return_value=False):
			self.assertTrue(settings.is_enabled("enable_total_stock_guard"))
			self.assertTrue(settings.is_enabled("enable_batch_bundle_override_logic"))
			self.assertFalse(settings.is_enabled("allow_bulk_credit_note_valuation_bypass"))

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
			patch.object(
				valuation_guard,
				"is_enabled",
				side_effect=lambda fieldname: fieldname != "allow_negative_valuation_rate",
			),
		):
			issues = valuation_guard.inspect_stock_valuation(doc)

		self.assertTrue(any(issue.severity == "block" for issue in issues))
		self.assertTrue(any(issue.item_code == "IMMUNITY 90" for issue in issues))

	def test_sales_invoice_guard_can_ignore_negative_stock_value(self):
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
					valuation_rate=28,
					stock_value=-990117474510.43,
				),
			),
			patch.object(valuation_guard, "_get_bundle_rate", return_value=None),
			patch.object(valuation_guard, "_get_latest_previous_sle", return_value=frappe._dict()),
			patch.object(
				valuation_guard,
				"get_float",
				side_effect=lambda fieldname: {
					"stock_value_block_limit": 990000000000,
					"stock_value_warning_limit": 900000000000,
					"max_allowed_valuation_rate": 1000000,
				}[fieldname],
			),
			patch.object(
				valuation_guard,
				"is_enabled",
				side_effect=lambda fieldname: fieldname == "allow_negative_stock_value",
			),
		):
			issues = valuation_guard.inspect_stock_valuation(doc)

		self.assertFalse([issue for issue in issues if issue.source == "bin"])

	def test_sales_invoice_guard_can_be_disabled(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-TEST", update_stock=1, items=[])

		with (
			patch.object(valuation_guard, "is_enabled", return_value=False),
			patch.object(valuation_guard, "inspect_stock_valuation") as inspect,
		):
			valuation_guard.validate_sales_invoice_stock_valuation(doc)

		inspect.assert_not_called()

	def test_bulk_credit_note_bypass_setting_disabled_still_validates(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-RETURN", is_return=1, update_stock=1, items=[])
		frappe.flags.ignore_sales_invoice_valuation_guard_for_bulk_credit_note = True
		frappe.flags.bulk_credit_note_name = "BCN-TEST"

		with (
			patch.object(
				valuation_guard,
				"is_enabled",
				side_effect=lambda fieldname: fieldname != "allow_bulk_credit_note_valuation_bypass",
			),
			patch.object(valuation_guard, "inspect_stock_valuation", return_value=[]) as inspect,
		):
			valuation_guard.validate_sales_invoice_stock_valuation(doc)

		inspect.assert_called_once_with(doc)

	def test_bulk_credit_note_bypass_skips_sales_invoice_valuation_guard(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-RETURN", is_return=1, update_stock=1, items=[])
		frappe.flags.ignore_sales_invoice_valuation_guard_for_bulk_credit_note = True
		frappe.flags.bulk_credit_note_name = "BCN-TEST"

		with (
			patch.object(valuation_guard, "is_enabled", return_value=True),
			patch.object(valuation_guard, "inspect_stock_valuation") as inspect,
		):
			valuation_guard.validate_sales_invoice_stock_valuation(doc)

		inspect.assert_not_called()

	def test_bulk_credit_note_bypass_requires_bulk_credit_note_name(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-RETURN", is_return=1, update_stock=1, items=[])
		frappe.flags.ignore_sales_invoice_valuation_guard_for_bulk_credit_note = True
		frappe.flags.pop("bulk_credit_note_name", None)

		with (
			patch.object(valuation_guard, "is_enabled", return_value=True),
			patch.object(valuation_guard, "inspect_stock_valuation", return_value=[]) as inspect,
		):
			valuation_guard.validate_sales_invoice_stock_valuation(doc)

		inspect.assert_called_once_with(doc)

	def test_bulk_credit_note_bypass_does_not_skip_normal_sales_invoice(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-TEST", is_return=0, update_stock=1, items=[])
		frappe.flags.ignore_sales_invoice_valuation_guard_for_bulk_credit_note = True
		frappe.flags.bulk_credit_note_name = "BCN-TEST"

		with (
			patch.object(valuation_guard, "is_enabled", return_value=True),
			patch.object(valuation_guard, "inspect_stock_valuation", return_value=[]) as inspect,
		):
			valuation_guard.validate_sales_invoice_stock_valuation(doc)

		inspect.assert_called_once_with(doc)

	def test_bulk_credit_note_bypass_requires_update_stock(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-RETURN", is_return=1, update_stock=0, items=[])
		frappe.flags.ignore_sales_invoice_valuation_guard_for_bulk_credit_note = True
		frappe.flags.bulk_credit_note_name = "BCN-TEST"

		with patch.object(valuation_guard, "is_enabled", return_value=True):
			self.assertFalse(valuation_guard._should_bypass_for_bulk_credit_note(doc))
