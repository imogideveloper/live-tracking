"""Email + password login for the customer/driver mobile app.

Replaces the earlier phone + OTP flow entirely (per explicit request,
2026-09-23) — issues a Frappe API key/secret pair on success, same as
before, so the rest of the app (ApiClient, secure-storage token persistence)
didn't need to change. Customers self-register here; driver accounts are
still provisioned by an admin (Driver master data linked to a User via
Driver.custom_user) — there's no self-registration path for drivers, same
constraint as the OTP era.
"""

import frappe
from frappe import _
from frappe.utils.password import check_password

# The "Web application" OAuth client — the Flutter app's google_sign_in is
# configured with this as `serverClientId`, which is what Google puts in the
# ID token's `aud` claim even though the user actually signs in through the
# separate "Android" OAuth client (package name + SHA-1, no secret needed
# here since we only verify a token, never exchange an auth code).
GOOGLE_WEB_CLIENT_ID = "792066251857-l8jqam8h1ng8vacrvq6snjfocfep178r.apps.googleusercontent.com"


@frappe.whitelist(allow_guest=True)
def register(email: str, password: str, full_name: str):
	email = (email or "").strip().lower()
	full_name = (full_name or "").strip()

	if not email or "@" not in email:
		frappe.throw(_("Email tidak valid."))
	if not password or len(password) < 6:
		frappe.throw(_("Password minimal 6 karakter."))
	if not full_name:
		frappe.throw(_("Nama wajib diisi."))
	if frappe.db.exists("User", email):
		frappe.throw(_("Email ini sudah terdaftar. Silakan login."))

	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name,
			"user_type": "Website User",
			"send_welcome_email": 0,
			"new_password": password,
			"roles": [{"role": "Towing Customer"}],
		}
	)
	user.flags.ignore_permissions = True
	user.insert(ignore_permissions=True)

	_ensure_customer_profile(user.name, full_name)
	keys = _issue_api_keys(user.name)
	frappe.db.commit()

	return {
		"user": user.name,
		"role": "customer",
		"api_key": keys["api_key"],
		"api_secret": keys["api_secret"],
		"profile": _customer_profile(user.name),
	}


def _resolve_role_and_profile(email: str, full_name_if_new: str):
	"""No role picker at login — an account IS a driver account (or not),
	decided entirely by whether it's linked from a Driver master record
	(admin-provisioned, via Driver.custom_user). Everyone else is a
	customer, auto-provisioned with a Customer profile on first login."""
	driver_profile = _driver_profile(email)
	if driver_profile:
		return "driver", driver_profile
	_ensure_customer_profile(email, full_name_if_new)
	return "customer", _customer_profile(email)


@frappe.whitelist(allow_guest=True)
def login(email: str, password: str):
	email = (email or "").strip().lower()
	if not email or not frappe.db.exists("User", email):
		frappe.throw(_("Email atau password salah."))

	try:
		check_password(email, password)
	except frappe.AuthenticationError:
		frappe.throw(_("Email atau password salah."))

	role, profile = _resolve_role_and_profile(
		email, frappe.db.get_value("User", email, "full_name") or email
	)
	keys = _issue_api_keys(email)
	frappe.db.commit()

	return {
		"user": email,
		"role": role,
		"api_key": keys["api_key"],
		"api_secret": keys["api_secret"],
		"profile": profile,
	}


@frappe.whitelist(allow_guest=True)
def google_login(id_token: str):
	from google.auth.transport import requests as google_requests
	from google.oauth2 import id_token as google_id_token

	try:
		payload = google_id_token.verify_oauth2_token(
			id_token, google_requests.Request(), audience=GOOGLE_WEB_CLIENT_ID
		)
	except Exception:
		frappe.throw(_("Verifikasi login Google gagal. Coba lagi."))

	email = (payload.get("email") or "").strip().lower()
	full_name = payload.get("name") or email
	if not email:
		frappe.throw(_("Akun Google ini tidak punya email."))
	if not payload.get("email_verified"):
		frappe.throw(_("Email Google ini belum diverifikasi."))

	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": full_name,
				"user_type": "Website User",
				"send_welcome_email": 0,
				"roles": [{"role": "Towing Customer"}],
			}
		)
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)

	role, profile = _resolve_role_and_profile(email, full_name)
	keys = _issue_api_keys(email)
	frappe.db.commit()

	return {
		"user": email,
		"role": role,
		"api_key": keys["api_key"],
		"api_secret": keys["api_secret"],
		"profile": profile,
	}


def _issue_api_keys(user_name: str) -> dict:
	user = frappe.get_doc("User", user_name)
	if not user.api_key:
		user.api_key = frappe.generate_hash(length=15)
	api_secret = frappe.generate_hash(length=15)
	user.api_secret = api_secret
	user.flags.ignore_permissions = True
	user.save(ignore_permissions=True)
	return {"api_key": user.api_key, "api_secret": api_secret}


def _ensure_customer_profile(user_name: str, full_name: str):
	if frappe.db.exists("Customer", {"custom_user": user_name}):
		return
	customer_defaults = frappe.get_all("Customer Group", filters={"is_group": 0}, limit=1, pluck="name")
	territory_defaults = frappe.get_all("Territory", filters={"is_group": 0}, limit=1, pluck="name")
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": full_name,
			"customer_type": "Individual",
			"customer_group": (customer_defaults or ["All Customer Groups"])[0],
			"territory": (territory_defaults or ["All Territories"])[0],
			"custom_user": user_name,
		}
	)
	customer.flags.ignore_permissions = True
	customer.insert(ignore_permissions=True)


def _customer_profile(user_name: str):
	profile = frappe.db.get_value(
		"Customer", {"custom_user": user_name}, ["name", "customer_name", "mobile_no"], as_dict=True
	)
	if profile:
		profile["customer"] = profile.pop("name")
	return profile


def _driver_profile(user_name: str):
	profile = frappe.db.get_value(
		"Driver", {"custom_user": user_name}, ["name", "full_name", "cell_number", "status"], as_dict=True
	)
	if profile:
		profile["driver"] = profile.pop("name")
	return profile
