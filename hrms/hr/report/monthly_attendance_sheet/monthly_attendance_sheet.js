// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Monthly Attendance Sheet"] = {
	filters: [
		{
			fieldname: "filter_based_on",
			label: __("Filter Based On"),
			fieldtype: "Select",
			options: ["Month", "Date Range"],
			default: "Month",
			reqd: 1,
			on_change: (report) => {
				let filter_based_on = frappe.query_report.get_filter_value("filter_based_on");

				if (filter_based_on == "Month") {
					set_reqd_filter("month", true);
					set_reqd_filter("year", true);
					set_reqd_filter("start_date", false);
					set_reqd_filter("end_date", false);
				}
				if (filter_based_on == "Date Range") {
					set_reqd_filter("month", false);
					set_reqd_filter("year", false);
					set_reqd_filter("start_date", true);
					set_reqd_filter("end_date", true);
				}
				report.refresh();
			},
		},
		{
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: [
				{ value: 1, label: __("Jan") },
				{ value: 2, label: __("Feb") },
				{ value: 3, label: __("Mar") },
				{ value: 4, label: __("Apr") },
				{ value: 5, label: __("May") },
				{ value: 6, label: __("June") },
				{ value: 7, label: __("July") },
				{ value: 8, label: __("Aug") },
				{ value: 9, label: __("Sep") },
				{ value: 10, label: __("Oct") },
				{ value: 11, label: __("Nov") },
				{ value: 12, label: __("Dec") },
			],
			default: frappe.datetime.str_to_obj(frappe.datetime.get_today()).getMonth() + 1,
			depends_on: "eval:doc.filter_based_on == 'Month'",
		},
		{
			fieldname: "start_date",
			label: __("Start Date"),
			fieldtype: "Date",
			depends_on: "eval:doc.filter_based_on == 'Date Range'",
			on_change: validate_date_range,
		},
		{
			fieldname: "end_date",
			label: __("End Date"),
			fieldtype: "Date",
			depends_on: "eval:doc.filter_based_on == 'Date Range'",
			on_change: validate_date_range,
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Select",
			depends_on: "eval:doc.filter_based_on == 'Month'",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => {
				var company = frappe.query_report.get_filter_value("company");
				return {
					filters: {
						company: company,
					},
				};
			},
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["", "Branch", "Grade", "Department", "Designation"],
		},
		{
			fieldname: "include_company_descendants",
			label: __("Include Company Descendants"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "summarized_view",
			label: __("Summarized View"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_chart",
			label: __("Show Chart"),
			fieldtype: "Check",
			default: 0,
		},
	],
	onload: function () {
		return frappe.call({
			method: "hrms.hr.report.monthly_attendance_sheet.monthly_attendance_sheet.get_attendance_years",
			callback: function (r) {
				var year_filter = frappe.query_report.get_filter("year");
				year_filter.df.options = r.message;
				year_filter.df.default = r.message.split("\n")[0];
				year_filter.refresh();
				year_filter.set_input(year_filter.df.default);
			},
		});
	},
	formatter: function (value, row, column, data, default_formatter) {
		if (column.fieldtype === "Float") return format_total(value);

		value = default_formatter(value, row, column, data);
		const group_by = frappe.query_report.get_filter_value("group_by");

		if (group_by && column.colIndex === 1) {
			value = "<strong>" + value + "</strong>";
		}

		const mark = data && data.marks && data.marks[column.fieldname];

		return mark ? get_mark_html(value, mark) : value;
	},
	get_datatable_options: (options) => ({
		...options,
		// a day carries two lines, the mark and the note under it, the way the sheet page
		// draws it; the stock 33 fits only one. The number has to be the real height of
		// the two, because it is also what the virtual scroll positions rows by, and the
		// cell hides whatever does not fit — see the line heights in style_days
		cellHeight: 50,
	}),
	after_datatable_render: () => {
		// the wrapper keeps the space of the chart it last drew, so it is hidden by hand
		if (!frappe.query_report.get_filter_value("show_chart")) frappe.query_report.$chart.hide();
		style_days();
	},
};

// the day cells are the sheet page's cells: the same class names, the same rules, down to
// the padding. The rest is what the datatable costs — its cell is a fixed box that hides
// what does not fit, so the two lines are given a height each and the box is cut to the
// bone (the stock half a rem all round leaves the second line under the floor). The
// filter row is not a day and keeps the height it had. Written once, reaching one table
function style_days() {
	const table_class = "attendance-marks";
	frappe.query_report.$report.addClass(table_class);

	if (document.getElementById(table_class)) return;

	const style = document.createElement("style");
	style.id = table_class;
	style.textContent = `
		.${table_class} .dt-cell__content { padding: 6px 4px; }
		.${table_class} .status { display: block; line-height: 1.4; }
		.${table_class} .hours { display: block; font-size: var(--text-xs); line-height: 1.2; }
		.${table_class} .hours.leave { color: var(--text-muted); }
		.${table_class} .hours.over { color: var(--green-500, green); }
		.${table_class} .hours.under { color: var(--red-500, red); }
		.${table_class} .dt-row-filter .dt-cell { height: 33px; }
	`;
	document.head.appendChild(style);
}

// the totals read the way the sheet page prints them: two decimals at the most, and none
// at all where the number is whole
function format_total(value) {
	if (value === null || value === undefined || value === "") return "";

	return String(Math.round(flt(value) * 100) / 100);
}

// the day of a cell, the way the attendance sheet page draws it: the mark in the colour
// of its status, and under it the hours the day ran over or short, or the kind of leave.
// Both come from the server, so the report holds no table of its own to keep in step
function get_mark_html(value, mark) {
	const note = mark.note
		? `<span class="hours ${mark.note.kind}">${frappe.utils.escape_html(
				mark.note.text,
		  )}</span>`
		: "";

	return `<span class="status" style="color:${mark.color}">${value}</span>${note}`;
}

function set_reqd_filter(fieldname, is_reqd) {
	let filter = frappe.query_report.get_filter(fieldname);
	filter.df.reqd = is_reqd;
	filter.refresh();
}
function validate_date_range(report) {
	let start_date = frappe.query_report.get_filter_value("start_date");
	let end_date = frappe.query_report.get_filter_value("end_date");
	if (!(start_date && end_date)) return;

	let start = frappe.datetime.str_to_obj(start_date);
	let end = frappe.datetime.str_to_obj(end_date);
	let milli_seconds_in_a_day = 24 * 60 * 60 * 1000;
	let day_diff = Math.floor((end - start) / milli_seconds_in_a_day);
	if (day_diff > 90) {
		frappe.throw({
			message: __("Please set a date range less than 90 days."),
			title: __("Date Range Exceeded"),
		});
	}
	report.refresh();
}
