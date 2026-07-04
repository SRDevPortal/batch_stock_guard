from __future__ import annotations

from functools import lru_cache

import frappe
from frappe import _
from frappe.utils import flt


SETTINGS_DOCTYPE = "Batch Stock Guard Settings"

DEFAULT_CHECK_VALUATION_ROLES = "System Manager\nStock Manager\nAccounts Manager"
DEFAULT_REPAIR_TOOL_ROLES = "System Manager"

DEFAULTS = {
	"enable_total_stock_guard": 1,
	"enable_batch_bundle_override_logic": 1,
	"enable_sales_invoice_valuation_guard": 1,
	"allow_bulk_credit_note_valuation_bypass": 0,
	"enable_stock_entry_valuation_guard": 1,
	"enable_valuation_repair_tools": 1,
	"enable_client_buttons": 1,
	"stock_value_warning_limit": 900000000000,
	"stock_value_block_limit": 990000000000,
	"max_allowed_valuation_rate": 1000000,
	"allow_negative_stock_value": 1,
	"allow_negative_valuation_rate": 0,
	"log_blocked_transactions": 1,
	"button_access_initialized": 0,
	"check_valuation_roles": "",
	"check_valuation_role_profiles": "",
	"repair_tool_roles": "",
	"repair_tool_role_profiles": "",
	"check_valuation_role_rows": [
		{"role": "System Manager"},
		{"role": "Stock Manager"},
		{"role": "Accounts Manager"},
	],
	"check_valuation_role_profile_rows": [],
	"repair_tool_role_rows": [{"role": "System Manager"}],
	"repair_tool_role_profile_rows": [],
}


def _doctype_available() -> bool:
	try:
		return bool(frappe.db.exists("DocType", SETTINGS_DOCTYPE))
	except Exception:
		return False


@lru_cache(maxsize=1)
def get_settings() -> frappe._dict:
	settings = frappe._dict(DEFAULTS.copy())
	if not _doctype_available():
		return settings

	try:
		doc = frappe.get_single(SETTINGS_DOCTYPE)
	except Exception:
		return settings

	for key in DEFAULTS:
		value = doc.get(key)
		if value is not None:
			settings[key] = value

	return settings


def clear_settings_cache() -> None:
	get_settings.cache_clear()


def is_enabled(fieldname: str) -> bool:
	return bool(get_settings().get(fieldname, DEFAULTS.get(fieldname)))


def get_float(fieldname: str) -> float:
	return flt(get_settings().get(fieldname, DEFAULTS.get(fieldname, 0)))


def _split_names(value) -> set[str]:
	if not value:
		return set()
	if isinstance(value, (list, tuple, set)):
		values = value
	else:
		values = str(value).replace(",", "\n").splitlines()
	return {str(name).strip() for name in values if str(name).strip()}


def _names_from_child_rows(rows, fieldname: str) -> set[str]:
	names = set()
	for row in rows or []:
		value = row.get(fieldname) if hasattr(row, "get") else None
		if value:
			names.add(str(value).strip())
	return names


def _get_access_names(settings, table_fieldname: str, child_fieldname: str, legacy_fieldname: str) -> set[str]:
	table_values = _names_from_child_rows(settings.get(table_fieldname), child_fieldname)
	if table_values:
		return table_values
	return _split_names(settings.get(legacy_fieldname))


def _get_user_role_profile(user: str | None = None) -> str | None:
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	try:
		return frappe.db.get_value("User", user, "role_profile_name")
	except Exception:
		return None


def has_configured_access(role_fieldname: str, role_profile_fieldname: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	settings = get_settings()
	allowed_roles = _get_access_names(settings, role_fieldname, "role", role_fieldname.replace("_rows", "s"))
	allowed_role_profiles = _get_access_names(
		settings,
		role_profile_fieldname,
		"role_profile",
		role_profile_fieldname.replace("_rows", "s"),
	)

	if allowed_roles and allowed_roles.intersection(set(frappe.get_roles(user))):
		return True

	role_profile = _get_user_role_profile(user)
	return bool(role_profile and role_profile in allowed_role_profiles)


def can_check_stock_valuation(user: str | None = None) -> bool:
	return is_enabled("enable_client_buttons") and has_configured_access(
		"check_valuation_role_rows",
		"check_valuation_role_profile_rows",
		user=user,
	)


def can_use_valuation_repair_tools(user: str | None = None) -> bool:
	return (
		is_enabled("enable_valuation_repair_tools")
		and has_configured_access("repair_tool_role_rows", "repair_tool_role_profile_rows", user=user)
	)


def ensure_can_check_stock_valuation() -> None:
	if not can_check_stock_valuation():
		frappe.throw(_("You are not allowed to check Batch Stock Guard valuation."), frappe.PermissionError)


def ensure_can_use_valuation_repair_tools() -> None:
	if not can_use_valuation_repair_tools():
		frappe.throw(_("You are not allowed to use Batch Stock Guard valuation repair tools."), frappe.PermissionError)


def ensure_default_settings() -> None:
	if not _doctype_available():
		return

	doc = frappe.get_single(SETTINGS_DOCTYPE)
	changed = False
	for fieldname, value in DEFAULTS.items():
		if isinstance(value, list):
			continue
		if not doc.meta.has_field(fieldname):
			continue
		if doc.get(fieldname) is None:
			doc.set(fieldname, value)
			changed = True

	if not doc.get("button_access_initialized"):
		check_roles = _split_names(doc.get("check_valuation_roles")) or _split_names(DEFAULT_CHECK_VALUATION_ROLES)
		check_role_profiles = _split_names(doc.get("check_valuation_role_profiles"))
		repair_roles = _split_names(doc.get("repair_tool_roles")) or _split_names(DEFAULT_REPAIR_TOOL_ROLES)
		repair_role_profiles = _split_names(doc.get("repair_tool_role_profiles"))

		doc.set("check_valuation_role_rows", [])
		for role in sorted(check_roles):
			doc.append("check_valuation_role_rows", {"role": role})

		doc.set("check_valuation_role_profile_rows", [])
		for role_profile in sorted(check_role_profiles):
			doc.append("check_valuation_role_profile_rows", {"role_profile": role_profile})

		doc.set("repair_tool_role_rows", [])
		for role in sorted(repair_roles):
			doc.append("repair_tool_role_rows", {"role": role})

		doc.set("repair_tool_role_profile_rows", [])
		for role_profile in sorted(repair_role_profiles):
			doc.append("repair_tool_role_profile_rows", {"role_profile": role_profile})

		doc.set("button_access_initialized", 1)
		changed = True

	if changed:
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)

	clear_settings_cache()


@frappe.whitelist()
def get_client_config() -> dict:
	can_repair = can_use_valuation_repair_tools()
	return {
		"enable_client_buttons": is_enabled("enable_client_buttons"),
		"enable_valuation_repair_tools": is_enabled("enable_valuation_repair_tools"),
		"can_check_stock_valuation": can_check_stock_valuation(),
		"can_preview_valuation_repair": can_repair,
		"can_apply_valuation_repair": can_repair,
	}
