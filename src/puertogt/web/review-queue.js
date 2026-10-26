"use strict";

// Coordinates stay in the preview grid. The server alone projects them to WGS84.
function normalizedPoint(clientX, clientY, bounds) {
  if (!(bounds.width > 0 && bounds.height > 0)) throw new Error("Imagen sin dimensiones");
  return [Math.max(0, Math.min(1, (clientX - bounds.left) / bounds.width)),
    Math.max(0, Math.min(1, (clientY - bounds.top) / bounds.height))];
}

function normalizedRectangle(first, last) {
  const values = [...first, ...last];
  if (values.length !== 4 || !values.every(v => Number.isFinite(v) && v >= 0 && v <= 1)) {
    throw new Error("Usa posiciones entre 0 y 100% de la imagen.");
  }
  const result = [Math.min(first[0], last[0]), Math.min(first[1], last[1]),
    Math.max(first[0], last[0]), Math.max(first[1], last[1])];
  if (result[0] === result[2] || result[1] === result[3]) throw new Error("El rectángulo debe tener área.");
  return result;
}

function polygonPath(geometry) {
  const polygons = geometry?.type === "Polygon" ? [geometry.coordinates]
    : geometry?.type === "MultiPolygon" ? geometry.coordinates : null;
  if (!Array.isArray(polygons) || !polygons.length) throw new Error("Geometría de imagen inválida.");
  return polygons.flatMap(polygon => {
    if (!Array.isArray(polygon) || !polygon.length) throw new Error("Polígono vacío.");
    return polygon.map(ring => {
      if (!Array.isArray(ring) || ring.length < 4 || !ring.every(point =>
        Array.isArray(point) && point.length >= 2 && point.slice(0, 2).every(Number.isFinite))) {
        throw new Error("Coordenadas de imagen inválidas.");
      }
      return ring.map((point, i) => `${i ? "L" : "M"}${point[0]},${point[1]}`).join(" ") + " Z";
    });
  }).join(" ");
}

function newInspection(inspectionId) {
  return {inspectionId, area: null, candidates: [], decision: "uncertain", note: "",
    elapsedMs: 0, startedAt: null, pendingBody: null, rejectedValidation: false, receipt: null};
}

function freezeSubmission(draft, context, now = Date.now()) {
  // Never change UUID, elapsed time, identity or findings after an ambiguous POST.
  if (draft.pendingBody) return draft.pendingBody;
  const elapsed = draft.elapsedMs + (draft.startedAt === null ? 0 : Math.max(0, now - draft.startedAt));
  draft.pendingBody = JSON.stringify({queue_id: context.queueId, inspection_id: draft.inspectionId,
    observation_id: context.observationId, author: context.author, reviewer_kind: context.reviewerKind,
    decision: draft.decision, candidates: draft.candidates.map(item => item.geometry),
    reviewed_area: draft.area?.geometry || null, note: draft.note,
    elapsed_seconds: Math.min(86400, Math.floor(elapsed / 1000))});
  return draft.pendingBody;
}

function matchesReceipt(receipt, body) {
  const submitted = JSON.parse(body);
  return receipt?.queue_id === submitted.queue_id && receipt?.observation_id === submitted.observation_id
    && receipt?.inspection_id === submitted.inspection_id
    && receipt?.is_training_label === false && receipt?.is_independent_validation === false
    && typeof receipt?.sha256 === "string" && /^[a-f0-9]{64}$/.test(receipt.sha256);
}

function matchesEntrySource(entry, expected, queueId) {
  return entry.queue_id === queueId
    && ["observation_id", "port_id", "sensor", "zone_id", "captured_at", "observable_fraction"]
      .every(key => entry[key] === expected[key])
    && JSON.stringify(entry.zone_geometry) === JSON.stringify(expected.zone_geometry)
    && JSON.stringify(entry.search_scope ?? null) === JSON.stringify(expected.search_scope ?? null);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {normalizedPoint, normalizedRectangle, polygonPath, newInspection, freezeSubmission,
    matchesReceipt, matchesEntrySource};
}

if (typeof document !== "undefined") startBlindReview();

