/**
 * AstrBot 漂流瓶审核台（插件可视化视图）。
 *
 * 与 Dashboard 的通信全部通过 bridge 完成：
 *   - 视图 iframe 无法直接读取 Dashboard 的 Cookie / localStorage；
 *   - 后端接口通过 context.register_web_api() 注册，路径前缀由 Dashboard 自动补全。
 */

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage || null;

const $ = (id) => document.getElementById(id);

const ui = {
  title: $("title"),
  subtitle: $("subtitle"),
  refresh: $("refresh"),
  footerState: $("footer-state"),

  statPendingBottles: $("stat-pending-bottles"),
  statPendingComments: $("stat-pending-comments"),
  statApprovedBottles: $("stat-approved-bottles"),
  statApprovedComments: $("stat-approved-comments"),

  tabBottlesCount: $("tab-count-bottles"),
  tabCommentsCount: $("tab-count-comments"),

  bottleMeta: $("bottle-meta"),
  bottleId: $("bottle-id"),
  bottleUser: $("bottle-user"),
  bottleGroup: $("bottle-group"),
  bottleTime: $("bottle-time"),
  bottleCard: $("bottle-card"),
  bottleContent: $("bottle-content"),
  bottleImages: $("bottle-images"),
  bottleEmpty: $("bottle-empty"),
  bottleActions: $("bottle-actions"),
  bottleApprove: $("bottle-approve"),
  bottleRefuse: $("bottle-refuse"),
  bottleNext: $("bottle-next"),

  commentMeta: $("comment-meta"),
  commentId: $("comment-id"),
  commentBottle: $("comment-bottle"),
  commentUser: $("comment-user"),
  commentCard: $("comment-card"),
  commentContent: $("comment-content"),
  commentEmpty: $("comment-empty"),
  commentActions: $("comment-actions"),
  commentApprove: $("comment-approve"),
  commentRefuse: $("comment-refuse"),
  commentNext: $("comment-next"),

  lightbox: $("lightbox"),
  lightboxImg: $("lightbox-img"),
  toast: $("toast"),
};

const state = {
  tab: "bottles",
  bottle: null,
  comment: null,
  busy: false,
  toastTimer: 0,
};

/* ------------------------------------------------------------------ */
/* bridge 封装                                                         */
/* ------------------------------------------------------------------ */
function unwrap(response) {
  // 后端若返回 { "status": "ok", "data": ... }，bridge 通常已经解包；
  // 这里再兼容一层，避免不同版本行为差异。
  if (
    response &&
    typeof response === "object" &&
    response.status === "ok" &&
    Object.prototype.hasOwnProperty.call(response, "data")
  ) {
    return response.data;
  }
  return response;
}

async function apiGet(endpoint, params) {
  if (!bridge) throw new Error("请从 AstrBot WebUI 的插件详情页打开本视图");
  return unwrap(await bridge.apiGet(endpoint, params || {}));
}

async function apiPost(endpoint, body) {
  if (!bridge) throw new Error("请从 AstrBot WebUI 的插件详情页打开本视图");
  return unwrap(await bridge.apiPost(endpoint, body || {}));
}

/* ------------------------------------------------------------------ */
/* 通用 UI                                                             */
/* ------------------------------------------------------------------ */
function setFooterState(text) {
  if (ui.footerState) ui.footerState.textContent = text;
}

function toast(message, type = "info", duration = 2600) {
  if (!ui.toast) return;
  ui.toast.textContent = message;
  ui.toast.className = `toast ${type}`;
  ui.toast.hidden = false;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    ui.toast.hidden = true;
  }, duration);
}

function errorMessage(error) {
  if (!error) return "未知错误";
  if (typeof error === "string") return error;
  if (error.message) return error.message;
  return String(error);
}

function setBusy(busy) {
  state.busy = busy;
  const buttons = [
    ui.bottleApprove,
    ui.bottleRefuse,
    ui.bottleNext,
    ui.commentApprove,
    ui.commentRefuse,
    ui.commentNext,
    ui.refresh,
  ];
  for (const button of buttons) {
    if (button) button.disabled = busy;
  }
}

function applyTheme(context) {
  const root = document.documentElement;
  const isDark =
    context && typeof context.isDark === "boolean"
      ? context.isDark
      : window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: dark)").matches;
  root.setAttribute("data-theme", isDark ? "dark" : "light");
}

