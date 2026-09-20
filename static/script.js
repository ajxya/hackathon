// WayPoint dashboard frontend — fetches /state and fills in the page.

const POLL_INTERVAL_MS = 2000;
let fetchInProgress = false;
let surgeInterval = null;

// Full meaning behind each T1-T5 abbreviation, used as a tooltip
// wherever the short badge form is shown (the Patients table).
const TIER_MEANINGS = {
  1: "Tier 1 — immediately life-threatening (immediate target)",
  2: "Tier 2 — emergent (10 min target)",
  3: "Tier 3 — urgent (30 min target)",
  4: "Tier 4 — less urgent (60 min target)",
  5: "Tier 5 — non-urgent (120 min target)",
};

// Which facility capabilities are relevant for a given breaching tier —
// a simple, rules-based heuristic for this demo, not a real clinical
// routing rule. Severe tiers (1-2) want acute-care capabilities; low
// tiers (4-5) are fine at urgent care.
const TIER_RELEVANT_CAPABILITIES = {
  1: ["trauma", "cardiac", "stroke"],
  2: ["trauma", "cardiac", "stroke"],
  3: ["trauma", "cardiac", "stroke", "urgent_care"],
  4: ["urgent_care", "pediatric"],
  5: ["urgent_care", "pediatric"],
};

// Kept in sync with the CSS status tokens in static/style.css
// (--color-success/--color-warning/--color-danger and --color-accent) —
// canvas and Leaflet markers can't consume CSS variables directly.
const STATUS_COLORS = { green: "#15803d", yellow: "#b45309", red: "#dc2626" };
const ACCENT_COLOR = "#dc2626";

let networkMap = null;
let networkMarkers = {};

let homeCoords = null;
let latestHospitalsData = null;
let latestBreachSummary = null;
let latestStatusLevel = "green";

// Impact tab trend charts — accumulated client-side from the same polling
// that already drives everything else, capped so memory doesn't grow
// unbounded during a long session.
const MAX_TREND_POINTS = 150; // 5 minutes of history at the 2s poll interval
let waitHistory = [];
let breachHistory = [];

function setText(id, value) {
  const target = document.getElementById(id);
  if (!target) return;
  target.textContent = String(value);
}

function barColorClass(pct) {
  if (pct >= 90) return "bar-red";
  if (pct >= 70) return "bar-yellow";
  return "";
}

function setBar(barId, pctId, pct) {
  const bar = document.getElementById(barId);
  bar.style.width = `${pct}%`;
  bar.className = "bar-fill " + barColorClass(pct);
  setText(pctId, `${pct}%`);
}

const RUNG_LABELS = {
  0: null,
  1: "Rung 1 · ED's Own Resources",
  2: "Rung 2 · Hospital Float Pool",
  3: "Rung 3 · Overflow Capacity",
  4: "Rung 4 · Relocation",
};

function renderRecommendations(recommendations) {
  const container = document.getElementById("recommendations-list");
  container.innerHTML = "";

  recommendations.forEach((rec) => {
    const item = document.createElement("div");
    item.className = "recommendation-item";

    const rungLabel = RUNG_LABELS[rec.rung];
    if (rungLabel) {
      const rung = document.createElement("div");
      rung.className = "recommendation-rung";
      rung.textContent = rungLabel;
      item.appendChild(rung);
    }

    const action = document.createElement("div");
    action.className = "recommendation-action";
    action.textContent = rec.action;

    const reason = document.createElement("div");
    reason.className = "recommendation-reason";
    reason.textContent = rec.reason;

    // Rungs 2-4 always have a real action attached (an Apply/Approve
    // button below) — "No action needed" is only correct for rung 0/1,
    // which never show a button. A proactive rung 2/3 recommendation has
    // no estimated_wait_reduction_minutes yet (nobody's actually waiting
    // longer because of it — that's the point of acting early), so it
    // needs its own phrasing rather than falling through to "No action
    // needed," which would read as if there were nothing to do.
    const hasAction = rec.rung === 2 || rec.rung === 3 || rec.rung === 4;
    const isProactive = rec.projected_breach_minutes !== null && rec.projected_breach_minutes !== undefined;
    const savings = document.createElement("div");
    savings.className = "recommendation-savings";
    if (rec.estimated_wait_reduction_minutes > 0) {
      savings.textContent = `Est. wait reduction: ~${rec.estimated_wait_reduction_minutes} min`;
    } else if (hasAction && isProactive) {
      savings.textContent = "Apply now to help prevent the projected breach";
    } else if (hasAction) {
      savings.textContent = "Apply to act on this recommendation";
    } else {
      savings.textContent = "No action needed";
    }

    item.appendChild(action);
    item.appendChild(reason);

    // Proactive recommendations (no Tier 1-2 breach yet, but one is
    // projected from the current bed-fill rate) surface that projection
    // as its own line, separate from the reason text.
    if (rec.projected_breach_minutes !== null && rec.projected_breach_minutes !== undefined) {
      const projection = document.createElement("div");
      projection.className = "recommendation-projection";
      projection.textContent =
        rec.projected_breach_minutes <= 0.5
          ? "Tier 1-2 breach projected imminently"
          : `Tier 1-2 breach projected in ~${rec.projected_breach_minutes} min`;
      item.appendChild(projection);
    }

    item.appendChild(savings);

    // Rungs 2 and 3 apply directly; rung 4 (relocation) has its own
    // approval endpoint, since it moves real patients rather than pulling
    // from a reserve.
    if (rec.rung === 2 || rec.rung === 3) {
      item.appendChild(makeApplyButton("Apply", () => applyRecommendation(rec.rung)));
    } else if (rec.rung === 4) {
      item.appendChild(makeApplyButton("Approve Relocation", () => applyRelocation()));
    }

    container.appendChild(item);
  });
}

function makeApplyButton(label, onClick) {
  const button = document.createElement("button");
  button.className = "apply-button";
  button.textContent = label;
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Applying…";
    try {
      await onClick();
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  });
  return button;
}

async function applyRecommendation(rung) {
  await postAndRefresh(`/recommendations/apply/${rung}`);
}

async function applyRelocation() {
  await postAndRefresh("/relocation/apply");
}

async function applyAllRecommendations() {
  await postAndRefresh("/recommendations/apply-all");
}

// Patients table (Step 4): sortable/filterable client-side over the same
// patients_list payload every poll already delivers — no new endpoint or
// data, just a richer view over it.
let latestPatientsData = [];
let patientsSortKey = "wait_minutes";
let patientsSortDir = "desc";
let patientsTierFilterValue = "all";

function renderPatientsList(patients) {
  latestPatientsData = patients;
  setText("patients-count", patients.length);
  renderPatientsTable();
}

function renderPatientsTable() {
  const tbody = document.getElementById("patients-list");
  if (!tbody) return;
  tbody.innerHTML = "";

  let rows = latestPatientsData;
  if (patientsTierFilterValue !== "all") {
    rows = rows.filter((p) => String(p.tier) === patientsTierFilterValue);
  }

  rows = [...rows].sort((a, b) => {
    let av = a[patientsSortKey];
    let bv = b[patientsSortKey];
    if (typeof av === "string") av = av.toLowerCase();
    if (typeof bv === "string") bv = bv.toLowerCase();
    if (av < bv) return patientsSortDir === "asc" ? -1 : 1;
    if (av > bv) return patientsSortDir === "asc" ? 1 : -1;
    return 0;
  });

  if (rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "muted";
    td.textContent =
      latestPatientsData.length === 0 ? "No patients yet this session." : "No patients match this tier filter.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  rows.forEach((p) => {
    const tr = document.createElement("tr");
    if (p.overdue) tr.className = "overdue";

    const nameTd = document.createElement("td");
    nameTd.textContent = p.name;

    const tierTd = document.createElement("td");
    const tierBadge = document.createElement("span");
    tierBadge.className = `tier-badge tier-${p.tier}`;
    tierBadge.textContent = `T${p.tier}`;
    tierBadge.title = TIER_MEANINGS[p.tier] || "";
    tierTd.appendChild(tierBadge);

    const injuryTd = document.createElement("td");
    injuryTd.textContent = p.injury;

    const sourceTd = document.createElement("td");
    sourceTd.textContent = p.source === "ambulance" ? "Ambulance" : "Walk-in";

    const statusTd = document.createElement("td");
    statusTd.textContent = p.status_label;

    const waitTd = document.createElement("td");
    waitTd.className = "mono-num" + (p.overdue ? " overdue-text" : "");
    waitTd.textContent = `${p.wait_minutes} min`;

    tr.append(nameTd, tierTd, injuryTd, sourceTd, statusTd, waitTd);
    tbody.appendChild(tr);
  });
}

