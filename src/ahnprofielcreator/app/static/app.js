"use strict";

// ---------- Hulpfuncties ----------

const LABELS = {
  cur_dir: "Werkmap (output)",
  input_line: "Dijkvakken (lijnen .shp)",
  input_line_outside: "Buitenlijn (.shp)",
  ahn_index: "AHN kaartbladen (.shp)",
  ahn_file_loc: "Map met AHN tiffs",
  group_id: "Attribuut vak-id",
  ahn_index_id: "Attribuut bladnaam",
  interval_length: "Interval [m]",
  cross_length: "Lengte dwarslijn [m]",
  ahn_stepsize: "Stapgrootte AHN [m]",
  ahn_file_prefix: "Prefix AHN tiff",
  ahn_file_suffix: "Suffix AHN tiff",
  ahn_file_transform: "Bladnaam omzetten (lower/upper)",
};
const ADVANCED = ["group_id", "ahn_index_id", "ahn_file_prefix", "ahn_file_suffix", "ahn_file_transform"];
const FILE_LABELS = {
  vakken: "Dijkvakken",
  buitenlijn: "Buitenlijn",
  punten: "Intervalpunten",
  dwarslijnen: "Dwarslijnen",
  profielen: "AHN-profielen",
};

const $ = (id) => document.getElementById(id);

