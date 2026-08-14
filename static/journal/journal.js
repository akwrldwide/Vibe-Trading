// ==========================================================================
// 9:30 NY ICT Trading Journal GUI Logic
// State Management • REST API Client • Canvas Equity Chart • Drag & Drop
// ==========================================================================

const API_BASE = "";

// App State
let state = {
  trades: [],
  stats: null,
  filters: {
    symbol: "ALL",
    action: "ALL",
    outcome: "ALL",
    setup_type: "ALL",
    sort_by: "trade_date",
    sort_dir: "DESC"
  },
  viewMode: "table" // 'table' | 'cards'
};

// DOM Elements
const elements = {
  // Top Stats
  statWinRate: document.getElementById("stat-win-rate"),
  statWinRateBar: document.getElementById("stat-win-rate-bar"),
  statTradesCount: document.getElementById("stat-trades-count"),
  statTotalR: document.getElementById("stat-total-r"),
  statWinLossSplit: document.getElementById("stat-win-loss-split"),
  statProfitFactor: document.getElementById("stat-profit-factor"),
  statAvgRR: document.getElementById("stat-avg-rr"),
  statTotalPnl: document.getElementById("stat-total-pnl"),
  statOpenCount: document.getElementById("stat-open-count"),
  pillBestPair: document.getElementById("pill-best-pair"),
  pillBestSetup: document.getElementById("pill-best-setup"),

  // Views & Containers
  tradesTableBody: document.getElementById("trades-table-body"),
  tradesCardGrid: document.getElementById("trades-card-grid"),
  tradesTableView: document.getElementById("trades-table-view"),
  tradesCardsView: document.getElementById("trades-cards-view"),
  tableEmptyState: document.getElementById("table-empty-state"),
  viewTableBtn: document.getElementById("view-table-btn"),
  viewCardsBtn: document.getElementById("view-cards-btn"),

  // Filters
  filterSymbol: document.getElementById("filter-symbol"),
  filterAction: document.getElementById("filter-action"),
  filterOutcome: document.getElementById("filter-outcome"),
  filterSetup: document.getElementById("filter-setup"),
  sortBy: document.getElementById("sort-by"),

  // Buttons
  btnNewTrade: document.getElementById("btn-new-trade"),
  btnUploadScreenshot: document.getElementById("btn-upload-screenshot"),
  btnExportCsv: document.getElementById("btn-export-csv"),

  // Modals
  tradeModal: document.getElementById("trade-modal"),
  tradeForm: document.getElementById("trade-form"),
  modalTitle: document.getElementById("modal-title"),
  modalCloseBtn: document.getElementById("modal-close-btn"),
  btnCancelModal: document.getElementById("btn-cancel-modal"),

  // Form Fields
  formTradeId: document.getElementById("form-trade-id"),
  formDate: document.getElementById("form-date"),
  formNyTime: document.getElementById("form-ny-time"),
  formSymbol: document.getElementById("form-symbol"),
  formAction: document.getElementById("form-action"),
  formSetupType: document.getElementById("form-setup-type"),
  formHtfBias: document.getElementById("form-htf-bias"),
  formEntry: document.getElementById("form-entry"),
  formSl: document.getElementById("form-sl"),
  formTp: document.getElementById("form-tp"),
  formRr: document.getElementById("form-rr"),
  formOutcome: document.getElementById("form-outcome"),
  formRealizedR: document.getElementById("form-realized-r"),
  formRealizedPnl: document.getElementById("form-realized-pnl"),
  formOrHigh: document.getElementById("form-or-high"),
  formOrLow: document.getElementById("form-or-low"),
  formNotes: document.getElementById("form-notes"),
  formScreenshotUrl: document.getElementById("form-screenshot-url"),
  formScreenshotPreviewContainer: document.getElementById("form-screenshot-preview-container"),
  formScreenshotImg: document.getElementById("form-screenshot-img"),
  btnRemoveScreenshot: document.getElementById("btn-remove-screenshot"),

  // Extract Modal
  extractModal: document.getElementById("extract-modal"),
  extractModalCloseBtn: document.getElementById("extract-modal-close-btn"),
  screenshotDropzone: document.getElementById("screenshot-dropzone"),
  screenshotFileInput: document.getElementById("screenshot-file-input"),
  extractLoading: document.getElementById("extract-loading"),
  extractResultContainer: document.getElementById("extract-result-container"),
  extractedImgPreview: document.getElementById("extracted-img-preview"),
  extDate: document.getElementById("ext-date"),
  extNyTime: document.getElementById("ext-ny-time"),
  extSymbol: document.getElementById("ext-symbol"),
  extAction: document.getElementById("ext-action"),
  extEntry: document.getElementById("ext-entry"),
  extSl: document.getElementById("ext-sl"),
  extTp: document.getElementById("ext-tp"),
  extHtfBias: document.getElementById("ext-htf-bias"),
  extOutcome: document.getElementById("ext-outcome"),
  extRr: document.getElementById("ext-rr"),
  extNotes: document.getElementById("ext-notes"),
  btnConfirmExtraction: document.getElementById("btn-confirm-extraction"),
  btnReUpload: document.getElementById("btn-re-upload"),

  // Lightbox
  lightboxModal: document.getElementById("lightbox-modal"),
  lightboxImg: document.getElementById("lightbox-img"),
  lightboxCloseBtn: document.getElementById("lightbox-close-btn")
};

