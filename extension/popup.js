/**
 * 원격 다운로더 크롬 확장 팝업 스크립트
 */

// UI 요소
const statusBadge = document.getElementById("statusBadge");
const statusText = document.getElementById("statusText");

const totalSpeedText = document.getElementById("totalSpeedText");
const refreshBtn = document.getElementById("refreshBtn");

const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const closeSettingsModal = document.getElementById("closeSettingsModal");

const sendCurrentTabBtn = document.getElementById("sendCurrentTabBtn");
const scanPageBtn = document.getElementById("scanPageBtn");
const scanSection = document.getElementById("scanSection");
const scanCountBadge = document.getElementById("scanCountBadge");
const scanSelectAll = document.getElementById("scanSelectAll");
const closeScanBtn = document.getElementById("closeScanBtn");
const scanListContainer = document.getElementById("scanListContainer");
const scanSubfolderInput = document.getElementById("scanSubfolderInput");
const sendScannedBtn = document.getElementById("sendScannedBtn");
const selectedScanCount = document.getElementById("selectedScanCount");

let scannedLinks = [];

const tabBtns = document.querySelectorAll(".tab-btn");
const tabCountActive = document.getElementById("tabCountActive");
const tabCountCompleted = document.getElementById("tabCountCompleted");
const tabCountFailed = document.getElementById("tabCountFailed");
const tabCountAll = document.getElementById("tabCountAll");
const tabActionsBar = document.getElementById("tabActionsBar");
const btnClearCompleted = document.getElementById("btnClearCompleted");
const btnClearFailed = document.getElementById("btnClearFailed");

const jobsList = document.getElementById("jobsList");
const jobsEmpty = document.getElementById("jobsEmpty");
const emptyMsg = document.getElementById("emptyMsg");

const toggleDirectAdd = document.getElementById("toggleDirectAdd");
const directAddContent = document.getElementById("directAddContent");
const directAddArrow = document.getElementById("directAddArrow");
const manualUrlInput = document.getElementById("manualUrlInput");
const manualSubfolderInput = document.getElementById("manualSubfolderInput");
const manualPasswordInput = document.getElementById("manualPasswordInput");
const sendManualBtn = document.getElementById("sendManualBtn");

const settingsContent = document.getElementById("settingsContent");

const serverUrlInput = document.getElementById("serverUrlInput");
const usernameInput = document.getElementById("usernameInput");
const passwordInput = document.getElementById("passwordInput");
const testConnBtn = document.getElementById("testConnBtn");
const saveSettingsBtn = document.getElementById("saveSettingsBtn");
const testResult = document.getElementById("testResult");
const openWebDashboardLink = document.getElementById("openWebDashboardLink");
const popupToast = document.getElementById("popupToast");

// 깃허브 최신 릴리즈 업데이트 요소
const updateBanner = document.getElementById("updateBanner");
const updateVersionText = document.getElementById("updateVersionText");
const updateNotesLink = document.getElementById("updateNotesLink");
const applyUpdateBtn = document.getElementById("applyUpdateBtn");

const popupPagination = document.getElementById("popupPagination");
const popupPrevBtn = document.getElementById("popupPrevBtn");
const popupNextBtn = document.getElementById("popupNextBtn");
const popupPageText = document.getElementById("popupPageText");

// 상태 관리
let currentTab = "active";
let cachedJobs = [];
const POPUP_PAGE_SIZE = 10;
let popupCurrentPage = 1;
let baseDownloadDir = "";
let pollTimer = null;
let isFetching = false;
let toastTimer = null;
let latestReleaseData = null;
const UPDATE_CHECK_CACHE_MS = 20 * 60 * 1000; // 20분 캐시

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  setupEventListeners();

  // 최신 릴리즈 확인 (백그라운드에서 비동기 체크)
  checkForExtensionUpdate();

  const isConnected = await checkConnection();
  if (isConnected) {
    await fetchJobs();
    startPolling();
  }
});

window.addEventListener("unload", () => {
  stopPolling();
});

function showPopupToast(message, isError = false) {
  if (!popupToast) return;
  if (toastTimer) clearTimeout(toastTimer);
  popupToast.textContent = message;
  popupToast.className = `popup-toast show${isError ? " error" : ""}`;
  toastTimer = setTimeout(() => {
    popupToast.classList.remove("show");
  }, 2600);
}