function renderBreachSummary(prefix, breachSummary) {
  // Headline metric — shared by the home page and the hospital detail
  // modal (Step 6), which reuses this exact function with prefix "modal-".
  const headline = document.getElementById(`${prefix}headline-metric`);
  if (headline) {
    const count = breachSummary.tier1_2_breaches;
    setText(`${prefix}headline-breach-count`, count);
    headline.className = "headline-metric " + (count > 0 ? "headline-breach" : "headline-ok");
  }

  // Per-tier table
  const tbody = document.getElementById(`${prefix}breach-table-body`);
  if (!tbody) return;
  tbody.innerHTML = "";

  for (let tier = 1; tier <= 5; tier++) {
    const row = breachSummary.by_tier[tier];
    const tr = document.createElement("tr");

    const tierCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `tier-badge tier-${tier}`;
    badge.textContent = `Tier ${tier}`;
    tierCell.appendChild(badge);

    const waitingCell = document.createElement("td");
    waitingCell.textContent = row.waiting;

    const breachedCell = document.createElement("td");
    breachedCell.textContent = row.breached;
    if (row.breached > 0) breachedCell.className = "breach-count-nonzero";

    const avgWaitCell = document.createElement("td");
    avgWaitCell.textContent = row.waiting > 0 ? `${row.avg_wait_minutes} min` : "–";

    tr.appendChild(tierCell);
    tr.appendChild(waitingCell);
    tr.appendChild(breachedCell);
    tr.appendChild(avgWaitCell);
    tbody.appendChild(tr);
  }
}

function worstBreachingTier(breachSummary) {
  // Most severe tier that's actually breached, if any...
  for (let tier = 1; tier <= 5; tier++) {
    if (breachSummary.by_tier[tier].breached > 0) return tier;
  }
  // ...otherwise (status is yellow/red from capacity strain, not a breach
  // yet) fall back to whichever tier currently has the highest wait.
  let worstTier = 3;
  let worstWait = -1;
  for (let tier = 1; tier <= 5; tier++) {
    const wait = breachSummary.by_tier[tier].avg_wait_minutes;
    if (wait > worstWait) {
      worstWait = wait;
      worstTier = tier;
    }
  }
  return worstTier;
}

function facilityWaitForTier(facility, tier) {
  return facility.type === "hospital" ? facility.estimated_wait_by_tier[String(tier)] : facility.avg_wait_minutes;
}

function rankFacilities(facilities, tier) {
  const relevant = TIER_RELEVANT_CAPABILITIES[tier] || [];
  return facilities
    .filter((f) => f.status !== "red") // never recommend a red facility
    .map((f) => ({
      facility: f,
      matches: f.capabilities.some((c) => relevant.includes(c)),
      waitForTier: facilityWaitForTier(f, tier),
    }))
    .sort((a, b) => {
      if (a.matches !== b.matches) return a.matches ? -1 : 1;
      return a.waitForTier - b.waitForTier;
    });
}

function directionsUrl(facility) {
  return (
    `https://www.google.com/maps/dir/?api=1&origin=${homeCoords.lat},${homeCoords.lng}` +
    `&destination=${facility.lat},${facility.lng}`
  );
}

function renderFacilityCard(ranked, tier, isRecommended) {
  const f = ranked.facility;
  const card = document.createElement("div");
  card.className = "facility-card clickable" + (isRecommended ? " recommended" : "");
  card.addEventListener("click", () => openHospitalModal(f));

  const header = document.createElement("div");
  header.className = "facility-card-header";

  const dot = document.createElement("span");
  dot.className = `status-dot-small status-${f.status}`;

  const name = document.createElement("strong");
  name.textContent = f.name;

  header.appendChild(dot);
  header.appendChild(name);

  if (isRecommended) {
    const badge = document.createElement("span");
    badge.className = "recommended-badge";
    badge.textContent = "Recommended";
    header.appendChild(badge);
  }

  const detail = document.createElement("div");
  detail.className = "facility-card-detail";
  const waitLabel =
    f.type === "hospital" ? `Est. wait (Tier ${tier}): ${ranked.waitForTier} min` : `Avg wait: ${ranked.waitForTier} min`;
  detail.textContent = `${waitLabel} · ${f.travel_time_minutes} min away (${f.distance_miles} mi)`;

  const directionsButton = document.createElement("button");
  directionsButton.className = "directions-button";
  directionsButton.textContent = "Get directions";
  directionsButton.addEventListener("click", (e) => {
    e.stopPropagation(); // don't also trigger the card's click-to-open-modal
    if (!homeCoords) return;
    window.open(directionsUrl(f), "_blank");
  });

  card.appendChild(header);
  card.appendChild(detail);
  card.appendChild(directionsButton);
  return card;
}

function updateNearbyHospitalsPanel() {
  const panel = document.getElementById("nearby-hospitals-panel");

  if (latestStatusLevel === "green" || !latestHospitalsData || !latestBreachSummary) {
    panel.classList.add("hidden");
    return;
  }

  const tier = worstBreachingTier(latestBreachSummary);
  const ranked = rankFacilities(latestHospitalsData.facilities, tier);

  const container = document.getElementById("nearby-hospitals-list");
  container.innerHTML = "";

  if (ranked.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No nearby facilities are currently available (all are at capacity).";
    container.appendChild(empty);
  } else {
    ranked.forEach((r, index) => {
      container.appendChild(renderFacilityCard(r, tier, index === 0));
    });
  }

  panel.classList.remove("hidden");
}

// --- Hospital Network map (Step 7) ----------------------------------------
// Leaflet needs its container to be visible (non-zero size) when it first
// measures itself, but our tabs use display:none — so the map is created
// lazily (on first data arrival or first time the tab is opened) and we
// call invalidateSize() whenever the tab becomes visible.

function initNetworkMap() {
  if (networkMap || !homeCoords || typeof L === "undefined") return;

  networkMap = L.map("network-map").setView([homeCoords.lat, homeCoords.lng], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(networkMap);

  // Charcoal, not the accent red — a red-status facility marker must
  // never be confused with the home ED's own fixed reference marker.
  L.circleMarker([homeCoords.lat, homeCoords.lng], {
    radius: 10,
    color: "#111827",
    fillColor: "#111827",
    fillOpacity: 1,
    weight: 2,
  })
    .addTo(networkMap)
    .bindPopup("<strong>WayPoint Emergency Department</strong><br>Home ED (you are here)");
}

function renderNetworkMarkers(facilities) {
  if (!networkMap) return;
  facilities.forEach((f) => {
    const color = STATUS_COLORS[f.status] || "#9ca3af";
    if (networkMarkers[f.id]) {
      networkMarkers[f.id].setStyle({ color, fillColor: color });
    } else {
      const marker = L.circleMarker([f.lat, f.lng], {
        radius: 9,
        color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 2,
      }).addTo(networkMap);
      marker.bindTooltip(f.name);
      marker.on("click", () => openHospitalModal(f));
      networkMarkers[f.id] = marker;
    }
  });
}

function renderNetworkFacilityCard(f) {
  const card = document.createElement("div");
  card.className = "facility-card clickable";
  card.addEventListener("click", () => openHospitalModal(f));

  const header = document.createElement("div");
  header.className = "facility-card-header";

  const dot = document.createElement("span");
  dot.className = `status-dot-small status-${f.status}`;

  const name = document.createElement("strong");
  name.textContent = f.name;

  header.appendChild(dot);
  header.appendChild(name);

  const detail = document.createElement("div");
  detail.className = "facility-card-detail";
  detail.textContent =
    `Avg wait: ${f.avg_wait_minutes} min · ${f.beds_available} beds/slots available · ` +
    `${f.travel_time_minutes} min away (${f.distance_miles} mi)`;

  const chips = document.createElement("div");
  chips.className = "capability-chips";
  f.capabilities.forEach((c) => {
    const chip = document.createElement("span");
    chip.className = "capability-chip";
    chip.textContent = c;
    chips.appendChild(chip);
  });

  const directionsButton = document.createElement("button");
  directionsButton.className = "directions-button";
  directionsButton.textContent = "Get directions";
  directionsButton.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!homeCoords) return;
    window.open(directionsUrl(f), "_blank");
  });

  card.appendChild(header);
  card.appendChild(detail);
  card.appendChild(chips);
  card.appendChild(directionsButton);
  return card;
}

