"""Authenticated mobile API for the customer app (session-based, not token-based)."""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.utils import flt, now_datetime, today

from live_tracking.api import matching, tracking

ACTIVE_STATUSES = ["Searching", "Offered", "Assigned"]

# Generic towing service Item (imogi_finance master data, non-stock) used as
# the Sales Order line for every mobile-app request — routes/pricing aren't
# a fixed catalog here, so pickup/dropoff/price get patched onto the
# auto-created DO afterward instead of coming from per-route Items.
TOWING_ITEM_CODE = "JASA-TOWING-001"


def _get_customer():
	customer = frappe.db.get_value(
		"Customer", {"custom_user": frappe.session.user}, ["name", "customer_name"], as_dict=True
	)
	if not customer:
		frappe.throw(_("Akun ini belum terhubung ke data Customer."), frappe.PermissionError)
	return customer


@frappe.whitelist()
def get_profile():
	customer = _get_customer()
	profile = frappe.db.get_value(
		"Customer", customer.name, ["customer_name", "mobile_no"], as_dict=True
	)
	# Since the email/password + Google migration, a brand-new Customer
	# always gets a real customer_name (from registration or the Google
	# profile) but never a phone number — mobile_no is what the driver's
	# "Hubungi Customer" button depends on, so profile completeness now
	# hinges on THAT, not on the old phone-placeholder-name check.
	profile["is_complete"] = bool(profile.customer_name and profile.mobile_no)
	profile["email"] = frappe.session.user
	return profile


@frappe.whitelist()
def update_profile(customer_name: str, mobile_no: str = None):
	if not (customer_name or "").strip():
		frappe.throw(_("Nama wajib diisi."))

	customer = _get_customer()
	update = {"customer_name": customer_name.strip()}
	if mobile_no and mobile_no.strip():
		update["mobile_no"] = mobile_no.strip()

	frappe.db.set_value("Customer", customer.name, update)
	frappe.db.commit()
	return {"customer_name": update["customer_name"], "mobile_no": update.get("mobile_no")}


@frappe.whitelist()
def change_password(new_password: str):
	if not new_password or len(new_password) < 6:
		frappe.throw(_("Password minimal 6 karakter."))
	from frappe.utils.password import update_password

	update_password(frappe.session.user, new_password)
	frappe.db.commit()
	return {"success": True}


def _vehicle_multiplier(tariff, vehicle_type: str) -> float:
	"""Case-insensitive lookup so 'sedan'/'Sedan'/'SEDAN' from the app's
	free-text field all match a 'Sedan' row in the tariff table. An
	unrecognised or blank type (including the app's "Lainnya" custom entry)
	falls back to a 1x multiplier rather than blocking the estimate."""
	vehicle_type = (vehicle_type or "").strip().lower()
	for row in tariff.vehicle_type_rates:
		if (row.vehicle_type or "").strip().lower() == vehicle_type:
			return flt(row.multiplier) or 1.0
	return 1.0


def _price_estimate(distance_km: float, vehicle_type: str = None) -> float:
	tariff = frappe.get_single("Towing Tariff Setting")
	base = flt(tariff.base_fare)
	per_km = flt(tariff.per_km_rate)
	min_fare = flt(tariff.min_fare)
	multiplier = _vehicle_multiplier(tariff, vehicle_type) if vehicle_type else 1.0
	price = (base + per_km * distance_km) * multiplier
	return max(price, min_fare * multiplier)


@frappe.whitelist()
def estimate_price(
	pickup_latitude: float,
	pickup_longitude: float,
	dropoff_latitude: float,
	dropoff_longitude: float,
	vehicle_type: str = None,
):
	"""Read-only price preview for the booking screen — same haversine +
	tariff math create_towing_request() uses when it actually saves a price,
	just without creating anything. Lets the app show an estimate before the
	customer confirms, Gojek/Grab-style."""
	distance_km = matching.haversine_km(
		flt(pickup_latitude), flt(pickup_longitude), flt(dropoff_latitude), flt(dropoff_longitude)
	)
	return {
		"distance_km": round(distance_km, 2),
		"price_estimate": _price_estimate(distance_km, vehicle_type),
	}