function openLightbox(src) {
  if (!ui.lightbox || !ui.lightboxImg) return;
  ui.lightboxImg.src = src;
  ui.lightbox.hidden = false;
}

/* ------------------------------------------------------------------ */
/* 数据渲染                                                            */
/* ------------------------------------------------------------------ */
function renderStats(stats) {
  const data = stats || {};
  const pendingBottles = Number(data.pending_bottles || 0);
  const pendingComments = Number(data.pending_comments || 0);

  if (ui.statPendingBottles) ui.statPendingBottles.textContent = pendingBottles;
  if (ui.statPendingComments) ui.statPendingComments.textContent = pendingComments;
  if (ui.statApprovedBottles) {
    ui.statApprovedBottles.textContent = Number(data.approved_bottles || 0);
  }
  if (ui.statApprovedComments) {
    ui.statApprovedComments.textContent = Number(data.approved_comments || 0);
  }
  if (ui.tabBottlesCount) ui.tabBottlesCount.textContent = pendingBottles;
  if (ui.tabCommentsCount) ui.tabCommentsCount.textContent = pendingComments;
}

function renderBottle(payload) {
  const bottle = payload && payload.bottle ? payload.bottle : null;
  state.bottle = bottle;

  const hasBottle = Boolean(bottle);
  ui.bottleMeta.hidden = !hasBottle;
  ui.bottleCard.hidden = !hasBottle;
  ui.bottleActions.hidden = !hasBottle;
  ui.bottleEmpty.hidden = hasBottle;

  ui.bottleImages.replaceChildren();
  if (!hasBottle) return;

  ui.bottleId.textContent = `#${bottle.id}`;
  ui.bottleUser.textContent = `发送者：${bottle.username || bottle.userid || "未知"}`;
  ui.bottleGroup.textContent = `群聊：${
    bottle.groupname || bottle.groupid || "私聊"
  }`;
  ui.bottleTime.textContent = bottle.timeinfo || "未知时间";
  ui.bottleContent.textContent =
    bottle.content && bottle.content.trim()
      ? bottle.content
      : "（只投了图片，没有写文字）";

  const images = Array.isArray(payload.images) ? payload.images : [];
  for (const base64 of images) {
    const img = document.createElement("img");
    img.alt = `漂流瓶 ${bottle.id} 的图片`;
    img.loading = "lazy";
    img.src = `data:image/webp;base64,${base64}`;
    img.addEventListener("click", () => openLightbox(img.src));
    ui.bottleImages.appendChild(img);
  }
}

function renderComment(payload) {
  const comment = payload && payload.comment ? payload.comment : null;
  state.comment = comment;

  const hasComment = Boolean(comment);
  ui.commentMeta.hidden = !hasComment;
  ui.commentCard.hidden = !hasComment;
  ui.commentActions.hidden = !hasComment;
  ui.commentEmpty.hidden = hasComment;

  if (!hasComment) return;

  ui.commentId.textContent = `评论 #${comment.comment_id}`;
  ui.commentBottle.textContent = `漂流瓶 #${comment.bottle_id}`;
  ui.commentUser.textContent = `来自：${comment.username || comment.uid || "未知"}`;
  ui.commentContent.textContent = comment.content || "（空评论）";
}

/* ------------------------------------------------------------------ */
/* 接口调用                                                            */
/* ------------------------------------------------------------------ */
async function refreshStats() {
  try {
    const data = await apiGet("stats");
    renderStats(data && data.stats ? data.stats : data);
  } catch (error) {
    console.warn("获取统计失败", error);
  }
}

async function loadBottle() {
  try {
    const data = await apiGet("bottles/random");
    renderBottle(data);
    if (data && typeof data.pending === "number") {
      ui.statPendingBottles.textContent = data.pending;
      ui.tabBottlesCount.textContent = data.pending;
    }
  } catch (error) {
    toast(`加载漂流瓶失败：${errorMessage(error)}`, "error");
    setFooterState("加载失败");
  }
}

async function loadComment() {
  try {
    const data = await apiGet("comments/random");
    renderComment(data);
    if (data && typeof data.pending === "number") {
      ui.statPendingComments.textContent = data.pending;
      ui.tabCommentsCount.textContent = data.pending;
    }
  } catch (error) {
    toast(`加载评论失败：${errorMessage(error)}`, "error");
    setFooterState("加载失败");
  }
}

