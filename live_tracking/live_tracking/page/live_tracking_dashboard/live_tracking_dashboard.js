// Chrome/Edge/Safari refuse to run a Web Audio AudioContext until the page
// has received a real user gesture (click/keydown/touch) — since the chime
// fires from a socketio callback with no gesture behind it, a fresh
// `new AudioContext()` in play_new_order_chime() silently stays "suspended"
// and never makes a sound (no error, so nothing shows up in the console
// either). Creating one shared context and resuming it on the admin's first
// interaction with the page is the standard unlock pattern for this.
let _ft_audio_ctx = null;
function _ft_unlock_audio() {
	if (!_ft_audio_ctx) {
		try {
			_ft_audio_ctx = new (window.AudioContext || window.webkitAudioContext)();
		} catch (e) {
			return;
		}
	}
	if (_ft_audio_ctx.state === "suspended") {
		_ft_audio_ctx.resume();
	}
}

frappe.pages["live-tracking-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Fleet Monitor",
		single_column: true,
	});

	inject_styles();

	["click", "keydown", "touchstart"].forEach((evt) =>
		document.addEventListener(evt, _ft_unlock_audio, { once: true })
	);

	const state = { map: null, markers: {}, animFrames: {}, drivers: [], selected: null };

	page.set_primary_action(__("Refresh"), () => load_fleet(state), "refresh");
	page.set_indicator(__("Live"), "green");

	page.main.empty().append(`
		<div class="ft-wrap">
			<div class="ft-map-col">
				<div id="ft-map"></div>
				<div class="ft-map-updated">Memuat...</div>
			</div>
			<div class="ft-side-col">
				<div class="ft-stats">
					<div class="ft-stat ft-stat-on_trip" data-filter="on_trip">
						<div class="ft-stat-icon">${svg_icon("truck", 16)}</div>
						<div class="ft-stat-body">
							<span class="ft-stat-num">0</span>
							<span class="ft-stat-label">Di Jalan</span>
						</div>
					</div>
					<div class="ft-stat ft-stat-idle" data-filter="idle">
						<div class="ft-stat-icon">${svg_icon("clock", 16)}</div>
						<div class="ft-stat-body">
							<span class="ft-stat-num">0</span>
							<span class="ft-stat-label">Idle</span>
						</div>
					</div>
					<div class="ft-stat ft-stat-offline" data-filter="offline">
						<div class="ft-stat-icon">${svg_icon("wifi-off", 16)}</div>
						<div class="ft-stat-body">
							<span class="ft-stat-num">0</span>
							<span class="ft-stat-label">Offline</span>
						</div>
					</div>
				</div>
				<div class="ft-search">
					<span class="ft-search-icon">${svg_icon("search", 15)}</span>
					<input type="text" class="form-control" placeholder="Cari nama / no. HP driver..." />
				</div>
				<div class="ft-list"></div>
			</div>
		</div>
	`);

	if (!document.getElementById("live-tracking-leaflet-css")) {
		const link = document.createElement("link");
		link.id = "live-tracking-leaflet-css";
		link.rel = "stylesheet";
		link.href = "/assets/live_tracking/leaflet/leaflet.css";
		document.head.appendChild(link);
	}

	frappe.require("/assets/live_tracking/leaflet/leaflet.js").then(() => {
		state.map = L.map(page.main.find("#ft-map")[0]).setView([-6.2, 106.816], 11);
		L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
			maxZoom: 19,
			attribution: "&copy; OpenStreetMap contributors",
		}).addTo(state.map);

		load_fleet(state);
		const timer = setInterval(() => load_fleet(state), 8000);
		$(wrapper).on("remove", () => clearInterval(timer));
	});

	page.main.on("input", ".ft-search input", (e) => {
		state.filterText = e.target.value.toLowerCase().trim();
		render_list(state);
	});

	page.main.on("click", ".ft-stat", (e) => {
		const filter = $(e.currentTarget).data("filter");
		state.activeFilter = state.activeFilter === filter ? null : filter;
		page.main.find(".ft-stat").removeClass("is-active");
		if (state.activeFilter) $(e.currentTarget).addClass("is-active");
		render_list(state);
	});

	page.main.on("click", ".ft-row", (e) => {
		const name = $(e.currentTarget).data("driver");
		focus_driver(state, name);
	});

	// Call/WhatsApp buttons sit inside a clickable row — stop the click from
	// also bubbling up and re-triggering focus_driver (harmless, but it'd
	// re-render the list mid-navigation for no reason).
	page.main.on("click", ".ft-icon-btn", (e) => {
		e.stopPropagation();
	});

	// live_tracking.api.customer.create_towing_request publishes this the
	// moment a request auto-converts into a DO — previously nothing on the
	// Desk side reacted to it at all, so an admin watching this dashboard
	// had no way to know a new order had come in without manually refreshing.
	frappe.realtime.on("towing_new_order", (data) => {
		play_new_order_chime();
		frappe.show_alert(
			{
				message: `🚛 Order towing baru dari <b>${frappe.utils.escape_html(data.customer_name || "Customer")}</b><br>${frappe.utils.escape_html(data.pickup_label || "-")}`,
				indicator: "green",
			},
			8
		);
		load_fleet(state);
	});
};

