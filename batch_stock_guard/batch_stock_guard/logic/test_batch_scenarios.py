import frappe
from frappe import _

def test_batch_logic():
    print("--- Starting Multi-Batch Stock Guard Test ---")
    
    item_code = "BATCH_MOCK_ITEM"
    warehouse = "Goods In Transit - SR"
    company = "Sriaas Private Limited"
    hsn_code = "999900"

    # 1. Create Test Item with Batch enabled
    if not frappe.db.exists("Item", item_code):
        item = frappe.new_doc("Item")
        item.item_code = item_code
        item.item_group = "All Item Groups"
        item.stock_uom = "Nos"
        item.is_stock_item = 1
        item.has_batch_no = 1
        item.create_new_batch = 1
        item.gst_hsn_code = hsn_code
        item.insert(ignore_permissions=True)
        frappe.db.commit()
    
    # Clean state
    frappe.db.sql("DELETE FROM `tabStock Ledger Entry` WHERE item_code = %s", item_code)
    frappe.db.commit()

    print(f"Testing Item: {item_code} (Batch Enabled)")

    # STEP 1: Add +10 to Batch-A
    print("Step 1: Adding +10 stock to BATCH-001...")
    try:
        sle1 = frappe.get_doc({
            "doctype": "Stock Ledger Entry",
            "item_code": item_code,
            "warehouse": warehouse,
            "actual_qty": 10,
            "batch_no": "BATCH-001",
            "posting_date": frappe.utils.today(),
            "posting_time": frappe.utils.nowtime(),
            "company": company,
            "voucher_type": "Stock Entry",
            "voucher_no": "TEST-BATCH-VOUCHER"
        })
        sle1.insert(ignore_permissions=True, ignore_links=True)
        frappe.db.commit()
        print("✅ Success: Batch-001 is +10. Total Item Stock is 10.")
    except Exception as e:
        print(f"❌ Error in Step 1: {e}")
        return

    # STEP 2: Withdraw -5 from Batch-B (which has 0 stock currently)
    # This makes Batch-B negative (-5), but total stock remains positive (5).
    print("Step 2: Withdrawing -5 from BATCH-002 (currently 0)...")
    try:
        sle2 = frappe.get_doc({
            "doctype": "Stock Ledger Entry",
            "item_code": item_code,
            "warehouse": warehouse,
            "actual_qty": -5,
            "batch_no": "BATCH-002",
            "posting_date": frappe.utils.today(),
            "posting_time": frappe.utils.nowtime(),
            "company": company,
            "voucher_type": "Stock Entry",
            "voucher_no": "TEST-BATCH-VOUCHER"
        })
        sle2.insert(ignore_permissions=True, ignore_links=True)
        frappe.db.commit()
        print("✅ Success: Batch-002 is now -5, but Item Total is +5. Entry ALLOWED.")
    except Exception as e:
        print(f"❌ Error in Step 2: {e}")

    # STEP 3: Withdraw -10 from Batch-A
    # Total would become 5 - 10 = -5. This should be BLOCKED.
    print("Step 3: Attempting to withdraw -10 from BATCH-001 (would make total -5)...")
    try:
        sle3 = frappe.get_doc({
            "doctype": "Stock Ledger Entry",
            "item_code": item_code,
            "warehouse": warehouse,
            "actual_qty": -10,
            "batch_no": "BATCH-001",
            "posting_date": frappe.utils.today(),
            "posting_time": frappe.utils.nowtime(),
            "company": company,
            "voucher_type": "Stock Entry",
            "voucher_no": "TEST-BATCH-VOUCHER"
        })
        sle3.insert(ignore_permissions=True, ignore_links=True)
        frappe.db.commit()
        print("❌ FAILURE: Item total stock went negative but was NOT blocked!")
    except frappe.ValidationError as e:
        if "overall total stock cannot go negative" in str(e):
            print("✅ CONFIRMED: Negative total stock was BLOCKED as expected.")
        else:
            print(f"❌ Unexpected ValidationError: {e}")
    except Exception as e:
        print(f"❌ Unexpected Exception: {e}")

    # Final Cleanup
    frappe.db.sql("DELETE FROM `tabStock Ledger Entry` WHERE item_code = %s", item_code)
    frappe.db.commit()
    print("--- Test Complete ---")

if __name__ == "__main__":
    test_batch_logic()