function setupEventListeners() {
  // 탭 전환
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentTab = btn.getAttribute("data-tab") || "active";
      popupCurrentPage = 1;
      renderJobs();
    });
  });

  // 완료 / 실패 목록 비우기 버튼
  if (btnClearCompleted) {
    btnClearCompleted.addEventListener("click", async () => {
      const serverUrl = normalizeServerUrl(serverUrlInput.value);
      if (!serverUrl) return;
      const completedCount = cachedJobs.filter((j) => j.status === "completed").length;
      if (completedCount === 0) return;
      try {
        const res = await fetch(`${serverUrl}/api/jobs/clear`, {
          method: "POST",
          credentials: "omit",
          headers: getAuthHeaders(),
          body: JSON.stringify({ filter: "completed" }),
        });
        if (res.ok) {
          showPopupToast(`완료된 작업 ${completedCount}개를 비웠습니다.`);
          await fetchJobs();
        }
      } catch (err) {
        showPopupToast("완료 목록 비우기 실패", true);
      }
    });
  }

  if (btnClearFailed) {
    btnClearFailed.addEventListener("click", async () => {
      const serverUrl = normalizeServerUrl(serverUrlInput.value);
      if (!serverUrl) return;
      const failedCount = cachedJobs.filter((j) => ["failed", "cancelled"].includes(j.status)).length;
      if (failedCount === 0) return;
      try {
        const res = await fetch(`${serverUrl}/api/jobs/clear`, {
          method: "POST",
          credentials: "omit",
          headers: getAuthHeaders(),
          body: JSON.stringify({ filter: "failed" }),
        });
        if (res.ok) {
          showPopupToast(`실패/취소 작업 ${failedCount}개를 비웠습니다.`);
          await fetchJobs();
        }
      } catch (err) {
        showPopupToast("실패 목록 비우기 실패", true);
      }
    });
  }


  // 새로고침 버튼
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.style.transform = "rotate(360deg)";
      refreshBtn.style.transition = "transform 0.5s ease";
      setTimeout(() => {
        refreshBtn.style.transform = "";
        refreshBtn.style.transition = "";
      }, 500);
      await fetchJobs();
    });
  }

  // 현재 탭 전송 버튼
  if (sendCurrentTabBtn) {
    sendCurrentTabBtn.addEventListener("click", sendCurrentTab);
  }

  // 현재 페이지 링크 스캔 버튼
  if (scanPageBtn) {
    scanPageBtn.addEventListener("click", scanCurrentPage);
  }

  // 스캔 창 닫기 버튼
  if (closeScanBtn) {
    closeScanBtn.addEventListener("click", () => {
      if (scanSection) scanSection.classList.add("hidden");
    });
  }

  // 스캔 전체 선택/해제 토글
  if (scanSelectAll) {
    scanSelectAll.addEventListener("change", (e) => {
      const isChecked = e.target.checked;
      if (scanListContainer) {
        const checkboxes = scanListContainer.querySelectorAll("input[type='checkbox']");
        checkboxes.forEach((cb) => {
          cb.checked = isChecked;
        });
        updateSelectedScanCount();
      }
    });
  }

  // 선택한 스캔 항목 전송 버튼
  if (sendScannedBtn) {
    sendScannedBtn.addEventListener("click", sendSelectedScannedLinks);
  }

  // 직접 추가 아코디언 토글
  if (toggleDirectAdd) {
    toggleDirectAdd.addEventListener("click", () => {
      const isShowing = directAddContent.classList.toggle("show");
      directAddArrow.textContent = isShowing ? "▲" : "▼";
    });
  }

  // 직접 입력 전송 버튼 및 Ctrl+Enter 단축키
  if (sendManualBtn) {
    sendManualBtn.addEventListener("click", sendManualUrl);
  }
  if (manualUrlInput) {
    manualUrlInput.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        sendManualUrl();
      }
    });
  }

  // 드래그 앤 드롭 지원
  if (manualUrlInput) {
    manualUrlInput.addEventListener("dragover", (e) => {
      e.preventDefault();
      manualUrlInput.style.borderColor = "var(--border-focus)";
    });
    manualUrlInput.addEventListener("dragleave", (e) => {
      e.preventDefault();
      manualUrlInput.style.borderColor = "";
    });
    manualUrlInput.addEventListener("drop", (e) => {
      e.preventDefault();
      manualUrlInput.style.borderColor = "";

      let targetText = "";
      const html = e.dataTransfer.getData("text/html");
      if (html) {
        try {
          const parser = new DOMParser();
          const doc = parser.parseFromString(html, "text/html");
          const a = doc.querySelector("a[href]");
          if (a) {
            const href = a.getAttribute("href");
            const linkText = a.textContent.trim();
            if (linkText && !linkText.startsWith("http://") && !linkText.startsWith("https://") && linkText.includes(".")) {
              targetText = `${href} | ${linkText}`;
            } else if (href) {
              targetText = href;
            }
          }
        } catch (err) {}
      }

      if (!targetText) {
        targetText = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "").trim();
      }

      if (targetText) {
        if (!directAddContent.classList.contains("show")) {
          directAddContent.classList.add("show");
          directAddArrow.textContent = "▲";
        }
        manualUrlInput.value = (manualUrlInput.value ? manualUrlInput.value + "\n" : "") + targetText;
      }
    });
  }

  // 설정 모달 열기 및 닫기
  if (settingsBtn) {
    settingsBtn.addEventListener("click", () => {
      if (settingsModal) settingsModal.classList.add("show");
    });
  }
  if (closeSettingsModal) {
    closeSettingsModal.addEventListener("click", () => {
      if (settingsModal) settingsModal.classList.remove("show");
    });
  }
  if (settingsModal) {
    settingsModal.addEventListener("click", (e) => {
      if (e.target === settingsModal) {
        settingsModal.classList.remove("show");
      }
    });
  }

  // 설정 입력창에서 Enter 키로 즉시 저장
  [serverUrlInput, usernameInput, passwordInput].forEach((input) => {
    if (input) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          saveSettings();
        }
      });
    }
  });

  // 연결 테스트 버튼
  if (testConnBtn) {
    testConnBtn.addEventListener("click", runConnectionTest);
  }

  // 설정 저장 버튼
  if (saveSettingsBtn) {
    saveSettingsBtn.addEventListener("click", saveSettings);
  }

  // 팝업 페이징 버튼
  if (popupPrevBtn) {
    popupPrevBtn.addEventListener("click", () => {
      if (popupCurrentPage > 1) {
        popupCurrentPage--;
        renderJobs();
      }
    });
  }
  if (popupNextBtn) {
    popupNextBtn.addEventListener("click", () => {
      const filtered = getSortedFilteredJobs();
      const totalPages = Math.ceil(filtered.length / POPUP_PAGE_SIZE) || 1;
      if (popupCurrentPage < totalPages) {
        popupCurrentPage++;
        renderJobs();
      }
    });
  }

  // 웹 대시보드 열기
  if (openWebDashboardLink) {
    openWebDashboardLink.addEventListener("click", (e) => {
      e.preventDefault();
      const url = normalizeServerUrl(serverUrlInput.value) || "http://127.0.0.1:7971";
      chrome.tabs.create({ url: url });
    });
  }

  // 깃허브 최신 릴리즈 원클릭 업데이트 버튼
  if (applyUpdateBtn) {
    applyUpdateBtn.addEventListener("click", applyExtensionUpdate);
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    await fetchJobs();
  }, 1500);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function normalizeServerUrl(url) {
  let u = (url || "").trim();
  if (!u) return "";
  if (!/^https?:\/\//i.test(u)) {
    u = "http://" + u;
  }
  return u.replace(/\/+$/, "");
}

async function getStorageConfig() {
  return new Promise((resolve) => {
    try {
      if (chrome.storage && chrome.storage.local) {
        chrome.storage.local.get(["serverUrl", "username", "password", "capturePage"], (localItems) => {
          if (!chrome.runtime.lastError && localItems && (localItems.serverUrl || localItems.password || typeof localItems.capturePage === "boolean")) {
            return resolve(localItems);
          }
          if (chrome.storage.sync) {
            chrome.storage.sync.get(["serverUrl", "username", "password", "capturePage"], (syncItems) => {
              resolve(Object.assign({}, syncItems || {}, localItems || {}));
            });
          } else {
            resolve(localItems || {});
          }
        });
      } else {
        resolve({});
      }
    } catch (e) {
      resolve({});
    }
  });
}

async function setStorageConfig(data) {
  try {
    if (chrome.storage && chrome.storage.local) {
      chrome.storage.local.set(data);
    }
  } catch (e) {}
  try {
    if (chrome.storage && chrome.storage.sync) {
      chrome.storage.sync.set(data);
    }
  } catch (e) {}
}

async function loadSettings() {
  const config = await getStorageConfig();

  serverUrlInput.value = config.serverUrl || "http://127.0.0.1:7971";
  usernameInput.value = config.username || "admin";
  passwordInput.value = config.password || "";

}

async function saveSettings() {
  const serverUrl = normalizeServerUrl(serverUrlInput.value) || "http://127.0.0.1:7971";
  const username = usernameInput.value.trim() || "admin";
  const password = passwordInput.value.trim();

  serverUrlInput.value = serverUrl;
  usernameInput.value = username;

  await setStorageConfig({
    serverUrl: serverUrl,
    username: username,
    password: password,
  });

  showTestResult(true, "설정이 저장되었습니다. 서버 연결을 확인하는 중...");
  const isConnected = await checkConnection();
  if (isConnected) {
    showTestResult(true, "✅ 연결 성공! 설정이 안전하게 저장되었습니다.");
    showPopupToast("설정이 저장되고 서버에 연결되었습니다.");
    await fetchJobs();
    startPolling();
  }
}

function getAuthHeaders(includeContentType = true) {
  const headers = {
    "X-Auth-User": usernameInput.value.trim() || "admin",
    "X-Auth-Password": passwordInput.value.trim(),
  };
  if (includeContentType) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

async function checkConnection() {
  let serverUrl = normalizeServerUrl(serverUrlInput.value);
  const username = usernameInput.value.trim() || "admin";
  const password = passwordInput.value.trim();

  statusBadge.className = "status-badge";
  statusText.textContent = "확인 중...";

  if (!serverUrl) {
    serverUrl = "http://127.0.0.1:7971";
    serverUrlInput.value = serverUrl;
  }

  const urlsToTry = [serverUrl];
  if (serverUrl.includes("localhost")) {
    urlsToTry.push(serverUrl.replace("localhost", "127.0.0.1"));
  } else if (serverUrl.includes("127.0.0.1")) {
    urlsToTry.push(serverUrl.replace("127.0.0.1", "localhost"));
  }

  let lastStatus = 0;
  for (const testUrl of urlsToTry) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 3500);

      const res = await fetch(`${testUrl}/api/auth/test`, {
        method: "POST",
        credentials: "omit",
        headers: {
          "Content-Type": "application/json",
          "X-Auth-User": username,
          "X-Auth-Password": password,
        },
        body: JSON.stringify({}),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      lastStatus = res.status;

      if (res.ok) {
        if (testUrl !== serverUrl) {
          serverUrlInput.value = testUrl;
          setStorageConfig({ serverUrl: testUrl });
        }
        const data = await res.json().catch(() => ({}));
        if (data.download_dir) {
          baseDownloadDir = data.download_dir;
        }

        statusBadge.className = "status-badge online";
        statusText.textContent = "연결됨";
        return true;
      } else if (res.status === 401) {
        statusBadge.className = "status-badge offline";
        statusText.textContent = "비밀번호 불일치";
        if (settingsModal) settingsModal.classList.add("show");
        showTestResult(false, "❌ 비밀번호 불일치: 비밀번호를 입력해주세요 (초기 기본값: password1234)");
        return false;
      }
    } catch (err) {
      // 다음 URL로 시도
    }
  }

  statusBadge.className = "status-badge offline";
  if (lastStatus === 401) {
    statusText.textContent = "비밀번호 불일치";
    if (settingsModal) settingsModal.classList.add("show");
    showTestResult(false, "❌ 비밀번호 불일치: 비밀번호를 확인해주세요 (초기 기본값: password1234)");
  } else {
    statusText.textContent = "연결 안됨";
    if (settingsModal) settingsModal.classList.add("show");
    showTestResult(false, `❌ 접속 불가: ${serverUrl} 에 연결할 수 없습니다. PC 서버가 켜져 있는지 확인하세요.`);
  }
  return false;
}

