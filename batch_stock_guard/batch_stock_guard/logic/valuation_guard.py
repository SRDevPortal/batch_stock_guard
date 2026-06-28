from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import frappe
from frappe import _
from frappe.utils import flt, get_datetime

from batch_stock_guard.batch_stock_guard.settings import (
	ensure_can_check_stock_valuation,
	ensure_can_use_valuation_repair_tools,
	get_float,
	is_enabled,
)


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
	source: str | None = None
	details: dict | None = None


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


def _row_rate_info(row) -> frappe._dict:
	for fieldname in ("incoming_rate", "valuation_rate", "basic_rate", "rate", "price_list_rate"):
		value = row.get(fieldname)
		if value not in (None, ""):
			return frappe._dict({"fieldname": fieldname, "rate": flt(value)})
	return frappe._dict({"fieldname": None, "rate": 0})


def _row_rate(row) -> float:
	return flt(_row_rate_info(row).rate)


def _sales_invoice_rows(doc):
	if not doc.get("update_stock"):
		return

	for row in doc.get("items") or []:
		item_code = row.get("item_code")
		warehouse = row.get("warehouse") or doc.get("set_warehouse")
		if not item_code or not warehouse or not _is_stock_item(item_code):
			continue

		qty = flt(row.get("stock_qty") or row.get("qty"))
		rate_info = _row_rate_info(row)
		yield frappe._dict(
			item_code=item_code,
			warehouse=warehouse,
			qty_delta=-abs(qty),
			rate=rate_info.rate,
			rate_fieldname=rate_info.fieldname,
			incoming_rate=flt(row.get("incoming_rate")) if row.get("incoming_rate") not in (None, "") else None,
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
		rate_info = _row_rate_info(row)
		if row.get("s_warehouse"):
			yield frappe._dict(
				item_code=item_code,
				warehouse=row.get("s_warehouse"),
				qty_delta=-abs(qty),
				rate=rate_info.rate,
				rate_fieldname=rate_info.fieldname,
				incoming_rate=flt(row.get("incoming_rate")) if row.get("incoming_rate") not in (None, "") else None,
				row_name=row.get("name"),
				batch_no=row.get("batch_no"),
				serial_and_batch_bundle=row.get("serial_and_batch_bundle"),
			)
		if row.get("t_warehouse"):
			yield frappe._dict(
				item_code=item_code,
				warehouse=row.get("t_warehouse"),
				qty_delta=abs(qty),
				rate=rate_info.rate,
				rate_fieldname=rate_info.fieldname,
				incoming_rate=flt(row.get("incoming_rate")) if row.get("incoming_rate") not in (None, "") else None,
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


def _sle_fields() -> list[str]:
	meta = frappe.get_meta("Stock Ledger Entry")
	fields = [
		"name",
		"item_code",
		"warehouse",
		"posting_date",
		"posting_time",
		"posting_datetime",
		"actual_qty",
		"qty_after_transaction",
		"incoming_rate",
		"valuation_rate",
		"stock_value",
		"stock_value_difference",
		"voucher_type",
		"voucher_no",
		"creation",
	]
	for fieldname in ("batch_no", "serial_and_batch_bundle"):
		if meta.has_field(fieldname):
			fields.append(fieldname)
	return fields


def _get_latest_previous_sle(item_code: str, warehouse: str, posting_date=None, posting_time=None, batch_no=None):
	filters = {"item_code": item_code, "warehouse": warehouse, "is_cancelled": 0}
	if posting_date:
		filters["posting_datetime"] = ("<=", get_datetime(f"{posting_date} {posting_time or '23:59:59'}"))

	meta = frappe.get_meta("Stock Ledger Entry")
	if batch_no and meta.has_field("batch_no"):
		filters["batch_no"] = batch_no

	sle = frappe.get_all(
		"Stock Ledger Entry",
		fields=_sle_fields(),
		filters=filters,
		order_by="posting_datetime desc, creation desc, name desc",
		limit_page_length=1,
	)
	return frappe._dict(sle[0]) if sle else frappe._dict()


def _stock_value_is_suspicious(value) -> bool:
	warning_limit = get_float("stock_value_warning_limit")
	block_limit = get_float("stock_value_block_limit")
	limit = block_limit or warning_limit
	return bool(limit and abs(flt(value)) >= limit)


def _rate_is_suspicious(value) -> bool:
	max_rate = get_float("max_allowed_valuation_rate")
	return bool(flt(value) < 0 or (max_rate and abs(flt(value)) > max_rate))


def _suggest_safe_rate(row, bin_doc, latest_sle) -> frappe._dict:
	candidates = []
	if flt(bin_doc.valuation_rate) > 0 and not _stock_value_is_suspicious(bin_doc.stock_value):
		candidates.append(("current_bin_valuation_rate", flt(bin_doc.valuation_rate)))
	if flt(latest_sle.valuation_rate) > 0 and not _stock_value_is_suspicious(latest_sle.stock_value):
		candidates.append(("latest_good_sle_valuation_rate", flt(latest_sle.valuation_rate)))

	if frappe.get_meta("Item").has_field("valuation_rate"):
		item_rate = frappe.db.get_value("Item", row.item_code, "valuation_rate")
		if flt(item_rate) > 0:
			candidates.append(("item_valuation_rate", flt(item_rate)))

	if flt(row.rate) > 0:
		candidates.append(("invoice_row_rate", flt(row.rate)))

	if not candidates:
		return frappe._dict({"source": None, "rate": None})
	return frappe._dict({"source": candidates[0][0], "rate": candidates[0][1]})


def diagnose_row_rate_source(doc, row) -> frappe._dict:
	bin_doc = _get_bin(row.item_code, row.warehouse)
	latest_sle = _get_latest_previous_sle(
		row.item_code,
		row.warehouse,
		posting_date=doc.get("posting_date"),
		posting_time=doc.get("posting_time"),
		batch_no=row.batch_no,
	)
	bundle_rate = _get_bundle_rate(row.serial_and_batch_bundle)
	suggestion = _suggest_safe_rate(row, bin_doc, latest_sle)

	incoming_rate = row.incoming_rate if row.incoming_rate is not None else row.rate
	source = "healthy"
	if flt(incoming_rate) < 0:
		if flt(bin_doc.valuation_rate) >= 0 and not _stock_value_is_suspicious(bin_doc.stock_value):
			source = "transaction_rate_source"
		else:
			source = "bin_and_transaction_rate"
	elif flt(bin_doc.valuation_rate) < 0 or _stock_value_is_suspicious(bin_doc.stock_value):
		source = "bin"
	elif latest_sle and (flt(latest_sle.valuation_rate) < 0 or _stock_value_is_suspicious(latest_sle.stock_value)):
		source = "latest_sle"
	elif bundle_rate is not None and _rate_is_suspicious(bundle_rate):
		source = "serial_and_batch_bundle"

	return frappe._dict(
		{
			"item_code": row.item_code,
			"warehouse": row.warehouse,
			"row_name": row.row_name,
			"batch_no": row.batch_no,
			"serial_and_batch_bundle": row.serial_and_batch_bundle,
			"rate_fieldname": row.rate_fieldname,
			"incoming_rate": incoming_rate,
			"transaction_rate": row.rate,
			"source": source,
			"bin": frappe._dict(
				{
					"name": bin_doc.name,
					"actual_qty": flt(bin_doc.actual_qty),
					"valuation_rate": flt(bin_doc.valuation_rate),
					"stock_value": flt(bin_doc.stock_value),
				}
			),
			"latest_sle": latest_sle,
			"bundle_rate": bundle_rate,
			"suggested_rate_source": suggestion.source,
			"suggested_rate": suggestion.rate,
		}
	)


def _issue_details(row, diagnosis: frappe._dict) -> dict:
	latest_sle = diagnosis.latest_sle or frappe._dict()
	return {
		"rate_fieldname": row.rate_fieldname,
		"incoming_rate": diagnosis.incoming_rate,
		"bin_actual_qty": diagnosis.bin.actual_qty,
		"bin_valuation_rate": diagnosis.bin.valuation_rate,
		"bin_stock_value": diagnosis.bin.stock_value,
		"bundle_rate": diagnosis.bundle_rate,
		"latest_sle": {
			"name": latest_sle.get("name"),
			"posting_datetime": latest_sle.get("posting_datetime"),
			"incoming_rate": flt(latest_sle.get("incoming_rate")),
			"valuation_rate": flt(latest_sle.get("valuation_rate")),
			"stock_value": flt(latest_sle.get("stock_value")),
			"voucher_type": latest_sle.get("voucher_type"),
			"voucher_no": latest_sle.get("voucher_no"),
		},
		"suggested_rate_source": diagnosis.suggested_rate_source,
		"suggested_rate": diagnosis.suggested_rate,
	}


def _append_issue(
	issues: list[StockValuationIssue],
	row,
	bin_doc,
	severity: str,
	message: str,
	source: str | None = None,
	details: dict | None = None,
):
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
			source=source,
			details=details,
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
		diagnosis = diagnose_row_rate_source(doc, row)
		details = _issue_details(row, diagnosis)

		if block_limit and abs(current_stock_value) >= block_limit:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Current Bin stock value is at or above the configured block limit."),
				source="bin",
				details=details,
			)
		elif warning_limit and abs(current_stock_value) >= warning_limit:
			_append_issue(
				issues,
				row,
				bin_doc,
				"warning",
				_("Current Bin stock value is near the configured block limit."),
				source="bin",
				details=details,
			)

		if block_limit and abs(projected_stock_value) >= block_limit:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Projected stock value after this transaction exceeds the configured block limit."),
				source=diagnosis.source,
				details=details,
			)

		if max_rate and abs(transaction_rate) > max_rate:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Transaction {0} is above the configured maximum.").format(row.rate_fieldname or _("rate")),
				source="invoice_row",
				details=details,
			)

		if bundle_rate is not None and max_rate and abs(bundle_rate) > max_rate:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Serial and Batch Bundle average rate is above the configured maximum."),
				source="serial_and_batch_bundle",
				details=details,
			)

		if not allow_negative_rate and current_valuation_rate < 0:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Current Bin valuation rate is negative."),
				source="bin",
				details=details,
			)

		if not allow_negative_rate and transaction_rate < 0:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_(
					"Transaction {0} is negative. Current Bin may be healthy, so check the latest SLE, selected batch, or invoice row source."
				).format(row.rate_fieldname or _("rate")),
				source=diagnosis.source,
				details=details,
			)

		if not allow_negative_rate and (latest_sle := diagnosis.latest_sle):
			if flt(latest_sle.valuation_rate) < 0:
				_append_issue(
					issues,
					row,
					bin_doc,
					"block",
					_("Latest previous Stock Ledger Entry valuation rate is negative."),
					source="latest_sle",
					details=details,
				)

		if bundle_rate is not None and not allow_negative_rate and bundle_rate < 0:
			_append_issue(
				issues,
				row,
				bin_doc,
				"block",
				_("Serial and Batch Bundle average rate is negative."),
				source="serial_and_batch_bundle",
				details=details,
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
		"{source_details}"
	).format(
		where=", ".join(bits),
		message=frappe.utils.escape_html(issue.message),
		stock_value=frappe.utils.fmt_money(flt(issue.current_stock_value)),
		projected=frappe.utils.fmt_money(flt(issue.projected_stock_value)),
		valuation_rate=frappe.utils.fmt_money(flt(issue.current_valuation_rate)),
		transaction_rate=frappe.utils.fmt_money(flt(issue.transaction_rate)),
		source_details=_format_source_details(issue),
	)


