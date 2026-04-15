import frappe
from frappe import _
from erpnext.stock.utils import get_combine_datetime


def get_total_stock(item_code, company=None, warehouse=None, posting_date=None, posting_time=None):
    """Return total stock for an item for the given company/warehouse/time slice."""
    conditions = ["item_code = %s", "is_cancelled = 0"]
    values = [item_code]

    if company:
        conditions.append("company = %s")
        values.append(company)

    if warehouse:
        conditions.append("warehouse = %s")
        values.append(warehouse)

    if posting_date:
        posting_datetime = get_combine_datetime(posting_date, posting_time or "23:59:59")
        conditions.append("posting_datetime <= %s")
        values.append(posting_datetime)

    result = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(actual_qty), 0) AS total
        FROM `tabStock Ledger Entry`
        WHERE {' AND '.join(conditions)}
    """,
        tuple(values),
        as_dict=True,
    )

    return result[0]["total"] if result else 0


def get_projected_warehouse_stock(
    item_code, warehouse, qty_delta, company=None, posting_date=None, posting_time=None
):
    """Return current and projected stock for an item in a specific warehouse."""
    current_total = get_total_stock(
        item_code,
        company=company,
        warehouse=warehouse,
        posting_date=posting_date,
        posting_time=posting_time,
    )
    projected_total = current_total + qty_delta
    return current_total, projected_total


def throw_warehouse_stock_error(item_code, warehouse, current_total, projected_total, qty_delta):
    frappe.throw(
        _(
            "Cannot proceed: stock of <b>{item}</b> in warehouse <b>{warehouse}</b> "
            "would become <b>{proj:.4f}</b>.<br><br>"
            "Current stock in selected warehouse: <b>{curr:.4f}</b><br>"
            "This transaction delta: <b>{delta:.4f}</b><br><br>"
            "Warehouse stock cannot go negative, even if stock exists in another warehouse."
        ).format(
            item=item_code,
            warehouse=warehouse,
            proj=projected_total,
            curr=current_total,
            delta=qty_delta,
        ),
        title=_("Negative Warehouse Stock Blocked"),
    )


def validate_warehouse_stock(doc):
    """Outgoing stock cannot drive the selected warehouse negative."""
    if doc.actual_qty >= 0 or not doc.warehouse:
        return

    current_total, projected_total = get_projected_warehouse_stock(
        doc.item_code,
        doc.warehouse,
        doc.actual_qty,
        company=getattr(doc, "company", None),
        posting_date=getattr(doc, "posting_date", None),
        posting_time=getattr(doc, "posting_time", None),
    )

    if projected_total < 0:
        throw_warehouse_stock_error(
            doc.item_code, doc.warehouse, current_total, projected_total, doc.actual_qty
        )


def validate_total_stock(doc, method):
    """
    Ensures outgoing stock does not drive the item's total in the
    selected warehouse below zero.
    """
    # Only check outgoing quantities - incoming (positive actual_qty) can never push total negative.
    if doc.actual_qty >= 0:
        return

    validate_warehouse_stock(doc)