@frappe.whitelist()
def create_towing_request(
	pickup_label: str,
	pickup_latitude: float,
	pickup_longitude: float,
	dropoff_label: str,
	dropoff_latitude: float,
	dropoff_longitude: float,
	vehicle_type: str,
	vehicle_plate_number: str,
	vehicle_chassis_number: str = None,
	vehicle_brand: str = None,
	vehicle_model: str = None,
	vehicle_note: str = None,
):
	if not (vehicle_type or "").strip():
		frappe.throw(_("Tipe kendaraan wajib diisi."))
	if not (vehicle_plate_number or "").strip():
		frappe.throw(_("Nomor polisi kendaraan wajib diisi."))

	customer = _get_customer()

	existing = frappe.get_all(
		"Towing Request",
		filters={"user": frappe.session.user, "status": ["in", ACTIVE_STATUSES]},
		fields=["name", "status", "delivery_order_towing"],
		order_by="creation desc",
		limit=1,
	)
	if existing:
		row = existing[0]
		# "Assigned" never changes back on its own once a trip is matched —
		# the real trip lifecycle plays out on the DO/Tracking Session, so
		# check whether that's actually finished before blocking a new
		# request (same logic as get_active_request()'s _trip_has_ended).
		still_active = not (
			row.status == "Assigned"
			and row.delivery_order_towing
			and _trip_has_ended(row.delivery_order_towing)
		)
		if still_active:
			frappe.throw(_("Anda masih punya permintaan towing yang aktif."))

	distance_km = matching.haversine_km(
		flt(pickup_latitude), flt(pickup_longitude), flt(dropoff_latitude), flt(dropoff_longitude)
	)

	doc = frappe.get_doc(
		{
			"doctype": "Towing Request",
			"user": frappe.session.user,
			"customer": customer.name,
			"status": "Searching",
			"requested_at": now_datetime(),
			"pickup_label": pickup_label,
			"pickup_latitude": pickup_latitude,
			"pickup_longitude": pickup_longitude,
			"dropoff_label": dropoff_label,
			"dropoff_latitude": dropoff_latitude,
			"dropoff_longitude": dropoff_longitude,
			"vehicle_type": vehicle_type,
			"vehicle_plate_number": vehicle_plate_number,
			"vehicle_chassis_number": vehicle_chassis_number,
			"vehicle_brand": vehicle_brand,
			"vehicle_model": vehicle_model,
			"vehicle_note": vehicle_note,
			"price_estimate": _price_estimate(distance_km, vehicle_type),
		}
	).insert()
	frappe.db.commit()

	do_name = _create_sales_order_and_do(doc)
	if do_name:
		frappe.db.set_value(
			"Towing Request", doc.name, {"status": "Offered", "delivery_order_towing": do_name}
		)
		frappe.db.commit()

		frappe.publish_realtime(
			"towing_new_order",
			{
				"towing_request": doc.name,
				"pickup_label": doc.pickup_label,
				"dropoff_label": doc.dropoff_label,
				"customer_name": customer.customer_name,
				"delivery_order_towing": do_name,
			},
		)

	return frappe.get_doc("Towing Request", doc.name).as_dict()


def _vehicle_brand_model_label(request) -> str:
	label = " ".join(part for part in (request.vehicle_brand, request.vehicle_model) if part)
	return label or request.vehicle_type


def _chassis_number_for_so(request) -> str:
	if (request.vehicle_chassis_number or "").strip():
		return request.vehicle_chassis_number
	# VIN is optional in the app — but SO Towing Kendaraan.nomor_rangka is
	# required and checked by validate_no_duplicate_rangka_rute, so a fixed
	# placeholder like "-" would make every VIN-less request look like the
	# same rangka+route being "reused". Fall back to the request's own
	# unique name, same trick the pre-VIN-collection code used for every
	# request.
	return request.name


