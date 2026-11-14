/**
 * 원격 다운로더 (Remote Downloader) Chrome Extension Background Service Worker
 * 우클릭 컨텍스트 메뉴, 오프스크린 클립보드 감시 및 백그라운드 전송 처리
 */

const OFFSCREEN_DOCUMENT_PATH = "offscreen.html";
const MAX_COPY_TEXT_LENGTH = 8000;

const CLIPBOARD_HOSTING_PATTERNS = [
  { service: "MEGA", regex: /mega\.(?:nz|co\.nz|io)\/(?:file\/|folder\/|#?!)/i },
  { service: "TeraBox", regex: /(?:1024terabox|terabox|teraboxapp|freeterabox|1024tera|teraboxlink)\.(?:com|app)/i },
  { service: "GoFile", regex: /gofile\.io\/d\//i },
  { service: "1fichier", regex: /1fichier\.com\/\?/i },
  { service: "Transfer.it", regex: /transfer\.it\/t\//i },
  { service: "GigaFile", regex: /(?:gigafile\.nu|xgf\.nu)\//i },
  { service: "kio.ac", regex: /kio\.ac\/[cf]\//i },
  { service: "Naver MYBOX", regex: /mybox\.naver\.com\/share\//i },
  { service: "Naver Cafe", regex: /downapi\.cafe\.naver\.com/i },
  { service: "Tistory", regex: /(?:\.tistory\.com\/(?:(?:m\/)?\d+|entry\/|attachment)|kakaocdn\.net\/dn|daumcdn\.net)/i },
  { service: "Rootz", regex: /rootz\.so\/d\//i },
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
  { service: "VikingFile", regex: /(?:vikingfile\.com|vik1ngfile\.site)\/f\//i },
  { service: "MegaUp", regex: /megaup\.net\//i },
  { service: "Pixeldrain", regex: /(?:pixeldrain\.(?:com|net)\/(?:u|l|d|api\/file)\/|pixeldra\.in\/)/i },
];

const CLIPBOARD_FILE_EXT_REGEX = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|bin|cue|chd|nsp|xci|nsz|apk|exe|msi|dmg|pkg|mp4|mkv|avi|mov|webm|mp3|flac|wav|torrent)(\?.*)?$/i;
const URL_TOKEN_REGEX = /(?:https?:\/\/[^\s<>"'`|]+|magnet:\?xt=[^\s<>"'`|]+)/gi;
const B64_TOKEN_REGEX = /(?:[A-Za-z0-9+/_-]{16,}={0,2})/g;
const PASSWORD_REGEX = /(?:비번|암호|비밀번호|추출\s*코드|dlkey|pwd|pw|pass|password|code)\s*[:=]\s*([^\s"'<>\n\r,;]{2,32})|(?:비번|암호|비밀번호|추출\s*코드)\s+([a-zA-Z0-9_!@#$%^&*-]{2,32})/i;

let creatingOffscreenPromise = null;
let tabInjectionPromise = null;
let clipboardProcessChain = Promise.resolve();
let lastHandledClipboardText = "";
const pendingClipboardPrompts = new Map();

function identifyClipboardService(url) {
  if (!url) return "";
  if (url.startsWith("magnet:?")) return "BitTorrent";
  for (const entry of CLIPBOARD_HOSTING_PATTERNS) {
    if (entry.regex.test(url)) return entry.service;
  }
  if (CLIPBOARD_FILE_EXT_REGEX.test(url)) return "Direct File";
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
    const service = identifyClipboardService(cleaned);
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

async function getSessionValue(key) {
  try {
    if (chrome.storage && chrome.storage.session) {
      const items = await chrome.storage.session.get([key]);
      return items ? items[key] : undefined;
    }
  } catch (e) {}
  return undefined;
}

async function setSessionValue(obj) {
  try {
    if (chrome.storage && chrome.storage.session) {
      await chrome.storage.session.set(obj);
    }
  } catch (e) {}
}

async function getClipboardPreferences() {
  let prefs = {};
  try {
    if (chrome.storage && chrome.storage.local) {
      prefs = await new Promise((res) =>
        chrome.storage.local.get(["clipboardCopyPrompt", "lastSentClipboardText"], (items) => {
          void chrome.runtime.lastError;
          res(items || {});
        })
      );
    }
    if (typeof prefs.clipboardCopyPrompt !== "boolean" && chrome.storage && chrome.storage.sync) {
      const syncItems = await new Promise((res) =>
        chrome.storage.sync.get(["clipboardCopyPrompt"], (items) => {
          void chrome.runtime.lastError;
          res(items || {});
        })
      );
      prefs = Object.assign({}, syncItems || {}, prefs || {});
    }
  } catch (e) {}
  return prefs;
}

async function hasOffscreenDocument() {
  try {
    const offscreenUrl = chrome.runtime.getURL(OFFSCREEN_DOCUMENT_PATH);
    if (typeof chrome.runtime.getContexts === "function") {
      const existing = await chrome.runtime.getContexts({
        contextTypes: ["OFFSCREEN_DOCUMENT"],
        documentUrls: [offscreenUrl],
      });
      if (existing && existing.length > 0) {
        return true;
      }
    }
    if (typeof clients !== "undefined" && typeof clients.matchAll === "function") {
      const matched = await clients.matchAll();
      for (const client of matched) {
        if (client.url === offscreenUrl) {
          return true;
        }
      }
    }
  } catch (e) {}
  return false;
}

async function ensureOffscreenDocument() {
  if (!chrome.offscreen || typeof chrome.offscreen.createDocument !== "function") {
    return { open: false, created: false };
  }
  if (await hasOffscreenDocument()) {
    return { open: true, created: false };
  }
  if (creatingOffscreenPromise) {
    await creatingOffscreenPromise;
    return { open: await hasOffscreenDocument(), created: false };
  }
  creatingOffscreenPromise = (async () => {
    try {
      await chrome.offscreen.createDocument({
        url: OFFSCREEN_DOCUMENT_PATH,
        reasons: ["CLIPBOARD"],
        justification: "우클릭 링크 복사, 웹페이지 복사 버튼 등 클립보드 다운로드 링크를 자동 감지하기 위해 사용합니다.",
      });
    } catch (e) {
      // 이미 존재하거나 생성 경합 발생 시 무시
    } finally {
      creatingOffscreenPromise = null;
    }
  })();
  await creatingOffscreenPromise;
  return { open: await hasOffscreenDocument(), created: true };
}

async function injectContentScriptToExistingTabs(force = false) {
  if (!chrome.scripting || typeof chrome.scripting.executeScript !== "function") return;
  if (tabInjectionPromise) {
    return tabInjectionPromise;
  }
  tabInjectionPromise = (async () => {
    try {
      if (!force) {
        const alreadyInjected = await getSessionValue("rdTabsInjected");
        if (alreadyInjected) return;
      }
      await setSessionValue({ rdTabsInjected: true });
      const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"] });
      for (const tab of tabs) {
        if (!tab || typeof tab.id !== "number") continue;
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ["content.js"],
          });
        } catch (err) {
          // 권한이 제한된 특수 페이지(크롬 웹스토어 등)는 무시
        }
      }
    } catch (e) {} finally {
      tabInjectionPromise = null;
    }
  })();
  return tabInjectionPromise;
}

async function showClipboardPromptNotification(rawText, parsed, activeTab) {
  const notifyId = `rd-clip-prompt-${Date.now()}`;
  const activeUrl = activeTab && /^https?:\/\//i.test(activeTab.url || "") ? activeTab.url : "";
  const promptData = {
    url: parsed.urls.join("\n"),
    password: parsed.password || "",
    referer: activeUrl,
    sourceTitle: (activeTab && activeTab.title) || "",
    sourceUrl: activeUrl,
    rawClipboardText: rawText,
    createdAt: Date.now(),
  };

  pendingClipboardPrompts.set(notifyId, promptData);
  try {
    const stored = (await getSessionValue("rdPendingClipPrompts")) || {};
    stored[notifyId] = promptData;
    await setSessionValue({ rdPendingClipPrompts: stored });
  } catch (e) {}

  const serviceLabel = parsed.hasBase64 && parsed.service !== "Base64" ? `${parsed.service} (Base64)` : parsed.service;
  const previewUrl = parsed.urls.length > 1 ? `${parsed.urls[0]} (+${parsed.urls.length - 1}개)` : parsed.urls[0];
  const pwHint = parsed.password ? ` [🔑 암호: ${parsed.password}]` : "";

  try {
    chrome.notifications.create(
      notifyId,
      {
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: `[원격 다운로더] ${serviceLabel} 링크 감지됨`,
        message: `복사한 주소를 원격 다운로더로 보낼까요?\n${previewUrl}${pwHint}`,
        buttons: [
          { title: "📥 원격 다운로드" },
          { title: "닫기" },
        ],
        priority: 2,
        requireInteraction: true,
      },
      () => {
        void chrome.runtime.lastError;
      }
    );
  } catch (e) {}
}

async function consumePendingClipboardPrompt(notificationId) {
  let data = pendingClipboardPrompts.get(notificationId);
  pendingClipboardPrompts.delete(notificationId);
  try {
    const stored = (await getSessionValue("rdPendingClipPrompts")) || {};
    if (!data && stored[notificationId]) {
      data = stored[notificationId];
    }
    if (stored[notificationId]) {
      delete stored[notificationId];
      await setSessionValue({ rdPendingClipPrompts: stored });
    }
  } catch (e) {}
  return data || null;
}

async function tryShowToastInTab(tabId, rawText) {
  try {
    return await new Promise((resolve) => {
      chrome.tabs.sendMessage(
        tabId,
        { action: "showClipboardToast", rawText: rawText },
        (resp) => {
          if (chrome.runtime.lastError) {
            resolve({ error: chrome.runtime.lastError.message || "sendMessage failed" });
            return;
          }
          resolve(resp || { ok: false });
        }
      );
    });
  } catch (e) {
    return { error: e ? e.message : "sendMessage exception" };
  }
}

async function handleDetectedClipboardText(rawText) {
  const text = (rawText || "").trim();
  if (!text || text.length > MAX_COPY_TEXT_LENGTH) return;

  const prevSessionHandled = await getSessionValue("rdLastHandledClipboardText");
  if (text === lastHandledClipboardText || text === prevSessionHandled) {
    return;
  }

  lastHandledClipboardText = text;
  await setSessionValue({ rdLastHandledClipboardText: text });

  const prefs = await getClipboardPreferences();
  if (prefs.clipboardCopyPrompt === false) {
    return;
  }
  if (prefs.lastSentClipboardText && prefs.lastSentClipboardText === text) {
    return;
  }

  const parsed = parseDownloadableClipboardText(text);
  if (!parsed) {
    return;
  }

  let activeTab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tabs && tabs.length > 0) {
      activeTab = tabs[0];
    }
  } catch (e) {}

  let toastResult = null;
  if (activeTab && typeof activeTab.id === "number" && /^https?:\/\//i.test(activeTab.url || "")) {
    toastResult = await tryShowToastInTab(activeTab.id, text);

    if (toastResult && toastResult.error && chrome.scripting && typeof chrome.scripting.executeScript === "function") {
      try {
        await chrome.scripting.executeScript({
          target: { tabId: activeTab.id },
          files: ["content.js"],
        });
        toastResult = await tryShowToastInTab(activeTab.id, text);
      } catch (e) {}
    }

    if (toastResult && (toastResult.ok || toastResult.reason === "dashboard" || toastResult.reason === "disabled")) {
      return;
    }
  }

  await showClipboardPromptNotification(text, parsed, activeTab);
}

function queueClipboardDetection(rawText) {
  clipboardProcessChain = clipboardProcessChain
    .then(() => handleDetectedClipboardText(rawText))
    .catch(() => {});
  return clipboardProcessChain;
}

// 1. 설치/업데이트/시작 시 우클릭 컨텍스트 메뉴 및 클립보드 감시 초기화
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    void chrome.runtime.lastError;

    // 1) 링크 위에서 우클릭
    chrome.contextMenus.create({
      id: "send-link",
      title: "🔗 원격 다운로더로 이 링크 보내기",
      contexts: ["link"],
    });

    // 2) 일반 페이지 빈 곳에서 우클릭
    chrome.contextMenus.create({
      id: "send-page",
      title: "📥 현재 페이지를 원격 다운로더로 보내기",
      contexts: ["page"],
    });

    // 3) 텍스트 드래그 선택 후 우클릭 (URL / Base64 / 암호 포함 텍스트 지원)
    chrome.contextMenus.create({
      id: "send-selection",
      title: "📋 선택한 주소/Base64를 원격 다운로더로 보내기",
      contexts: ["selection"],
    });
  });

  ensureOffscreenDocument().catch(() => {});
  injectContentScriptToExistingTabs(true).catch(() => {});
});

if (chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    ensureOffscreenDocument().catch(() => {});
    injectContentScriptToExistingTabs(true).catch(() => {});
  });
}

// Service Worker 기동 시에도 오프스크린 클립보드 감시 및 기존 탭 주입 보장
ensureOffscreenDocument().catch(() => {});
injectContentScriptToExistingTabs(false).catch(() => {});

// 2. 우클릭 메뉴 클릭 이벤트 핸들러
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let targetUrl = "";
  const pageUrl = info.pageUrl || (tab && tab.url ? tab.url : "");

  if (info.menuItemId === "send-link") {
    targetUrl = info.linkUrl || "";
  } else if (info.menuItemId === "send-page") {
    targetUrl = pageUrl;
  } else if (info.menuItemId === "send-selection") {
    targetUrl = info.selectionText || "";
  }

  targetUrl = targetUrl.trim();
  if (!targetUrl) return;

  if (/^(chrome|edge|about|chrome-extension):/i.test(targetUrl)) {
    showNotification("전송 불가", "브라우저 내부 시스템 페이지는 다운로드할 수 없습니다.");
    flashActionBadge("!", "#ef4444");
    return;
  }

  const referer = /^https?:\/\//i.test(pageUrl) ? pageUrl : "";
  await sendToServer(targetUrl, referer);
});

// 2-1. 클립보드 감지 시스템 알림 버튼 클릭 핸들러
if (chrome.notifications && chrome.notifications.onButtonClicked) {
  chrome.notifications.onButtonClicked.addListener(async (notificationId, buttonIndex) => {
    if (!notificationId || !notificationId.startsWith("rd-clip-prompt-")) return;
    try {
      chrome.notifications.clear(notificationId, () => {
        void chrome.runtime.lastError;
      });
    } catch (e) {}

    const promptData = await consumePendingClipboardPrompt(notificationId);
    if (buttonIndex === 0 && promptData && promptData.url) {
      await sendToServer(promptData.url, promptData.referer || "", {
        password: promptData.password || "",
        sourceTitle: promptData.sourceTitle || "",
        sourceUrl: promptData.sourceUrl || "",
        rawClipboardText: promptData.rawClipboardText || "",
      });
    }
  });
}

if (chrome.notifications && chrome.notifications.onClosed) {
  chrome.notifications.onClosed.addListener((notificationId) => {
    if (notificationId && notificationId.startsWith("rd-clip-prompt-")) {
      consumePendingClipboardPrompt(notificationId).catch(() => {});
    }
  });
}

// 3. 서버로 URL 전송하는 공통 함수
async function sendToServer(url, referer = "", extraOptions = {}) {
  let config = {};
  try {
    if (chrome.storage && chrome.storage.local) {
      config = await new Promise((res) =>
        chrome.storage.local.get(["serverUrl", "username", "password", "capturePage"], (items) => {
          void chrome.runtime.lastError;
          res(items || {});
        })
      );
    }
    if ((!config || !config.serverUrl) && chrome.storage && chrome.storage.sync) {
      const syncItems = await new Promise((res) =>
        chrome.storage.sync.get(["serverUrl", "username", "capturePage"], (items) => {
          void chrome.runtime.lastError;
          res(items || {});
        })
      );
      config = Object.assign({}, syncItems || {}, config || {});
    }
  } catch (e) {}

  let serverUrl = (config && config.serverUrl ? config.serverUrl : "").trim();
  if (!serverUrl) {
    serverUrl = "http://127.0.0.1:7971";
  }
  if (!/^https?:\/\//i.test(serverUrl)) {
    serverUrl = "http://" + serverUrl;
  }
  serverUrl = serverUrl.replace(/\/+$/, "");

  const urlsToTry = [serverUrl];
  if (serverUrl.includes("localhost")) {
    urlsToTry.push(serverUrl.replace("localhost", "127.0.0.1"));
  } else if (serverUrl.includes("127.0.0.1")) {
    urlsToTry.push(serverUrl.replace("127.0.0.1", "localhost"));
  }

  const payload = { url: url };
  if (referer) {
    payload.referer = referer;
  }
  if (extraOptions.password) {
    payload.password = extraOptions.password;
    payload.terabox_password = extraOptions.password;
  }
  if (extraOptions.subfolder) {
    payload.subfolder = extraOptions.subfolder;
  }
  if (extraOptions.sourceTitle) {
    payload.source_title = extraOptions.sourceTitle;
  }
  if (extraOptions.sourceUrl) {
    payload.source_url = extraOptions.sourceUrl;
  }
  if (config && typeof config.capturePage === "boolean" && config.capturePage) {
    payload.capture_page = true;
  }

  for (const targetUrl of urlsToTry) {
    try {
      const res = await fetch(`${targetUrl}/api/jobs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Auth-User": config.username || "admin",
          "X-Auth-Password": config.password || "",
        },
        body: JSON.stringify(payload),
        signal: (typeof AbortSignal !== "undefined" && AbortSignal.timeout) ? AbortSignal.timeout(8000) : undefined,
      });

      const data = await res.json().catch(() => ({}));

      if (res.status === 401) {
        flashActionBadge("!", "#ef4444");
        showNotification(
          "인증 실패 (401)",
          "비밀번호가 올바르지 않습니다. 확장 프로그램 아이콘을 클릭하여 비밀번호를 설정하세요."
        );
        return { ok: false, error: "인증 실패 (401)" };
      }

      if (res.ok && data.success) {
        const count = data.added_count || 1;
        if (extraOptions.rawClipboardText) {
          const sentText = String(extraOptions.rawClipboardText).trim();
          lastHandledClipboardText = sentText;
          setSessionValue({ rdLastHandledClipboardText: sentText }).catch(() => {});
          if (chrome.storage && chrome.storage.local) {
            try {
              chrome.storage.local.set({ lastSentClipboardText: sentText }, () => {
                void chrome.runtime.lastError;
              });
            } catch (e) {}
          }
          try {
            chrome.runtime.sendMessage({ action: "syncClipboardBaseline", text: sentText }, () => {
              void chrome.runtime.lastError;
            });
          } catch (e) {}
        }
        flashActionBadge("✓", "#10b981");
        showNotification("다운로드 추가 완료", `${count}개의 다운로드 작업이 원격 PC로 전송되었습니다!`);
        return { ok: true, count: count };
      } else {
        const errMsg = data.error || "서버에서 오류를 반환했습니다.";
        flashActionBadge("!", "#ef4444");
        showNotification("전송 실패", errMsg);
        return { ok: false, error: errMsg };
      }
    } catch (err) {
      // 다음 URL(localhost <-> 127.0.0.1)로 재시도
    }
  }

  flashActionBadge("!", "#ef4444");
  showNotification(
    "서버 연결 실패",
    `서버(${serverUrl})에 접속할 수 없습니다. PC 서버가 켜져 있는지 확인하세요.`
  );
  return { ok: false, error: "서버 연결 실패" };
}

// 4. 확장 아이콘 배지 피드백
function flashActionBadge(text, color) {
  try {
    if (!chrome.action) return;
    chrome.action.setBadgeBackgroundColor({ color: color });
    chrome.action.setBadgeText({ text: text });
    setTimeout(() => {
      chrome.action.setBadgeText({ text: "" });
    }, 2500);
  } catch (e) {}
}

// 5. 알림 헬퍼 함수
function showNotification(title, message) {
  try {
    chrome.notifications.create(
      `rd-notify-${Date.now()}`,
      {
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: `[원격 다운로더] ${title}`,
        message: message,
      },
      () => {
        void chrome.runtime.lastError;
      }
    );
  } catch (e) {}
}

// 6. 팝업 및 콘텐츠/오프스크린 스크립트 메시지 수신 리스너
chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (!request) return false;

  if (request.action === "sendUrl") {
    sendToServer(request.url, request.referer || "", {
      password: request.password || "",
      subfolder: request.subfolder || "",
      sourceTitle: request.sourceTitle || "",
      sourceUrl: request.sourceUrl || "",
      rawClipboardText: request.rawClipboardText || "",
    })
      .then((result) => sendResponse(Object.assign({ status: "done" }, result || {})))
      .catch((err) => sendResponse({ ok: false, error: err ? err.message : "unknown" }));
    return true;
  }

  if (request.action === "checkClipboardNow") {
    (async () => {
      const state = await ensureOffscreenDocument();
      if (!state.open) {
        return { ok: false, reason: "no_offscreen" };
      }
      const resp = await chrome.runtime
        .sendMessage({ action: "offscreenCheckClipboard" })
        .catch(() => null);
      if (resp && resp.text) {
        await queueClipboardDetection(resp.text);
        return { ok: true, found: true };
      }
      return { ok: true, found: false };
    })()
      .then((res) => sendResponse(res))
      .catch((err) => sendResponse({ ok: false, error: err ? err.message : "unknown" }));
    return true;
  }

  if (request.action === "clipboardChanged") {
    queueClipboardDetection(request.text || "")
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }

  return false;
});