async function runConnectionTest() {
  testResult.style.display = "block";
  testResult.className = "test-result";
  testResult.textContent = "서버에 연결하는 중...";

  let serverUrl = normalizeServerUrl(serverUrlInput.value);
  const username = usernameInput.value.trim() || "admin";
  const password = passwordInput.value.trim();

  if (!serverUrl) {
    serverUrl = "http://127.0.0.1:7971";
    serverUrlInput.value = serverUrl;
  }
  serverUrlInput.value = serverUrl;

  const urlsToTry = [serverUrl];
  if (serverUrl.includes("localhost")) {
    urlsToTry.push(serverUrl.replace("localhost", "127.0.0.1"));
  } else if (serverUrl.includes("127.0.0.1")) {
    urlsToTry.push(serverUrl.replace("127.0.0.1", "localhost"));
  }

  let errorDetails = "";

  for (const testUrl of urlsToTry) {
    try {
      const res = await fetch(`${testUrl}/api/auth/test`, {
        method: "POST",
        credentials: "omit",
        headers: {
          "Content-Type": "application/json",
          "X-Auth-User": username,
          "X-Auth-Password": password,
        },
        body: JSON.stringify({}),
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data.success) {
        if (testUrl !== serverUrl) {
          serverUrlInput.value = testUrl;
        }
        await setStorageConfig({
          serverUrl: testUrl,
          username: username,
          password: password,
        });

        showTestResult(true, `✅ 연결 및 인증 성공! (${data.username} 계정으로 설정이 저장되었습니다)`);
        showPopupToast("설정이 저장되었습니다.");
        setTimeout(() => { if (settingsModal) settingsModal.classList.remove("show"); }, 600);
        statusBadge.className = "status-badge online";
        statusText.textContent = "연결됨";
        if (data.download_dir) {
          baseDownloadDir = data.download_dir;
        }

        await fetchJobs();
        startPolling();
        return;
      } else if (res.status === 401) {
        showTestResult(false, "❌ 인증 실패(401): 비밀번호가 틀립니다. (초기 기본값: password1234)");
        statusBadge.className = "status-badge offline";
        statusText.textContent = "비밀번호 불일치";
        return;
      } else {
        errorDetails = `응답 코드 ${res.status}`;
      }
    } catch (err) {
      errorDetails = err.message || "연결 거부";
    }
  }

  showTestResult(false, `❌ 접속 불가(${errorDetails}): 서버(${serverUrl})에 접속할 수 없습니다. RemoteDownloader가 실행 중인지 확인하세요.`);
  statusBadge.className = "status-badge offline";
  statusText.textContent = "연결 안됨";
}

function showTestResult(isSuccess, message) {
  testResult.style.display = "block";
  testResult.className = `test-result ${isSuccess ? "success" : "error"}`;
  testResult.textContent = message;
}

// ==========================================
// 다운로드 작업 목록 조회 및 렌더링
// ==========================================

async function fetchJobs() {
  if (isFetching) return;
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  if (!serverUrl) return;

  isFetching = true;
  try {
    const res = await fetch(`${serverUrl}/api/jobs`, {
      method: "GET",
      credentials: "omit",
      headers: getAuthHeaders(false),
    });

    if (res.ok) {
      const data = await res.json();
      cachedJobs = data.jobs || [];
      const stats = data.stats || {};

      if (totalSpeedText) {
        totalSpeedText.textContent = stats.total_speed_str || "0 B/s";
      }

      const activeCount = cachedJobs.filter((j) =>
        ["queued", "resolving", "downloading", "paused"].includes(j.status)
      ).length;
      const completedCount = cachedJobs.filter((j) => j.status === "completed").length;
      const failedCount = cachedJobs.filter((j) => ["failed", "cancelled"].includes(j.status)).length;
      const allCount = cachedJobs.length;

      if (tabCountActive) tabCountActive.textContent = activeCount;
      if (tabCountCompleted) tabCountCompleted.textContent = completedCount;
      if (tabCountFailed) tabCountFailed.textContent = failedCount;
      if (tabCountAll) tabCountAll.textContent = allCount;

      if (btnClearCompleted) {
        btnClearCompleted.style.display = completedCount > 0 ? "inline-block" : "none";
      }
      if (btnClearFailed) {
        btnClearFailed.style.display = failedCount > 0 ? "inline-block" : "none";
      }
      if (tabActionsBar) {
        tabActionsBar.classList.toggle("show", completedCount > 0 || failedCount > 0);
      }

      renderJobs();
    }
  } catch (err) {
    console.debug("작업 목록 가져오기 오류:", err);
  } finally {
    isFetching = false;
  }
}

function getSortedFilteredJobs() {
  let filtered = [];
  const statusWeight = { downloading: 1, resolving: 2, queued: 3, paused: 4, completed: 5, failed: 6, cancelled: 7 };

  if (currentTab === "active") {
    filtered = cachedJobs.filter((j) =>
      ["queued", "resolving", "downloading", "paused"].includes(j.status)
    );
    filtered.sort((a, b) => {
      const wa = statusWeight[a.status] || 99;
      const wb = statusWeight[b.status] || 99;
      if (wa !== wb) return wa - wb;
      if (a.status === "downloading" && b.status === "downloading") {
        return (b.progress || 0) - (a.progress || 0);
      }
      return (a.created_at || "").localeCompare(b.created_at || "");
    });
  } else if (currentTab === "completed") {
    filtered = cachedJobs.filter((j) => j.status === "completed");
    filtered.sort((a, b) => {
      const timeA = a.completed_at || a.created_at || "";
      const timeB = b.completed_at || b.created_at || "";
      return timeB.localeCompare(timeA);
    });
  } else if (currentTab === "failed") {
    filtered = cachedJobs.filter((j) => ["failed", "cancelled"].includes(j.status));
    filtered.sort((a, b) => {
      const timeA = a.completed_at || a.created_at || "";
      const timeB = b.completed_at || b.created_at || "";
      return timeB.localeCompare(timeA);
    });
  } else {
    filtered = cachedJobs.slice().sort((a, b) => {
      const wa = statusWeight[a.status] || 99;
      const wb = statusWeight[b.status] || 99;
      const isAActive = wa <= 4;
      const isBActive = wb <= 4;
      if (isAActive && isBActive) {
        if (wa !== wb) return wa - wb;
        return (a.created_at || "").localeCompare(b.created_at || "");
      }
      if (isAActive !== isBActive) return isAActive ? -1 : 1;
      const timeA = a.completed_at || a.created_at || "";
      const timeB = b.completed_at || b.created_at || "";
      return timeB.localeCompare(timeA);
    });
  }
  return filtered;
}

function renderJobs() {
  const filtered = getSortedFilteredJobs();
  const totalItems = filtered.length;
  const totalPages = Math.ceil(totalItems / POPUP_PAGE_SIZE) || 1;

  if (popupCurrentPage > totalPages) {
    popupCurrentPage = totalPages;
  }
  if (popupCurrentPage < 1) {
    popupCurrentPage = 1;
  }

  if (totalItems === 0) {
    jobsList.querySelectorAll(".job-card").forEach((c) => c.remove());
    jobsEmpty.style.display = "flex";
    if (popupPagination) popupPagination.style.display = "none";
    if (currentTab === "active") {
      emptyMsg.textContent = "진행 또는 대기 중인 다운로드가 없습니다.";
    } else if (currentTab === "completed") {
      emptyMsg.textContent = "완료된 다운로드가 없습니다.";
    } else if (currentTab === "failed") {
      emptyMsg.textContent = "실패 또는 취소된 다운로드가 없습니다.";
    } else {
      emptyMsg.textContent = "등록된 다운로드 작업이 없습니다.";
    }
    return;
  }

  jobsEmpty.style.display = "none";

  if (popupPagination) {
    if (totalItems <= POPUP_PAGE_SIZE) {
      popupPagination.style.display = "none";
    } else {
      popupPagination.style.display = "flex";
      if (popupPageText) {
        popupPageText.textContent = `${popupCurrentPage} / ${totalPages} (총 ${totalItems}개)`;
      }
      if (popupPrevBtn) popupPrevBtn.disabled = popupCurrentPage <= 1;
      if (popupNextBtn) popupNextBtn.disabled = popupCurrentPage >= totalPages;
    }
  }

  const startIndex = (popupCurrentPage - 1) * POPUP_PAGE_SIZE;
  const endIndex = Math.min(startIndex + POPUP_PAGE_SIZE, totalItems);
  const pageItems = filtered.slice(startIndex, endIndex);

  // 현재 페이지에 없는 기존 카드 제거
  const targetIds = new Set(pageItems.map((j) => `job-${j.id}`));
  jobsList.querySelectorAll(".job-card").forEach((c) => {
    if (!targetIds.has(c.id)) {
      c.remove();
    }
  });

  // 기존 카드는 제자리(in-place) 업데이트하여 폴링 중 버튼 클릭 유실 및 깜빡임 방지
  pageItems.forEach((job, idx) => {
    const existing = document.getElementById(`job-${job.id}`);
    if (existing && existing.dataset.status === job.status) {
      updateJobCardInPlace(existing, job);
      const currentAtIdx = jobsList.children[idx + 1]; // +1 because jobsEmpty is first child
      if (currentAtIdx !== existing) {
        jobsList.insertBefore(existing, currentAtIdx || null);
      }
    } else {
      const newCard = createJobCard(job);
      if (existing) {
        jobsList.replaceChild(newCard, existing);
      } else {
        const currentAtIdx = jobsList.children[idx + 1];
        jobsList.insertBefore(newCard, currentAtIdx || null);
      }
    }
  });
}

function extractSubfolderName(job) {
  if (job.download_dir && baseDownloadDir) {
    const normJobDir = job.download_dir.replace(/\\/g, "/").replace(/\/+$/, "");
    const normBaseDir = baseDownloadDir.replace(/\\/g, "/").replace(/\/+$/, "");
    if (normJobDir.startsWith(normBaseDir) && normJobDir.length > normBaseDir.length) {
      return normJobDir.slice(normBaseDir.length).replace(/^\/+/, "");
    }
  }
  return "";
}

function updateJobCardInPlace(card, job) {
  const isResolving = job.status === "resolving";
  const isCloud = Boolean(job.is_cloud);
  const progressPercent = job.status === "completed" ? 100 : Math.min(100, Math.max(0, job.progress || 0));
  const statusInfo = getStatusInfo(job.status, isCloud);
  const safeFilename = job.filename || "파일";

  const titleEl = card.querySelector(".job-title");
  if (titleEl && titleEl.textContent !== safeFilename) {
    titleEl.textContent = safeFilename;
    titleEl.title = safeFilename;
  }

  const fillEl = card.querySelector(".job-progress-fill");
  if (fillEl) {
    fillEl.className = `job-progress-fill ${isResolving ? "indeterminate" : ""} status-${job.status}`;
    fillEl.style.width = `${progressPercent}%`;
  }

  const statusPill = card.querySelector(".job-status-pill");
  if (statusPill) {
    statusPill.className = `job-status-pill status-${job.status}`;
    statusPill.textContent = statusInfo.text;
  }

  const bytesEl = card.querySelector(".job-bytes");
  if (bytesEl) {
    bytesEl.textContent = isCloud
      ? (job.total_bytes > 0 ? job.total_str : "클라우드 보관")
      : `${job.downloaded_str || "0 B"} / ${job.total_str || "알 수 없음"}`;
  }

  const metaRight = card.querySelector(".job-meta-right");
  if (metaRight) {
    metaRight.innerHTML = renderMetaRightHtml(job);
  }
}

function renderMetaRightHtml(job) {
  if (job.status === "downloading") {
    let html = "";
    if (job.speed_str) html += `<span>${escapeHtml(job.speed_str)}</span>`;
    if (job.eta_str) html += `<span style="color:var(--text-sub);">(${escapeHtml(job.eta_str)})</span>`;
    return html;
  }
  if (job.status === "completed") {
    return `<span style="color:var(--status-green);">100%</span>`;
  }
  return "";
}

function getDomainFromUrl(url) {
  try {
    const u = new URL(url);
    return u.hostname;
  } catch (e) {
    return url || "";
  }
}

function createJobCard(job) {
  const card = document.createElement("div");
  card.className = "job-card";
  card.id = `job-${job.id}`;
  card.dataset.status = job.status;

  const isCloud = Boolean(job.is_cloud);
  const serviceTagClass = getServiceTagClass(job.service_name, isCloud);
  const statusInfo = getStatusInfo(job.status, isCloud);
  const isResolving = job.status === "resolving";
  const progressPercent = job.status === "completed" ? 100 : Math.min(100, Math.max(0, job.progress || 0));
  const subfolderName = extractSubfolderName(job);
  const safeFilename = escapeHtml(job.filename || "파일");
  const bytesText = isCloud
    ? (job.total_bytes > 0 ? escapeHtml(job.total_str) : "클라우드 보관")
    : `${escapeHtml(job.downloaded_str || "0 B")} / ${escapeHtml(job.total_str || "알 수 없음")}`;

  const displaySource = job.source_title || (job.source_url ? getDomainFromUrl(job.source_url) : "");
  const sourceHref = job.source_url || job.referer || "";

  card.innerHTML = `
    <div class="job-header">
      <div class="job-info-left">
        <span class="job-service-tag ${serviceTagClass}">${escapeHtml(job.service_name || "Direct")}</span>
        ${subfolderName ? `<span class="job-folder-tag" title="저장 폴더: ${escapeHtml(subfolderName)}">📁 ${escapeHtml(subfolderName)}</span>` : ""}
        ${displaySource ? `<span class="job-source-tag" title="출처: ${escapeHtml(displaySource)}">${sourceHref ? `<a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer">📝 ${escapeHtml(displaySource)} ↗</a>` : `📝 ${escapeHtml(displaySource)}`}</span>` : ""}
      </div>
      <div class="job-actions">
        ${renderActionButtons(job)}
      </div>
    </div>

    <div class="job-title-row">
      <span class="job-title" title="${safeFilename}">${safeFilename}</span>
      ${job.original_url ? `
        <div class="job-url-sub">
          <a href="${escapeHtml(job.original_url)}" target="_blank" rel="noopener noreferrer" class="job-url-link" title="다운로드 주소: ${escapeHtml(job.original_url)}">🔗 ${escapeHtml(job.original_url)}</a>
          <button type="button" class="btn-copy-url" data-url="${escapeHtml(job.original_url)}" title="링크 복사">📋</button>
        </div>
      ` : ""}
    </div>

    <div class="job-progress-track">
      <div class="job-progress-fill ${isResolving ? "indeterminate" : ""} status-${job.status}" 
           style="width: ${progressPercent}%"></div>
    </div>

    <div class="job-meta">
      <div class="job-meta-left">
        <span class="job-status-pill status-${job.status}">${statusInfo.text}</span>
        <span class="job-bytes">${bytesText}</span>
      </div>
      <div class="job-meta-right">
        ${renderMetaRightHtml(job)}
      </div>
    </div>
    ${job.error_message ? `
      <div class="job-error-msg">
        <div class="job-error-header">
          <strong>❌ 다운로드 실패</strong>
          ${displaySource ? `
            <span class="job-error-source">
              [출처: ${sourceHref ? `<a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(displaySource)} ↗</a>` : escapeHtml(displaySource)}]
            </span>` : ""}
        </div>
        ${job.original_url ? `
          <div class="job-error-url-row">
            <span class="job-error-url-label">요청 주소:</span>
            <a href="${escapeHtml(job.original_url)}" target="_blank" rel="noopener noreferrer" class="job-error-url-link" title="${escapeHtml(job.original_url)}">${escapeHtml(job.original_url)} ↗</a>
            <button type="button" class="btn-copy-url" data-url="${escapeHtml(job.original_url)}" title="주소 복사">📋</button>
          </div>
        ` : ""}
        ${(sourceHref && sourceHref !== job.original_url) ? `
          <div class="job-error-url-row">
            <span class="job-error-url-label">출처 웹페이지:</span>
            <a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer" class="job-error-url-link" title="${escapeHtml(sourceHref)}">${escapeHtml(job.source_title ? `${job.source_title} (${sourceHref})` : sourceHref)} ↗</a>
          </div>
        ` : ""}
        <div class="job-error-detail">${escapeHtml(job.error_message)}</div>
      </div>
    ` : ""}
  `;

  // 복사 버튼 이벤트 바인딩
  card.querySelectorAll(".btn-copy-url").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const textToCopy = btn.getAttribute("data-url");
      if (!textToCopy) return;
      try {
        await navigator.clipboard.writeText(textToCopy);
        const origText = btn.textContent;
        btn.textContent = "✓";
        setTimeout(() => {
          btn.textContent = origText;
        }, 1200);
        showPopupToast("클립보드에 링크가 복사되었습니다!");
      } catch (err) {
        showPopupToast("복사 실패", true);
      }
    });
  });

  // 액션 버튼 이벤트 바인딩
  const pauseBtn = card.querySelector(".action-pause");
  if (pauseBtn) pauseBtn.addEventListener("click", () => handleJobAction(job.id, "pause"));

  const resumeBtn = card.querySelector(".action-resume");
  if (resumeBtn) resumeBtn.addEventListener("click", () => handleJobAction(job.id, "resume"));

  const retryBtn = card.querySelector(".action-retry");
  if (retryBtn) retryBtn.addEventListener("click", () => handleJobAction(job.id, "retry"));

  const deleteBtn = card.querySelector(".action-delete");
  if (deleteBtn) deleteBtn.addEventListener("click", () => handleJobAction(job.id, "delete"));

  return card;
}

