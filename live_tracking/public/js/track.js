(function () {
	"use strict";

	var STEPS = [
		{ key: "Pending", label: "Menunggu Driver" },
		{ key: "Active", label: "Dalam Perjalanan" },
		{ key: "Completed", label: "Selesai" },
	];
	var POLL_MS = 5000;

	var app = document.getElementById("lt-track-app");
	var sheet = document.getElementById("lt-sheet");
	var token = app ? app.getAttribute("data-token") : "";

	if (!token) {
		sheet.innerHTML = '<div class="lt-error">Link tracking tidak valid.</div>';
		return;
	}

	var map = L.map("lt-map", { zoomControl: true, attributionControl: true }).setView([-6.2, 106.8], 12);
	// tile.openstreetmap.org forbids embedding in a distributed page served
	// to many end users ("heavy use") and blocks/blanks out exactly like
	// this once real customers started opening the link. CARTO's basemap
	// turned out to also require an API key now (their policy changed).
	// Esri's World Street Map tiles are free, no signup/key, and
	// explicitly meant for this kind of public app/page embedding —
	// confirmed with a real tile fetch before shipping this, not assumed.
	L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
		maxZoom: 19,
		attribution: "Tiles &copy; Esri",
	}).addTo(map);

	var currentMarker = null;
	var pickupMarker = null;
	var dropoffMarker = null;
	var plannedRouteLine = null;
	var hasFitBounds = false;
	var trail = [];
	var trailSeeded = false;

	function emojiIcon(emoji, size) {
		return L.divIcon({
			className: "",
			html:
				'<div style="font-size:' + Math.round(size * 0.62) + 'px;line-height:' + size + 'px;' +
				"width:" + size + "px;height:" + size + "px;text-align:center;" +
				"background:#fff;border-radius:50%;box-shadow:0 2px 8px rgba(0,0,0,0.35);" +
				'border:2px solid #fff;">' + emoji + "</div>",
			iconSize: [size, size],
			iconAnchor: [size / 2, size / 2],
		});
	}

	function setMarker(ref, latlng, opts) {
		if (!latlng[0] || !latlng[1]) return null;
		if (ref) {
			ref.setLatLng(latlng);
			return ref;
		}
		return L.marker(latlng, opts).addTo(map);
	}

	function timeAgo(dtString) {
		if (!dtString) return "-";
		var then = new Date(dtString.replace(" ", "T"));
		var diff = Math.max(0, (Date.now() - then.getTime()) / 1000);
		if (diff < 60) return "Baru saja";
		if (diff < 3600) return Math.floor(diff / 60) + " menit lalu";
		return Math.floor(diff / 3600) + " jam lalu";
	}

	function renderStepper(status) {
		var idx = STEPS.findIndex(function (s) { return s.key === status; });
		if (idx < 0) idx = 0;
		return (
			'<div class="lt-stepper">' +
			STEPS.map(function (s, i) {
				var cls = i < idx ? "is-done" : i === idx ? "is-current" : "";
				return (
					'<div class="lt-step ' + cls + '">' +
					'<div class="lt-step-dot"></div>' +
					'<span class="lt-step-label">' + s.label + "</span>" +
					"</div>"
				);
			}).join("") +
			"</div>"
		);
	}

	function statusDotClass(status) {
		return {
			Pending: "is-pending",
			Active: "is-active",
			Completed: "is-completed",
			Cancelled: "is-cancelled",
		}[status] || "";
	}

	function render(data) {
		var rows = "";
		if (data.driver_name) {
			rows +=
				'<div class="lt-info-row"><span class="lt-info-label">Driver</span>' +
				'<span class="lt-info-value">' + data.driver_name +
				(data.driver_phone ? ' &middot; <a href="tel:' + data.driver_phone + '">' + data.driver_phone + "</a>" : "") +
				"</span></div>";
		}
		if (data.vehicle && data.vehicle.nomor_polisi) {
			rows +=
				'<div class="lt-info-row"><span class="lt-info-label">Kendaraan</span>' +
				'<span class="lt-info-value">' + [data.vehicle.merk, data.vehicle.tipe, "(" + data.vehicle.nomor_polisi + ")"].filter(Boolean).join(" ") +
				"</span></div>";
		}
		if (data.pickup && data.pickup.label) {
			rows += '<div class="lt-info-row"><span class="lt-info-label">Pick Up</span><span class="lt-info-value">' + data.pickup.label + "</span></div>";
		}
		if (data.dropoff && data.dropoff.label) {
			rows += '<div class="lt-info-row"><span class="lt-info-label">Tujuan</span><span class="lt-info-value">' + data.dropoff.label + "</span></div>";
		}

		var gpsNote = "";
		if (data.status === "Pending" && !data.last_ping_at) {
			gpsNote = '<div class="lt-error" style="margin:0 0 12px;padding:10px;background:#fff7ed;color:#9a3412;border-radius:8px;">Driver belum memulai perjalanan / belum mengirim lokasi.</div>';
		} else if (data.status === "Active" && !data.last_ping_at) {
			gpsNote = '<div class="lt-error" style="margin:0 0 12px;padding:10px;background:#eff6ff;color:#1d4ed8;border-radius:8px;">Menunggu sinyal GPS pertama dari driver...</div>';
		}

		sheet.innerHTML =
			'<div class="lt-status-row"><div class="lt-status-dot ' + statusDotClass(data.status) + '"></div>' +
			'<div class="lt-status-label">' + data.status_label + "</div></div>" +
			renderStepper(data.status) +
			gpsNote +
			rows +
			'<div class="lt-updated">' +
			(data.last_ping_at ? "Posisi terakhir: " + timeAgo(data.last_ping_at) : "Belum ada update posisi") +
			"</div>";

		var bounds = [];
		var pll = null;
		var dll = null;
		if (data.pickup && data.pickup.latitude && data.pickup.longitude) {
			pll = [data.pickup.latitude, data.pickup.longitude];
			pickupMarker = setMarker(pickupMarker, pll, { title: "Pick Up", icon: emojiIcon("🟢", 34) });
			bounds.push(pll);
		}
		if (data.dropoff && data.dropoff.latitude && data.dropoff.longitude) {
			dll = [data.dropoff.latitude, data.dropoff.longitude];
			dropoffMarker = setMarker(dropoffMarker, dll, { title: "Tujuan", icon: emojiIcon("🏁", 34) });
			bounds.push(dll);
		}
		if (pll && dll) {
			var routePoints = data.planned_route && data.planned_route.length ? data.planned_route : [pll, dll];
			if (plannedRouteLine) {
				plannedRouteLine.setLatLngs(routePoints);
			} else {
				plannedRouteLine = L.polyline(routePoints, {
					color: "#16a34a",
					weight: 6,
					opacity: 0.85,
					lineJoin: "round",
					lineCap: "round",
				}).addTo(map);
			}
		}

		if (!trailSeeded) {
			trailSeeded = true;
			if (data.path && data.path.length) {
				trail = data.path.slice();
			}
		}

		if (data.current && data.current.latitude && data.current.longitude) {
			var cll = [data.current.latitude, data.current.longitude];
			currentMarker = setMarker(currentMarker, cll, { title: "Driver", icon: emojiIcon("🚚", 46) });
			bounds.push(cll);

			var last = trail[trail.length - 1];
			if (!last || last[0] !== cll[0] || last[1] !== cll[1]) {
				trail.push(cll);
			}
		}

		var fitTargets = bounds.concat(trail);
		if (fitTargets.length && !hasFitBounds) {
			hasFitBounds = true;
			if (fitTargets.length === 1) {
				map.setView(fitTargets[0], 15);
			} else {
				map.fitBounds(fitTargets, { padding: [40, 40] });
			}
		} else if (currentMarker && bounds.length === 1) {
			map.panTo(bounds[0]);
		}
	}

	var stopped = false;

	function poll() {
		if (stopped) return;
		frappe.call({
			type: "GET",
			method: "live_tracking.api.tracking.get_tracking_info",
			args: { tracking_token: token },
			callback: function (r) {
				if (r.message) {
					render(r.message);
					if (r.message.status === "Completed" || r.message.status === "Cancelled") {
						stopped = true;
						return;
					}
				}
				setTimeout(poll, POLL_MS);
			},
			error: function () {
				stopped = true;
				sheet.innerHTML = '<div class="lt-error">Link tracking tidak ditemukan atau sudah tidak berlaku.</div>';
			},
		});
	}

	poll();
})();
