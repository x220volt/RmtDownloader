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
const clipboardAutoFillCheck = document.getElementById("clipboardAutoFillCheck");
const clipboardCopyPromptCheck = document.getElementById("clipboardCopyPromptCheck");
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
const remoteUpdateModal = document.getElementById("remoteUpdateModal");
const closeRemoteUpdateModal = document.getElementById("closeRemoteUpdateModal");
const remoteUpdateDesc = document.getElementById("remoteUpdateDesc");
const selectExtFolderUpdateBtn = document.getElementById("selectExtFolderUpdateBtn");
const reselectExtFolderBtn = document.getElementById("reselectExtFolderBtn");
const downloadExtZipBtn = document.getElementById("downloadExtZipBtn");

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
let isServerConnected = false;
let toastTimer = null;
let latestReleaseData = null;
let pendingServerUpdateData = null;
let autoFilledClipboardRawText = "";
const UPDATE_CHECK_CACHE_MS = 20 * 60 * 1000; // 20분 캐시
const EXT_ALLOWED_EXTENSIONS = new Set([
  ".js",
  ".html",
  ".css",
  ".json",
  ".png",
  ".svg",
  ".jpg",
  ".jpeg",
  ".gif",
  ".ico",
]);

async function fetchWithTimeout(url, options = {}, timeoutMs = 2500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, Object.assign({}, options, { signal: controller.signal }));
  } finally {
    clearTimeout(timer);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  setupEventListeners();

  const params = new URLSearchParams(window.location.search);
  const isStandaloneUpdate = params.get("standaloneUpdate") === "1";
  const openSettingsParam = params.get("openSettings") === "1";

  if (isStandaloneUpdate) {
    await checkForExtensionUpdate(true);
    await openRemoteUpdateModal();
    return;
  }

  const needsInitialSetup = !passwordInput || !passwordInput.value.trim();
  let autoOpenedInitialSettings = false;
  if ((openSettingsParam || needsInitialSetup) && settingsModal) {
    autoOpenedInitialSettings = needsInitialSetup && !openSettingsParam;
    if (autoOpenedInitialSettings) {
      settingsModal.addEventListener(
        "input",
        () => {
          autoOpenedInitialSettings = false;
        },
        true
      );
    }
    settingsModal.classList.add("show");
    if (
      serverUrlInput &&
      document.activeElement !== usernameInput &&
      document.activeElement !== passwordInput
    ) {
      try {
        serverUrlInput.focus();
      } catch (e) {}
    }
  }

  await tryAutoFillFromClipboard();
  await tryAutoFillRetroDbSubfolder();

  // 최신 릴리즈 확인 (백그라운드에서 비동기 체크)
  checkForExtensionUpdate();

  const isConnected = await checkConnection();
  if (isConnected) {
    if (autoOpenedInitialSettings && settingsModal) {
      settingsModal.classList.remove("show");
    }
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
      if (!isServerConnected) {
        const ok = await checkConnection();
        if (ok) {
          await fetchJobs();
        }
        return;
      }
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
      if (!settingsModal) return;
      if (settingsModal.classList.contains("show")) {
        settingsModal.classList.remove("show");
        return;
      }
      if (remoteUpdateModal) remoteUpdateModal.classList.remove("show");
      settingsModal.classList.add("show");
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

  // 클립보드 옵션 체크박스 즉시 저장
  if (clipboardAutoFillCheck) {
    clipboardAutoFillCheck.addEventListener("change", () => {
      setStorageConfig({ clipboardAutoFill: clipboardAutoFillCheck.checked });
    });
  }
  if (clipboardCopyPromptCheck) {
    clipboardCopyPromptCheck.addEventListener("change", () => {
      setStorageConfig({ clipboardCopyPrompt: clipboardCopyPromptCheck.checked });
    });
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
  if (closeRemoteUpdateModal && remoteUpdateModal) {
    closeRemoteUpdateModal.addEventListener("click", () => {
      remoteUpdateModal.classList.remove("show");
    });
  }
  if (remoteUpdateModal) {
    remoteUpdateModal.addEventListener("click", (e) => {
      if (e.target === remoteUpdateModal) {
        remoteUpdateModal.classList.remove("show");
      }
    });
  }
  if (selectExtFolderUpdateBtn) {
    selectExtFolderUpdateBtn.addEventListener("click", () => handleSelectFolderUpdateClick(false));
  }
  if (reselectExtFolderBtn) {
    reselectExtFolderBtn.addEventListener("click", () => handleSelectFolderUpdateClick(true));
  }
  if (downloadExtZipBtn) {
    downloadExtZipBtn.addEventListener("click", () => {
      const downloadUrl = getLatestExtensionZipUrl();
      chrome.tabs.create({ url: downloadUrl });
    });
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
  const keys = ["serverUrl", "username", "password", "capturePage", "clipboardAutoFill", "clipboardCopyPrompt"];
  return new Promise((resolve) => {
    try {
      if (chrome.storage && chrome.storage.local) {
        chrome.storage.local.get(keys, (localItems) => {
          if (!chrome.runtime.lastError && localItems && (localItems.serverUrl || localItems.password || typeof localItems.capturePage === "boolean")) {
            return resolve(localItems);
          }
          if (chrome.storage.sync) {
            chrome.storage.sync.get(keys, (syncItems) => {
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
      const syncData = Object.assign({}, data);
      delete syncData.password;
      chrome.storage.sync.set(syncData);
      chrome.storage.sync.remove("password");
    }
  } catch (e) {}
}

async function loadSettings() {
  const config = await getStorageConfig();

  serverUrlInput.value = config.serverUrl || "http://127.0.0.1:7971";
  usernameInput.value = config.username || "admin";
  passwordInput.value = config.password || "";
  if (clipboardAutoFillCheck) {
    clipboardAutoFillCheck.checked = config.clipboardAutoFill !== false;
  }
  if (clipboardCopyPromptCheck) {
    clipboardCopyPromptCheck.checked = config.clipboardCopyPrompt !== false;
  }
}

async function saveSettings() {
  const serverUrl = normalizeServerUrl(serverUrlInput.value) || "http://127.0.0.1:7971";
  const username = usernameInput.value.trim() || "admin";
  const password = passwordInput.value.trim();

  serverUrlInput.value = serverUrl;
  usernameInput.value = username;

  const origBtnText = saveSettingsBtn ? saveSettingsBtn.textContent : "";
  if (saveSettingsBtn) {
    saveSettingsBtn.disabled = true;
    saveSettingsBtn.textContent = "⏳ 저장 중...";
  }

  try {
    await setStorageConfig({
      serverUrl: serverUrl,
      username: username,
      password: password,
      clipboardAutoFill: clipboardAutoFillCheck ? clipboardAutoFillCheck.checked : true,
      clipboardCopyPrompt: clipboardCopyPromptCheck ? clipboardCopyPromptCheck.checked : true,
    });

    showTestResult(true, "설정이 저장되었습니다. 서버 연결을 확인하는 중...");
    const isConnected = await checkConnection();
    if (isConnected) {
      showTestResult(true, "✅ 연결 성공! 설정이 안전하게 저장되었습니다.");
      showPopupToast("설정이 저장되고 서버에 연결되었습니다.");
      setTimeout(() => {
        if (settingsModal) settingsModal.classList.remove("show");
      }, 600);
      await fetchJobs();
      startPolling();
    }
  } finally {
    if (saveSettingsBtn) {
      saveSettingsBtn.disabled = false;
      saveSettingsBtn.textContent = origBtnText || "설정 저장";
    }
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
    if (serverUrlInput && document.activeElement !== serverUrlInput) {
      serverUrlInput.value = serverUrl;
    }
  }

  const isDefaultLocalUrl =
    serverUrl === "http://127.0.0.1:7971" || serverUrl === "http://localhost:7971";
  const urlsToTry = [serverUrl];
  if (!isDefaultLocalUrl) {
    if (serverUrl.includes("localhost")) {
      urlsToTry.push(serverUrl.replace("localhost", "127.0.0.1"));
    } else if (serverUrl.includes("127.0.0.1")) {
      urlsToTry.push(serverUrl.replace("127.0.0.1", "localhost"));
    }
  }

  let lastStatus = 0;
  for (const testUrl of urlsToTry) {
    try {
      const timeoutMs = (testUrl.includes("127.0.0.1") || testUrl.includes("localhost")) ? 800 : 1500;
      const res = await fetchWithTimeout(
        `${testUrl}/api/auth/test`,
        {
          method: "POST",
          credentials: "omit",
          headers: {
            "Content-Type": "application/json",
            "X-Auth-User": username,
            "X-Auth-Password": password,
          },
          body: JSON.stringify({}),
        },
        timeoutMs
      );

      lastStatus = res.status;

      if (res.ok) {
        if (testUrl !== serverUrl) {
          if (serverUrlInput && document.activeElement !== serverUrlInput) {
            serverUrlInput.value = testUrl;
          }
          setStorageConfig({ serverUrl: testUrl });
        }
        const data = await res.json().catch(() => ({}));
        if (data.download_dir) {
          baseDownloadDir = data.download_dir;
        }

        isServerConnected = true;
        statusBadge.className = "status-badge online";
        statusText.textContent = "연결됨";
        return true;
      } else if (res.status === 401) {
        isServerConnected = false;
        statusBadge.className = "status-badge offline";
        statusText.textContent = "비밀번호 불일치";
        if (settingsModal && !(remoteUpdateModal && remoteUpdateModal.classList.contains("show"))) {
          settingsModal.classList.add("show");
        }
        showTestResult(
          false,
          !password
            ? "❌ 인증 필요: 아이디와 비밀번호를 입력해 주세요 (초기 기본값: password1234)"
            : "❌ 비밀번호 불일치: 비밀번호를 입력해주세요 (초기 기본값: password1234)"
        );
        return false;
      }
    } catch (err) {
      // 다음 URL로 시도
    }
  }

  isServerConnected = false;
  statusBadge.className = "status-badge offline";
  if (lastStatus === 401) {
    statusText.textContent = "비밀번호 불일치";
    if (settingsModal && !(remoteUpdateModal && remoteUpdateModal.classList.contains("show"))) {
      settingsModal.classList.add("show");
    }
    showTestResult(
      false,
      !password
        ? "❌ 인증 필요: 아이디와 비밀번호를 입력해 주세요 (초기 기본값: password1234)"
        : "❌ 비밀번호 불일치: 비밀번호를 확인해주세요 (초기 기본값: password1234)"
    );
  } else {
    statusText.textContent = "연결 안됨";
    if (settingsModal && !(remoteUpdateModal && remoteUpdateModal.classList.contains("show"))) {
      settingsModal.classList.add("show");
    }
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

  const origTestBtnText = testConnBtn ? testConnBtn.textContent : "";
  if (testConnBtn) {
    testConnBtn.disabled = true;
    testConnBtn.textContent = "⏳ 테스트 중...";
  }

  let errorDetails = "";

  try {
    for (const testUrl of urlsToTry) {
      try {
        const res = await fetchWithTimeout(
          `${testUrl}/api/auth/test`,
          {
            method: "POST",
            credentials: "omit",
            headers: {
              "Content-Type": "application/json",
              "X-Auth-User": username,
              "X-Auth-Password": password,
            },
            body: JSON.stringify({}),
          },
          2500
        );

        const data = await res.json().catch(() => ({}));

        if (res.ok && data.success) {
          if (testUrl !== serverUrl) {
            serverUrlInput.value = testUrl;
          }
          await setStorageConfig({
            serverUrl: testUrl,
            username: username,
            password: password,
            clipboardAutoFill: clipboardAutoFillCheck ? clipboardAutoFillCheck.checked : true,
            clipboardCopyPrompt: clipboardCopyPromptCheck ? clipboardCopyPromptCheck.checked : true,
          });

          isServerConnected = true;
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
          isServerConnected = false;
          showTestResult(
            false,
            !password
              ? "❌ 인증 필요(401): 비밀번호를 입력해 주세요. (초기 기본값: password1234)"
              : "❌ 인증 실패(401): 비밀번호가 틀립니다. (초기 기본값: password1234)"
          );
          statusBadge.className = "status-badge offline";
          statusText.textContent = "비밀번호 불일치";
          return;
        } else {
          errorDetails = `응답 코드 ${res.status}`;
        }
      } catch (err) {
        errorDetails = err && err.name === "AbortError" ? "응답 시간 초과" : (err.message || "연결 거부");
      }
    }

    isServerConnected = false;
    showTestResult(false, `❌ 접속 불가(${errorDetails}): 서버(${serverUrl})에 접속할 수 없습니다. RemoteDownloader가 실행 중인지 확인하세요.`);
    statusBadge.className = "status-badge offline";
    statusText.textContent = "연결 안됨";
  } finally {
    if (testConnBtn) {
      testConnBtn.disabled = false;
      testConnBtn.textContent = origTestBtnText || "연결 테스트";
    }
  }
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
    const res = await fetchWithTimeout(
      `${serverUrl}/api/jobs`,
      {
        method: "GET",
        credentials: "omit",
        headers: getAuthHeaders(false),
      },
      2500
    );

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
    ${(job.error_message && job.status !== "downloading" && (["failed", "cancelled"].includes(job.status) || (job.status === "queued" && job.error_message.includes("재시도")))) ? `
      <div class="job-error-msg">
        <div class="job-error-header">
          <strong>${job.status === "queued" ? "⚠️ 재시도 대기" : (job.error_message && job.error_message.includes("중복") ? "⚠️ 중복 다운로드 제외" : "❌ 다운로드 실패")}</strong>
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

  const cancelBtn = card.querySelector(".action-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => handleJobAction(job.id, "cancel"));

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

  if (["downloading", "queued", "resolving", "paused"].includes(job.status)) {
    btns += `
      <button type="button" class="job-action-btn action-cancel" title="취소">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
  }

  btns += `
    <button type="button" class="job-action-btn btn-del action-delete" title="목록에서 삭제">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
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

    const res = await fetchWithTimeout(
      url,
      {
        method: method,
        credentials: "omit",
        headers: getAuthHeaders(),
      },
      2500
    );

    if (res.ok) {
      await fetchJobs();
      if (action === "cancel") {
        showPopupToast("다운로드가 취소되었습니다.");
      } else if (action === "pause") {
        showPopupToast("다운로드가 일시정지되었습니다.");
      } else if (action === "resume") {
        showPopupToast("다운로드가 재개되었습니다.");
      }
    } else {
      const data = await res.json().catch(() => ({}));
      showPopupToast(data.error || "작업 처리에 실패했습니다.", true);
    }
  } catch (err) {
    const errMsg = err && err.name === "AbortError" ? "서버 응답 시간 초과" : err.message;
    showPopupToast(`작업 처리 실패: ${errMsg}`, true);
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
  if (lower.includes("romspure")) return "tag-romspure";
  if (lower.includes("wowroms")) return "tag-wowroms";
  if (lower.includes("planetemu")) return "tag-planetemu";
  if (lower.includes("vimm")) return "tag-vimm";
  if (lower.includes("mediafire")) return "tag-mediafire";
  if (lower.includes("filekeeper")) return "tag-filekeeper";
  if (lower.includes("datanodes")) return "tag-datanodes";
  if (lower.includes("buzzheavier") || lower.includes("bzzhr")) return "tag-buzzheavier";
  if (lower.includes("akirabox")) return "tag-akirabox";
  if (lower.includes("pixeldrain")) return "tag-pixeldrain";
  if (lower.includes("rootz")) return "tag-rootz";
  if (lower.includes("github") || lower.includes("깃허브")) return "tag-github";
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
// 클립보드 다운로드 링크 감지 및 자동 입력
// ==========================================

const CLIPBOARD_HOSTING_PATTERNS = [
  /mega\.(?:nz|co\.nz|io)\/(?:file\/|folder\/|#?!)/i,
  /(?:1024terabox|terabox|teraboxapp|freeterabox|1024tera|teraboxlink)\.(?:com|app)/i,
  /gofile\.io\/d\//i,
  /1fichier\.com\/\?/i,
  /transfer\.it\/t\//i,
  /(?:gigafile\.nu|xgf\.nu)\//i,
  /kio\.ac\/[cf]\//i,
  /mybox\.naver\.com\/share\//i,
  /downapi\.cafe\.naver\.com/i,
  /(?:\.tistory\.com\/(?:(?:m\/)?\d+|entry\/|attachment)|kakaocdn\.net\/dn|daumcdn\.net)/i,
  /rootz\.so\/d\//i,
  /^https?:\/\/(?:(?:www\.)?github\.com\/(?!(?:settings|notifications|explore|trending|topics|marketplace|sponsors|login|signup|join|orgs|organizations|users|search|features|pricing|enterprise|about|security|customer-stories|readme|collections|events|codespaces|copilot|pulls|issues|site|solutions|resources|account|apps|dashboard)(?:\/|$|\?|#))[^\/\s?#]+\/[^\/\s?#]+(?:\/(?:releases|archive|zipball|tarball|raw|blob|tree)(?:\/|$|\?|#)|\/?(?:$|\?|#))|(?:raw|objects|release-assets|gist)\.githubusercontent\.com\/[^\/\s]+\/|codeload\.github\.com\/[^\/\s]+\/)/i,
  /romsfun\.com\/download\//i,
  /romspure\.cc\/(?:download|roms)\//i,
  /wowroms\.com\//i,
  /planetemu\.net\/(?:[a-z]{2}\/)?(?:roms?\/[^/?#]+\/[^/?#]+|download\/rom)/i,
  /vimm\.net\/vault\//i,
  /x-share\.net\/[sb]\//i,
  /(?:drive|docs|drive\.usercontent)\.google\.com\/(?:file\/(?:u\/\d+\/)?d\/|open\?id=|uc\?(?:[^#\s]*&)?id=|drive\/(?:u\/\d+\/)?folders\/|download\?id=)/i,
  /mediafire\.com\/(?:file|download|view|\?)/i,
  /filekeeper\.net\/[a-zA-Z0-9_-]+/i,
  /datanodes\.to\/[a-zA-Z0-9_-]+/i,
  /(?:buzzheavier\.com|bzzhr\.to)\/[a-zA-Z0-9_-]+/i,
  /akirabox\.(?:to|com|net)\/(?:[a-zA-Z0-9_-]+\/file|[a-zA-Z0-9_-]+)/i,
  /(?:vik[i1]ngf[i1]le\.[a-z]{2,})\/[fd]\//i,
  /megaup\.net\//i,
  /(?:pixeldrain\.(?:com|net)\/(?:u|l|d|api\/file)\/|pixeldra\.in\/)/i,
  /teeteetee-korean\.com\/(?:projects\/[a-zA-Z0-9_\-]+|api\/patch-releases|api\/[a-z0-9_\-]+-xdelta|downloads\/)/i,
  /hangulteo\.com\/[a-zA-Z0-9_\-]+/i,
];

const CLIPBOARD_FILE_EXT_REGEX = /\.(zip|rar|7z|tar|gz|bz2|xz|iso|bin|cue|chd|nsp|xci|nsz|apk|exe|msi|dmg|pkg|xdelta|bps|ips|ups|ppf|patch|mp4|mkv|avi|mov|webm|mp3|flac|wav|torrent)(\?.*)?$/i;

function decodeClipboardBase64Once(token) {
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

function decodeClipboardBase64Chain(token) {
  let current = decodeClipboardBase64Once(token);
  if (!current) return "";
  if (/^[A-Za-z0-9+/_-]{16,}={0,2}$/.test(current)) {
    const second = decodeClipboardBase64Once(current);
    if (second) {
      current = second;
    }
  }
  return current;
}

function isSupportedDownloadUrl(candidate, fromBase64 = false) {
  if (!candidate) return false;
  if (candidate.startsWith("magnet:?")) return true;
  if (CLIPBOARD_HOSTING_PATTERNS.some((re) => re.test(candidate))) return true;
  if (CLIPBOARD_FILE_EXT_REGEX.test(candidate)) return true;
  if (fromBase64) {
    try {
      const parsed = new URL(candidate);
      return (parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.hostname.includes(".");
    } catch (e) {
      return false;
    }
  }
  return false;
}

function parseClipboardDownloadText(rawText) {
  const text = (rawText || "").trim();
  if (!text || text.length > 8000) return null;

  const urlRegex = /(?:https?:\/\/[^\s<>"'`|]+|magnet:\?xt=[^\s<>"'`|]+)/gi;
  const b64Regex = /(?:[A-Za-z0-9+/_-]{16,}={0,2})/g;
  const pwRegex = /(?:비번|암호|비밀번호|추출\s*코드|dlkey|pwd|pw|pass|password|code)\s*[:=]\s*([^\s"'<>\n\r,;]{2,32})|(?:비번|암호|비밀번호|추출\s*코드)\s+([a-zA-Z0-9_!@#$%^&*-]{2,32})/i;

  const urls = [];
  const seen = new Set();

  const tryAdd = (raw, fromBase64) => {
    const cleaned = (raw || "").trim().replace(/[.,;:)\]>\x7d"']+$/, "");
    if (!cleaned || seen.has(cleaned)) return;
    if (!isSupportedDownloadUrl(cleaned, fromBase64)) return;
    seen.add(cleaned);
    urls.push(cleaned);
  };

  let m;
  while ((m = urlRegex.exec(text)) !== null) {
    tryAdd(m[0], false);
  }

  let b64m;
  while ((b64m = b64Regex.exec(text)) !== null) {
    const decoded = decodeClipboardBase64Chain(b64m[0]);
    if (!decoded) continue;
    if (decoded.includes("http://") || decoded.includes("https://") || decoded.includes("magnet:?")) {
      const inner = decoded.match(urlRegex);
      if (inner) {
        for (const u of inner) {
          tryAdd(u, true);
        }
      }
    }
  }

  if (urls.length === 0) return null;

  let password = "";
  const pwMatch = text.match(pwRegex);
  if (pwMatch) {
    password = (pwMatch[1] || pwMatch[2] || "").trim();
  }

  return { urls, password };
}

async function tryAutoFillFromClipboard() {
  try {
    const config = await getStorageConfig();
    if (config.clipboardAutoFill === false) return;
    if (!manualUrlInput || manualUrlInput.value.trim() !== "") return;

    const stored = await new Promise((resolve) => {
      try {
        if (chrome.storage && chrome.storage.local) {
          chrome.storage.local.get(
            ["lastCopiedClipboardText", "lastCopiedClipboardAt", "lastSentClipboardText"],
            (items) => {
              void chrome.runtime.lastError;
              resolve(items || {});
            }
          );
        } else {
          resolve({});
        }
      } catch (e) {
        resolve({});
      }
    });

    const clipText = (stored.lastCopiedClipboardText || "").trim();
    if (!clipText) return;

    const copiedAt = Number(stored.lastCopiedClipboardAt || 0);
    if (!copiedAt || Date.now() - copiedAt > 30 * 60 * 1000) {
      return;
    }

    if (stored.lastSentClipboardText && stored.lastSentClipboardText === clipText) {
      return;
    }

    const parsed = parseClipboardDownloadText(clipText);
    if (!parsed || parsed.urls.length === 0) return;

    if (directAddContent && !directAddContent.classList.contains("show")) {
      directAddContent.classList.add("show");
      if (directAddArrow) directAddArrow.textContent = "▲";
    }

    manualUrlInput.value = parsed.urls.join("\n");
    if (parsed.password && manualPasswordInput && !manualPasswordInput.value.trim()) {
      manualPasswordInput.value = parsed.password;
    }
    autoFilledClipboardRawText = clipText;
    showPopupToast(`📋 복사한 ${parsed.urls.length}개의 다운로드 링크를 불러왔습니다.`);
  } catch (e) {
    // 저장된 클립보드 캐시 읽기 실패 등은 조용히 무시
  }
}

function clearSubfolderInputsAndContext() {
  if (manualSubfolderInput) manualSubfolderInput.value = "";
  if (scanSubfolderInput) scanSubfolderInput.value = "";
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

async function tryAutoFillRetroDbSubfolder() {
  try {
    if (!chrome.storage || !chrome.storage.local) return;
    const items = await new Promise((resolve) =>
      chrome.storage.local.get(["pendingRetroDbContext"], (data) => {
        void chrome.runtime.lastError;
        resolve(data || {});
      })
    );
    const ctx = items && items.pendingRetroDbContext;
    if (!ctx || typeof ctx !== "object" || !ctx.subfolder) return;
    if (typeof ctx.clickedAt !== "number" || Date.now() - ctx.clickedAt > 5 * 60 * 1000) {
      return;
    }
    if (manualSubfolderInput && !manualSubfolderInput.value.trim()) {
      manualSubfolderInput.value = ctx.subfolder;
    }
    if (scanSubfolderInput && !scanSubfolderInput.value.trim()) {
      scanSubfolderInput.value = ctx.subfolder;
    }
    // 1회성 적용: 팝업 입력창에 자동 지정 후 저장소의 컨텍스트는 즉시 제거하여 후속 다운로드/새 창 영향 방지
    try {
      chrome.storage.local.remove(["pendingRetroDbContext"], () => {
        void chrome.runtime.lastError;
      });
      chrome.runtime.sendMessage({ action: "clearRetroDbContext" }, () => {
        void chrome.runtime.lastError;
      });
    } catch (e) {}
  } catch (e) {}
}

// ==========================================
// URL 전송 관련
// ==========================================

function maybePromptSettingsOnSendError(err) {
  const msg = (err && err.message) || "";
  if (
    msg.includes("연결할 수 없습니다") ||
    msg.includes("인증 실패") ||
    msg.includes("서버 주소")
  ) {
    if (settingsModal) settingsModal.classList.add("show");
    if (typeof showTestResult === "function" && testResult) {
      showTestResult(false, `❌ ${msg}`);
    }
  }
}

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

  const subfolder = (manualSubfolderInput ? manualSubfolderInput.value : "").trim();
  sendCurrentTabBtn.disabled = true;
  const originalHtml = sendCurrentTabBtn.innerHTML;
  sendCurrentTabBtn.innerHTML = `<span>전송 중...</span>`;

  try {
    // 네이버 카페 게시글인 경우 브라우저 탭 세션으로 첨부파일 직접 추출 시도
    const isNaverCafe = /(?:cafe\.naver\.com|m\.cafe\.naver\.com)/i.test(tab.url);
    const cafeArticleMatch = tab.url.match(/cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?|ca-fe\/)/i);
    if (isNaverCafe && cafeArticleMatch && tab.id) {
      try {
        const scanResults = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: extractPageDownloadLinksInPage,
        });
        if (scanResults && scanResults[0] && Array.isArray(scanResults[0].result)) {
          const directFiles = scanResults[0].result.filter(
            (item) => item.url && (item.url.includes("downapi.cafe.naver.com") || item.filename)
          );
          if (directFiles.length > 0) {
            const items = directFiles.map((f) => ({
              url: f.url,
              filename: f.filename || f.title || "",
              subfolder: subfolder || f.subfolder || "",
              referer: tab.url,
              source_title: tab.title || "",
              source_url: tab.url,
            }));
            const resData = await sendBatchJobs(
              items,
              subfolder,
              tab.url,
              tab.title || "",
              tab.url
            );
            clearSubfolderInputsAndContext();
            sendCurrentTabBtn.innerHTML = `<span>✓ 전송 완료!</span>`;
            const addedCount = resData.added_count || items.length;
            showPopupToast(`✅ 네이버 카페 첨부파일 ${addedCount}개가 서버로 전송되었습니다!`);
            setTimeout(() => {
              sendCurrentTabBtn.disabled = false;
              sendCurrentTabBtn.innerHTML = originalHtml;
            }, 1400);

            switchToActiveTab();
            await fetchJobs();
            return;
          }
        }
      } catch (scanErr) {
        console.warn("네이버 카페 탭 내 첨부파일 사전 추출 실패, URL 직접 전송으로 대체:", scanErr);
      }
    }

    // AkiraBox 페이지인 경우 #download 요소 및 파일명 직접 검사
    const isAkiraBox = /akirabox\.(?:to|com|net)/i.test(tab.url);
    if (isAkiraBox && tab.id) {
      try {
        const akiraRes = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            let fileName = "";
            const h1 = document.querySelector("h1");
            if (h1 && h1.innerText && h1.innerText.trim()) {
              fileName = h1.innerText.trim();
            } else if (document.title) {
              fileName = document.title.replace(/\s*·\s*AkiraBox.*$/i, "").trim();
            }
            const btn = document.querySelector("#download, a.download-button");
            const hasDirect = btn && btn.href && btn.href.startsWith("http") && !btn.href.endsWith("#") && !btn.href.endsWith("/file");
            return {
              direct_url: hasDirect ? btn.href : "",
              filename: fileName,
            };
          },
        });
        if (akiraRes && akiraRes[0] && akiraRes[0].result) {
          const resObj = akiraRes[0].result;
          const targetLink = resObj.direct_url || tab.url;
          const resolvedFilename = resObj.filename || "";
          const resData = await sendUrl(targetLink, subfolder, "", "", tab.url, tab.title || "", tab.url, resolvedFilename);
          clearSubfolderInputsAndContext();
          sendCurrentTabBtn.innerHTML = `<span>✓ 전송 완료!</span>`;
          showPopupToast(`✅ AkiraBox 작업이 서버로 전송되었습니다! (${resolvedFilename || "전송 완료"})`);
          setTimeout(() => {
            sendCurrentTabBtn.disabled = false;
            sendCurrentTabBtn.innerHTML = originalHtml;
          }, 1400);

          switchToActiveTab();
          await fetchJobs();
          return;
        }
      } catch (akiraErr) {
        console.warn("AkiraBox 직링크 사전 추출 건너뜀:", akiraErr);
      }
    }

    const resData = await sendUrl(tab.url, subfolder, "", "", tab.url, tab.title || "", tab.url);
    clearSubfolderInputsAndContext();
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
    maybePromptSettingsOnSendError(err);
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
    try {
      const sentClipVal = autoFilledClipboardRawText || raw;
      if (sentClipVal && chrome.storage && chrome.storage.local) {
        chrome.storage.local.set({ lastSentClipboardText: sentClipVal });
      }
    } catch (e) {}
    autoFilledClipboardRawText = "";
    manualUrlInput.value = "";
    clearSubfolderInputsAndContext();
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
    maybePromptSettingsOnSendError(err);
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

async function sendUrl(targetUrl, subfolder = "", password = "", teraboxMode = "", referer = "", sourceTitle = "", sourceUrl = "", filename = "", cookies = "") {
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
  if (filename) {
    payload.filename = filename;
  }
  if (cookies) {
    payload.cookies = cookies;
  }

  let res;
  try {
    res = await fetchWithTimeout(
      `${serverUrl}/api/jobs`,
      {
        method: "POST",
        credentials: "omit",
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
      },
      4000
    );
  } catch (networkErr) {
    throw new Error("서버에 연결할 수 없습니다. 서버 IP 설정을 확인하세요.");
  }

  const data = await res.json().catch(() => ({}));

  if (res.status === 401) {
    throw new Error("비밀번호 인증 실패 (설정을 확인하세요)");
  }

  if (!res.ok || !data.success) {
    throw new Error(data.error || `서버 에러 (${res.status})`);
  }

  return data;
}

async function sendBatchJobs(items, subfolder = "", referer = "", sourceTitle = "", sourceUrl = "") {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  if (!serverUrl) {
    throw new Error("서버 주소를 먼저 설정해 주세요.");
  }

  const payload = { items: items };
  if (subfolder) {
    payload.subfolder = subfolder;
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

  let res;
  try {
    res = await fetchWithTimeout(
      `${serverUrl}/api/jobs`,
      {
        method: "POST",
        credentials: "omit",
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
      },
      8000
    );
  } catch (networkErr) {
    throw new Error("서버에 연결할 수 없습니다. 서버 IP 설정을 확인하세요.");
  }

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
async function extractPageDownloadLinksInPage() {
  const links = [];
  const seenUrls = new Set();
  const currentUrl = window.location.href;

  const HOSTING_PATTERNS = [
    { service: "MEGA", regex: /mega\.(?:nz|co\.nz|io)\/(?:file\/|#?!)/i },
    { service: "TeraBox", regex: /(?:1024terabox|terabox|teraboxapp|freeterabox)\.(?:com|app)/i },
    { service: "GoFile", regex: /gofile\.io\/d\//i },
    { service: "1fichier", regex: /1fichier\.com\/\?/i },
    { service: "Transfer.it", regex: /transfer\.it\/t\//i },
    { service: "GigaFile", regex: /(?:gigafile\.nu|xgf\.nu)/i },
    { service: "kio.ac", regex: /kio\.ac\/[cf]\//i },
    { service: "Naver MYBOX", regex: /mybox\.naver\.com\/share\//i },
    { service: "Naver Cafe", regex: /(?:downapi\.cafe\.naver\.com|cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?|ca-fe\/))/i },
    { service: "Tistory", regex: /(?:tistory\.com\/attachment|kakaocdn\.net\/dna?|daumcdn\.net)/i },
    { service: "Rootz", regex: /rootz\.so\/d\//i },
    { service: "GitHub", regex: /^https?:\/\/(?:(?:www\.)?github\.com\/(?!(?:settings|notifications|explore|trending|topics|marketplace|sponsors|login|signup|join|orgs|organizations|users|search|features|pricing|enterprise|about|security|customer-stories|readme|collections|events|codespaces|copilot|pulls|issues|site|solutions|resources|account|apps|dashboard)(?:\/|$|\?|#))[^\/\s?#]+\/[^\/\s?#]+(?:\/(?:releases|archive|zipball|tarball|raw|blob|tree)(?:\/|$|\?|#)|\/?(?:$|\?|#))|(?:raw|objects|release-assets|gist)\.githubusercontent\.com\/[^\/\s]+\/|codeload\.github\.com\/[^\/\s]+\/)/i },
    { service: "Romsfun", regex: /romsfun\.com\/download\//i },
    { service: "Romspure", regex: /romspure\.cc\/(?:download|roms)\//i },
    { service: "WoWroms", regex: /wowroms\.com\//i },
    { service: "PlanetEmu", regex: /planetemu\.net\/(?:[a-z]{2}\/)?(?:roms?\/[^\/?#]+\/[^\/?#]+|download\/rom)/i },
    { service: "Vimm's Lair", regex: /vimm\.net\/vault\//i },
    { service: "X-Share", regex: /x-share\.net\/[sb]\//i },
    { service: "Google Drive", regex: /(?:drive|docs|drive\.usercontent)\.google\.com\/(?:file\/(?:u\/\d+\/)?d\/|open\?id=|uc\?(?:[^#\s]*&)?id=|drive\/(?:u\/\d+\/)?folders\/|download\?id=)/i },
    { service: "MediaFire", regex: /mediafire\.com\/(?:file|download|view|\?)/i },
    { service: "FileKeeper", regex: /filekeeper\.net\/[a-zA-Z0-9_-]+/i },
    { service: "DataNodes", regex: /datanodes\.to\/[a-zA-Z0-9_-]+/i },
    { service: "Buzzheavier", regex: /(?:buzzheavier\.com|bzzhr\.to)\/[a-zA-Z0-9_-]+/i },
    { service: "AkiraBox", regex: /akirabox\.(?:to|com|net)\/(?:[a-zA-Z0-9_-]+\/file|[a-zA-Z0-9_-]+)/i },
    { service: "VikingFile", regex: /(?:vik[i1]ngf[i1]le\.[a-z]{2,})\/[fd]\//i },
    { service: "MegaUp", regex: /megaup\.net\//i },
    { service: "Pixeldrain", regex: /pixeldrain\.com\/u\//i },
    { service: "TeeTeeTee", regex: /teeteetee-korean\.com\/(?:projects\/[a-zA-Z0-9_\-]+|api\/patch-releases|api\/[a-z0-9_\-]+-xdelta|downloads\/)/i },
    { service: "Hangulteo", regex: /hangulteo\.com\/[a-zA-Z0-9_\-]+/i },
  ];

  const FILE_EXT_REGEX = /\.(zip|rar|7z|tar|gz|iso|bin|cue|apk|exe|msi|dmg|pkg|xdelta|bps|ips|ups|ppf|patch|mp4|mkv|mp3|flac|torrent)(\?.*)?$/i;

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
    return str.replace(/\s+/g, " ").replace(/^[\s▼▶▷►\-_]+/, "").trim();
  }

  function extractCleanAnchorTitle(a) {
    const explicit = a.getAttribute("download") || a.getAttribute("data-ke-file-name") || a.getAttribute("data-filename") || "";
    if (explicit.trim()) return cleanTitle(explicit);
    const nameEl = a.querySelector(".filename .name, span.name, .se-file-name, .file_name, .filename");
    if (nameEl && (nameEl.innerText || "").trim()) {
      return cleanTitle(nameEl.innerText);
    }
    try {
      const clone = a.cloneNode(true);
      clone.querySelectorAll(".size, .file-size, .filesize").forEach((el) => el.remove());
      const rawClean = cleanTitle(clone.textContent || a.getAttribute("title") || "");
      if (rawClean) {
        return rawClean.replace(/(\.[a-zA-Z0-9]{1,8}(?:\.\d{3})?)(?:\s*\d+(?:\.\d+)?\s*[KMGTPE]i?B|\s+\d+(?:\.\d+)?\s*B)$/i, "$1").trim();
      }
    } catch (e) {}
    return cleanTitle(a.innerText || a.getAttribute("title") || "");
  }

  const isNaverCafe = /(?:cafe\.naver\.com|m\.cafe\.naver\.com)/i.test(currentUrl);

  // 1. 현재 열린 페이지 자체가 네이버 카페 게시글인 경우:
  const cafeArticleMatch = currentUrl.match(/cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?|ca-fe\/)/i);
  if (isNaverCafe && cafeArticleMatch) {
    let articleTitle = document.title ? document.title.replace(/\s*:\s*네이버\s*카페\s*$/i, "").trim() : "";
    let clubId = "";
    let articleId = "";
    let cafeName = "";

    try {
      const urlObj = new URL(currentUrl);
      clubId = urlObj.searchParams.get("clubid") || urlObj.searchParams.get("clubId") || "";
      articleId = urlObj.searchParams.get("articleid") || urlObj.searchParams.get("articleId") || "";

      if (!clubId || !articleId) {
        const mCaFe = currentUrl.match(/\/cafes\/(\d+)\/articles\/(\d+)/i);
        if (mCaFe) {
          clubId = mCaFe[1];
          articleId = mCaFe[2];
        }
      }

      if (!articleId) {
        const mPath = currentUrl.match(/cafe\.naver\.com\/([A-Za-z0-9_-]+)\/(\d+)/i);
        if (mPath) {
          cafeName = mPath[1];
          articleId = mPath[2];
        }
      }
    } catch (e) {}

    // iframe#cafe_main 확인
    try {
      const iframe = document.querySelector('iframe#cafe_main, iframe#mainFrame, iframe[name="cafe_main"]');
      if (iframe) {
        if (iframe.src) {
          const mIframeClub = iframe.src.match(/clubid=(\d+)/i);
          if (mIframeClub && !clubId) clubId = mIframeClub[1];
          const mIframeArt = iframe.src.match(/articleid=(\d+)/i);
          if (mIframeArt && !articleId) articleId = mIframeArt[1];
        }
        if (iframe.contentDocument) {
          const subElem = iframe.contentDocument.querySelector(".title_text, h3.title, .article_header .title");
          if (subElem && subElem.innerText && subElem.innerText.trim()) {
            articleTitle = subElem.innerText.trim();
          }
        }
      }
    } catch (e) {}

    // window.g_sClubId 또는 script 태그 내 clubid 검색
    if (!clubId) {
      try {
        if (typeof window.g_sClubId !== "undefined" && window.g_sClubId) {
          clubId = String(window.g_sClubId);
        }
      } catch (e) {}
    }
    if (!clubId) {
      try {
        const scripts = Array.from(document.querySelectorAll("script"));
        for (const s of scripts) {
          const m = (s.textContent || "").match(/(?:clubid|club_id|g_sClubId)\s*[:=]\s*["']?(\d+)/i);
          if (m) {
            clubId = m[1];
            break;
          }
        }
      } catch (e) {}
    }

    // 여전히 clubId가 없고 cafeName이 있다면 카페 홈에서 clubId 파싱 시도
    if (!clubId && cafeName && cafeName.toLowerCase() !== "articleread.nhn") {
      try {
        const homeResp = await fetch("https://cafe.naver.com/" + cafeName, { credentials: "include" });
        if (homeResp.ok) {
          const homeHtml = await homeResp.text();
          const m = homeHtml.match(/(?:clubid|club_id|g_sClubId)\s*[:=]\s*["']?(\d+)/i);
          if (m) clubId = m[1];
        }
      } catch (e) {}
    }

    // 브라우저 탭 세션 쿠키를 이용해 네이버 카페 API 직접 호출하여 첨부파일 목록 일괄 추출
    let directFilesFound = 0;
    if (clubId && articleId) {
      try {
        const apiUrl = `https://apis.naver.com/cafe-web/cafe-articleapi/v2.1/cafes/${clubId}/articles/${articleId}`;
        const resp = await fetch(apiUrl, { credentials: "include" });
        if (resp.ok) {
          const data = await resp.json();
          const res = (data && data.result) || {};
          const art = res.article || {};
          if (art.subject && art.subject.trim()) {
            articleTitle = art.subject.trim();
          }
          const attaches = res.attaches || [];
          for (const att of attaches) {
            if (att.type === "F" && att.url) {
              const fileName = att.fullName || att.name || "attachment.dat";
              const fileUrl = att.url;
              if (!seenUrls.has(fileUrl)) {
                seenUrls.add(fileUrl);
                directFilesFound++;
                let sizeStr = "";
                if (typeof att.size === "number" && att.size > 0) {
                  const mb = att.size / (1024 * 1024);
                  sizeStr = mb >= 1 ? ` (${mb.toFixed(1)}MB)` : ` (${Math.round(att.size / 1024)}KB)`;
                }
                links.push({
                  url: fileUrl,
                  title: `${fileName}${sizeStr}`,
                  filename: fileName,
                  service: "Naver Cafe",
                  password: "",
                  subfolder: articleTitle,
                  size: att.size || 0,
                });
              }
            }
          }
        }
      } catch (e) {
        console.warn("Naver Cafe API scan error:", e);
      }
    }

    const cleanArticleUrl = currentUrl.split("?")[0].replace(/\/+$/, "") + (currentUrl.includes("?") ? "?" + currentUrl.split("?")[1] : "");
    if (!seenUrls.has(cleanArticleUrl)) {
      seenUrls.add(cleanArticleUrl);
      links.push({
        url: currentUrl,
        title: `[네이버 카페 첨부파일] ${articleTitle || "게시글 첨부파일 일괄"}`,
        service: "Naver Cafe",
        password: "",
        subfolder: articleTitle,
      });
    }
  }

  // 1-1. 현재 열린 페이지가 티스토리 게시글인 경우:
  const isTistory = /(?:\.tistory\.com)/i.test(currentUrl);
  const tistoryArticleMatch = currentUrl.match(/\.tistory\.com\/(?:(?:m\/)?\d+|entry\/[^\s\/?#]+)/i);
  if (isTistory && tistoryArticleMatch) {
    let articleTitle = "";
    try {
      const ogTitleElem = document.querySelector('meta[property="og:title"]');
      if (ogTitleElem && ogTitleElem.content) {
        articleTitle = ogTitleElem.content.trim();
      } else {
        const tElem = document.querySelector(".title_post, h1.title, .entry-title, h2.title, .article_header .title");
        if (tElem && tElem.innerText.trim()) {
          articleTitle = tElem.innerText.trim();
        } else if (document.title) {
          articleTitle = document.title.replace(/\s*::\s*[^:]+$/i, "").replace(/\s*-\s*[^-]+$/i, "").trim();
        }
      }
    } catch (e) {}

    const cleanArticleUrl = currentUrl.split("?")[0].replace(/\/+$/, "") + (currentUrl.includes("?") ? "?" + currentUrl.split("?")[1] : "");
    if (!seenUrls.has(cleanArticleUrl)) {
      seenUrls.add(cleanArticleUrl);
      links.push({
        url: currentUrl,
        title: `[티스토리 첨부/링크] ${articleTitle || "게시글 다운로드"}`,
        service: "Tistory",
        password: "",
      });
    }
  }

  // 1-2. 현재 열린 페이지가 블로그스팟(Blogspot/Blogger) 게시글인 경우:
  const isBlogspot = /(?:blogspot\.(?:com|kr)|blogger\.com)/i.test(currentUrl);
  const blogspotArticleMatch = currentUrl.match(/blogspot\.(?:com|kr)\/\d{4}\/\d{2}\/[^\s\/?#]+\.html/i);
  if (isBlogspot && blogspotArticleMatch) {
    let articleTitle = "";
    try {
      const ogTitleElem = document.querySelector('meta[property="og:title"]');
      if (ogTitleElem && ogTitleElem.content) {
        articleTitle = ogTitleElem.content.trim();
      } else {
        const tElem = document.querySelector(".post-title, h1.post-title, h3.post-title, .entry-title, h1");
        if (tElem && tElem.innerText.trim()) {
          articleTitle = tElem.innerText.trim();
        } else if (document.title) {
          articleTitle = document.title.replace(/\s*[-|–].*$/i, "").trim();
        }
      }
    } catch (e) {}

    const cleanArticleUrl = currentUrl.split("?")[0].replace(/\/+$/, "") + (currentUrl.includes("?") ? "?" + currentUrl.split("?")[1] : "");
    if (!seenUrls.has(cleanArticleUrl)) {
      seenUrls.add(cleanArticleUrl);
      links.push({
        url: currentUrl,
        title: `[블로그스팟 파일 검출] ${articleTitle || "게시글 파일 다운로드"}`,
        service: "WebPage Post",
        password: "",
        subfolder: articleTitle,
      });
    }
  }

  // 1-2. 현재 열린 페이지 자체가 지원되는 호스팅/클라우드/ROM 다운로드 페이지인 경우 최상단 항목으로 자동 추가
  const ghRepoPageMatch = currentUrl.match(/^https?:\/\/(?:www\.)?github\.com\/([^\/\s?#]+\/[^\/\s?#]+)/i);
  const isGitHubPage = Boolean(ghRepoPageMatch);
  const currentGhRepo = ghRepoPageMatch ? ghRepoPageMatch[1].toLowerCase().replace(/\.git$/i, "") : "";

  function shouldSkipGitHubLink(targetUrl, anchorEl) {
    if (!isGitHubPage) return false;
    if (anchorEl && anchorEl.closest("header, nav, footer, .Header, .AppHeader, .footer, .BorderGrid-cell, .Layout-sidebar")) {
      return true;
    }
    const ghLinkMatch = targetUrl.match(/^https?:\/\/(?:www\.)?github\.com\/([^\/\s?#]+\/[^\/\s?#]+)(?:\/([^\/\s?#]+))?/i);
    if (!ghLinkMatch) return false;
    const linkRepo = ghLinkMatch[1].toLowerCase().replace(/\.git$/i, "");
    if (currentGhRepo && linkRepo !== currentGhRepo) {
      return true;
    }
    const linkSub = (ghLinkMatch[2] || "").toLowerCase();
    if (!linkSub || linkSub === "tree" || linkSub === "blob") {
      return true;
    }
    return false;
  }

  const currentHostingService = identifyService(currentUrl);
  if (
    currentHostingService &&
    currentHostingService !== "Direct Link" &&
    currentHostingService !== "Direct File" &&
    !isNaverCafe &&
    !isTistory
  ) {
    let pageTitle = "";
    try {
      // PlanetEmu / Vimm / ROM 전용 폼 버튼 텍스트 추출 시도
      const romBtn = document.querySelector('form.downloadForm input[type="submit"], form.downloadForm button, form#dl-form button, a#downloadButton, a[aria-label="Download file"]');
      if (romBtn) {
        const btnVal = (romBtn.value || romBtn.innerText || "").trim().replace(/^(?:Download|Télécharger|Telecharger)\s+/i, "");
        if (btnVal) pageTitle = btnVal;
      }
      if (!pageTitle && isGitHubPage && currentGhRepo) {
        const ghName = document.querySelector("#repository-container-header strong a, h1.d-inline");
        if (ghName && ghName.innerText && ghName.innerText.trim()) {
          pageTitle = ghName.innerText.trim();
        } else {
          pageTitle = currentGhRepo;
        }
      }
      if (!pageTitle) {
        const h1 = document.querySelector("h1, h2, .title, #title");
        if (h1 && h1.innerText && h1.innerText.trim()) {
          pageTitle = h1.innerText.trim();
        } else if (document.title) {
          pageTitle = document.title.replace(/\s*[-|–].*$/i, "").trim();
        }
      }
    } catch (e) {}

    const cleanCurrentUrl = currentUrl.split("#")[0];
    if (!seenUrls.has(cleanCurrentUrl)) {
      seenUrls.add(cleanCurrentUrl);
      links.push({
        url: cleanCurrentUrl,
        title: `[${currentHostingService}] ${pageTitle || document.title || "게임/파일 다운로드"}`,
        service: currentHostingService,
        password: "",
      });
    }
  }

  // 검색할 도큐먼트 목록 (최상위 document + iframe)
  const docsToScan = [document];
  try {
    const iframes = Array.from(document.querySelectorAll("iframe"));
    for (const iframe of iframes) {
      if (iframe.contentDocument && iframe.contentDocument.body) {
        docsToScan.push(iframe.contentDocument);
      }
    }
  } catch (e) {}

  for (const doc of docsToScan) {
    // 1. 네이버 카페 전용 첨부파일 요소 정밀 스캔
    const cafeAttachLinks = Array.from(
      doc.querySelectorAll(
        'a[href*="downapi.cafe.naver.com"], a[href*="FileDownload"], a[href*="blogattach.naver.net"], a.link_file, a.btn_file, .attach_box a, .list_file a, li.AttachFileListItem a.file_menu[href], .se-module-file a'
      )
    );
    for (const a of cafeAttachLinks) {
      const href = (a.href || "").trim();
      if (!href || href.startsWith("javascript:") || href.startsWith("#")) continue;
      if (seenUrls.has(href)) continue;
      seenUrls.add(href);

      let title = "";
      const liItem = a.closest("li.AttachFileListItem, .AttachFileListItem");
      if (liItem) {
        const nameEl = liItem.querySelector(".file_name .text, .file_name, .name");
        if (nameEl && nameEl.innerText && nameEl.innerText.trim()) {
          title = nameEl.innerText.trim();
        }
      }
      if (!title) {
        title = extractCleanAnchorTitle(a) || href.split("/").pop().split("?")[0] || href;
      }

      links.push({
        url: href,
        title: title,
        filename: title,
        service: "Naver Cafe",
        password: "",
      });
    }

    // 2. 일반 <a> 태그 스캔
    const anchors = Array.from(doc.querySelectorAll("a[href]"));
    for (const a of anchors) {
      const href = (a.href || "").trim();
      if (!href || href.startsWith("javascript:") || href.startsWith("#")) continue;

      if (isNaverCafe && /cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?)/i.test(href)) {
        continue;
      }
      if (shouldSkipGitHubLink(href, a)) {
        continue;
      }

      const isMagnet = href.startsWith("magnet:?");
      const isHosting = HOSTING_PATTERNS.some((p) => p.regex.test(href));
      const isFile = FILE_EXT_REGEX.test(href);

      if (isMagnet || isHosting || isFile) {
        if (!seenUrls.has(href)) {
          seenUrls.add(href);
          let leafName = href.split("/").pop().split("?")[0] || href;
          try {
            leafName = decodeURIComponent(leafName);
          } catch (e) {}
          const title = extractCleanAnchorTitle(a) || leafName || href;

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

      if (isNaverCafe && /cafe\.naver\.com\/(?:[A-Za-z0-9_-]+\/\d+|ArticleRead\.nhn\?)/i.test(rawUrl)) {
        continue;
      }
      if (shouldSkipGitHubLink(rawUrl, null)) {
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
          title: cleanTitle(rawUrl.split("/").pop().split("?")[0]) || rawUrl,
          service: identifyService(rawUrl),
          password: password,
        });
      }
    }

    // 3-2. 본문 Base64 인코딩 링크 탐지 (단일 문자열 or "URL | 파일명" or "aHR0c...")
    const b64Regex = /(?:[A-Za-z0-9+/_-]{16,}={0,2})/g;
    let b64Match;
    while ((b64Match = b64Regex.exec(bodyText)) !== null) {
      const candidate = b64Match[0];
      try {
        let normalized = candidate.replace(/-/g, "+").replace(/_/g, "/");
        while (normalized.length % 4 !== 0) {
          normalized += "=";
        }
        const decoded = atob(normalized);
        if (
          decoded &&
          (decoded.includes("http://") ||
            decoded.includes("https://") ||
            decoded.includes("magnet:?"))
        ) {
          const innerUrls = decoded.match(/(?:https?:\/\/[^\s<>"'`|]+|magnet:\?xt=[^\s<>"'`|]+)/g);
          if (innerUrls) {
            for (const decUrl of innerUrls) {
              if (seenUrls.has(decUrl)) continue;
              seenUrls.add(decUrl);

              let password = "";
              const startIdx = Math.max(0, b64Match.index - 50);
              const endIdx = Math.min(bodyText.length, b64Match.index + candidate.length + 100);
              const ctxText = bodyText.substring(startIdx, endIdx);
              const pwMatch = ctxText.match(
                /(?:비번|암호|비밀번호|추출\s*코드|pwd|pw|pass|code)\s*[:=]?\s*([a-zA-Z0-9_!@#$%^&*-]{2,32})/i
              );
              if (pwMatch) {
                password = pwMatch[1].trim();
              }

              links.push({
                url: decUrl,
                title: `[Base64] ${cleanTitle(decUrl.split("/").pop().split("?")[0]) || decUrl}`,
                service: identifyService(decUrl),
                password: password,
              });
            }
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
  let lastError = null;

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
      await sendUrl(
        item.url,
        subfolder || item.subfolder || "",
        password,
        "",
        referer,
        sourceTitle || item.title,
        referer,
        item.filename || ""
      );
      successCount++;
    } catch (e) {
      failCount++;
      lastError = e;
      console.warn("스캔 항목 전송 실패:", item.url, e);
    }
  }

  sendScannedBtn.disabled = false;
  sendScannedBtn.innerHTML = originalHtml;

  if (successCount > 0) {
    clearSubfolderInputsAndContext();
    showPopupToast(`✅ ${successCount}개 항목이 서버로 전송되었습니다!${failCount > 0 ? ` (${failCount}개 실패)` : ""}`);
    if (scanSection) scanSection.classList.add("hidden");
    switchToActiveTab();
    await fetchJobs();
  } else {
    showPopupToast(
      `전송 실패: ${(lastError && lastError.message) || "서버 연결 상태를 확인해 주세요."}`,
      true
    );
    maybePromptSettingsOnSendError(lastError);
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
 * 디스크 상의 manifest.json 버전을 캐시 없이 읽어옵니다.
 */
async function getDiskManifestVersion() {
  try {
    const manifestUrl = chrome.runtime.getURL(`manifest.json?_t=${Date.now()}`);
    const res = await fetch(manifestUrl, { cache: "no-store" });
    if (!res.ok) return "";
    const data = await res.json();
    return (data && data.version ? String(data.version) : "").trim();
  } catch (e) {
    return "";
  }
}

function getLatestExtensionZipUrl() {
  if (latestReleaseData && Array.isArray(latestReleaseData.assets)) {
    const zipAsset = latestReleaseData.assets.find(
      (a) => a && a.name === "RemoteDownloader-Extension.zip" && a.browser_download_url
    );
    if (zipAsset) {
      return zipAsset.browser_download_url;
    }
  }
  return "https://github.com/x220volt/RmtDownloader/releases/latest/download/RemoteDownloader-Extension.zip";
}

function openUpdaterDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("rd_ext_updater_db", 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("handles")) {
        db.createObjectStore("handles");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error("IndexedDB 열기 실패"));
  });
}

async function getSavedExtDirHandle() {
  try {
    const db = await openUpdaterDb();
    return await new Promise((resolve) => {
      const tx = db.transaction("handles", "readonly");
      const req = tx.objectStore("handles").get("extDirHandle");
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
      tx.oncomplete = () => db.close();
    });
  } catch (e) {
    return null;
  }
}

async function saveExtDirHandle(handle) {
  try {
    const db = await openUpdaterDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction("handles", "readwrite");
      tx.objectStore("handles").put(handle, "extDirHandle");
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error);
      };
    });
  } catch (e) {
    console.warn("확장 폴더 핸들 저장 실패:", e);
  }
}

async function clearSavedExtDirHandle() {
  try {
    const db = await openUpdaterDb();
    await new Promise((resolve) => {
      const tx = db.transaction("handles", "readwrite");
      tx.objectStore("handles").delete("extDirHandle");
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        resolve();
      };
    });
  } catch (e) {}
}

function base64ToUint8Array(b64) {
  const bin = atob(b64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = bin.charCodeAt(i);
  }
  return bytes;
}

function validateExtRelPath(rawPath) {
  const normalized = String(rawPath || "").replace(/\\/g, "/");
  if (!normalized || normalized.startsWith("/") || normalized.includes(":")) {
    throw new Error(`허용되지 않는 압축 경로입니다: ${rawPath}`);
  }
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length === 0 || parts.some((p) => p === "." || p === "..")) {
    throw new Error(`잘못된 압축 경로입니다: ${rawPath}`);
  }
  const filename = parts[parts.length - 1];
  const dotIdx = filename.lastIndexOf(".");
  const ext = dotIdx >= 0 ? filename.slice(dotIdx).toLowerCase() : "";
  if (!EXT_ALLOWED_EXTENSIONS.has(ext)) {
    throw new Error(`허용되지 않는 확장자입니다: ${filename}`);
  }
  return parts.join("/");
}

async function extractZipArrayBuffer(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  const view = new DataView(arrayBuffer);
  const minPos = Math.max(0, bytes.length - 65557);

  let eocdOffset = -1;
  for (let i = bytes.length - 22; i >= minPos; i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      const commentLen = view.getUint16(i + 20, true);
      if (i + 22 + commentLen === bytes.length) {
        eocdOffset = i;
        break;
      }
    }
  }
  if (eocdOffset < 0) {
    throw new Error("유효한 ZIP 아카이브(EOCD)를 찾을 수 없습니다.");
  }

  const totalEntries = view.getUint16(eocdOffset + 10, true);
  let ptr = view.getUint32(eocdOffset + 16, true);
  const decoder = new TextDecoder("utf-8");
  const filesMap = {};

  for (let idx = 0; idx < totalEntries; idx++) {
    if (ptr + 46 > bytes.length || view.getUint32(ptr, true) !== 0x02014b50) {
      throw new Error("ZIP 중앙 디렉터리 헤더가 손상되었습니다.");
    }
    const compressionMethod = view.getUint16(ptr + 10, true);
    const compressedSize = view.getUint32(ptr + 20, true);
    const fileNameLen = view.getUint16(ptr + 28, true);
    const extraLen = view.getUint16(ptr + 30, true);
    const commentLen = view.getUint16(ptr + 32, true);
    const localHeaderOffset = view.getUint32(ptr + 42, true);

    const rawName = decoder.decode(bytes.subarray(ptr + 46, ptr + 46 + fileNameLen));
    ptr += 46 + fileNameLen + extraLen + commentLen;

    const cleanRaw = rawName.replace(/\\/g, "/");
    if (cleanRaw.endsWith("/")) {
      continue;
    }

    const safeRelPath = validateExtRelPath(cleanRaw);
    if (
      localHeaderOffset + 30 > bytes.length ||
      view.getUint32(localHeaderOffset, true) !== 0x04034b50
    ) {
      throw new Error("ZIP 로컬 파일 헤더가 손상되었습니다.");
    }
    const lfNameLen = view.getUint16(localHeaderOffset + 26, true);
    const lfExtraLen = view.getUint16(localHeaderOffset + 28, true);
    const dataStart = localHeaderOffset + 30 + lfNameLen + lfExtraLen;
    if (dataStart + compressedSize > bytes.length) {
      throw new Error(`ZIP 엔트리 크기가 올바르지 않습니다: ${safeRelPath}`);
    }
    const compData = bytes.subarray(dataStart, dataStart + compressedSize);

    if (compressionMethod === 0) {
      filesMap[safeRelPath] = new Uint8Array(compData);
    } else if (compressionMethod === 8) {
      const ds = new DecompressionStream("deflate-raw");
      const decompressedStream = new Blob([compData]).stream().pipeThrough(ds);
      const decompressedBuf = await new Response(decompressedStream).arrayBuffer();
      filesMap[safeRelPath] = new Uint8Array(decompressedBuf);
    } else {
      throw new Error(`지원하지 않는 ZIP 압축 방식(${compressionMethod})입니다.`);
    }
  }

  if (Object.keys(filesMap).length === 0) {
    throw new Error("확장 프로그램 ZIP 패키지가 비어 있습니다.");
  }
  return filesMap;
}

async function fetchLatestExtensionFilesMap(serverUpdateData) {
  if (serverUpdateData && serverUpdateData.files && typeof serverUpdateData.files === "object") {
    const entries = Object.entries(serverUpdateData.files);
    if (entries.length > 0) {
      const map = {};
      for (const [relPath, b64Content] of entries) {
        const safeRel = validateExtRelPath(relPath);
        map[safeRel] = base64ToUint8Array(b64Content);
      }
      return map;
    }
  }

  const downloadUrl = getLatestExtensionZipUrl();
  const res = await fetch(downloadUrl, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`GitHub 확장 패키지 다운로드 실패 (${res.status})`);
  }
  const arrayBuffer = await res.arrayBuffer();
  return await extractZipArrayBuffer(arrayBuffer);
}

async function resolveExtensionTargetHandle(rootDirHandle) {
  try {
    await rootDirHandle.getFileHandle("manifest.json");
    return rootDirHandle;
  } catch (e) {}

  for (const subName of ["RemoteDownloader-Extension", "extension"]) {
    try {
      const subHandle = await rootDirHandle.getDirectoryHandle(subName);
      await subHandle.getFileHandle("manifest.json");
      return subHandle;
    } catch (e) {}
  }

  throw new Error(
    "선택한 폴더에서 manifest.json을 찾을 수 없습니다. 크롬에 로드된 RemoteDownloader-Extension 폴더를 선택해 주세요."
  );
}

async function writeFilesToDirectoryHandle(rootDirHandle, filesMap) {
  const targetHandle = await resolveExtensionTargetHandle(rootDirHandle);

  for (const [relPath, contentBytes] of Object.entries(filesMap)) {
    const parts = relPath.split("/").filter(Boolean);
    let dir = targetHandle;
    for (let i = 0; i < parts.length - 1; i++) {
      dir = await dir.getDirectoryHandle(parts[i], { create: true });
    }
    const fileHandle = await dir.getFileHandle(parts[parts.length - 1], { create: true });
    const writable = await fileHandle.createWritable();
    await writable.write(contentBytes);
    await writable.close();
  }

  return targetHandle;
}

function finalizeExtensionReload(versionLabel) {
  showPopupToast(`✅ 확장 프로그램(${versionLabel || "최신"}) 파일 갱신 완료! 재로드합니다...`);
  chrome.storage.local.remove(["lastUpdateCheckTime", "cachedReleaseInfo"]).catch(() => {});
  setTimeout(() => {
    try {
      chrome.runtime.reload();
    } catch (e) {
      window.location.reload();
    }
  }, 900);
}

async function openRemoteUpdateModal() {
  if (!remoteUpdateModal) return;
  const savedHandle = await getSavedExtDirHandle();
  if (selectExtFolderUpdateBtn) {
    selectExtFolderUpdateBtn.disabled = false;
    selectExtFolderUpdateBtn.textContent = savedHandle
      ? `⚡ 저장된 폴더(${savedHandle.name})로 즉시 업데이트`
      : "📁 확장 폴더 선택 후 즉시 업데이트";
  }
  if (reselectExtFolderBtn) {
    reselectExtFolderBtn.classList.toggle("hidden", !savedHandle);
  }
  if (remoteUpdateDesc) {
    const targetTag =
      (pendingServerUpdateData && pendingServerUpdateData.version) ||
      (latestReleaseData && latestReleaseData.tag_name) ||
      "최신 버전";
    remoteUpdateDesc.textContent = savedHandle
      ? `원격 서버 환경에서 ${targetTag} 업데이트를 적용하려면 이전에 선택한 '${savedHandle.name}' 폴더 쓰기 권한을 허용해 주세요.`
      : `원격 서버에 연결되어 있거나 브라우저 확장 폴더가 자동 갱신되지 않았습니다. 내 PC의 확장 폴더를 선택하면 ${targetTag} 파일을 직접 덮어쓰고 재로드합니다.`;
  }
  remoteUpdateModal.classList.add("show");
}

async function performClientFolderUpdate(dirHandle, isInteractive = true) {
  const filesMap = await fetchLatestExtensionFilesMap(pendingServerUpdateData);
  const resolvedHandle = await writeFilesToDirectoryHandle(dirHandle, filesMap);
  await saveExtDirHandle(resolvedHandle);

  const currentVersion = (chrome.runtime.getManifest().version || "0.0.0").trim();
  const diskVer = await getDiskManifestVersion();
  const targetVer =
    (pendingServerUpdateData && pendingServerUpdateData.version) ||
    (latestReleaseData && latestReleaseData.tag_name) ||
    "";

  if (
    diskVer &&
    (diskVer !== currentVersion || (targetVer && !isNewerVersion(diskVer, targetVer)))
  ) {
    if (remoteUpdateModal) remoteUpdateModal.classList.remove("show");
    finalizeExtensionReload(targetVer || `v${diskVer}`);
    return true;
  }

  if (isInteractive) {
    throw new Error(
      "선택한 폴더에 파일은 복사되었으나 현재 크롬에 로드된 확장 폴더(manifest.json)가 바뀌지 않았습니다. chrome://extensions 에서 실제 경로를 확인해 주세요."
    );
  }
  return false;
}

async function handleSelectFolderUpdateClick(forcePicker = false) {
  if (!window.showDirectoryPicker) {
    showPopupToast("현재 브라우저에서 폴더 직접 쓰기를 지원하지 않습니다. ZIP 다운로드를 이용해 주세요.", true);
    return;
  }

  const originalText = selectExtFolderUpdateBtn ? selectExtFolderUpdateBtn.textContent : "";
  if (selectExtFolderUpdateBtn) {
    selectExtFolderUpdateBtn.disabled = true;
    selectExtFolderUpdateBtn.textContent = "⏳ 최신 파일 다운로드 및 덮어쓰기 중...";
  }
  if (reselectExtFolderBtn) {
    reselectExtFolderBtn.disabled = true;
  }

  try {
    let dirHandle = null;
    if (!forcePicker) {
      const saved = await getSavedExtDirHandle();
      if (saved) {
        const perm = await saved.requestPermission({ mode: "readwrite" });
        if (perm === "granted") {
          dirHandle = saved;
        }
      }
    }

    if (!dirHandle) {
      dirHandle = await window.showDirectoryPicker({
        id: "rd_ext_dir",
        mode: "readwrite",
      });
    }

    await performClientFolderUpdate(dirHandle, true);
  } catch (err) {
    if (err && err.name === "AbortError") {
      showPopupToast("폴더 선택이 취소되었습니다.");
    } else {
      console.error("클라이언트 폴더 직접 업데이트 오류:", err);
      const errMsg = err && err.message ? err.message : "폴더 업데이트 실패";
      if (errMsg.includes("manifest.json")) {
        await clearSavedExtDirHandle();
      }
      showPopupToast(errMsg, true);
    }
    if (selectExtFolderUpdateBtn) {
      selectExtFolderUpdateBtn.disabled = false;
      selectExtFolderUpdateBtn.textContent = originalText || "📁 확장 폴더 선택 후 즉시 업데이트";
    }
    if (reselectExtFolderBtn) {
      reselectExtFolderBtn.disabled = false;
    }
  }
}

/**
 * 로컬 파이썬 서버(/api/extension/update) 및 클라이언트 폴더 직접 갱신을 통한 원클릭 업데이트 실행
 */
async function applyExtensionUpdate() {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  if (!applyUpdateBtn) return;

  const originalHtml = applyUpdateBtn.innerHTML;
  applyUpdateBtn.disabled = true;
  applyUpdateBtn.innerHTML = "<span>⏳ 업데이트 중...</span>";

  const currentVersion = (chrome.runtime.getManifest().version || "0.0.0").trim();
  const payload = { extension_id: chrome.runtime.id };
  if (latestReleaseData && latestReleaseData.tag_name) {
    payload.tag_name = latestReleaseData.tag_name;
  }

  let serverData = null;
  const hasPassword = Boolean(passwordInput && passwordInput.value.trim());
  if (serverUrl && isServerConnected && hasPassword) {
    try {
      showPopupToast("최신 확장 프로그램 버전을 확인 및 다운로드 중입니다...");
      const res = await fetchWithTimeout(
        `${serverUrl}/api/extension/update`,
        {
          method: "POST",
          credentials: "omit",
          headers: getAuthHeaders(),
          body: JSON.stringify(payload),
        },
        2500
      );
      const parsed = await res.json().catch(() => null);
      if (res.ok && parsed && parsed.status === "ok") {
        serverData = parsed;
      }
    } catch (err) {
      console.warn("서버 확장 업데이트 API 호출 건너뜀(클라이언트 직접 갱신 시도):", err);
    }
  } else {
    showPopupToast("클라이언트 직접 업데이트를 준비 중입니다...");
  }

  pendingServerUpdateData = serverData;
  const targetVer =
    (serverData && serverData.version) ||
    (latestReleaseData && latestReleaseData.tag_name) ||
    "";

  // 1. 로컬 서버가 내 PC의 현재 확장 폴더를 실제로 덮어썼는지 디스크 manifest.json으로 직접 검증
  const diskVer = await getDiskManifestVersion();
  if (
    diskVer &&
    diskVer !== currentVersion &&
    (!targetVer || !isNewerVersion(diskVer, targetVer))
  ) {
    finalizeExtensionReload(targetVer || `v${diskVer}`);
    return;
  }

  // 2. 원격 서버이거나 경로 불일치로 로컬 디스크가 갱신되지 않은 경우: 저장된 폴더 핸들로 무인 갱신 시도
  try {
    const savedHandle = await getSavedExtDirHandle();
    if (savedHandle) {
      const perm = await savedHandle.queryPermission({ mode: "readwrite" });
      if (perm === "granted") {
        const updated = await performClientFolderUpdate(savedHandle, false);
        if (updated) {
          return;
        }
      }
    }
  } catch (e) {
    console.warn("저장된 폴더 핸들 자동 업데이트 실패:", e);
  }

  // 3. 사용자 폴더 선택/권한 허용 UI 필요 (툴바 팝업에서는 창이 닫히므로 독립 창으로 전환)
  const params = new URLSearchParams(window.location.search);
  const isStandaloneUpdate = params.get("standaloneUpdate") === "1";

  applyUpdateBtn.disabled = false;
  applyUpdateBtn.innerHTML = originalHtml;

  if (!isStandaloneUpdate) {
    try {
      await chrome.windows.create({
        url: chrome.runtime.getURL("popup.html?standaloneUpdate=1"),
        type: "popup",
        width: 420,
        height: 560,
      });
      window.close();
      return;
    } catch (winErr) {
      console.warn("독립 업데이트 창 열기 실패, 현재 팝업에서 모달 표시:", winErr);
    }
  }

  await openRemoteUpdateModal();
}