function renderActionButtons(job) {
  let btns = "";

  if (job.is_cloud && job.status === "completed" && job.cloud_url) {
    btns += `
      <a href="${escapeHtml(job.cloud_url)}" target="_blank" rel="noopener noreferrer" class="job-cloud-link" title="내 클라우드 드라이브 열기">
        ☁️ 열기 ↗
      </a>
    `;
  }

  if (job.status === "downloading") {
    btns += `
      <button type="button" class="job-action-btn action-pause" title="일시정지">
        <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>
      </button>
    `;
  } else if (job.status === "paused") {
    btns += `
      <button type="button" class="job-action-btn action-resume" title="다운로드 재개">
        <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
      </button>
    `;
  } else if (job.status === "failed" || job.status === "cancelled") {
    btns += `
      <button type="button" class="job-action-btn action-retry" title="다시 시도">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
      </button>
    `;
  } else if (job.status === "completed") {
    btns += `
      <button type="button" class="job-action-btn action-retry" title="다시 다운로드">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
      </button>
    `;
  }

  btns += `
    <button type="button" class="job-action-btn btn-del action-delete" title="목록에서 제거">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
    </button>
  `;

  return btns;
}

async function handleJobAction(jobId, action) {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  if (!serverUrl) return;

  try {
    let url = `${serverUrl}/api/jobs/${jobId}/${action}`;
    let method = "POST";

    if (action === "delete") {
      url = `${serverUrl}/api/jobs/${jobId}`;
      method = "DELETE";
    }

    const res = await fetch(url, {
      method: method,
      credentials: "omit",
      headers: getAuthHeaders(),
    });

    if (res.ok) {
      await fetchJobs();
    }
  } catch (err) {
    showPopupToast(`작업 처리 실패: ${err.message}`, true);
  }
}

