# Copyright (c) 2026, Imogi and contributors
# For license information, please see license.txt

import json
import time

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_url, now_datetime

COORDINATE_FIELDS = {
	"pickup_latitude": (-90, 90),
	"dropoff_latitude": (-90, 90),
	"pickup_longitude": (-180, 180),
	"dropoff_longitude": (-180, 180),
}

# Public OSRM demo server: free, no API key, good enough for light use.
# Not meant for heavy production traffic — self-host OSRM if volume grows.
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"

# Public Nominatim demo server: free, no API key. Usage policy caps this at
# ~1 request/sec and requires a real User-Agent — fine for our low volume.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "live_tracking-frappe-app/1.0"}

GEOCODE_PAIRS = [
	("pickup_label", "pickup_latitude", "pickup_longitude"),
	("dropoff_label", "dropoff_latitude", "dropoff_longitude"),
]

# Fixed coordinates for our own depots and the city areas we actually
# deliver to, sourced from the company's own route sheet (RUTE_TOWING_REVISI,
# 2026-10) rather than Nominatim. Two reasons this exists instead of just
# letting _geocode() handle everything:
#   1. lokasi_pickup is almost always an internal depot codename ("PUNINAR",
#      "HANDAL", "RDC Cilincing") — not a real address, so a public geocoder
#      has nothing to match and silently returns no coordinates, leaving the
#      customer's tracking map without a pickup pin.
#   2. lokasi_tujuan is usually a destination CITY, not a specific dealer —
#      the dealer isn't captured as structured data at all, so the best
#      available anchor is the route sheet's average position across that
#      city's dealers (noted per-entry below) rather than a guess.
# Anything NOT in this table (Bandung, Bogor, Serang, Tangerang Selatan,
# GUDANG BEKASI, POOL, WANAHERANG, ...) still falls through to _geocode() —
# those are either real city names Nominatim resolves fine, or locations we
# haven't mapped yet.
KNOWN_LOCATIONS = {
	# Depots — exact.
	"RDC CILINCING": (-6.128718, 106.931055),
	"KARAWANG": (-6.355241, 107.262412),
	"PUNINAR": (-6.129357, 106.941505),
	"HANDAL": (-6.129358, 106.941506),
	# Destination cities — averaged across that city's dealers on the route
	# sheet (see each comment for the dealer count behind the average).
	"TANGERANG": (-6.220462, 106.647014),  # 7 dealers
	"CIBINONG": (-6.477837, 106.868559),  # 1 dealer (exact)
	"CIBUBUR": (-6.386430, 106.938369),  # 1 dealer (exact)
	"BEKASI": (-6.292085, 106.993386),  # 6 dealers
	"JAKARTA": (-6.270746, 106.775568),  # 1 dealer (exact)
	"DKI JAKARTA": (-6.270746, 106.775568),
	"JAKARTA SELATAN": (-6.243953, 106.802838),  # 5 dealers
	"JAKARTA BARAT": (-6.197299, 106.760216),  # 6 dealers
	"DEPOK": (-6.352555, 106.796864),  # 3 dealers
}