def _create_sales_order_and_do(request):
	"""Turns a customer's Towing Request straight into a submitted Sales
	Order, which — via imogi_finance's existing on_submit hook
	(create_do_from_sales_order) — auto-creates the Delivery Order Towing an
	admin will assign a driver to from Desk. Runs elevated since creating
	and submitting a Sales Order is normally a Sales role action, not
	something the customer's own account can do."""
	from imogi_finance.overrides.delivery_order_towing import _resolve_company

	original_user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		company = _resolve_company()
		transaction_date = today()

		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": request.customer,
				"company": company,
				"transaction_date": transaction_date,
				"delivery_date": transaction_date,
				"items": [
					{
						"item_code": TOWING_ITEM_CODE,
						"qty": 1,
						"rate": request.price_estimate,
						"delivery_date": transaction_date,
					}
				],
				"custom_towing_kendaraan": [
					{
						"so_item_code": TOWING_ITEM_CODE,
						# Real vehicle data collected from the customer at
						# booking time (2026-09-23) — previously this used
						# request.name as a nomor_rangka placeholder (dodging
						# validate_no_duplicate_rangka_rute, since the app
						# didn't collect a real VIN/plate at all), which
						# showed up confusingly as a "VIN" in the desk.
						"tipe_model": _vehicle_brand_model_label(request),
						"nomor_rangka": _chassis_number_for_so(request),
						"nomor_polisi": request.vehicle_plate_number,
					}
				],
			}
		)
		so.insert(ignore_permissions=True)
		so.submit()

		do_name = frappe.db.get_value("SO Towing Kendaraan", {"parent": so.name}, "delivery_order")
		if do_name:
			# The auto-created DO's pickup/dropoff came from the generic
			# item (blank) — patch in the customer's actual request details.
			frappe.db.set_value(
				"Delivery Order Towing",
				do_name,
				{
					"lokasi_pickup": request.pickup_label,
					"lokasi_tujuan": request.dropoff_label,
					"catatan_khusus": request.vehicle_note,
				},
			)
		frappe.db.commit()
		return do_name
	except Exception:
		frappe.log_error(
			f"Gagal bikin Sales Order/DO Towing dari Towing Request {request.name}",
			"Live Tracking SO/DO Auto-Create Error",
		)
		return None
	finally:
		frappe.set_user(original_user)


def _trip_has_ended(delivery_order_towing: str) -> bool:
	"""An Assigned Towing Request stays 'Assigned' forever once matched — the
	actual trip lifecycle plays out on the DO/Tracking Session instead. This
	checks whether that underlying trip has actually finished, so a completed
	trip doesn't block the customer from starting a new request."""
	session_status = frappe.db.get_value(
		"Tracking Session",
		{"reference_doctype": "Delivery Order Towing", "reference_name": delivery_order_towing},
		"status",
	)
	return session_status in ("Completed", "Cancelled")


@frappe.whitelist()
def get_active_request():
	candidates = frappe.get_all(
		"Towing Request",
		filters={"user": frappe.session.user, "status": ["in", ACTIVE_STATUSES]},
		fields=["name", "status", "delivery_order_towing"],
		order_by="creation desc",
		limit=1,
	)
	if not candidates:
		return None

	request = candidates[0]
	if request.status == "Assigned" and request.delivery_order_towing:
		if _trip_has_ended(request.delivery_order_towing):
			return None

	return frappe.get_doc("Towing Request", request.name).as_dict()


def _cancellation_fee_for(doc) -> float:
	"""Free while still Searching/Offered (no driver committed yet) — a fee
	only kicks in once a driver has been Assigned, so cancelling on them
	after they've already started heading over isn't costless."""
	if doc.status != "Assigned":
		return 0
	fee_percent = flt(frappe.db.get_single_value("Towing Tariff Setting", "cancellation_fee_percent"))
	return flt(flt(doc.price_estimate) * fee_percent / 100, 0)


