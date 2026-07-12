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

const fmt_rate = (value) => {
	const number_value = Number(value || 0);
	if (!Number.isFinite(number_value)) {
		return "";
	}
	return number_value.toFixed(6);
};

const show_update_stock_disabled_message = (frm) => {
	if (frm.doc.docstatus !== 0 || frm.doc.update_stock) {
		return;
	}
	frm.dashboard.add_comment(
		__(
			"Stock valuation check skipped because Update Stock is disabled. This invoice will not create stock movement."
		),
		"orange",
		true
	);
};

const render_diagnosis_rows = (rows) => {
	if (!rows.length) {
		return `<p>${__("No invoice rate source issues found.")}</p>`;
	}

	const heading_style =
		"background: var(--fg-color); color: var(--text-color); font-weight: 600; vertical-align: top; white-space: normal;";
	const text_cell_style = "vertical-align: top; white-space: normal; word-break: break-word;";
	const number_cell_style = "text-align: right; vertical-align: top; white-space: nowrap; font-variant-numeric: tabular-nums;";

	return `<div style="overflow-x: auto; padding-bottom: 2px;">
		<table class="table table-bordered table-condensed" style="min-width: 980px; margin-bottom: 0;">
			<thead>
				<tr>
					<th style="${heading_style} width: 170px;">${__("Item")}</th>
					<th style="${heading_style} width: 120px;">${__("Batch")}</th>
					<th style="${heading_style} width: 170px;">${__("Issue Source")}</th>
					<th style="${heading_style} width: 130px; text-align: right;">${__("Invoice Incoming Rate")}</th>
					<th style="${heading_style} width: 120px; text-align: right;">${__("Current Bin Rate")}</th>
					<th style="${heading_style} width: 130px; text-align: right;">${__("Previous SLE Rate")}</th>
					<th style="${heading_style} width: 140px; text-align: right;">${__("Suggested Safe Rate")}</th>
				</tr>
			</thead>
			<tbody>
				${rows
					.map((row) => {
						const sle = row.latest_sle || {};
						return `<tr>
							<td style="${text_cell_style}">${escape_html(row.item_code || "")}</td>
							<td style="${text_cell_style}">${escape_html(row.batch_no || "")}</td>
							<td style="${text_cell_style}">${escape_html(row.source || "")}</td>
							<td style="${number_cell_style}">${escape_html(fmt_rate(row.incoming_rate))}</td>
							<td style="${number_cell_style}">${escape_html(fmt_rate(row.bin?.valuation_rate))}</td>
							<td style="${number_cell_style}">${escape_html(fmt_rate(sle.valuation_rate))}</td>
							<td style="${number_cell_style}">${escape_html(fmt_rate(row.suggested_rate))}</td>
						</tr>`;
					})
					.join("")}
			</tbody>
		</table>
	</div>`;
};

const show_rate_source_diagnosis = (rows) => {
	const dialog = new frappe.ui.Dialog({
		title: __("Valuation Source Diagnosis"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "diagnosis_html",
			},
		],
		primary_action_label: __("Close"),
		primary_action() {
			dialog.hide();
		},
	});

	dialog.fields_dict.diagnosis_html.$wrapper.html(render_diagnosis_rows(rows));
	dialog.show();
	dialog.$wrapper.find(".modal-dialog").css("max-width", "1100px");
};