// A synthesized two-note chime via Web Audio API — no binary asset file to
// ship/host, and it's short enough that generating it beats fetching one.
function play_new_order_chime() {
	try {
		_ft_unlock_audio();
		if (!_ft_audio_ctx) return;
		const ctx = _ft_audio_ctx;
		const now = ctx.currentTime;
		[880, 1320].forEach((freq, i) => {
			const osc = ctx.createOscillator();
			const gain = ctx.createGain();
			osc.type = "sine";
			osc.frequency.value = freq;
			const start = now + i * 0.15;
			gain.gain.setValueAtTime(0, start);
			gain.gain.linearRampToValueAtTime(0.3, start + 0.02);
			gain.gain.exponentialRampToValueAtTime(0.001, start + 0.35);
			osc.connect(gain);
			gain.connect(ctx.destination);
			osc.start(start);
			osc.stop(start + 0.4);
		});
	} catch (e) {
		// Web Audio unavailable/blocked by autoplay policy — a missed chime
		// shouldn't break the dashboard, the toast alert still shows.
	}
}

const STALE_AFTER_SECONDS = 180;

function is_stale(last_update) {
	if (!last_update) return false;
	return moment().diff(moment(last_update), "seconds") > STALE_AFTER_SECONDS;
}

function to_whatsapp_number(cell_number) {
	let digits = (cell_number || "").replace(/\D/g, "");
	if (digits.startsWith("0")) digits = "62" + digits.slice(1);
	return digits;
}

// Small Feather/Lucide-style line icon set, inlined as SVG — renders
// identically across every OS/browser (unlike emoji, which look inconsistent
// and a bit toylike next to the rest of a polished Desk page).
const ICON_PATHS = {
	truck:
		'<rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
	clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
	"wifi-off":
		'<line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"/><path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"/><path d="M10.71 5.05A16 16 0 0 1 22.58 9"/><path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>',
	phone:
		'<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>',
	"message-circle":
		'<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
	"map-pin": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
	"alert-triangle":
		'<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
	search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
};

