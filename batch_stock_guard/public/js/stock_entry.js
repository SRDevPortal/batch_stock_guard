frappe.ui.form.on("Stock Entry", {
    refresh(frm) {
        if (frm.doc.docstatus === 0) {
            frm.dashboard.add_comment(
                __("&#8505;&#65039; <b>Batch Stock Policy:</b> Individual batches may go negative, " +
                "but the item's total stock across <em>all warehouses</em> cannot. " +
                "The system enforces this on submit."),
                "blue",
                true
            );
        }
    }
});