async function api(url, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function vakColor(vak) {
  const i = state.vakIndex.get(vak) ?? 0;
  return `hsl(${(i * 137.508) % 360}, 65%, 42%)`;
}

// 0 = volledige AHN-dekking (groen), 1 = geen hoogtes (rood).
function nodataColor(fraction) {
  if (fraction === null || fraction === undefined) return "#98a2b3";
  const stops = [[26, 152, 80], [254, 224, 139], [215, 48, 39]];
  const t = Math.min(Math.max(fraction, 0), 1) * 2;
  const [a, b] = t <= 1 ? [stops[0], stops[1]] : [stops[1], stops[2]];
  const f = t <= 1 ? t : t - 1;
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
}

// ---------- Toestand ----------

const state = {
  settings: null,
  layers: {},
  vakIndex: new Map(),
  colorMode: "vak",
  profiles: new Map(), // vak -> {outside, profiles}
  selected: null, // {vak, index}
};

// ---------- Kaart ----------

const map = L.map("map", { preferCanvas: true }).setView([52.2, 5.3], 8);
const renderer = L.canvas({ tolerance: 6 });
const pdok = "https://service.pdok.nl";
const attribution = '&copy; <a href="https://www.pdok.nl">PDOK</a> / Kadaster';
const baseLayers = {
  "BRT grijs": L.tileLayer(`${pdok}/brt/achtergrondkaart/wmts/v2_0/grijs/EPSG:3857/{z}/{x}/{y}.png`, { maxZoom: 19, attribution }),
  "BRT kleur": L.tileLayer(`${pdok}/brt/achtergrondkaart/wmts/v2_0/standaard/EPSG:3857/{z}/{x}/{y}.png`, { maxZoom: 19, attribution }),
  "Luchtfoto": L.tileLayer(`${pdok}/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg`, { maxZoom: 21, attribution }),
  "Geen achtergrond": L.layerGroup(),
};
baseLayers["BRT grijs"].addTo(map);
const ahnLayer = L.tileLayer.wms(`${pdok}/rws/ahn/wms/v1_0`, {
  layers: "dtm_05m", format: "image/png", transparent: true, opacity: 0.6, maxZoom: 21,
  attribution: "AHN &copy; Rijkswaterstaat",
});

const dataLayers = {
  vakken: L.layerGroup().addTo(map),
  buitenlijn: L.layerGroup().addTo(map),
  dwarslijnen: L.layerGroup().addTo(map),
  punten: L.layerGroup(),
  overgeslagen: L.layerGroup().addTo(map),
};
const highlight = L.layerGroup().addTo(map);

L.control.layers(baseLayers, {
  "AHN DTM (PDOK)": ahnLayer,
  "Dijkvakken": dataLayers.vakken,
  "Buitenlijn": dataLayers.buitenlijn,
  "Dwarslijnen": dataLayers.dwarslijnen,
  "Intervalpunten": dataLayers.punten,
  "Punten zonder dwarslijn": dataLayers.overgeslagen,
}, { collapsed: false }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);

function crossStyle(feature) {
  const p = feature.properties;
  const color = state.colorMode === "nodata" ? nodataColor(p.nodata) : vakColor(p.vak);
  return { color, weight: 2, opacity: 0.9 };
}

function drawLayers() {
  for (const group of Object.values(dataLayers)) group.clearLayers();
  const { vakken, buitenlijn, punten, dwarslijnen } = state.layers;

  if (vakken) {
    L.geoJSON(vakken, {
      renderer,
      style: (f) => ({ color: vakColor(f.properties.vak), weight: 5, opacity: 0.8 }),
      onEachFeature: (f, layer) => {
        layer.bindTooltip(`Vak ${escapeHtml(f.properties.vak)}`, { sticky: true });
        layer.on("click", () => selectVak(f.properties.vak));
      },
    }).addTo(dataLayers.vakken);
  }
  if (buitenlijn) {
    L.geoJSON(buitenlijn, {
      renderer,
      style: { color: "#344054", weight: 2, dashArray: "6 4", opacity: 0.9 },
      onEachFeature: (f, layer) => layer.bindTooltip(`Buitenlijn ${escapeHtml(f.properties.vak)}`, { sticky: true }),
    }).addTo(dataLayers.buitenlijn);
  }
  if (dwarslijnen) {
    L.geoJSON(dwarslijnen, {
      renderer,
      style: crossStyle,
      onEachFeature: (f, layer) => {
        const p = f.properties;
        const nodata = p.nodata === null || p.nodata === undefined ? "" : `<br>zonder AHN: ${Math.round(p.nodata * 100)}%`;
        layer.bindTooltip(`Vak ${escapeHtml(p.vak)} – dwarslijn ${p.id}${nodata}`, { sticky: true });
        layer.on("click", () => selectProfile(p.vak, p.id));
      },
    }).addTo(dataLayers.dwarslijnen);
  }
  if (punten) {
    for (const f of punten.features) {
      const [lon, lat] = f.geometry.coordinates;
      const p = f.properties;
      if (p.skipped) {
        L.circleMarker([lat, lon], { renderer, radius: 5, color: "#c62828", weight: 2, fillColor: "#fff", fillOpacity: 1 })
          .bindTooltip(`Vak ${escapeHtml(p.vak)} – punt ${p.id}<br>geen dwarslijn (geen snijpunt met buitenlijn)`)
          .addTo(dataLayers.overgeslagen);
      } else {
        L.circleMarker([lat, lon], { renderer, radius: 3, color: vakColor(p.vak), weight: 1, fillOpacity: 0.8 })
          .bindTooltip(`Vak ${escapeHtml(p.vak)} – punt ${p.id}`)
          .addTo(dataLayers.punten);
      }
    }
  }
  drawLegend();
  drawHighlight();
}

function restyleCross() {
  dataLayers.dwarslijnen.eachLayer((geojson) => geojson.setStyle(crossStyle));
  drawLegend();
}

function drawLegend() {
  const el = $("legend");
  if (state.colorMode === "nodata") {
    el.innerHTML = `<div>Aandeel punten zonder AHN-hoogte</div><div class="bar"></div><div class="ticks"><span>0%</span><span>50%</span><span>100%</span></div>`;
  } else {
    el.innerHTML = "";
  }

  const { punten, dwarslijnen } = state.layers;
  const skipped = punten ? punten.features.filter((f) => f.properties.skipped).length : 0;
  const counts = [
    [state.vakIndex.size, "vakken"],
    [punten && punten.features.length, "intervalpunten"],
    [dwarslijnen && dwarslijnen.features.length, "dwarslijnen"],
    [punten && skipped, "punten zonder dwarslijn"],
  ];
  $("counts").innerHTML = counts
    .filter(([n]) => n !== null && n !== undefined)
    .map(([n, label]) => `<li>${n} ${label}</li>`)
    .join("");
}

function drawHighlight() {
  highlight.clearLayers();
  const profile = currentProfile();
  if (!profile || !state.layers.dwarslijnen) return;
  const feature = state.layers.dwarslijnen.features.find((f) => f.properties.id === profile.id);
  if (!feature) return;
  const layer = L.geoJSON(feature, { renderer, interactive: false, style: { color: "#111", weight: 6, opacity: 0.9 } });
  highlight.addLayer(layer);
  L.geoJSON(feature, { renderer, interactive: false, style: { color: "#ffd400", weight: 3 } }).addTo(highlight);
  const bounds = layer.getBounds();
  if (!map.getBounds().contains(bounds)) map.panTo(bounds.getCenter());
}

function fitToData() {
  const bounds = L.latLngBounds([]);
  for (const name of ["vakken", "buitenlijn", "dwarslijnen"]) {
    if (state.layers[name]) bounds.extend(L.geoJSON(state.layers[name]).getBounds());
  }
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20] });
}

