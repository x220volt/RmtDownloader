/**
 * 원격 다운로더 (Remote Downloader) Offscreen Clipboard Monitor
 * Service Worker 백그라운드에서 클립보드 변경(우클릭 링크 복사, 웹페이지 복사 버튼,
 * 주소창 복사 등)을 감지하여 background.js로 전달합니다.
 */
(() => {
  "use strict";

  const POLL_INTERVAL_MS = 800;
  const MAX_CLIPBOARD_LENGTH = 8000;
  let lastClipboardText = "";
  let baselineInitialized = false;

  function readClipboardText() {
    const area = document.getElementById("clipboardArea");
    if (!area) return "";
    try {
      area.value = "";
      area.focus();
      const pasted = document.execCommand("paste");
      const text = pasted ? (area.value || "").trim() : "";
      area.value = "";
      if (text.length > MAX_CLIPBOARD_LENGTH) {
        return "";
      }
      return text;
    } catch (e) {
      return "";
    }
  }

  function pollClipboard(forceReport = false) {
    const text = readClipboardText();

    if (!baselineInitialized) {
      baselineInitialized = true;
      lastClipboardText = text;
      if (forceReport && text) {
        return text;
      }
      return "";
    }

    if (!text) {
      lastClipboardText = "";
      return "";
    }

    if (text === lastClipboardText) {
      return forceReport ? text : "";
    }

    lastClipboardText = text;
    try {
      chrome.runtime.sendMessage(
        { action: "clipboardChanged", text: text },
        () => {
          void chrome.runtime.lastError;
        }
      );
    } catch (e) {}
    return text;
  }

  // 시작 시 기존 클립보드 내용을 기준값으로 기록하여 불필요한 초기 팝업 방지
  pollClipboard(false);

  setInterval(() => {
    pollClipboard(false);
  }, POLL_INTERVAL_MS);

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (!request) return false;

    if (request.action === "offscreenCheckClipboard") {
      const currentText = pollClipboard(true);
      sendResponse({ ok: true, text: currentText });
      return false;
    }

    if (request.action === "syncClipboardBaseline") {
      const nextText = typeof request.text === "string" ? request.text.trim() : "";
      baselineInitialized = true;
      lastClipboardText = nextText;
      sendResponse({ ok: true });
      return false;
    }

    return false;
  });
})();
