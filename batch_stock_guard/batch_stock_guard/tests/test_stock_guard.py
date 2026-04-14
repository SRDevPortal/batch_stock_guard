import frappe
from frappe.tests.utils import FrappeTestCase

class TestStockGuard(FrappeTestCase):
    def test_negative_stock_validation(self):
        item_code = "TEST_ITEM_GUARD"
        warehouse = "Goods In Transit - SR" # Valid warehouse found earlier
        
        # Create test item
        if not frappe.db.exists("Item", item_code):
            item = frappe.new_doc("Item")
            item.item_code = item_code
            item.item_group = "All Item Groups"
            item.stock_uom = "Nos"
            item.is_stock_item = 1
            item.insert()

        # Cleanup existing SLEs for item
        frappe.db.sql("DELETE FROM `tabStock Ledger Entry` WHERE item_code = %s", item_code)

        # 1. Positive entry (Total: 10)
        sle1 = frappe.new_doc("Stock Ledger Entry")
        sle1.item_code = item_code
        sle1.warehouse = warehouse
        sle1.actual_qty = 10
        sle1.posting_date = frappe.utils.today()
        sle1.posting_time = frappe.utils.nowtime()
        sle1.insert()
        
        # 2. Negative entry within limit (Total: 5)
        sle2 = frappe.new_doc("Stock Ledger Entry")
        sle2.item_code = item_code
        sle2.warehouse = warehouse
        sle2.actual_qty = -5
        sle2.posting_date = frappe.utils.today()
        sle2.posting_time = frappe.utils.nowtime()
        sle2.insert()

        # 3. Negative entry EXCEEDING total limit (Total: -5)
        sle3 = frappe.new_doc("Stock Ledger Entry")
        sle3.item_code = item_code
        sle3.warehouse = warehouse
        sle3.actual_qty = -10
        sle3.posting_date = frappe.utils.today()
        sle3.posting_time = frappe.utils.nowtime()
        
        self.assertRaises(frappe.ValidationError, sle3.insert)
        
        # Cleanup
        frappe.db.sql("DELETE FROM `tabStock Ledger Entry` WHERE item_code = %s", item_code)
        frappe.db.commit()