def _format_source_details(issue: StockValuationIssue) -> str:
	details = issue.details or {}
	latest_sle = details.get("latest_sle") or {}
	lines = [
		_("Source: <b>{0}</b>").format(frappe.utils.escape_html(issue.source or "unknown")),
		_("Invoice incoming/rate field: <b>{0}</b> = <b>{1}</b>").format(
			frappe.utils.escape_html(details.get("rate_fieldname") or "rate"),
			frappe.utils.fmt_money(flt(details.get("incoming_rate"))),
		),
	]
	if details.get("bundle_rate") is not None:
		lines.append(_("Bundle average rate: <b>{0}</b>").format(frappe.utils.fmt_money(flt(details.get("bundle_rate")))))
	if latest_sle.get("name"):
		lines.append(
			_("Latest previous SLE: <b>{0}</b>, valuation <b>{1}</b>, stock value <b>{2}</b>").format(
				frappe.utils.escape_html(latest_sle.get("name")),
				frappe.utils.fmt_money(flt(latest_sle.get("valuation_rate"))),
				frappe.utils.fmt_money(flt(latest_sle.get("stock_value"))),
			)
		)
	if details.get("suggested_rate"):
		lines.append(
			_("Suggested safe rate: <b>{0}</b> from <b>{1}</b>").format(
				frappe.utils.fmt_money(flt(details.get("suggested_rate"))),
				frappe.utils.escape_html(details.get("suggested_rate_source") or ""),
			)
		)
	return "<br>" + "<br>".join(lines)


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
	if doc.doctype == "Sales Invoice" and not doc.get("update_stock"):
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
	ensure_can_check_stock_valuation()
	if doctype not in {"Sales Invoice", "Stock Entry"}:
		frappe.throw(_("Batch Stock Guard valuation preview is not available for {0}.").format(doctype))

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if doctype == "Sales Invoice" and not doc.get("update_stock"):
		return [
			{
				"severity": "skipped",
				"message": _(
					"Stock valuation check skipped because Update Stock is disabled. This invoice will not create stock movement."
				),
			}
		]
	return [asdict(issue) for issue in inspect_stock_valuation(doc)]


