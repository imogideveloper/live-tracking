"""SOS/emergency signal, shared by both the customer and driver mobile apps.

Just logs a Towing SOS Alert doc for admin follow-up (visible in the desk /
ops workspace) — there's no push-notification pipeline yet to page an admin
in real time, so this is a "raise a flag admin sees when they look" safety
net, not a dispatch system.
"""

import frappe
from frappe import _


@frappe.whitelist()
def trigger_sos(
	reference_doctype: str,
	reference_name: str,
	latitude: float = None,
	longitude: float = None,
):
	if reference_doctype == "Towing Request":
		role = "Customer"
		owner = frappe.db.get_value("Towing Request", reference_name, "user")
		if owner != frappe.session.user:
			frappe.throw(_("Anda tidak berhak mengirim SOS untuk trip ini."), frappe.PermissionError)
		towing_request = reference_name
		delivery_order_towing = frappe.db.get_value("Towing Request", reference_name, "delivery_order_towing")
	elif reference_doctype == "Delivery Order Towing":
		role = "Driver"
		do_driver = frappe.db.get_value("Delivery Order Towing", reference_name, "driver")
		session_driver = frappe.db.get_value("Driver", {"custom_user": frappe.session.user}, "name")
		if not session_driver or do_driver != session_driver:
			frappe.throw(_("Anda tidak berhak mengirim SOS untuk trip ini."), frappe.PermissionError)
		delivery_order_towing = reference_name
		towing_request = frappe.db.get_value("Towing Request", {"delivery_order_towing": reference_name}, "name")
	else:
		frappe.throw(_("Referensi tidak valid."))

	doc = frappe.get_doc(
		{
			"doctype": "Towing SOS Alert",
			"raised_by": frappe.session.user,
			"role": role,
			"towing_request": towing_request,
			"delivery_order_towing": delivery_order_towing,
			"latitude": latitude,
			"longitude": longitude,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return {
		"success": True,
		"alert": doc.name,
		"emergency_contact_number": frappe.db.get_single_value(
			"Towing Tariff Setting", "emergency_contact_number"
		),
	}
