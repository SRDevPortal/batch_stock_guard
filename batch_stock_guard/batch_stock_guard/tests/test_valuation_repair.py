from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from batch_stock_guard.batch_stock_guard import valuation_repair


class TestValuationRepair(FrappeTestCase):
    def test_group_rows_uses_weighted_average_for_batch_rows(self):
        rows = [
            frappe._dict(
                item_code="PUNAR 60",
                warehouse="Packaging Warehouse - SR",
                posting_date="2026-06-22",
                posting_time="15:04:00",
                qty=10,
                valuation_rate=18,
                batch_no="BATCH-1",
            ),
            frappe._dict(
                item_code="PUNAR 60",
                warehouse="Packaging Warehouse - SR",
                posting_date="2026-06-22",
                posting_time="15:04:00",
                qty=5,
                valuation_rate=24,
                batch_no="BATCH-2",
            ),
        ]

        grouped = valuation_repair._group_rows(rows)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0].qty, 15)
        self.assertEqual(grouped[0].batch_count, 2)
        self.assertEqual(grouped[0].valuation_rate, 20)

    def test_build_repair_result_preserves_previous_stock_value(self):
        row = frappe._dict(
            item_code="Levoheal 60 Tablets",
            warehouse="Packaging Warehouse - SR",
            posting_date="2026-06-22",
            posting_time="15:04:00",
            valuation_rate=39,
            qty=100,
        )
        baseline = frappe._dict(
            name="SLE-1",
            item_code=row.item_code,
            warehouse=row.warehouse,
            company="SR",
            posting_date="2026-06-22",
            posting_time="15:04:00",
            posting_datetime="2026-06-22 15:04:00",
            actual_qty=100,
            qty_after_transaction=100,
            incoming_rate=255188.79,
            valuation_rate=-80500.84,
            stock_value=-137656436.55,
            stock_value_difference=-137660336.55,
            voucher_type="Stock Reconciliation",
            voucher_no="MAT-RECO-2026-00189",
            creation="2026-06-22 17:00:00",
        )

        with (
            patch.object(valuation_repair, "_find_baseline_sle", return_value=baseline),
            patch(
                "batch_stock_guard.batch_stock_guard.valuation_repair.frappe.get_all",
                return_value=[
                    frappe._dict(
                        name="BIN-1",
                        actual_qty=100,
                        valuation_rate=-80500.84,
                        stock_value=-137656436.55,
                    )
                ],
            ),
        ):
            result = valuation_repair._build_repair_result(row)

        self.assertEqual(result.new.stock_value, 3900)
        self.assertEqual(result.new.stock_value_difference, 0)
        self.assertEqual(result.bin.new_stock_value, 3900)

    def test_build_repair_uses_sle_balance_not_invoice_qty(self):
        row = frappe._dict(
            item_code="BSG DEMO ITEM",
            warehouse="BSG Demo Warehouse",
            posting_date="2026-07-11",
            posting_time="12:00:00",
            valuation_rate=100,
            qty=1,
        )
        baseline = frappe._dict(
            name="SLE-DEMO",
            item_code=row.item_code,
            warehouse=row.warehouse,
            company="SR",
            qty_after_transaction=100,
            incoming_rate=100,
            valuation_rate=-9100000000,
            stock_value=-910000000000,
            stock_value_difference=-910000000000,
        )

        with (
            patch.object(valuation_repair, "_find_baseline_sle", return_value=baseline),
            patch(
                "batch_stock_guard.batch_stock_guard.valuation_repair.frappe.get_all",
                return_value=[
                    frappe._dict(
                        name="BIN-DEMO",
                        actual_qty=100,
                        valuation_rate=-9100000000,
                        stock_value=-910000000000,
                    )
                ],
            ),
        ):
            result = valuation_repair._build_repair_result(row)

        self.assertEqual(result.target_qty, 100)
        self.assertEqual(result.new.stock_value, 10000)
        self.assertEqual(result.new.stock_value_difference, 10000)

    def test_apply_requires_confirm(self):
        with (
            patch.object(valuation_repair, "_ensure_repair_access"),
            self.assertRaises(frappe.ValidationError),
        ):
            valuation_repair.apply_bulk_valuation_repair(rows=[], confirm=0)
