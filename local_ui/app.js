"use strict";

(() => {
  const MAX_UPLOAD_BYTES = 1048576;
  const MAX_DOWNLOAD_BYTES = 32 * 1048576;
  const POLL_BUDGET_MS = 150000;
  const PRESETS = {vertical: "9:16", square: "1:1", feed: "4:5"};
  const $ = (id) => document.getElementById(id);
  const state = {token: null, epoch: 0, busy: false, files: null, flow: null, fixtures: [], controllers: new Set(), urls: [], logical: 0, physical: 0};

  class APIError extends Error {
    constructor(status, code) { super(code); this.status = status; this.code = code; }
  }
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  function ensureCurrent(epoch) { if (state.epoch !== epoch) throw new APIError(0, "cancelled"); }
  function log(message) {
    const entry = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString("pt-BR", {hour12: false});
    const content = document.createElement("span");
    content.textContent = message;
    entry.append(time, content);
    $("status-log").prepend(entry);
    while ($("status-log").children.length > 30) $("status-log").lastChild.remove();
    $("live-status").textContent = message;
  }
  function counts() { $("network-count").textContent = `${state.logical} operações · ${state.physical} tentativas HTTP`; }
  function phase(name, status, description) {
    const node = $(name + "-state");
    node.dataset.state = status;
    node.textContent = description;
  }
  function clearError() { $("error-panel").hidden = true; $("error-panel").textContent = ""; }
  function friendly(error) {
    if (error.status === 401) return "Sua sessão expirou ou foi encerrada. Entre novamente para continuar.";
    if (error.status === 403) return "Acesso negado. Sua sessão não tem permissão atual para esta organização ou operação.";
    if (error.status === 413) return "O arquivo ultrapassa o limite permitido. Upload: 1 MiB por arquivo; download: 32 MiB.";
    if (error.status === 429) return "A cota da organização foi atingida. A operação não criou uma nova reserva.";
    if (error.status === 503) return "O serviço está ocupado ou o banco está indisponível (503). As tentativas limitadas terminaram; consulte a mesma operação antes de reenviar.";
    if (error.status === 408 || error.code === "request_timeout") return "O tempo de resposta terminou. O resultado pode estar pendente; consulte a mesma operação para recuperar o estado.";
    if (error.code === "poll_timeout") return "O acompanhamento atingiu o limite de tempo. O backend pode continuar trabalhando; consulte a mesma operação para ver o estado real.";
    if (error.code === "uncertain") return "A operação está em estado incerto. Os dados e a cota foram preservados; consulte a mesma operação para acompanhar a recuperação.";
    if (error.code === "invalid_files") return "Selecione um vídeo MP4 e sua legenda SRT, ambos não vazios e com até 1 MiB.";
    if (error.code === "integrity") return "A verificação dos arquivos recebidos falhou. Os downloads foram bloqueados.";
    if (error.code === "no_fixture") return "Não há amostra sintética aprovada para esta organização.";
    if (error.code === "ingest_failed") return "A ingestão foi recusada. Verifique se os arquivos correspondem à amostra sintética aprovada, estão íntegros e têm áudio/SRT válidos.";
    if (error.code === "render_failed") return "O processamento falhou no backend. Consulte a atividade e o estado da mesma operação; uma nova renderização não foi criada automaticamente.";
    if (error.status === 409) return "A operação está ocupada ou conflita com um estado existente (409). Aguarde e consulte a mesma operação.";
    if (error.code === "network") return "A conexão local foi interrompida. O resultado pode ter sido salvo; consulte a mesma operação antes de reenviar.";
    return `A operação não foi concluída${error.status ? ` (HTTP ${error.status})` : ""}. Verifique os arquivos e consulte o estado antes de tentar novamente.`;
  }
  function showError(error) {
    if (error.code === "cancelled") return;
    const message = friendly(error);
    if (error.status === 401 && state.token) resetSession();
    $("error-panel").textContent = message;
    $("error-panel").hidden = false;
    $("error-panel").focus();
    $("live-status").textContent = message;
    if (state.token && state.flow) $("recover-button").hidden = false;
  }
  function busy(value) {
    state.busy = value;
    document.body.dataset.busy = String(value);
    $("workspace").setAttribute("aria-busy", String(value));
    $("login-button").disabled = value;
    $("org-select").disabled = value;
    $("video-file").disabled = value;
    $("caption-file").disabled = value;
    $("sample-button").disabled = value || !state.fixtures.length;
    $("upload-button").disabled = value || !state.files;
    $("recover-button").disabled = value;
    $("render-button").disabled = value || !state.flow?.asset || Boolean(state.flow?.manifest);
  }
  function resetOutputs() {
    for (const url of state.urls) URL.revokeObjectURL(url);
    state.urls = [];
    $("output-grid").replaceChildren();
    $("outputs-panel").hidden = true;
    $("manifest-download").removeAttribute("href");
  }
  function resetFlow() {
    state.flow = null;
    resetOutputs();
    phase("upload", "idle", "Aguardando arquivos");
    phase("ingest", "idle", "Aguardando upload");
    phase("render", "idle", "Aguardando ingestão");
    phase("verification", "idle", "Aguardando renderização");
    $("recover-button").hidden = true;
    $("job-reference").hidden = true;
    $("job-reference").textContent = "";
  }
  function resetSession() {
    state.epoch += 1;
    state.token = null;
    for (const controller of state.controllers) controller.abort();
    state.controllers.clear();
    state.files = null;
    state.fixtures = [];
    $("video-file").value = "";
    $("caption-file").value = "";
    $("org-select").replaceChildren();
    $("session-label").textContent = "Sessão autenticada";
    $("video-description").textContent = "Nenhum vídeo selecionado.";
    $("caption-description").textContent = "Nenhuma legenda selecionada.";
    $("status-log").replaceChildren();
    state.logical = 0;
    state.physical = 0;
    counts();
    resetFlow();
    $("workspace").hidden = true;
    $("login-panel").hidden = false;
    document.body.dataset.session = "anonymous";
    busy(false);
  }
  function orgPath(resource) { return `/local/v1/orgs/${encodeURIComponent($("org-select").value)}/${resource}`; }
  function sharedPath(resource) { return `/v1/orgs/${encodeURIComponent($("org-select").value)}/${resource}`; }
  function validLocalPath(path) {
    if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.length > 8192 || /[?#\\]/.test(path)) throw new APIError(0, "invalid_path");
    return path;
  }
  async function request(path, {method = "GET", json, body, auth = true, tokenOverride, retry = false, label = "Consulta", binary = false} = {}) {
    validLocalPath(path);
    const epoch = state.epoch;
    const started = performance.now();
    const payload = json === undefined ? body : JSON.stringify(json);
    const headers = {};
    if (json !== undefined) headers["Content-Type"] = "application/json";
    else if (body !== undefined) headers["Content-Type"] = "application/octet-stream";
    if (auth) {
      const token = tokenOverride || state.token;
      if (!token) throw new APIError(401, "authentication_required");
      headers.Authorization = `Bearer ${token}`;
    }
    state.logical += 1;
    counts();
    const maximum = retry ? 4 : 1;
    for (let attempt = 1; attempt <= maximum; attempt += 1) {
      if (state.epoch !== epoch) throw new APIError(0, "cancelled");
      const remaining = 20000 - (performance.now() - started);
      if (remaining <= 0) throw new APIError(408, "request_timeout");
      const controller = new AbortController();
      state.controllers.add(controller);
      const timer = setTimeout(() => controller.abort(), Math.min(15000, remaining));
      let response;
      state.physical += 1;
      counts();
      try {
        response = await fetch(path, {method, headers, body: payload, signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error"});
        if (state.epoch !== epoch) throw new APIError(0, "cancelled");
        if (response.status === 503 && attempt < maximum) {
          await response.arrayBuffer();
          ensureCurrent(epoch);
          log(`${label}: resposta 503; tentativa ${attempt + 1}/${maximum} com a mesma operação.`);
        } else if (!response.ok) {
          let result;
          try { result = await response.json(); } catch (_) { result = {}; }
          ensureCurrent(epoch);
          throw new APIError(response.status, typeof result.error === "string" ? result.error : "request_failed");
        } else if (binary) {
          const limit = path.includes("/fixtures/") ? MAX_UPLOAD_BYTES : MAX_DOWNLOAD_BYTES;
          const declared = Number(response.headers.get("Content-Length"));
          if (declared > limit) throw new APIError(413, "body_too_large");
          const blob = await response.blob();
          ensureCurrent(epoch);
          if (blob.size > limit || blob.size === 0) throw new APIError(413, "body_too_large");
          return blob;
        } else {
          const result = response.status === 204 ? {} : await response.json();
          ensureCurrent(epoch);
          return result;
        }
      } catch (error) {
        if (error instanceof APIError) throw error;
        if (state.epoch !== epoch) throw new APIError(0, "cancelled");
        if (controller.signal.aborted) throw new APIError(408, "request_timeout");
        throw new APIError(0, "network");
      } finally {
        clearTimeout(timer);
        state.controllers.delete(controller);
      }
      await sleep(Math.min(800, 200 * 2 ** (attempt - 1)));
    }
    throw new APIError(503, "database_unavailable");
  }
  async function digest(blob) {
    const epoch = state.epoch;
    const hash = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    ensureCurrent(epoch);
    return Array.from(new Uint8Array(hash), (x) => x.toString(16).padStart(2, "0")).join("");
  }
  function fileDescription(file) { return `${file.name} · ${Math.ceil(file.size / 1024)} KiB`; }
  function updateFiles(video, caption) {
    resetFlow();
    state.files = video && caption ? {video, caption} : null;
    $("video-description").textContent = video ? fileDescription(video) : "Nenhum vídeo selecionado.";
    $("caption-description").textContent = caption ? fileDescription(caption) : "Nenhuma legenda selecionada.";
  }
  async function loadFixtures() {
    const result = await request("/local/v1/fixtures", {label: "Amostras"});
    state.fixtures = (Array.isArray(result.fixtures) ? result.fixtures : []).filter((fixture) => !fixture.organization_id || fixture.organization_id === $("org-select").value);
  }
  async function login(event) {
    event.preventDefault();
    if (state.busy) return;
    state.epoch += 1;
    const epoch = state.epoch;
    clearError();
    busy(true);
    try {
      const auth = await request("/local/v1/auth/login", {method: "POST", auth: false, json: {email: $("email").value.trim(), password: $("password").value}, label: "Autenticação"});
      if (typeof auth.access_token !== "string") throw new APIError(401, "invalid_session");
      state.token = auth.access_token;
      $("password").value = "";
      const me = await request("/local/v1/me", {label: "Organizações"});
      if (!Array.isArray(me.organizations) || !me.organizations.length) throw new APIError(403, "organization_access_denied");
      $("org-select").replaceChildren();
      for (const org of me.organizations) {
        const option = document.createElement("option"); option.value = org.id; option.textContent = org.name || org.id; $("org-select").append(option);
      }
      $("session-label").textContent = $("email").value.trim();
      $("workspace").hidden = false;
      $("login-panel").hidden = true;
      document.body.dataset.session = "authenticated";
      await loadFixtures();
      log("Sessão local autenticada; organizações consultadas no backend.");
    } catch (error) {
      if (error.code === "cancelled") return;
      if (state.token) resetSession();
      showError(error);
    } finally { if (state.epoch === epoch) busy(false); }
  }
  async function logout() {
    const token = state.token;
    resetSession();
    const epoch = state.epoch;
    clearError();
    try {
      await request("/local/v1/auth/logout", {method: "POST", tokenOverride: token, label: "Logout"});
      $("live-status").textContent = "Sessão encerrada no Auth local.";
    } catch (error) {
      if (state.epoch !== epoch) return;
      $("error-panel").textContent = "Os dados da sessão foram removidos deste navegador, mas o logout no Auth local não pôde ser confirmado. Verifique a conexão local.";
      $("error-panel").hidden = false;
    }
    if (state.epoch === epoch) $("email").focus();
  }
  async function sample() {
    const epoch = state.epoch;
    clearError(); busy(true);
    try {
      const fixture = state.fixtures[0];
      if (!fixture) throw new APIError(0, "no_fixture");
      const prefix = `/local/v1/fixtures/${encodeURIComponent(fixture.id)}`;
      const video = await request(prefix + "/video", {binary: true, label: "Amostra de vídeo"});
      const caption = await request(prefix + "/caption", {binary: true, label: "Amostra de legenda"});
      const files = {video: new File([video], fixture.video_name || "amostra-sintetica.mp4", {type: "video/mp4"}), caption: new File([caption], fixture.caption_name || "amostra-sintetica.srt", {type: "application/x-subrip"})};
      updateFiles(files.video, files.caption);
      $("video-file").value = ""; $("caption-file").value = "";
      log("Amostra sintética carregada do ambiente local. Pronta para enviar.");
    } catch (error) { showError(error); } finally { if (state.epoch === epoch) busy(false); }
  }
  function prepareFlow() {
    if (!state.files || Object.values(state.files).some((file) => !file.size || file.size > MAX_UPLOAD_BYTES) || !state.files.video.name.toLowerCase().endsWith(".mp4") || !state.files.caption.name.toLowerCase().endsWith(".srt")) throw new APIError(0, "invalid_files");
    if (!state.flow) state.flow = {organization: $("org-select").value, video: {file: state.files.video, key: "ui-video-" + crypto.randomUUID()}, caption: {file: state.files.caption, key: "ui-srt-" + crypto.randomUUID()}, ingestKey: "ui-ingest-" + crypto.randomUUID(), renderKey: "ui-render-" + crypto.randomUUID()};
    return state.flow;
  }
  async function uploadOne(item, label, recovering) {
    if (item.receipt) return item.receipt;
    item.sha256 ||= await digest(item.file);
    if (!item.grant) item.grant = await request(sharedPath("uploads"), {method: "POST", json: {request_key: item.key, size_bytes: item.file.size, sha256: item.sha256}, retry: true, label: "Reserva " + label});
    try {
      item.receipt = await request(item.grant.upload_path, {method: "PUT", body: item.file, retry: true, label: "Upload " + label});
    } catch (error) {
      if (error.status === 401 || error.status === 403) throw error;
      try {
        item.receipt = await request(sharedPath("uploads/") + encodeURIComponent(item.grant.id), {retry: true, label: "Reconciliação " + label});
      } catch (lookupError) {
        if (lookupError.status === 401 || lookupError.status === 403) throw lookupError;
        if (!recovering) throw error;
        item.receipt = await request(sharedPath("uploads/") + encodeURIComponent(item.grant.id) + "/resume", {method: "POST", body: item.file, retry: true, label: "Retomada " + label});
      }
    }
    if (item.receipt.sha256 !== item.sha256 || item.receipt.size_bytes !== item.file.size) throw new APIError(409, "integrity");
    log(`${label}: recibo confirmado, ${item.file.size} bytes e SHA-256 conferidos.`);
    return item.receipt;
  }
  const ingestLabels = {queued: "Na fila de validação", validating: "Serviço interno validando bytes, mídia e SRT", running: "Validação em execução", completed: "Asset validado e com proveniência", failed: "Validação recusada", uncertain: "Resultado incerto; dados preservados"};
  const renderLabels = {queued: "Na fila do outbox / worker", running: "FFmpeg em execução", completed: "Três vídeos concluídos pelo worker", failed: "Processamento falhou", uncertain: "Execução incerta; aguardando reconciliação"};
  async function poll(path, kind) {
    const epoch = state.epoch;
    const deadline = performance.now() + POLL_BUDGET_MS;
    let previous;
    while (performance.now() < deadline) {
      if (state.epoch !== epoch) throw new APIError(0, "cancelled");
      const result = await request(path, {retry: true, label: kind === "ingest" ? "Estado da ingestão" : "Estado do render"});
      const status = result.state;
      const label = (kind === "ingest" ? ingestLabels : renderLabels)[status] || `Estado recebido: ${String(status).slice(0,40)}`;
      phase(kind, status, label);
      if (previous !== status) log(`${kind === "ingest" ? "Ingestão" : "Render"}: ${label}.`);
      previous = status;
      if (status === "completed") return result;
      if (status === "failed") throw new APIError(409, kind === "ingest" ? "ingest_failed" : "render_failed");
      if (status === "uncertain") throw new APIError(409, "uncertain");
      await sleep(500);
    }
    throw new APIError(408, "poll_timeout");
  }
  async function uploadAndValidate(recovering = false) {
    const epoch = state.epoch;
    clearError(); busy(true);
    try {
      const flow = prepareFlow();
      if (flow.job || flow.renderRequested) { await submitAndObserve(flow); return; }
      if (flow.asset) { log("O asset já está validado. A renderização pode ser solicitada."); return; }
      phase("upload", "publishing", "Reservando e enviando os arquivos privados");
      await uploadOne(flow.video, "Vídeo", recovering);
      await uploadOne(flow.caption, "Legenda", recovering);
      phase("upload", "uploaded", "Vídeo e SRT com recibos verificados");
      if (!flow.ingestion) flow.ingestion = await request(orgPath("ingestions"), {method: "POST", json: {request_key: flow.ingestKey, video_receipt_id: flow.video.grant.id, caption_receipt_id: flow.caption.grant.id}, retry: true, label: "Solicitação de ingestão"});
      const validated = await poll(orgPath("ingestions/") + encodeURIComponent(flow.ingestion.id), "ingest");
      const asset = validated.asset_id || validated.asset?.id;
      const duration = validated.duration_ms || validated.metadata?.duration_ms || validated.asset?.duration_ms;
      if (!asset || !Number.isInteger(duration) || duration <= 0) throw new APIError(409, "ingest_failed");
      flow.asset = asset;
      flow.duration = duration;
      $("recover-button").hidden = true;
      log("Validação concluída. O botão Renderizar cria um job real no PostgreSQL.");
      $("live-status").textContent = "Asset validado. Renderizar 3 vídeos está disponível.";
    } catch (error) { showError(error); } finally { if (state.epoch === epoch) busy(false); }
  }
  function blobURL(blob) { const url = URL.createObjectURL(blob); state.urls.push(url); return url; }
  async function showOutputs(flow) {
    phase("verification", "verifying", "Baixando e conferindo os três arquivos");
    const result = await request(orgPath("renders/") + encodeURIComponent(flow.job.id) + "/manifest", {retry: true, label: "Manifesto"});
    const manifest = result.manifest || result;
    if (!Array.isArray(manifest.outputs) || manifest.outputs.length !== 3 || new Set(manifest.outputs.map((x) => x.preset)).size !== 3) throw new APIError(409, "integrity");
    const ready = [];
    for (const preset of Object.keys(PRESETS)) {
      const output = manifest.outputs.find((row) => row.preset === preset);
      if (!output || typeof output.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(output.sha256) || !Number.isInteger(output.size_bytes) || output.size_bytes <= 0 || output.size_bytes > MAX_DOWNLOAD_BYTES) throw new APIError(409, "integrity");
      const grant = await request(sharedPath("renders/") + encodeURIComponent(flow.job.id) + "/downloads", {method: "POST", json: {preset}, retry: true, label: "Autorização de download"});
      if (typeof grant.download_path !== "string" || !grant.download_path.startsWith("/v1/downloads/")) throw new APIError(409, "integrity");
      const blob = await request(grant.download_path, {binary: true, retry: true, label: "Download " + PRESETS[preset]});
      if (blob.size !== output.size_bytes || await digest(blob) !== output.sha256) throw new APIError(409, "integrity");
      ready.push({preset, output, blob});
    }
    resetOutputs();
    for (const {preset, output, blob} of ready) {
      const card = document.createElement("article"); card.className = "output-card"; card.dataset.preset = preset; card.dataset.verified = "true";
      const stage = document.createElement("div"); stage.className = "video-stage";
      const video = document.createElement("video"); video.controls = true; video.playsInline = true; video.preload = "metadata"; video.src = blobURL(blob); video.setAttribute("aria-label", `Prévia ${PRESETS[preset]}`); stage.append(video);
      const info = document.createElement("div"); info.className = "output-info";
      const top = document.createElement("div"); top.className = "output-topline";
      const title = document.createElement("h3"); title.textContent = PRESETS[preset];
      const verified = document.createElement("span"); verified.className = "verified-badge"; verified.textContent = "SHA-256 OK"; top.append(title, verified);
      const meta = document.createElement("p"); meta.className = "output-meta"; meta.textContent = `${output.width} × ${output.height} · ${Number(output.duration_seconds).toFixed(2)} s · ${Math.ceil(output.size_bytes / 1024)} KiB${output.has_audio ? " · Áudio preservado" : " · Sem áudio"}`;
      const hash = document.createElement("p"); hash.className = "output-hash"; hash.textContent = `SHA-256 ${output.sha256}`;
      const link = document.createElement("a"); link.className = "button button-primary"; link.id = "download-" + preset; link.dataset.testid = link.id; link.href = video.src; link.download = `let-them-cook-${preset}.mp4`; link.textContent = "Baixar MP4 " + PRESETS[preset];
      info.append(top, meta, hash, link); card.append(stage, info); $("output-grid").append(card);
    }
    $("manifest-download").href = blobURL(new Blob([JSON.stringify(manifest, null, 2)], {type: "application/json"}));
    $("outputs-panel").hidden = false;
    flow.manifest = manifest;
    phase("verification", "verified", "3/3 arquivos recebidos com SHA-256 correto");
    $("recover-button").hidden = true;
    log("Três vídeos verificados no navegador. Downloads e manifesto disponíveis.");
    $("live-status").textContent = "Processamento concluído. Três downloads verificados estão disponíveis.";
  }
  async function observeRender(flow) {
    await poll(sharedPath("renders/") + encodeURIComponent(flow.job.id), "render");
    await showOutputs(flow);
  }
  async function submitAndObserve(flow) {
    flow.renderRequested = true;
    if (!flow.job) flow.job = await request(sharedPath("renders"), {method: "POST", json: {request_key: flow.renderKey, asset_id: flow.asset, duration_ms: flow.duration, quality: "smoke"}, retry: true, label: "Submissão de render"});
    $("job-reference").textContent = "Job: " + flow.job.id;
    $("job-reference").hidden = false;
    await observeRender(flow);
  }
  async function render() {
    const epoch = state.epoch;
    clearError(); busy(true);
    try {
      const flow = state.flow;
      if (!flow?.asset) throw new APIError(409, "ingest_failed");
      await submitAndObserve(flow);
    } catch (error) { showError(error); } finally { if (state.epoch === epoch) busy(false); }
  }

  $("login-form").addEventListener("submit", login);
  $("logout-button").addEventListener("click", logout);
  $("sample-button").addEventListener("click", sample);
  $("upload-button").addEventListener("click", () => uploadAndValidate(false));
  $("recover-button").addEventListener("click", () => uploadAndValidate(true));
  $("render-button").addEventListener("click", render);
  for (const id of ["video-file", "caption-file"]) $(id).addEventListener("change", () => { clearError(); updateFiles($("video-file").files[0], $("caption-file").files[0]); busy(false); });
  $("org-select").addEventListener("change", async () => {
    const epoch = state.epoch;
    clearError(); state.files = null; $("video-file").value = ""; $("caption-file").value = ""; updateFiles(null, null);
    busy(true);
    try { await loadFixtures(); } catch (error) { showError(error); } finally { if (state.epoch === epoch) busy(false); }
  });
  window.addEventListener("pagehide", () => { for (const controller of state.controllers) controller.abort(); resetOutputs(); });
  busy(false);
})();
