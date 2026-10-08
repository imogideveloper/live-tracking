import frappe

REFERENCE_DOCTYPE = "Delivery Order Towing"

# Delivery Order Towing status -> Tracking Session status.
#
# "Delivered" is leg 2 of the trip (vehicle loaded, driver now en route to
# the customer's actual destination) — NOT the end of tracking. It used to
# map to "Completed" here, which closed the customer's live-tracking screen
# the moment the vehicle was picked up, while the driver was still driving.
# The trip is only actually over once the driver has arrived and confirmed
# it ("Awaiting Dokument") — "Done" is later still (admin paperwork), kept
# here too since a DO can jump straight from "Assigned" to "Done" via the
# admin shortcut, bypassing Pick Up/Delivered/Awaiting Dokument entirely.
STATUS_MAP = {
	"Pick Up": "Active",
	"Delivered": "Active",
	"Awaiting Dokument": "Completed",
	"Done": "Completed",
	"Cancelled": "Cancelled",
}


def sync_tracking_session(doc, method=None):
	"""Keep a Tracking Session in lockstep with a Delivery Order Towing's status.

	Registered as a doc_event on "Delivery Order Towing" so imogi_finance
	never needs to know live_tracking exists.
	"""
	if doc.status == "Draft":
		return

	session_name = frappe.db.get_value(
		"Tracking Session",
		{"reference_doctype": REFERENCE_DOCTYPE, "reference_name": doc.name},
	)

	if not session_name:
		if doc.status != "Assigned":
			# Only start tracking once a driver has actually been assigned.
			return
		_create_session(doc)
		# No frappe.msgprint() here on purpose — a modal popping up the
		# instant a driver gets assigned caught staff off guard ("tiba-tiba
		# ada modal, link apa ini?"). The links are available on demand
		# instead, via the map-pin icon the "Delivery Order Towing-Form-
		# Live-Tracking" Client Script (fixtures/client_script.json) adds to
		# the form once a session exists.
		return

	session = frappe.get_doc("Tracking Session", session_name)

	# Driver can change after the session was created (re-assignment).
	if doc.driver and session.driver != doc.driver:
		session.driver = doc.driver
		session.save(ignore_permissions=True)

	target_status = STATUS_MAP.get(doc.status)
	if not target_status or session.status == target_status:
		return

	if target_status == "Active":
		session.mark_active()
	elif target_status == "Completed":
		session.mark_completed()
	elif target_status == "Cancelled":
		session.mark_cancelled()


def _create_session(doc):
	session = frappe.new_doc("Tracking Session")
	session.reference_doctype = REFERENCE_DOCTYPE
	session.reference_name = doc.name
	session.driver = doc.driver
	session.pickup_label = doc.get("lokasi_pickup")
	session.dropoff_label = doc.get("lokasi_tujuan")
	session.insert(ignore_permissions=True)
	return session


def sync_towing_request_status(doc, method=None):
	"""Mirror a Delivery Order Towing's status back onto the mobile app's
	Towing Request, once an admin manually assigns a driver in Desk (or
	cancels it) — the request was auto-converted into this DO on submit
	(see live_tracking.api.customer.create_towing_request), so the customer
	app needs to know when that DO actually gets a driver."""
	request_name = frappe.db.get_value("Towing Request", {"delivery_order_towing": doc.name})
	if not request_name:
		return

	if doc.status == "Assigned" and doc.driver:
		frappe.db.set_value(
			"Towing Request", request_name, {"status": "Assigned", "matched_driver": doc.driver}
		)
	elif doc.status == "Done":
		# Terminal state — everything from Pick Up through Awaiting Dokument is
		# still "in progress" as far as the customer app's history list is
		# concerned (status stays "Assigned" so the live-tracking view keeps
		# showing), only "Done" means the trip is actually over.
		frappe.db.set_value("Towing Request", request_name, "status", "Completed")
	elif doc.status == "Cancelled":
		frappe.db.set_value("Towing Request", request_name, "status", "Cancelled")
