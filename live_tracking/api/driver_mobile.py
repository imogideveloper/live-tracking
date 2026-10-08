"""Authenticated mobile API for the driver app (session-based, not driver_token-based).

Kept deliberately separate from api/driver.py, which remains the guest-token,
one-trip-at-a-time API used by the (still working) web fallback page.
"""

import base64

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import cint, flt, now_datetime, today

from live_tracking.api import driver as guest_driver
from live_tracking.api import tracking

ACTIVE_DO_STATUSES = ["Assigned", "Pick Up", "Delivered"]

# Vehicle-condition photo stages map straight onto the two Attach fields
# `Delivery Order Towing` already had (attachment_invoice aside) — no new
# doctype/field needed, just a way for the mobile app to fill them in.
FOTO_FIELD_BY_STAGE = {
	"pickup": "foto_kendaraan",
	"delivered": "foto_delivered",
}


def _get_driver():
	driver = frappe.db.get_value(
		"Driver",
		{"custom_user": frappe.session.user},
		["name", "full_name", "status", "custom_unit_towing"],
		as_dict=True,
	)
	if not driver:
		frappe.throw(_("Akun ini belum terhubung ke data Driver."), frappe.PermissionError)
	return driver


def _get_active_do(driver_name: str):
	return frappe.db.get_value(
		"Delivery Order Towing",
		{"driver": driver_name, "status": ["in", ACTIVE_DO_STATUSES], "docstatus": ["<", 2]},
		[
			"name",
			"status",
			"customer",
			"customer_name",
			"nomor_polisi",
			"lokasi_pickup",
			"lokasi_tujuan",
			"foto_kendaraan",
			"foto_delivered",
		],
		as_dict=True,
	)


@frappe.whitelist()
def set_online_status(is_online):
	driver = _get_driver()
	frappe.db.set_value("Driver", driver.name, "custom_is_online", 1 if int(is_online) else 0)
	frappe.db.commit()
	return {"is_online": bool(int(is_online))}


@frappe.whitelist()
def update_my_location(latitude: float, longitude: float):
	driver = _get_driver()
	frappe.db.set_value(
		"Driver",
		driver.name,
		{
			"custom_current_latitude": flt(latitude, 6),
			"custom_current_longitude": flt(longitude, 6),
			"custom_last_location_at": now_datetime(),
		},
	)
	frappe.db.commit()
	return {"success": True}


@frappe.whitelist()
def get_pending_offers():
	driver = _get_driver()
	offers = frappe.get_all(
		"Towing Job Offer",
		filters={"driver": driver.name, "status": "Pending"},
		fields=["name", "towing_request", "distance_km", "offered_at"],
		order_by="creation desc",
	)
	for offer in offers:
		req = frappe.db.get_value(
			"Towing Request",
			offer.towing_request,
			["pickup_label", "dropoff_label", "price_estimate", "vehicle_type"],
			as_dict=True,
		)
		offer.update(req or {})
	return offers


