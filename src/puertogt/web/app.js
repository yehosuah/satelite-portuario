"use strict";

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const labels = {
  pending: "Pendiente", processing: "Procesando", usable: "Utilizable", partial: "Parcial",
  unusable: "No observable", failed: "Falló", provisional: "Provisional", reviewed: "Revisado",
  rejected: "Rechazado", candidate: "Candidato", persistent: "Persistente", reverted: "Revertido",
};
const state = {
  ports: new Map(), zones: new Map(), observations: [], observation: null, cursor: null, proposals: [], changes: [],
  selected: null, mode: "detections", tool: "select", token: "", author: "", busy: false,
  asset: null, bounds: null, drag: null, generation: 0, loading: false, reviewerKind: "human_visual_review",
  coverageGeneration: 0,
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function notify(message, error = false) {
  $("feedback").hidden = false;
  $("feedback").classList.toggle("error", error);
  $("feedback").textContent = message;
}

function savedState() {
  try { return JSON.parse(localStorage.getItem("puertogt.selection") || "{}"); }
  catch { return {}; }
}

function persistSelection() {
  try {
    localStorage.setItem("puertogt.selection", JSON.stringify({
      observationId: state.observation?.observation_id,
      port: $("port-filter").value, sensor: $("sensor-filter").value,
      start: $("start-filter").value, end: $("end-filter").value,
    }));
  } catch { /* Reading and reviewing remain available without local storage. */ }
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.method) {
    headers["Content-Type"] = "application/json";
    headers.Authorization = `Bearer ${state.token}`;
  }
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { detail = ""; }
    const error = new Error(typeof detail === "string" ? detail : `Respuesta ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function explain(error) {
  if (error.status === 401) {
    state.token = "";
    $("auth-panel").hidden = false;
    updateControls();
    return "La credencial fue rechazada. Introduce una credencial local válida.";
  }
  if (error.status === 409) return "Otra revisión modificó esta propuesta. La corrección no se guardó. Actualiza la escena antes de volver a revisar; el formulario actual sigue visible.";
  if (error.status === 503) return "La revisión no está habilitada en este servicio. Configura la credencial local del servidor.";
  if (error.status === 422) return `No se guardó: ${error.message}`;
  return error.status ? `No se pudo completar la acción (${error.status}): ${error.message}` : "No se pudo conectar con el servicio local. Comprueba que siga en ejecución y actualiza.";
}

function date(value, compact = false) {
  if (!value) return "Fecha no disponible";
  return new Intl.DateTimeFormat("es-GT", {
    timeZone: "UTC", year: compact ? undefined : "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(value)) + " UTC";
}

async function loadSystemStatus() {
  const button = $("refresh-system");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const result = await api("/v1/system");
    const number = (value) => new Intl.NumberFormat("es-GT").format(value);
    const reviews = result.reviews.states.reviewed || 0;
    const validated = result.model_tasks.filter((task) => task.local_validation_declared).length;
    const campaigns = result.history.filter((campaign) => campaign.readable);
    const completed = campaigns.reduce((n, campaign) => n + campaign.complete, 0);
    const total = campaigns.reduce((n, campaign) => n + campaign.total, 0);
    const unreadable = result.history.length - campaigns.length;
    const historySummary = total ? `${number(completed)}/${number(total)} procesadas en campañas` : (result.history.length ? "Campañas sin datos consultables" : "Sin campaña histórica");
    $("system-summary").textContent = `${historySummary}${unreadable ? ` · ${unreadable} sin estado disponible` : ""} · ${validated}/${result.model_tasks.length} tareas con validación local registrada`;
    $("system-cards").replaceChildren();
    const cards = [
      ["Catálogo", number(result.catalog_acquisitions), "Adquisiciones descubiertas, incluido contexto radar"],
      ["Observaciones", number(result.observations.total), "Cada zona y receta cuenta por separado"],
      ["Revisadas", number(reviews), `${number(result.reviews.complete_scopes)} con ámbito revisado completo`],
      ["Validación local", `${validated} / ${result.model_tasks.length}`, "Declarada por el manifiesto; no recalcula precisión"],
    ];
    for (const [label, value, note] of cards) {
      const card = node("div", undefined, "system-card");
      card.append(node("span", label), node("strong", value), node("p", note));
      $("system-cards").append(card);
    }
    $("history-status").replaceChildren();
    for (const campaign of result.history) {
      const row = node("div", undefined, "history-row");
      row.append(node("strong", campaign.campaign));
      if (!campaign.readable) {
        row.append(node("p", "No se pudo consultar esta campaña. Su estado no se considera cero."));
      } else {
        row.append(node("p", `${number(campaign.complete)} de ${number(campaign.total)} procesadas · ${number(campaign.outstanding)} por resolver · ${number(campaign.unavailable)} sin fuente compatible`));
        const progress = node("progress");
        progress.max = campaign.total || 1; progress.value = campaign.complete;
        progress.setAttribute("aria-label", `Adquisiciones procesadas de ${campaign.campaign}`);
        row.append(progress);
        const activity = campaign.last_activity_at ? `Última actividad registrada: ${date(campaign.last_activity_at)}.` : "Sin actividad registrada.";
        row.append(node("p", `${campaign.pause_requested ? "Pausa solicitada. " : ""}${activity} ${campaign.counts.failed ? `${campaign.counts.failed} agotaron sus reintentos. ` : ""}El registro no confirma que el trabajador siga ejecutándose.`, "quiet"));
      }
      $("history-status").append(row);
    }
    $("system-updated").textContent = `Consultado: ${date(result.generated_at)}`;
    $("system-error").hidden = true;
  } catch (error) {
    $("system-summary").textContent = "Estado sin actualizar";
    $("system-error").textContent = "No se pudo actualizar el estado. Los datos anteriores, si aparecen, pueden estar desactualizados.";
    $("system-error").hidden = false;
  } finally { button.disabled = false; }
}

function filterParams() {
  const params = new URLSearchParams({ limit: "100" });
  for (const [input, key] of [["port-filter", "port_id"], ["sensor-filter", "sensor"], ["start-filter", "start"], ["end-filter", "end"]]) {
    const value = $(input).value;
    if (value) params.set(key, input.includes("start") || input.includes("end") ? `${value}T00:00:00Z` : value);
  }
  return params;
}

function coverageTable(items, byZone = false) {
  const table = node("table", undefined, "coverage-table");
  const caption = node("caption", byZone ? "Cobertura por zona y sensor" : "Pasadas únicas por puerto y sensor");
  const head = node("thead"), header = node("tr");
  for (const text of [byZone ? "Zona" : "Puerto", "Sensor", "Pasadas", "Con área mínima", "Días útiles", "Intervalo mediano", "Mayor intervalo"]) {
    const th = node("th", text); th.scope = "col"; header.append(th);
  }
  head.append(header);
  const body = node("tbody"), number = new Intl.NumberFormat("es-GT", { maximumFractionDigits: 1 });
  const days = (value) => value == null ? "Sin suficientes datos" : `${number.format(value)} días`;
  const sensorNames = { "sentinel-1": "Radar", "sentinel-2": "Óptico", combined: "Radar y óptico" };
  for (const item of items) {
    const row = node("tr");
    row.classList.toggle("combined-pass", item.sensor === "combined");
    const label = byZone ? item.zone_id.replace(/^[^_]+_/, "").replaceAll("_", " ") : (state.ports.get(item.port_id) || item.port_name || item.port_id);
    const name = node("th", byZone ? `${state.ports.get(item.port_id) || item.port_id} · ${label}` : label); name.scope = "row";
    const gaps = item.sufficiently_observed_intervals_days;
    row.append(name, node("td", sensorNames[item.sensor] || item.sensor), node("td", number.format(item.catalogued_passes)),
      node("td", number.format(item.sufficiently_observed_passes)), node("td", number.format(item.sufficiently_observed_days)),
      node("td", days(gaps.median)), node("td", days(gaps.max)));
    body.append(row);
  }
  table.append(caption, head, body);
  return table;
}

async function loadPassCoverage() {
  const generation = ++state.coverageGeneration;
  const button = $("refresh-coverage"); button.disabled = true;
  $("coverage-error").hidden = true;
  try {
    const params = filterParams(); params.delete("limit");
    params.set("min_observable_fraction", $("coverage-threshold").value);
    const result = await api(`/v1/coverage/passes?${params}`);
    if (generation !== state.coverageGeneration) return;
    const minimum = Math.round(result.filters.min_observable_fraction * 100);
    const hasPasses = result.items.some((item) => item.catalogued_passes > 0);
    $("coverage-summary").textContent = `Área mínima ${minimum}% por zona · ${hasPasses ? "frecuencia medida" : "sin pasadas en este periodo"}`;
    $("coverage-period").textContent = `${date(result.period.start)} a ${date(result.period.end)} (fin exclusivo). Los intervalos se miden entre pasadas útiles; no incluyen los extremos del periodo.`;
    $("coverage-table").replaceChildren(hasPasses ? coverageTable(result.items) : node("p", "No hay pasadas en este periodo."));
    $("coverage-zone-table").replaceChildren(result.zones.length ? coverageTable(result.zones, true) : node("p", "Sin zonas para estos filtros."));
    $("coverage-method").textContent = result.source.mask_union_entries_applied > 0
      ? "Las áreas observables de partes compatibles de una misma pasada se unen sin contar dos veces el solapamiento. En las demás se conserva la mayor fracción observada. Sin datos suficientes, el intervalo queda sin estimar."
      : "Estimación conservadora: usa la mayor fracción observada de cada zona dentro de la pasada. Las partes de una escena no cuentan como visitas adicionales y sus porcentajes no se suman. Sin datos suficientes, el intervalo queda sin estimar.";
  } catch (error) {
    if (generation !== state.coverageGeneration) return;
    $("coverage-summary").textContent = "Cobertura sin actualizar";
    $("coverage-table").replaceChildren(); $("coverage-zone-table").replaceChildren();
    $("coverage-period").textContent = "";
    $("coverage-error").textContent = error.status === 422 ? `Revisa los filtros: ${error.message}` : "No se pudo consultar la cobertura. Actualiza para volver a intentarlo.";
    $("coverage-error").hidden = false;
  } finally { if (generation === state.coverageGeneration) button.disabled = false; }
}

async function loadObservations(append = false) {
  if (state.loading) return;
  const start = $("start-filter").value, end = $("end-filter").value;
  if (start && end && start >= end) { notify("La fecha final exclusiva debe ser posterior al inicio.", true); return; }
  state.loading = true;
  $("more-scenes").disabled = true;
  try {
    const params = filterParams();
    if (append && state.cursor) params.set("cursor", state.cursor);
    const result = await api(`/v1/observations?${params}`);
    state.observations = append ? [...state.observations, ...result.items] : result.items;
    state.cursor = result.next_cursor;
    renderSceneList();
    if (!append) {
      const previous = state.observation?.observation_id || savedState().observationId;
      const selected = state.observations.find((o) => o.observation_id === previous) || state.observations[0];
      if (selected) await selectObservation(selected.observation_id);
      else clearObservation();
    }
    persistSelection();
  } catch (error) { notify(explain(error), true); }
  finally { state.loading = false; $("more-scenes").disabled = false; }
}

function renderSceneList() {
  $("scene-list").replaceChildren();
  $("scene-count").textContent = `${state.observations.length} cargadas`;
  $("more-scenes").hidden = !state.cursor;
  if (!state.observations.length) {
    const empty = node("div", undefined, "scene-empty");
    empty.append(node("strong", "Aún no hay observaciones"), node("p", "Las adquisiciones de catálogo aparecen aquí cuando existe una observación registrada. Prueba otro periodo o consulta la cobertura en la API."));
    $("scene-list").append(empty);
    return;
  }
  for (const observation of state.observations) {
    const button = node("button", undefined, "scene-item");
    button.type = "button";
    const selected = observation.observation_id === state.observation?.observation_id;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
    button.append(node("strong", state.ports.get(observation.port_id) || observation.port_id),
      node("span", date(observation.captured_at, true)), node("span", observation.zone_id));
    const row = node("span", undefined, "scene-row");
    row.append(node("span", observation.sensor === "sentinel-1" ? "S1 · Radar" : "S2 · Óptico", "scene-tag"), node("span", labels[observation.status] || observation.status));
    button.append(row);
    button.addEventListener("click", () => selectObservation(observation.observation_id));
    $("scene-list").append(button);
  }
}

function clearObservation() {
  state.generation++;
  state.observation = null; state.selected = null; state.proposals = []; state.changes = [];
  state.asset = null; state.bounds = null;
  $("scene-title").textContent = "Sin observaciones en este periodo";
  $("scene-meta").textContent = "El inventario de catálogo se consulta por separado.";
  $("scene-status").hidden = true;
  $("model-notice").hidden = true;
  $("scene-review-note").textContent = "";
  $("scene-review-note").hidden = true;
  $("scope-caption").textContent = "";
  for (const id of ["model-count", "observable-fraction", "review-label"]) $(id).textContent = "—";
  emptyEvidence("Sin imágenes para revisar", "No hay observaciones registradas que coincidan con estos filtros. La disponibilidad de catálogo no acredita procesamiento ni visibilidad.");
  renderProposals(); updateControls();
}

async function allDetections(observationId) {
  const items = [];
  let cursor = null;
  do {
    const params = new URLSearchParams({ observation_id: observationId, include_rejected: "true", limit: "500" });
    if (cursor) params.set("cursor", cursor);
    const page = await api(`/v1/detections?${params}`);
    items.push(...page.features); cursor = page.next_cursor;
  } while (cursor);
  return items;
}

async function selectObservation(id, preferredProposal = null) {
  const generation = ++state.generation;
  setTool("select");
  state.selected = null; state.proposals = []; state.bounds = null;
  try {
    const [observation, proposals] = await Promise.all([
      api(`/v1/observations/${encodeURIComponent(id)}`), allDetections(id),
    ]);
    if (generation !== state.generation) return;
    state.observation = observation; state.proposals = proposals;
    $("scene-title").textContent = state.ports.get(observation.port_id) || observation.port_id;
    $("scene-meta").textContent = `${date(observation.captured_at)} · ${observation.zone_id} · ${observation.recipe_version}`;
    $("scene-status").hidden = false;
    $("scene-status").textContent = labels[observation.status] || observation.status;
    const validation = observation.metadata?.validation_status || observation.validation_status;
    $("model-notice").hidden = validation === "validated";
    $("model-notice").textContent = `${validation === "experimental" ? "Modelo experimental" : "Sin validación local registrada"}. Estos conteos son propuestas para revisión. Un cero no confirma ausencia de buques.`;
    const observable = ["usable", "partial"].includes(observation.status);
    $("model-count").textContent = observable && observation.detection_count !== null ? String(observation.detection_count) : "No disponible";
    $("observable-fraction").textContent = observation.observable_fraction === null || observation.observable_fraction === undefined ? "Sin medir" : `${(observation.observable_fraction * 100).toFixed(1)} %`;
    const aiReview = observation.provenance?.kind === "ai_visual_review";
    $("review-label").textContent = observation.review_status === "reviewed" && aiReview ? "Revisado por IA" : labels[observation.review_status] || "Provisional";
    $("scene-review-note").textContent = observation.note || "";
    $("scene-review-note").hidden = !observation.note;
    $("scope-caption").textContent = observation.reviewed_area
      ? "Amarillo: zona provisional. Verde: área etiquetada; el resto no se usa como negativo."
      : "Amarillo: límite de la zona provisional. La imagen puede cubrir solo una parte.";
    renderSceneList();
    if (state.mode === "changes") await loadChanges(generation);
    else {
      await showEvidence(observation, generation);
      renderProposals();
      const chosen = proposals.find((p) => p.id === preferredProposal) || proposals[0];
      if (chosen) selectProposal(chosen.id);
    }
    persistSelection(); updateControls();
  } catch (error) { if (generation === state.generation) notify(explain(error), true); }
}

function emptyEvidence(title, description) {
  $("image-wrap").hidden = true; $("comparison").hidden = true; $("empty-evidence").hidden = false;
  $("empty-evidence").querySelector("h3").textContent = title;
  $("empty-evidence").querySelector("p").textContent = description;
  $("asset-download").hidden = true;
  $("overlay").replaceChildren();
}

function imageAsset(observation) {
  const images = (observation.assets || []).filter((asset) => /^image\/(png|jpeg|webp)$/.test(asset.mime_type));
  return images.find((asset) => asset.metadata?.role === "preview") || images[0];
}

function assetBounds(asset) {
  const bounds = asset.metadata?.wgs84_bounds || (/(4326|CRS84)/i.test(asset.crs || "") ? asset.bounds : null);
  if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isFinite) || bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) return null;
  return bounds;
}

async function showEvidence(observation, generation) {
  let asset = imageAsset(observation);
  state.asset = null; state.bounds = null;
  $("comparison").hidden = true;
  if (!asset) {
    emptyEvidence("Esta observación no tiene una imagen", "No se puede revisar visualmente ni añadir objetos sin evidencia registrada. Consulta los activos y el estado de procesamiento.");
    $("asset-caption").textContent = "Sin imagen de consulta registrada.";
    return;
  }
  if (!asset.metadata) {
    try { asset = await api(`/v1/assets/${encodeURIComponent(asset.asset_id)}?metadata=true`); }
    catch (error) { notify(explain(error), true); return; }
  }
  if (generation !== state.generation) return;
  state.asset = asset; state.bounds = assetBounds(asset);
  $("empty-evidence").hidden = true; $("image-wrap").hidden = false;
  $("evidence-image").src = `/v1/assets/${encodeURIComponent(asset.asset_id)}`;
  $("evidence-image").alt = `Evidencia de ${state.ports.get(observation.port_id) || observation.port_id}, ${date(observation.captured_at)}, zona ${observation.zone_id}`;
  $("asset-download").href = $("evidence-image").src;
  $("asset-download").hidden = false;
  $("asset-caption").textContent = `${asset.attribution || "Fuente según activo registrado"}${state.bounds ? " · Referencia WGS84 disponible" : " · Sin límites WGS84: edición geométrica deshabilitada"}`;
  drawOverlay(); updateControls();
}

$("evidence-image").addEventListener("error", () => {
  state.bounds = null;
  emptyEvidence("No se pudo abrir la evidencia", "El activo registrado no está disponible. Actualiza la observación o comprueba el almacenamiento del servicio.");
  updateControls();
});
function fitEvidence() {
  const image = $("evidence-image"), stage = $("evidence-stage");
  if (!image.naturalWidth || !image.naturalHeight || $("image-wrap").hidden) return;
  $("image-wrap").style.width = `${Math.min(stage.clientWidth, stage.clientHeight * image.naturalWidth / image.naturalHeight)}px`;
}
$("evidence-image").addEventListener("load", () => { fitEvidence(); updateControls(); });
for (const id of ["before-image", "after-image"]) {
  $(id).addEventListener("load", updateControls);
  $(id).addEventListener("error", () => {
    emptyEvidence("No se pudo abrir el par temporal", "Uno de los activos registrados no está disponible. Actualiza antes de revisar el cambio.");
    updateControls();
  });
}
window.addEventListener("resize", fitEvidence);

function proposals() { return state.mode === "changes" ? state.changes : state.proposals; }
function currentProposal() { return proposals().find((p) => (p.id || p.change_id) === state.selected); }
function properties(proposal) { return state.mode === "changes" ? proposal : proposal?.properties; }

function renderProposals() {
  $("proposal-heading").textContent = state.mode === "changes" ? "Cambios en el puerto" : "Propuestas";
  $("proposal-count").textContent = String(proposals().length);
  $("proposal-list").replaceChildren();
  $("detail-panel").hidden = !currentProposal();
  if (!proposals().length) {
    const observable = ["usable", "partial"].includes(state.observation?.status);
    $("proposal-list").append(node("p", state.mode === "changes" ? "No hay cambios registrados en este periodo." : observable ? "No hay propuestas en esta escena. Revisa la imagen completa para buscar omisiones." : "Las propuestas se habilitan en observaciones utilizables o parciales.", "quiet"));
  }
  proposals().forEach((proposal, index) => {
    const data = properties(proposal), id = proposal.id || proposal.change_id;
    const button = node("button", undefined, `proposal-item ${data.review_status || "provisional"}`);
    button.type = "button";
    button.classList.toggle("selected", state.selected === id);
    button.setAttribute("aria-pressed", String(state.selected === id));
    const title = node("div"); title.append(node("i", undefined, "mark"), document.createTextNode(state.mode === "changes" ? `Cambio ${index + 1}` : `Buque ${index + 1}`));
    button.append(title, node("span", labels[data.review_status] || "Provisional"));
    button.addEventListener("click", () => selectProposal(id));
    $("proposal-list").append(button);
  });
}

async function selectProposal(id) {
  state.selected = id;
  const proposal = currentProposal();
  if (!proposal) return;
  const data = properties(proposal), index = proposals().indexOf(proposal) + 1;
  renderProposals(); drawOverlay();
  $("detail-panel").hidden = false;
  $("detail-title").textContent = state.mode === "changes" ? `Cambio ${index}` : `Buque ${index}`;
  const score = data.detection_score;
  $("detail-meta").textContent = state.mode === "changes" ? `${labels[data.status] || data.status} · ${labels[data.review_status] || "Provisional"}` : `${score === null || score === undefined ? "Anotación sin score del modelo" : `Score del modelo: ${(score * 100).toFixed(1)} %`} · ${labels[data.review_status] || "Provisional"}`;
  $("attributes-form").hidden = state.mode !== "detections";
  $("abstain").hidden = state.mode !== "detections";
  $("change-detail").hidden = state.mode !== "changes";
  $("audit-list").hidden = true;
  if (state.mode === "detections") {
    $("family").value = data.family || "";
    $("length").value = data.length_m ?? "";
    $("width").value = data.width_m ?? "";
    $("review-note").value = data.note || "";
    const references = Object.values(data.provenance || {}).map((field) => field.reference).filter(Boolean);
    $("reference").value = [...new Set(references)].join("\n");
  } else {
    $("change-detail").replaceChildren(node("p", "Aceptar registra la revisión. La persistencia requiere t0 y dos observaciones posteriores válidas; esta acción no confirma persistencia."), node("p", `Intervalo: ${date(data.interval_start)} a ${date(data.interval_end)}`));
    await showComparison(data);
  }
  updateControls();
}

function project(coordinate) {
  const [west, south, east, north] = state.bounds;
  return [(coordinate[0] - west) / (east - west) * 1000, (north - coordinate[1]) / (north - south) * 1000];
}

function drawOverlay() {
  $("overlay").replaceChildren();
  if (!state.bounds || state.mode !== "detections") return;
  for (const [geometry, className] of [
    [state.zones.get(state.observation?.zone_id), "zone-boundary"],
    [state.observation?.reviewed_area, "reviewed-boundary"],
  ]) {
    if (!geometry) continue;
    const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
    const boundary = document.createElementNS(NS, "path");
    boundary.setAttribute("d", polygons.map((rings) => rings.map((ring) => ring.map((point, index) => `${index ? "L" : "M"}${project(point).join(",")}`).join(" ") + " Z").join(" ")).join(" "));
    boundary.setAttribute("fill-rule", "evenodd");
    boundary.classList.add(className);
    $("overlay").append(boundary);
  }
  for (const proposal of state.proposals) {
    const geometry = proposal.geometry;
    let element;
    if (geometry.type === "Point") {
      element = document.createElementNS(NS, "circle");
      const [x, y] = project(geometry.coordinates);
      element.setAttribute("cx", x); element.setAttribute("cy", y); element.setAttribute("r", "5");
    } else {
      const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
      if (!polygons.length) continue;
      element = document.createElementNS(NS, "path");
      element.setAttribute("d", polygons.map((rings) => rings.map((ring) => ring.map((point, index) => `${index ? "L" : "M"}${project(point).join(",")}`).join(" ") + " Z").join(" ")).join(" "));
      element.setAttribute("fill-rule", "evenodd");
    }
    element.classList.add("detection-shape", proposal.properties.review_status || "provisional");
    if (proposal.id === state.selected) element.classList.add("selected");
    element.dataset.id = proposal.id;
    element.setAttribute("tabindex", "0");
    element.setAttribute("role", "button");
    element.setAttribute("aria-label", `Seleccionar propuesta ${state.proposals.indexOf(proposal) + 1}`);
    element.addEventListener("click", () => { if (state.tool === "select") selectProposal(proposal.id); });
    element.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectProposal(proposal.id); } });
    $("overlay").append(element);
  }
}

function updateControls() {
  const authenticated = Boolean(state.token && state.author) && !state.busy;
  const observable = ["usable", "partial"].includes(state.observation?.status);
  const selected = Boolean(currentProposal());
  const loaded = (id) => $(id).complete && $(id).naturalWidth > 0;
  const hasEvidence = state.mode === "detections"
    ? Boolean(state.asset) && !$("image-wrap").hidden && loaded("evidence-image")
    : !$("comparison").hidden && loaded("before-image") && loaded("after-image");
  for (const id of ["accept", "reject", "abstain", "save-attributes"]) $(id).disabled = !authenticated || !selected || !hasEvidence;
  for (const id of ["tool-add", "tool-move", "tool-redraw"]) $(id).disabled = !authenticated || !observable || !state.bounds || !hasEvidence || (id !== "tool-add" && !selected);
  $("mark-unusable").disabled = !authenticated || !state.observation || state.observation.status === "unusable";
  $("complete-scene").disabled = !authenticated || !observable || !hasEvidence || state.mode !== "detections";
  $("auth-toggle").textContent = state.token ? "Credencial en memoria" : "Acceso a revisión";
  $("drawing-tools").hidden = state.mode !== "detections";
}

function setTool(tool) {
  if (state.drag?.original && currentProposal()) currentProposal().geometry = state.drag.original;
  state.tool = tool;
  state.drag = null;
  for (const name of ["select", "add", "move", "redraw"]) {
    $("tool-" + name).classList.toggle("active", name === tool);
    $("tool-" + name).setAttribute("aria-pressed", String(name === tool));
  }
  $("overlay").classList.toggle("draw", ["add", "redraw"].includes(tool));
  $("overlay").classList.toggle("move", tool === "move");
  $("drawing-hint").textContent = ({ select: "Selecciona una propuesta para revisarla.", add: "Arrastra un recuadro alrededor del buque omitido.", redraw: "Dibuja el nuevo recuadro de la propuesta.", move: "Arrastra la propuesta seleccionada." })[tool];
  drawOverlay();
}

function pointer(event) {
  const rect = $("overlay").getBoundingClientRect();
  return [Math.max(0, Math.min(1000, (event.clientX - rect.left) / rect.width * 1000)), Math.max(0, Math.min(1000, (event.clientY - rect.top) / rect.height * 1000))];
}
function unproject([x, y]) {
  const [west, south, east, north] = state.bounds;
  return [west + x / 1000 * (east - west), north - y / 1000 * (north - south)];
}
function rectangle(a, b) {
  const [x1, y1] = unproject(a), [x2, y2] = unproject(b);
  return { type: "Polygon", coordinates: [[[Math.min(x1, x2), Math.min(y1, y2)], [Math.max(x1, x2), Math.min(y1, y2)], [Math.max(x1, x2), Math.max(y1, y2)], [Math.min(x1, x2), Math.max(y1, y2)], [Math.min(x1, x2), Math.min(y1, y2)]]] };
}
function translate(geometry, dx, dy) {
  const walk = (coordinates) => typeof coordinates[0] === "number" ? [coordinates[0] + dx, coordinates[1] + dy, ...coordinates.slice(2)] : coordinates.map(walk);
  return { ...geometry, coordinates: walk(geometry.coordinates) };
}

$("overlay").addEventListener("pointerdown", (event) => {
  if (state.tool === "select" || !state.bounds || !state.token || state.busy || event.button !== 0) return;
  const selected = currentProposal();
  if (state.tool === "move" && event.target.dataset.id !== state.selected) return;
  if (state.tool !== "add" && !selected) return;
  event.preventDefault();
  state.drag = { start: pointer(event), original: selected ? structuredClone(selected.geometry) : null, tool: state.tool };
  $("overlay").setPointerCapture(event.pointerId);
});
$("overlay").addEventListener("pointermove", (event) => {
  if (!state.drag) return;
  const current = pointer(event), drag = state.drag;
  if (drag.tool === "move") {
    const a = unproject(drag.start), b = unproject(current);
    currentProposal().geometry = translate(drag.original, b[0] - a[0], b[1] - a[1]);
    drawOverlay();
  } else {
    let preview = $("overlay").querySelector(".draw-preview");
    if (!preview) { preview = document.createElementNS(NS, "rect"); preview.classList.add("draw-preview"); $("overlay").append(preview); }
    preview.setAttribute("x", Math.min(drag.start[0], current[0])); preview.setAttribute("y", Math.min(drag.start[1], current[1]));
    preview.setAttribute("width", Math.abs(drag.start[0] - current[0])); preview.setAttribute("height", Math.abs(drag.start[1] - current[1]));
  }
});
$("overlay").addEventListener("pointerup", async (event) => {
  const drag = state.drag;
  if (!drag) return;
  const finish = pointer(event);
  state.drag = null;
  if ($( "overlay").hasPointerCapture(event.pointerId)) $("overlay").releasePointerCapture(event.pointerId);
  const distance = Math.hypot(finish[0] - drag.start[0], finish[1] - drag.start[1]);
  if (distance < 3 || (drag.tool !== "move" && (Math.abs(finish[0] - drag.start[0]) < 2 || Math.abs(finish[1] - drag.start[1]) < 2))) {
    if (drag.original && currentProposal()) currentProposal().geometry = drag.original;
    drawOverlay(); return;
  }
  const geometry = drag.tool === "move" ? currentProposal().geometry : rectangle(drag.start, finish);
  if (drag.tool === "add") await addOmitted(geometry);
  else {
    if (drag.tool === "move") currentProposal().geometry = drag.original;
    await saveReview({ geometry }, "detection");
  }
  setTool("select");
});
$("overlay").addEventListener("pointercancel", () => {
  if (state.drag?.original && currentProposal()) currentProposal().geometry = state.drag.original;
  state.drag = null; drawOverlay();
});

async function saveReview(correction, entityType = state.mode === "changes" ? "change" : "detection") {
  if (!state.token || !state.author || state.busy) return;
  const observationId = state.observation?.observation_id;
  const selected = entityType === "observation" ? state.observation : properties(currentProposal());
  const entityId = entityType === "observation" ? observationId : state.selected;
  if (!entityId || !selected) return;
  const previous = selected.latest_revision_id || selected.metadata?.latest_revision_id || null;
  correction.provenance = { kind: state.reviewerKind, reviewer: state.author, ...correction.provenance };
  state.busy = true; updateControls();
  try {
    await api("/v1/reviews", { method: "POST", body: JSON.stringify({ entity_type: entityType, entity_id: entityId, correction, author: state.author, previous_revision_id: previous }) });
    notify("Revisión guardada. La propuesta original y la corrección se conservan en el historial.");
    if (state.observation?.observation_id === observationId) await selectObservation(observationId, entityId);
  } catch (error) { notify(explain(error), true); drawOverlay(); }
  finally { state.busy = false; updateControls(); }
}

async function addOmitted(geometry) {
  const observationId = state.observation?.observation_id;
  if (!observationId || !state.token || state.busy) return;
  state.busy = true; updateControls();
  try {
    const result = await api("/v1/reviews/detections", { method: "POST", body: JSON.stringify({ observation_id: observationId, geometry, author: state.author, provenance: { kind: state.reviewerKind, reviewer: state.author } }) });
    notify("Buque omitido registrado como anotación manual, sin inventar un score del modelo.");
    if (state.observation?.observation_id === observationId) await selectObservation(observationId, result.detection_id);
  } catch (error) { notify(explain(error), true); }
  finally { state.busy = false; updateControls(); }
}

async function loadChanges(generation = state.generation) {
  const params = filterParams(); params.delete("sensor"); params.set("include_rejected", "true");
  if (state.observation?.port_id) params.set("port_id", state.observation.port_id);
  const changes = [];
  let cursor;
  do {
    if (cursor) params.set("cursor", cursor);
    const page = await api(`/v1/changes?${params}`);
    changes.push(...page.items); cursor = page.next_cursor;
  } while (cursor);
  if (generation !== state.generation) return;
  state.changes = changes; state.selected = null; renderProposals();
  if (changes.length) await selectProposal(changes[0].change_id);
  else emptyEvidence("Sin cambios registrados", "La persistencia se determina con una referencia y dos observaciones posteriores comparables. No se generan eventos a partir de una imagen de consulta.");
  updateControls();
}

async function showComparison(change) {
  const selected = state.selected;
  let before, after;
  try {
    if (change.reference_observation_id) before = imageAsset(await api(`/v1/observations/${encodeURIComponent(change.reference_observation_id)}`));
    if (change.first_observation_id) after = imageAsset(await api(`/v1/observations/${encodeURIComponent(change.first_observation_id)}`));
    const direct = change.metadata || {};
    if (!before && (direct.before_asset_id || change.before_asset_id)) before = await api(`/v1/assets/${encodeURIComponent(direct.before_asset_id || change.before_asset_id)}?metadata=true`);
    if (!after && (direct.after_asset_id || change.after_asset_id)) after = await api(`/v1/assets/${encodeURIComponent(direct.after_asset_id || change.after_asset_id)}?metadata=true`);
    if (selected !== state.selected || state.mode !== "changes") return;
    if (!before || !after || !/^image\//.test(before.mime_type || "") || !/^image\//.test(after.mime_type || "")) {
      emptyEvidence("Falta evidencia del par temporal", "Este evento todavía no enlaza dos imágenes de consulta. Consulta su historial antes de revisarlo.");
      return;
    }
    $("empty-evidence").hidden = true; $("image-wrap").hidden = true; $("comparison").hidden = false;
    $("before-image").src = `/v1/assets/${encodeURIComponent(before.asset_id)}`;
    $("after-image").src = `/v1/assets/${encodeURIComponent(after.asset_id)}`;
    $("before-note").textContent = date(change.interval_start);
    $("after-note").textContent = date(change.interval_end);
    $("asset-caption").textContent = "Par de evidencia registrado. Las imágenes pueden tener encuadres distintos; revisa sus referencias espaciales.";
    $("asset-download").hidden = true;
  } catch (error) { notify(explain(error), true); }
}

$("auth-toggle").addEventListener("click", () => { $("auth-panel").hidden = !$("auth-panel").hidden; if (!$("auth-panel").hidden) $("author").focus(); });
$("auth-form").addEventListener("submit", (event) => {
  event.preventDefault(); state.author = $("author").value.trim(); state.token = $("token").value;
  state.reviewerKind = $("reviewer-kind").value;
  $("token").value = ""; $("auth-panel").hidden = true;
  notify("Credencial guardada en memoria. Se verificará al guardar una corrección."); updateControls();
});
$("lock-review").addEventListener("click", () => { state.token = ""; $("token").value = ""; setTool("select"); updateControls(); notify("Acceso de revisión cerrado."); });
$("filters").addEventListener("submit", (event) => { event.preventDefault(); loadObservations(); if ($("coverage-panel").open) loadPassCoverage(); });
$("more-scenes").addEventListener("click", () => loadObservations(true));
$("refresh-system").addEventListener("click", loadSystemStatus);
$("refresh-coverage").addEventListener("click", loadPassCoverage);
$("coverage-threshold").addEventListener("change", loadPassCoverage);
$("coverage-panel").addEventListener("toggle", () => { if ($("coverage-panel").open) loadPassCoverage(); });
for (const tool of ["select", "add", "move", "redraw"]) $("tool-" + tool).addEventListener("click", () => setTool(tool));
$("accept").addEventListener("click", () => saveReview({ review_status: "reviewed" }));
$("reject").addEventListener("click", () => saveReview(state.mode === "changes" ? { review_status: "rejected", status: "rejected" } : { review_status: "rejected" }));
$("abstain").addEventListener("click", () => saveReview({ family: null }));
$("mark-unusable").addEventListener("click", () => saveReview({ status: "unusable", review_status: "reviewed", note: "No observable según revisión visual" }, "observation"));
$("complete-scene").addEventListener("click", () => saveReview({ scene_complete: true, review_status: "reviewed", note: "Revisión de escena completa, incluidas posibles omisiones" }, "observation"));
$("attributes-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const correction = { family: $("family").value || null, length_m: $("length").value ? Number($("length").value) : null, width_m: $("width").value ? Number($("width").value) : null, note: $("review-note").value.trim(), provenance: {} };
  const reference = $("reference").value.trim();
  for (const field of ["family", "length_m", "width_m"]) if (correction[field] !== null) {
    if (!reference) { notify("Añade una referencia independiente para la familia o las dimensiones. Puedes dejarlas indeterminadas.", true); $("reference").focus(); return; }
    correction.provenance[field] = { kind: "independent_reference", reference };
  }
  saveReview(correction);
});
$("show-audit").addEventListener("click", async () => {
  const type = state.mode === "changes" ? "change" : "detection", selected = state.selected;
  try {
    const result = await api(`/v1/reviews/${type}/${encodeURIComponent(selected)}`);
    if (selected !== state.selected) return;
    $("audit-list").replaceChildren(); $("audit-list").hidden = false;
    if (!result.items.length) $("audit-list").append(node("p", "Todavía no hay revisiones."));
    for (const revision of result.items) {
      const row = node("div", undefined, "audit-row");
      row.append(node("strong", revision.author), node("span", date(revision.created_at)), node("pre", JSON.stringify(revision.correction, null, 2)));
      $("audit-list").append(row);
    }
  } catch (error) { notify(explain(error), true); }
});
for (const mode of ["detections", "changes"]) $("tab-" + mode).addEventListener("click", async () => {
  state.mode = mode; state.selected = null; setTool("select");
  for (const other of ["detections", "changes"]) $("tab-" + other).setAttribute("aria-selected", String(other === mode));
  if (state.observation) await selectObservation(state.observation.observation_id);
  else { renderProposals(); updateControls(); }
});
document.addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === "escape") { setTool("select"); return; }
  if (["j", "k"].includes(key) && proposals().length) {
    event.preventDefault();
    const items = proposals(), index = Math.max(0, items.findIndex((p) => (p.id || p.change_id) === state.selected));
    const next = items[(index + (key === "j" ? 1 : -1) + items.length) % items.length];
    selectProposal(next.id || next.change_id);
  }
  if (key === "a" && !$("accept").disabled) { event.preventDefault(); $("accept").click(); }
  if (key === "r" && !$("reject").disabled) { event.preventDefault(); $("reject").click(); }
});

async function init() {
  try {
    const [, ports] = await Promise.all([api("/health"), api("/v1/ports")]);
    $("connection").textContent = "Servicio local conectado"; $("connection").classList.add("online");
    for (const feature of ports.features || []) {
      state.ports.set(feature.properties.port_id, feature.properties.port_name || feature.properties.port_id);
      state.zones.set(feature.properties.zone_id, feature.geometry);
    }
    for (const [id, name] of state.ports) { const option = node("option", name); option.value = id; $("port-filter").append(option); }
    const previous = savedState();
    $("port-filter").value = previous.port || ""; $("sensor-filter").value = previous.sensor || "";
    $("start-filter").value = previous.start || ""; $("end-filter").value = previous.end || "";
    await loadObservations();
    await loadSystemStatus();
  } catch (error) { $("connection").textContent = "Servicio no disponible"; notify(explain(error), true); }
}
init();