const render_issue_details = (issue) => {
	const details = issue.details || {};
	const latest_sle = details.latest_sle || {};
	const offending_sle = details.offending_sle || {};
	const heading_style =
		"width: 260px; background: var(--fg-color); color: var(--text-color); font-weight: 600; vertical-align: top;";
	const value_style = "vertical-align: top; word-break: break-word;";
	const number_value_style = "text-align: right; vertical-align: top; white-space: nowrap; font-variant-numeric: tabular-nums;";
	const rows = [
		[__("Issue Source"), issue.source],
		[__("Invoice Rate Field"), details.rate_fieldname],
		[__("Invoice Incoming/Transaction Rate"), details.incoming_rate],
		[__("Current Bin Valuation Rate"), details.bin_valuation_rate],
		[__("Current Bin Stock Value"), details.bin_stock_value],
		[__("Bundle Average Rate"), details.bundle_rate],
		[__("Latest Previous SLE"), latest_sle.name],
		[__("Previous SLE Valuation Rate"), latest_sle.valuation_rate],
		[__("Previous SLE Stock Value"), latest_sle.stock_value],
		[__("Suggested Safe Rate"), details.suggested_rate],
		[__("Suggested Rate Source"), details.suggested_rate_source],
		[__("Offending SLE"), offending_sle.name],
		[__("Offending SLE Time"), offending_sle.posting_datetime],
		[__("Source Voucher Type"), offending_sle.voucher_type],
		[__("Source Voucher"), offending_sle.voucher_no],
		[__("Unsafe Field"), details.offending_field],
		[__("Unsafe Value"), details.offending_value],
	].filter((row) => row[1] !== undefined && row[1] !== null && row[1] !== "");

	if (!rows.length) {
		return "";
	}

	return `<table class="table table-bordered table-condensed" style="margin: 10px 0 0;">
		<tbody>
			${rows
				.map(([label, value]) => {
					const is_number = typeof value === "number";
					const display = is_number ? fmt_rate(value) : value;
					return `<tr>
						<td style="${heading_style}">${escape_html(label)}</td>
						<td style="${is_number ? number_value_style : value_style}">${escape_html(display)}</td>
					</tr>`;
				})
				.join("")}
		</tbody>
	</table>`;
};

const get_issue_action = (issue) => {
	if (["transaction_rate_source", "invoice_row", "bin_and_transaction_rate"].includes(issue.source)) {
		return __("Use Fix Incoming Rate.");
	}
	if (["bin", "latest_sle"].includes(issue.source)) {
		return __("Use Apply Bin/SLE Repair.");
	}
	if (issue.source === "serial_and_batch_bundle") {
		return __("Check the Serial and Batch Bundle rate source.");
	}
	if (issue.source === "ledger_replay_chain") {
		return __("Repair the source voucher/SLE, then repost stock before submitting.");
	}
	if (issue.source === "database_storage_limit") {
		return __("Repair the corrupted valuation before submitting.");
	}
	return __("Review the diagnosis before submitting.");
};

const render_stock_valuation_issues = (issues) => {
	const groups = new Map();
	for (const issue of issues) {
		const key = issue.row_name || `${issue.item_code || ""}::${issue.warehouse || ""}`;
		if (!groups.has(key)) {
			groups.set(key, {
				primary: issue,
				messages: [],
			});
		}
		groups.get(key).messages.push(issue.message || "");
	}

	return Array.from(groups.values())
		.map(({ primary, messages }) => {
			const unique_messages = [...new Set(messages.filter(Boolean))];
			const message_list = unique_messages
				.map((message) => `<li>${escape_html(message)}</li>`)
				.join("");
			const severity_color = primary.severity === "block" ? "red" : "orange";
			return `<div style="border: 1px solid var(--border-color); border-radius: 6px; padding: 14px; margin-bottom: 14px;">
				<div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 10px;">
					<div>
						<div style="font-weight: 600;">${escape_html(primary.item_code || "")}</div>
						<div class="text-muted">${escape_html(primary.warehouse || "")}</div>
					</div>
					<div style="text-align: right;">
						<span class="indicator-pill ${severity_color}">${escape_html(primary.source || "")}</span>
						<div class="text-muted" style="margin-top: 4px;">${escape_html(get_issue_action(primary))}</div>
					</div>
				</div>
				<ul style="margin: 0 0 10px 18px; padding: 0;">${message_list}</ul>
				${render_issue_details(primary)}
			</div>`;
		})
		.join("");
};