@frappe.whitelist()
def respond_offer(offer_name: str, accept):
	driver = _get_driver()
	accept = bool(frappe.utils.cint(accept)) if not isinstance(accept, bool) else accept

	offer = frappe.get_doc("Towing Job Offer", offer_name)
	if offer.driver != driver.name:
		frappe.throw(_("Offer ini bukan untuk Anda."), frappe.PermissionError)
	if offer.status != "Pending":
		frappe.throw(_("Offer ini sudah tidak berlaku."))

	if not accept:
		offer.status = "Rejected"
		offer.responded_at = now_datetime()
		offer.save(ignore_permissions=True)
		frappe.db.commit()
		return {"status": "Rejected"}

	# Row-lock the request so two drivers accepting at once can't both win.
	locked = frappe.db.sql(
		"""SELECT name, status, customer, pickup_label, pickup_latitude, pickup_longitude,
			dropoff_label, dropoff_latitude, dropoff_longitude, vehicle_type, price_estimate,
			delivery_order_towing
			FROM `tabTowing Request` WHERE name=%s FOR UPDATE""",
		(offer.towing_request,),
		as_dict=True,
	)
	if not locked:
		frappe.throw(_("Permintaan towing tidak ditemukan."))
	request_row = locked[0]

	if request_row.status not in ("Searching", "Offered"):
		offer.status = "Expired"
		offer.responded_at = now_datetime()
		offer.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.throw(_("Maaf, order ini sudah diambil driver lain."))

	original_user = frappe.session.user
	try:
		# Creating the DO and driving it Draft -> Assigned is normally a
		# staff-only workflow action (role Admin Towing/Sales Manager) — this
		# is the automated equivalent of that dispatch decision, so it runs
		# with elevated privileges rather than the driver's own role.
		frappe.set_user("Administrator")

		# create_towing_request already created a Draft DO (backed by a
		# submitted Sales Order) the moment the request was made — reuse
		# and promote THAT one instead of creating a second, parallel DO.
		# Creating a fresh one here (the old behaviour) orphaned the
		# Sales-Order-backed Draft forever: Towing Request.delivery_order_towing
		# got overwritten to point at the new one, so the first DO (and its
		# submitted SO) never got touched again by anything, including
		# cancel_request — it just sat there as a stray "Draft" row.
		do_name = request_row.delivery_order_towing
		if do_name and frappe.db.exists("Delivery Order Towing", do_name):
			do = frappe.get_doc("Delivery Order Towing", do_name)
			do.driver = driver.name
			do.harga_jasa = request_row.price_estimate
			do.tipe_kendaraan = request_row.vehicle_type
			do.kendaraan_towing = driver.custom_unit_towing
			do.save(ignore_permissions=True)
		else:
			# Fallback for the rare case create_towing_request's SO/DO
			# creation failed (see _create_sales_order_and_do's except
			# clause) — nothing to reuse, so fall back to creating fresh.
			do = frappe.get_doc(
				{
					"doctype": "Delivery Order Towing",
					"customer": request_row.customer,
					"tanggal_do": today(),
					"lokasi_pickup": request_row.pickup_label,
					"lokasi_tujuan": request_row.dropoff_label,
					"driver": driver.name,
					"harga_jasa": request_row.price_estimate,
					"tipe_kendaraan": request_row.vehicle_type,
					"kendaraan_towing": driver.custom_unit_towing,
				}
			)
			do.insert(ignore_permissions=True)
		apply_workflow(do, "Assign Driver")

		frappe.db.set_value(
			"Towing Request",
			request_row.name,
			{"status": "Assigned", "matched_driver": driver.name, "delivery_order_towing": do.name},
		)

		other_pending = frappe.get_all(
			"Towing Job Offer",
			filters={"towing_request": request_row.name, "status": "Pending"},
			pluck="name",
		)
		for name in other_pending:
			frappe.db.set_value("Towing Job Offer", name, "status", "Expired", update_modified=False)

		offer.reload()
		offer.status = "Accepted"
		offer.responded_at = now_datetime()
		offer.save(ignore_permissions=True)

		frappe.db.commit()
	finally:
		frappe.set_user(original_user)

	return {"status": "Accepted", "delivery_order_towing": do.name}


@frappe.whitelist()
def get_active_trip():
	driver = _get_driver()
	do = _get_active_do(driver.name)
	if not do:
		return None

	transitions = get_transitions(frappe.get_doc("Delivery Order Towing", do.name))

	session_row = frappe.db.get_value(
		"Tracking Session",
		{"reference_doctype": "Delivery Order Towing", "reference_name": do.name},
		tracking.SESSION_FIELDS,
		as_dict=True,
	)

	customer_phone = frappe.db.get_value("Customer", do.customer, "mobile_no") if do.customer else None

	return {
		"delivery_order_towing": do.name,
		"status": do.status,
		"customer_name": do.customer_name,
		"customer_phone": customer_phone,
		"nomor_polisi": do.nomor_polisi,
		"lokasi_pickup": do.lokasi_pickup,
		"lokasi_tujuan": do.lokasi_tujuan,
		"foto_kendaraan": do.foto_kendaraan,
		"foto_delivered": do.foto_delivered,
		"available_actions": [t.get("action") for t in transitions],
		"session": tracking.build_tracking_payload(session_row) if session_row else None,
	}


@frappe.whitelist()
def get_trip_history(limit: int = 20):
	"""Past (and current) trips for this driver, most recently updated
	first — so the driver app has something to show even on a day with
	nothing currently Assigned, instead of a permanently empty screen.
	Drivers also just want to see what they did before, ongoing."""
	driver = _get_driver()
	return frappe.get_all(
		"Delivery Order Towing",
		filters={"driver": driver.name, "docstatus": ["<", 2], "status": ["!=", "Draft"]},
		fields=[
			"name",
			"status",
			"customer_name",
			"nomor_polisi",
			"lokasi_pickup",
			"lokasi_tujuan",
			"tanggal_do",
			"waktu_assigned",
			"waktu_pickup",
			"waktu_delivered",
			"waktu_done",
		],
		order_by="modified desc",
		limit_page_length=cint(limit) or 20,
	)


