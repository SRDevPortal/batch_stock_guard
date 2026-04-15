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


def get_projected_total_stock(item_code, qty_delta, company=None, posting_date=None, posting_time=None):
    """Return current and projected stock for an item across all warehouses."""
    current_total = get_total_stock(
        item_code,
        company=company,
        posting_date=posting_date,
        posting_time=posting_time,
    )
    projected_total = current_total + qty_delta
    return current_total, projected_total


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


def throw_total_stock_error(item_code, current_total, projected_total, qty_delta):
    frappe.throw(
        _("Cannot proceed: total stock of <b>{item}</b> across all warehouses "
        "would become <b>{proj:.4f}</b>.<br><br>"
        "Current total (all warehouses + batches): <b>{curr:.4f}</b><br>"
        "This transaction delta: <b>{delta:.4f}</b><br><br>"
        "Batch-level negative is allowed, but the item's "
        "<b>overall total stock cannot go negative</b>.").format(
            item=item_code,
            proj=projected_total,
            curr=current_total,
            delta=qty_delta,
        ),
        title=_("Negative Total Stock Blocked"),
    )


def throw_warehouse_stock_error(item_code, warehouse, current_total, projected_total, qty_delta):
    frappe.throw(
        _(
            "Cannot proceed: stock of <b>{item}</b> in warehouse <b>{warehouse}</b> "
            "would become <b>{proj:.4f}</b>.<br><br>"
            "Current stock in selected warehouse: <b>{curr:.4f}</b><br>"
            "This transaction delta: <b>{delta:.4f}</b><br><br>"
            "Stock available in another warehouse cannot be billed from this warehouse."
        ).format(
            item=item_code,
            warehouse=warehouse,
            proj=projected_total,
            curr=current_total,
            delta=qty_delta,
        ),
        title=_("Negative Warehouse Stock Blocked"),
    )


def validate_sales_invoice_warehouse_stock(doc):
    """Sales Invoice must have enough stock in the selected warehouse, not just somewhere in the company."""
    if doc.voucher_type != "Sales Invoice" or doc.actual_qty >= 0 or not doc.warehouse:
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
    Ensures that while individual batches/warehouses can go negative,
    the total stock of the item across ALL warehouses does not.
    """
    # Only check outgoing quantities - incoming (positive actual_qty) can never push total negative.
    if doc.actual_qty >= 0:
        return

    validate_sales_invoice_warehouse_stock(doc)

    current_total, projected_total = get_projected_total_stock(
        doc.item_code,
        doc.actual_qty,
        company=getattr(doc, "company", None),
        posting_date=getattr(doc, "posting_date", None),
        posting_time=getattr(doc, "posting_time", None),
    )

    if projected_total < 0:
        throw_total_stock_error(doc.item_code, current_total, projected_total, doc.actual_qty)
