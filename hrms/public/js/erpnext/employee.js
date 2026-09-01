// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Employee", {
	refresh: function (frm) {
		frm.set_query("payroll_cost_center", function () {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
				},
			};
		});

		// Table MultiSelect reads get_query off the parent field, not off the link
		// inside the child table, so the query is set on the field itself.
		frm.set_query("attendance_sheet_extra_employees", function (doc) {
			return {
				query: "erpnext.payroll_ua.page.attendance_sheet.attendance_sheet.extra_employee_query",
				filters: { manager: doc.name },
			};
		});
	},

	date_of_birth(frm) {
		frm.call({
			method: "hrms.overrides.employee_master.get_retirement_date",
			args: {
				date_of_birth: frm.doc.date_of_birth,
			},
		}).then((r) => {
			if (r && r.message) frm.set_value("date_of_retirement", r.message);
		});
	},
});