@frappe.whitelist()
def diagnose_invoice_rate_source(invoice: str):
	ensure_can_check_stock_valuation()
	doc = frappe.get_doc("Sales Invoice", invoice)
	doc.check_permission("read")
	if not doc.get("update_stock"):
		return {
			"skipped": True,
			"message": _(
				"Stock valuation check skipped because Update Stock is disabled. This invoice will not create stock movement."
			),
			"rows": [],
		}
	return {"skipped": False, "rows": [diagnose_row_rate_source(doc, row) for row in _sales_invoice_rows(doc)]}


@frappe.whitelist()
def fix_invoice_incoming_rate(invoice: str, rows=None, confirm: bool | str = False):
	ensure_can_use_valuation_repair_tools()
	if str(confirm).lower() not in {"1", "true", "yes"}:
		frappe.throw(_("Set confirm=1 to fix invoice incoming rates. Run diagnosis first."))

	doc = frappe.get_doc("Sales Invoice", invoice)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only draft Sales Invoices can be updated."))
	if not doc.get("update_stock"):
		frappe.throw(_("Update Stock is disabled. This invoice will not create stock movement."))

	selected_rows = {}
	if rows:
		rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
		selected_rows = {row.get("row_name"): flt(row.get("suggested_rate")) for row in rows if row.get("row_name")}

	diagnostics = [diagnose_row_rate_source(doc, row) for row in _sales_invoice_rows(doc)]
	updates = []
	for diagnosis in diagnostics:
		suggested_rate = selected_rows.get(diagnosis.row_name) or flt(diagnosis.suggested_rate)
		if not diagnosis.row_name or not suggested_rate or flt(diagnosis.incoming_rate) >= 0:
			continue

		row = next((item for item in doc.items if item.name == diagnosis.row_name), None)
		if not row:
			continue

		row.incoming_rate = suggested_rate
		updates.append(
			{
				"row_name": row.name,
				"item_code": row.item_code,
				"old_incoming_rate": flt(diagnosis.incoming_rate),
				"new_incoming_rate": suggested_rate,
				"source": diagnosis.suggested_rate_source,
			}
		)

	if not updates:
		frappe.throw(_("No negative invoice incoming rates with safe suggested rates were found."))

	doc.flags.ignore_validate_update_after_submit = True
	doc.save()
	return updates