function svg_icon(name, size) {
	size = size || 16;
	return (
		`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
		`stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name] || ""}</svg>`
	);
}

function load_fleet(state) {
	frappe.call({ method: "live_tracking.api.fleet_dashboard.get_fleet_overview" }).then((r) => {
		state.drivers = r.message || [];
		render_stats(state);
		render_list(state);
		render_markers(state);
		const el = document.querySelector(".ft-map-updated");
		if (el) el.textContent = "Update terakhir: " + frappe.datetime.now_time();
	});
}

function render_stats(state) {
	const counts = { on_trip: 0, idle: 0, offline: 0 };
	state.drivers.forEach((d) => counts[d.status]++);
	Object.keys(counts).forEach((key) => {
		$(`.ft-stat-${key} .ft-stat-num`).text(counts[key]);
	});
}

function render_list(state) {
	const container = $(".ft-list").empty();
	const filterText = state.filterText || "";
	const activeFilter = state.activeFilter;

	const rows = state.drivers.filter((d) => {
		if (activeFilter && d.status !== activeFilter) return false;
		if (!filterText) return true;
		return (
			(d.full_name || "").toLowerCase().includes(filterText) ||
			(d.cell_number || "").toLowerCase().includes(filterText)
		);
	});

	if (!rows.length) {
		container.append(`<div class="ft-empty">Tidak ada driver yang cocok.</div>`);
		return;
	}

	// Sort: on the road first (most actionable), then idle, then offline.
	const order = { on_trip: 0, idle: 1, offline: 2 };
	rows.sort((a, b) => order[a.status] - order[b.status] || (a.full_name || "").localeCompare(b.full_name || ""));

	rows.forEach((d) => container.append(driver_row_html(d, state.selected === d.name)));
}

function driver_row_html(d, isSelected) {
	const initial = (d.full_name || "?").trim().charAt(0).toUpperCase();
	const badge = {
		on_trip: { label: "Di Jalan", cls: "on_trip" },
		idle: { label: "Idle", cls: "idle" },
		offline: { label: "Offline", cls: "offline" },
	}[d.status];

	let subtitle;
	if (d.status === "on_trip" && d.trip) {
		const speed = d.speed_kmh ? Math.round(d.speed_kmh) + " km/j" : "";
		const departed = d.trip.started_at ? "Berangkat " + frappe.datetime.comment_when(d.trip.started_at) : "";
		subtitle = `→ ${frappe.utils.escape_html(d.trip.lokasi_tujuan || "-")}${speed ? " · " + speed : ""}`;
		if (departed) subtitle += `<br>${departed}`;
	} else if (d.status === "idle") {
		subtitle = "Online · menunggu order";
	} else {
		subtitle = d.last_update ? "Terakhir aktif " + frappe.datetime.comment_when(d.last_update) : "Belum pernah online";
	}

	const addressLine =
		d.address && d.status !== "offline"
			? `<div class="ft-address" title="${frappe.utils.escape_html(d.address)}">` +
			  `<span class="ft-icon-inline">${svg_icon("map-pin", 11)}</span>${frappe.utils.escape_html(d.address)}</div>`
			: "";

	const staleLine =
		d.status !== "offline" && is_stale(d.last_update)
			? `<div class="ft-stale"><span class="ft-icon-inline">${svg_icon("alert-triangle", 11)}</span>` +
			  `GPS belum update ${frappe.datetime.comment_when(d.last_update)} — cek koneksi driver</div>`
			: "";

	const canContact = d.status !== "offline" && d.cell_number;
	const actions = canContact
		? `
			<div class="ft-icons">
				<a class="ft-icon-btn ft-icon-btn-call" href="tel:${d.cell_number}" title="Telepon ${frappe.utils.escape_html(d.full_name || "")}">${svg_icon("phone", 13)}</a>
				<a class="ft-icon-btn ft-icon-btn-wa" href="https://wa.me/${to_whatsapp_number(d.cell_number)}" target="_blank" rel="noopener" title="WhatsApp ${frappe.utils.escape_html(d.full_name || "")}">${svg_icon("message-circle", 13)}</a>
			</div>
		`
		: "";

	return `
		<div class="ft-row ${isSelected ? "is-selected" : ""}" data-driver="${d.name}">
			<div class="ft-avatar ft-avatar-${badge.cls}">${initial}</div>
			<div class="ft-info">
				<div class="ft-name">${frappe.utils.escape_html(d.full_name || d.name)}</div>
				<div class="ft-sub">${subtitle}</div>
				${addressLine}
				${staleLine}
			</div>
			<div class="ft-right">
				<div class="ft-badge ft-badge-${badge.cls}">${badge.label}</div>
				${actions}
			</div>
		</div>
	`;
}

function render_markers(state) {
	if (!state.map) return;

	const seen = new Set();
	state.drivers.forEach((d) => {
		// Offline drivers clutter the map with stale pins — they're still in
		// the side list, just not plotted.
		if (d.status === "offline" || !d.latitude || !d.longitude) return;
		seen.add(d.name);
		update_marker(state, d);
	});

	Object.keys(state.markers).forEach((name) => {
		if (!seen.has(name)) {
			state.map.removeLayer(state.markers[name]);
			delete state.markers[name];
		}
	});
}

function update_marker(state, d) {
	const isOnTrip = d.status === "on_trip";
	const icon = marker_icon(isOnTrip, d.heading);
	let marker = state.markers[d.name];

	if (!marker) {
		marker = L.marker([d.latitude, d.longitude], { icon }).addTo(state.map);
		marker.on("click", () => focus_driver(state, d.name));
		state.markers[d.name] = marker;
	} else {
		marker.setIcon(icon);
		animate_marker_to(state, d.name, marker, d.latitude, d.longitude);
	}

	const speed = d.speed_kmh ? Math.round(d.speed_kmh) + " km/j" : "-";
	marker.bindPopup(
		`<b>${frappe.utils.escape_html(d.full_name || d.name)}</b><br>` +
			(isOnTrip
				? `→ ${frappe.utils.escape_html((d.trip && d.trip.lokasi_tujuan) || "-")}<br>Kecepatan: ${speed}<br>`
				: `Status: Idle (online, menunggu order)<br>`) +
			`<span class="ft-icon-inline">${svg_icon("map-pin", 11)}</span>${frappe.utils.escape_html(d.address || "Alamat tidak diketahui")}<br>` +
			`Update: ${d.last_update ? frappe.datetime.comment_when(d.last_update) : "-"}`,
		// Leaflet's default popup close button is a plain `<a href="#close">`
		// — Frappe's desk router treats any `#...` click as an internal page
		// route and throws "Page #close not found". Popups already close on
		// click-elsewhere (autoClose/closeOnClick default to true), so the
		// button isn't needed.
		{ closeButton: false }
	);
}