function zoomToVak(vak) {
  const bounds = L.latLngBounds([]);
  for (const name of ["vakken", "dwarslijnen"]) {
    const layer = state.layers[name];
    if (!layer) continue;
    const features = layer.features.filter((f) => f.properties.vak === vak);
    if (features.length) bounds.extend(L.geoJSON({ type: "FeatureCollection", features }).getBounds());
  }
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30] });
}

// ---------- Instellingen, bestanden en berekening ----------

function renderSettings(info) {
  state.settings = info;
  const main = $("fields-main");
  const advanced = $("fields-advanced");
  main.innerHTML = "";
  advanced.innerHTML = "";
  for (const field of info.fields) {
    const div = document.createElement("div");
    div.className = "field";
    const id = `field-${field.name}`;
    const req = field.required ? ' <span class="req">*</span>' : "";
    div.innerHTML = `<label for="${id}">${escapeHtml(LABELS[field.name] || field.name)}${req}</label>`;
    const input = document.createElement("input");
    input.id = id;
    input.name = field.name;
    input.type = field.number ? "number" : "text";
    if (field.number) input.step = "any";
    input.value = field.value ?? "";
    input.placeholder = field.default ?? "";
    input.title = input.value;
    div.appendChild(input);
    (ADVANCED.includes(field.name) ? advanced : main).appendChild(div);
  }
  renderFiles(info.files);
}

function renderFiles(files) {
  $("files").innerHTML = Object.entries(FILE_LABELS).map(([key, label]) => {
    const file = files[key];
    const status = !file.path ? "missing" : file.exists ? "ok" : "missing";
    const mark = file.exists ? "✓" : "✗";
    const title = file.path ? escapeHtml(file.path) : "niet ingesteld";
    return `<li class="${status}" title="${title}"><span class="mark">${mark}</span><span class="name">${label}</span></li>`;
  }).join("");
}

function formValues() {
  const values = {};
  for (const input of $("settings-form").querySelectorAll("input")) values[input.name] = input.value;
  return values;
}

async function applySettings() {
  renderSettings(await api("/api/settings", formValues()));
  await loadLayers();
}

async function loadLayers() {
  const data = await api("/api/layers");
  state.layers = data.layers;
  state.profiles.clear();

  const vakken = new Set();
  for (const layer of Object.values(state.layers)) {
    if (layer) for (const f of layer.features) if (f.properties.vak !== "") vakken.add(f.properties.vak);
  }
  const sorted = [...vakken].sort((a, b) => a.localeCompare(b, "nl", { numeric: true }));
  state.vakIndex = new Map(sorted.map((v, i) => [v, i]));
  $("vak-select").innerHTML = '<option value="">–</option>' +
    sorted.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");

  drawLayers();
  if (state.selected) {
    await selectVak(state.selected.vak, state.selected.index);
  } else {
    drawChart();
  }
}

function setRunStatus(run) {
  const status = $("run-status");
  const log = $("run-log");
  const labels = { running: "Berekening loopt…", done: "Berekening klaar.", error: run.message };
  status.hidden = run.state === "idle";
  status.className = `status ${run.state}`;
  status.textContent = labels[run.state] || "";
  log.hidden = run.log.length === 0;
  log.textContent = run.log.join("\n");
  log.scrollTop = log.scrollHeight;
  $("btn-run").disabled = run.state === "running";
  $("btn-apply").disabled = run.state === "running";
}

async function startRun() {
  try {
    renderSettings(await api("/api/settings", formValues()));
    setRunStatus(await api("/api/run", {}));
  } catch (e) {
    setRunStatus({ state: "error", message: e.message, log: [] });
    return;
  }
  await pollRun();
}

async function pollRun() {
  const run = await api("/api/run");
  setRunStatus(run);
  if (run.state === "running") {
    setTimeout(pollRun, 1000);
  } else if (run.state === "done") {
    renderSettings(await api("/api/settings"));
    await loadLayers();
  }
}

// ---------- Profielen ----------

