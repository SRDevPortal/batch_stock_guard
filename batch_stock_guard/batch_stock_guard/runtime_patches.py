import frappe
from frappe.utils import flt


def apply():
    from erpnext.stock import serial_batch_bundle as sbb

    if getattr(sbb.update_batch_qty, "_batch_stock_guard_patched", False):
        return

    original_update_batch_qty = sbb.update_batch_qty

    def patched_update_batch_qty(voucher_type, voucher_no, docstatus, via_landed_cost_voucher=False):
        if via_landed_cost_voucher:
            return original_update_batch_qty(
                voucher_type,
                voucher_no,
                docstatus,
                via_landed_cost_voucher=via_landed_cost_voucher,
            )

        batches = sbb.get_batchwise_qty(voucher_type, voucher_no)
        if not batches:
            return

        precision = frappe.get_precision("Batch", "batch_qty")
        bundle_rows = frappe.get_all(
            "Serial and Batch Bundle",
            filters={"voucher_type": voucher_type, "voucher_no": voucher_no},
            fields=["name", "item_code", "company"],
        )
        item_company_by_bundle = {row.name: row for row in bundle_rows}

        batch_to_context = {}
        entry_rows = frappe.get_all(
            "Serial and Batch Entry",
            filters={"parent": ("in", list(item_company_by_bundle.keys()))},
            fields=["parent", "batch_no"],
        )
        for row in entry_rows:
            if row.batch_no and row.parent in item_company_by_bundle and row.batch_no not in batch_to_context:
                batch_to_context[row.batch_no] = item_company_by_bundle[row.parent]

        for batch, qty in batches.items():
            current_qty = sbb.get_batch_current_qty(batch)
            current_qty += flt(qty, precision) * (-1 if docstatus == 2 else 1)

            if current_qty < 0:
                context = batch_to_context.get(batch)
                if context:
                    from batch_stock_guard.batch_stock_guard.logic.stock_guard import get_total_stock

                    total_stock = get_total_stock(context.item_code, company=context.company)
                    if total_stock < 0:
                        sbb.throw_negative_batch_validation(batch, current_qty)
                else:
                    sbb.throw_negative_batch_validation(batch, current_qty)

            frappe.db.set_value("Batch", batch, "batch_qty", current_qty)

    patched_update_batch_qty._batch_stock_guard_patched = True
    sbb.update_batch_qty = patched_update_batch_qty
