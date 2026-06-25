from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import frappe
from frappe import _
from frappe.utils import flt

from batch_stock_guard.batch_stock_guard.settings import get_float, is_enabled


@dataclass
class StockValuationIssue:
	severity: str
	item_code: str
	warehouse: str
	message: str
	row_name: str | None = None
	batch_no: str | None = None
	serial_and_batch_bundle: str | None = None
	current_stock_value: float | None = None
	projected_stock_value: float | None = None
	current_valuation_rate: float | None = None
	transaction_rate: float | None = None


def _is_stock_item(item_code: str | None) -> bool:
	if not item_code:
		return False
	return bool(frappe.db.get_value("Item", item_code, "is_stock_item"))


def _get_bin(item_code: str, warehouse: str) -> frappe._dict:
	bin_doc = frappe.get_all(
		"Bin",
		fields=["name", "actual_qty", "valuation_rate", "stock_value"],
		filters={"item_code": item_code, "warehouse": warehouse},
		limit_page_length=1,
	)
	return frappe._dict(bin_doc[0]) if bin_doc else frappe._dict()


def _get_bundle_rate(bundle_name: str | None) -> float | None:
	if not bundle_name:
		return None

	try:
		rate = frappe.db.get_value("Serial and Batch Bundle", bundle_name, "avg_rate")
	except Exception:
		return None

	return flt(rate) if rate is not None else None


def _row_rate(row) -> float:
	for fieldname in ("incoming_rate", "valuation_rate", "basic_rate", "rate", "price_list_rate"):
		value = row.get(fieldname)
		if value not in (None, ""):
			return flt(value)
	return 0


def _sales_invoice_rows(doc):
	if not doc.get("update_stock"):
		return

	for row in doc.get("items") or []:
		item_code = row.get("item_code")
		warehouse = row.get("warehouse") or doc.get("set_warehouse")
		if not item_code or not warehouse or not _is_stock_item(item_code):
			continue

		qty = flt(row.get("stock_qty") or row.get("qty"))
		yield frappe._dict(
			item_code=item_code,
			warehouse=warehouse,
			qty_delta=-abs(qty),
			rate=_row_rate(row),
			row_name=row.get("name"),
			batch_no=row.get("batch_no"),
			serial_and_batch_bundle=row.get("serial_and_batch_bundle"),
		)


def _stock_entry_rows(doc):
	for row in doc.get("items") or []:
		item_code = row.get("item_code")
		if not item_code or not _is_stock_item(item_code):
			continue

		qty = flt(row.get("transfer_qty") or row.get("qty"))
		rate = _row_rate(row)
		if row.get("s_warehouse"):
			yield frappe._dict(
				item_code=item_code,
				warehouse=row.get("s_warehouse"),
				qty_delta=-abs(qty),
				rate=rate,
				row_name=row.get("name"),
				batch_no=row.get("batch_no"),
				serial_and_batch_bundle=row.get("serial_and_batch_bundle"),
			)
		if row.get("t_warehouse"):
			yield frappe._dict(
				item_code=item_code,
				warehouse=row.get("t_warehouse"),
				qty_delta=abs(qty),
				rate=rate,
				row_name=row.get("name"),
				batch_no=row.get("batch_no"),
				serial_and_batch_bundle=row.get("serial_and_batch_bundle"),
			)


def _get_rows(doc):
	if doc.doctype == "Sales Invoice":
		return list(_sales_invoice_rows(doc) or [])
	if doc.doctype == "Stock Entry":
		return list(_stock_entry_rows(doc) or [])
	return []


def _append_issue(issues: list[StockValuationIssue], row, bin_doc, severity: str, message: str):
	projected = flt(bin_doc.stock_value) + flt(row.qty_delta) * abs(flt(row.rate))
	issues.append(
		StockValuationIssue(
			severity=severity,
			item_code=row.item_code,
			warehouse=row.warehouse,
			message=message,
			row_name=row.row_name,
			batch_no=row.batch_no,
			serial_and_batch_bundle=row.serial_and_batch_bundle,
			current_stock_value=flt(bin_doc.stock_value),
			projected_stock_value=projected,
			current_valuation_rate=flt(bin_doc.valuation_rate),
			transaction_rate=flt(row.rate),
		)
	)


