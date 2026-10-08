import frappe


@frappe.whitelist()
def get_links_for_reference(reference_doctype: str, reference_name: str):
	session_name = frappe.db.get_value(
		"Tracking Session",
		{"reference_doctype": reference_doctype, "reference_name": reference_name},
	)
	if not session_name:
		return None

	session = frappe.get_doc("Tracking Session", session_name)
	frappe.has_permission("Tracking Session", doc=session, throw=True)

	return {
		"status": session.status,
		"tracking_url": session.get_tracking_url(),
		"driver_url": session.get_driver_url(),
	}
