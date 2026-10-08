"""Push notifications to the mobile app via Expo's push HTTP API.

Free, no API key needed (Expo signs pushes to FCM/APNs on our behalf) — a
reasonable default for the MVP, same "free public service" posture as the
Nominatim/OSRM calls already used elsewhere in this app.
"""

import frappe

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def send_push(user: str, title: str, body: str, data: dict | None = None):
	if not user:
		return

	tokens = frappe.get_all(
		"Towing Device Token",
		filters={"user": user, "is_active": 1},
		pluck="token",
	)
	if not tokens:
		return

	import requests

	messages = [
		{"to": token, "title": title, "body": body, "data": data or {}, "sound": "default"}
		for token in tokens
	]
	try:
		requests.post(EXPO_PUSH_URL, json=messages, timeout=8)
	except Exception:
		frappe.log_error(
			f"Gagal mengirim push notification ke user {user}", "Live Tracking Push Error"
		)