def inspect_stock_valuation(doc) -> list[StockValuationIssue]:
	block_limit = get_float("stock_value_block_limit")
	warning_limit = get_float("stock_value_warning_limit")
	max_rate = get_float("max_allowed_valuation_rate")
	allow_negative_rate = is_enabled("allow_negative_valuation_rate")

	issues: list[StockValuationIssue] = []
	for row in _get_rows(doc):
		bin_doc = _get_bin(row.item_code, row.warehouse)
		current_stock_value = flt(bin_doc.stock_value)
		current_valuation_rate = flt(bin_doc.valuation_rate)
		transaction_rate = flt(row.rate)
		projected_stock_value = current_stock_value + flt(row.qty_delta) * abs(transaction_rate)
		bundle_rate = _get_bundle_rate(row.serial_and_batch_bundle)

		if block_limit and abs(current_stock_value) >= block_limit:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Current Bin stock value is at or above the configured block limit."),
			)
		elif warning_limit and abs(current_stock_value) >= warning_limit:
			_append_issue(
				issues,
				row,
				bin_doc,
				"warning",
				_("Current Bin stock value is near the configured block limit."),
			)

		if block_limit and abs(projected_stock_value) >= block_limit:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Projected stock value after this transaction exceeds the configured block limit."),
			)

		if max_rate and abs(transaction_rate) > max_rate:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Transaction valuation rate is above the configured maximum."),
			)

		if bundle_rate is not None and max_rate and abs(bundle_rate) > max_rate:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Serial and Batch Bundle average rate is above the configured maximum."),
			)

		if not allow_negative_rate and (current_valuation_rate < 0 or transaction_rate < 0):
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Negative valuation rate is not allowed by Batch Stock Guard Settings."),
			)

	return issues


def _format_issue(issue: StockValuationIssue) -> str:
	bits = [
		f"<b>{frappe.utils.escape_html(issue.item_code)}</b>",
		f"warehouse <b>{frappe.utils.escape_html(issue.warehouse)}</b>",
	]
	if issue.batch_no:
		bits.append(f"batch <b>{frappe.utils.escape_html(issue.batch_no)}</b>")
	if issue.serial_and_batch_bundle:
		bits.append(f"bundle <b>{frappe.utils.escape_html(issue.serial_and_batch_bundle)}</b>")

	return (
		"{where}: {message}<br>"
		"Current stock value: <b>{stock_value}</b>, projected: <b>{projected}</b>, "
		"Bin valuation rate: <b>{valuation_rate}</b>, transaction rate: <b>{transaction_rate}</b>"
	).format(
		where=", ".join(bits),
		message=frappe.utils.escape_html(issue.message),
		stock_value=frappe.utils.fmt_money(flt(issue.current_stock_value)),
		projected=frappe.utils.fmt_money(flt(issue.projected_stock_value)),
		valuation_rate=frappe.utils.fmt_money(flt(issue.current_valuation_rate)),
		transaction_rate=frappe.utils.fmt_money(flt(issue.transaction_rate)),
	)


def _log_issues(doc, issues: list[StockValuationIssue]) -> None:
	if not is_enabled("log_blocked_transactions"):
		return

	frappe.log_error(
		title=f"Batch Stock Guard blocked {doc.doctype}",
		message=json.dumps(
			{
				"doctype": doc.doctype,
				"name": doc.name,
				"issues": [asdict(issue) for issue in issues],
			},
			default=str,
			indent=2,
		),
	)


def _validate_doc(doc, feature_flag: str) -> None:
	if not is_enabled(feature_flag):
		return

	issues = inspect_stock_valuation(doc)
	blocking_issues = [issue for issue in issues if issue.severity == "block"]
	if not blocking_issues:
		return

	_log_issues(doc, blocking_issues)
	frappe.throw(
		"<br><br>".join(_format_issue(issue) for issue in blocking_issues)
		+ "<br><br>"
		+ _(
			"Fix the listed stock valuation issues before submitting. "
			"Use valuation repair for corrupted Bin/SLE values, and correct the invoice item or batch/source rate for transaction-rate issues."
		),
		title=_("Stock Valuation Blocked"),
	)


def validate_sales_invoice_stock_valuation(doc, method=None):
	_validate_doc(doc, "enable_sales_invoice_valuation_guard")


def validate_stock_entry_stock_valuation(doc, method=None):
	_validate_doc(doc, "enable_stock_entry_valuation_guard")


@frappe.whitelist()
def preview_stock_valuation_for_doc(doctype: str, name: str):
	if not is_enabled("enable_client_buttons"):
		frappe.throw(_("Batch Stock Guard client tools are disabled."))

	doc = frappe.get_doc(doctype, name)
	return [asdict(issue) for issue in inspect_stock_valuation(doc)]
