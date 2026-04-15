import frappe
from frappe.utils import flt


def apply():
    from erpnext.stock import serial_batch_bundle as sbb
    from erpnext.stock.doctype.batch.batch import get_available_batches
    from collections import defaultdict

    if getattr(sbb.update_batch_qty, "_batch_stock_guard_patched", False):
        return

    original_update_batch_qty = sbb.update_batch_qty

    def _resolve_docstatus(voucher_type, voucher_no, docstatus):
        if isinstance(docstatus, bool):
            return None, docstatus

        if docstatus is None:
            docstatus = frappe.db.get_value(voucher_type, voucher_no, "docstatus")

        return docstatus, None

    def _throw_negative_batch_validation(batch_no, warehouse, qty):
        try:
            sbb.throw_negative_batch_validation(batch_no, warehouse, qty)
        except TypeError:
            sbb.throw_negative_batch_validation(batch_no, qty)

    def patched_update_batch_qty(
        voucher_type, voucher_no, docstatus=None, via_landed_cost_voucher=False
    ):
        _resolved_docstatus, positional_via_landed_cost_voucher = _resolve_docstatus(
            voucher_type, voucher_no, docstatus
        )
        if positional_via_landed_cost_voucher is not None:
            via_landed_cost_voucher = positional_via_landed_cost_voucher

        if via_landed_cost_voucher:
            return original_update_batch_qty(
                voucher_type,
                voucher_no,
                via_landed_cost_voucher=via_landed_cost_voucher,
            )

        batches = sbb.get_distinct_batches(voucher_type, voucher_no)
        if not batches:
            return

        precision = frappe.get_precision("Batch", "batch_qty")
        batch_data = get_available_batches(
            frappe._dict(
                {"batch_no": batches, "consider_negative_batches": 1, "based_on_warehouse": True}
            )
        )
        batchwise_qty = defaultdict(float)

        bundle_rows = frappe.get_all(
            "Serial and Batch Bundle",
            filters={"voucher_type": voucher_type, "voucher_no": voucher_no},
            fields=["name", "item_code", "company", "warehouse"],
        )
        bundle_by_name = {row.name: row for row in bundle_rows}

        batch_context_by_key = {}
        entry_rows = frappe.get_all(
            "Serial and Batch Entry",
            filters={"parent": ("in", list(bundle_by_name.keys()))},
            fields=["parent", "batch_no"],
        )
        for row in entry_rows:
            if not row.batch_no or row.parent not in bundle_by_name:
                continue

            context = bundle_by_name[row.parent]
            batch_context_by_key.setdefault((row.batch_no, context.warehouse), context)

        for (batch_no, warehouse), qty in batch_data.items():
            qty = flt(qty, precision)
            batchwise_qty[batch_no] += qty

            if qty >= 0:
                continue

            context = batch_context_by_key.get((batch_no, warehouse))
            if not context:
                _throw_negative_batch_validation(batch_no, warehouse, qty)
                continue

            from batch_stock_guard.batch_stock_guard.logic.stock_guard import get_total_stock

            total_stock = get_total_stock(context.item_code, company=context.company)
            if total_stock < 0:
                _throw_negative_batch_validation(batch_no, warehouse, qty)

        for batch_no in batches:
            qty = flt(batchwise_qty.get(batch_no, 0), precision)
            frappe.db.set_value("Batch", batch_no, "batch_qty", qty)

    patched_update_batch_qty._batch_stock_guard_patched = True
    sbb.update_batch_qty = patched_update_batch_qty