function getStatusInfo(status, isCloud = false) {
  switch (status) {
    case "queued":
      return { text: "대기 중" };
    case "resolving":
      return { text: isCloud ? "클라우드 전송 중" : "링크 분석 중" };
    case "downloading":
      return { text: "다운로드 중" };
    case "paused":
      return { text: "일시정지" };
    case "completed":
      return { text: isCloud ? "☁️ 클라우드 보관됨" : "완료됨" };
    case "failed":
      return { text: "실패" };
    case "cancelled":
      return { text: "취소됨" };
    default:
      return { text: status || "알 수 없음" };
  }
}

function getServiceTagClass(serviceName, isCloud = false) {
  const lower = (serviceName || "").toLowerCase();
  if (isCloud || lower.includes("클라우드") || lower.includes("cloud")) return "tag-terabox-cloud";
  if (lower.includes("mega") && !lower.includes("megaup")) return "tag-mega";
  if (lower.includes("mybox")) return "tag-mybox";
  if (lower.includes("naver") || lower.includes("네이버")) return "tag-naver";
  if (lower.includes("tistory") || lower.includes("티스토리")) return "tag-tistory";
  if (lower.includes("gdrive") || lower.includes("google") || lower.includes("구글")) return "tag-gdrive";
  if (lower.includes("terabox") || lower.includes("테라박스")) return "tag-terabox";
  if (lower.includes("gigafile") || lower.includes("기가파일")) return "tag-gigafile";
  if (lower.includes("transfer.it") || lower.includes("transferit")) return "tag-transferit";
  if (lower.includes("1fichier") || lower.includes("onefichier")) return "tag-onefichier";
  if (lower.includes("kio")) return "tag-kio";
  if (lower.includes("gofile")) return "tag-gofile";
  if (lower.includes("viking")) return "tag-viking";
  if (lower.includes("megaup")) return "tag-megaup";
  if (lower.includes("x-share") || lower.includes("xshare")) return "tag-xshare";
  if (lower.includes("romsfun")) return "tag-romsfun";
  if (lower.includes("wowroms")) return "tag-wowroms";
  if (lower.includes("rootz")) return "tag-rootz";
  if (lower.includes("torrent") || lower.includes("마그넷")) return "tag-torrent";
  return "tag-direct";
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ==========================================
// URL 전송 관련
// ==========================================

async function sendCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    showPopupToast("현재 탭의 URL을 가져올 수 없습니다.", true);
    return;
  }

  if (!/^(https?:\/\/|magnet:\?)/i.test(tab.url)) {
    showPopupToast("현재 탭은 다운로드 가능한 웹페이지가 아닙니다.", true);
    return;
  }

  sendCurrentTabBtn.disabled = true;
  const originalHtml = sendCurrentTabBtn.innerHTML;
  sendCurrentTabBtn.innerHTML = `<span>전송 중...</span>`;

  try {
    const resData = await sendUrl(tab.url, "", "", "", tab.url, tab.title || "", tab.url);
    sendCurrentTabBtn.innerHTML = `<span>✓ 전송 완료!</span>`;
    const addedCount = resData.added_count || 1;
    showPopupToast(`✅ ${addedCount}개의 작업이 서버로 전송되었습니다!`);
    setTimeout(() => {
      sendCurrentTabBtn.disabled = false;
      sendCurrentTabBtn.innerHTML = originalHtml;
    }, 1400);

    switchToActiveTab();
    await fetchJobs();
  } catch (err) {
    showPopupToast("전송 실패: " + err.message, true);
    sendCurrentTabBtn.disabled = false;
    sendCurrentTabBtn.innerHTML = originalHtml;
  }
}