function marker_icon(isOnTrip, heading) {
	if (isOnTrip) {
		const rotation = typeof heading === "number" ? heading : 0;
		return L.divIcon({
			className: "",
			html:
				'<div class="ft-marker-pulse ft-marker-pulse-trip"></div>' +
				'<div class="ft-marker ft-marker-trip">' +
				`<span style="display:inline-block;transform:rotate(${rotation}deg);">${svg_icon("truck", 16)}</span>` +
				"</div>",
			iconSize: [34, 34],
			iconAnchor: [17, 17],
		});
	}
	return L.divIcon({
		className: "",
		html: '<div class="ft-marker-pulse ft-marker-pulse-idle"></div><div class="ft-marker ft-marker-idle"></div>',
		iconSize: [18, 18],
		iconAnchor: [9, 9],
	});
}

// Same glide-instead-of-teleport treatment as the mobile app's tracking map.
function animate_marker_to(state, key, marker, lat, lng) {
	if (state.animFrames[key]) cancelAnimationFrame(state.animFrames[key]);
	const from = marker.getLatLng();
	if (from.lat === lat && from.lng === lng) return;

	const duration = 1200;
	let startTime = null;
	function step(timestamp) {
		if (!startTime) startTime = timestamp;
		const t = Math.min((timestamp - startTime) / duration, 1);
		const eased = 1 - Math.pow(1 - t, 2);
		marker.setLatLng([from.lat + (lat - from.lat) * eased, from.lng + (lng - from.lng) * eased]);
		if (t < 1) {
			state.animFrames[key] = requestAnimationFrame(step);
		} else {
			state.animFrames[key] = null;
		}
	}
	state.animFrames[key] = requestAnimationFrame(step);
}

function focus_driver(state, name) {
	state.selected = name;
	render_list(state);
	const marker = state.markers[name];
	if (marker && state.map) {
		state.map.setView(marker.getLatLng(), Math.max(state.map.getZoom(), 15));
		marker.openPopup();
	}
}

