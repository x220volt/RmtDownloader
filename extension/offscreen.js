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
    // RDP(rdpclip.exe) 환경 등에서 UI 스레드 블로킹 및 포커스 탈취를 유발하는
    // 주기적 execCommand("paste") 폴링을 비활성화했습니다.
    return "";
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