@frappe.whitelist()
def get_cancellation_quote(name: str):
	doc = frappe.db.get_value(
		"Towing Request", name, ["user", "status", "price_estimate"], as_dict=True
	)
	if not doc:
		frappe.throw(_("Permintaan tidak ditemukan."))
	if doc.user != frappe.session.user:
		frappe.throw(_("Anda tidak berhak melihat permintaan ini."), frappe.PermissionError)

	return {"fee": _cancellation_fee_for(doc)}


@frappe.whitelist()
def cancel_request(name: str):
	doc = frappe.db.get_value(
		"Towing Request",
		name,
		["user", "status", "price_estimate", "delivery_order_towing"],
		as_dict=True,
	)
	if not doc:
		frappe.throw(_("Permintaan tidak ditemukan."))
	if doc.user != frappe.session.user:
		frappe.throw(_("Anda tidak berhak membatalkan permintaan ini."), frappe.PermissionError)
	if doc.status not in ACTIVE_STATUSES:
		frappe.throw(_("Permintaan ini sudah tidak bisa dibatalkan."))

	fee = _cancellation_fee_for(doc)

	if doc.delivery_order_towing:
		# create_towing_request creates the Sales Order + Delivery Order
		# Towing (as Draft) immediately, before any driver has even seen an
		# offer — status flips to "Offered" right away, well before
		# "Assigned". So a linked DO can exist at any active status, not
		# just "Assigned"; each stage needs different cleanup.
		do_status = frappe.db.get_value("Delivery Order Towing", doc.delivery_order_towing, "status")
		current_user = frappe.session.user
		if do_status == "Draft":
			# No workflow "Cancel" transition exists from Draft — but
			# cancelling the Sales Order backing it (submitted the moment
			# the request was made, not only once a driver accepts)
			# already cascades to the DO via imogi_finance's own
			# cancel_do_from_sales_order before_cancel hook.
			sales_order = frappe.db.get_value(
				"Delivery Order Towing", doc.delivery_order_towing, "sales_order"
			)
			frappe.set_user("Administrator")
			try:
				if sales_order and frappe.db.get_value("Sales Order", sales_order, "docstatus") == 1:
					frappe.get_doc("Sales Order", sales_order).cancel()
				else:
					# No submitted SO backing it (shouldn't normally happen)
					# — nothing to cascade from, so force the DO directly.
					frappe.db.set_value(
						"Delivery Order Towing", doc.delivery_order_towing, "status", "Cancelled"
					)
			finally:
				frappe.set_user(current_user)
		elif do_status == "Assigned":
			# The DO's own "Cancel" transition is role-gated to Admin
			# Towing / Sales Manager — the customer's role was never meant
			# to drive this workflow directly (same reason respond_offer
			# elevates for the Assign transition). Cancelling the DO
			# cascades, via the existing sync_tracking_session /
			# sync_towing_request_status doc_events, to also cancel the
			# linked Tracking Session and re-set this Towing Request's
			# status — so the driver's active-trip screen actually clears
			# too, not just the customer's own view of it.
			do_doc = frappe.get_doc("Delivery Order Towing", doc.delivery_order_towing)
			frappe.set_user("Administrator")
			try:
				apply_workflow(do_doc, "Cancel")
			finally:
				frappe.set_user(current_user)
		elif do_status:
			# Pick Up / Delivered / Awaiting Dokument / Done / already
			# Cancelled — the driver has moved past "just assigned".
			# Towing Request.status stays stuck at "Assigned" forever once
			# matched (see _trip_has_ended), so this is the only place
			# that actually knows the trip is already under way.
			frappe.throw(
				_("Trip sudah berjalan (driver sudah menjemput kendaraan), tidak bisa dibatalkan lagi. Hubungi admin jika ada kendala.")
			)

	frappe.db.set_value("Towing Request", name, {"status": "Cancelled", "cancellation_fee": fee})
	frappe.db.commit()
	return frappe.db.get_value(
		"Towing Request",
		name,
		[
			"name",
			"status",
			"pickup_label",
			"dropoff_label",
			"price_estimate",
			"requested_at",
			"delivery_order_towing",
			"cancellation_fee",
		],
		as_dict=True,
	)


