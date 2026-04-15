import frappe
from frappe import _

from batch_stock_guard.batch_stock_guard.compat import call_with_supported_kwargs
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import SerialandBatchBundle

from batch_stock_guard.batch_stock_guard.logic.stock_guard import (
    get_total_stock,
    throw_total_stock_error,
)


class BatchStockGuardSerialAndBatchBundle(SerialandBatchBundle):
    def _get_post_transaction_total_stock(self):
        return get_total_stock(
            self.item_code,
            company=getattr(self, "company", None),
            posting_date=getattr(self, "posting_date", None),
            posting_time=getattr(self, "posting_time", None),
        )

    def _allow_negative_batch_with_positive_total(self):
        return self._get_post_transaction_total_stock() >= 0

    def validate_negative_batch(self, batch_no, available_qty):
        if available_qty >= 0 or self.is_stock_reco_for_valuation_adjustment(available_qty):
            return

        qty_delta = self.total_qty
        if not qty_delta:
            self.calculate_total_qty(save=False)
            qty_delta = self.total_qty

        # During outward voucher submission, this validation runs after the current
        # Stock Ledger Entry has already been inserted, so the live total already
        # reflects the current transaction. Re-applying qty_delta here would
        # double-count the same movement.
        post_transaction_total = self._get_post_transaction_total_stock()
        if post_transaction_total < 0:
            pre_transaction_total = post_transaction_total - qty_delta
            throw_total_stock_error(
                self.item_code,
                pre_transaction_total,
                post_transaction_total,
                qty_delta,
            )

        frappe.msgprint(
            _(
                "Batch {0} will go negative in warehouse {1}, but total stock for item {2} across all warehouses remains non-negative, so submission is allowed."
            ).format(frappe.bold(batch_no), frappe.bold(self.warehouse), frappe.bold(self.item_code)),
            alert=True,
        )

    def throw_negative_batch(self, batch_no, available_qty, precision, posting_datetime=None):
        if self._allow_negative_batch_with_positive_total():
            return

        return call_with_supported_kwargs(
            super().throw_negative_batch,
            batch_no=batch_no,
            available_qty=available_qty,
            precision=precision,
            posting_datetime=posting_datetime,
        )