function renderNetworkList(facilities) {
  const container = document.getElementById("network-facility-list");
  if (!container) return;
  container.innerHTML = "";
  facilities.forEach((f) => container.appendChild(renderNetworkFacilityCard(f)));
}

async function fetchHospitals() {
  try {
    const res = await fetch("/hospitals");
    const data = await res.json();
    homeCoords = data.home;
    latestHospitalsData = data;
    updateNearbyHospitalsPanel();

    initNetworkMap();
    renderNetworkMarkers(data.facilities);
    renderNetworkList(data.facilities);

    // If the modal is open on an urgent care site, its summary comes from
    // this same payload (urgent care sites don't have a /state endpoint).
    if (modalFacility && modalFacility.type === "urgent_care") {
      const fresh = data.facilities.find((f) => f.id === modalFacility.id);
      if (fresh) renderUrgentCareSummary(fresh);
    }
  } catch (e) {
    console.error("Failed to fetch nearby hospitals", e);
  }
}

// --- Hospital detail modal (Step 6) ---------------------------------------
// Reuses renderStatusBanner / renderBreachSummary / renderHospitalWidgets
// with prefix "modal-" instead of duplicating the rendering logic.

let modalFacility = null;
let modalPollInterval = null;

function renderUrgentCareSummary(facility) {
  setText("modal-uc-status", facility.status.toUpperCase());
  setText("modal-uc-wait", `${facility.avg_wait_minutes} min`);
  setText("modal-uc-beds", facility.beds_available);
  setText("modal-uc-travel", `${facility.travel_time_minutes} min (${facility.distance_miles} mi)`);
}

async function refreshHospitalDetail() {
  if (!modalFacility) return;
  try {
    const res = await fetch(`/hospitals/${modalFacility.id}/state`);
    const data = await res.json();
    renderStatusBanner("modal-", data.status);
    renderBreachSummary("modal-", data.breach_summary);
    renderHospitalWidgets("modal-", data);
  } catch (e) {
    console.error("Failed to refresh hospital detail", e);
  }
}

function openHospitalModal(facility) {
  modalFacility = facility;
  const isUrgentCare = facility.type === "urgent_care";

  setText("modal-hospital-name", facility.name);
  document.getElementById("modal-hospital-detail").classList.toggle("hidden", isUrgentCare);
  document.getElementById("modal-urgent-care-detail").classList.toggle("hidden", !isUrgentCare);
  document.getElementById("hospital-detail-modal").classList.remove("hidden");

  const directionsButton = document.getElementById("modal-directions-button");
  directionsButton.onclick = () => window.open(directionsUrl(facility), "_blank");

  if (isUrgentCare) {
    renderUrgentCareSummary(facility);
  } else {
    refreshHospitalDetail();
    modalPollInterval = setInterval(refreshHospitalDetail, POLL_INTERVAL_MS);
  }
}

function closeHospitalModal() {
  modalFacility = null;
  document.getElementById("hospital-detail-modal").classList.add("hidden");
  if (modalPollInterval) {
    clearInterval(modalPollInterval);
    modalPollInterval = null;
  }
}

function renderStatusBanner(prefix, status) {
  const banner = document.getElementById(`${prefix}status-banner`);
  if (!banner) return;

  banner.className = "status-banner status-" + status.level;
  setText(`${prefix}status-level`, status.level.toUpperCase());
  setText(`${prefix}status-reason`, status.reason);

  // A distinct shape per status (not just a color swap) — status must
  // never be conveyed by color alone.
  const icon = document.getElementById(`${prefix}status-icon`);
  if (icon) icon.innerHTML = ICONS["status-" + status.level] || "";
}

// Fills the Patients/Beds/Rooms/Nurses/Physicians/Arrivals/Float-Pool
// widgets. Shared verbatim by the home ED (prefix "") and the hospital
// detail modal (prefix "modal-", Step 6) — same formatting logic, just
// targeting a different set of element ids.
function renderHospitalWidgets(prefix, data) {
  setText(`${prefix}patients-total`, data.patients.total);
  setText(`${prefix}patients-waiting`, data.patients.waiting);
  setText(`${prefix}patients-in-bed`, data.patients.in_bed);
  setText(`${prefix}avg-wait`, `${data.utilization.avg_wait_minutes} min`);

  setText(`${prefix}beds-occupied`, data.utilization.beds.occupied);
  setText(`${prefix}beds-total`, data.utilization.beds.total);
  setBar(`${prefix}beds-bar`, `${prefix}beds-pct`, data.utilization.beds.pct);

  setText(`${prefix}rooms-in-use`, data.utilization.rooms.in_use);
  setText(`${prefix}rooms-total`, data.utilization.rooms.total);
  setBar(`${prefix}rooms-bar`, `${prefix}rooms-pct`, data.utilization.rooms.pct);

  setText(`${prefix}nurses-used`, data.utilization.nurses.capacity_used);
  setText(`${prefix}nurses-total`, data.utilization.nurses.capacity_total);
  setBar(`${prefix}nurses-bar`, `${prefix}nurses-pct`, data.utilization.nurses.pct);

  setText(`${prefix}physicians-used`, data.utilization.physicians.capacity_used);
  setText(`${prefix}physicians-total`, data.utilization.physicians.capacity_total);
  setBar(`${prefix}physicians-bar`, `${prefix}physicians-pct`, data.utilization.physicians.pct);

  setText(`${prefix}arrivals-ambulance`, data.arrivals.ambulance);
  setText(`${prefix}arrivals-walkin`, data.arrivals.walk_in);
  if (data.arrivals.relocated !== undefined) {
    setText(`${prefix}arrivals-relocated`, data.arrivals.relocated);
  }

  setText(`${prefix}float-nurses`, data.float_pool.nurses_available);
  setText(`${prefix}float-physicians`, data.float_pool.physicians_available);
  setText(`${prefix}float-rooms`, data.float_pool.rooms_available);
  if (data.overflow_pool) {
    setText(`${prefix}overflow-beds`, data.overflow_pool.overflow_beds_available);
  }
}

function renderRelocationBanner(relocation) {
  const banner = document.getElementById("relocation-banner");
  const destinations = relocation.active_destinations;
  if (!destinations || destinations.length === 0) {
    banner.classList.add("hidden");
    return;
  }
  setText("relocation-destinations", destinations.join(", "));
  banner.classList.remove("hidden");
}

function createEventLogRow(event) {
  const row = document.createElement("div");
  row.className = "event-log-row";

  const time = document.createElement("span");
  time.className = "event-log-time";
  time.textContent = event.time;

  const message = document.createElement("span");
  message.className = "event-log-message";
  message.textContent = event.message;

  row.append(time, message);
  return row;
}

function renderEventLog(containerId, events, limit) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";

  if (!events || events.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No events yet — run a surge to see status changes and applied actions here.";
    container.appendChild(empty);
    return;
  }

  // events is already newest-first (see app/event_log.py), so a limit
  // just takes the most recent N rather than the oldest.
  const shown = limit ? events.slice(0, limit) : events;
  shown.forEach((event) => container.appendChild(createEventLogRow(event)));
}

// Tier 2's target wait (see app/config.py's TIER_TARGET_MINUTES, and the
// tier table on the How It Works page) — drawn as a reference line on the
// wait chart so a plain number has something concrete to read against.
const TIER2_TARGET_MINUTES = 10;