function startBlindReview() {
  const $ = id => document.getElementById(id);
  const ports = {prq: "Puerto Quetzal", pbr: "Puerto Barrios", stc: "Santo Tomás"};
  const state = {queue: null, entry: null, asset: null, imageSize: null, objectURL: null,
    boundQueueId: new URLSearchParams(window.location.search).get("queue"),
    generation: 0, controller: null, loadingQueue: false, geometryBusy: false, saving: false,
    tool: "explore", drag: null, zoom: 1, drafts: new Map(), author: "", reviewerKind: "human_visual_review", token: ""};

  function draftKey() { return `${state.queue?.queue_id}/${state.entry?.observation_id}`; }
  function draft() { return state.drafts.get(draftKey()); }
  function locked() { return state.saving || state.geometryBusy || Boolean(draft()?.pendingBody || draft()?.receipt); }
  function canEdit() { return Boolean(state.imageSize && state.entry && !locked()); }
  function feedback(message, isError = false) {
    $("feedback").textContent = message;
    $("feedback").hidden = !message;
    $("feedback").classList.toggle("error", isError);
  }
  function authenticationNeeded() {
    if (state.token && state.author) return false;
    $("auth-panel").hidden = false;
    $("auth-toggle").setAttribute("aria-expanded", "true");
    feedback("Identifica al revisor y agrega la credencial local para dibujar o guardar.", true);
    (!state.author ? $("author") : $("token")).focus();
    return true;
  }
  async function request(path, options = {}) {
    const response = await fetch(path, {cache: "no-store", ...options});
    if (!response.ok) {
      let detail;
      try { detail = (await response.json()).detail; } catch { detail = null; }
      const text = Array.isArray(detail) ? detail.map(x => x.msg).join("; ") : detail;
      const error = new Error(typeof text === "string" ? text : `Error HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return response;
  }
  function stopClock() {
    const current = draft();
    if (current && current.startedAt !== null) {
      current.elapsedMs += Math.max(0, Date.now() - current.startedAt);
      current.startedAt = null;
    }
  }
  function clearImage() {
    stopClock();
    state.generation++;
    state.controller?.abort();
    state.controller = new AbortController();
    state.geometryBusy = false;
    state.drag = null;
    state.imageSize = null;
    state.entry = null;
    state.asset = null;
    state.zoom = 1;
    $("raw-image").removeAttribute("src");
    if (state.objectURL) URL.revokeObjectURL(state.objectURL);
    state.objectURL = null;
    $("image-wrap").hidden = true;
    $("image-placeholder").hidden = false;
    $("overlay").replaceChildren();
    $("load-status").textContent = "Sin imagen";
    $("scene-title").textContent = "Selecciona una observación";
    $("scene-metadata").textContent = "Solo observaciones de la cola TRAIN.";
    $("attribution").textContent = "Contains modified Copernicus Sentinel data";
    $("stage").scrollTo(0, 0);
    setTool("explore");
    renderDraft();
  }
  function isCurrent(generation, observationId) {
    return state.generation === generation && state.entry?.observation_id === observationId;
  }
  function renderSearchContext() {
    const scope = state.entry?.search_scope;
    $("search-legend").hidden = !scope;
    $("search-legend").textContent = scope ? `Azul: búsqueda ${scope.radius_m} m` : "";
    $("search-context").textContent = scope
      ? `El contorno azul delimita la búsqueda hasta ${scope.radius_m} m alrededor de la zona de referencia amarilla.${scope.frame_clipped ? " El área queda limitada por el encuadre disponible." : ""} La observabilidad indicada corresponde a la zona de referencia; no mide el área ampliada. La ubicación de un candidato no determina su actividad.`
      : "La zona de referencia amarilla delimita el área de búsqueda de esta cola. La observabilidad indicada corresponde a esa zona.";
  }
  function renderList() {
    const nodes = (state.queue?.items || []).map(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "scene-item";
      button.dataset.observationId = item.observation_id;
      button.setAttribute("aria-current", String(state.entry?.observation_id === item.observation_id));
      button.disabled = state.saving || state.loadingQueue;
      const title = document.createElement("strong");
      title.textContent = `${ports[item.port_id] || item.port_id} · ${item.sensor === "sentinel-1" ? "Radar" : "Óptico"}`;
      const subtitle = document.createElement("span");
      subtitle.textContent = `${item.captured_at.slice(0, 10)} · ${item.zone_id.split("_").slice(1).join(" ")}`;
      const status = document.createElement("span");
      const local = state.drafts.get(`${state.queue.queue_id}/${item.observation_id}`);
      status.textContent = local?.receipt ? "Registrada en esta sesión" : local?.pendingBody ? "Envío por confirmar" : "Primera inspección";
      button.append(title, subtitle, status);
      button.addEventListener("click", () => selectObservation(item));
      return button;
    });
    $("scene-list").replaceChildren(...nodes);
  }
  function renderDraft() {
    const current = draft();
    const immutable = locked();
    $("decision").value = current?.decision || "uncertain";
    $("note").value = current?.note || "";
    $("decision").disabled = $("note").disabled = !state.imageSize || immutable;
    $("clear-area").disabled = !current?.area || immutable;
    $("scope-status").textContent = current?.area ? "Área delimitada dentro del área de búsqueda" : "Sin área delimitada";
    renderSearchContext();
    const children = (current?.candidates || []).map((candidate, index) => {
      const row = document.createElement("div");
      row.className = "q-candidate-row";
      const label = document.createElement("span");
      label.textContent = `Candidato local ${index + 1}`;
      if (candidate.location === "search_margin") {
        const location = document.createElement("span");
        location.className = "q-location-badge";
        location.textContent = "Fuera de la zona de referencia";
        label.append(location);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-button danger";
      remove.textContent = "Quitar";
      remove.setAttribute("aria-label", `Quitar candidato local ${index + 1}`);
      remove.disabled = immutable;
      remove.addEventListener("click", () => {
        if (!canEdit()) return;
        draft().candidates.splice(index, 1);
        renderDraft(); renderOverlay();
      });
      row.append(label, remove);
      return row;
    });
    if (!children.length) {
      const empty = document.createElement("p"); empty.className = "quiet";
      empty.textContent = "Todavía no has marcado candidatos."; children.push(empty);
    }
    $("candidate-list").replaceChildren(...children);
    $("reviewer-summary").textContent = state.author
      ? `Revisor: ${state.author} · ${state.reviewerKind === "ai_visual_review" ? "Asistente de IA" : "Persona"}`
      : "Identifica al revisor antes de guardar.";
    $("save").disabled = !state.imageSize || state.saving || state.geometryBusy || Boolean(current?.receipt);
    $("save").textContent = state.saving ? "Guardando…" : current?.receipt ? "Inspección registrada"
      : current?.pendingBody ? "Reintentar el mismo envío" : "Guardar primera inspección";
    $("save-help").textContent = current?.pendingBody && !current?.receipt
      ? "El envío quedó fijado. Reintentar conserva hallazgos, identidad, tiempo e identificador. La credencial puede renovarse."
      : "El recibo conserva estos hallazgos. No crea etiquetas de entrenamiento ni valida precisión.";
    $("edit-rejected").hidden = !current?.rejectedValidation || Boolean(current?.receipt);
    $("edit-rejected").disabled = state.saving;
    $("receipt").hidden = !current?.receipt;
    $("continue-review").hidden = !current?.receipt;
    $("continue-review").removeAttribute("href");
    if (current?.receipt) {
      $("receipt-detail").textContent = `Registro ${current.receipt.inspection_id} · ${current.receipt.recorded_at} · SHA256 ${current.receipt.sha256}`;
      $("continue-review").href = `/?observation=${encodeURIComponent(current.receipt.observation_id)}`;
    }
    for (const kind of ["area", "candidate"]) $("tool-" + kind).disabled = !canEdit();
    $("rectangle-submit").disabled = !canEdit();
    for (const element of $("rectangle-form").querySelectorAll("input, select")) element.disabled = !canEdit();
    $("reload").disabled = state.saving || state.loadingQueue;
    $("overlay").setAttribute("aria-busy", String(state.geometryBusy));
    $("image-wrap").classList.toggle("q-locked", immutable);
    renderList();
  }
  function pathNode(geometry, className) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", polygonPath(geometry));
    path.setAttribute("class", className);
    return path;
  }
  function renderOverlay() {
    const nodes = [];
    if (state.entry?.search_scope && state.asset?.metadata?.search_pixel_geometry) {
      nodes.push(pathNode(state.asset.metadata.search_pixel_geometry, "q-search"));
    }
    if (state.asset?.metadata?.zone_pixel_geometry) nodes.push(pathNode(state.asset.metadata.zone_pixel_geometry, "q-zone"));
    if (draft()?.area) nodes.push(pathNode(draft().area.pixel_geometry, "q-area"));
    for (const candidate of draft()?.candidates || []) nodes.push(pathNode(candidate.pixel_geometry, "q-candidate"));
    if (state.drag) {
      const [x1, y1] = state.drag.start, [x2, y2] = state.drag.end;
      nodes.push(pathNode({type: "Polygon", coordinates: [[[x1,y1],[x2,y1],[x2,y2],[x1,y2],[x1,y1]]]}, "q-drag"));
    }
    $("overlay").replaceChildren(...nodes);
  }
  function setTool(tool) {
    state.tool = tool;
    state.drag = null;
    for (const kind of ["explore", "area", "candidate"]) {
      $("tool-" + kind).classList.toggle("active", kind === tool);
      $("tool-" + kind).setAttribute("aria-pressed", String(kind === tool));
    }
    $("overlay").classList.toggle("drawing", tool !== "explore");
    renderOverlay();
  }
  function fitImage() {
    $("zoom").value = String(state.zoom);
    $("zoom-value").textContent = `${Math.round(state.zoom * 100)}%`;
    for (const id of ["zoom", "zoom-in", "zoom-out", "fit"]) $(id).disabled = !state.imageSize;
    if (!state.imageSize) return;
    const aspect = state.imageSize[0] / state.imageSize[1];
    const width = Math.max(1, Math.min($("stage").clientWidth, $("stage").clientHeight * aspect));
    $("image-wrap").style.width = `${width * state.zoom}px`;
  }
  function setZoom(value) { state.zoom = Math.max(1, Math.min(8, value)); fitImage(); }

  async function loadQueue() {
    if (state.saving) return;
    const previousId = state.entry?.observation_id;
    clearImage();
    const generation = state.generation;
    state.loadingQueue = true;
    renderDraft();
    feedback("");
    $("queue-summary").textContent = "Cargando cola congelada…";
    try {
      if (state.boundQueueId !== null && !/^train-[a-f0-9]{20}$/.test(state.boundQueueId)) {
        throw new Error("El enlace no identifica una cola TRAIN congelada válida.");
      }
      const path = "/v1/review-queue" + (state.boundQueueId === null ? ""
        : `?queue_id=${encodeURIComponent(state.boundQueueId)}`);
      const queue = await (await request(path, {signal: state.controller.signal})).json();
      if (generation !== state.generation) return;
      if (queue.available && (queue.split !== "train" || !/^train-[a-f0-9]{20}$/.test(queue.queue_id) || !Array.isArray(queue.items))) {
        throw new Error("La respuesta no identifica una cola TRAIN congelada.");
      }
      if (queue.available && state.boundQueueId !== null && queue.queue_id !== state.boundQueueId) {
        throw new Error("La respuesta pertenece a otra cola. Se conserva el identificador solicitado.");
      }
      state.queue = queue.available ? queue : null;
      if (state.queue) state.boundQueueId = state.queue.queue_id;
      $("queue-identity").textContent = state.queue?.queue_id || "";
      const deficit = Object.values(queue.distribution || {}).reduce((sum, stratum) => sum + (stratum.deficit || 0), 0);
      $("queue-summary").textContent = queue.available
        ? `${queue.items.length} observaciones para inspeccionar.${deficit ? ` Faltan ${deficit} plazas por evidencia insuficiente.` : ""}`
        : "No hay una cola TRAIN congelada activa.";
      state.loadingQueue = false;
      renderList();
      const selected = queue.items?.find(item => item.observation_id === previousId) || queue.items?.[0];
      if (state.queue && selected) await selectObservation(selected);
      else $("image-placeholder").textContent = "No hay observaciones elegibles en esta cola.";
    } catch (error) {
      if (generation !== state.generation || error.name === "AbortError") return;
      state.queue = null;
      $("queue-identity").textContent = "";
      $("queue-summary").textContent = "No se pudo cargar la cola.";
      feedback(error.message, true);
    } finally {
      if (generation === state.generation || !state.loadingQueue) {
        state.loadingQueue = false;
        renderDraft(); fitImage();
      }
    }
  }
  async function selectObservation(item) {
    if (state.saving || state.loadingQueue || !state.queue?.items.includes(item)) return;
    clearImage();
    state.entry = item;
    const generation = state.generation, observationId = item.observation_id, queueId = state.queue.queue_id;
    const signal = state.controller.signal;
    if (!state.drafts.has(draftKey())) state.drafts.set(draftKey(), newInspection(crypto.randomUUID()));
    $("scene-title").textContent = `${ports[item.port_id] || item.port_id} · ${item.zone_id.split("_").slice(1).join(" ")}`;
    $("scene-metadata").textContent = `${item.captured_at.slice(0, 10)} UTC · ${item.sensor} · Observabilidad en zona de referencia ${(item.observable_fraction * 100).toFixed(0)}%`;
    $("image-placeholder").textContent = "Cargando evidencia cruda verificada…";
    $("load-status").textContent = "Cargando…";
    feedback(""); renderDraft(); fitImage();
    let url;
    try {
      const binding = `?queue_id=${encodeURIComponent(queueId)}`;
      const entry = await (await request(`/v1/review-queue/observations/${encodeURIComponent(observationId)}${binding}`, {signal})).json();
      if (!isCurrent(generation, observationId)) return;
      const asset = entry.assets?.find(candidate => item.assets?.some(expected => expected.asset_id === candidate.asset_id
        && expected.sha256 === candidate.sha256 && expected.crs === candidate.crs
        && JSON.stringify(expected.bounds) === JSON.stringify(candidate.bounds)));
      if (!matchesEntrySource(entry, item, queueId) || !asset || asset.mime_type !== "image/png") {
        throw new Error("La evidencia no coincide con la cola congelada. Actualiza la cola.");
      }
      polygonPath(asset.metadata?.zone_pixel_geometry);
      if (entry.search_scope) {
        if (entry.search_scope.radius_m !== 500) throw new Error("El margen de búsqueda no coincide con los 500 m previstos.");
        polygonPath(entry.search_scope.geometry);
        polygonPath(asset.metadata?.search_pixel_geometry);
      }
      const assetPath = `/v1/review-queue/assets/${encodeURIComponent(observationId)}/${encodeURIComponent(asset.asset_id)}${binding}`;
      if (asset.href !== undefined && asset.href !== assetPath) {
        throw new Error("El enlace de la imagen no pertenece a esta observación y cola congelada.");
      }
      const response = await request(asset.href || assetPath, {signal});
      const blob = await response.blob();
      if (!isCurrent(generation, observationId)) return;
      url = URL.createObjectURL(blob);
      const image = new Image(); image.src = url;
      await image.decode();
      if (!isCurrent(generation, observationId)) { URL.revokeObjectURL(url); return; }
      if (!(image.naturalWidth > 0 && image.naturalHeight > 0)) throw new Error("El preview no tiene dimensiones válidas.");
      state.asset = asset;
      state.imageSize = [image.naturalWidth, image.naturalHeight];
      state.objectURL = url;
      $("raw-image").src = url;
      $("raw-image").alt = `Evidencia cruda ${item.sensor}, ${ports[item.port_id] || item.port_id}, ${item.zone_id}, ${item.captured_at.slice(0, 10)} UTC`;
      $("image-wrap").hidden = false;
      $("image-placeholder").hidden = true;
      $("attribution").textContent = `${asset.attribution || "Contains modified Copernicus Sentinel data"} ${item.captured_at.slice(0, 4)}`;
      $("load-status").textContent = "Evidencia cruda";
      if (!draft().receipt && !draft().pendingBody) draft().startedAt = Date.now();
      fitImage(); renderDraft(); renderOverlay();
    } catch (error) {
      if (url && state.objectURL !== url) URL.revokeObjectURL(url);
      if (!isCurrent(generation, observationId) || error.name === "AbortError") return;
      $("load-status").textContent = "No disponible";
      $("image-placeholder").textContent = "La imagen no está disponible. Puedes actualizar para reintentar.";
      feedback(error.message, true);
    }
  }
  async function addRectangle(kind, rectangle) {
    if (!canEdit() || authenticationNeeded()) return;
    if (kind === "candidate" && draft().candidates.length >= 100) { feedback("El máximo es 100 candidatos por inspección.", true); return; }
    const generation = state.generation, observationId = state.entry.observation_id;
    state.geometryBusy = true;
    renderDraft(); feedback("");
    try {
      const result = await (await request("/v1/review-queue/geometry", {method: "POST", signal: state.controller.signal,
        headers: {"Content-Type": "application/json", "Authorization": `Bearer ${state.token}`},
        body: JSON.stringify({queue_id: state.queue.queue_id, observation_id: observationId,
          asset_id: state.asset.asset_id, rectangle, kind})})).json();
      if (!isCurrent(generation, observationId)) return;
      polygonPath(result.pixel_geometry); polygonPath(result.geometry);
      if (kind === "candidate" && ((state.entry.search_scope
          && !["reference_zone", "search_margin"].includes(result.location))
          || (!state.entry.search_scope && result.location === "search_margin"))) {
        throw new Error("La ubicación del candidato no coincide con el área de búsqueda.");
      }
      if (kind === "area") draft().area = result;
      else draft().candidates.push(result);
      feedback(kind === "area" ? "Área delimitada y recortada al área de búsqueda. Revisa el contorno violeta."
        : "Candidato local añadido. Su centro debe quedar dentro del área revisada al guardar.");
      renderOverlay();
    } catch (error) {
      if (!isCurrent(generation, observationId) || error.name === "AbortError") return;
      feedback(`No se añadió el rectángulo: ${error.message}`, true);
    } finally {
      if (isCurrent(generation, observationId)) { state.geometryBusy = false; renderDraft(); }
    }
  }
  async function saveInspection(event) {
    event.preventDefault();
    if (!state.imageSize || state.saving || state.geometryBusy || draft()?.receipt || authenticationNeeded()) return;
    const current = draft();
    if (!current.pendingBody) {
      if (current.decision !== "uncertain" && !current.area) { feedback("Delimita el área que revisaste antes de guardar este resultado.", true); return; }
      if (current.decision === "candidates" && !current.candidates.length) { feedback("Marca al menos un candidato local.", true); return; }
      if (current.decision === "empty" && current.candidates.length) { feedback("Un área sin embarcaciones no puede contener candidatos. Revisa el resultado o los trazados.", true); return; }
      if (current.decision === "uncertain" && !current.note.trim()) { feedback("Describe qué impide decidir sobre la imagen.", true); $("note").focus(); return; }
    }
    const body = freezeSubmission(current, {queueId: state.queue.queue_id, observationId: state.entry.observation_id,
      author: state.author, reviewerKind: state.reviewerKind});
    stopClock();
    state.saving = true;
    setTool("explore"); renderDraft(); feedback("");
    try {
      const receipt = await (await request("/v1/review-queue/first-pass", {method: "POST",
        headers: {"Content-Type": "application/json", "Authorization": `Bearer ${state.token}`}, body})).json();
      if (!matchesReceipt(receipt, body)) throw new Error("La respuesta no es el recibo de este envío. Reintenta para recuperar el recibo.");
      current.receipt = receipt;
      feedback("Primera inspección fijada. Puedes continuar explícitamente al visor.");
    } catch (error) {
      // Only known semantic rejection allows an explicit edit. All retries,
      // including renewed credentials, preserve the original JSON byte for byte.
      current.rejectedValidation = error.status === 422 && [
        "Candidate centers must lie inside the inspected area",
        "Inspected area must remain inside the configured zone",
        "Inspected area must remain inside the configured search scope",
        "Mark at least one candidate", "An empty area cannot include candidates",
        "Describe why the image is uncertain", "Delimit the inspected area before recording findings"
      ].includes(error.message);
      feedback(`No se confirmó el guardado: ${error.message} Reintentar enviará exactamente el mismo registro.${current.rejectedValidation ? " Puedes corregir los hallazgos rechazados mediante el botón de corrección." : ""}`, true);
    } finally {
      state.saving = false; renderDraft(); renderOverlay();
    }
  }

  $("auth-toggle").addEventListener("click", () => {
    $("auth-panel").hidden = !$("auth-panel").hidden;
    $("auth-toggle").setAttribute("aria-expanded", String(!$("auth-panel").hidden));
  });
  $("auth-form").addEventListener("submit", event => {
    event.preventDefault();
    const author = $("author").value.trim(), token = $("token").value.trim();
    if (!author || !token) return;
    state.author = author; state.token = token; state.reviewerKind = $("reviewer-kind").value;
    $("token").value = "";
    $("auth-panel").hidden = true; $("auth-toggle").setAttribute("aria-expanded", "false");
    renderDraft(); feedback("Identidad lista en esta sesión. La credencial se verificará al enviar.");
  });
  $("lock-review").addEventListener("click", () => {
    state.token = ""; $("token").value = ""; setTool("explore"); feedback("Credencial retirada de la memoria de esta sesión.");
  });
  $("reload").addEventListener("click", loadQueue);
  for (const kind of ["explore", "area", "candidate"]) $("tool-" + kind).addEventListener("click", () => {
    if (kind !== "explore" && (!canEdit() || authenticationNeeded())) return;
    setTool(kind);
  });
  $("zoom").addEventListener("input", event => setZoom(Number(event.target.value)));
  $("zoom-in").addEventListener("click", () => setZoom(state.zoom + .5));
  $("zoom-out").addEventListener("click", () => setZoom(state.zoom - .5));
  $("fit").addEventListener("click", () => { setZoom(1); $("stage").scrollTo(0, 0); });
  $("stage").addEventListener("keydown", event => {
    if (event.key === "+" || event.key === "=") { setZoom(state.zoom + .5); event.preventDefault(); }
    if (event.key === "-") { setZoom(state.zoom - .5); event.preventDefault(); }
    if (event.key === "Escape") { setTool("explore"); }
  });
  $("overlay").addEventListener("pointerdown", event => {
    if (state.tool === "explore" || !canEdit() || event.button !== 0) return;
    const start = normalizedPoint(event.clientX, event.clientY, $("overlay").getBoundingClientRect());
    state.drag = {start, end: start, pointerId: event.pointerId};
    $("overlay").setPointerCapture(event.pointerId);
    event.preventDefault(); renderOverlay();
  });
  $("overlay").addEventListener("pointermove", event => {
    if (!state.drag || state.drag.pointerId !== event.pointerId) return;
    state.drag.end = normalizedPoint(event.clientX, event.clientY, $("overlay").getBoundingClientRect());
    event.preventDefault(); renderOverlay();
  });
  $("overlay").addEventListener("pointerup", event => {
    if (!state.drag || state.drag.pointerId !== event.pointerId) return;
    const start = state.drag.start;
    const end = normalizedPoint(event.clientX, event.clientY, $("overlay").getBoundingClientRect());
    const bounds = $("overlay").getBoundingClientRect();
    state.drag = null;
    if ($("overlay").hasPointerCapture(event.pointerId)) $("overlay").releasePointerCapture(event.pointerId);
    renderOverlay();
    if (Math.abs(start[0] - end[0]) * bounds.width < 3 || Math.abs(start[1] - end[1]) * bounds.height < 3) {
      feedback("Dibuja un rectángulo un poco más grande o aumenta la imagen.", true); return;
    }
    addRectangle(state.tool, normalizedRectangle(start, end));
  });
  $("overlay").addEventListener("pointercancel", () => { state.drag = null; renderOverlay(); });
  $("rectangle-form").addEventListener("submit", event => {
    event.preventDefault();
    try {
      const values = ["rect-left", "rect-top", "rect-right", "rect-bottom"].map(id => Number($(id).value) / 100);
      addRectangle($("rectangle-kind").value, normalizedRectangle(values.slice(0, 2), values.slice(2)));
    } catch (error) { feedback(error.message, true); }
  });
  $("clear-area").addEventListener("click", () => {
    if (!canEdit()) return;
    draft().area = null; renderDraft(); renderOverlay();
  });
  $("decision").addEventListener("change", event => { if (canEdit()) draft().decision = event.target.value; });
  $("note").addEventListener("input", event => { if (canEdit()) draft().note = event.target.value; });
  $("first-pass-form").addEventListener("submit", saveInspection);
  $("edit-rejected").addEventListener("click", () => {
    const current = draft();
    if (!current?.rejectedValidation || current.receipt || state.saving) return;
    current.pendingBody = null;
    current.rejectedValidation = false;
    current.startedAt = Date.now();
    renderDraft();
    feedback("El servidor rechazó estos hallazgos antes de registrarlos. Corrige el área o los candidatos y vuelve a guardar.");
  });
  window.addEventListener("beforeunload", event => {
    const unsaved = [...state.drafts.values()].some(item => !item.receipt && (item.area || item.candidates.length || item.note || item.pendingBody));
    if (unsaved || state.saving) { event.preventDefault(); event.returnValue = ""; }
  });
  new ResizeObserver(fitImage).observe($("stage"));
  renderDraft(); fitImage(); loadQueue();
}
