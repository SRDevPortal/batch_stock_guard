from __future__ import annotations

from collections import OrderedDict

import frappe
from frappe.utils import getdate, today

from batch_stock_guard.batch_stock_guard.compat import call_with_supported_kwargs
from erpnext.stock.doctype.batch.batch import get_batch_qty


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_batch_no_for_sales_invoice(doctype, txt, searchfield, start, page_len, filters):
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)

	if not filters.get("item_code"):
		return []

	expiry_date = filters.get("posting_date") or today()
	query_filters = {
		"item": filters.get("item_code"),
		"disabled": 0,
	}

	if txt:
		query_filters["name"] = ["like", f"%{txt}%"]

	batches = frappe.get_all(
		"Batch",
		fields=["name", "manufacturing_date", "expiry_date"],
		filters=query_filters,
		limit_start=start,
		limit_page_length=page_len,
		order_by="expiry_date asc, creation asc",
	)

	results = OrderedDict()
	for batch in batches:
		if batch.expiry_date and getdate(batch.expiry_date) < getdate(expiry_date):
			continue

		qty_args = {
			"batch_no": batch.name,
			"warehouse": filters.get("warehouse"),
			"item_code": filters.get("item_code"),
			"posting_date": filters.get("posting_date"),
			"posting_time": filters.get("posting_time"),
			"consider_negative_batches": True,
			"ignore_reserved_stock": True,
		}
		qty = call_with_supported_kwargs(get_batch_qty, **qty_args)

		results[batch.name] = (
			batch.name,
			qty,
			f"MFG-{batch.manufacturing_date}" if batch.manufacturing_date else None,
			f"EXP-{batch.expiry_date}" if batch.expiry_date else None,
		)

	return list(results.values())