function drawTrendChart(canvasId, dataPoints, color, options = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  const leftPad = 32;
  const rightPad = 10;
  const topPad = 16;
  const bottomPad = 20;
  const plotWidth = width - leftPad - rightPad;
  const plotHeight = height - topPad - bottomPad;

  if (dataPoints.length < 2) {
    ctx.fillStyle = "#9ca3af";
    ctx.font = "13px 'IBM Plex Sans', sans-serif";
    ctx.fillText("Collecting data — leave this tab open during a surge…", leftPad, height / 2);
    return;
  }

  const peak = Math.max(...dataPoints);
  const realMax = Math.max(peak, options.thresholdValue || 0);
  const divisor = realMax || 1; // avoid divide-by-zero without lying about the peak

  // Light gridlines with numeric y-axis labels at 0 / half / max.
  ctx.strokeStyle = "#e5e5e5";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#9ca3af";
  ctx.font = "10px 'IBM Plex Mono', monospace";
  ctx.textAlign = "right";
  [0, 0.5, 1].forEach((frac) => {
    const y = topPad + plotHeight * (1 - frac);
    ctx.beginPath();
    ctx.moveTo(leftPad, y + 0.5);
    ctx.lineTo(width - rightPad, y + 0.5);
    ctx.stroke();
    ctx.fillText(String(Math.round(divisor * frac)), leftPad - 6, y + 3);
  });
  ctx.textAlign = "left";

  // Tier-target threshold line, when one applies to this chart. Always
  // red — the one reserved "needs attention" color — regardless of the
  // data line's own color, so the two are never confused.
  if (options.thresholdValue) {
    const ty = topPad + plotHeight * (1 - options.thresholdValue / divisor);
    ctx.save();
    ctx.strokeStyle = "#dc2626";
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(leftPad, ty);
    ctx.lineTo(width - rightPad, ty);
    ctx.stroke();
    ctx.restore();
    // Left-aligned, just below the line — the peak annotation owns the
    // top-right corner, so this avoids colliding with it even when the
    // threshold sits near the top of the chart.
    ctx.fillStyle = "#dc2626";
    ctx.font = "10px 'IBM Plex Mono', monospace";
    ctx.fillText(options.thresholdLabel || "target", leftPad + 4, ty + 11);
  }

  // The data line, with a soft fill underneath.
  const stepX = plotWidth / (dataPoints.length - 1);
  ctx.beginPath();
  dataPoints.forEach((value, i) => {
    const x = leftPad + i * stepX;
    const y = topPad + plotHeight * (1 - value / divisor);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.lineTo(leftPad + plotWidth, topPad + plotHeight);
  ctx.lineTo(leftPad, topPad + plotHeight);
  ctx.closePath();
  ctx.fillStyle = color + "1f"; // ~12% opacity fill under the line
  ctx.fill();

  // Axis lines and the peak value, annotated directly on the chart.
  ctx.strokeStyle = "#d4d4d4";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(leftPad, topPad);
  ctx.lineTo(leftPad, topPad + plotHeight);
  ctx.lineTo(leftPad + plotWidth, topPad + plotHeight);
  ctx.stroke();

  ctx.fillStyle = "#111827";
  ctx.font = "11px 'IBM Plex Mono', monospace";
  ctx.textAlign = "right";
  ctx.fillText(`peak: ${peak}`, width - rightPad, topPad - 4);
  ctx.textAlign = "left";

  ctx.fillStyle = "#9ca3af";
  ctx.font = "10px 'IBM Plex Sans', sans-serif";
  ctx.fillText("earlier", leftPad, height - 4);
  ctx.textAlign = "right";
  ctx.fillText("now", width - rightPad, height - 4);
  ctx.textAlign = "left";
}

function renderImpactTab(data) {
  const scorecard = data.scorecard;
  setText("scorecard-peak-wait", `${scorecard.peak_wait_minutes} min`);
  setText("scorecard-peak-breaches", scorecard.peak_tier12_breaches);
  setText("scorecard-treated", data.patients.discharged);
  setText(
    "scorecard-recovery",
    scorecard.last_recovery_seconds !== null
      ? `${scorecard.last_recovery_seconds}s`
      : scorecard.currently_in_red
        ? "in progress…"
        : "–"
  );
  setText(
    "scorecard-lwbs",
    data.lwbs.eligible_total > 0 ? `${data.lwbs.left_count} (${data.lwbs.rate_pct}%)` : "0"
  );

  waitHistory.push(data.utilization.avg_wait_minutes);
  breachHistory.push(data.breach_summary.tier1_2_breaches);
  if (waitHistory.length > MAX_TREND_POINTS) waitHistory.shift();
  if (breachHistory.length > MAX_TREND_POINTS) breachHistory.shift();

  // Charcoal, not accent red — the threshold line is red, so the data
  // line itself needs a neutral color to stay visually distinct from it.
  drawTrendChart("chart-wait", waitHistory, "#111827", {
    thresholdValue: TIER2_TARGET_MINUTES,
    thresholdLabel: "Tier 2 target (10 min)",
  });
  drawTrendChart("chart-breaches", breachHistory, STATUS_COLORS.red);

  renderImpactComparison(data.impact);
}

// Impact of WayPoint: the with/without-WayPoint comparison card. Uses the
// exact same `impact` object the situation report reads from
// GET /api/session-summary, so the two never disagree.
function renderImpactComparison(impact) {
  setText("impact-baseline-definition", impact.baseline_definition);

  const noIntervention = document.getElementById("impact-no-intervention");
  const content = document.getElementById("impact-content");

  if (!impact.any_intervention_applied) {
    noIntervention.classList.remove("hidden");
    content.classList.add("hidden");
    return;
  }

  noIntervention.classList.add("hidden");
  content.classList.remove("hidden");

  const headline =
    impact.without_edflow.tier12_breach_minutes > 0
      ? `WayPoint reduced Tier 1-2 breach time by ${impact.headline.breach_pct_reduction}% and saved ` +
        `${impact.headline.patient_minutes_saved} patient-minutes of waiting.`
      : `WayPoint saved ${impact.headline.patient_minutes_saved} patient-minutes of waiting ` +
        `(no Tier 1-2 breaches occurred in either scenario).`;
  setText("impact-headline", headline);

  const rows = [
    ["Avg wait (min)", "avg_wait_minutes"],
    ["Peak avg wait (min)", "peak_avg_wait_minutes"],
    ["Tier 1-2 breach-minutes", "tier12_breach_minutes"],
    ["Total patient-minutes waited", "total_patient_minutes_waited"],
    ["Left without being seen", "left_without_being_seen"],
  ];

  const tbody = document.getElementById("impact-table-body");
  tbody.innerHTML = "";
  rows.forEach(([label, key]) => {
    const diff = impact.difference[key];
    const diffText = diff > 0 ? `+${diff}` : `${diff}`;

    const tr = document.createElement("tr");
    const labelCell = document.createElement("td");
    labelCell.textContent = label;
    const withoutCell = document.createElement("td");
    withoutCell.textContent = impact.without_edflow[key];
    const withCell = document.createElement("td");
    withCell.textContent = impact.with_edflow[key];
    const diffCell = document.createElement("td");
    diffCell.textContent = diffText;
    if (diff > 0) diffCell.className = "impact-favorable";

    tr.append(labelCell, withoutCell, withCell, diffCell);
    tbody.appendChild(tr);
  });
}

// --- Situation report (Impact tab): a deterministic, template-built
// shift-handoff report from GET /api/session-summary. No AI call, no
// dependency on the assistant — it reads the same shared summary the
// dashboard itself is built from, so it can never disagree with it, and
// it works even if the assistant/LLM call in Step 3 is unavailable.
let currentReportText = "";

function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const parts = [];
  if (h) parts.push(`${h}h`);
  if (h || m) parts.push(`${m}m`);
  parts.push(`${sec}s`);
  return parts.join(" ");
}

function summaryHasNoActivity(summary) {
  return (
    summary.event_log.length === 0 &&
    summary.treated === 0 &&
    summary.relocated === 0 &&
    summary.left_without_being_seen === 0 &&
    summary.peak.avg_wait_minutes === 0 &&
    summary.peak.tier12_breaches === 0
  );
}

function buildSituationReportText(summary) {
  const lines = [];
  const generated = new Date(summary.generated_at).toLocaleString();

  lines.push("EDFLOW SITUATION REPORT");
  lines.push(`Generated: ${generated}  •  Session duration: ${formatDuration(summary.session.duration_seconds)}`);
  lines.push("Simulated data — for demo purposes only, not a real clinical record.");
  lines.push("");

  lines.push("CURRENT STATUS");
  lines.push(
    `${summary.status.level.toUpperCase()} — ${summary.breach_summary.tier1_2_breaches} Tier 1-2 patient(s) past target wait`
  );
  lines.push(summary.status.reason);
  lines.push("");

  lines.push("PEAK DURING THIS SESSION");
  lines.push(
    `Peak avg wait: ${summary.peak.avg_wait_minutes} min` +
      (summary.peak.avg_wait_at ? ` (at ${summary.peak.avg_wait_at})` : "")
  );
  lines.push(
    `Peak Tier 1-2 breaches: ${summary.peak.tier12_breaches}` +
      (summary.peak.tier12_breaches_at ? ` (at ${summary.peak.tier12_breaches_at})` : "")
  );
  lines.push("");

  lines.push("BREACHES BY TIER");
  for (let tier = 1; tier <= 5; tier++) {
    const t = summary.breach_summary.by_tier[tier];
    lines.push(`Tier ${tier}: ${t.waiting} waiting, ${t.breached} breached, avg wait ${t.avg_wait_minutes} min`);
  }
  lines.push("");

  lines.push("ARRIVALS AND THROUGHPUT");
  lines.push(`Ambulance arrivals: ${summary.arrivals.ambulance}`);
  lines.push(`Walk-in arrivals: ${summary.arrivals.walk_in}`);
  lines.push(`Total arrivals: ${summary.arrivals.total}`);
  lines.push(`Treated (discharged): ${summary.treated}`);
  lines.push(`Left without being seen: ${summary.left_without_being_seen}`);
  lines.push(`Relocated: ${summary.relocated}`);
  lines.push("");

  lines.push("ACTIONS TAKEN");
  const actions = summary.event_log.filter((e) => e.message.startsWith("Applied:"));
  if (actions.length === 0) {
    lines.push("No actions were applied this session.");
  } else {
    actions.forEach((e) => lines.push(`- ${e.time} — ${e.message}`));
  }
  lines.push("");

  lines.push("OUTCOME");
  if (summary.status.level === "green" && !summary.currently_in_red) {
    lines.push(
      summary.last_recovery_seconds !== null
        ? `Recovered — the last red episode lasted ${summary.last_recovery_seconds}s before returning to green.`
        : "No red episode occurred this session."
    );
  } else {
    lines.push(`Still unresolved — status is currently ${summary.status.level.toUpperCase()}.`);
  }
  lines.push("");

  lines.push("IMPACT OF EDFLOW (SIMULATED COMPARISON)");
  lines.push(summary.impact.baseline_definition);
  if (!summary.impact.any_intervention_applied) {
    lines.push(
      "No recommendations have been applied yet this session, so there's nothing to compare — " +
        "WayPoint and the baseline are identical so far."
    );
  } else {
    const impact = summary.impact;
    lines.push(
      impact.without_edflow.tier12_breach_minutes > 0
        ? `WayPoint reduced Tier 1-2 breach time by ${impact.headline.breach_pct_reduction}% and saved ` +
          `${impact.headline.patient_minutes_saved} patient-minutes of waiting.`
        : `WayPoint saved ${impact.headline.patient_minutes_saved} patient-minutes of waiting ` +
          `(no Tier 1-2 breaches occurred in either scenario).`
    );
    lines.push("");
    lines.push("  Metric                          Without WayPoint   With WayPoint   Difference");
    const impactRows = [
      ["Avg wait (min)", "avg_wait_minutes"],
      ["Peak avg wait (min)", "peak_avg_wait_minutes"],
      ["Tier 1-2 breach-minutes", "tier12_breach_minutes"],
      ["Total patient-minutes waited", "total_patient_minutes_waited"],
      ["Left without being seen", "left_without_being_seen"],
    ];
    impactRows.forEach(([label, key]) => {
      const diff = impact.difference[key];
      const diffText = diff > 0 ? `+${diff}` : `${diff}`;
      lines.push(
        `  ${label.padEnd(31)} ${String(impact.without_edflow[key]).padEnd(17)} ${String(impact.with_edflow[key]).padEnd(13)} ${diffText}`
      );
    });
  }
  lines.push("");

  lines.push("HANDOFF NOTES FOR INCOMING SHIFT");
  const notes = [];
  if (summary.breach_summary.tier1_2_breaches > 0) {
    notes.push(`${summary.breach_summary.tier1_2_breaches} Tier 1-2 patient(s) still past target wait.`);
  }
  const floatUsed = summary.float_pool_used;
  const floatUsedParts = [];
  if (floatUsed.nurses) floatUsedParts.push(`${floatUsed.nurses} nurse(s)`);
  if (floatUsed.physicians) floatUsedParts.push(`${floatUsed.physicians} physician(s)`);
  if (floatUsed.rooms) floatUsedParts.push(`${floatUsed.rooms} room(s)`);
  if (floatUsedParts.length) {
    notes.push(`Float pool still in use: ${floatUsedParts.join(", ")}.`);
  }
  if (summary.overflow_used.beds) {
    notes.push(`Overflow beds still in use: ${summary.overflow_used.beds}.`);
  }
  if (summary.active_relocation_destinations.length) {
    notes.push(`Active relocation to: ${summary.active_relocation_destinations.join(", ")}.`);
  }
  const watchFacilities = summary.nearby_facilities.filter((f) => f.status === "yellow" || f.status === "red");
  if (watchFacilities.length) {
    notes.push(`Nearby facilities to watch: ${watchFacilities.map((f) => `${f.name} (${f.status})`).join(", ")}.`);
  }
  if (notes.length === 0) {
    notes.push("No outstanding concerns for the incoming shift.");
  }
  notes.forEach((note) => lines.push(`- ${note}`));
  lines.push("");

  lines.push("EVENT LOG");
  if (summary.event_log.length === 0) {
    lines.push("No events recorded this session.");
  } else {
    // Stored newest-first; show chronologically (oldest first) in the
    // report, the way a real handoff log would read top to bottom.
    [...summary.event_log].reverse().forEach((e) => lines.push(`${e.time}  ${e.message}`));
  }

  return lines.join("\n");
}

// --- Situation report: rendered document (on-screen + print) ---
// Deliberately built from the dashboard's own components (status banner,
// scorecard tiles, breach table, event log rows) rather than a plain-text
// dump, so it reads as a natural part of the app. Copy/Download still use
// buildSituationReportText() above — a portable plain-text version for
// pasting into email/Slack.

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

// Small inline SVG icons for dynamically-generated content (static markup
// icons live directly in index.html) — trusted, hardcoded strings only,
// never built from user or server input.
const ICONS = {
  check: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3.2L13 4.5"/></svg>',
  warning:
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.2 14.5 13.3H1.5Z"/><path d="M8 6.4v3.2"/><circle cx="8" cy="11.6" r="0.6" fill="currentColor" stroke="none"/></svg>',
  // Status shapes — a distinct SILHOUETTE per level (circle/triangle/
  // octagon), not just a color swap, so status is never conveyed by
  // color alone (Step 3).
  "status-green":
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><circle cx="8" cy="8" r="6.5"/></svg>',
  "status-yellow":
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M8 1.6 14.8 13.6H1.2Z"/></svg>',
  "status-red":
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M5.1 1.6h5.8L14.4 5.1v5.8L10.9 14.4H5.1L1.6 10.9V5.1Z"/></svg>',
};

function iconSpan(name, className) {
  const span = document.createElement("span");
  if (className) span.className = className;
  span.innerHTML = ICONS[name] || "";
  return span;
}

function buildReportStatTile(value, label) {
  const tile = el("div", "report-stat-tile");
  tile.appendChild(el("div", "report-stat-value", value));
  tile.appendChild(el("div", "report-stat-label", label));
  return tile;
}

function buildReportImpactSection(impact) {
  const fragment = document.createDocumentFragment();
  fragment.appendChild(el("p", "impact-baseline-definition", impact.baseline_definition));

  if (!impact.any_intervention_applied) {
    fragment.appendChild(
      el(
        "div",
        "impact-no-intervention",
        "No recommendations have been applied yet this session, so there's nothing to compare — " +
          "WayPoint and the baseline are identical so far."
      )
    );
    return fragment;
  }

  const headlineText =
    impact.without_edflow.tier12_breach_minutes > 0
      ? `WayPoint reduced Tier 1-2 breach time by ${impact.headline.breach_pct_reduction}% and saved ` +
        `${impact.headline.patient_minutes_saved} patient-minutes of waiting.`
      : `WayPoint saved ${impact.headline.patient_minutes_saved} patient-minutes of waiting ` +
        `(no Tier 1-2 breaches occurred in either scenario).`;
  fragment.appendChild(el("p", "impact-headline", headlineText));

  const table = document.createElement("table");
  table.className = "breach-table impact-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Metric", "Without WayPoint", "With WayPoint", "Difference"].forEach((h) => headRow.appendChild(el("th", null, h)));
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  const impactRows = [
    ["Avg wait (min)", "avg_wait_minutes"],
    ["Peak avg wait (min)", "peak_avg_wait_minutes"],
    ["Tier 1-2 breach-minutes", "tier12_breach_minutes"],
    ["Total patient-minutes waited", "total_patient_minutes_waited"],
    ["Left without being seen", "left_without_being_seen"],
  ];
  impactRows.forEach(([label, key]) => {
    const diff = impact.difference[key];
    const diffText = diff > 0 ? `+${diff}` : `${diff}`;
    const tr = document.createElement("tr");
    tr.append(
      el("td", null, label),
      el("td", null, String(impact.without_edflow[key])),
      el("td", null, String(impact.with_edflow[key])),
      el("td", diff > 0 ? "impact-favorable" : null, diffText)
    );
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  fragment.appendChild(table);
  return fragment;
}

function renderSituationReportDoc(summary) {
  const container = document.getElementById("situation-report-doc");
  container.innerHTML = "";

  const generated = new Date(summary.generated_at).toLocaleString();

  const titleRow = el("div", "report-doc-title-row");
  const titleBlock = document.createElement("div");
  titleBlock.appendChild(el("h2", "report-doc-title", "Shift Situation Report"));
  titleBlock.appendChild(
    el(
      "p",
      "report-doc-meta",
      `Generated ${generated} · ${formatDuration(summary.session.duration_seconds)} into this session · Simulated data`
    )
  );
  titleRow.appendChild(titleBlock);
  container.appendChild(titleRow);

  // Current status — the exact same banner component as Live Ops.
  const banner = el("div", `status-banner status-${summary.status.level}`);
  banner.appendChild(el("span", "status-dot"));
  const bannerText = document.createElement("div");
  bannerText.appendChild(el("strong", null, summary.status.level.toUpperCase()));
  bannerText.appendChild(el("p", null, summary.status.reason));
  banner.append(bannerText);
  container.appendChild(banner);

  container.appendChild(el("h3", null, "Peak during this session"));
  const peakGrid = el("div", "report-stat-grid");
  peakGrid.append(
    buildReportStatTile(
      `${summary.peak.avg_wait_minutes} min`,
      `Peak avg wait${summary.peak.avg_wait_at ? ` · at ${summary.peak.avg_wait_at}` : ""}`
    ),
    buildReportStatTile(
      String(summary.peak.tier12_breaches),
      `Peak Tier 1-2 breaches${summary.peak.tier12_breaches_at ? ` · at ${summary.peak.tier12_breaches_at}` : ""}`
    )
  );
  container.appendChild(peakGrid);

  container.appendChild(el("h3", null, "Breaches by tier"));
  const breachTable = document.createElement("table");
  breachTable.className = "breach-table";
  const breachHead = document.createElement("thead");
  const breachHeadRow = document.createElement("tr");
  ["Tier", "Waiting", "Breached", "Avg wait"].forEach((h) => breachHeadRow.appendChild(el("th", null, h)));
  breachHead.appendChild(breachHeadRow);
  breachTable.appendChild(breachHead);
  const breachBody = document.createElement("tbody");
  for (let tier = 1; tier <= 5; tier++) {
    const t = summary.breach_summary.by_tier[tier];
    const tr = document.createElement("tr");
    tr.append(
      el("td", null, `Tier ${tier}`),
      el("td", null, String(t.waiting)),
      el("td", t.breached > 0 ? "breach-count-nonzero" : null, String(t.breached)),
      el("td", null, `${t.avg_wait_minutes} min`)
    );
    breachBody.appendChild(tr);
  }
  breachTable.appendChild(breachBody);
  container.appendChild(breachTable);

  container.appendChild(el("h3", null, "Arrivals and throughput"));
  const arrivalsGrid = el("div", "report-stat-grid");
  [
    [summary.arrivals.ambulance, "Ambulance arrivals"],
    [summary.arrivals.walk_in, "Walk-in arrivals"],
    [summary.arrivals.total, "Total arrivals"],
    [summary.treated, "Treated"],
    [summary.left_without_being_seen, "Left without being seen"],
    [summary.relocated, "Relocated"],
  ].forEach(([value, label]) => arrivalsGrid.appendChild(buildReportStatTile(String(value), label)));
  container.appendChild(arrivalsGrid);

  container.appendChild(el("h3", null, "Actions taken"));
  const actions = summary.event_log.filter((e) => e.message.startsWith("Applied:"));
  if (actions.length === 0) {
    container.appendChild(el("p", "report-muted-line", "No actions were applied this session."));
  } else {
    const actionsList = el("div", "event-log-list report-actions-list");
    actions.forEach((e) => actionsList.appendChild(createEventLogRow(e)));
    container.appendChild(actionsList);
  }

  container.appendChild(el("h3", null, "Outcome"));
  const recovered = summary.status.level === "green" && !summary.currently_in_red;
  const outcome = el("p", `report-outcome ${recovered ? "is-good" : "is-open"}`);
  outcome.appendChild(iconSpan(recovered ? "check" : "warning", "report-outcome-icon"));
  outcome.appendChild(
    el(
      "span",
      null,
      recovered
        ? summary.last_recovery_seconds !== null
          ? `Recovered — the last red episode lasted ${summary.last_recovery_seconds}s before returning to green.`
          : "No red episode occurred this session."
        : `Still unresolved — status is currently ${summary.status.level.toUpperCase()}.`
    )
  );
  container.appendChild(outcome);

  const impactHeading = el("h3", null, "Impact of WayPoint ");
  impactHeading.appendChild(el("span", "simulated-badge", "Simulated comparison"));
  container.appendChild(impactHeading);
  container.appendChild(buildReportImpactSection(summary.impact));

  container.appendChild(el("h3", null, "Handoff notes for incoming shift"));
  const notes = [];
  if (summary.breach_summary.tier1_2_breaches > 0) {
    notes.push(`${summary.breach_summary.tier1_2_breaches} Tier 1-2 patient(s) still past target wait.`);
  }
  const floatUsed = summary.float_pool_used;
  const floatUsedParts = [];
  if (floatUsed.nurses) floatUsedParts.push(`${floatUsed.nurses} nurse(s)`);
  if (floatUsed.physicians) floatUsedParts.push(`${floatUsed.physicians} physician(s)`);
  if (floatUsed.rooms) floatUsedParts.push(`${floatUsed.rooms} room(s)`);
  if (floatUsedParts.length) notes.push(`Float pool still in use: ${floatUsedParts.join(", ")}.`);
  if (summary.overflow_used.beds) notes.push(`Overflow beds still in use: ${summary.overflow_used.beds}.`);
  if (summary.active_relocation_destinations.length) {
    notes.push(`Active relocation to: ${summary.active_relocation_destinations.join(", ")}.`);
  }
  const watchFacilities = summary.nearby_facilities.filter((f) => f.status === "yellow" || f.status === "red");
  if (watchFacilities.length) {
    notes.push(`Nearby facilities to watch: ${watchFacilities.map((f) => `${f.name} (${f.status})`).join(", ")}.`);
  }
  if (notes.length === 0) notes.push("No outstanding concerns for the incoming shift.");
  const notesList = document.createElement("ul");
  notesList.className = "report-notes-list";
  notes.forEach((note) => notesList.appendChild(el("li", null, note)));
  container.appendChild(notesList);

  container.appendChild(el("h3", null, "Event log"));
  if (summary.event_log.length === 0) {
    container.appendChild(el("p", "report-muted-line", "No events recorded this session."));
  } else {
    const logList = el("div", "event-log-list");
    [...summary.event_log].reverse().forEach((e) => logList.appendChild(createEventLogRow(e)));
    container.appendChild(logList);
  }
}

async function generateSituationReport() {
  const emptyState = document.getElementById("situation-report-empty");
  const card = document.getElementById("situation-report-card");
  const feedback = document.getElementById("report-copy-feedback");
  feedback.textContent = "";

  const res = await fetch("/api/session-summary");
  const summary = await res.json();

  if (summaryHasNoActivity(summary)) {
    emptyState.classList.remove("hidden");
    card.classList.add("hidden");
    currentReportText = "";
    return;
  }

  currentReportText = buildSituationReportText(summary);
  renderSituationReportDoc(summary);
  emptyState.classList.add("hidden");
  card.classList.remove("hidden");
}

function clearSituationReport() {
  currentReportText = "";
  document.getElementById("situation-report-doc").innerHTML = "";
  document.getElementById("situation-report-card").classList.add("hidden");
  document.getElementById("situation-report-empty").classList.add("hidden");
  const feedback = document.getElementById("report-copy-feedback");
  if (feedback) feedback.textContent = "";
}

document.getElementById("btn-generate-report").addEventListener("click", generateSituationReport);

document.getElementById("btn-report-copy").addEventListener("click", async () => {
  const feedback = document.getElementById("report-copy-feedback");
  try {
    await navigator.clipboard.writeText(currentReportText);
    feedback.textContent = "Copied!";
  } catch (err) {
    // Clipboard API unavailable (e.g. insecure context) — fall back to a
    // temporary textarea and the older execCommand copy.
    const textarea = document.createElement("textarea");
    textarea.value = currentReportText;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
      feedback.textContent = "Copied!";
    } catch (err2) {
      feedback.textContent = "Copy failed — select and copy manually.";
    }
    document.body.removeChild(textarea);
  }
  setTimeout(() => {
    feedback.textContent = "";
  }, 2500);
});

document.getElementById("btn-report-download").addEventListener("click", () => {
  const blob = new Blob([currentReportText], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `edflow-situation-report-${Date.now()}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

document.getElementById("btn-report-print").addEventListener("click", () => {
  window.print();
});

function render(data) {
  renderHospitalWidgets("", data);
  renderStatusBanner("", data.status);

  // Last-updated timestamp, so you can see polling is actually happening.
  const now = new Date();
  setText("last-updated", `Last updated ${now.toLocaleTimeString()}`);

  renderRecommendations(data.recommendations);
  renderPatientsList(data.patients_list);
  renderBreachSummary("", data.breach_summary);
  renderRelocationBanner(data.relocation);
  renderEventLog("event-log-list", data.event_log);
  renderEventLog("impact-event-log-list", data.event_log);
  renderEventLog("live-event-log-list", data.event_log, 8);
  renderImpactTab(data);

  latestBreachSummary = data.breach_summary;
  latestStatusLevel = data.status.level;
  updateNearbyHospitalsPanel();
}

// True once /state has ever loaded successfully — a later transient
// failure (e.g. a dropped connection) shouldn't blank out or replace
// already-rendered data, only the very first load should show the
// "waking up" message instead of stale placeholder text.
let hasLoadedOnce = false;

function showWakingUpNotice() {
  if (hasLoadedOnce) return; // don't stomp on real data with a loading message
  setText("status-level", "Waking up the server…");
  setText(
    "status-reason",
    "The backend can take up to a minute to respond on its first request — retrying automatically."
  );
  const skeletonMessage = document.getElementById("app-skeleton-message");
  if (skeletonMessage) {
    skeletonMessage.textContent = "Waking up the server, this can take up to a minute…";
  }
}

// The skeleton overlay covers the whole app until the very first /state
// fetch succeeds — after that, the app's own empty/placeholder states
// (dashes, "no events yet", etc.) take over, so the skeleton never needs
// to reappear for the rest of the session.
let appSkeletonDismissed = false;
function dismissAppSkeleton() {
  if (appSkeletonDismissed) return;
  appSkeletonDismissed = true;
  const skeleton = document.getElementById("app-skeleton");
  if (skeleton) skeleton.classList.add("dismissed");
}

async function fetchState(force = false) {
  // Skip this tick if a previous fetch is still in flight (e.g. a slow
  // network), so requests don't pile up on top of each other. `force`
  // bypasses that guard — used right after an action (like Reset Demo)
  // that must always show its own result, even if a surge tick or the
  // background poll happened to be mid-flight at that exact moment.
  if (fetchInProgress && !force) return;
  fetchInProgress = true;
  try {
    const res = await fetch("/state");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    hasLoadedOnce = true;
    dismissAppSkeleton();
    render(data);
  } catch (e) {
    // The background poll (setInterval below) already retries every
    // POLL_INTERVAL_MS without any special handling here — this only
    // needs to make the FIRST load's wait legible instead of leaving the
    // static "LOADING…" placeholder up with no explanation.
    console.error("Failed to fetch /state", e);
    showWakingUpNotice();
  } finally {
    fetchInProgress = false;
  }
}

async function postAndRefresh(endpoint, force = false) {
  await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  await fetchState(force);
}

// Tracks whichever surge tick request is currently in flight, so Reset
// Demo can wait for it to fully land before wiping the database — closes
// the race where a tick's patient gets inserted right after a reset.
let currentTickPromise = null;

// The surge's length and arrivals cap now live on the server (app/surge.py)
// — this flag just tracks whether the CLIENT thinks a surge is running, so
// a Stop click that lands while startSurge() is still awaiting its first
// tick still cancels it cleanly instead of racing the interval into being
// set up anyway.
let surgeRunning = false;

async function runSurgeTick() {
  currentTickPromise = (async () => {
    try {
      const res = await fetch("/simulate/tick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      await fetchState();
      // The server ends the surge on its own once it hits its duration or
      // arrivals cap — when that happens, stop polling and restore the
      // button without waiting for the user to click Stop.
      if (surgeRunning && !data.surge_active) {
        stopSurge();
      }
    } catch (e) {
      console.error("Surge tick failed", e);
    }
  })();
  await currentTickPromise;
}

async function startSurge() {
  if (surgeRunning) return; // already running (or starting up)
  surgeRunning = true;

  const button = document.getElementById("btn-surge");
  button.textContent = "Stop Surge";
  button.classList.add("surge-active");

  try {
    // Fixes this surge's total-arrivals budget on the server; a no-op if
    // one is somehow already active there, so this can never spin up a
    // second generator.
    await fetch("/surge/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    await runSurgeTick(); // first tick immediately, don't wait a full second
  } catch (e) {
    console.error("Surge start failed", e);
  }

  if (!surgeRunning) return; // stopped while starting up — don't begin polling after all
  surgeInterval = setInterval(runSurgeTick, 1000);
}

function stopSurge() {
  surgeRunning = false;
  if (surgeInterval) {
    clearInterval(surgeInterval);
    surgeInterval = null;
  }
  const button = document.getElementById("btn-surge");
  button.classList.remove("surge-active");
  button.textContent = "Run Surge";
  // Cancels the generator server-side immediately, same as it running out
  // its duration/arrivals budget on its own.
  fetch("/surge/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch((e) =>
    console.error("Surge stop failed", e)
  );
}

// The surge is a manual toggle, not a timed run: click to start it, click
// again whenever you want to stop it, as many times as you like.
document.getElementById("btn-surge").addEventListener("click", () => {
  if (surgeRunning) {
    stopSurge();
  } else {
    startSurge();
  }
});

document.getElementById("btn-reset").addEventListener("click", async (e) => {
  // Can be clicked any time, any number of times, even mid-surge: it stops
  // the surge, waits for any surge tick that was already in flight to fully
  // land (so its patient can't sneak in right after the wipe), then forces
  // a fresh screen refresh (see the `force` flag on fetchState) so the
  // dashboard never gets stuck showing stale pre-reset numbers.
  //
  // Destructive and irreversible (wipes every patient and the whole event
  // log this session) — confirm before doing anything else. Deliberately
  // has no keyboard shortcut of its own for the same reason.
  if (!window.confirm("Reset the demo? This clears all patients and activity for this session.")) {
    return;
  }

  // The button is captured into a variable BEFORE any `await` — `e.currentTarget`
  // is reset to null by the browser once the event finishes dispatching,
  // which happens as soon as this handler crosses its first `await`. Using
  // `e.currentTarget` after that point throws (setting a property on null),
  // which — inside a `finally` — permanently skipped `disabled = false` and
  // left this button stuck disabled after its very first click.
  const button = e.currentTarget;
  stopSurge();
  if (currentTickPromise) {
    await currentTickPromise;
  }
  waitHistory = [];
  breachHistory = [];
  clearSituationReport();
  clearAssistantConversation();
  button.disabled = true;
  try {
    await postAndRefresh("/demo/reset", true);
  } finally {
    button.disabled = false;
  }
});

document.getElementById("toggle-patients").addEventListener("click", () => {
  document.getElementById("patients-list-container").classList.toggle("hidden");
});

// Sortable column headers — click toggles direction on the same column,
// or switches to a new column (ascending, except Wait which defaults to
// descending since the longest waits are usually what you want to see
// first).
document.querySelectorAll("#patients-table th[data-sort-key]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sortKey;
    if (patientsSortKey === key) {
      patientsSortDir = patientsSortDir === "asc" ? "desc" : "asc";
    } else {
      patientsSortKey = key;
      patientsSortDir = key === "wait_minutes" ? "desc" : "asc";
    }
    document.querySelectorAll("#patients-table th[data-sort-key]").forEach((h) => h.classList.remove("sort-asc", "sort-desc"));
    th.classList.add(patientsSortDir === "asc" ? "sort-asc" : "sort-desc");
    renderPatientsTable();
  });
});

document.querySelectorAll("#tier-filter .tier-filter-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#tier-filter .tier-filter-chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    patientsTierFilterValue = chip.dataset.tierFilter;
    renderPatientsTable();
  });
});

document.getElementById("btn-apply-all").addEventListener("click", async (e) => {
  // Same `currentTarget`-after-`await` pitfall as the reset button above —
  // capture the element first so re-enabling it in `finally` doesn't throw.
  const button = e.currentTarget;
  button.disabled = true;
  button.textContent = "Applying…";
  try {
    await applyAllRecommendations();
  } finally {
    button.disabled = false;
    button.textContent = "Apply All";
  }
});

// Tab switching is purely a show/hide toggle — no page navigation, no
// state reset. Simulation state lives entirely on the backend and the
// polling/surge intervals below keep running no matter which tab is
// visible, so switching tabs never interrupts a running surge.
document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab-button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));

    button.classList.add("active");
    document.querySelector(`[data-tab-content="${button.dataset.tab}"]`).classList.remove("hidden");

    // The top bar names whichever view is currently showing.
    const topbarTitle = document.getElementById("topbar-title");
    if (topbarTitle && button.dataset.viewTitle) topbarTitle.textContent = button.dataset.viewTitle;

    // Leaflet measures its container on creation, which fails silently if
    // the tab was hidden (display:none) at the time — so (re-)init and
    // force a resize check now that the container is actually visible.
    if (button.dataset.tab === "network") {
      initNetworkMap();
      if (networkMap) setTimeout(() => networkMap.invalidateSize(), 0);
    }
  });
});

// Sidebar collapse toggle: shrinks the sidebar to icons-only. Purely a
// display preference — doesn't touch which tab is active or any data.
document.getElementById("sidebar-collapse-toggle").addEventListener("click", () => {
  document.getElementById("sidebar").classList.toggle("collapsed");
});

// The Live Ops activity feed is a compact read-only preview of the same
// event log the Patients tab shows in full — this just switches tabs via
// the existing mechanism above rather than duplicating that logic.
document.getElementById("live-feed-view-log").addEventListener("click", () => {
  document.querySelector('.tab-button[data-tab="patients"]').click();
});

document.getElementById("modal-close").addEventListener("click", closeHospitalModal);

// Clicking the dark overlay itself (not the modal card) closes it too.
document.getElementById("hospital-detail-modal").addEventListener("click", (e) => {
  if (e.target.id === "hospital-detail-modal") closeHospitalModal();
});

// --- Assistant widget (floating chat button + panel): talks to POST
// /api/assistant. The conversation lives only in this array for the
// current browser session/tab — nothing is persisted or sent anywhere
// except as short-term context for the next reply.
const MAX_ASSISTANT_HISTORY = 6;
const ASSISTANT_SUGGESTIONS = [
  "Why is the status red?",
  "What should I do first?",
  "Which nearby hospital is best?",
  "Summarize the last few minutes",
];
let assistantHistory = [];
let assistantRequestInFlight = false;

function renderAssistantSuggestions() {
  const container = document.getElementById("assistant-messages");
  const suggestions = document.createElement("div");
  suggestions.id = "assistant-suggestions";
  suggestions.className = "assistant-suggestions";
  ASSISTANT_SUGGESTIONS.forEach((question) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "assistant-chip";
    chip.dataset.question = question;
    chip.textContent = question;
    suggestions.appendChild(chip);
  });
  container.appendChild(suggestions);
}

function appendAssistantMessage(role, content) {
  const container = document.getElementById("assistant-messages");
  const suggestions = document.getElementById("assistant-suggestions");
  if (suggestions) suggestions.remove(); // only shown while the chat is empty

  const bubble = document.createElement("div");
  bubble.className = `assistant-message ${role}`;
  bubble.textContent = content;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

function setAssistantTyping(isTyping) {
  document.getElementById("assistant-typing").classList.toggle("hidden", !isTyping);
  const container = document.getElementById("assistant-messages");
  container.scrollTop = container.scrollHeight;
}

async function sendAssistantMessage(message) {
  const trimmed = message.trim();
  if (!trimmed || assistantRequestInFlight) return;

  appendAssistantMessage("user", trimmed);
  assistantRequestInFlight = true;
  document.getElementById("assistant-send").disabled = true;
  setAssistantTyping(true);

  try {
    const res = await fetch("/api/assistant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: trimmed,
        history: assistantHistory.slice(-MAX_ASSISTANT_HISTORY),
      }),
    });
    const data = await res.json();
    const reply = data.reply || "Sorry, something went wrong on my end.";
    appendAssistantMessage("assistant", reply);
    assistantHistory.push({ role: "user", content: trimmed });
    assistantHistory.push({ role: "assistant", content: reply });
    if (assistantHistory.length > MAX_ASSISTANT_HISTORY) {
      assistantHistory = assistantHistory.slice(-MAX_ASSISTANT_HISTORY);
    }
  } catch (err) {
    appendAssistantMessage("error", "Couldn't reach the assistant just now — please try again.");
  } finally {
    setAssistantTyping(false);
    assistantRequestInFlight = false;
    document.getElementById("assistant-send").disabled = false;
  }
}

function openAssistantPanel() {
  document.getElementById("assistant-panel").classList.remove("hidden");
  document.getElementById("assistant-input").focus();
}

function closeAssistantPanel() {
  document.getElementById("assistant-panel").classList.add("hidden");
}

function clearAssistantConversation() {
  assistantHistory = [];
  const container = document.getElementById("assistant-messages");
  container.innerHTML = "";
  renderAssistantSuggestions();
}

document.getElementById("assistant-toggle").addEventListener("click", () => {
  const panel = document.getElementById("assistant-panel");
  if (panel.classList.contains("hidden")) {
    openAssistantPanel();
  } else {
    closeAssistantPanel();
  }
});

document.getElementById("assistant-close").addEventListener("click", closeAssistantPanel);
document.getElementById("assistant-clear").addEventListener("click", clearAssistantConversation);

document.getElementById("assistant-messages").addEventListener("click", (e) => {
  const chip = e.target.closest(".assistant-chip");
  if (chip) sendAssistantMessage(chip.dataset.question);
});

document.getElementById("assistant-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("assistant-input");
  const message = input.value;
  input.value = "";
  sendAssistantMessage(message);
});

fetchState();
fetchHospitals();
setInterval(fetchState, POLL_INTERVAL_MS);
setInterval(fetchHospitals, POLL_INTERVAL_MS);

// A cold Render instance doesn't error — the very first fetch() just sits
// pending for up to a minute. That case never reaches fetchState()'s own
// catch block, so upgrade the message on a timer instead, once "LOADING…"
// has been up long enough that it's clearly not a normal fast response.
setTimeout(showWakingUpNotice, 2500);

// Keyboard shortcuts (Step 5): 1-5 switch views, S starts/stops a surge.
// Ignored while typing in a field, and with any modifier held, so this
// never fights normal browser/OS shortcuts or text entry. Reset Demo
// deliberately has no shortcut of its own — it's destructive and already
// requires a confirmation click.
const TAB_SHORTCUT_ORDER = ["live-ops", "patients", "network", "impact", "how-it-works"];
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const target = e.target;
  const isTyping =
    target &&
    (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
  if (isTyping) return;

  if (e.key >= "1" && e.key <= "5") {
    const tab = TAB_SHORTCUT_ORDER[Number(e.key) - 1];
    const button = document.querySelector(`.tab-button[data-tab="${tab}"]`);
    if (button) {
      e.preventDefault();
      button.click();
    }
  } else if (e.key === "s" || e.key === "S") {
    e.preventDefault();
    document.getElementById("btn-surge").click();
  }
});

// Narrow-screen notice (Step 5): WayPoint's layout is desktop-first: below
// 900px the console grids stack into a single column instead of
// breaking or scrolling horizontally, but it's still worth telling the
// user this is a desktop-designed app. Dismissing it is remembered for
// the rest of the browser session (sessionStorage), not just this page
// load, but reappears in a fresh tab/session — it's a notice, not a
// permanent setting.
const NARROW_BANNER_DISMISSED_KEY = "edflow-narrow-banner-dismissed";
const narrowBanner = document.getElementById("narrow-screen-banner");
try {
  if (narrowBanner && sessionStorage.getItem(NARROW_BANNER_DISMISSED_KEY) === "1") {
    narrowBanner.classList.add("dismissed");
  }
} catch (e) {
  // sessionStorage can throw in some private-browsing modes — the banner
  // just won't remember being dismissed, which is a harmless fallback.
}
document.getElementById("dismiss-narrow-banner").addEventListener("click", () => {
  if (narrowBanner) narrowBanner.classList.add("dismissed");
  try {
    sessionStorage.setItem(NARROW_BANNER_DISMISSED_KEY, "1");
  } catch (e) {
    // ignore — see above
  }
});
