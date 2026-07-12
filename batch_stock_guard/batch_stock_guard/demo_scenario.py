from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime


ITEM_CODE = "BSG-OVERFLOW-DEMO"
ACCESS_ITEM_CODE = "BSG-ACCESS-CONTROL-DEMO"
ACCESS_USER = "bsg.sales@example.com"
WAREHOUSE_NAME = "BSG Overflow Demo"
SEED_VOUCHER = "BSG-OVERFLOW-DEMO-SEED"
ACCESS_SEED_VOUCHER = "BSG-ACCESS-CONTROL-DEMO-SEED"
CORRUPT_STOCK_VALUE = -910_000_000_000
SAFE_RATE = 100
SEED_QTY = 100


def _ensure_local_site() -> None:
	conf = getattr(frappe.local, "conf", frappe._dict())
	site = getattr(frappe.local, "site", "") or ""
	if not (site.endswith(".localhost") and bool(conf.get("developer_mode"))):
		frappe.throw(
			_("The overflow demo can only run on a developer-mode *.localhost site."),
			frappe.PermissionError,
		)


def _first(doctype: str, filters=None) -> str | None:
	rows = frappe.get_all(doctype, filters=filters or {}, pluck="name", limit_page_length=1)
	return rows[0] if rows else None


def _ensure_item(item_code: str = ITEM_CODE) -> None:
	if frappe.db.exists("Item", item_code):
		return

	item = frappe.new_doc("Item")
	item.item_code = item_code
	item.item_name = f"Batch Stock Guard Demo - {item_code}"
	item.item_group = _first("Item Group", {"is_group": 0}) or "All Item Groups"
	item.stock_uom = _first("UOM") or "Nos"
	item.is_stock_item = 1
	if item.meta.has_field("gst_hsn_code"):
		item.gst_hsn_code = "30049011"
	item.flags.ignore_permissions = True
	item.insert(ignore_permissions=True)


def _ensure_warehouse(company: str, abbr: str) -> str:
	existing = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": WAREHOUSE_NAME, "company": company},
		"name",
	)
	if existing:
		return existing

	warehouse = frappe.new_doc("Warehouse")
	warehouse.warehouse_name = WAREHOUSE_NAME
	warehouse.company = company
	warehouse.parent_warehouse = frappe.db.get_value(
		"Warehouse",
		{"company": company, "is_group": 1, "warehouse_name": "All Warehouses"},
		"name",
	) or _first("Warehouse", {"company": company, "is_group": 1})
	warehouse.flags.ignore_permissions = True
	warehouse.insert(ignore_permissions=True)
	return warehouse.name


def _ensure_customer() -> str:
	customer = _first("Customer", {"disabled": 0})
	if customer:
		return customer

	customer_doc = frappe.new_doc("Customer")
	customer_doc.customer_name = "Batch Stock Guard Demo Customer"
	customer_doc.customer_type = "Individual"
	customer_doc.customer_group = _first("Customer Group", {"is_group": 0}) or "All Customer Groups"
	customer_doc.territory = _first("Territory", {"is_group": 0}) or "All Territories"
	customer_doc.flags.ignore_permissions = True
	customer_doc.insert(ignore_permissions=True)
	return customer_doc.name


def _clear_demo_ledger(item_code: str = ITEM_CODE) -> None:
	frappe.db.delete("Stock Ledger Entry", {"item_code": item_code})
	frappe.db.delete("Bin", {"item_code": item_code})


def _make_corrupted_seed(
	company: str,
	warehouse: str,
	posting_datetime,
	item_code: str = ITEM_CODE,
	voucher_no: str = SEED_VOUCHER,
) -> str:
	sle = frappe.new_doc("Stock Ledger Entry")
	sle.item_code = item_code
	sle.warehouse = warehouse
	sle.company = company
	sle.posting_date = posting_datetime.date()
	sle.posting_time = posting_datetime.time()
	sle.actual_qty = SEED_QTY
	sle.qty_after_transaction = SEED_QTY
	sle.incoming_rate = SAFE_RATE
	sle.valuation_rate = SAFE_RATE
	sle.stock_value = SEED_QTY * SAFE_RATE
	sle.stock_value_difference = SEED_QTY * SAFE_RATE
	sle.voucher_type = "Stock Reconciliation"
	sle.voucher_no = voucher_no
	sle.flags.ignore_permissions = True
	sle.flags.ignore_links = True
	sle.insert(ignore_permissions=True)

	# Deliberately bypass Document.db_update to reproduce already-corrupted live
	# history. The value remains inside MariaDB DECIMAL(21,9), but outside the
	# app's safe ceiling, so the guard and repair button can detect it.
	frappe.db.set_value(
		"Stock Ledger Entry",
		sle.name,
		{
			"valuation_rate": CORRUPT_STOCK_VALUE / SEED_QTY,
			"stock_value": CORRUPT_STOCK_VALUE,
			"stock_value_difference": CORRUPT_STOCK_VALUE,
		},
		update_modified=False,
	)

	bin_doc = frappe.get_doc(
		{
			"doctype": "Bin",
			"item_code": item_code,
			"warehouse": warehouse,
			"actual_qty": SEED_QTY,
			"valuation_rate": CORRUPT_STOCK_VALUE / SEED_QTY,
			"stock_value": CORRUPT_STOCK_VALUE,
		}
	)
	bin_doc.flags.ignore_permissions = True
	bin_doc.insert(ignore_permissions=True)
	return sle.name