async function reviewBottle(action) {
  const bottle = state.bottle;
  if (!bottle || state.busy) return;
  setBusy(true);
  setFooterState(action === "approve" ? "正在通过..." : "正在拒绝...");
  try {
    const data = await apiPost(`bottles/${action}/${bottle.id}`);
    toast(
      action === "approve" ? `漂流瓶 #${bottle.id} 已通过` : `漂流瓶 #${bottle.id} 已拒绝`,
      "success",
    );
    if (data && typeof data.pending === "number") {
      ui.statPendingBottles.textContent = data.pending;
      ui.tabBottlesCount.textContent = data.pending;
    }
    await loadBottle();
    setFooterState("就绪");
  } catch (error) {
    toast(`操作失败：${errorMessage(error)}`, "error");
    setFooterState("操作失败");
  } finally {
    setBusy(false);
  }
}

async function reviewComment(action) {
  const comment = state.comment;
  if (!comment || state.busy) return;
  setBusy(true);
  setFooterState(action === "approve" ? "正在通过..." : "正在拒绝...");
  try {
    const data = await apiPost(`comments/${action}/${comment.comment_id}`);
    toast(
      action === "approve"
        ? `评论 #${comment.comment_id} 已通过`
        : `评论 #${comment.comment_id} 已拒绝`,
      "success",
    );
    if (data && typeof data.pending === "number") {
      ui.statPendingComments.textContent = data.pending;
      ui.tabCommentsCount.textContent = data.pending;
    }
    await loadComment();
    setFooterState("就绪");
  } catch (error) {
    toast(`操作失败：${errorMessage(error)}`, "error");
    setFooterState("操作失败");
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ */
/* 交互                                                                */
/* ------------------------------------------------------------------ */
function switchTab(tab) {
  if (tab !== "bottles" && tab !== "comments") return;
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${tab}`);
  });
  if (tab === "bottles" && !state.bottle) loadBottle();
  if (tab === "comments" && !state.comment) loadComment();
}

function bindEvents() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });

  ui.bottleApprove.addEventListener("click", () => reviewBottle("approve"));
  ui.bottleRefuse.addEventListener("click", () => reviewBottle("refuse"));
  ui.bottleNext.addEventListener("click", () => loadBottle());

  ui.commentApprove.addEventListener("click", () => reviewComment("approve"));
  ui.commentRefuse.addEventListener("click", () => reviewComment("refuse"));
  ui.commentNext.addEventListener("click", () => loadComment());

  ui.refresh.addEventListener("click", async () => {
    if (state.busy) return;
    setBusy(true);
    setFooterState("刷新中...");
    await Promise.all([refreshStats(), loadBottle(), loadComment()]);
    setBusy(false);
    setFooterState("就绪");
    toast("已刷新", "success", 1400);
  });

  ui.lightbox.addEventListener("click", () => {
    ui.lightbox.hidden = true;
    ui.lightboxImg.src = "";
  });

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const tag = (event.target && event.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (state.busy) return;

    const key = event.key.toLowerCase();
    if (key === "n" || key === "arrowright") {
      event.preventDefault();
      if (state.tab === "bottles") loadBottle();
      else loadComment();
      return;
    }
    if (key === "a") {
      event.preventDefault();
      if (state.tab === "bottles") reviewBottle("approve");
      else reviewComment("approve");
      return;
    }
    if (key === "r") {
      event.preventDefault();
      if (state.tab === "bottles") reviewBottle("refuse");
      else reviewComment("refuse");
    }
  });
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function main() {
  applyTheme(null);
  bindEvents();

  if (!bridge) {
    setFooterState("未连接 Dashboard");
    toast("请从 AstrBot WebUI 的插件详情页打开本视图", "error", 6000);
    ui.bottleEmpty.hidden = false;
    ui.commentEmpty.hidden = false;
    return;
  }

  try {
    const context = await bridge.ready();
    applyTheme(context);
    if (typeof bridge.onContext === "function") {
      bridge.onContext((next) => applyTheme(next));
    }
    const title = bridge.t
      ? bridge.t("views.review.title", "漂流瓶审核台")
      : "漂流瓶审核台";
    document.title = title;
    ui.title.textContent = title;
    if (context && context.displayName) {
      ui.subtitle.textContent = `${context.displayName} · 漂流瓶与评论审核`;
    }
  } catch (error) {
    console.warn("bridge 初始化失败", error);
  }

  setFooterState("加载中...");
  await Promise.all([refreshStats(), loadBottle(), loadComment()]);
  setFooterState("就绪");
}

main();