async function loadProfiles(vak) {
  if (!state.profiles.has(vak)) {
    try {
      state.profiles.set(vak, await api(`/api/profiles?vak=${encodeURIComponent(vak)}`));
    } catch (e) {
      state.profiles.set(vak, { error: e.message, profiles: [] });
    }
  }
  return state.profiles.get(vak);
}

function currentProfile() {
  if (!state.selected) return null;
  const data = state.profiles.get(state.selected.vak);
  return data ? data.profiles[state.selected.index] || null : null;
}

async function selectVak(vak, index = 0) {
  state.selected = { vak, index };
  $("vak-select").value = vak;
  const data = await loadProfiles(vak);
  state.selected.index = Math.min(index, Math.max(data.profiles.length - 1, 0));
  drawChart();
  drawHighlight();
}

async function selectProfile(vak, id) {
  const data = await loadProfiles(vak);
  const index = data.profiles.findIndex((p) => p.id === id);
  await selectVak(vak, Math.max(index, 0));
}

function step(delta) {
  if (!state.selected) return;
  const data = state.profiles.get(state.selected.vak);
  if (!data || !data.profiles.length) return;
  state.selected.index = (state.selected.index + delta + data.profiles.length) % data.profiles.length;
  drawChart();
  drawHighlight();
}

