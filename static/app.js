const statusMessage = document.getElementById("status-message");
const form = document.getElementById("analyze-form");
const input = document.getElementById("url-input");
const dropzone = document.getElementById("dropzone");
const historyCards = document.getElementById("history-cards");
const historyTableBody = document.getElementById("history-table-body");
const historyTableWrap = document.getElementById("history-table-wrap");
const clearHistoryButton = document.getElementById("clear-history");
const detailsModal = document.getElementById("details-modal");
const detailsTitle = document.getElementById("details-title");
const detailsUrl = document.getElementById("details-url");
const detailsStatus = document.getElementById("details-status");
const detailsDate = document.getElementById("details-date");
const detailsError = document.getElementById("details-error");
const detailsStreams = document.getElementById("details-streams");
const detailsTrace = document.getElementById("details-trace");
const detailsSourceType = document.getElementById("details-source-type");
const detailsVideos = document.getElementById("details-videos");
const detailsAssets = document.getElementById("details-assets");
const historySearch = document.getElementById("history-search");
const historyStatus = document.getElementById("history-status");
const historyMedia = document.getElementById("history-media");
const historyPerPage = document.getElementById("history-per-page");
const historyApply = document.getElementById("history-apply");
const historyReset = document.getElementById("history-reset");
const historyPrev = document.getElementById("history-prev");
const historyNext = document.getElementById("history-next");
const historyPage = document.getElementById("history-page");
const historyCache = new Map();
const historyModeButtons = document.querySelectorAll("[data-history-view]");
const historyMeta = document.getElementById("history-meta");
const latestAnalysisContent = document.getElementById("latest-analysis-content");
const toast = document.getElementById("toast");
const submitButton = form.querySelector("button[type='submit']");
const statAnalyses = document.getElementById("stat-analyses");
const statStreams = document.getElementById("stat-streams");
const statVideos = document.getElementById("stat-videos");
const statResources = document.getElementById("stat-resources");
const statDirect = document.getElementById("stat-direct");
const statSources = document.getElementById("stat-sources");
const dashboardTotal = document.getElementById("dashboard-total");
const dashboardStatusTotal = document.getElementById("dashboard-status-total");
const dashboardMediaTotal = document.getElementById("dashboard-media-total");
const dashboardSourceTotal = document.getElementById("dashboard-source-total");
const dashboardStatusChart = document.getElementById("dashboard-status-chart");
const dashboardMediaChart = document.getElementById("dashboard-media-chart");
const dashboardSourceChart = document.getElementById("dashboard-source-chart");

const historyState = {
  page: 1,
  perPage: Number(historyPerPage?.value || 10),
  status: historyStatus?.value || "all",
  media: historyMedia?.value || "all",
  search: historySearch?.value || "",
  view: localStorage.getItem("history-view") || "cards",
  grouped: true,
};

function setStatus(message, isError = false) {
  statusMessage.textContent = message;
  statusMessage.style.color = isError ? "#fecaca" : "";
}