async function sendManualUrl() {
  const raw = manualUrlInput.value.trim();
  const subfolder = (manualSubfolderInput ? manualSubfolderInput.value : "").trim();
  const password = (manualPasswordInput ? manualPasswordInput.value : "").trim();
  if (!raw) {
    showPopupToast("전송할 다운로드 링크를 입력해주세요.", true);
    return;
  }

  sendManualBtn.disabled = true;
  sendManualBtn.textContent = "전송 중...";

  try {
    let referer = "";
    let sourceTitle = "";
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && tab.url && /^https?:\/\//i.test(tab.url)) {
        referer = tab.url;
        sourceTitle = tab.title || "";
      }
    } catch (e) {}

    const resData = await sendUrl(raw, subfolder, password, "", referer, sourceTitle, referer);
    manualUrlInput.value = "";
    if (manualSubfolderInput) manualSubfolderInput.value = "";
    if (manualPasswordInput) manualPasswordInput.value = "";
    sendManualBtn.textContent = "✓ 전송 완료!";
    const addedCount = resData.added_count || 1;
    showPopupToast(`✅ ${addedCount}개의 링크가 서버로 전송되었습니다!`);
    setTimeout(() => {
      sendManualBtn.disabled = false;
      sendManualBtn.textContent = "링크 전송";
    }, 1400);

    switchToActiveTab();
    await fetchJobs();
  } catch (err) {
    showPopupToast("전송 실패: " + err.message, true);
    sendManualBtn.disabled = false;
    sendManualBtn.textContent = "링크 전송";
  }
}

function switchToActiveTab() {
  tabBtns.forEach((b) => {
    if (b.getAttribute("data-tab") === "active") {
      b.classList.add("active");
    } else {
      b.classList.remove("active");
    }
  });
  currentTab = "active";
}

async function sendUrl(targetUrl, subfolder = "", password = "", teraboxMode = "", referer = "", sourceTitle = "", sourceUrl = "") {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  if (!serverUrl) {
    throw new Error("서버 주소를 먼저 설정해 주세요.");
  }

  const payload = { url: targetUrl };
  if (subfolder) {
    payload.subfolder = subfolder;
  }
  if (password) {
    payload.password = password;
    payload.terabox_password = password;
  }
  if (teraboxMode) {
    payload.terabox_mode = teraboxMode;
  }
  if (referer) {
    payload.referer = referer;
  }
  if (sourceTitle) {
    payload.source_title = sourceTitle;
  }
  if (sourceUrl) {
    payload.source_url = sourceUrl;
  }

  const res = await fetch(`${serverUrl}/api/jobs`, {
    method: "POST",
    credentials: "omit",
    headers: getAuthHeaders(),
    body: JSON.stringify(payload),
  });

  const data = await res.json().catch(() => ({}));

  if (res.status === 401) {
    throw new Error("비밀번호 인증 실패 (설정을 확인하세요)");
  }

  if (!res.ok || !data.success) {
    throw new Error(data.error || `서버 에러 (${res.status})`);
  }

  return data;
}

// ==========================================
// 현재 페이지 다운로드 링크 스캔 기능
// ==========================================

async function scanCurrentPage() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !tab.url || !/^https?:\/\//i.test(tab.url)) {
    showPopupToast("일반 웹페이지에서만 링크 스캔이 가능합니다.", true);
    return;
  }

  scanPageBtn.disabled = true;
  const originalHtml = scanPageBtn.innerHTML;
  scanPageBtn.innerHTML = `<span>스캔 중...</span>`;

  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractPageDownloadLinksInPage,
    });

    scanPageBtn.disabled = false;
    scanPageBtn.innerHTML = originalHtml;

    if (!results || !results[0] || !Array.isArray(results[0].result)) {
      showPopupToast("페이지 분석 결과를 가져오지 못했습니다.", true);
      return;
    }

    scannedLinks = results[0].result;
    if (scannedLinks.length === 0) {
      showPopupToast("현재 페이지에서 다운로드 가능한 링크를 찾지 못했습니다.");
      if (scanSection) scanSection.classList.add("hidden");
      return;
    }

    renderScannedLinks();
    if (scanSection) {
      scanSection.classList.remove("hidden");
      scanSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    showPopupToast(`🔍 ${scannedLinks.length}개의 다운로드 링크를 발견했습니다!`);
  } catch (err) {
    scanPageBtn.disabled = false;
    scanPageBtn.innerHTML = originalHtml;
    showPopupToast("스캔 실패: " + err.message, true);
  }
}