@frappe.whitelist()
def update_trip_status(action: str):
	"""Drive the Delivery Order Towing workflow forward (Konfirmasi Pick Up /
	Konfirmasi Delivered / Selesaikan DO) — the driver's own "Towing Driver"
	role already has rights to these transitions, no elevation needed."""
	driver = _get_driver()
	do = _get_active_do(driver.name)
	if not do:
		frappe.throw(_("Tidak ada trip aktif."))

	doc = frappe.get_doc("Delivery Order Towing", do.name)
	if doc.driver != driver.name:
		frappe.throw(_("Trip ini bukan milik Anda."), frappe.PermissionError)

	apply_workflow(doc, action)
	frappe.db.commit()
	return {"status": doc.status}


@frappe.whitelist()
def ping_location(latitude: float, longitude: float, speed: float = None, heading: float = None, accuracy: float = None):
	driver = _get_driver()
	do = _get_active_do(driver.name)
	if not do:
		frappe.throw(_("Tidak ada trip aktif."))

	session = frappe.db.get_value(
		"Tracking Session",
		{"reference_doctype": "Delivery Order Towing", "reference_name": do.name},
		["name", "status", "started_at"],
		as_dict=True,
	)
	if not session:
		frappe.throw(_("Sesi tracking belum tersedia untuk trip ini."))

	return guest_driver.record_ping(
		session.name, session.status, session.started_at, latitude, longitude, speed, heading, accuracy
	)


@frappe.whitelist()
def upload_vehicle_photo(stage: str, file_base64: str, file_name: str = None):
	"""Before/after vehicle condition photo, sent as base64 in the JSON body
	(same plain-REST pattern every other endpoint here uses) rather than
	multipart — the app's own image_picker already downsamples the photo
	first, so the base64 overhead stays small. Reduces "siapa yang bikin
	lecet" disputes by giving pickup vs. delivered a paper trail on the DO
	itself, which is exactly what an admin already reviews."""
	driver = _get_driver()
	do = _get_active_do(driver.name)
	if not do:
		frappe.throw(_("Tidak ada trip aktif."))

	fieldname = FOTO_FIELD_BY_STAGE.get(stage)
	if not fieldname:
		frappe.throw(_("Tahap foto tidak dikenali."))

	try:
		content = base64.b64decode(file_base64)
	except Exception:
		frappe.throw(_("File foto tidak valid."))
	if len(content) > 8 * 1024 * 1024:
		frappe.throw(_("Ukuran foto terlalu besar."))

	from frappe.utils.file_manager import save_file

	file_doc = save_file(
		file_name or f"{stage}.jpg",
		content,
		"Delivery Order Towing",
		do.name,
		is_private=0,
	)
	frappe.db.set_value("Delivery Order Towing", do.name, fieldname, file_doc.file_url)
	frappe.db.commit()
	return {"file_url": file_doc.file_url}


@frappe.whitelist()
def get_earnings_summary(from_date: str = None, to_date: str = None):
	driver = _get_driver()
	filters = {"driver": driver.name, "docstatus": 1}
	if from_date:
		filters["from_date"] = [">=", from_date]
	if to_date:
		filters["to_date"] = ["<=", to_date]

	rows = frappe.get_all(
		"Driver Commission",
		filters=filters,
		fields=["name", "from_date", "to_date", "status", "do_count", "total_komisi"],
		order_by="from_date desc",
		limit_page_length=50,
	)
	total = sum(flt(r.total_komisi) for r in rows)
	return {"total_komisi": total, "records": rows}


@frappe.whitelist()
def submit_customer_rating(delivery_order_towing: str, rating: int, review: str = None):
	"""Mirrors customer.submit_rating, other direction — driver rating the
	customer after a trip. Keyed by DO (the driver's own identifier for a
	trip) rather than the Towing Request name the customer app uses."""
	driver = _get_driver()
	do = frappe.db.get_value("Delivery Order Towing", delivery_order_towing, ["driver"], as_dict=True)
	if not do:
		frappe.throw(_("Trip tidak ditemukan."))
	if do.driver != driver.name:
		frappe.throw(_("Anda tidak berhak menilai trip ini."), frappe.PermissionError)

	request_doc = frappe.db.get_value(
		"Towing Request", {"delivery_order_towing": delivery_order_towing}, ["name", "driver_rating"], as_dict=True
	)
	if not request_doc:
		frappe.throw(_("Data permintaan trip tidak ditemukan."))
	if request_doc.driver_rating:
		frappe.throw(_("Trip ini sudah dinilai."))

	rating = int(rating)
	if rating < 1 or rating > 5:
		frappe.throw(_("Rating harus antara 1-5."))

	frappe.db.set_value(
		"Towing Request",
		request_doc.name,
		{"driver_rating": rating, "driver_review": review},
	)
	frappe.db.commit()
	return {"success": True}