function extractUrls(text) {
  const matches = text.match(/https?:\/\/[^\s'"<>]+/gi) || [];
  return [...new Set(matches.map((item) => item.trim()))];
}

function getItemStreams(item) {
  return Array.isArray(item.streams) ? item.streams : [];
}

function getItemVideos(item) {
  return Array.isArray(item.videos) ? item.videos : [];
}

function getExtraAssets(item) {
  return [
    ...(Array.isArray(item.documents) ? item.documents : []),
    ...(Array.isArray(item.images) ? item.images : []),
    ...(Array.isArray(item.other_assets) ? item.other_assets : []),
  ];
}

function getPrimaryStream(item) {
  const streams = getItemStreams(item);
  return streams.length ? streams[0] : null;
}

function escapeText(value) {
  return escapeHtml(value);
}

function renderCopyButton(value, label = "Copier") {
  if (!value) {
    return `<button class="copy-button" type="button" disabled>${label}</button>`;
  }
  return `<button class="copy-button" type="button" data-copy="${escapeAttr(value)}">${label}</button>`;
}

function renderOpenButton(value, label = "Ouvrir") {
  if (!value) {
    return `<button class="open-button" type="button" disabled>${label}</button>`;
  }
  return `<a class="open-button" href="${escapeAttr(value)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

function renderValueActions(value, copyLabel = "Copier", openLabel = "Ouvrir") {
  return `
    <div class="pill-actions">
      ${renderCopyButton(value, copyLabel)}
      ${renderOpenButton(value, openLabel)}
    </div>
  `;
}

function renderStreamBlock(value, label = "Copier") {
  return `
    <div class="stream-pill">
      <code title="${escapeAttr(value)}">${escapeText(value)}</code>
      ${renderValueActions(value, label)}
    </div>
  `;
}

function renderCompactValueList(values, emptyLabel) {
  if (!values.length) {
    return `<span class="muted">${escapeText(emptyLabel)}</span>`;
  }
  const [first, ...rest] = values;
  return `
    <div class="cell-stack">
      ${renderStreamBlock(first)}
      ${rest.length ? `<span class="muted">+ ${rest.length} autre(s)</span>` : ""}
    </div>
  `;
}

function renderAssetList(values) {
  if (!values.length) {
    return '<span class="muted">Aucune</span>';
  }
  return `
    <div class="cell-stack">
      ${values.map((value) => renderStreamBlock(value)).join("")}
    </div>
  `;
}

function renderHistoryCard(item) {
  const streams = getItemStreams(item);
  const primaryStream = getPrimaryStream(item);
  const extraAssets = getExtraAssets(item);
  const card = document.createElement("article");
  card.className = "history-card";
  card.dataset.id = item.id;
  card.innerHTML = `
    <div class="history-card-top">
      <div class="history-card-heading">
        <h3>${escapeText(item.page_title || "Sans titre")}</h3>
        <p class="history-card-url truncate"><a href="${escapeAttr(item.page_url)}" target="_blank" rel="noreferrer" title="${escapeAttr(item.page_url)}">${escapeText(item.page_url)}</a></p>
      </div>
      <div class="history-card-badges">
        <span class="badge status-${escapeAttr(item.status)}">${escapeText(item.status)}</span>
        <span class="badge badge-source source-${escapeAttr(item.source_type || "unknown")}">${escapeText(item.source_label || "Inconnue")}</span>
      </div>
    </div>
    <div class="history-card-body">
      <div class="history-card-block">
        <span class="latest-label">Flux détecté</span>
        ${primaryStream ? renderStreamBlock(primaryStream) : '<span class="muted">Aucun flux détecté</span>'}
        ${streams.length > 1 ? `<span class="muted">+ ${streams.length - 1} autre(s)</span>` : ""}
      </div>
      <div class="history-card-block history-card-metrics">
        <span class="mini-chip">Date ${escapeText(item.scanned_at)}</span>
        <span class="mini-chip">Ressources ${Number(item.asset_count || extraAssets.length || 0)}</span>
        <span class="mini-chip">Vidéos ${Number(item.video_count || getItemVideos(item).length || 0)}</span>
        <span class="mini-chip">Source ${escapeText(item.source_label || "Inconnue")}</span>
      </div>
    </div>
    <div class="history-card-actions">
      ${renderValueActions(item.page_url, "Copier URL", "Ouvrir URL")}
      ${renderValueActions(primaryStream, "Copier flux", "Ouvrir flux")}
      <button class="ghost-button details-row" type="button">Voir détails</button>
      <a class="ghost-button" href="/analysis/${item.id}" target="_blank" rel="noreferrer">Aperçu</a>
      <button class="danger-button delete-row" type="button">Supprimer</button>
    </div>
  `;
  return card;
}

function renderHistoryTableRow(item) {
  const streams = getItemStreams(item);
  const videos = getItemVideos(item);
  const extraAssets = getExtraAssets(item);
  const row = document.createElement("tr");
  row.dataset.id = item.id;
  row.innerHTML = `
    <td>${item.id}</td>
    <td>${escapeText(item.page_title || "Sans titre")}</td>
    <td class="cell-truncate">
      <div class="cell-link-row">
        <a href="${escapeAttr(item.page_url)}" target="_blank" rel="noreferrer" title="${escapeAttr(item.page_url)}">${escapeText(item.page_url)}</a>
        ${renderValueActions(item.page_url, "Copier", "Ouvrir")}
      </div>
    </td>
    <td>${renderCompactValueList(streams, "Aucun")}</td>
    <td>${renderCompactValueList(videos, "Aucun")}</td>
    <td>${renderCompactValueList(extraAssets, "Aucune")}</td>
    <td><span class="badge badge-source source-${escapeAttr(item.source_type || "unknown")}">${escapeText(item.source_label || "Inconnue")}</span></td>
    <td>${escapeText(item.scanned_at)}</td>
    <td><span class="badge status-${escapeAttr(item.status)}">${escapeText(item.status)}</span></td>
    <td>
      <div class="table-actions">
        <button class="ghost-button details-row" type="button">Détails</button>
        <a class="ghost-button" href="/analysis/${item.id}" target="_blank" rel="noreferrer">Page</a>
        <button class="danger-button delete-row" type="button">Supprimer</button>
      </div>
    </td>
  `;
  return row;
}

function renderLatestAnalysis(item) {
  if (!latestAnalysisContent) return;
  if (!item) {
    latestAnalysisContent.innerHTML = `
      <div class="empty-state">
        <strong>Aucune analyse enregistrée.</strong>
        <p>Colle une URL ou dépose un fichier texte pour lancer la première inspection locale.</p>
      </div>
    `;
    return;
  }

  const primaryStream = getPrimaryStream(item);
  latestAnalysisContent.innerHTML = `
    <article class="latest-card" data-id="${item.id}">
      <div class="latest-top">
        <div>
          <h3>${escapeText(item.page_title || "Sans titre")}</h3>
          <p class="latest-url truncate"><a href="${escapeAttr(item.page_url)}" target="_blank" rel="noreferrer" title="${escapeAttr(item.page_url)}">${escapeText(item.page_url)}</a></p>
        </div>
        <div class="latest-statuses">
          <span class="badge status-${escapeAttr(item.status)}">${escapeText(item.status)}</span>
          <span class="badge badge-source source-${escapeAttr(item.source_type || "unknown")}">${escapeText(item.source_label || "Inconnue")}</span>
        </div>
      </div>
      <div class="latest-grid">
        <div class="latest-block">
          <span class="latest-label">Flux .m3u8</span>
          <div class="latest-value-list">
            ${primaryStream ? `
              <div class="stream-pill">
                <code title="${escapeAttr(primaryStream)}">${escapeText(primaryStream)}</code>
                ${renderValueActions(primaryStream)}
              </div>
              ${getItemStreams(item).length > 1 ? `<span class="muted">+ ${getItemStreams(item).length - 1} autre(s)</span>` : ""}
            ` : '<span class="muted">Aucun flux détecté</span>'}
          </div>
        </div>
        <div class="latest-block">
          <span class="latest-label">Ressources</span>
          <div class="latest-meta-row">
            <span class="mini-chip">Vidéos ${Number(item.video_count || 0)}</span>
            <span class="mini-chip">Ressources ${Number(item.asset_count || 0)}</span>
            <span class="mini-chip">Date ${escapeText(item.scanned_at)}</span>
          </div>
        </div>
      </div>
      <div class="latest-actions">
        <button class="ghost-button details-row" type="button">Aperçu</button>
        <a class="ghost-button" href="/analysis/${item.id}" target="_blank" rel="noreferrer">Rapport</a>
        <button class="danger-button delete-row" type="button">Supprimer</button>
      </div>
    </article>
  `;
}

function renderHistory(items) {
  historyCache.clear();
  if (historyCards) {
    historyCards.innerHTML = "";
  }
  if (historyTableBody) {
    historyTableBody.innerHTML = "";
  }

  if (!items.length) {
    if (historyCards) {
      historyCards.innerHTML = `
        <div class="empty-state history-empty">
          <strong>Aucun résultat.</strong>
          <p>La prochaine analyse apparaîtra ici sous forme de carte, puis dans le tableau expert si besoin.</p>
        </div>
      `;
    }
    return;
  }

  items.forEach((item) => {
    historyCache.set(String(item.id), item);
    if (historyCards) {
      historyCards.appendChild(renderHistoryCard(item));
    }
    if (historyTableBody) {
      historyTableBody.appendChild(renderHistoryTableRow(item));
    }
  });
}

function setHistoryView(view) {
  historyState.view = view === "table" ? "table" : "cards";
  localStorage.setItem("history-view", historyState.view);

  if (historyCards) {
    historyCards.classList.toggle("hidden", historyState.view === "table");
  }
  if (historyTableWrap) {
    historyTableWrap.classList.toggle("hidden", historyState.view !== "table");
  }

  historyModeButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.historyView === historyState.view);
  });
}

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.classList.remove("show");
  }, 1800);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

async function analyzeOne(url) {
  const response = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await response.json();
  if (!response.ok && !data.status) {
    throw new Error("Erreur serveur");
  }
  return data;
}

function buildHistoryQuery() {
  const params = new URLSearchParams({
    page: String(historyState.page),
    per_page: String(historyState.perPage),
    status: historyState.status,
    media: historyState.media,
    search: historyState.search,
    grouped: historyState.grouped ? "1" : "0",
  });
  return params.toString();
}

async function refreshHistory() {
  const response = await fetch(`/api/history?${buildHistoryQuery()}`);
  const data = await response.json();
  renderHistory(data.items || []);
  updateHeroStats(data.items, data.pagination);
  updateDashboard(data.summary);
  renderLatestAnalysis((data.items || [])[0] || null);
  setHistoryView(historyState.view);

  const pagination = data.pagination || {};
  const totalItems = pagination.total_items ?? data.items.length;
  historyMeta.textContent = `${totalItems} analyse(s)`;
  historyPage.textContent = `Page ${pagination.page || 1} / ${pagination.total_pages || 1}`;
  historyPrev.disabled = !pagination.has_previous;
  historyNext.disabled = !pagination.has_next;
}

function updateHeroStats(items, pagination) {
  const totalAnalyses = pagination?.total_items ?? items.length;
  const totalStreams = items.reduce((sum, item) => sum + Number(item.stream_count || 0), 0);
  const totalVideos = items.reduce((sum, item) => sum + Number(item.video_count || 0), 0);
  const totalResources = items.reduce((sum, item) => sum + Number(item.asset_count || 0), 0);
  const directCount = items.filter((item) => item.source_type === "direct").length;
  const sourceTypes = new Set(items.map((item) => item.source_type || "unknown"));

  statAnalyses.textContent = String(totalAnalyses);
  statStreams.textContent = String(totalStreams);
  if (statVideos) {
    statVideos.textContent = String(totalVideos);
  }
  if (statResources) {
    statResources.textContent = String(totalResources);
  }
  statDirect.textContent = String(directCount);
  statSources.textContent = String(sourceTypes.size);
}

function renderChart(container, entries, emptyLabel) {
  if (!container) return;
  container.innerHTML = "";
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "chart-empty";
    empty.textContent = emptyLabel;
    container.appendChild(empty);
    return;
  }
  const maxValue = Math.max(...entries.map((entry) => entry.value), 1);
  entries.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "chart-row";

    const label = document.createElement("span");
    label.className = "chart-label";
    label.textContent = entry.label;

    const barTrack = document.createElement("div");
    barTrack.className = "chart-bar";

    const barFill = document.createElement("div");
    barFill.className = `chart-fill ${entry.variant || ""}`.trim();
    barFill.style.width = `${Math.max(8, (entry.value / maxValue) * 100)}%`;
    barTrack.appendChild(barFill);

    const value = document.createElement("span");
    value.className = "chart-value";
    value.textContent = String(entry.value);

    row.append(label, barTrack, value);
    container.appendChild(row);
  });
}

function updateDashboard(summary) {
  const safeSummary = summary || {};
  const statusCounts = safeSummary.status_counts || {};
  const typeCounts = safeSummary.asset_counts || {};
  const sourceCounts = safeSummary.source_counts || {};

  const statusEntries = Object.entries(statusCounts)
    .map(([label, value]) => ({ label, value, variant: `status-${label}` }))
    .sort((a, b) => b.value - a.value);
  const mediaEntries = Object.entries(typeCounts)
    .map(([label, value]) => ({ label, value, variant: `type-${label}` }))
    .sort((a, b) => b.value - a.value);
  const sourceEntries = Object.entries(sourceCounts)
    .map(([label, value]) => ({ label, value, variant: `source-${label}` }))
    .sort((a, b) => b.value - a.value);

  renderChart(dashboardStatusChart, statusEntries, "Aucune donnée de statut.");
  renderChart(dashboardMediaChart, mediaEntries, "Aucune donnée de type.");
  renderChart(dashboardSourceChart, sourceEntries, "Aucune donnée source.");

  const statusTotal = statusEntries.reduce((sum, entry) => sum + entry.value, 0);
  const mediaTotal = mediaEntries.reduce((sum, entry) => sum + entry.value, 0);
  const sourceTotal = sourceEntries.reduce((sum, entry) => sum + entry.value, 0);

  if (dashboardTotal) {
    dashboardTotal.textContent = `${safeSummary.total_items ?? 0} analyses filtrées`;
  }
  if (dashboardStatusTotal) {
    dashboardStatusTotal.textContent = `${statusTotal} entrées`;
  }
  if (dashboardMediaTotal) {
    dashboardMediaTotal.textContent = `${mediaTotal} entrées`;
  }
  if (dashboardSourceTotal) {
    dashboardSourceTotal.textContent = `${sourceTotal} entrées`;
  }
}

function openDetails(item) {
  detailsTitle.textContent = item.page_title || "Sans titre";
  detailsUrl.textContent = item.page_url || "-";
  detailsStatus.textContent = item.status || "-";
  detailsDate.textContent = item.scanned_at || "-";
  detailsError.textContent = item.error_message || "Aucune";
  detailsSourceType.textContent = item.source_label || "Inconnue";
  detailsSourceType.className = `badge badge-source source-${escapeAttr(item.source_type || "unknown")}`;

  detailsStreams.innerHTML = "";
  const streams = Array.isArray(item.streams) ? item.streams : [];
  if (streams.length) {
    streams.forEach((stream) => {
      const wrapper = document.createElement("div");
      wrapper.className = "stream-pill";
      const code = document.createElement("code");
      code.textContent = stream;
      const actions = document.createElement("div");
      actions.className = "pill-actions";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "copy-button";
      copy.dataset.copy = stream;
      copy.textContent = "Copier";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "open-button";
      open.dataset.open = stream;
      open.textContent = "Ouvrir";
      actions.append(copy, open);
      wrapper.append(code, actions);
      detailsStreams.appendChild(wrapper);
    });
  } else {
    detailsStreams.textContent = item.status === "success" ? "Flux non présent sur cette ligne." : "Aucun";
  }

  if (detailsVideos) {
    detailsVideos.innerHTML = "";
    const videos = Array.isArray(item.videos) ? item.videos : [];
    if (videos.length) {
      videos.forEach((video) => {
        const wrapper = document.createElement("div");
        wrapper.className = "stream-pill";
        const code = document.createElement("code");
        code.textContent = video;
        const actions = document.createElement("div");
        actions.className = "pill-actions";
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "copy-button";
        copy.dataset.copy = video;
        copy.textContent = "Copier";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "open-button";
        open.dataset.open = video;
        open.textContent = "Ouvrir";
        actions.append(copy, open);
        wrapper.append(code, actions);
        detailsVideos.appendChild(wrapper);
      });
    } else {
      detailsVideos.textContent = item.status === "success" ? "Vidéos non présentes sur cette ligne." : "Aucune";
    }
  }

  if (detailsAssets) {
    detailsAssets.innerHTML = "";
    const extraAssets = [
      ...(Array.isArray(item.documents) ? item.documents : []),
      ...(Array.isArray(item.images) ? item.images : []),
      ...(Array.isArray(item.other_assets) ? item.other_assets : []),
    ];
    if (extraAssets.length) {
      extraAssets.forEach((asset) => {
        const wrapper = document.createElement("div");
        wrapper.className = "stream-pill";
        const code = document.createElement("code");
        code.textContent = asset;
        const actions = document.createElement("div");
        actions.className = "pill-actions";
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "copy-button";
        copy.dataset.copy = asset;
        copy.textContent = "Copier";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "open-button";
        open.dataset.open = asset;
        open.textContent = "Ouvrir";
        actions.append(copy, open);
        wrapper.append(code, actions);
        detailsAssets.appendChild(wrapper);
      });
    } else {
      detailsAssets.textContent = "Aucune";
    }
  }

  detailsTrace.innerHTML = "";
  const trace = parseTrace(item.source_trace);
  if (trace.length === 0) {
    const li = document.createElement("li");
    li.textContent = "Aucune trace disponible.";
    detailsTrace.appendChild(li);
  } else {
    trace.forEach((step) => {
      const li = document.createElement("li");
      const parts = [step.stage];
      if (step.kind) parts.push(`kind=${step.kind}`);
      if (step.url) parts.push(step.url);
      if (step.message) parts.push(step.message);
      if (typeof step.count !== "undefined") parts.push(`count=${step.count}`);
      if (typeof step.bytes !== "undefined") parts.push(`bytes=${step.bytes}`);
      if (typeof step.streams !== "undefined") parts.push(`streams=${step.streams}`);
      li.textContent = parts.join(" · ");
      detailsTrace.appendChild(li);
    });
  }

  detailsModal.classList.remove("hidden");
  detailsModal.setAttribute("aria-hidden", "false");
}

function parseTrace(rawTrace) {
  if (!rawTrace) return [];
  if (Array.isArray(rawTrace)) return rawTrace;
  if (typeof rawTrace === "string") {
    try {
      const parsed = JSON.parse(rawTrace);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

function closeDetails() {
  detailsModal.classList.add("hidden");
  detailsModal.setAttribute("aria-hidden", "true");
}

function setLoading(isLoading) {
  if (!submitButton) return;
  form.classList.toggle("is-loading", isLoading);
  submitButton.disabled = isLoading;
  submitButton.textContent = isLoading ? "Analyse en cours..." : "Analyser";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const urls = extractUrls(input.value);

  if (urls.length === 0) {
    setStatus("Ajoute une URL HTTP ou HTTPS valide.", true);
    return;
  }

  setStatus(`Analyse de ${urls.length} URL(s) en cours...`);
  setLoading(true);

  try {
    for (const url of urls) {
      const result = await analyzeOne(url);
      if (result.status === "success") {
        setStatus(`Flux trouvé pour ${result.page_url}`);
      } else if (result.status === "no_stream_found") {
        setStatus(`Aucun flux trouvé pour ${result.page_url}`);
      } else {
        setStatus(result.error_message || `Erreur pour ${url}`, true);
      }
    }
    historyState.page = 1;
    await refreshHistory();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setLoading(false);
  }
});

dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", async (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragover");

  const text = event.dataTransfer.getData("text/plain");
  if (text) {
    input.value = input.value ? `${input.value}\n${text}` : text;
    return;
  }

  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  if (file && file.name.toLowerCase().endsWith(".txt")) {
    const content = await file.text();
    input.value = input.value ? `${input.value}\n${content}` : content;
  }
});

dropzone.addEventListener("click", () => input.focus());

async function handleHistoryAction(event) {
  const target = event.target;
  const row = target.closest("tr");
  const item = row ? historyCache.get(String(row.dataset.id)) : null;
  const card = target.closest(".history-card, .latest-card");
  const cardItem = card ? historyCache.get(String(card.dataset.id)) : null;
  const activeItem = item || cardItem;

  if (target.classList.contains("details-row") && activeItem) {
    openDetails(activeItem);
    return;
  }

  if (target.classList.contains("delete-row")) {
    const id = row?.dataset?.id || card?.dataset?.id;
    if (!id) return;
    if (!confirm("Supprimer cette entrée ?")) return;

    const response = await fetch(`/api/history/${id}`, { method: "DELETE" });
    if (response.ok) {
      setStatus("Analyse supprimée.");
      await refreshHistory();
    } else {
      setStatus("Impossible de supprimer l’analyse.", true);
    }
    return;
  }

}

if (historyCards) {
  historyCards.addEventListener("click", async (event) => {
    await handleHistoryAction(event);
  });
}

if (historyTableBody) {
  historyTableBody.addEventListener("click", async (event) => {
    await handleHistoryAction(event);
  });
}

if (latestAnalysisContent) {
  latestAnalysisContent.addEventListener("click", async (event) => {
    await handleHistoryAction(event);
  });
}

document.addEventListener("click", async (event) => {
  const target = event.target;
  if (target.dataset.copy) {
    const text = target.dataset.copy;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Lien copié dans le presse-papiers.");
      showToast("Copié");
    } catch {
      setStatus("Copie impossible.", true);
    }
    return;
  }

});

detailsModal.addEventListener("click", (event) => {
  if (event.target.matches("[data-close-modal]")) {
    closeDetails();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !detailsModal.classList.contains("hidden")) {
    closeDetails();
  }
});

clearHistoryButton.addEventListener("click", async () => {
  if (!confirm("Vider tout l’historique ?")) return;
  const response = await fetch("/api/history", { method: "DELETE" });
  if (response.ok) {
    historyState.page = 1;
    setStatus("Historique vidé.");
    historyCache.clear();
    await refreshHistory();
  } else {
    setStatus("Impossible de vider l’historique.", true);
  }
});

historyApply.addEventListener("click", async () => {
  historyState.page = 1;
  historyState.search = historySearch.value.trim();
  historyState.status = historyStatus.value;
  historyState.media = historyMedia.value;
  historyState.perPage = Number(historyPerPage.value || 10);
  await refreshHistory();
});

historyReset.addEventListener("click", async () => {
  historySearch.value = "";
  historyStatus.value = "all";
  historyMedia.value = "all";
  historyPerPage.value = "10";
  historyState.page = 1;
  historyState.search = "";
  historyState.status = "all";
  historyState.media = "all";
  historyState.perPage = 10;
  await refreshHistory();
});

historyPrev.addEventListener("click", async () => {
  if (historyState.page > 1) {
    historyState.page -= 1;
    await refreshHistory();
  }
});

historyNext.addEventListener("click", async () => {
  historyState.page += 1;
  await refreshHistory();
});

historyModeButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    setHistoryView(button.dataset.historyView);
  });
});

setHistoryView(historyState.view);
refreshHistory();