@frappe.whitelist()
def get_request_history():
	requests = frappe.get_all(
		"Towing Request",
		filters={"user": frappe.session.user},
		fields=[
			"name",
			"status",
			"pickup_label",
			"dropoff_label",
			"price_estimate",
			"requested_at",
			"delivery_order_towing",
		],
		order_by="creation desc",
		limit_page_length=50,
	)

	# Towing Request.status is stuck at "Assigned" forever once matched (see
	# _trip_has_ended) — the history list would otherwise show every past
	# trip as "Assigned" regardless of whether it actually finished, so
	# resolve the same Tracking Session status the active-request/rating
	# checks already use, per row, for display only (doesn't touch the DB).
	do_names = [r.delivery_order_towing for r in requests if r.status == "Assigned" and r.delivery_order_towing]
	session_statuses = {}
	if do_names:
		sessions = frappe.get_all(
			"Tracking Session",
			filters={"reference_doctype": "Delivery Order Towing", "reference_name": ["in", do_names]},
			fields=["reference_name", "status"],
		)
		session_statuses = {s.reference_name: s.status for s in sessions}

	for r in requests:
		if r.status == "Assigned" and r.delivery_order_towing:
			session_status = session_statuses.get(r.delivery_order_towing)
			if session_status in ("Completed", "Cancelled"):
				r.status = session_status

	return requests


@frappe.whitelist()
def submit_rating(name: str, rating: int, review: str = None):
	doc = frappe.db.get_value(
		"Towing Request", name, ["user", "status", "delivery_order_towing", "rating"], as_dict=True
	)
	if not doc:
		frappe.throw(_("Permintaan tidak ditemukan."))
	if doc.user != frappe.session.user:
		frappe.throw(_("Anda tidak berhak menilai permintaan ini."), frappe.PermissionError)
	# Towing Request.status only flips to "Completed" once admin confirms
	# paperwork on the DO (status "Done") — the customer's own app already
	# shows the trip as finished as soon as the Tracking Session ends
	# (DO reaches "Awaiting Dokument"), so rating eligibility follows that,
	# same check get_active_request()/create_towing_request() already use.
	trip_ended = doc.status == "Completed" or (
		doc.status == "Assigned" and doc.delivery_order_towing and _trip_has_ended(doc.delivery_order_towing)
	)
	if not trip_ended:
		frappe.throw(_("Trip belum selesai."))
	if doc.rating:
		frappe.throw(_("Trip ini sudah dinilai."))

	rating = int(rating)
	if not (1 <= rating <= 5):
		frappe.throw(_("Rating harus antara 1 sampai 5."))

	frappe.db.set_value(
		"Towing Request", name, {"rating": rating, "review": (review or "").strip() or None}
	)
	frappe.db.commit()
	return {"rating": rating, "review": review}


@frappe.whitelist()
def get_trip_status(name: str):
	request = frappe.db.get_value(
		"Towing Request",
		name,
		["name", "user", "status", "delivery_order_towing", "rating"],
		as_dict=True,
	)
	if not request:
		frappe.throw(_("Permintaan tidak ditemukan."))
	if request.user != frappe.session.user:
		frappe.throw(_("Anda tidak berhak melihat permintaan ini."), frappe.PermissionError)

	# The Int field defaults to 0 at the DB level for a never-rated row, not
	# NULL — expose that as None so the app's "already rated?" check (which
	# treats any non-null value as "yes") doesn't mistake 0 for a real rating.
	result = {
		"request_status": request.status,
		"session": None,
		"rating": request.rating or None,
	}
	if not request.delivery_order_towing:
		return result

	session_row = frappe.db.get_value(
		"Tracking Session",
		{"reference_doctype": "Delivery Order Towing", "reference_name": request.delivery_order_towing},
		tracking.SESSION_FIELDS,
		as_dict=True,
	)
	if session_row:
		result["session"] = tracking.build_tracking_payload(session_row)
	return result
