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

frappe.ui.form.on("Sales Invoice", {
	setup(frm) {
		set_negative_batch_query(frm);
	},
	onload(frm) {
		set_negative_batch_query(frm);
	},
	refresh(frm) {
		set_negative_batch_query(frm);
	},
});
