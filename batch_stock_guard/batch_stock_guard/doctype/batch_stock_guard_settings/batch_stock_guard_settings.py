import frappe
from frappe.model.document import Document


class BatchStockGuardSettings(Document):
	def validate(self):
		if self.sr_barcode_compliance_mode == "SR Barcode Compliance":
			# Compliance-only Items are deliberately unbatched. Disable only the
			# batch-specific and Sales Invoice valuation interception. Keep total
			# stock and database storage safety enabled.
			self.enable_batch_bundle_override_logic = 0
			self.enable_sales_invoice_valuation_guard = 0

		if self.stock_value_warning_limit and self.stock_value_block_limit:
			if self.stock_value_warning_limit >= self.stock_value_block_limit:
				frappe.throw("Stock Value Warning Limit must be less than Stock Value Block Limit.")

		if self.stock_value_block_limit and self.stock_value_block_limit <= 0:
			frappe.throw("Stock Value Block Limit must be greater than zero.")

		if self.max_allowed_valuation_rate and self.max_allowed_valuation_rate <= 0:
			frappe.throw("Max Allowed Valuation Rate must be greater than zero.")

	def on_update(self):
		from batch_stock_guard.batch_stock_guard.settings import clear_settings_cache

		clear_settings_cache()