const show_stock_valuation_issues = (issues) => {
	const dialog = new frappe.ui.Dialog({
		title: __("Stock Valuation Issues"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "issues_html",
			},
		],
		primary_action_label: __("Close"),
		primary_action() {
			dialog.hide();
		},
	});

	dialog.fields_dict.issues_html.$wrapper.html(render_stock_valuation_issues(issues));
	dialog.show();
	dialog.$wrapper.find(".modal-dialog").css("max-width", "1100px");
};

const add_stock_guard_buttons = (frm, config) => {
	if (config.can_check_stock_valuation) {
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
					if (issues.some((issue) => issue.severity === "skipped")) {
						frappe.msgprint({
							title: __("Stock Valuation Skipped"),
							message: escape_html(issues[0].message || ""),
							indicator: "orange",
						});
						return;
					}

					if (!issues.length) {
						frappe.msgprint(__("No stock valuation issues found."));
						return;
					}

					show_stock_valuation_issues(issues);
				},
			});
		}, __("Batch Stock Guard"));

		frm.add_custom_button(__("Diagnose Valuation Source"), () => {
			if (frm.is_new()) {
				frappe.msgprint(__("Please save the Sales Invoice before diagnosing rate source."));
				return;
			}

			frappe.call({
				method: "batch_stock_guard.batch_stock_guard.logic.valuation_guard.diagnose_invoice_rate_source",
				args: {
					invoice: frm.doc.name,
				},
				callback(r) {
					const result = r.message || {};
					if (result.skipped) {
						frappe.msgprint({
							title: __("Stock Valuation Skipped"),
							message: escape_html(result.message || ""),
							indicator: "orange",
						});
						return;
					}
					show_rate_source_diagnosis(result.rows || []);
				},
			});
		}, __("Batch Stock Guard"));
	}

	if (config.can_apply_valuation_repair) {
		frm.add_custom_button(__("Fix Incoming Rate"), () => {
			if (frm.is_new()) {
				frappe.msgprint(__("Please save the Sales Invoice before fixing incoming rates."));
				return;
			}

			frappe.call({
				method: "batch_stock_guard.batch_stock_guard.logic.valuation_guard.diagnose_invoice_rate_source",
				args: {
					invoice: frm.doc.name,
				},
				callback(r) {
					const rows = (r.message?.rows || []).filter(
						(row) => Number(row.incoming_rate || 0) < 0 && Number(row.suggested_rate || 0) > 0
					);
					if (!rows.length) {
						frappe.msgprint(__("No negative invoice incoming rates with safe suggested rates were found."));
						return;
					}

					const summary = rows.map((row) => {
						return `<li><b>${escape_html(row.item_code || "")}</b>: ${escape_html(
							fmt_rate(row.incoming_rate)
						)} -> <b>${escape_html(fmt_rate(row.suggested_rate))}</b> (${escape_html(
							row.suggested_rate_source || ""
						)})</li>`;
					});

					frappe.confirm(
						`${__("Apply these incoming rate fixes?")}<br><ul>${summary.join("")}</ul>`,
						() => {
							frappe.call({
								method: "batch_stock_guard.batch_stock_guard.logic.valuation_guard.fix_invoice_incoming_rate",
								args: {
									invoice: frm.doc.name,
									rows,
									confirm: 1,
								},
								freeze: true,
								freeze_message: __("Fixing invoice incoming rates..."),
								callback(result) {
									const updates = result.message || [];
									frappe.msgprint({
										title: __("Incoming Rates Updated"),
										message: __("Updated {0} invoice row(s). Check stock valuation again before submitting.", [
											updates.length,
										]),
										indicator: "green",
									});
									frm.reload_doc();
								},
							});
						}
					);
				},
			});
		}, __("Batch Stock Guard"));
	}

	if (config.can_preview_valuation_repair) {
		frm.add_custom_button(__("Show Bin/SLE Repair Plan"), () => {
			if (frm.is_new()) {
				frappe.msgprint(__("Please save the Sales Invoice before previewing Bin/SLE repair."));
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
						frappe.msgprint(__("No Bin/SLE valuation repair rows found."));
						return;
					}

					const summary = rows.map((row) => {
						if (row.status === "skipped") {
							return `<li><b>${escape_html(row.item_code || "")}</b> - ${escape_html(row.warehouse || "")}: ${escape_html(row.message || "")}</li>`;
						}
						return `<li><b>${escape_html(row.item_code || "")}</b> - ${escape_html(row.warehouse || "")}: ${escape_html(String(row.new?.valuation_rate ?? ""))}</li>`;
					});
					frappe.msgprint({
						title: __("Bin/SLE Repair Plan"),
						message: `<ul>${summary.join("")}</ul>`,
						indicator: "blue",
					});
				},
			});
		}, __("Batch Stock Guard"));
	}
};

