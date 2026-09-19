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
          ? "⏱ Tier 1-2 breach projected imminently"
          : `⏱ Tier 1-2 breach projected in ~${rec.projected_breach_minutes} min`;
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

function renderEventLog(containerId, events) {
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

  events.forEach((event) => container.appendChild(createEventLogRow(event)));
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

  renderImpactComparison(data.impact);
}

// Impact of EDFlow: the with/without-EDFlow comparison card. Uses the
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
      ? `EDFlow reduced Tier 1-2 breach time by ${impact.headline.breach_pct_reduction}% and saved ` +
        `${impact.headline.patient_minutes_saved} patient-minutes of waiting.`
      : `EDFlow saved ${impact.headline.patient_minutes_saved} patient-minutes of waiting ` +
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
        "EDFlow and the baseline are identical so far."
    );
  } else {
    const impact = summary.impact;
    lines.push(
      impact.without_edflow.tier12_breach_minutes > 0
        ? `EDFlow reduced Tier 1-2 breach time by ${impact.headline.breach_pct_reduction}% and saved ` +
          `${impact.headline.patient_minutes_saved} patient-minutes of waiting.`
        : `EDFlow saved ${impact.headline.patient_minutes_saved} patient-minutes of waiting ` +
          `(no Tier 1-2 breaches occurred in either scenario).`
    );
    lines.push("");
    lines.push("  Metric                          Without EDFlow   With EDFlow   Difference");
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
          "EDFlow and the baseline are identical so far."
      )
    );
    return fragment;
  }

  const headlineText =
    impact.without_edflow.tier12_breach_minutes > 0
      ? `EDFlow reduced Tier 1-2 breach time by ${impact.headline.breach_pct_reduction}% and saved ` +
        `${impact.headline.patient_minutes_saved} patient-minutes of waiting.`
      : `EDFlow saved ${impact.headline.patient_minutes_saved} patient-minutes of waiting ` +
        `(no Tier 1-2 breaches occurred in either scenario).`;
  fragment.appendChild(el("p", "impact-headline", headlineText));

  const table = document.createElement("table");
  table.className = "breach-table impact-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Metric", "Without EDFlow", "With EDFlow", "Difference"].forEach((h) => headRow.appendChild(el("th", null, h)));
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
  outcome.appendChild(el("span", "report-outcome-icon", recovered ? "✓" : "⚠"));
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

  const impactHeading = el("h3", null, "Impact of EDFlow ");
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
  button.textContent = "⏹ Stop Surge";
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
  button.textContent = "🚨 Run Surge";
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