def _make_invoice(
	company: str,
	customer: str,
	warehouse: str,
	posting_datetime,
	item_code: str = ITEM_CODE,
	owner: str | None = None,
) -> str:
	invoice = frappe.new_doc("Sales Invoice")
	invoice.company = company
	invoice.customer = customer
	invoice.posting_date = posting_datetime.date()
	invoice.posting_time = posting_datetime.time()
	invoice.set_posting_time = 1
	invoice.update_stock = 1
	invoice.append(
		"items",
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"qty": 1,
			"rate": SAFE_RATE,
			"incoming_rate": SAFE_RATE,
		},
	)
	invoice.flags.ignore_permissions = True
	invoice.insert(ignore_permissions=True)
	if owner:
		frappe.db.set_value("Sales Invoice", invoice.name, "owner", owner, update_modified=False)
	return invoice.name


@frappe.whitelist()
def create_local_overflow_scenario() -> dict:
	"""Create an idempotent local fixture for testing button-driven valuation repair."""
	_ensure_local_site()
	company = _first("Company")
	if not company:
		frappe.throw(_("Create a Company before creating the overflow demo."))
	abbr = frappe.db.get_value("Company", company, "abbr")
	_ensure_item()
	warehouse = _ensure_warehouse(company, abbr)
	customer = _ensure_customer()

	demo_invoices = frappe.get_all(
		"Sales Invoice Item",
		filters={"item_code": ITEM_CODE, "parenttype": "Sales Invoice", "docstatus": 0},
		pluck="parent",
	)
	for invoice_name in set(demo_invoices):
		frappe.delete_doc("Sales Invoice", invoice_name, force=True, ignore_permissions=True)
	_clear_demo_ledger(ITEM_CODE)

	seed_time = add_to_date(now_datetime(), minutes=-5)
	sle_name = _make_corrupted_seed(company, warehouse, seed_time)
	invoice_name = _make_invoice(company, customer, warehouse, add_to_date(seed_time, minutes=1))
	frappe.db.commit()

	return {
		"site": frappe.local.site,
		"invoice": invoice_name,
		"route": f"/app/sales-invoice/{invoice_name}",
		"item_code": ITEM_CODE,
		"warehouse": warehouse,
		"corrupted_sle": sle_name,
		"corrupted_stock_value": CORRUPT_STOCK_VALUE,
		"expected_repaired_stock_value": SEED_QTY * SAFE_RATE,
		"instructions": [
			"Open the invoice and click Batch Stock Guard > Check Stock Valuation.",
			"Click Show Bin/SLE Repair Plan, then Apply Bin/SLE Repair.",
			"Run Check Stock Valuation again before submitting.",
		],
	}


def _configure_sales_user_access() -> None:
	settings = frappe.get_single("Batch Stock Guard Settings")
	check_roles = {row.role for row in settings.check_valuation_role_rows if row.role}
	if "BSG Sales Invoice Submitter" not in check_roles:
		settings.append("check_valuation_role_rows", {"role": "BSG Sales Invoice Submitter"})

	# The sales role may inspect and preview, but must never write Bin/SLE values.
	settings.set(
		"repair_tool_role_rows",
		[row for row in settings.repair_tool_role_rows if row.role != "BSG Sales Invoice Submitter"],
	)
	if not any(row.role == "System Manager" for row in settings.repair_tool_role_rows):
		settings.append("repair_tool_role_rows", {"role": "System Manager"})
	settings.flags.ignore_permissions = True
	settings.save(ignore_permissions=True)

	from batch_stock_guard.batch_stock_guard.settings import clear_settings_cache

	clear_settings_cache()