// ==========================================================================
// Initialization & Event Listeners
// ==========================================================================
document.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  loadData();
});

function setupEventListeners() {
  // View Toggle
  elements.viewTableBtn.addEventListener("click", () => setViewMode("table"));
  elements.viewCardsBtn.addEventListener("click", () => setViewMode("cards"));

  // Filters
  elements.filterSymbol.addEventListener("change", (e) => {
    state.filters.symbol = e.target.value;
    loadTrades();
  });
  elements.filterAction.addEventListener("change", (e) => {
    state.filters.action = e.target.value;
    loadTrades();
  });
  elements.filterOutcome.addEventListener("change", (e) => {
    state.filters.outcome = e.target.value;
    loadTrades();
  });
  elements.filterSetup.addEventListener("change", (e) => {
    state.filters.setup_type = e.target.value;
    loadTrades();
  });
  elements.sortBy.addEventListener("change", (e) => {
    const [col, dir] = e.target.value.split(":");
    state.filters.sort_by = col;
    state.filters.sort_dir = dir;
    loadTrades();
  });

  // Modal Buttons
  elements.btnNewTrade.addEventListener("click", () => openTradeModal());
  elements.modalCloseBtn.addEventListener("click", closeTradeModal);
  elements.btnCancelModal.addEventListener("click", closeTradeModal);
  elements.tradeForm.addEventListener("submit", handleTradeFormSubmit);

  // Auto R:R calculation on entry / SL / TP changes in form
  elements.formEntry.addEventListener("input", autoCalcRR);
  elements.formSl.addEventListener("input", autoCalcRR);
  elements.formTp.addEventListener("input", autoCalcRR);
  elements.formOutcome.addEventListener("change", autoSetOutcomeR);

  // Screenshot Extractor
  elements.btnUploadScreenshot.addEventListener("click", openExtractModal);
  elements.extractModalCloseBtn.addEventListener("click", closeExtractModal);
  elements.btnReUpload.addEventListener("click", resetExtractDropzone);
  elements.btnConfirmExtraction.addEventListener("click", handleConfirmExtraction);

  // Dropzone drag & drop
  const dropzone = elements.screenshotDropzone;
  dropzone.addEventListener("click", () => elements.screenshotFileInput.click());
  elements.screenshotFileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileUpload(e.target.files[0]);
    }
  });
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  });

  // Paste from clipboard (Ctrl+V) anywhere on page
  window.addEventListener("paste", (e) => {
    if (e.clipboardData && e.clipboardData.items) {
      for (let item of e.clipboardData.items) {
        if (item.type.indexOf("image") !== -1) {
          const file = item.getAsFile();
          openExtractModal();
          handleFileUpload(file);
          break;
        }
      }
    }
  });

  // Lightbox
  elements.lightboxCloseBtn.addEventListener("click", closeLightbox);
  elements.lightboxModal.addEventListener("click", (e) => {
    if (e.target === elements.lightboxModal) closeLightbox();
  });

  // Export CSV
  elements.btnExportCsv.addEventListener("click", () => {
    window.location.href = "/api/export/csv";
  });

  // Remove Screenshot in modal
  elements.btnRemoveScreenshot.addEventListener("click", () => {
    elements.formScreenshotUrl.value = "";
    elements.formScreenshotPreviewContainer.classList.add("hidden");
  });
}

