import frappe
from frappe.utils import flt

from batch_stock_guard.batch_stock_guard.compat import call_with_supported_kwargs


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
        call_with_supported_kwargs(
            sbb.throw_negative_batch_validation,
            batch_no=batch_no,
            warehouse=warehouse,
            qty=qty,
        )

    def _get_bundle_rows(voucher_type, voucher_no):
        return frappe.get_all(
            "Serial and Batch Bundle",
            filters={"voucher_type": voucher_type, "voucher_no": voucher_no},
            fields=["name", "item_code", "company", "warehouse"],
        )

    def _get_batch_context(bundle_rows):
        bundle_by_name = {row.name: row for row in bundle_rows}
        if not bundle_by_name:
            return [], {}

        entry_rows = frappe.get_all(
            "Serial and Batch Entry",
            filters={"parent": ("in", list(bundle_by_name.keys()))},
            fields=["parent", "batch_no"],
        )

        batches = []
        seen_batches = set()
        batch_context_by_key = {}
        for row in entry_rows:
            if not row.batch_no or row.parent not in bundle_by_name:
                continue

            if row.batch_no not in seen_batches:
                seen_batches.add(row.batch_no)
                batches.append(row.batch_no)

            context = bundle_by_name[row.parent]
            batch_context_by_key.setdefault((row.batch_no, context.warehouse), context)

        return batches, batch_context_by_key

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

        bundle_rows = _get_bundle_rows(voucher_type, voucher_no)
        batches, batch_context_by_key = _get_batch_context(bundle_rows)
        if not batches:
            return

        precision = frappe.get_precision("Batch", "batch_qty")
        batch_data = get_available_batches(
            frappe._dict(
                {"batch_no": batches, "consider_negative_batches": 1, "based_on_warehouse": True}
            )
        )
        batchwise_qty = defaultdict(float)

        for (batch_no, warehouse), qty in batch_data.items():
            qty = flt(qty, precision)
            batchwise_qty[batch_no] += qty

            if qty >= 0:
                continue

            context = batch_context_by_key.get((batch_no, warehouse))
            if not context:
                # Ignore negative balances for the same batch in warehouses that are
                # unrelated to the current voucher. The current rule only cares about
                # the item's projected stock in the voucher's selected warehouse.
                continue

            from batch_stock_guard.batch_stock_guard.logic.stock_guard import get_total_stock

            warehouse_stock = get_total_stock(
                context.item_code,
                company=context.company,
                warehouse=warehouse,
            )
            if warehouse_stock < 0:
                _throw_negative_batch_validation(batch_no, warehouse, qty)

        for batch_no in batches:
            qty = flt(batchwise_qty.get(batch_no, 0), precision)
            frappe.db.set_value("Batch", batch_no, "batch_qty", qty)

    patched_update_batch_qty._batch_stock_guard_patched = True
    sbb.update_batch_qty = patched_update_batch_qty