function niceStep(range, target) {
  const raw = range / target;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / magnitude;
  return (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * magnitude;
}

function ticks(min, max, target) {
  const s = niceStep(max - min || 1, target);
  const result = [];
  for (let v = Math.ceil(min / s) * s; v <= max + 1e-9; v += s) result.push(+v.toFixed(6));
  return result;
}

// SVG-pad met onderbrekingen waar geen hoogte is (nodata).
function pathData(profile, x, y) {
  let d = "";
  let pen = false;
  for (let i = 0; i < profile.d.length; i++) {
    const z = profile.z[i];
    if (z === null) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${x(profile.d[i]).toFixed(1)},${y(z).toFixed(1)}`;
    pen = true;
  }
  return d;
}

function drawChart() {
  const el = $("chart");
  const title = $("profile-title");
  $("chart-tip").hidden = true;

  if (!state.selected) {
    title.textContent = "Klik op een dwarslijn in de kaart om het profiel te zien.";
    el.innerHTML = state.layers.dwarslijnen ? "" : '<div class="message">Nog geen dwarslijnen voor deze instellingen.</div>';
    return;
  }
  const { vak, index } = state.selected;
  const data = state.profiles.get(vak);
  if (!data || data.error || !data.profiles.length) {
    title.textContent = `Vak ${vak}`;
    el.innerHTML = `<div class="message">${escapeHtml(data && data.error ? data.error : "Geen profielen in dit vak.")}</div>`;
    return;
  }

  const profile = data.profiles[index];
  title.textContent = `Vak ${vak} – dwarslijn ${profile.id} (${index + 1} van ${data.profiles.length})`;

  const shown = $("show-others").checked ? data.profiles : [profile];
  let dMin = Infinity, dMax = -Infinity, zMin = Infinity, zMax = -Infinity;
  for (const p of shown) {
    for (let i = 0; i < p.d.length; i++) {
      dMin = Math.min(dMin, p.d[i]);
      dMax = Math.max(dMax, p.d[i]);
      if (p.z[i] !== null) { zMin = Math.min(zMin, p.z[i]); zMax = Math.max(zMax, p.z[i]); }
    }
  }
  if (!isFinite(zMin)) { zMin = 0; zMax = 1; }
  const zPad = Math.max((zMax - zMin) * 0.08, 0.25);
  zMin -= zPad; zMax += zPad;

  const width = el.clientWidth;
  const height = el.clientHeight;
  const m = { left: 52, right: 16, top: 14, bottom: 36 };
  const x = (d) => m.left + ((d - dMin) / (dMax - dMin || 1)) * (width - m.left - m.right);
  const y = (z) => height - m.bottom - ((z - zMin) / (zMax - zMin)) * (height - m.top - m.bottom);
  const xInv = (px) => dMin + ((px - m.left) / (width - m.left - m.right)) * (dMax - dMin);

  const xt = ticks(dMin, dMax, Math.max(width / 90, 2));
  const yt = ticks(zMin, zMax, Math.max(height / 50, 2));
  const color = vakColor(vak);
  const others = shown.filter((p) => p !== profile).map((p) => `<path class="other" d="${pathData(p, x, y)}"/>`).join("");
  const sides = data.outside
    ? `<text class="axis-label" x="${m.left + 4}" y="${m.top + 12}">← buiten</text>
       <text class="axis-label" x="${width - m.right - 4}" y="${m.top + 12}" text-anchor="end">binnen →</text>`
    : "";

  el.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}">
      <g class="grid">
        ${xt.map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${m.top}" y2="${height - m.bottom}"/>`).join("")}
        ${yt.map((t) => `<line x1="${m.left}" x2="${width - m.right}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}
      </g>
      <g class="axis">
        ${xt.map((t) => `<text x="${x(t)}" y="${height - m.bottom + 15}" text-anchor="middle">${t}</text>`).join("")}
        ${yt.map((t) => `<text x="${m.left - 6}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join("")}
      </g>
      <text class="axis-label" x="${(m.left + width - m.right) / 2}" y="${height - 4}" text-anchor="middle">afstand tot dijklijn [m]</text>
      <text class="axis-label" transform="translate(13 ${(m.top + height - m.bottom) / 2}) rotate(-90)" text-anchor="middle">hoogte [m NAP]</text>
      ${dMin < 0 && dMax > 0 ? `<line class="zero" x1="${x(0)}" x2="${x(0)}" y1="${m.top}" y2="${height - m.bottom}"/>` : ""}
      ${sides}
      ${others}
      <path class="selected" stroke="${color}" d="${pathData(profile, x, y)}"/>
      <line class="cursor" y1="${m.top}" y2="${height - m.bottom}" visibility="hidden"/>
      <circle class="dot" r="4" fill="${color}" visibility="hidden"/>
      <rect x="${m.left}" y="${m.top}" width="${width - m.left - m.right}" height="${height - m.top - m.bottom}" fill="transparent"/>
    </svg>`;

  // Hover: waarde van het geselecteerde profiel op de cursorpositie.
  const svg = el.querySelector("svg");
  const cursor = svg.querySelector(".cursor");
  const dot = svg.querySelector(".dot");
  const tip = $("chart-tip");
  svg.addEventListener("mousemove", (event) => {
    const box = svg.getBoundingClientRect();
    const d = xInv(event.clientX - box.left);
    let best = 0;
    for (let i = 1; i < profile.d.length; i++) {
      if (Math.abs(profile.d[i] - d) < Math.abs(profile.d[best] - d)) best = i;
    }
    const z = profile.z[best];
    cursor.setAttribute("x1", x(profile.d[best]));
    cursor.setAttribute("x2", x(profile.d[best]));
    cursor.setAttribute("visibility", "visible");
    if (z !== null) {
      dot.setAttribute("cx", x(profile.d[best]));
      dot.setAttribute("cy", y(z));
    }
    dot.setAttribute("visibility", z === null ? "hidden" : "visible");
    tip.hidden = false;
    tip.textContent = `${profile.d[best].toFixed(1)} m: ${z === null ? "geen AHN" : `${z.toFixed(2)} m NAP`}`;
    const panel = $("profile-panel").getBoundingClientRect();
    tip.style.left = `${Math.min(event.clientX - panel.left + 12, panel.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${event.clientY - panel.top - 28}px`;
  });
  svg.addEventListener("mouseleave", () => {
    cursor.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tip.hidden = true;
  });
}

// ---------- Gebeurtenissen ----------

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await applySettings();
  } catch (e) {
    setRunStatus({ state: "error", message: e.message, log: [] });
  }
});
$("btn-run").addEventListener("click", startRun);
$("btn-prev").addEventListener("click", () => step(-1));
$("btn-next").addEventListener("click", () => step(1));
$("show-others").addEventListener("change", drawChart);
$("color-mode").addEventListener("change", (event) => {
  state.colorMode = event.target.value;
  restyleCross();
});
$("vak-select").addEventListener("change", (event) => {
  const vak = event.target.value;
  if (!vak) return;
  zoomToVak(vak);
  selectVak(vak);
});
document.addEventListener("keydown", (event) => {
  if (event.target.closest?.("input:not([type=checkbox]), select, textarea")) return;
  if (event.key === "ArrowLeft") step(-1);
  if (event.key === "ArrowRight") step(1);
});
new ResizeObserver(() => drawChart()).observe($("chart"));

(async function init() {
  try {
    renderSettings(await api("/api/settings"));
    await loadLayers();
    fitToData();
    const run = await api("/api/run");
    setRunStatus(run);
    if (run.state === "running") pollRun();
  } catch (e) {
    setRunStatus({ state: "error", message: e.message, log: [] });
  }
})();