function setViewMode(mode) {
  state.viewMode = mode;
  if (mode === "table") {
    elements.viewTableBtn.classList.add("active");
    elements.viewCardsBtn.classList.remove("active");
    elements.tradesTableView.classList.remove("hidden");
    elements.tradesCardsView.classList.add("hidden");
  } else {
    elements.viewCardsBtn.classList.add("active");
    elements.viewTableBtn.classList.remove("active");
    elements.tradesCardsView.classList.remove("hidden");
    elements.tradesTableView.classList.add("hidden");
  }
}

// ==========================================================================
// Data Fetching & Rendering
// ==========================================================================
async function loadData() {
  await Promise.all([loadStats(), loadTrades()]);
}

async function loadStats() {
  try {
    const res = await fetch(`${API_BASE}/api/stats`);
    const data = await res.json();
    state.stats = data;
    renderStats(data);
    renderEquityChart(data.equity_curve || []);
  } catch (err) {
    console.error("Failed to load stats:", err);
  }
}

async function loadTrades() {
  try {
    const params = new URLSearchParams({
      symbol: state.filters.symbol,
      action: state.filters.action,
      outcome: state.filters.outcome,
      setup_type: state.filters.setup_type,
      sort_by: state.filters.sort_by,
      sort_dir: state.filters.sort_dir
    });

    const res = await fetch(`${API_BASE}/api/trades?${params.toString()}`);
    const trades = await res.json();
    state.trades = trades;
    renderTrades(trades);
  } catch (err) {
    console.error("Failed to load trades:", err);
  }
}

function renderStats(stats) {
  if (!stats) return;

  elements.statWinRate.textContent = `${stats.win_rate}%`;
  elements.statWinRateBar.style.width = `${Math.min(stats.win_rate, 100)}%`;
  elements.statTradesCount.textContent = `${stats.completed_trades} / ${stats.total_trades} completed`;

  const totalR = stats.total_realized_r;
  elements.statTotalR.textContent = `${totalR >= 0 ? "+" : ""}${totalR.toFixed(2)}R`;
  elements.statTotalR.className = `metric-value ${totalR >= 0 ? "text-purple" : "text-red"}`;
  elements.statWinLossSplit.textContent = `${stats.wins}W / ${stats.losses}L / ${stats.break_evens}BE`;

  elements.statProfitFactor.textContent = stats.profit_factor.toFixed(2);
  elements.statAvgRR.textContent = `Avg R:R 1:${stats.avg_rr.toFixed(1)}`;

  const totalPnl = stats.total_realized_pnl;
  elements.statTotalPnl.textContent = `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`;
  elements.statTotalPnl.className = `metric-value ${totalPnl >= 0 ? "text-emerald" : "text-red"}`;
  elements.statOpenCount.textContent = `${stats.open_trades} Open / Pending`;

  // Best Asset & Setup Pill
  if (stats.pair_breakdown) {
    const bestPair = Object.entries(stats.pair_breakdown).sort((a, b) => b[1].net_r - a[1].net_r)[0];
    if (bestPair) {
      elements.pillBestPair.textContent = `Top Asset: ${bestPair[0]} (+${bestPair[1].net_r}R)`;
    }
  }
  if (stats.setup_breakdown) {
    const bestSetup = Object.entries(stats.setup_breakdown).sort((a, b) => b[1].net_r - a[1].net_r)[0];
    if (bestSetup) {
      elements.pillBestSetup.textContent = `Top Model: ${bestSetup[0]}`;
    }
  }
}

