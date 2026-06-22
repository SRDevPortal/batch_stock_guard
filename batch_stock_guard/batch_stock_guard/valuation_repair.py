from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate


SLE_FIELDS = [
    "name",
    "item_code",
    "warehouse",
    "company",
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


def _ensure_system_manager() -> None:
    frappe.only_for("System Manager")


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _load_rows(rows) -> list[frappe._dict]:
    if not rows:
        return []

    if isinstance(rows, str):
        rows = frappe.parse_json(rows)

    if isinstance(rows, dict):
        rows = [rows]

    parsed = []
    for row in rows:
        row = frappe._dict(row)
        if not row.item_code:
            frappe.throw(_("Item Code is required."))
        if not row.warehouse:
            frappe.throw(_("Warehouse is required for item {0}.").format(row.item_code))
        if row.valuation_rate is None:
            frappe.throw(_("Valuation Rate is required for item {0}.").format(row.item_code))

        row.valuation_rate = flt(row.valuation_rate)
        if row.valuation_rate < 0:
            frappe.throw(_("Valuation Rate cannot be negative for item {0}.").format(row.item_code))

        row.posting_date = getdate(row.posting_date) if row.posting_date else getdate()
        row.posting_time = str(row.posting_time or "23:59:59")
        if row.qty is not None:
            row.qty = flt(row.qty)

        parsed.append(row)

    return parsed


def _get_invoice_rows(invoice: str) -> list[frappe._dict]:
    doc = frappe.get_doc("Sales Invoice", invoice)
    if not doc.update_stock:
        frappe.throw(_("Sales Invoice {0} is not an update-stock invoice.").format(invoice))

    rows = []
    for item in doc.items:
        if not item.item_code or not item.warehouse:
            continue
        if item.get("incoming_rate") is None:
            continue

        rate = flt(item.incoming_rate)
        if rate < 0:
            rate = flt(item.rate) or flt(item.price_list_rate) or 0

        rows.append(
            frappe._dict(
                item_code=item.item_code,
                warehouse=item.warehouse,
                batch_no=item.get("batch_no"),
                qty=abs(flt(item.qty)),
                valuation_rate=rate,
                posting_date=doc.posting_date,
                posting_time=doc.posting_time,
                company=doc.company,
                reference_doctype="Sales Invoice",
                reference_name=doc.name,
                reference_row=item.name,
            )
        )

    return rows


def _find_baseline_sle(row: frappe._dict) -> frappe._dict:
    posting_datetime = get_datetime(f"{row.posting_date} {row.posting_time}")
    sle = frappe.get_all(
        "Stock Ledger Entry",
        fields=SLE_FIELDS,
        filters={
            "item_code": row.item_code,
            "warehouse": row.warehouse,
            "is_cancelled": 0,
            "posting_datetime": ("<=", posting_datetime),
        },
        order_by="posting_datetime desc, creation desc, name desc",
        limit_page_length=1,
    )
    if not sle:
        frappe.throw(
            _("No Stock Ledger Entry found for {0} in {1} up to {2}.").format(
                row.item_code,
                row.warehouse,
                posting_datetime,
            )
        )
    return frappe._dict(sle[0])


def _group_rows(rows: list[frappe._dict]) -> list[frappe._dict]:
    groups = defaultdict(list)
    for row in rows:
        key = (row.item_code, row.warehouse, str(row.posting_date), str(row.posting_time))
        groups[key].append(row)

    grouped_rows = []
    for (item_code, warehouse, posting_date, posting_time), group_rows in groups.items():
        total_qty = sum(Decimal(str(flt(row.qty))) for row in group_rows if row.qty is not None)
        total_value = sum(
            Decimal(str(flt(row.qty))) * Decimal(str(flt(row.valuation_rate)))
            for row in group_rows
            if row.qty is not None
        )

        if total_qty:
            valuation_rate = flt(total_value / total_qty)
        else:
            valuation_rate = flt(group_rows[0].valuation_rate)

        grouped_rows.append(
            frappe._dict(
                item_code=item_code,
                warehouse=warehouse,
                posting_date=posting_date,
                posting_time=posting_time,
                valuation_rate=valuation_rate,
                qty=flt(total_qty) if total_qty else None,
                batch_count=len([row for row in group_rows if row.get("batch_no")]),
                batch_rows=[row for row in group_rows if row.get("batch_no")],
                company=group_rows[0].get("company"),
                reference_doctype=group_rows[0].get("reference_doctype"),
                reference_name=group_rows[0].get("reference_name"),
            )
        )

    return grouped_rows


def _build_repair_result(row: frappe._dict) -> frappe._dict:
    sle = _find_baseline_sle(row)
    target_qty = flt(row.qty) if row.qty is not None else flt(sle.qty_after_transaction)
    target_stock_value = flt(target_qty * flt(row.valuation_rate))
    previous_stock_value = flt(sle.stock_value) - flt(sle.stock_value_difference)
    target_stock_value_difference = flt(target_stock_value - previous_stock_value)

    bin_doc = frappe.get_all(
        "Bin",
        fields=["name", "actual_qty", "valuation_rate", "stock_value"],
        filters={"item_code": row.item_code, "warehouse": row.warehouse},
        limit_page_length=1,
    )
    bin_doc = frappe._dict(bin_doc[0]) if bin_doc else frappe._dict()
    target_bin_stock_value = flt(flt(bin_doc.actual_qty) * flt(row.valuation_rate)) if bin_doc else None

    return frappe._dict(
        item_code=row.item_code,
        warehouse=row.warehouse,
        posting_date=row.posting_date,
        posting_time=row.posting_time,
        valuation_rate=flt(row.valuation_rate),
        target_qty=target_qty,
        target_stock_value=target_stock_value,
        target_stock_value_difference=target_stock_value_difference,
        batch_count=row.batch_count or 0,
        batch_rows=row.batch_rows or [],
        baseline_sle=sle,
        old=frappe._dict(
            {
                "valuation_rate": flt(sle.valuation_rate),
                "incoming_rate": flt(sle.incoming_rate),
                "stock_value": flt(sle.stock_value),
                "stock_value_difference": flt(sle.stock_value_difference),
            }
        ),
        new=frappe._dict(
            {
                "valuation_rate": flt(row.valuation_rate),
                "stock_value": target_stock_value,
                "stock_value_difference": target_stock_value_difference,
            }
        ),
        bin=frappe._dict(
            {
                "name": bin_doc.name,
                "actual_qty": flt(bin_doc.actual_qty) if bin_doc else None,
                "old_valuation_rate": flt(bin_doc.valuation_rate) if bin_doc else None,
                "old_stock_value": flt(bin_doc.stock_value) if bin_doc else None,
                "new_valuation_rate": flt(row.valuation_rate) if bin_doc else None,
                "new_stock_value": target_bin_stock_value,
            }
        ),
        company=row.company or sle.company,
        reference_doctype=row.reference_doctype,
        reference_name=row.reference_name,
    )


def _apply_repair(result: frappe._dict, update_incoming_rate: bool = False) -> None:
    sle_updates = {
        "valuation_rate": result.new.valuation_rate,
        "stock_value": result.new.stock_value,
        "stock_value_difference": result.new.stock_value_difference,
    }
    if update_incoming_rate:
        sle_updates["incoming_rate"] = result.new.valuation_rate

    frappe.db.set_value(
        "Stock Ledger Entry",
        result.baseline_sle.name,
        sle_updates,
        update_modified=False,
    )

    if result.bin.name:
        frappe.db.set_value(
            "Bin",
            result.bin.name,
            {
                "valuation_rate": result.bin.new_valuation_rate,
                "stock_value": result.bin.new_stock_value,
            },
            update_modified=False,
        )


def _create_repost(result: frappe._dict) -> str | None:
    if not frappe.db.exists("DocType", "Repost Item Valuation"):
        return None

    repost = frappe.new_doc("Repost Item Valuation")
    repost.based_on = "Item and Warehouse"
    repost.item_code = result.item_code
    repost.warehouse = result.warehouse
    repost.posting_date = result.posting_date
    repost.posting_time = result.posting_time
    repost.allow_negative_stock = 1
    repost.allow_zero_rate = 1
    if result.company:
        repost.company = result.company

    repost.flags.ignore_permissions = True
    repost.insert(ignore_permissions=True)
    repost.submit()
    return repost.name


def _make_rows(rows=None, invoice: str | None = None) -> list[frappe._dict]:
    parsed_rows = []
    if invoice:
        parsed_rows.extend(_get_invoice_rows(invoice))
    parsed_rows.extend(_load_rows(rows))

    if not parsed_rows:
        frappe.throw(_("Pass rows or invoice to repair valuation."))

    return _group_rows(parsed_rows)


@frappe.whitelist()
def preview_bulk_valuation_repair(rows=None, invoice: str | None = None):
    _ensure_system_manager()
    repair_rows = _make_rows(rows=rows, invoice=invoice)
    return [_build_repair_result(row) for row in repair_rows]


@frappe.whitelist()
def apply_bulk_valuation_repair(
    rows=None,
    invoice: str | None = None,
    confirm: bool | str = False,
    enqueue_repost: bool | str = True,
    update_incoming_rate: bool | str = False,
    commit: bool | str = True,
):
    _ensure_system_manager()
    if not _as_bool(confirm):
        frappe.throw(_("Set confirm=1 to apply valuation repair. Run preview first."))

    results = preview_bulk_valuation_repair(rows=rows, invoice=invoice)
    for result in results:
        _apply_repair(result, update_incoming_rate=_as_bool(update_incoming_rate))
        if _as_bool(enqueue_repost):
            result.repost_item_valuation = _create_repost(result)

    frappe.log_error(
        title="Batch Stock Guard valuation repair",
        message=json.dumps(results, default=str, indent=2),
    )
    if _as_bool(commit):
        frappe.db.commit()
    return results


@frappe.whitelist()
def repair_item_valuation(
    item_code: str,
    warehouse: str,
    valuation_rate,
    posting_date=None,
    posting_time=None,
    qty=None,
    confirm: bool | str = False,
    enqueue_repost: bool | str = True,
    commit: bool | str = True,
):
    row = {
        "item_code": item_code,
        "warehouse": warehouse,
        "valuation_rate": valuation_rate,
        "posting_date": posting_date,
        "posting_time": posting_time,
        "qty": qty,
    }
    if _as_bool(confirm):
        return apply_bulk_valuation_repair(
            rows=[row],
            confirm=confirm,
            enqueue_repost=enqueue_repost,
            commit=commit,
        )
    return preview_bulk_valuation_repair(rows=[row])
