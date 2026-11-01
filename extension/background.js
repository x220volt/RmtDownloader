/**
 * 원격 다운로더 (Remote Downloader) Chrome Extension Background Service Worker
 * 우클릭 컨텍스트 메뉴 및 백그라운드 전송 처리
 */

// 1. 설치/업데이트 시 우클릭 컨텍스트 메뉴 생성
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
});

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

// 3. 서버로 URL 전송하는 공통 함수
async function sendToServer(url, referer = "") {
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
        return;
      }

      if (res.ok && data.success) {
        const count = data.added_count || 1;
        flashActionBadge("✓", "#10b981");
        showNotification("다운로드 추가 완료", `${count}개의 다운로드 작업이 원격 PC로 전송되었습니다!`);
        return;
      } else {
        flashActionBadge("!", "#ef4444");
        showNotification("전송 실패", data.error || "서버에서 오류를 반환했습니다.");
        return;
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

// 6. 팝업에서 메시지를 보낼 때 수신 리스너
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request && request.action === "sendUrl") {
    sendToServer(request.url, request.referer || "").then(() => sendResponse({ status: "done" }));
    return true; // 비동기 응답 유지
  }
});
