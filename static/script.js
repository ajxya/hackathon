// EDFlow dashboard frontend — fetches /state and fills in the page.

const POLL_INTERVAL_MS = 2000;
let fetchInProgress = false;
let surgeInterval = null;

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

const STATUS_COLORS = { green: "#1b8a5a", yellow: "#b8860b", red: "#c0392b" };

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
  document.getElementById(id).textContent = value;
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

    const savings = document.createElement("div");
    savings.className = "recommendation-savings";
    savings.textContent =
      rec.estimated_wait_reduction_minutes > 0
        ? `Est. wait reduction: ~${rec.estimated_wait_reduction_minutes} min`
        : "No action needed";

    item.appendChild(action);
    item.appendChild(reason);
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

function renderPatientsList(patients) {
  setText("patients-count", patients.length);

  const container = document.getElementById("patients-list");
  container.innerHTML = "";

  patients.forEach((p) => {
    const row = document.createElement("div");
    row.className = "patient-row" + (p.overdue ? " overdue" : "");

    const main = document.createElement("div");
    main.className = "patient-row-main";

    const nameEl = document.createElement("strong");
    nameEl.textContent = p.name;

    const tierEl = document.createElement("span");
    tierEl.className = `tier-badge tier-${p.tier}`;
    tierEl.textContent = `Tier ${p.tier}`;

    main.appendChild(nameEl);
    main.appendChild(tierEl);

    const detail = document.createElement("div");
    detail.className = "patient-row-detail";
    const sourceLabel = p.source === "ambulance" ? "🚑 Ambulance" : "🚶 Walk-in";
    detail.textContent = `${p.injury} · ${sourceLabel} · ${p.status_label}`;

    const wait = document.createElement("div");
    wait.className = "patient-row-wait" + (p.overdue ? " overdue-text" : "");
    wait.textContent = `${p.wait_minutes} min wait (target: ${p.target_minutes} min)`;

    row.appendChild(main);
    row.appendChild(detail);
    row.appendChild(wait);
    container.appendChild(row);
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

  L.circleMarker([homeCoords.lat, homeCoords.lng], {
    radius: 10,
    color: "#0b3d91",
    fillColor: "#0b3d91",
    fillOpacity: 1,
    weight: 2,
  })
    .addTo(networkMap)
    .bindPopup("<strong>EDFlow Emergency Department</strong><br>Home ED (you are here)");
}

function renderNetworkMarkers(facilities) {
  if (!networkMap) return;
  facilities.forEach((f) => {
    const color = STATUS_COLORS[f.status] || "#6b7280";
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
    `${f.travel_time_minutes} min away (${f.distance_miles} mi) · ${f.capabilities.join(", ")}`;

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

function renderEventLog(events) {
  const container = document.getElementById("event-log-list");
  if (!container) return; // only present on the Patients tab
  container.innerHTML = "";

  if (!events || events.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No events yet — run a surge to see status changes and applied actions here.";
    container.appendChild(empty);
    return;
  }

  events.forEach((event) => {
    const row = document.createElement("div");
    row.className = "event-log-row";

    const time = document.createElement("span");
    time.className = "event-log-time";
    time.textContent = event.time;

    const message = document.createElement("span");
    message.className = "event-log-message";
    message.textContent = event.message;

    row.appendChild(time);
    row.appendChild(message);
    container.appendChild(row);
  });
}

function drawTrendChart(canvasId, dataPoints, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  if (dataPoints.length < 2) {
    ctx.fillStyle = "#9ca3af";
    ctx.font = "13px sans-serif";
    ctx.fillText("Collecting data — leave this tab open during a surge…", 10, height / 2);
    return;
  }

  const realMax = Math.max(...dataPoints);
  const divisor = realMax || 1; // avoid divide-by-zero without lying about the peak
  const stepX = width / (dataPoints.length - 1);
  const topPadding = 10;

  ctx.beginPath();
  dataPoints.forEach((value, i) => {
    const x = i * stepX;
    const y = height - (value / divisor) * (height - topPadding);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.lineTo(width, height);
  ctx.lineTo(0, height);
  ctx.closePath();
  ctx.fillStyle = color + "26"; // ~15% opacity fill under the line
  ctx.fill();

  // Label the peak so the chart is readable without hovering.
  ctx.fillStyle = "#374151";
  ctx.font = "11px sans-serif";
  ctx.fillText(`peak: ${realMax}`, width - 70, 12);
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

  drawTrendChart("chart-wait", waitHistory, "#0b3d91");
  drawTrendChart("chart-breaches", breachHistory, "#c0392b");
}

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
  renderEventLog(data.event_log);
  renderImpactTab(data);

  latestBreachSummary = data.breach_summary;
  latestStatusLevel = data.status.level;
  updateNearbyHospitalsPanel();
}

async function fetchState() {
  // Skip this tick if a previous fetch is still in flight (e.g. a slow
  // network), so requests don't pile up on top of each other.
  if (fetchInProgress) return;
  fetchInProgress = true;
  try {
    const res = await fetch("/state");
    const data = await res.json();
    render(data);
  } finally {
    fetchInProgress = false;
  }
}

async function postAndRefresh(endpoint) {
  await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  await fetchState();
}

async function runSurgeTick() {
  try {
    await postAndRefresh("/simulate/tick");
  } catch (e) {
    console.error("Surge tick failed", e);
  }
}

function startSurge() {
  if (surgeInterval) return; // already running

  const button = document.getElementById("btn-surge");
  button.textContent = "⏹ Stop Surge";
  button.classList.add("surge-active");

  runSurgeTick(); // first tick immediately, don't wait a full second
  surgeInterval = setInterval(runSurgeTick, 1000);
}

function stopSurge() {
  // The only "background timer" in this whole app is this browser-side
  // interval — the backend has no persistent timers of its own, it just
  // computes discharges/placements lazily whenever a request comes in.
  // So cancelling a surge is entirely a client-side concern: clear the
  // interval so no further ticks fire, and restore the button.
  if (surgeInterval) {
    clearInterval(surgeInterval);
    surgeInterval = null;
  }
  const button = document.getElementById("btn-surge");
  button.classList.remove("surge-active");
  button.textContent = "🚨 Run Surge";
}

// The surge is a manual toggle, not a timed run: click to start it, click
// again whenever you want to stop it, as many times as you like.
document.getElementById("btn-surge").addEventListener("click", () => {
  if (surgeInterval) {
    stopSurge();
  } else {
    startSurge();
  }
});

document.getElementById("btn-reset").addEventListener("click", async (e) => {
  stopSurge();
  waitHistory = [];
  breachHistory = [];
  e.currentTarget.disabled = true;
  try {
    await postAndRefresh("/demo/reset");
  } finally {
    e.currentTarget.disabled = false;
  }
});

document.getElementById("toggle-patients").addEventListener("click", () => {
  document.getElementById("patients-list-container").classList.toggle("hidden");
});

document.getElementById("btn-apply-all").addEventListener("click", async (e) => {
  e.currentTarget.disabled = true;
  e.currentTarget.textContent = "Applying…";
  try {
    await applyAllRecommendations();
  } finally {
    e.currentTarget.disabled = false;
    e.currentTarget.textContent = "Apply All";
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

    // Leaflet measures its container on creation, which fails silently if
    // the tab was hidden (display:none) at the time — so (re-)init and
    // force a resize check now that the container is actually visible.
    if (button.dataset.tab === "network") {
      initNetworkMap();
      if (networkMap) setTimeout(() => networkMap.invalidateSize(), 0);
    }
  });
});

document.getElementById("modal-close").addEventListener("click", closeHospitalModal);

// Clicking the dark overlay itself (not the modal card) closes it too.
document.getElementById("hospital-detail-modal").addEventListener("click", (e) => {
  if (e.target.id === "hospital-detail-modal") closeHospitalModal();
});

fetchState();
fetchHospitals();
setInterval(fetchState, POLL_INTERVAL_MS);
setInterval(fetchHospitals, POLL_INTERVAL_MS);
