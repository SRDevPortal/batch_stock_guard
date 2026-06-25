from __future__ import annotations

from functools import lru_cache

import frappe
from frappe.utils import flt


SETTINGS_DOCTYPE = "Batch Stock Guard Settings"

DEFAULTS = {
	"enable_total_stock_guard": 1,
	"enable_batch_bundle_override_logic": 1,
	"enable_sales_invoice_valuation_guard": 1,
	"enable_stock_entry_valuation_guard": 1,
	"enable_valuation_repair_tools": 1,
	"enable_client_buttons": 1,
	"stock_value_warning_limit": 900000000000,
	"stock_value_block_limit": 990000000000,
	"max_allowed_valuation_rate": 1000000,
	"allow_negative_valuation_rate": 0,
	"log_blocked_transactions": 1,
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


def ensure_default_settings() -> None:
	if not _doctype_available():
		return

	doc = frappe.get_single(SETTINGS_DOCTYPE)
	changed = False
	for fieldname, value in DEFAULTS.items():
		if doc.get(fieldname) is None:
			doc.set(fieldname, value)
			changed = True

	if changed:
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)

	clear_settings_cache()


@frappe.whitelist()
def get_client_config() -> dict:
	return {
		"enable_client_buttons": is_enabled("enable_client_buttons"),
		"enable_valuation_repair_tools": is_enabled("enable_valuation_repair_tools"),
	}