@frappe.whitelist()
def create_local_role_access_scenario() -> dict:
	"""Create and verify a check-allowed/repair-denied scenario for the sales user."""
	_ensure_local_site()
	if not frappe.db.exists("User", ACCESS_USER):
		frappe.throw(_("User {0} does not exist.").format(ACCESS_USER))

	_configure_sales_user_access()
	company = _first("Company")
	if not company:
		frappe.throw(_("Create a Company before creating the access-control demo."))
	abbr = frappe.db.get_value("Company", company, "abbr")
	_ensure_item(ACCESS_ITEM_CODE)
	warehouse = _ensure_warehouse(company, abbr)
	customer = _ensure_customer()

	demo_invoices = frappe.get_all(
		"Sales Invoice Item",
		filters={"item_code": ACCESS_ITEM_CODE, "parenttype": "Sales Invoice", "docstatus": 0},
		pluck="parent",
	)
	for invoice_name in set(demo_invoices):
		frappe.delete_doc("Sales Invoice", invoice_name, force=True, ignore_permissions=True)
	_clear_demo_ledger(ACCESS_ITEM_CODE)

	seed_time = add_to_date(now_datetime(), minutes=-5)
	sle_name = _make_corrupted_seed(
		company,
		warehouse,
		seed_time,
		item_code=ACCESS_ITEM_CODE,
		voucher_no=ACCESS_SEED_VOUCHER,
	)
	invoice_name = _make_invoice(
		company,
		customer,
		warehouse,
		add_to_date(seed_time, minutes=1),
		item_code=ACCESS_ITEM_CODE,
		owner=ACCESS_USER,
	)
	frappe.share.add_docshare(
		"Sales Invoice",
		invoice_name,
		ACCESS_USER,
		read=1,
		write=1,
		submit=1,
		flags={"ignore_share_permission": True},
	)
	frappe.db.commit()

	previous_user = frappe.session.user
	try:
		frappe.set_user(ACCESS_USER)
		from batch_stock_guard.batch_stock_guard import settings as access_settings
		from batch_stock_guard.batch_stock_guard.logic.valuation_guard import preview_stock_valuation_for_doc
		from batch_stock_guard.batch_stock_guard.valuation_repair import apply_invoice_valuation_repair

		access_settings.clear_settings_cache()
		client_config = access_settings.get_client_config()
		check_issues = preview_stock_valuation_for_doc("Sales Invoice", invoice_name)
		repair_denied = False
		try:
			apply_invoice_valuation_repair(
				invoice_name,
				confirm=True,
				enqueue_repost=False,
				commit=False,
			)
		except frappe.PermissionError:
			repair_denied = True
	finally:
		frappe.set_user(previous_user)
		from batch_stock_guard.batch_stock_guard.settings import clear_settings_cache

		clear_settings_cache()

	return {
		"user": ACCESS_USER,
		"user_route": f"/app/user/{ACCESS_USER}",
		"invoice": invoice_name,
		"invoice_route": f"/app/sales-invoice/{invoice_name}",
		"item_code": ACCESS_ITEM_CODE,
		"warehouse": warehouse,
		"corrupted_sle": sle_name,
		"client_config": client_config,
		"check_issue_count": len(check_issues),
		"check_allowed": bool(check_issues),
		"repair_denied": repair_denied,
		"expected_buttons": {
			"check_stock_valuation": True,
			"diagnose_valuation_source": True,
			"show_repair_plan": True,
			"fix_incoming_rate": False,
			"apply_repair": False,
		},
	}


@frappe.whitelist()
def get_local_overflow_scenario_status() -> dict:
	_ensure_local_site()
	sle = frappe.get_all(
		"Stock Ledger Entry",
		filters={"item_code": ITEM_CODE},
		fields=[
			"name",
			"qty_after_transaction",
			"incoming_rate",
			"valuation_rate",
			"stock_value",
			"stock_value_difference",
		],
		order_by="posting_datetime desc, creation desc",
		limit_page_length=1,
	)
	bin_rows = frappe.get_all(
		"Bin",
		filters={"item_code": ITEM_CODE},
		fields=["name", "actual_qty", "valuation_rate", "stock_value"],
		limit_page_length=1,
	)
	invoice = frappe.get_all(
		"Sales Invoice Item",
		filters={"item_code": ITEM_CODE, "parenttype": "Sales Invoice", "docstatus": ("in", [0, 1])},
		fields=["parent", "docstatus"],
		order_by="creation desc",
		limit_page_length=1,
	)
	return {
		"invoice": invoice[0] if invoice else None,
		"sle": sle[0] if sle else None,
		"bin": bin_rows[0] if bin_rows else None,
	}