class TrackingSession(Document):
	def before_insert(self):
		if not self.tracking_token:
			self.tracking_token = frappe.generate_hash(length=32)
		if not self.driver_token:
			self.driver_token = frappe.generate_hash(length=32)
		if not self.status:
			self.status = "Pending"

	def validate(self):
		self.auto_geocode_labels()
		self.validate_coordinates()

	def auto_geocode_labels(self):
		before = self.get_doc_before_save()

		for label_field, lat_field, lng_field in GEOCODE_PAIRS:
			label = (self.get(label_field) or "").strip()
			if not label:
				continue

			label_changed = bool(before) and (before.get(label_field) or "").strip() != label
			if label_changed:
				self.set(lat_field, None)
				self.set(lng_field, None)

			if self.get(lat_field) or self.get(lng_field):
				continue

			coords = KNOWN_LOCATIONS.get(label.upper()) or self._geocode(label)
			if coords:
				self.set(lat_field, f"{coords[0]:.6f}")
				self.set(lng_field, f"{coords[1]:.6f}")

	def _geocode(self, query: str):
		import requests

		try:
			resp = requests.get(
				NOMINATIM_URL,
				params={"q": query, "format": "json", "limit": 1, "countrycodes": "id"},
				headers=NOMINATIM_HEADERS,
				timeout=8,
			)
			results = resp.json()
			if not results:
				return None
			return float(results[0]["lat"]), float(results[0]["lon"])
		except Exception:
			frappe.log_error(
				f"Gagal geocode '{query}' untuk Tracking Session {self.name or '(baru)'}",
				"Live Tracking Geocode Error",
			)
			return None
		finally:
			# Nominatim's public usage policy caps requests at ~1/sec.
			time.sleep(1)

	def validate_coordinates(self):
		route_inputs_changed = False
		for fieldname, (low, high) in COORDINATE_FIELDS.items():
			value = (self.get(fieldname) or "").strip()
			if not value:
				continue
			try:
				number = float(value)
			except ValueError:
				frappe.throw(
					_("{0} tidak valid: {1}. Gunakan titik (.) sebagai desimal, contoh: -6.182245").format(
						self.meta.get_label(fieldname), value
					)
				)
			if not (low <= number <= high):
				frappe.throw(
					_("{0} harus di antara {1} dan {2}, dapat: {3}").format(
						self.meta.get_label(fieldname), low, high, value
					)
				)
			normalized = f"{number:.6f}"
			if self.get(fieldname) != normalized:
				route_inputs_changed = True
			self.set(fieldname, normalized)

		if route_inputs_changed:
			self.route_polyline = None

	def get_road_route(self):
		"""Return a cached (or freshly fetched) road-following route as a list of [lat, lng].

		Empty list means either the coordinates aren't set yet, or the last
		fetch attempt failed/found no route — cached either way so we don't
		hammer the routing API on every customer poll.
		"""
		if self.route_polyline is not None:
			try:
				return json.loads(self.route_polyline)
			except ValueError:
				pass

		route = self._fetch_road_route()
		frappe.db.set_value(
			"Tracking Session", self.name, "route_polyline", json.dumps(route), update_modified=False
		)
		return route

	def _fetch_road_route(self):
		try:
			p_lat, p_lng = float(self.pickup_latitude), float(self.pickup_longitude)
			d_lat, d_lng = float(self.dropoff_latitude), float(self.dropoff_longitude)
		except (TypeError, ValueError):
			return []
		if not (p_lat and p_lng and d_lat and d_lng):
			return []

		import requests

		url = OSRM_URL.format(lon1=p_lng, lat1=p_lat, lon2=d_lng, lat2=d_lat)
		try:
			resp = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=8)
			data = resp.json()
			coords = data["routes"][0]["geometry"]["coordinates"]
			return [[lat, lng] for lng, lat in coords]
		except Exception:
			frappe.log_error(
				f"Gagal ambil rute OSRM untuk Tracking Session {self.name}", "Live Tracking OSRM Error"
			)
			return []

	def get_tracking_url(self):
		return get_url(f"/track?token={self.tracking_token}")

	def get_driver_url(self):
		return get_url(f"/driver?token={self.driver_token}")

	def mark_active(self):
		if self.status != "Active":
			self.status = "Active"
		if not self.started_at:
			self.started_at = now_datetime()
		self.save(ignore_permissions=True)

	def mark_completed(self):
		self.status = "Completed"
		if not self.ended_at:
			self.ended_at = now_datetime()
		self.save(ignore_permissions=True)

	def mark_cancelled(self):
		self.status = "Cancelled"
		if not self.ended_at:
			self.ended_at = now_datetime()
		self.save(ignore_permissions=True)