// 브라우저 탭 내에서 실행되는 스캐너 함수
function extractPageDownloadLinksInPage() {
  const links = [];
  const seenUrls = new Set();

  const currentUrl = window.location.href;
  const isNaverCafe = /(?:cafe\.naver\.com|m\.cafe\.naver\.com)/i.test(currentUrl);

  // 1. 현재 열린 페이지 자체가 네이버 카페 게시글인 경우:
  // 현재 게시글 URL을 1순위 항목(전체 첨부파일 다운로드)으로 자동 추가
  const cafeArticleMatch = currentUrl.match(/cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?|ca-fe\/)/i);
  if (isNaverCafe && cafeArticleMatch) {
    let articleTitle = document.title ? document.title.replace(/\s*:\s*네이버\s*카페\s*$/i, "").trim() : "";
    try {
      const iframe = document.querySelector('iframe#cafe_main, iframe#mainFrame, iframe[name="cafe_main"]');
      if (iframe && iframe.contentDocument) {
        const subElem = iframe.contentDocument.querySelector(".title_text, h3.title, .article_header .title");
        if (subElem && subElem.innerText.trim()) {
          articleTitle = subElem.innerText.trim();
        }
      }
    } catch (e) {}

    const cleanArticleUrl = currentUrl.split("?")[0].replace(/\/+$/, "") + (currentUrl.includes("?") ? "?" + currentUrl.split("?")[1] : "");
    seenUrls.add(cleanArticleUrl);
    links.push({
      url: currentUrl,
      title: `[네이버 카페 첨부파일] ${articleTitle || "게시글 첨부파일 일괄"}`,
      service: "Naver Cafe",
      password: "",
    });
  }

  // 검색할 도큐먼트 목록 (최상위 document + iframe#cafe_main)
  const docsToScan = [document];
  try {
    const iframes = Array.from(document.querySelectorAll("iframe"));
    for (const iframe of iframes) {
      if (iframe.contentDocument && iframe.contentDocument.body) {
        docsToScan.push(iframe.contentDocument);
      }
    }
  } catch (e) {}

  const HOSTING_PATTERNS = [
    { service: "MEGA", regex: /mega\.(?:nz|co\.nz|io)\/(?:file\/|#?!)/i },
    { service: "TeraBox", regex: /(?:1024terabox|terabox|teraboxapp|freeterabox)\.(?:com|app)/i },
    { service: "GoFile", regex: /gofile\.io\/d\//i },
    { service: "1fichier", regex: /1fichier\.com\/\?/i },
    { service: "Transfer.it", regex: /transfer\.it\/t\//i },
    { service: "GigaFile", regex: /(?:gigafile\.nu|xgf\.nu)/i },
    { service: "kio.ac", regex: /kio\.ac\/[cf]\//i },
    { service: "Naver MYBOX", regex: /mybox\.naver\.com\/share\//i },
    { service: "Naver Cafe", regex: /downapi\.cafe\.naver\.com/i },
    { service: "Tistory", regex: /(?:tistory\.com\/attachment|kakaocdn\.net\/dn|daumcdn\.net)/i },
    { service: "Rootz", regex: /rootz\.so\/d\//i },
    { service: "Romsfun", regex: /romsfun\.com\/download\//i },
    { service: "WoWroms", regex: /wowroms\.com\//i },
    { service: "X-Share", regex: /x-share\.net\/[sb]\//i },
    { service: "Google Drive", regex: /(?:drive|docs)\.google\.com\/(?:file\/d\/|open\?id=|uc\?id=)/i },
    { service: "VikingFile", regex: /(?:vikingfile\.com|vik1ngfile\.site)\/f\//i },
    { service: "MegaUp", regex: /megaup\.net\//i },
    { service: "Pixeldrain", regex: /pixeldrain\.com\/u\//i },
  ];

  const FILE_EXT_REGEX = /\.(zip|rar|7z|tar|gz|iso|bin|cue|apk|exe|msi|dmg|pkg|mp4|mkv|mp3|flac|torrent)(\?.*)?$/i;

  function identifyService(url) {
    if (url.startsWith("magnet:?")) return "BitTorrent";
    for (const p of HOSTING_PATTERNS) {
      if (p.regex.test(url)) return p.service;
    }
    if (FILE_EXT_REGEX.test(url)) return "Direct File";
    return "Direct Link";
  }

  function cleanTitle(str) {
    if (!str) return "";
    return str.replace(/\s+/g, " ").trim();
  }

  for (const doc of docsToScan) {
    // 1. 네이버 카페 전용 첨부파일 요소 정밀 스캔
    const cafeAttachLinks = Array.from(
      doc.querySelectorAll(
        'a[href*="downapi.cafe.naver.com"], a[href*="FileDownload"], a.link_file, a.btn_file, .attach_box a, .list_file a'
      )
    );
    for (const a of cafeAttachLinks) {
      const href = (a.href || "").trim();
      if (!href || href.startsWith("javascript:") || href.startsWith("#")) continue;
      if (seenUrls.has(href)) continue;
      seenUrls.add(href);

      const title =
        cleanTitle(
          a.innerText || a.getAttribute("download") || a.getAttribute("title") || ""
        ) ||
        href.split("/").pop().split("?")[0] ||
        href;
      links.push({
        url: href,
        title: title,
        service: "Naver Cafe",
        password: "",
      });
    }

    // 2. 일반 <a> 태그 스캔
    const anchors = Array.from(doc.querySelectorAll("a[href]"));
    for (const a of anchors) {
      const href = (a.href || "").trim();
      if (!href || href.startsWith("javascript:") || href.startsWith("#")) continue;

      // 네이버 카페 내부의 다른 게시글 링크(목록/추천/사이드바)는 무시!
      if (isNaverCafe && /cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?)/i.test(href)) {
        continue;
      }

      const isMagnet = href.startsWith("magnet:?");
      const isHosting = HOSTING_PATTERNS.some((p) => p.regex.test(href));
      const isFile = FILE_EXT_REGEX.test(href);

      if (isMagnet || isHosting || isFile) {
        if (!seenUrls.has(href)) {
          seenUrls.add(href);
          const title =
            cleanTitle(
              a.innerText || a.getAttribute("download") || a.getAttribute("title") || ""
            ) ||
            href.split("/").pop().split("?")[0] ||
            href;

          let password = "";
          const parentText = a.parentElement ? a.parentElement.innerText : "";
          const pwMatch = parentText.match(
            /(?:비번|암호|비밀번호|추출\s*코드|pwd|pw|pass|code)\s*[:=]?\s*([a-zA-Z0-9_!@#$%^&*-]{2,32})/i
          );
          if (pwMatch) {
            password = pwMatch[1].trim();
          }

          links.push({
            url: href,
            title: title,
            service: identifyService(href),
            password: password,
          });
        }
      }
    }

    // 3. 본문 텍스트 내 평문 링크 및 Base64 스캔
    const bodyText = doc.body ? doc.body.innerText : "";

    // 3-1. 평문 링크
    const urlRegex = /(?:https?:\/\/[^\s<>"'`]+|magnet:\?xt=[^\s<>"'`]+)/g;
    let match;
    while ((match = urlRegex.exec(bodyText)) !== null) {
      let rawUrl = match[0].replace(/[.,;:)]+$/, "");
      if (seenUrls.has(rawUrl)) continue;

      // 네이버 카페 게시글 링크는 평문 링크에서도 제외
      if (isNaverCafe && /cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?)/i.test(rawUrl)) {
        continue;
      }

      const isMagnet = rawUrl.startsWith("magnet:?");
      const isHosting = HOSTING_PATTERNS.some((p) => p.regex.test(rawUrl));
      const isFile = FILE_EXT_REGEX.test(rawUrl);

      if (isMagnet || isHosting || isFile) {
        seenUrls.add(rawUrl);

        let password = "";
        const startIdx = Math.max(0, match.index - 50);
        const endIdx = Math.min(bodyText.length, match.index + rawUrl.length + 100);
        const ctxText = bodyText.substring(startIdx, endIdx);
        const pwMatch = ctxText.match(
          /(?:비번|암호|비밀번호|추출\s*코드|pwd|pw|pass|code)\s*[:=]?\s*([a-zA-Z0-9_!@#$%^&*-]{2,32})/i
        );
        if (pwMatch) {
          password = pwMatch[1].trim();
        }

        links.push({
          url: rawUrl,
          title: rawUrl.split("/").pop().split("?")[0] || rawUrl,
          service: identifyService(rawUrl),
          password: password,
        });
      }
    }

    // 3-2. Base64 인코딩 링크
    const b64Regex = /(?:aHR0cHM6Ly[A-Za-z0-9+/=]{16,}|bWFnbmV0O[A-Za-z0-9+/=]{16,})/g;
    let b64Match;
    while ((b64Match = b64Regex.exec(bodyText)) !== null) {
      try {
        const decoded = atob(b64Match[0]);
        if (/^(https?:\/\/|magnet:\?)/i.test(decoded)) {
          if (!seenUrls.has(decoded)) {
            seenUrls.add(decoded);

            let password = "";
            const startIdx = Math.max(0, b64Match.index - 50);
            const endIdx = Math.min(bodyText.length, b64Match.index + b64Match[0].length + 100);
            const ctxText = bodyText.substring(startIdx, endIdx);
            const pwMatch = ctxText.match(
              /(?:비번|암호|비밀번호|추출\s*코드|pwd|pw|pass|code)\s*[:=]?\s*([a-zA-Z0-9_!@#$%^&*-]{2,32})/i
            );
            if (pwMatch) {
              password = pwMatch[1].trim();
            }

            links.push({
              url: decoded,
              title: `[Base64] ` + (decoded.split("/").pop().split("?")[0] || decoded),
              service: identifyService(decoded),
              password: password,
            });
          }
        }
      } catch (e) {}
    }
  }

  return links;
}

function renderScannedLinks() {
  if (!scanListContainer) return;
  scanListContainer.innerHTML = "";

  if (scanCountBadge) {
    scanCountBadge.textContent = `${scannedLinks.length}개 발견`;
  }
  if (scanSelectAll) {
    scanSelectAll.checked = true;
  }

  scannedLinks.forEach((item, idx) => {
    const row = document.createElement("div");
    row.className = "scan-item";

    const tagClass = getServiceTagClass(item.service);

    row.innerHTML = `
      <input type="checkbox" id="scanItem_${idx}" data-idx="${idx}" checked>
      <div class="scan-item-content">
        <div class="scan-item-header">
          <span class="scan-item-service ${tagClass}">${escapeHtml(item.service)}</span>
          <span class="scan-item-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</span>
        </div>
        <div class="scan-item-url" title="${escapeHtml(item.url)}">${escapeHtml(item.url)}</div>
        <div class="scan-item-pwd-box">
          <span class="scan-item-pwd-tag">암호:</span>
          <input type="text" class="scan-item-pwd-input" id="scanPwd_${idx}" value="${escapeHtml(item.password || "")}" placeholder="없음">
        </div>
      </div>
    `;

    const checkbox = row.querySelector("input[type='checkbox']");
    if (checkbox) {
      checkbox.addEventListener("change", updateSelectedScanCount);
    }

    scanListContainer.appendChild(row);
  });

  updateSelectedScanCount();
}

function updateSelectedScanCount() {
  if (!scanListContainer || !selectedScanCount) return;
  const checkboxes = scanListContainer.querySelectorAll("input[type='checkbox']:checked");
  const count = checkboxes.length;
  selectedScanCount.textContent = count;
  if (sendScannedBtn) {
    sendScannedBtn.disabled = count === 0;
  }
}

async function sendSelectedScannedLinks() {
  if (!scanListContainer) return;
  const checkedBoxes = Array.from(scanListContainer.querySelectorAll("input[type='checkbox']:checked"));
  if (checkedBoxes.length === 0) {
    showPopupToast("선택된 다운로드 링크가 없습니다.", true);
    return;
  }

  const subfolder = (scanSubfolderInput ? scanSubfolderInput.value : "").trim();
  sendScannedBtn.disabled = true;
  const originalHtml = sendScannedBtn.innerHTML;
  sendScannedBtn.innerHTML = `<span>전송 중 (${checkedBoxes.length}개)...</span>`;

  let successCount = 0;
  let failCount = 0;

  let referer = "";
  let sourceTitle = "";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url && /^https?:\/\//i.test(tab.url)) {
      referer = tab.url;
      sourceTitle = tab.title || "";
    }
  } catch (e) {}

  for (const cb of checkedBoxes) {
    const idx = parseInt(cb.getAttribute("data-idx"), 10);
    const item = scannedLinks[idx];
    if (!item) continue;

    const pwdInput = document.getElementById(`scanPwd_${idx}`);
    const password = pwdInput ? pwdInput.value.trim() : (item.password || "");

    try {
      await sendUrl(item.url, subfolder, password, "", referer, sourceTitle || item.title, referer);
      successCount++;
    } catch (e) {
      failCount++;
      console.warn("스캔 항목 전송 실패:", item.url, e);
    }
  }

  sendScannedBtn.disabled = false;
  sendScannedBtn.innerHTML = originalHtml;

  if (successCount > 0) {
    showPopupToast(`✅ ${successCount}개 항목이 서버로 전송되었습니다!${failCount > 0 ? ` (${failCount}개 실패)` : ""}`);
    if (scanSection) scanSection.classList.add("hidden");
    switchToActiveTab();
    await fetchJobs();
  } else {
    showPopupToast("전송에 실패했습니다. 서버 연결 상태를 확인해 주세요.", true);
  }
}

/**
 * 버전 문자열 비교 (SemVer 지원, v접두어 무시)
 * @param {string} currentVer 예: "0.1.34"
 * @param {string} latestVer 예: "v0.1.35" 또는 "0.1.35"
 * @returns {boolean} 최신 버전이 현재 버전보다 높으면 true
 */
function isNewerVersion(currentVer, latestVer) {
  if (!latestVer) return false;
  const cleanCurrent = (currentVer || "").replace(/^v/i, "").trim();
  const cleanLatest = (latestVer || "").replace(/^v/i, "").trim();
  if (!cleanLatest) return false;
  if (cleanCurrent === cleanLatest) return false;

  const currentParts = cleanCurrent.split(".").map((p) => parseInt(p, 10) || 0);
  const latestParts = cleanLatest.split(".").map((p) => parseInt(p, 10) || 0);
  const maxLen = Math.max(currentParts.length, latestParts.length);

  for (let i = 0; i < maxLen; i++) {
    const c = currentParts[i] || 0;
    const l = latestParts[i] || 0;
    if (l > c) return true;
    if (l < c) return false;
  }
  return false;
}

/**
 * GitHub 릴리즈 최신 버전 확인 및 배너 표시
 */
async function checkForExtensionUpdate(force = false) {
  try {
    const manifest = chrome.runtime.getManifest();
    const currentVersion = manifest.version || "0.0.0";

    const storage = await chrome.storage.local.get(["lastUpdateCheckTime", "cachedReleaseInfo"]);
    const now = Date.now();

    let release = null;
    if (!force && storage.lastUpdateCheckTime && (now - storage.lastUpdateCheckTime < UPDATE_CHECK_CACHE_MS) && storage.cachedReleaseInfo) {
      release = storage.cachedReleaseInfo;
    } else {
      const resp = await fetch("https://api.github.com/repos/x220volt/RmtDownloader/releases/latest", {
        headers: { "Accept": "application/vnd.github.v3+json" }
      });
      if (resp.ok) {
        release = await resp.json();
        await chrome.storage.local.set({
          lastUpdateCheckTime: now,
          cachedReleaseInfo: release
        });
      }
    }

    if (release && release.tag_name) {
      if (isNewerVersion(currentVersion, release.tag_name)) {
        latestReleaseData = release;
        const verStr = release.tag_name.replace(/^v/i, "");
        if (updateVersionText) {
          updateVersionText.textContent = `v${verStr} 출시 (현재: v${currentVersion})`;
        }
        if (updateNotesLink) {
          updateNotesLink.href = release.html_url || "https://github.com/x220volt/RmtDownloader/releases";
        }
        if (updateBanner) {
          updateBanner.classList.remove("hidden");
        }
        return;
      }
    }

    // 최신 버전이 아니거나 확인 불가 시 배너 숨김
    if (updateBanner) {
      updateBanner.classList.add("hidden");
    }
  } catch (err) {
    console.debug("확장 업데이트 확인 중 경미한 오류 (무시 가능):", err);
  }
}

/**
 * 로컬 파이썬 서버(/api/extension/update)를 통한 원클릭 업데이트 실행
 */
async function applyExtensionUpdate() {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  if (!serverUrl) {
    showPopupToast("서버 주소가 설정되지 않았습니다.", true);
    return;
  }

  if (!applyUpdateBtn) return;
  const originalHtml = applyUpdateBtn.innerHTML;
  applyUpdateBtn.disabled = true;
  applyUpdateBtn.innerHTML = "<span>⏳ 업데이트 중...</span>";

  try {
    showPopupToast("최신 확장 프로그램 버전을 다운로드 및 갱신 중입니다...");
    const payload = {};
    if (latestReleaseData && latestReleaseData.tag_name) {
      payload.tag_name = latestReleaseData.tag_name;
    }

    const res = await fetch(`${serverUrl}/api/extension/update`, {
      method: "POST",
      credentials: "omit",
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === "ok") {
      showPopupToast("✅ 확장이 최신 버전으로 업데이트되었습니다! 확장 프로그램을 재로드합니다...");
      // 캐시 삭제
      await chrome.storage.local.remove(["lastUpdateCheckTime", "cachedReleaseInfo"]);
      setTimeout(() => {
        try {
          chrome.runtime.reload();
        } catch (e) {
          window.location.reload();
        }
      }, 1200);
    } else {
      const errMsg = data.error || data.detail || data.message || (res.status === 401 ? "로그인이 필요합니다." : "업데이트 실패");
      showPopupToast(errMsg, true);
      applyUpdateBtn.disabled = false;
      applyUpdateBtn.innerHTML = originalHtml;
    }
  } catch (err) {
    console.error("원클릭 업데이트 요청 오류:", err);
    showPopupToast(`업데이트 요청 실패: ${err.message || "서버 통신 오류"}`, true);
    applyUpdateBtn.disabled = false;
    applyUpdateBtn.innerHTML = originalHtml;
  }
}