const add_apply_valuation_repair_button = (frm) => {
	frm.add_custom_button(__("Apply Bin/SLE Repair"), () => {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Sales Invoice before applying valuation repair."));
			return;
		}

		frappe.call({
			method: "batch_stock_guard.batch_stock_guard.valuation_repair.preview_invoice_valuation_repair",
			args: {
				invoice: frm.doc.name,
			},
			callback(r) {
				const rows = r.message || [];
				const repairable_rows = rows.filter((row) => row.status !== "skipped");
				if (!repairable_rows.length) {
					frappe.msgprint({
						title: __("No Bin/SLE Repair Rows"),
						message: __(
							"No Bin/SLE valuation repair rows were found for this invoice. If the issue is a negative invoice incoming rate, use Fix Incoming Rate."
						),
						indicator: "orange",
					});
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
							callback(result) {
								const updated_rows = result.message || [];
								frappe.msgprint({
									title: __("Valuation Repair Applied"),
									message: __("Updated {0} valuation row(s). Check stock valuation again before submitting.", [
										updated_rows.length,
									]),
									indicator: "green",
								});
							},
						});
					}
				);
			},
		});
	}, __("Batch Stock Guard"));
};

const add_apply_valuation_repair_button_if_allowed = (frm, config) => {
	if (frm.is_new() || !config.can_apply_valuation_repair) {
		return;
	}
	add_apply_valuation_repair_button(frm);
};

const remove_stock_guard_button = (frm, label) => {
	if (frm.remove_custom_button) {
		frm.remove_custom_button(__(label), __("Batch Stock Guard"));
	}
};

const update_contextual_action_buttons = (frm, config) => {
	if (frm.is_new()) {
		return;
	}

	if (config.can_apply_valuation_repair) {
		frappe.call({
			method: "batch_stock_guard.batch_stock_guard.logic.valuation_guard.diagnose_invoice_rate_source",
			args: {
				invoice: frm.doc.name,
			},
			callback(r) {
				const rows = (r.message?.rows || []).filter(
					(row) => Number(row.incoming_rate || 0) < 0 && Number(row.suggested_rate || 0) > 0
				);
				if (!rows.length) {
					remove_stock_guard_button(frm, "Fix Incoming Rate");
				}
			},
		});
	}

	if (config.can_preview_valuation_repair || config.can_apply_valuation_repair) {
		frappe.call({
			method: "batch_stock_guard.batch_stock_guard.valuation_repair.preview_invoice_valuation_repair",
			args: {
				invoice: frm.doc.name,
			},
			callback(r) {
				const rows = (r.message || []).filter((row) => row.status !== "skipped");
				if (!rows.length) {
					remove_stock_guard_button(frm, "Show Bin/SLE Repair Plan");
					remove_stock_guard_button(frm, "Apply Bin/SLE Repair");
				}
			},
		});
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
		show_update_stock_disabled_message(frm);
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
				add_stock_guard_buttons(frm, config);
				add_apply_valuation_repair_button_if_allowed(frm, config);
				update_contextual_action_buttons(frm, config);
			},
		});
	},
	update_stock(frm) {
		if (frm.dashboard.clear_comment) {
			frm.dashboard.clear_comment();
		}
		frm.trigger("refresh");
	},
});
