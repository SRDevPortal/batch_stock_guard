import frappe


SETTINGS_DOCTYPE = "Batch Stock Guard Settings"
ROLE_PROFILE_ACCESS_DOCTYPE = "Batch Stock Guard Role Profile Access"


def after_install():
    """
    1. Enables 'Allow Negative Stock' in Stock Settings.
    2. Removes old DB-based scripts from sriaas_clinic to avoid conflicts.
    """
    enable_negative_stock()
    cleanup_old_scripts()
    ensure_settings()


def after_migrate():
    ensure_settings()
    remove_obsolete_role_profile_access_doctype()


def before_migrate():
    migrate_role_profile_access_to_roles()


def _roles_from_profiles(profile_names):
    if not profile_names:
        return set()
    return set(
        frappe.get_all(
            "Has Role",
            filters={"parenttype": "Role Profile", "parent": ("in", list(profile_names))},
            pluck="role",
        )
    )


def migrate_role_profile_access_to_roles():
    """Preserve effective check access before obsolete profile fields are removed."""
    if not (
        frappe.db.exists("DocType", SETTINGS_DOCTYPE)
        and frappe.db.exists("DocType", ROLE_PROFILE_ACCESS_DOCTYPE)
    ):
        return

    doc = frappe.get_single(SETTINGS_DOCTYPE)
    if not doc.meta.has_field("check_valuation_role_profile_rows"):
        return

    check_profiles = {
        row.role_profile
        for row in (doc.get("check_valuation_role_profile_rows") or [])
        if row.role_profile
    }
    repair_profiles = {
        row.role_profile
        for row in (doc.get("repair_tool_role_profile_rows") or [])
        if row.role_profile
    }
    check_roles = _roles_from_profiles(check_profiles)
    repair_profile_roles = _roles_from_profiles(repair_profiles)
    existing_check_roles = {row.role for row in (doc.get("check_valuation_role_rows") or []) if row.role}
    existing_repair_roles = {row.role for row in (doc.get("repair_tool_role_rows") or []) if row.role}

    changed = False
    for role in sorted(check_roles - existing_check_roles):
        doc.append("check_valuation_role_rows", {"role": role})
        changed = True

    # Broad profiles must not silently grant stock-ledger write access.
    if "System Manager" in repair_profile_roles and "System Manager" not in existing_repair_roles:
        doc.append("repair_tool_role_rows", {"role": "System Manager"})
        changed = True

    skipped_repair_roles = sorted(repair_profile_roles - {"System Manager"})
    if skipped_repair_roles:
        frappe.log_error(
            title="Batch Stock Guard repair access migration review",
            message=(
                "Roles from Repair Role Profiles were not granted direct repair access: "
                + ", ".join(skipped_repair_roles)
            ),
        )

    if changed:
        doc.flags.ignore_permissions = True
        doc.save(ignore_permissions=True)


def remove_obsolete_role_profile_access_doctype():
    if frappe.db.exists("DocType", ROLE_PROFILE_ACCESS_DOCTYPE):
        frappe.delete_doc(
            "DocType",
            ROLE_PROFILE_ACCESS_DOCTYPE,
            ignore_permissions=True,
            force=True,
        )

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


def ensure_settings():
    from batch_stock_guard.batch_stock_guard.settings import ensure_default_settings

    ensure_default_settings()