function inject_styles() {
	if (document.getElementById("ft-dashboard-styles")) return;
	const style = document.createElement("style");
	style.id = "ft-dashboard-styles";
	style.textContent = `
		.ft-wrap { display: flex; gap: 16px; height: calc(100vh - 180px); min-height: 480px; }
		.ft-map-col { flex: 1 1 65%; min-width: 0; position: relative; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.12); }
		#ft-map { height: 100%; }
		.ft-map-updated { position: absolute; bottom: 10px; left: 10px; z-index: 500; background: rgba(255,255,255,0.92); padding: 4px 10px; border-radius: 6px; font-size: 12px; color: #555; box-shadow: 0 1px 3px rgba(0,0,0,0.15); }

		.ft-side-col { flex: 0 0 340px; min-width: 0; display: flex; flex-direction: column; min-height: 0; }

		.ft-stats { display: flex; gap: 8px; margin-bottom: 12px; }
		.ft-stat { flex: 1; display: flex; align-items: center; gap: 8px; background: #fff; border-radius: 10px; padding: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); cursor: pointer; border: 2px solid transparent; transition: transform 0.1s ease, border-color 0.15s ease; min-width: 0; }
		.ft-stat:hover { transform: translateY(-1px); }
		.ft-stat.is-active { border-color: #0F766E; }
		.ft-stat-icon { width: 30px; height: 30px; flex-shrink: 0; border-radius: 8px; display: flex; align-items: center; justify-content: center; }
		.ft-stat-on_trip .ft-stat-icon { background: #CCFBF1; color: #0F766E; }
		.ft-stat-idle .ft-stat-icon { background: #FEF3C7; color: #92400E; }
		.ft-stat-offline .ft-stat-icon { background: #F3F4F6; color: #6B7280; }
		.ft-stat-body { min-width: 0; display: flex; flex-direction: column; }
		.ft-stat-num { font-size: 20px; font-weight: 700; color: #111827; line-height: 1.2; }
		.ft-stat-label { font-size: 11px; color: #6B7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

		.ft-search { position: relative; margin-bottom: 10px; }
		.ft-search input { border-radius: 8px; padding-left: 32px; }
		.ft-search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: #9CA3AF; pointer-events: none; display: flex; }

		.ft-list { flex: 1; min-width: 0; overflow-y: auto; background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 6px; }
		.ft-empty { padding: 24px 12px; text-align: center; color: #9CA3AF; font-size: 13px; }

		.ft-row { display: flex; align-items: center; gap: 10px; padding: 9px 8px; border-radius: 8px; cursor: pointer; transition: background 0.12s ease; min-width: 0; }
		.ft-row:hover { background: #F3F4F6; }
		.ft-row.is-selected { background: #F0FDFA; }
		.ft-row + .ft-row { margin-top: 2px; }

		.ft-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; font-size: 14px; flex-shrink: 0; }
		.ft-avatar-on_trip { background: #0F766E; }
		.ft-avatar-idle { background: #D97706; }
		.ft-avatar-offline { background: #9CA3AF; }

		.ft-info { flex: 1 1 auto; min-width: 0; overflow: hidden; }
		.ft-name { font-size: 13.5px; font-weight: 600; color: #111827; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
		.ft-sub { font-size: 12px; color: #6B7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
		.ft-address { font-size: 11px; color: #9CA3AF; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
		.ft-stale { font-size: 11px; color: #B45309; margin-top: 3px; line-height: 1.35; white-space: normal; }
		.ft-icon-inline { display: inline-flex; vertical-align: -2px; margin-right: 3px; opacity: 0.85; }

		.ft-right { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0; }

		.ft-badge { font-size: 10.5px; font-weight: 600; padding: 3px 8px; border-radius: 999px; flex-shrink: 0; }
		.ft-badge-on_trip { background: #CCFBF1; color: #0F766E; }
		.ft-badge-idle { background: #FEF3C7; color: #92400E; }
		.ft-badge-offline { background: #F3F4F6; color: #6B7280; }

		.ft-icons { display: flex; gap: 4px; }
		.ft-icon-btn { width: 25px; height: 25px; display: flex; align-items: center; justify-content: center; border-radius: 50%; background: #F3F4F6; text-decoration: none; transition: background 0.12s ease, transform 0.1s ease; }
		.ft-icon-btn:hover { transform: scale(1.1); text-decoration: none; }
		.ft-icon-btn-call { color: #0F766E; }
		.ft-icon-btn-call:hover { background: #CCFBF1; color: #0F766E; }
		.ft-icon-btn-wa { color: #25D366; }
		.ft-icon-btn-wa:hover { background: #DCFCE7; color: #25D366; }

		.ft-marker-trip { width: 34px; height: 34px; display: flex; align-items: center; justify-content: center; background: #0F766E; color: #fff; border-radius: 50%; border: 2px solid #fff; box-shadow: 0 2px 5px rgba(0,0,0,0.4); position: relative; }
		.ft-marker-idle { width: 18px; height: 18px; border-radius: 50%; background: #D97706; border: 2px solid #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.4); position: relative; }

		.ft-marker-pulse { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); border-radius: 50%; pointer-events: none; animation: ft-pulse 2s ease-out infinite; }
		.ft-marker-pulse-trip { width: 34px; height: 34px; background: rgba(15, 118, 110, 0.35); }
		.ft-marker-pulse-idle { width: 18px; height: 18px; background: rgba(217, 119, 6, 0.35); }

		@keyframes ft-pulse {
			0% { transform: translate(-50%, -50%) scale(1); opacity: 0.7; }
			100% { transform: translate(-50%, -50%) scale(2.2); opacity: 0; }
		}

		@media (max-width: 900px) {
			.ft-wrap { flex-direction: column; height: auto; }
			.ft-map-col { height: 360px; }
			.ft-side-col { flex: none; }
		}
	`;
	document.head.appendChild(style);
}
