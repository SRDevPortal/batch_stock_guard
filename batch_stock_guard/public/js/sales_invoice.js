const set_negative_batch_query = (frm) => {
	const query = function (doc, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.item_code) {
			frappe.throw(__("Please enter Item Code to get batch no"));
		}

		return {
			query: "batch_stock_guard.batch_stock_guard.api.get_batch_no_for_sales_invoice",
			filters: {
				item_code: row.item_code,
				warehouse: row.warehouse,
				posting_date: doc.posting_date || frappe.datetime.nowdate(),
				posting_time: doc.posting_time || null,
			},
		};
	};

	frm.set_query("batch_no", "items", query);

	const batch_field = frm.fields_dict.items?.grid?.get_field("batch_no");
	if (batch_field) {
		batch_field.get_query = query;
	}
};

const escape_html = (value) => {
	if (frappe.utils?.escape_html) {
		return frappe.utils.escape_html(value);
	}
	return String(value || "")
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;")
		.replaceAll('"', "&quot;")
		.replaceAll("'", "&#039;");
};

const add_stock_guard_buttons = (frm) => {
	frm.add_custom_button(__("Check Stock Valuation"), () => {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Sales Invoice before checking stock valuation."));
			return;
		}

		frappe.call({
			method: "batch_stock_guard.batch_stock_guard.logic.valuation_guard.preview_stock_valuation_for_doc",
			args: {
				doctype: frm.doctype,
				name: frm.doc.name,
			},
			callback(r) {
				const issues = r.message || [];
				if (!issues.length) {
					frappe.msgprint(__("No stock valuation issues found."));
					return;
				}

				const rows = issues.map((issue) => {
					return `<li><b>${escape_html(issue.item_code || "")}</b> - ${escape_html(issue.message || "")}</li>`;
				});
				frappe.msgprint({
					title: __("Stock Valuation Issues"),
					message: `<ul>${rows.join("")}</ul>`,
					indicator: issues.some((issue) => issue.severity === "block") ? "red" : "orange",
				});
			},
		});
	}, __("Batch Stock Guard"));

	frm.add_custom_button(__("Preview Valuation Repair"), () => {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Sales Invoice before previewing valuation repair."));
			return;
		}

		frappe.call({
			method: "batch_stock_guard.batch_stock_guard.valuation_repair.preview_invoice_valuation_repair",
			args: {
				invoice: frm.doc.name,
			},
			callback(r) {
				const rows = r.message || [];
				if (!rows.length) {
					frappe.msgprint(__("No valuation repair rows found."));
					return;
				}

				const summary = rows.map((row) => {
					if (row.status === "skipped") {
						return `<li><b>${escape_html(row.item_code || "")}</b> - ${escape_html(row.warehouse || "")}: ${escape_html(row.message || "")}</li>`;
					}
					return `<li><b>${escape_html(row.item_code || "")}</b> - ${escape_html(row.warehouse || "")}: ${escape_html(String(row.new?.valuation_rate ?? ""))}</li>`;
				});
				frappe.msgprint({
					title: __("Valuation Repair Preview"),
					message: `<ul>${summary.join("")}</ul>`,
					indicator: "blue",
				});
			},
		});
	}, __("Batch Stock Guard"));
};

const add_apply_valuation_repair_button = (frm) => {
	frm.add_custom_button(__("Apply Valuation Repair"), () => {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Sales Invoice before applying valuation repair."));
			return;
		}

		frappe.confirm(
			__("Apply valuation repair for corrupted Bin/SLE values on this Sales Invoice?"),
			() => {
				frappe.call({
					method: "batch_stock_guard.batch_stock_guard.valuation_repair.apply_invoice_valuation_repair",
					args: {
						invoice: frm.doc.name,
						confirm: 1,
					},
					freeze: true,
					freeze_message: __("Applying valuation repair..."),
					callback(r) {
						const rows = r.message || [];
						frappe.msgprint({
							title: __("Valuation Repair Applied"),
							message: __("Updated {0} valuation row(s). Check stock valuation again before submitting.", [
								rows.length,
							]),
							indicator: "green",
						});
					},
				});
			}
		);
	}, __("Batch Stock Guard"));
};

const maybe_add_apply_valuation_repair_button = (frm) => {
	if (frm.is_new()) {
		return;
	}

	frappe.call({
		method: "batch_stock_guard.batch_stock_guard.valuation_repair.preview_invoice_valuation_repair",
		args: {
			invoice: frm.doc.name,
		},
		callback(r) {
			const rows = r.message || [];
			if (rows.some((row) => row.status !== "skipped")) {
				add_apply_valuation_repair_button(frm);
			}
		},
	});
};

frappe.ui.form.on("Sales Invoice", {
	setup(frm) {
		set_negative_batch_query(frm);
	},
	onload(frm) {
		set_negative_batch_query(frm);
	},
	refresh(frm) {
		set_negative_batch_query(frm);
		if (frm.doc.docstatus !== 0 || !frm.doc.update_stock) {
			return;
		}

		frappe.call({
			method: "batch_stock_guard.batch_stock_guard.settings.get_client_config",
			callback(r) {
				const config = r.message || {};
				if (!config.enable_client_buttons) {
					return;
				}
				add_stock_guard_buttons(frm);
				maybe_add_apply_valuation_repair_button(frm);
			},
		});
	},
});
