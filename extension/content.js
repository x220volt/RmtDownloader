/**
 * 원격 다운로더 (Remote Downloader) Chrome Extension Content Script
 * 웹페이지에서 다운로드 가능한 주소를 복사(Ctrl+C, 우클릭 링크 복사, 복사 버튼 등)할 때
 * 화면 우측 하단에 원격 다운로드 알림 토스트를 표시합니다.
 */
(() => {
  "use strict";

  if (typeof window.__rdClipboardMonitorCleanup === "function") {
    try {
      window.__rdClipboardMonitorCleanup();
    } catch (e) {}
  }

  const HOST_ELEMENT_ID = "rd-clipboard-toast-host";
  const RETRODB_CONTEXT_TTL_MS = 5 * 60 * 1000;
  const MAX_COPY_TEXT_LENGTH = 8000;
  const TOAST_AUTO_HIDE_MS = 7500;
  const RETRODB_TOAST_AUTO_HIDE_MS = 12000;
  const DUPLICATE_TOAST_COOLDOWN_MS = 3500;

  const HOSTING_PATTERNS = [
    { service: "MEGA", regex: /mega\.(?:nz|co\.nz|io)\/(?:file\/|folder\/|#?!)/i },
    { service: "TeraBox", regex: /(?:1024terabox|terabox|teraboxapp|freeterabox|1024tera|teraboxlink)\.(?:com|app)/i },
    { service: "GoFile", regex: /gofile\.io\/d\//i },
    { service: "1fichier", regex: /1fichier\.com\/\?/i },
    { service: "Transfer.it", regex: /transfer\.it\/t\//i },
    { service: "GigaFile", regex: /(?:gigafile\.nu|xgf\.nu)\//i },
    { service: "kio.ac", regex: /kio\.ac\/[cf]\//i },
    { service: "Naver MYBOX", regex: /mybox\.naver\.com\/share\//i },
    { service: "Naver Cafe", regex: /(?:downapi\.cafe\.naver\.com|cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?|ca-fe\/))/i },
    { service: "Tistory", regex: /(?:\.tistory\.com\/(?:(?:m\/)?\d+|entry\/|attachment)|kakaocdn\.net\/dn|daumcdn\.net)/i },
    { service: "Rootz", regex: /rootz\.so\/d\//i },
    { service: "GitHub", regex: /^https?:\/\/(?:(?:www\.)?github\.com\/(?!(?:settings|notifications|explore|trending|topics|marketplace|sponsors|login|signup|join|orgs|organizations|users|search|features|pricing|enterprise|about|security|customer-stories|readme|collections|events|codespaces|copilot|pulls|issues|site|solutions|resources|account|apps|dashboard)(?:\/|$|\?|#))[^\/\s?#]+\/[^\/\s?#]+(?:\/(?:releases|archive|zipball|tarball|raw|blob|tree)(?:\/|$|\?|#)|\/?(?:$|\?|#))|(?:raw|objects|release-assets|gist)\.githubusercontent\.com\/[^\/\s]+\/|codeload\.github\.com\/[^\/\s]+\/)/i },
    { service: "Romsfun", regex: /romsfun\.com\/download\//i },
    { service: "Romspure", regex: /romspure\.cc\/(?:download|roms)\//i },
    { service: "WoWroms", regex: /wowroms\.com\//i },
    { service: "PlanetEmu", regex: /planetemu\.net\/(?:[a-z]{2}\/)?(?:roms?\/[^/?#]+\/[^/?#]+|download\/rom)/i },
    { service: "Vimm's Lair", regex: /vimm\.net\/vault\//i },
    { service: "X-Share", regex: /x-share\.net\/[sb]\//i },
    { service: "Google Drive", regex: /(?:drive|docs|drive\.usercontent)\.google\.com\/(?:file\/(?:u\/\d+\/)?d\/|open\?id=|uc\?(?:[^#\s]*&)?id=|drive\/(?:u\/\d+\/)?folders\/|download\?id=)/i },
    { service: "MediaFire", regex: /mediafire\.com\/(?:file|download|view|\?)/i },
    { service: "FileKeeper", regex: /filekeeper\.net\/[a-zA-Z0-9_-]+/i },
    { service: "DataNodes", regex: /datanodes\.to\/[a-zA-Z0-9_-]+/i },
    { service: "Buzzheavier", regex: /(?:buzzheavier\.com|bzzhr\.to)\/[a-zA-Z0-9_-]+/i },
    { service: "VikingFile", regex: /(?:vik[i1]ngf[i1]le\.[a-z]{2,})\/[fd]\//i },
    { service: "MegaUp", regex: /megaup\.net\//i },
    { service: "Pixeldrain", regex: /(?:pixeldrain\.(?:com|net)\/(?:u|l|d|api\/file)\/|pixeldra\.in\/)/i },
    { service: "TeeTeeTee", regex: /teeteetee-korean\.com\/(?:projects\/[a-zA-Z0-9_\-]+|api\/patch-releases|api\/[a-z0-9_\-]+-xdelta|downloads\/)/i },
    { service: "Hangulteo", regex: /hangulteo\.com\/[a-zA-Z0-9_\-]+/i },
  ];

  const FILE_EXT_REGEX = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|bin|cue|chd|nsp|xci|nsz|apk|exe|msi|dmg|pkg|xdelta|bps|ips|ups|ppf|patch|mp4|mkv|avi|mov|webm|mp3|flac|wav|torrent)(\?.*)?$/i;
  const URL_TOKEN_REGEX = /(?:https?:\/\/[^\s<>"'`|]+|magnet:\?xt=[^\s<>"'`|]+)/gi;
  const B64_TOKEN_REGEX = /(?:[A-Za-z0-9+/_-]{16,}={0,2})/g;
  const PASSWORD_REGEX = /(?:비번|암호|비밀번호|추출\s*코드|dlkey|pwd|pw|pass|password|code)\s*[:=]\s*([^\s"'<>\n\r,;]{2,32})|(?:비번|암호|비밀번호|추출\s*코드)\s+([a-zA-Z0-9_!@#$%^&*-]{2,32})/i;

  let clipboardPromptEnabled = true;
  let hideTimer = null;
  let copyTimer = null;
  let clipboardCheckTimers = [];
  let lastPromptRawText = "";
  let lastPromptAt = 0;
  let lastShownRetroDbToastKey = "";

  function isRetroDbSite() {
    const host = (location.hostname || "").toLowerCase();
    return (
      host === "retrodb.info" ||
      host.endsWith(".retrodb.info") ||
      host === "redb.me" ||
      host.endsWith(".redb.me")
    );
  }

  function sanitizeFolderPart(str) {
    return String(str || "")
      .replace(/\s+/g, " ")
      .replace(/[\\/:*?"<>|]/g, "_")
      .trim();
  }

  function formatRetroDbSubfolder(platform, title, version, author) {
    const p = sanitizeFolderPart(platform);
    const t = sanitizeFolderPart(title);
    const vRaw = sanitizeFolderPart(version);
    const a = sanitizeFolderPart(author);
    const parts = [];
    if (p) {
      parts.push(`[${p}]`);
    }
    if (t) {
      parts.push(t);
    }
    if (vRaw && vRaw !== "-") {
      const vNorm = /^v/i.test(vRaw) ? vRaw : `v${vRaw}`;
      parts.push(vNorm);
    }
    if (a && a !== "-") {
      parts.push(`(${a})`);
    }
    return parts.join(" ").trim();
  }

  function readElementText(root, selector) {
    if (!root || typeof root.querySelector !== "function") return "";
    const el = root.querySelector(selector);
    return el && el.textContent ? el.textContent.replace(/\s+/g, " ").trim() : "";
  }

  function extractRetroDbContextFromTrigger(targetNode) {
    if (!isRetroDbSite() || !targetNode || typeof targetNode.closest !== "function") {
      return null;
    }
    const goAnchor = targetNode.closest('a[data-kpgo], a[href*="/krpatch/go"]');
    if (!goAnchor) return null;

    let patchId = (goAnchor.getAttribute("data-kpgo") || "").trim();
    if (!patchId) {
      try {
        const parsedGo = new URL(goAnchor.getAttribute("href") || "", location.origin);
        patchId = (parsedGo.searchParams.get("id") || "").trim();
      } catch (e) {}
    }

    let platform = "";
    let title = "";
    let version = "";
    let author = "";

    const kpItem = goAnchor.closest(".kpItem");
    if (kpItem) {
      if (!patchId) {
        patchId = (kpItem.getAttribute("data-id") || "").trim();
      }
      platform = readElementText(kpItem, ".kpBadge");
      title = readElementText(kpItem, ".kpTitleT");
      version = readElementText(kpItem, ".kpVer");
      author = readElementText(kpItem, ".kpAuthor");
    } else {
      const kpdOther = goAnchor.closest(".kpdOther");
      const heroWatch = document.querySelector(".kpdWatch[data-id]");
      const watchId = heroWatch ? (heroWatch.getAttribute("data-id") || "").trim() : "";
      const watchPlatform = heroWatch ? (heroWatch.getAttribute("data-c") || "").trim() : "";
      const watchTitle = heroWatch ? (heroWatch.getAttribute("data-ti") || "").trim() : "";
      const watchAuthor = heroWatch ? (heroWatch.getAttribute("data-a") || "").trim() : "";

      if (kpdOther) {
        if (!patchId) {
          patchId = (kpdOther.getAttribute("data-kpd-row") || "").trim();
        }
        platform = readElementText(kpdOther, ".kpBadge") || watchPlatform || readElementText(document, ".kpdHeroCode");
        title = readElementText(kpdOther, ".kpdOtherNameT") || watchTitle;
        version = readElementText(kpdOther, ".kpdOtherVer");
        author = readElementText(kpdOther, ".kpAuthor, .kpdOtherAuthor");
        if (!author && (!watchId || !patchId || patchId === watchId || kpdOther.querySelector(".kpOldTag"))) {
          author = watchAuthor || readElementText(document, ".kpdHeroAuthor");
        }
      } else {
        if (!patchId) {
          patchId = watchId;
        }
        platform = watchPlatform || readElementText(document, ".kpdHeroCode");
        title = watchTitle || readElementText(document, ".kpdTitle").replace(/\s*한글패치\s*$/, "").trim();
        version = readElementText(document, ".kpdHeroKV b");
        author = watchAuthor || readElementText(document, ".kpdHeroAuthor");
      }
    }

    const subfolder = formatRetroDbSubfolder(platform, title, version, author);
    if (!subfolder) return null;

    return {
      subfolder: subfolder,
      platform: platform,
      title: title,
      version: version,
      author: author,
      patchId: patchId,
      retrodbUrl: location.href,
      clickedAt: Date.now(),
    };
  }

  async function getValidRetroDbContext() {
    try {
      if (!chrome.storage || !chrome.storage.local) return null;
      const items = await new Promise((resolve) =>
        chrome.storage.local.get(["pendingRetroDbContext"], (data) => {
          void chrome.runtime.lastError;
          resolve(data || {});
        })
      );
      const ctx = items && items.pendingRetroDbContext;
      if (!ctx || typeof ctx !== "object" || !ctx.subfolder) return null;
      if (typeof ctx.clickedAt !== "number" || Date.now() - ctx.clickedAt > RETRODB_CONTEXT_TTL_MS) {
        return null;
      }
      return ctx;
    } catch (e) {
      return null;
    }
  }

  function clearRetroDbContext() {
    try {
      if (chrome.storage && chrome.storage.local) {
        chrome.storage.local.remove(["pendingRetroDbContext"], () => {
          void chrome.runtime.lastError;
        });
      }
    } catch (e) {}
    try {
      chrome.runtime.sendMessage({ action: "clearRetroDbContext" }, () => {
        void chrome.runtime.lastError;
      });
    } catch (e) {}
  }

  function isRemoteDownloaderDashboard() {
    return Boolean(
      document.getElementById("jobsContainer") && document.getElementById("urlInput")
    );
  }

  function loadPromptSetting() {
    try {
      if (chrome.storage && chrome.storage.local) {
        chrome.storage.local.get(["clipboardCopyPrompt"], (items) => {
          void chrome.runtime.lastError;
          if (items && typeof items.clipboardCopyPrompt === "boolean") {
            clipboardPromptEnabled = items.clipboardCopyPrompt;
          } else {
            clipboardPromptEnabled = true;
          }
        });
      }
    } catch (e) {}
  }

  loadPromptSetting();

  function onStorageChanged(changes, areaName) {
    if (areaName === "local" && changes.clipboardCopyPrompt) {
      clipboardPromptEnabled = changes.clipboardCopyPrompt.newValue !== false;
    }
  }

  try {
    if (chrome.storage && chrome.storage.onChanged) {
      chrome.storage.onChanged.addListener(onStorageChanged);
    }
  } catch (e) {}

  function identifyService(url) {
    if (!url) return "";
    if (url.startsWith("magnet:?")) return "BitTorrent";
    for (const entry of HOSTING_PATTERNS) {
      if (entry.regex.test(url)) return entry.service;
    }
    if (FILE_EXT_REGEX.test(url)) return "Direct File";
    return "";
  }

  function decodeBase64Once(token) {
    if (!token || token.length < 16) return "";
    try {
      let normalized = token.replace(/-/g, "+").replace(/_/g, "/");
      while (normalized.length % 4 !== 0) {
        normalized += "=";
      }
      const binary = atob(normalized);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      const decoded = new TextDecoder("utf-8", { fatal: false }).decode(bytes).trim();
      if (!decoded || /[\x00-\x08\x0e-\x1f]/.test(decoded)) {
        return "";
      }
      return decoded;
    } catch (e) {
      return "";
    }
  }

  function decodeBase64Chain(token) {
    let current = decodeBase64Once(token);
    if (!current) return "";
    if (/^[A-Za-z0-9+/_-]{16,}={0,2}$/.test(current)) {
      const second = decodeBase64Once(current);
      if (second) {
        current = second;
      }
    }
    return current;
  }

  function cleanExtractedUrl(rawUrl) {
    return (rawUrl || "").trim().replace(/[.,;:)\]>\x7d"']+$/, "");
  }

  function isValidDecodedUrl(candidate) {
    if (!candidate) return false;
    if (candidate.startsWith("magnet:?")) return true;
    try {
      const parsed = new URL(candidate);
      return (parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.hostname.includes(".");
    } catch (e) {
      return false;
    }
  }

  function parseDownloadableClipboardText(rawText) {
    const text = (rawText || "").trim();
    if (!text || text.length > MAX_COPY_TEXT_LENGTH) {
      return null;
    }

    const urls = [];
    const seen = new Set();
    let hasBase64 = false;
    let primaryService = "";

    const addCandidate = (url, fromBase64) => {
      const cleaned = cleanExtractedUrl(url);
      if (!cleaned || seen.has(cleaned)) return;
      const service = identifyService(cleaned);
      if (!service && !(fromBase64 && isValidDecodedUrl(cleaned))) {
        return;
      }
      seen.add(cleaned);
      urls.push(cleaned);
      if (fromBase64) hasBase64 = true;
      if (!primaryService) {
        primaryService = service || (fromBase64 ? "Base64 링크" : "Direct Link");
      }
    };

    let m;
    URL_TOKEN_REGEX.lastIndex = 0;
    while ((m = URL_TOKEN_REGEX.exec(text)) !== null) {
      addCandidate(m[0], false);
    }

    B64_TOKEN_REGEX.lastIndex = 0;
    while ((m = B64_TOKEN_REGEX.exec(text)) !== null) {
      const decoded = decodeBase64Chain(m[0]);
      if (!decoded) continue;
      if (decoded.includes("http://") || decoded.includes("https://") || decoded.includes("magnet:?")) {
        const innerMatches = decoded.match(URL_TOKEN_REGEX);
        if (innerMatches) {
          for (const innerUrl of innerMatches) {
            addCandidate(innerUrl, true);
          }
        }
      }
    }

    if (urls.length === 0) {
      return null;
    }

    let password = "";
    const pwMatch = text.match(PASSWORD_REGEX);
    if (pwMatch) {
      password = (pwMatch[1] || pwMatch[2] || "").trim();
    }

    return {
      urls: urls,
      password: password,
      service: hasBase64 && primaryService === "Base64 링크" ? "Base64" : (primaryService || "링크"),
      hasBase64: hasBase64,
    };
  }

  function resolveAnchorHref(anchorEl) {
    if (!anchorEl) return "";
    try {
      const rawHref = anchorEl.getAttribute("href") || "";
      if (!rawHref || rawHref.startsWith("#") || /^javascript:/i.test(rawHref)) {
        return "";
      }
      const absUrl = new URL(rawHref, document.baseURI).href;
      if (/^(?:https?:\/\/|magnet:\?)/i.test(absUrl)) {
        return absUrl;
      }
    } catch (e) {}
    return "";
  }

  function findClosestAnchorHref(node) {
    let cur = node;
    while (cur && cur !== document) {
      if (cur.nodeType === 1 && cur.tagName === "A" && cur.hasAttribute("href")) {
        return resolveAnchorHref(cur);
      }
      cur = cur.parentNode;
    }
    return "";
  }

  function getSelectedCopyText(event) {
    const active = document.activeElement;
    if (active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT")) {
      const start = typeof active.selectionStart === "number" ? active.selectionStart : 0;
      const end = typeof active.selectionEnd === "number" ? active.selectionEnd : 0;
      if (end > start && typeof active.value === "string") {
        const slice = active.value.slice(start, end).trim();
        if (slice) return slice;
      }
    }

    try {
      const sel = window.getSelection ? window.getSelection() : null;
      const selText = sel ? String(sel).trim() : "";

      if (selText && parseDownloadableClipboardText(selText)) {
        return selText;
      }

      if (sel && sel.rangeCount > 0) {
        const anchorHrefs = [];
        const addHref = (href) => {
          if (href && !anchorHrefs.includes(href)) {
            anchorHrefs.push(href);
          }
        };

        const range = sel.getRangeAt(0);
        const ancestorHref = findClosestAnchorHref(range.commonAncestorContainer);
        if (ancestorHref) {
          addHref(ancestorHref);
        }
        const fragment = range.cloneContents();
        if (fragment && typeof fragment.querySelectorAll === "function") {
          const anchors = fragment.querySelectorAll("a[href]");
          for (const a of anchors) {
            addHref(resolveAnchorHref(a));
          }
        }
        if (anchorHrefs.length > 0) {
          return selText ? `${anchorHrefs.join("\n")}\n${selText}` : anchorHrefs.join("\n");
        }
      }

      if (selText) return selText;
    } catch (e) {}

    try {
      if (event && event.clipboardData) {
        const clipText = (event.clipboardData.getData("text/plain") || "").trim();
        if (clipText) return clipText;
      }
    } catch (e) {}

    return "";
  }

  function removeToast() {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    const existing = document.getElementById(HOST_ELEMENT_ID);
    if (existing) {
      existing.remove();
    }
  }

  function scheduleToastAutoHide(durationMs = TOAST_AUTO_HIDE_MS) {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      removeToast();
    }, durationMs);
  }

  function showClipboardDownloadToast(parsed, rawText, retroCtx = null) {
    const container = document.body || document.documentElement;
    if (!container) return false;
    removeToast();

    const host = document.createElement("div");
    host.id = HOST_ELEMENT_ID;
    host.style.cssText =
      "all: initial; position: fixed; right: 20px; bottom: 20px; z-index: 2147483647; display: block !important; visibility: visible !important; opacity: 1 !important; pointer-events: auto !important;";

    const shadow = host.attachShadow({ mode: "open" });

    const styleEl = document.createElement("style");
    styleEl.textContent = `
      .rd-toast {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Malgun Gothic", sans-serif;
        width: 332px;
        max-width: calc(100vw - 32px);
        background: rgba(15, 23, 42, 0.96);
        color: #f8fafc;
        border: 1px solid rgba(99, 102, 241, 0.45);
        border-radius: 12px;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
        padding: 12px 13px;
        box-sizing: border-box;
        backdrop-filter: blur(8px);
        animation: rdSlideUp 0.2s ease-out;
      }
      @keyframes rdSlideUp {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }
      .rd-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 6px;
      }
      .rd-badges {
        display: flex;
        align-items: center;
        gap: 6px;
        min-width: 0;
        flex-wrap: wrap;
      }
      .rd-service {
        background: rgba(99, 102, 241, 0.22);
        color: #c7d2fe;
        border: 1px solid rgba(99, 102, 241, 0.45);
        font-size: 11px;
        font-weight: 700;
        padding: 2px 7px;
        border-radius: 999px;
      }
      .rd-subbadge {
        font-size: 11px;
        color: #94a3b8;
        font-weight: 600;
      }
      .rd-pwbadge {
        background: rgba(16, 185, 129, 0.16);
        color: #6ee7b7;
        border: 1px solid rgba(16, 185, 129, 0.35);
        font-size: 10.5px;
        font-weight: 600;
        padding: 1px 6px;
        border-radius: 999px;
      }
      .rd-folderbadge {
        background: rgba(245, 158, 11, 0.16);
        color: #fcd34d;
        border: 1px solid rgba(245, 158, 11, 0.35);
        font-size: 10.5px;
        font-weight: 600;
        padding: 1px 6px;
        border-radius: 999px;
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .rd-close {
        background: transparent;
        border: none;
        color: #94a3b8;
        font-size: 16px;
        line-height: 1;
        cursor: pointer;
        padding: 2px 4px;
        border-radius: 4px;
      }
      .rd-close:hover {
        color: #f8fafc;
        background: rgba(255, 255, 255, 0.08);
      }
      .rd-title {
        font-size: 12.5px;
        font-weight: 600;
        color: #e2e8f0;
        margin-bottom: 4px;
      }
      .rd-url {
        font-size: 11px;
        color: #94a3b8;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-bottom: 10px;
        background: rgba(2, 6, 23, 0.55);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 6px;
        padding: 5px 7px;
      }
      .rd-actions {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .rd-btn-dl {
        flex: 1;
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: #ffffff;
        border: none;
        border-radius: 7px;
        padding: 7px 10px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: filter 0.15s ease;
      }
      .rd-btn-dl:hover:not(:disabled) {
        filter: brightness(1.1);
      }
      .rd-btn-dl:disabled {
        opacity: 0.72;
        cursor: default;
      }
      .rd-btn-dl.ok {
        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
      }
      .rd-btn-dl.err {
        background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
      }
      .rd-btn-dismiss {
        background: rgba(255, 255, 255, 0.07);
        color: #cbd5e1;
        border: 1px solid rgba(148, 163, 184, 0.25);
        border-radius: 7px;
        padding: 7px 10px;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
      }
      .rd-btn-dismiss:hover {
        background: rgba(255, 255, 255, 0.14);
        color: #ffffff;
      }
    `;

    const card = document.createElement("div");
    card.className = "rd-toast";

    const head = document.createElement("div");
    head.className = "rd-head";

    const badges = document.createElement("div");
    badges.className = "rd-badges";

    const svcBadge = document.createElement("span");
    svcBadge.className = "rd-service";
    svcBadge.textContent = parsed.hasBase64 && parsed.service !== "Base64" ? `${parsed.service} (Base64)` : parsed.service;
    badges.appendChild(svcBadge);

    if (parsed.urls.length > 1) {
      const cntBadge = document.createElement("span");
      cntBadge.className = "rd-subbadge";
      cntBadge.textContent = `총 ${parsed.urls.length}개 링크`;
      badges.appendChild(cntBadge);
    }

    if (parsed.password) {
      const pwBadge = document.createElement("span");
      pwBadge.className = "rd-pwbadge";
      pwBadge.textContent = "🔑 암호 포함";
      badges.appendChild(pwBadge);
    }

    if (retroCtx && retroCtx.subfolder) {
      const folderBadge = document.createElement("span");
      folderBadge.className = "rd-folderbadge";
      folderBadge.textContent = `📁 ${retroCtx.subfolder}`;
      folderBadge.title = `저장 폴더: ${retroCtx.subfolder}`;
      badges.appendChild(folderBadge);
    }

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "rd-close";
    closeBtn.title = "닫기";
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", removeToast);

    head.appendChild(badges);
    head.appendChild(closeBtn);

    const titleEl = document.createElement("div");
    titleEl.className = "rd-title";
    titleEl.textContent = "복사한 주소를 원격 다운로더로 보낼까요?";

    const urlEl = document.createElement("div");
    urlEl.className = "rd-url";
    urlEl.textContent = parsed.urls.length > 1 ? `${parsed.urls[0]} (+${parsed.urls.length - 1}개)` : parsed.urls[0];
    urlEl.title = parsed.urls.join("\n");

    const actions = document.createElement("div");
    actions.className = "rd-actions";

    const dlBtn = document.createElement("button");
    dlBtn.type = "button";
    dlBtn.className = "rd-btn-dl";
    dlBtn.textContent = "📥 원격 다운로드";

    const dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.className = "rd-btn-dismiss";
    dismissBtn.textContent = "닫기";
    dismissBtn.addEventListener("click", removeToast);

    dlBtn.addEventListener("click", () => {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      dlBtn.disabled = true;
      dismissBtn.disabled = true;
      dlBtn.textContent = "⏳ 서버로 전송 중...";

      try {
        chrome.runtime.sendMessage(
          {
            action: "sendUrl",
            url: parsed.urls.join("\n"),
            subfolder: retroCtx && retroCtx.subfolder ? retroCtx.subfolder : "",
            password: parsed.password || "",
            referer: location.href,
            sourceTitle: (retroCtx && retroCtx.subfolder) || document.title || "",
            sourceUrl: location.href,
            rawClipboardText: rawText,
          },
          (response) => {
            if (chrome.runtime.lastError || !response || !response.ok) {
              dlBtn.className = "rd-btn-dl err";
              dlBtn.textContent = "❌ 전송 실패 (팝업 설정 확인)";
              dlBtn.disabled = false;
              dismissBtn.disabled = false;
              scheduleToastAutoHide();
              return;
            }
            const count = response.count || parsed.urls.length || 1;
            if (retroCtx) {
              clearRetroDbContext();
            }
            dlBtn.className = "rd-btn-dl ok";
            dlBtn.textContent = `✓ ${count}개 작업 전송 완료!`;
            setTimeout(() => {
              removeToast();
            }, 1800);
          }
        );
      } catch (err) {
        dlBtn.className = "rd-btn-dl err";
        dlBtn.textContent = "❌ 확장 프로그램 연결 오류";
        dlBtn.disabled = false;
        dismissBtn.disabled = false;
        scheduleToastAutoHide();
      }
    });

    actions.appendChild(dlBtn);
    actions.appendChild(dismissBtn);

    card.appendChild(head);
    card.appendChild(titleEl);
    card.appendChild(urlEl);
    card.appendChild(actions);

    card.addEventListener("mouseenter", () => {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
    });
    card.addEventListener("mouseleave", () => {
      if (!dlBtn.disabled) {
        scheduleToastAutoHide();
      }
    });

    shadow.appendChild(styleEl);
    shadow.appendChild(card);
    container.appendChild(host);
    scheduleToastAutoHide();
    return true;
  }

  function showRetroDbArrivalToast(ctx) {
    if (!ctx || !ctx.subfolder) return false;
    const container = document.body || document.documentElement;
    if (!container) return false;
    removeToast();

    function scheduleRetroDbAutoHide(durationMs = RETRODB_TOAST_AUTO_HIDE_MS) {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => {
        clearRetroDbContext();
        removeToast();
      }, durationMs);
    }

    const host = document.createElement("div");
    host.id = HOST_ELEMENT_ID;
    host.style.cssText =
      "all: initial; position: fixed; right: 20px; bottom: 20px; z-index: 2147483647; display: block !important; visibility: visible !important; opacity: 1 !important; pointer-events: auto !important;";

    const shadow = host.attachShadow({ mode: "open" });

    const styleEl = document.createElement("style");
    styleEl.textContent = `
      .rd-toast {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Malgun Gothic", sans-serif;
        width: 348px;
        max-width: calc(100vw - 32px);
        background: rgba(15, 23, 42, 0.96);
        color: #f8fafc;
        border: 1px solid rgba(245, 158, 11, 0.5);
        border-radius: 12px;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
        padding: 12px 13px;
        box-sizing: border-box;
        backdrop-filter: blur(8px);
        animation: rdSlideUp 0.2s ease-out;
      }
      @keyframes rdSlideUp {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }
      .rd-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 6px;
      }
      .rd-service {
        background: rgba(245, 158, 11, 0.2);
        color: #fcd34d;
        border: 1px solid rgba(245, 158, 11, 0.45);
        font-size: 11px;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 999px;
      }
      .rd-close {
        background: transparent;
        border: none;
        color: #94a3b8;
        font-size: 16px;
        line-height: 1;
        cursor: pointer;
        padding: 2px 4px;
        border-radius: 4px;
      }
      .rd-close:hover {
        color: #f8fafc;
        background: rgba(255, 255, 255, 0.08);
      }
      .rd-folder {
        font-size: 12.5px;
        font-weight: 700;
        color: #fef3c7;
        margin-bottom: 4px;
        word-break: break-all;
      }
      .rd-url {
        font-size: 11px;
        color: #94a3b8;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-bottom: 10px;
        background: rgba(2, 6, 23, 0.55);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 6px;
        padding: 5px 7px;
      }
      .rd-actions {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .rd-btn-dl {
        flex: 1;
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: #ffffff;
        border: none;
        border-radius: 7px;
        padding: 7px 10px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: filter 0.15s ease;
      }
      .rd-btn-dl:hover:not(:disabled) {
        filter: brightness(1.1);
      }
      .rd-btn-dl:disabled {
        opacity: 0.72;
        cursor: default;
      }
      .rd-btn-dl.ok {
        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
      }
      .rd-btn-dl.err {
        background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
      }
      .rd-btn-dismiss {
        background: rgba(255, 255, 255, 0.07);
        color: #cbd5e1;
        border: 1px solid rgba(148, 163, 184, 0.25);
        border-radius: 7px;
        padding: 7px 10px;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
      }
      .rd-btn-dismiss:hover {
        background: rgba(255, 255, 255, 0.14);
        color: #ffffff;
      }
    `;

    const card = document.createElement("div");
    card.className = "rd-toast";

    const head = document.createElement("div");
    head.className = "rd-head";

    const badge = document.createElement("span");
    badge.className = "rd-service";
    badge.textContent = "RetroDB 한글패치";

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "rd-close";
    closeBtn.title = "닫기";
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", () => {
      clearRetroDbContext();
      removeToast();
    });

    head.appendChild(badge);
    head.appendChild(closeBtn);

    const folderEl = document.createElement("div");
    folderEl.className = "rd-folder";
    folderEl.textContent = `📁 ${ctx.subfolder}`;

    const urlEl = document.createElement("div");
    urlEl.className = "rd-url";
    urlEl.textContent = location.href;
    urlEl.title = location.href;

    const actions = document.createElement("div");
    actions.className = "rd-actions";

    const dlBtn = document.createElement("button");
    dlBtn.type = "button";
    dlBtn.className = "rd-btn-dl";
    dlBtn.textContent = "📥 이 폴더로 현재 페이지 다운로드";

    const dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.className = "rd-btn-dismiss";
    dismissBtn.textContent = "닫기";
    dismissBtn.addEventListener("click", () => {
      clearRetroDbContext();
      removeToast();
    });

    dlBtn.addEventListener("click", () => {
      clearRetroDbContext();
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      dlBtn.disabled = true;
      dismissBtn.disabled = true;
      dlBtn.textContent = "⏳ 서버로 전송 중...";

      try {
        chrome.runtime.sendMessage(
          {
            action: "sendUrl",
            url: location.href,
            subfolder: ctx.subfolder,
            referer: location.href,
            sourceTitle: ctx.subfolder,
            sourceUrl: location.href,
          },
          (response) => {
            clearRetroDbContext();
            if (chrome.runtime.lastError || !response || !response.ok) {
              dlBtn.className = "rd-btn-dl err";
              dlBtn.textContent = "❌ 전송 실패 (팝업 설정 확인)";
              dlBtn.disabled = false;
              dismissBtn.disabled = false;
              scheduleRetroDbAutoHide(RETRODB_TOAST_AUTO_HIDE_MS);
              return;
            }
            const count = response.count || 1;
            dlBtn.className = "rd-btn-dl ok";
            dlBtn.textContent = `✓ ${count}개 작업 전송 완료!`;
            setTimeout(() => {
              removeToast();
            }, 1800);
          }
        );
      } catch (err) {
        clearRetroDbContext();
        dlBtn.className = "rd-btn-dl err";
        dlBtn.textContent = "❌ 확장 프로그램 연결 오류";
        dlBtn.disabled = false;
        dismissBtn.disabled = false;
        scheduleRetroDbAutoHide(RETRODB_TOAST_AUTO_HIDE_MS);
      }
    });

    actions.appendChild(dlBtn);
    actions.appendChild(dismissBtn);

    card.appendChild(head);
    card.appendChild(folderEl);
    card.appendChild(urlEl);
    card.appendChild(actions);

    card.addEventListener("mouseenter", () => {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
    });
    card.addEventListener("mouseleave", () => {
      if (!dlBtn.disabled) {
        scheduleRetroDbAutoHide(RETRODB_TOAST_AUTO_HIDE_MS);
      }
    });

    shadow.appendChild(styleEl);
    shadow.appendChild(card);
    container.appendChild(host);
    scheduleRetroDbAutoHide(RETRODB_TOAST_AUTO_HIDE_MS);
    return true;
  }

  async function checkPendingRetroDbArrival() {
    if (isRetroDbSite() || isRemoteDownloaderDashboard()) {
      return;
    }
    if (!/^https?:\/\//i.test(location.href || "")) {
      return;
    }
    const ctx = await getValidRetroDbContext();
    if (!ctx || !ctx.subfolder) {
      return;
    }
    const dedupKey = `${ctx.clickedAt}|${location.href}`;
    if (dedupKey === lastShownRetroDbToastKey) {
      return;
    }
    lastShownRetroDbToastKey = dedupKey;
    showRetroDbArrivalToast(ctx);
  }

  async function triggerToastForClipboardText(rawText) {
    if (!clipboardPromptEnabled) {
      return { ok: false, reason: "disabled" };
    }
    if (isRemoteDownloaderDashboard()) {
      return { ok: false, reason: "dashboard" };
    }

    const text = (rawText || "").trim();
    if (!text || text.length > MAX_COPY_TEXT_LENGTH) {
      return { ok: false, reason: "empty" };
    }

    const now = Date.now();
    if (text === lastPromptRawText && now - lastPromptAt < DUPLICATE_TOAST_COOLDOWN_MS) {
      return { ok: true, reason: "deduped" };
    }

    const parsed = parseDownloadableClipboardText(text);
    if (!parsed) {
      return { ok: false, reason: "unsupported" };
    }

    lastPromptRawText = text;
    lastPromptAt = now;
    const retroCtx = isRetroDbSite() ? null : await getValidRetroDbContext();
    const shown = showClipboardDownloadToast(parsed, text, retroCtx);
    return { ok: shown, reason: shown ? "shown" : "no_container" };
  }

  function clearClipboardCheckTimers() {
    while (clipboardCheckTimers.length > 0) {
      clearTimeout(clipboardCheckTimers.pop());
    }
  }

  function requestBackgroundClipboardCheck() {
    if (!clipboardPromptEnabled || isRemoteDownloaderDashboard()) return;
    try {
      chrome.runtime.sendMessage({ action: "checkClipboardNow" }, () => {
        void chrome.runtime.lastError;
      });
    } catch (e) {}
  }

  function scheduleClipboardCheck() {
    if (!clipboardPromptEnabled || isRemoteDownloaderDashboard()) return;
    clearClipboardCheckTimers();
    for (const delay of [80, 450, 1100]) {
      const tid = setTimeout(() => {
        requestBackgroundClipboardCheck();
      }, delay);
      clipboardCheckTimers.push(tid);
    }
  }

  function onCopyEvent(event) {
    if (isRemoteDownloaderDashboard()) return;

    const rawText = getSelectedCopyText(event);
    if (!rawText) return;

    const parsed = parseDownloadableClipboardText(rawText);
    if (!parsed) return;

    try {
      if (chrome.storage && chrome.storage.local) {
        chrome.storage.local.set(
          {
            lastCopiedClipboardText: rawText,
            lastCopiedClipboardAt: Date.now(),
          },
          () => {
            void chrome.runtime.lastError;
          }
        );
      }
    } catch (e) {}

    if (!clipboardPromptEnabled) return;

    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      copyTimer = null;
      triggerToastForClipboardText(rawText);
    }, 60);
  }

  function captureRetroDbContextFromEvent(event) {
    if (!isRetroDbSite()) return;
    if (event && event.type === "auxclick" && event.button !== 1) return;
    const ctx = extractRetroDbContextFromTrigger(event ? event.target : null);
    if (!ctx || !ctx.subfolder) return;
    try {
      if (chrome.storage && chrome.storage.local) {
        chrome.storage.local.set({ pendingRetroDbContext: ctx }, () => {
          void chrome.runtime.lastError;
        });
      }
    } catch (e) {}
    try {
      chrome.runtime.sendMessage({ action: "saveRetroDbContext", context: ctx }, () => {
        void chrome.runtime.lastError;
      });
    } catch (e) {}
  }

  function onClickEvent(event) {
    captureRetroDbContextFromEvent(event);
  }

  function onAuxClickEvent(event) {
    captureRetroDbContextFromEvent(event);
  }

  function onContextMenuEvent() {
    // 백그라운드 클립보드 폴링을 사용하지 않습니다.
  }

  function onRuntimeMessage(request, _sender, sendResponse) {
    if (!request || request.action !== "showClipboardToast") {
      return false;
    }
    triggerToastForClipboardText(request.rawText || "")
      .then((result) => sendResponse(result))
      .catch(() => sendResponse({ ok: false, reason: "error" }));
    return true;
  }

  document.addEventListener("copy", onCopyEvent, true);
  document.addEventListener("click", onClickEvent, true);
  document.addEventListener("auxclick", onAuxClickEvent, true);
  document.addEventListener("contextmenu", onContextMenuEvent, true);

  checkPendingRetroDbArrival();

  try {
    if (chrome.runtime && chrome.runtime.onMessage) {
      chrome.runtime.onMessage.addListener(onRuntimeMessage);
    }
  } catch (e) {}

  window.__rdClipboardMonitorCleanup = () => {
    document.removeEventListener("copy", onCopyEvent, true);
    document.removeEventListener("click", onClickEvent, true);
    document.removeEventListener("auxclick", onAuxClickEvent, true);
    document.removeEventListener("contextmenu", onContextMenuEvent, true);
    try {
      if (chrome.storage && chrome.storage.onChanged) {
        chrome.storage.onChanged.removeListener(onStorageChanged);
      }
    } catch (e) {}
    try {
      if (chrome.runtime && chrome.runtime.onMessage) {
        chrome.runtime.onMessage.removeListener(onRuntimeMessage);
      }
    } catch (e) {}
    if (copyTimer) {
      clearTimeout(copyTimer);
      copyTimer = null;
    }
    clearClipboardCheckTimers();
    removeToast();
  };
})();
