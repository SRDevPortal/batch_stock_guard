import frappe
from frappe.model.document import Document


class BatchStockGuardSettings(Document):
	def validate(self):
		if self.stock_value_warning_limit and self.stock_value_block_limit:
			if self.stock_value_warning_limit >= self.stock_value_block_limit:
				frappe.throw("Stock Value Warning Limit must be less than Stock Value Block Limit.")

		if self.stock_value_block_limit and self.stock_value_block_limit <= 0:
			frappe.throw("Stock Value Block Limit must be greater than zero.")

		if self.max_allowed_valuation_rate and self.max_allowed_valuation_rate <= 0:
			frappe.throw("Max Allowed Valuation Rate must be greater than zero.")
