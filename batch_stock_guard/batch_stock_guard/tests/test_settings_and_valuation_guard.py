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

	def test_effective_role_grants_check_access_without_role_profile_setting(self):
		configured = frappe._dict(
			enable_client_buttons=1,
			check_valuation_role_rows=[frappe._dict(role="Stock Manager")],
			repair_tool_role_rows=[frappe._dict(role="System Manager")],
		)
		with (
			patch.object(settings, "get_settings", return_value=configured),
			patch.object(settings.frappe, "get_roles", return_value=["Stock Manager"]),
		):
			self.assertTrue(settings.can_check_stock_valuation("stock@example.com"))
			self.assertFalse(settings.can_use_valuation_repair_tools("stock@example.com"))

	def test_repair_role_also_receives_check_and_preview_capability(self):
		configured = frappe._dict(
			enable_client_buttons=1,
			enable_valuation_repair_tools=1,
			check_valuation_role_rows=[],
			repair_tool_role_rows=[frappe._dict(role="System Manager")],
		)
		with (
			patch.object(settings, "get_settings", return_value=configured),
			patch.object(settings.frappe, "get_roles", return_value=["System Manager"]),
		):
			self.assertTrue(settings.can_check_stock_valuation("manager@example.com"))
			self.assertTrue(settings.can_use_valuation_repair_tools("manager@example.com"))

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
					"database_safe_stock_value_limit": 900000000000,
					"max_allowed_valuation_rate": 1000000,
				}[fieldname],
			),
			patch.object(
				valuation_guard,
				"is_enabled",
				side_effect=lambda fieldname: fieldname != "allow_negative_valuation_rate",
			),
			patch.object(valuation_guard, "_get_replay_sles", return_value=[]),
		):
			issues = valuation_guard.inspect_stock_valuation(doc)

		self.assertTrue(any(issue.severity == "block" for issue in issues))
		self.assertTrue(any(issue.item_code == "IMMUNITY 90" for issue in issues))

	def test_database_safety_overrides_negative_stock_value_permission(self):
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
					"stock_value_block_limit": 900000000000,
					"stock_value_warning_limit": 900000000000,
					"database_safe_stock_value_limit": 900000000000,
					"max_allowed_valuation_rate": 1000000,
				}[fieldname],
			),
			patch.object(
				valuation_guard,
				"is_enabled",
				side_effect=lambda fieldname: fieldname == "allow_negative_stock_value",
			),
			patch.object(valuation_guard, "_get_replay_sles", return_value=[]),
		):
			issues = valuation_guard.inspect_stock_valuation(doc)

		self.assertTrue([issue for issue in issues if issue.source == "database_storage_limit"])

	def test_small_negative_stock_value_is_still_allowed(self):
		self.assertFalse(valuation_guard._unsafe_database_number(-100))
		self.assertFalse(
			valuation_guard._stock_value_exceeds_limit(-100, 1000, allow_negative_stock_value=True)
		)

	def test_database_limit_blocks_both_signs(self):
		limit = valuation_guard.Decimal("900000000000")
		self.assertTrue(valuation_guard._unsafe_database_number(900000000000, limit=limit))
		self.assertTrue(valuation_guard._unsafe_database_number(-900000000000, limit=limit))

	def test_replay_chain_detects_unsafe_fifo_queue(self):
		sle = frappe._dict(
			stock_value=100,
			stock_value_difference=10,
			qty_after_transaction=10,
			valuation_rate=10,
			incoming_rate=10,
			stock_queue='[[1000000, 1000000]]',
		)
		problem = valuation_guard._replay_sle_problem(
			sle, max_rate=1000000, hard_limit=valuation_guard.Decimal("900000000000")
		)
		self.assertEqual(problem[0], "stock_queue row 1")

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
