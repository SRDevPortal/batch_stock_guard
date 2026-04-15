import frappe


def after_install():
    """
    1. Enables 'Allow Negative Stock' in Stock Settings.
    2. Removes old DB-based scripts from sriaas_clinic to avoid conflicts.
    """
    enable_negative_stock()
    cleanup_old_scripts()

def enable_negative_stock():
    frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)


def cleanup_old_scripts():
    scripts_to_delete = {
        "Server Script": ["Batch Stock Total Guard"],
        "Client Script": ["Stock Entry Batch Warning"]
    }

    for doctype, names in scripts_to_delete.items():
        for name in names:
            if frappe.db.exists(doctype, name):
                frappe.delete_doc(doctype, name)