function renderTrades(trades) {
  const tbody = elements.tradesTableBody;
  const grid = elements.tradesCardGrid;
  tbody.innerHTML = "";
  grid.innerHTML = "";

  if (!trades || trades.length === 0) {
    elements.tableEmptyState.classList.remove("hidden");
    return;
  }
  elements.tableEmptyState.classList.add("hidden");

  trades.forEach((t) => {
    // 1. Table Row
    const tr = document.createElement("tr");
    const actionBadgeClass = t.action === "BUY" ? "badge-buy" : "badge-sell";
    const outcomeBadgeClass = getOutcomeBadgeClass(t.outcome);
    const htfBadgeClass = t.htf_bias === "BULLISH" ? "badge-green" : t.htf_bias === "BEARISH" ? "badge-red" : "badge-gray";
    const realizedRFormatted = t.realized_r !== null ? `${t.realized_r >= 0 ? "+" : ""}${Number(t.realized_r).toFixed(2)}R` : "--";

    tr.innerHTML = `
      <td>
        <strong>${t.trade_date}</strong>
        <div style="font-size: 11px; color: var(--text-muted);">${t.ny_time || "09:45 AM"}</div>
      </td>
      <td><span class="symbol-badge">${t.symbol}</span></td>
      <td><span class="badge ${actionBadgeClass}">${t.action}</span></td>
      <td style="font-size: 12px; color: #cbd5e1;">${t.setup_type}</td>
      <td><span class="badge ${htfBadgeClass}">${t.htf_bias}</span></td>
      <td class="font-mono">${Number(t.entry_price).toFixed(2)}</td>
      <td class="font-mono text-red">${Number(t.stop_loss).toFixed(2)}</td>
      <td class="font-mono text-emerald">${Number(t.take_profit).toFixed(2)}</td>
      <td class="font-mono">1:${Number(t.rr_ratio || 2.0).toFixed(1)}</td>
      <td><span class="badge ${outcomeBadgeClass}">${t.outcome}</span></td>
      <td class="font-mono font-bold ${Number(t.realized_r) >= 0 ? "text-emerald" : "text-red"}">${realizedRFormatted}</td>
      <td>
        ${t.screenshot_url ? `<img src="${t.screenshot_url}" class="thumb-preview" alt="Chart" onclick="openLightbox('${t.screenshot_url}')" title="Click to view chart screenshot">` : `<span style="color: var(--text-muted); font-size: 11px;">--</span>`}
      </td>
      <td class="text-right">
        <div class="action-btn-group">
          <button class="btn-icon-sm" onclick="editTrade(${t.id})" title="Edit trade">✏️</button>
          <button class="btn-icon-sm delete" onclick="deleteTrade(${t.id})" title="Delete trade">🗑️</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);

    // 2. Card Grid
    const card = document.createElement("div");
    card.className = "trade-card";
    card.innerHTML = `
      <div class="card-header">
        <div class="card-title-group">
          <span class="symbol-badge">${t.symbol}</span>
          <span class="badge ${actionBadgeClass}">${t.action}</span>
          <span class="badge ${outcomeBadgeClass}">${t.outcome}</span>
        </div>
        <span class="font-mono font-bold ${Number(t.realized_r) >= 0 ? "text-emerald" : "text-red"}">${realizedRFormatted}</span>
      </div>

      ${t.screenshot_url ? `
        <div class="card-img-wrapper" onclick="openLightbox('${t.screenshot_url}')">
          <img src="${t.screenshot_url}" alt="${t.symbol} Chart">
        </div>
      ` : ""}

      <div class="card-body">
        <div class="card-levels-grid">
          <div class="card-level-item">
            <span class="card-level-label">Entry</span>
            <span>${Number(t.entry_price).toFixed(2)}</span>
          </div>
          <div class="card-level-item">
            <span class="card-level-label">Stop Loss</span>
            <span class="text-red">${Number(t.stop_loss).toFixed(2)}</span>
          </div>
          <div class="card-level-item">
            <span class="card-level-label">Take Profit</span>
            <span class="text-emerald">${Number(t.take_profit).toFixed(2)}</span>
          </div>
        </div>

        <div style="display: flex; gap: 6px; margin-bottom: 10px;">
          <span class="badge badge-purple" style="font-size: 10px;">${t.setup_type}</span>
          <span class="badge ${htfBadgeClass}" style="font-size: 10px;">${t.htf_bias}</span>
        </div>

        ${t.notes ? `<div class="card-notes">${escapeHtml(t.notes)}</div>` : ""}
      </div>

      <div class="card-footer">
        <span>📅 ${t.trade_date} ${t.ny_time || ""}</span>
        <div class="action-btn-group">
          <button class="btn-icon-sm" onclick="editTrade(${t.id})">✏️</button>
          <button class="btn-icon-sm delete" onclick="deleteTrade(${t.id})">🗑️</button>
        </div>
      </div>
    `;
    grid.appendChild(card);
  });
}

function getOutcomeBadgeClass(outcome) {
  switch (outcome) {
    case "WIN": return "badge-green";
    case "LOSS": return "badge-red";
    case "BREAK-EVEN": return "badge-amber";
    default: return "badge-purple";
  }
}

function escapeHtml(text) {
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
  return text.replace(/[&<>"']/g, (m) => map[m]);
}

// ==========================================================================
// Canvas Equity Curve Drawing (Ultra Crisp, Responsive & Lightweight)
// ==========================================================================
function renderEquityChart(curveData) {
  const canvas = document.getElementById("equity-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  // Resize canvas for sharp retina display
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const w = rect.width;
  const h = rect.height;
  ctx.clearRect(0, 0, w, h);

  if (!curveData || curveData.length === 0) {
    ctx.fillStyle = "#64748b";
    ctx.font = "12px Inter";
    ctx.textAlign = "center";
    ctx.fillText("No completed trade curve data yet", w / 2, h / 2);
    return;
  }

  const padding = { top: 20, right: 30, bottom: 25, left: 45 };
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;

  // Insert 0R starting baseline point
  const points = [{ trade_num: 0, cum_r: 0.0, date: "Start" }, ...curveData];
  const rValues = points.map(p => p.cum_r);
  const minR = Math.min(0, ...rValues) - 0.5;
  const maxR = Math.max(2, ...rValues) + 0.5;

  const getX = (i) => padding.left + (i / (points.length - 1 || 1)) * chartW;
  const getY = (r) => padding.top + chartH - ((r - minR) / (maxR - minR || 1)) * chartH;

  // Draw Grid & Zero Line
  ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yVal = minR + (i / 4) * (maxR - minR);
    const yPos = getY(yVal);
    ctx.beginPath();
    ctx.moveTo(padding.left, yPos);
    ctx.lineTo(w - padding.right, yPos);
    ctx.stroke();

    ctx.fillStyle = "#64748b";
    ctx.font = "10px JetBrains Mono";
    ctx.textAlign = "right";
    ctx.fillText(`${yVal >= 0 ? "+" : ""}${yVal.toFixed(1)}R`, padding.left - 8, yPos + 3);
  }

  // Draw Gradient Fill under curve
  const zeroY = getY(0);
  const grad = ctx.createLinearGradient(0, padding.top, 0, padding.top + chartH);
  grad.addColorStop(0, "rgba(139, 92, 246, 0.35)");
  grad.addColorStop(1, "rgba(139, 92, 246, 0.0)");

  ctx.beginPath();
  ctx.moveTo(getX(0), zeroY);
  points.forEach((p, i) => {
    ctx.lineTo(getX(i), getY(p.cum_r));
  });
  ctx.lineTo(getX(points.length - 1), zeroY);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Draw Smooth Line
  ctx.beginPath();
  ctx.strokeStyle = "#8b5cf6";
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  points.forEach((p, i) => {
    if (i === 0) ctx.moveTo(getX(i), getY(p.cum_r));
    else ctx.lineTo(getX(i), getY(p.cum_r));
  });
  ctx.stroke();

  // Draw Glowing Points
  points.forEach((p, i) => {
    if (i === 0) return;
    const x = getX(i);
    const y = getY(p.cum_r);
    const isWin = p.outcome === "WIN";

    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = isWin ? "#10b981" : "#f43f5e";
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });
}

// Window resize handler for canvas
window.addEventListener("resize", () => {
  if (state.stats && state.stats.equity_curve) {
    renderEquityChart(state.stats.equity_curve);
  }
});

// ==========================================================================
// Modal Handlers & Automatic RR Calculation
// ==========================================================================
function autoCalcRR() {
  const entry = parseFloat(elements.formEntry.value);
  const sl = parseFloat(elements.formSl.value);
  const tp = parseFloat(elements.formTp.value);
  const action = elements.formAction.value;

  if (!isNaN(entry) && !isNaN(sl) && !isNaN(tp)) {
    const risk = Math.abs(entry - sl);
    const reward = Math.abs(tp - entry);
    if (risk > 0) {
      elements.formRr.value = (reward / risk).toFixed(1);
    }
  }
}

function autoSetOutcomeR() {
  const outcome = elements.formOutcome.value;
  const rr = parseFloat(elements.formRr.value) || 2.0;

  if (outcome === "WIN") {
    elements.formRealizedR.value = rr.toFixed(2);
  } else if (outcome === "LOSS") {
    elements.formRealizedR.value = "-1.00";
  } else if (outcome === "BREAK-EVEN") {
    elements.formRealizedR.value = "0.00";
  }
}

function openTradeModal(tradeData = null) {
  elements.tradeForm.reset();
  elements.formTradeId.value = "";
  elements.formScreenshotUrl.value = "";
  elements.formScreenshotPreviewContainer.classList.add("hidden");

  if (tradeData) {
    elements.modalTitle.textContent = `Edit Trade #${tradeData.id} (${tradeData.symbol})`;
    elements.formTradeId.value = tradeData.id;
    elements.formDate.value = tradeData.trade_date;
    elements.formNyTime.value = tradeData.ny_time || "09:45 AM";
    elements.formSymbol.value = tradeData.symbol;
    elements.formAction.value = tradeData.action;
    elements.formSetupType.value = tradeData.setup_type;
    elements.formHtfBias.value = tradeData.htf_bias;
    elements.formEntry.value = tradeData.entry_price;
    elements.formSl.value = tradeData.stop_loss;
    elements.formTp.value = tradeData.take_profit;
    elements.formRr.value = tradeData.rr_ratio || 2.0;
    elements.formOutcome.value = tradeData.outcome;
    elements.formRealizedR.value = tradeData.realized_r !== null ? tradeData.realized_r : "";
    elements.formRealizedPnl.value = tradeData.realized_pnl !== null ? tradeData.realized_pnl : "";
    elements.formOrHigh.value = tradeData.or_high || "";
    elements.formOrLow.value = tradeData.or_low || "";
    elements.formNotes.value = tradeData.notes || "";

    if (tradeData.screenshot_url) {
      elements.formScreenshotUrl.value = tradeData.screenshot_url;
      elements.formScreenshotImg.src = tradeData.screenshot_url;
      elements.formScreenshotPreviewContainer.classList.remove("hidden");
    }
  } else {
    elements.modalTitle.textContent = "Log 9:30 NY ICT Trade";
    elements.formDate.value = new Date().toISOString().split("T")[0];
    elements.formNyTime.value = "09:55 AM";
    elements.formSymbol.value = "BTCUSDT";
    elements.formAction.value = "BUY";
    elements.formSetupType.value = "OR Breakout (Candle 2 FVG)";
    elements.formHtfBias.value = "BULLISH";
    elements.formRr.value = "2.0";
    elements.formOutcome.value = "OPEN";
  }

  elements.tradeModal.classList.remove("hidden");
}

function closeTradeModal() {
  elements.tradeModal.classList.add("hidden");
}

async function handleTradeFormSubmit(e) {
  e.preventDefault();

  const tradeId = elements.formTradeId.value;
  const payload = {
    trade_date: elements.formDate.value,
    ny_time: elements.formNyTime.value,
    symbol: elements.formSymbol.value.toUpperCase().trim(),
    action: elements.formAction.value,
    setup_type: elements.formSetupType.value,
    htf_bias: elements.formHtfBias.value,
    entry_price: parseFloat(elements.formEntry.value),
    stop_loss: parseFloat(elements.formSl.value),
    take_profit: parseFloat(elements.formTp.value),
    rr_ratio: parseFloat(elements.formRr.value) || 2.0,
    outcome: elements.formOutcome.value,
    realized_r: elements.formRealizedR.value ? parseFloat(elements.formRealizedR.value) : 0.0,
    realized_pnl: elements.formRealizedPnl.value ? parseFloat(elements.formRealizedPnl.value) : 0.0,
    or_high: elements.formOrHigh.value ? parseFloat(elements.formOrHigh.value) : null,
    or_low: elements.formOrLow.value ? parseFloat(elements.formOrLow.value) : null,
    notes: elements.formNotes.value,
    screenshot_url: elements.formScreenshotUrl.value || ""
  };

  try {
    let res;
    if (tradeId) {
      res = await fetch(`${API_BASE}/api/trades/${tradeId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } else {
      res = await fetch(`${API_BASE}/api/trades`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    }

    if (res.ok) {
      closeTradeModal();
      await loadData();
    } else {
      const err = await res.json();
      alert(`Error saving trade: ${err.detail || "Unknown error"}`);
    }
  } catch (err) {
    console.error("Trade submit error:", err);
    alert("Failed to connect to backend server.");
  }
}

async function editTrade(id) {
  try {
    const res = await fetch(`${API_BASE}/api/trades/${id}`);
    if (res.ok) {
      const trade = await res.json();
      openTradeModal(trade);
    }
  } catch (err) {
    console.error("Failed to load trade for edit:", err);
  }
}

async function deleteTrade(id) {
  if (!confirm(`Are you sure you want to delete trade #${id}?`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/trades/${id}`, { method: "DELETE" });
    if (res.ok) {
      await loadData();
    }
  } catch (err) {
    console.error("Failed to delete trade:", err);
  }
}

// ==========================================================================
// Smart Screenshot Extractor Logic
// ==========================================================================
let currentExtractedData = null;

function openExtractModal() {
  resetExtractDropzone();
  elements.extractModal.classList.remove("hidden");
}

function closeExtractModal() {
  elements.extractModal.classList.add("hidden");
}

function resetExtractDropzone() {
  elements.screenshotFileInput.value = "";
  elements.extractLoading.classList.add("hidden");
  elements.extractResultContainer.classList.add("hidden");
  elements.screenshotDropzone.classList.remove("hidden");
  currentExtractedData = null;
}

async function handleFileUpload(file) {
  elements.screenshotDropzone.classList.add("hidden");
  elements.extractLoading.classList.remove("hidden");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`${API_BASE}/api/extract-screenshot`, {
      method: "POST",
      body: formData
    });

    if (!res.ok) throw new Error("Extraction failed");

    const json = await res.json();
    currentExtractedData = json.data;

    populateExtractedFields(json.data);
    elements.extractLoading.classList.add("hidden");
    elements.extractResultContainer.classList.remove("hidden");
  } catch (err) {
    console.error("Screenshot upload error:", err);
    alert("Could not process image. Please try another screenshot.");
    resetExtractDropzone();
  }
}

function populateExtractedFields(data) {
  elements.extractedImgPreview.src = data.screenshot_url;
  elements.extDate.value = data.trade_date || new Date().toISOString().split("T")[0];
  elements.extNyTime.value = data.ny_time || "09:55 AM";
  elements.extSymbol.value = data.symbol || "BTCUSDT";
  elements.extAction.value = data.action || "BUY";
  elements.extEntry.value = data.entry_price !== null && data.entry_price !== undefined ? data.entry_price : "";
  elements.extSl.value = data.stop_loss !== null && data.stop_loss !== undefined ? data.stop_loss : "";
  elements.extTp.value = data.take_profit !== null && data.take_profit !== undefined ? data.take_profit : "";
  elements.extHtfBias.value = data.htf_bias || "BEARISH";
  elements.extOutcome.value = data.outcome || "WIN";
  elements.extRr.value = data.rr_ratio || 2.0;
  elements.extNotes.value = data.notes || "Captured from TradingView 9:30 NY ICT setup screenshot.";
}

async function handleConfirmExtraction() {
  if (!currentExtractedData) return;

  const entry = parseFloat(elements.extEntry.value) || 0.0;
  const sl = parseFloat(elements.extSl.value) || 0.0;
  const tp = parseFloat(elements.extTp.value) || 0.0;
  const outcome = elements.extOutcome.value;
  const rr = parseFloat(elements.extRr.value) || 2.0;

  let realizedR = 0.0;
  if (outcome === "WIN") realizedR = rr;
  else if (outcome === "LOSS") realizedR = -1.0;

  const payload = {
    trade_date: elements.extDate.value || currentExtractedData.trade_date || new Date().toISOString().split("T")[0],
    ny_time: elements.extNyTime.value || currentExtractedData.ny_time || "09:55 AM",
    symbol: elements.extSymbol.value.toUpperCase().trim(),
    action: elements.extAction.value,
    setup_type: currentExtractedData.setup_type || "OR Breakout (Candle 2 FVG)",
    htf_bias: elements.extHtfBias.value,
    entry_price: entry,
    stop_loss: sl,
    take_profit: tp,
    rr_ratio: rr,
    outcome: outcome,
    realized_r: realizedR,
    realized_pnl: 0.0,
    or_high: null,
    or_low: null,
    notes: elements.extNotes.value,
    screenshot_url: currentExtractedData.screenshot_url
  };

  try {
    const res = await fetch(`${API_BASE}/api/trades`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      closeExtractModal();
      await loadData();
    } else {
      alert("Failed to save extracted trade.");
    }
  } catch (err) {
    console.error("Save error:", err);
  }
}

// ==========================================================================
// Lightbox Modal
// ==========================================================================
function openLightbox(url) {
  elements.lightboxImg.src = url;
  elements.lightboxModal.classList.remove("hidden");
}

function closeLightbox() {
  elements.lightboxModal.classList.add("hidden");
}

// Global exposes for inline onclick handlers
window.openLightbox = openLightbox;
window.editTrade = editTrade;
window.deleteTrade = deleteTrade;
