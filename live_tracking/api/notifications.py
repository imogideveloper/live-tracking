"""Push-device-token registration, shared by the customer and driver mobile apps."""

import frappe


@frappe.whitelist()
def register_device_token(token: str, platform: str = None, device_id: str = None):
	existing = frappe.db.get_value(
		"Towing Device Token", {"user": frappe.session.user, "token": token}
	)
	if existing:
		frappe.db.set_value("Towing Device Token", existing, "is_active", 1)
		return {"name": existing}

	doc = frappe.get_doc(
		{
			"doctype": "Towing Device Token",
			"user": frappe.session.user,
			"token": token,
			"platform": platform,
			"device_id": device_id,
			"is_active": 1,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": doc.name}


@frappe.whitelist()
def unregister_device_token(token: str):
	frappe.db.set_value(
		"Towing Device Token", {"user": frappe.session.user, "token": token}, "is_active", 0
	)
	frappe.db.commit()
	return {"success": True}
