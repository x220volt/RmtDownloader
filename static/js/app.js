/**
 * 원격 다운로더 (Remote Downloader) 프론트엔드 스크립트
 * SSE 실시간 스트리밍 & 반응형 작업 관리
 */

// 전역 상태
let jobsMap = new Map();
let currentTab = 'active'; // 'active', 'completed', 'all'
let currentPage = 1;
let pageSize = 10;
let sseSource = null;
let serverInfo = null;

// DOM 요소 참조
const urlInput = document.getElementById('urlInput');
const pasteBtn = document.getElementById('pasteBtn');
const torrentFileBtn = document.getElementById('torrentFileBtn');
const torrentFileInput = document.getElementById('torrentFileInput');
const downloadForm = document.getElementById('downloadForm');
const submitBtn = document.getElementById('submitBtn');
const jobsList = document.getElementById('jobsList');
const emptyState = document.getElementById('emptyState');
const downloadDirText = document.getElementById('downloadDirText');
const jobsPaginationWrapper = document.getElementById('jobsPaginationWrapper');
const paginationInfo = document.getElementById('paginationInfo');
const paginationControls = document.getElementById('paginationControls');
const pageSizeSelect = document.getElementById('pageSizeSelect');

// 통계 요소
const statSpeed = document.getElementById('statSpeed');
const statActive = document.getElementById('statActive');
const statCompleted = document.getElementById('statCompleted');
const activeCountBadge = document.getElementById('activeCountBadge');
const completedCountBadge = document.getElementById('completedCountBadge');
const failedCountBadge = document.getElementById('failedCountBadge');
const allCountBadge = document.getElementById('allCountBadge');
const clearCompletedBtn = document.getElementById('clearCompletedBtn');
const clearFailedBtn = document.getElementById('clearFailedBtn');

// 모달 요소

const settingsModal = document.getElementById('settingsModal');
const settingsBtn = document.getElementById('settingsBtn');
const closeSettingsModal = document.getElementById('closeSettingsModal');
const cancelSettingsBtn = document.getElementById('cancelSettingsBtn');
const settingsForm = document.getElementById('settingsForm');
const settingDownloadDir = document.getElementById('settingDownloadDir');
const settingPort = document.getElementById('settingPort');
const settingMaxWorkers = document.getElementById('settingMaxWorkers');
const settingGofileToken = document.getElementById('settingGofileToken');
const settingTeraboxCookie = document.getElementById('settingTeraboxCookie');
const toastContainer = document.getElementById('toastContainer');

// ==========================================
// 1. 초기화 및 서버 정보 불러오기
// ==========================================
let registeredAddons = [];

document.addEventListener('DOMContentLoaded', () => {
  initServerInfo();
  initAddons();
  initSSE();
  initTabs();
  initEvents();
  checkAppUpdate();
});

async function initAddons() {
  try {
    const res = await fetch('/api/addons');
    if (res.ok) {
      const data = await res.json();
      if (data && Array.isArray(data.addons)) {
        registeredAddons = data.addons;
      }
    }
  } catch (err) {
    console.debug('애드온 매니페스트 로드 생략:', err);
  }
}

async function initServerInfo() {
  try {
    const res = await fetch('/api/info');
    if (res.ok) {
      serverInfo = await res.json();
      if (downloadDirText) {
        downloadDirText.textContent = serverInfo.download_dir || '다운로드 폴더';
      }
      const teraboxPwdInput = document.getElementById('teraboxPwdInput');
      if (teraboxPwdInput) {
        teraboxPwdInput.placeholder = 'TeraBox 암호';
      }
      if (serverInfo.username) {
        const currentUsernameText = document.getElementById('currentUsernameText');
        if (currentUsernameText) currentUsernameText.textContent = serverInfo.username;
      }

    }
  } catch (err) {
    console.warn('서버 정보 로드 실패:', err);
  }
}

// ==========================================
// 2. Server-Sent Events (SSE) 실시간 연동
// ==========================================
function initSSE() {
  if (sseSource) {
    sseSource.close();
  }

  sseSource = new EventSource('/api/events');

  sseSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      handleServerEvent(payload);
    } catch (err) {
      console.error('SSE 메시지 파싱 오류:', err);
    }
  };

  sseSource.onerror = () => {
    const statusPill = document.getElementById('serverStatusBadge');
    if (statusPill) {
      statusPill.innerHTML = '<span class="status-dot" style="background:#ef4444"></span><span class="status-text">재연결 중...</span>';
    }
  };

  sseSource.onopen = () => {
    const statusPill = document.getElementById('serverStatusBadge');
    if (statusPill) {
      statusPill.innerHTML = '<span class="status-dot pulse"></span><span class="status-text">서버 연결됨</span>';
    }
  };
}

function handleServerEvent(event) {
  const type = event.type;

  if (type === 'initial_state') {
    jobsMap.clear();
    (event.jobs || []).forEach((job) => jobsMap.set(job.id, job));
    updateStats(event.stats);
    renderJobs();
  } else if (type === 'job_added') {
    jobsMap.set(event.data.id, event.data);
    renderJobs();
    updateJobCounts();
    showToast(`새 작업이 추가되었습니다: ${event.data.filename}`);
  } else if (type === 'job_updated') {
    jobsMap.set(event.data.id, event.data);
    updateJobCard(event.data);
    updateJobCounts();
  } else if (type === 'job_progress') {
    const job = jobsMap.get(event.data.id);
    if (job) {
      Object.assign(job, event.data);
      updateJobProgressOnly(event.data);
      recalcSpeedAndActive();
    }
  } else if (type === 'stats_updated') {
    updateStats(event.data);
  } else if (type === 'job_removed') {
    jobsMap.delete(event.data.id);
    renderJobs();
  }
}

// ==========================================
// 3. 작업 카드 렌더링 & 고속 DOM 업데이트
// ==========================================

function formatJobTime(dateStr) {
  if (!dateStr) return '';
  try {
    const parts = dateStr.split(' ');
    if (parts.length === 2) {
      const dp = parts[0].split('-');
      const tp = parts[1].split(':');
      if (dp.length === 3 && tp.length >= 2) {
        return `${dp[1]}-${dp[2]} ${tp[0]}:${tp[1]}`;
      }
    }
  } catch (e) {}
  return dateStr;
}

function getRelativeTime(dateStr) {
  if (!dateStr) return '시간 미기록';
  try {
    const target = new Date(dateStr.replace(/-/g, '/')).getTime();
    if (isNaN(target)) return formatJobTime(dateStr);
    const now = Date.now();
    const diffSec = Math.floor((now - target) / 1000);
    if (diffSec < 10) return '방금 전';
    if (diffSec < 60) return `${diffSec}초 전`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}분 전`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}시간 전`;
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay < 7) return `${diffDay}일 전`;
    return formatJobTime(dateStr);
  } catch (e) {
    return formatJobTime(dateStr);
  }
}

function renderJobs() {
  jobsList.innerHTML = '';
  const filtered = getFilteredJobs();
  const totalItems = filtered.length;
  const totalPages = Math.ceil(totalItems / pageSize) || 1;

  if (currentPage > totalPages) {
    currentPage = totalPages;
  }
  if (currentPage < 1) {
    currentPage = 1;
  }

  if (totalItems === 0) {
    jobsList.appendChild(emptyState);
    emptyState.style.display = 'flex';
    updateEmptyStateText();
  } else {
    emptyState.style.display = 'none';
    const startIndex = (currentPage - 1) * pageSize;
    const endIndex = Math.min(startIndex + pageSize, totalItems);
    const pageItems = filtered.slice(startIndex, endIndex);

    pageItems.forEach((job) => {
      jobsList.appendChild(createJobCard(job));
    });
  }

  renderPagination(totalItems, totalPages);
  updateJobCounts();
}

function updateEmptyStateText() {
  const emptyTitle = emptyState.querySelector('.empty-title');
  const emptyDesc = emptyState.querySelector('.empty-desc');
  if (!emptyTitle) return;

  if (currentTab === 'active') {
    emptyTitle.textContent = '진행 또는 대기 중인 작업이 없습니다';
    if (emptyDesc) emptyDesc.textContent = '상단에 다운로드 링크를 입력하여 서버 PC에 원격 다운로드를 지시해보세요.';
  } else if (currentTab === 'completed') {
    emptyTitle.textContent = '완료된 다운로드 작업이 없습니다';
    if (emptyDesc) emptyDesc.textContent = '다운로드가 완료되면 여기에 파일 목록이 보관됩니다.';
  } else if (currentTab === 'failed') {
    emptyTitle.textContent = '실패 또는 취소된 작업이 없습니다';
    if (emptyDesc) emptyDesc.textContent = '다운로드 실패나 오류가 발생하면 여기에 기록됩니다.';
  } else {
    emptyTitle.textContent = '등록된 다운로드 작업이 없습니다';
    if (emptyDesc) emptyDesc.textContent = '상단에 다운로드 링크를 입력하여 새로운 작업을 시작해보세요.';
  }
}

function renderPagination(totalItems, totalPages) {
  if (!jobsPaginationWrapper) return;

  if (totalItems <= pageSize && totalPages <= 1) {
    jobsPaginationWrapper.style.display = 'none';
    return;
  }

  jobsPaginationWrapper.style.display = 'flex';

  const startIndex = (currentPage - 1) * pageSize + 1;
  const endIndex = Math.min(currentPage * pageSize, totalItems);

  if (paginationInfo) {
    paginationInfo.textContent = `전체 ${totalItems.toLocaleString()}개 중 ${startIndex} - ${endIndex}개 표시 (${currentPage} / ${totalPages} 페이지)`;
  }

  if (paginationControls) {
    paginationControls.innerHTML = '';

    // 이전 페이지 버튼
    const prevBtn = document.createElement('button');
    prevBtn.type = 'button';
    prevBtn.className = 'btn-page-nav';
    prevBtn.innerHTML = '&#171; 이전';
    prevBtn.title = '이전 페이지';
    prevBtn.disabled = currentPage <= 1;
    prevBtn.addEventListener('click', () => {
      if (currentPage > 1) {
        setPage(currentPage - 1);
      }
    });
    paginationControls.appendChild(prevBtn);

    // 페이지 번호 버튼들
    const pageNumbers = getPageNumbers(currentPage, totalPages);
    pageNumbers.forEach((p) => {
      if (p === '...') {
        const ellipsis = document.createElement('span');
        ellipsis.className = 'pagination-ellipsis';
        ellipsis.textContent = '...';
        paginationControls.appendChild(ellipsis);
      } else {
        const numBtn = document.createElement('button');
        numBtn.type = 'button';
        numBtn.className = `btn-page-num${p === currentPage ? ' active' : ''}`;
        numBtn.textContent = String(p);
        numBtn.title = `${p} 페이지로 이동`;
        if (p === currentPage) {
          numBtn.setAttribute('aria-current', 'page');
        } else {
          numBtn.addEventListener('click', () => setPage(p));
        }
        paginationControls.appendChild(numBtn);
      }
    });

    // 다음 페이지 버튼
    const nextBtn = document.createElement('button');
    nextBtn.type = 'button';
    nextBtn.className = 'btn-page-nav';
    nextBtn.innerHTML = '다음 &#187;';
    nextBtn.title = '다음 페이지';
    nextBtn.disabled = currentPage >= totalPages;
    nextBtn.addEventListener('click', () => {
      if (currentPage < totalPages) {
        setPage(currentPage + 1);
      }
    });
    paginationControls.appendChild(nextBtn);
  }
}

function getPageNumbers(current, total) {
  if (total <= 7) {
    const pages = [];
    for (let i = 1; i <= total; i++) pages.push(i);
    return pages;
  }
  const pages = [1];
  let left = Math.max(2, current - 1);
  let right = Math.min(total - 1, current + 1);

  if (current <= 3) {
    right = 4;
  }
  if (current >= total - 2) {
    left = total - 3;
  }

  if (left > 2) {
    pages.push('...');
  }
  for (let i = left; i <= right; i++) {
    pages.push(i);
  }
  if (right < total - 1) {
    pages.push('...');
  }
  pages.push(total);
  return pages;
}

function setPage(page) {
  currentPage = page;
  renderJobs();
  const card = document.querySelector('.jobs-card');
  if (card) {
    const rect = card.getBoundingClientRect();
    if (rect.top < 0) {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }
}

function getFilteredJobs() {
  const jobs = Array.from(jobsMap.values());
  let filtered = [];

  if (currentTab === 'active') {
    filtered = jobs.filter((j) => ['queued', 'resolving', 'downloading', 'paused'].includes(j.status));
    const statusWeight = { downloading: 1, resolving: 2, queued: 3, paused: 4 };
    filtered.sort((a, b) => {
      const wa = statusWeight[a.status] || 99;
      const wb = statusWeight[b.status] || 99;
      if (wa !== wb) return wa - wb;
      if (a.status === 'downloading' && b.status === 'downloading') {
        return (b.progress || 0) - (a.progress || 0);
      }
      return (a.created_at || '').localeCompare(b.created_at || '');
    });
  } else if (currentTab === 'completed') {
    filtered = jobs.filter((j) => j.status === 'completed');
    // 완료 목록: 가장 최근에 완료된 작업이 최상단에 오도록 완료 시각 역순 정렬
    filtered.sort((a, b) => {
      const timeA = a.completed_at || a.created_at || '';
      const timeB = b.completed_at || b.created_at || '';
      return timeB.localeCompare(timeA);
    });
  } else if (currentTab === 'failed') {
    filtered = jobs.filter((j) => ['failed', 'cancelled'].includes(j.status));
    // 실패/취소 목록: 가장 최근 발생한 작업이 최상단에 오도록 역순 정렬
    filtered.sort((a, b) => {
      const timeA = a.completed_at || a.created_at || '';
      const timeB = b.completed_at || b.created_at || '';
      return timeB.localeCompare(timeA);
    });
  } else {
    // 'all' 전체 탭: 진행 중인 작업을 상단에 두고, 완료/실패 작업은 최신순으로 정렬
    const statusWeight = { downloading: 1, resolving: 2, queued: 3, paused: 4, completed: 5, failed: 6, cancelled: 7 };
    filtered = jobs.slice().sort((a, b) => {
      const wa = statusWeight[a.status] || 99;
      const wb = statusWeight[b.status] || 99;
      const isAActive = wa <= 4;
      const isBActive = wb <= 4;
      if (isAActive && isBActive) {
        if (wa !== wb) return wa - wb;
        return (a.created_at || '').localeCompare(b.created_at || '');
      }
      if (isAActive !== isBActive) {
        return isAActive ? -1 : 1;
      }
      const timeA = a.completed_at || a.created_at || '';
      const timeB = b.completed_at || b.created_at || '';
      return timeB.localeCompare(timeA);
    });
  }
  return filtered;
}

function checkEmptyState() {
  const filtered = getFilteredJobs();
  if (filtered.length === 0) {
    if (!jobsList.contains(emptyState)) {
      jobsList.appendChild(emptyState);
    }
    emptyState.style.display = 'flex';
    updateEmptyStateText();
  } else {
    emptyState.style.display = 'none';
  }
  if (jobsPaginationWrapper) {
    jobsPaginationWrapper.style.display = filtered.length > pageSize ? 'flex' : 'none';
  }
}

function getDomainFromUrl(url) {
  try {
    const u = new URL(url);
    return u.hostname;
  } catch (e) {
    return url || '';
  }
}

function safeLinkUrl(url) {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (/^(https?:\/\/|magnet:\?)/i.test(trimmed)) {
    return trimmed;
  }
  return '';
}

function escapeJsString(str) {
  return String(str || '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '&quot;')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '');
}

window.copyTextToClipboard = async function (text, btn) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    if (btn) {
      const origText = btn.innerHTML;
      btn.innerHTML = '✓ 복사됨';
      setTimeout(() => {
        btn.innerHTML = origText;
      }, 1500);
    }
    showToast('클립보드에 복사되었습니다.');
  } catch (err) {
    console.error('클립보드 복사 실패:', err);
    showToast('복사에 실패했습니다.', true);
  }
};

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-copy-url]');
  if (btn) {
    const text = btn.getAttribute('data-copy-url');
    if (text) {
      window.copyTextToClipboard(text, btn);
    }
  }
});

function createJobCard(job) {
  const card = document.createElement('div');
  card.className = `job-item status-${job.status}`;
  card.id = `job-${job.id}`;

  const isCloud = (job.service_name || '').includes('클라우드') || (job.file_path || '').startsWith('내 테라박스');
  const serviceTagClass = getServiceTagClass(job.service_name);
  const statusBadge = getStatusBadge(job.status, isCloud);
  const isResolving = job.status === 'resolving';
  const progressPercent = job.progress || 0;

  // 출처 정보 및 링크
  const displaySource = job.source_title || (job.source_url ? getDomainFromUrl(job.source_url) : '');
  const sourceHref = safeLinkUrl(job.source_url || job.referer || '');
  const safeOriginalUrl = safeLinkUrl(job.original_url);

  // 서브폴더명 추출
  let subfolderName = '';
  if (job.download_dir && serverInfo && serverInfo.download_dir) {
    const normJobDir = job.download_dir.replace(/\\/g, '/').replace(/\/+$/, '');
    const normBaseDir = serverInfo.download_dir.replace(/\\/g, '/').replace(/\/+$/, '');
    if (normJobDir.startsWith(normBaseDir) && normJobDir.length > normBaseDir.length) {
      subfolderName = normJobDir.slice(normBaseDir.length).replace(/^\/+/, '');
    }
  }

  // 타임스탬프 라벨 및 툴팁
  let timeLabel = '';
  let fullTimeTooltip = '';
  if (job.status === 'completed') {
    const t = job.completed_at || job.created_at;
    timeLabel = `완료: ${getRelativeTime(t)}`;
    fullTimeTooltip = `완료 시각: ${t || '알 수 없음'}`;
  } else if (['failed', 'cancelled'].includes(job.status)) {
    const t = job.completed_at || job.created_at;
    timeLabel = `${job.status === 'cancelled' ? '취소' : '실패'}: ${getRelativeTime(t)}`;
    fullTimeTooltip = `종료 시각: ${t || '알 수 없음'}`;
  } else {
    const t = job.created_at;
    timeLabel = `등록: ${getRelativeTime(t)}`;
    fullTimeTooltip = `등록 시각: ${t || '알 수 없음'}`;
  }

  card.innerHTML = `
    <div class="job-header-row">
      <div class="job-badges-group">
        <span class="job-service-tag ${serviceTagClass}">${escapeHtml(job.service_name || 'Direct')}</span>
        ${job.is_auto ? `<span class="job-mode-tag auto" title="자동 수집(감시) 다운로드">🤖 자동</span>` : `<span class="job-mode-tag manual" title="사용자 수동 추가 다운로드">👤 수동</span>`}
        ${isCloud && job.file_path ? `<span class="job-folder-tag cloud" title="저장 위치: ${escapeHtml(job.file_path)}">☁️ ${escapeHtml(job.file_path)}</span>` : ''}
        ${!isCloud && subfolderName ? `<span class="job-folder-tag" title="저장 폴더: ${escapeHtml(subfolderName)}">📁 ${escapeHtml(subfolderName)}</span>` : ''}
        ${displaySource ? `<span class="job-source-tag" title="출처: ${escapeHtml(displaySource)}">${sourceHref ? `<a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer">📝 ${escapeHtml(displaySource)} ↗</a>` : `📝 ${escapeHtml(displaySource)}`}</span>` : ''}
      </div>
      <div class="job-actions">
        ${isCloud && job.status === 'completed' ? `<a href="https://www.terabox.com/main" target="_blank" rel="noopener noreferrer" class="cloud-open-btn" title="테라박스 웹 드라이브 열기">☁️ 테라박스 ↗</a>` : ''}
        ${renderActionButtons(job)}
      </div>
    </div>

    <div class="job-title-row">
      <span class="job-filename" title="${escapeHtml(job.filename)}">${escapeHtml(job.filename)}</span>
      ${job.original_url ? `
        <div class="job-url-sub">
          ${safeOriginalUrl ? `<a href="${escapeHtml(safeOriginalUrl)}" target="_blank" rel="noopener noreferrer" class="job-url-link" title="다운로드 주소: ${escapeHtml(job.original_url)}">🔗 ${escapeHtml(job.original_url)}</a>` : `<span class="job-url-link" title="다운로드 주소: ${escapeHtml(job.original_url)}">🔗 ${escapeHtml(job.original_url)}</span>`}
          <button type="button" class="btn-copy-url" data-copy-url="${escapeHtml(job.original_url)}" title="다운로드 링크 복사">📋</button>
        </div>
      ` : ''}
    </div>

    <div class="progress-bar-container ${job.status === 'completed' ? 'completed' : ''}">
      <div class="progress-bar-fill ${isResolving ? 'indeterminate' : ''} ${job.status}" 
           id="prog-fill-${job.id}" 
           style="width: ${progressPercent}%"></div>
    </div>

    <div class="job-meta">
      <div class="job-meta-left">
        <span class="job-status-badge ${statusBadge.className}" id="status-badge-${job.id}">${statusBadge.text}</span>
        <span class="job-bytes" id="bytes-${job.id}">${isCloud && job.status === 'completed' ? (job.total_str || formatBytes(job.total_bytes)) : ((job.downloaded_str || '0 B') + ' / ' + (job.total_str || '알 수 없음'))}</span>
      </div>
      <div class="job-meta-right">
        ${job.status === 'downloading' ? `<span class="job-speed" id="speed-${job.id}">${job.speed_str}</span>` : ''}
        ${job.status === 'downloading' && job.eta_str ? `<span class="job-eta" id="eta-${job.id}">남음: ${job.eta_str}</span>` : ''}
        <span class="job-time" id="time-${job.id}" title="${escapeHtml(fullTimeTooltip)}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="time-icon">
            <circle cx="12" cy="12" r="10"></circle>
            <polyline points="12 6 12 12 16 14"></polyline>
          </svg>
          ${escapeHtml(timeLabel)}
        </span>
      </div>
    </div>
    ${job.error_message ? `
      <div class="job-error-text">
        <div class="job-error-header">
          <span>${job.error_message && job.error_message.includes('중복') ? '⚠️ 중복 다운로드 제외' : '❌ 다운로드 실패'}</span>
          ${(job.downloaded_bytes && job.downloaded_bytes > 0) ? `<span class="job-resume-tag" title="이전에 다운로드된 데이터(${job.downloaded_str || formatBytes(job.downloaded_bytes)})가 보관되어 이어받기가 가능합니다">📦 이어받기 가능 (${job.downloaded_str || formatBytes(job.downloaded_bytes)})</span>` : ''}
          ${displaySource ? `
            <span class="job-error-source">
              [출처: ${sourceHref ? `<a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer" class="job-error-link">${escapeHtml(displaySource)} ↗</a>` : escapeHtml(displaySource)}]
            </span>` : ''}
          <button type="button" class="btn-retry-mini" onclick="retryJob('${job.id}')" title="이 작업을 다시 다운로드합니다">${(job.downloaded_bytes && job.downloaded_bytes > 0) ? '🔄 이어받기 (재시도)' : '🔄 다시 다운로드'}</button>
        </div>
        ${job.original_url ? `
          <div class="job-error-link-row">
            <span class="error-link-label">요청 링크:</span>
            ${safeOriginalUrl ? `<a href="${escapeHtml(safeOriginalUrl)}" target="_blank" rel="noopener noreferrer" class="error-url-link" title="${escapeHtml(job.original_url)}">${escapeHtml(job.original_url)} ↗</a>` : `<span class="error-url-link" title="${escapeHtml(job.original_url)}">${escapeHtml(job.original_url)}</span>`}
            <button type="button" class="btn-copy-mini" data-copy-url="${escapeHtml(job.original_url)}" title="요청 링크 복사">📋 복사</button>
          </div>
        ` : ''}
        ${(sourceHref && sourceHref !== job.original_url) ? `
          <div class="job-error-source-row">
            <span class="error-link-label">요청 웹페이지:</span>
            <a href="${escapeHtml(sourceHref)}" target="_blank" rel="noopener noreferrer" class="error-url-link" title="${escapeHtml(sourceHref)}">${escapeHtml(job.source_title ? `${job.source_title} (${sourceHref})` : sourceHref)} ↗</a>
          </div>
        ` : ''}
        <div class="job-error-detail">${escapeHtml(job.error_message)}</div>
      </div>` : ''}
  `;

  return card;
}

function updateJobCard(job) {
  renderJobs();
}

function updateJobProgressOnly(data) {
  const fill = document.getElementById(`prog-fill-${data.id}`);
  const bytes = document.getElementById(`bytes-${data.id}`);
  const speed = document.getElementById(`speed-${data.id}`);
  const eta = document.getElementById(`eta-${data.id}`);

  if (fill) {
    fill.classList.remove('indeterminate');
    fill.style.width = `${data.progress}%`;
  }
  if (bytes) {
    bytes.textContent = `${data.downloaded_str} / ${data.total_str}`;
  }
  if (speed) {
    speed.textContent = data.speed_str;
  }
  if (eta) {
    eta.textContent = data.eta_str ? `남음: ${data.eta_str}` : '';
  }
}

function renderActionButtons(job) {
  let btns = '';

  if (job.status === 'downloading') {
    btns += `<button class="action-btn" onclick="pauseJob('${job.id}')" title="일시정지">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>
    </button>`;
  } else if (job.status === 'paused') {
    btns += `<button class="action-btn" onclick="resumeJob('${job.id}')" title="재개">
      <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
    </button>`;
  } else if (job.status === 'failed' || job.status === 'cancelled') {
    const isResume = (job.downloaded_bytes && job.downloaded_bytes > 0);
    btns += `<button class="action-btn" onclick="retryJob('${job.id}')" title="${isResume ? '이어받기 (다시 시도)' : '다시 시도'}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
    </button>`;
  } else if (job.status === 'completed') {
    btns += `<button class="action-btn" onclick="retryJob('${job.id}')" title="다시 다운로드">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
    </button>`;
  }

  if (job.status === 'downloading' || job.status === 'queued' || job.status === 'resolving') {
    btns += `<button class="action-btn" onclick="cancelJob('${job.id}')" title="취소">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
    </button>`;
  }

  btns += `<button class="action-btn delete" onclick="deleteJob('${job.id}')" title="목록에서 삭제">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
  </button>`;

  return btns;
}

function getServiceTagClass(service) {
  const lower = (service || '').toLowerCase().trim();
  if (lower.includes('클라우드') || lower.includes('cloud')) return 'badge-terabox-cloud';

  // 동적 애드온 매니페스트 검색 우선
  if (Array.isArray(registeredAddons) && registeredAddons.length > 0) {
    const match = registeredAddons.find(a => {
      const aid = (a.id || '').toLowerCase();
      const aname = (a.name || '').toLowerCase();
      return aid === lower || aname === lower || (aid && lower.includes(aid)) || (aname && lower.includes(aname));
    });
    if (match && match.badge_class) {
      return match.badge_class;
    }
  }

  if (lower.includes('torrent') || lower.includes('마그넷')) return 'badge-torrent';
  if (lower.includes('gofile')) return 'badge-gofile';
  if (lower.includes('viking')) return 'badge-viking';
  if (lower.includes('megaup')) return 'badge-megaup';
  if (lower === 'mega' || lower.includes('mega.nz') || (lower.includes('mega') && !lower.includes('megaup'))) return 'badge-mega';
  if (lower.includes('mybox')) return 'badge-mybox';
  if (lower.includes('naver')) return 'badge-naver';
  if (lower.includes('tistory')) return 'badge-tistory';
  if (lower.includes('terabox')) return 'badge-terabox';
  if (lower.includes('gigafile')) return 'badge-gigafile';
  if (lower.includes('transfer.it') || lower.includes('transferit')) return 'badge-transferit';
  if (lower.includes('1fichier') || lower.includes('onefichier')) return 'badge-onefichier';
  if (lower.includes('kio')) return 'badge-kio';
  if (lower.includes('x-share') || lower.includes('xshare')) return 'badge-xshare';
  if (lower.includes('romsfun')) return 'badge-romsfun';
  if (lower.includes('wowroms')) return 'badge-wowroms';
  if (lower.includes('rootz')) return 'badge-rootz';
  if (lower.includes('gdrive') || lower.includes('google drive') || lower.includes('google')) return 'badge-gdrive';
  return 'badge-direct';
}

function getStatusBadge(status, isCloud = false) {
  switch (status) {
    case 'queued':
      return { className: 'status-queued', text: '대기 중' };
    case 'resolving':
      return { className: 'status-resolving', text: isCloud ? '클라우드 전송 중...' : '링크 분석 중...' };
    case 'downloading':
      return { className: 'status-downloading', text: '다운로드 중' };
    case 'paused':
      return { className: 'status-paused', text: '일시정지됨' };
    case 'completed':
      return { className: 'status-completed', text: isCloud ? '☁️ 클라우드 저장 완료' : '다운로드 완료' };
    case 'failed':
      return { className: 'status-failed', text: '실패' };
    case 'cancelled':
      return { className: 'status-cancelled', text: '취소됨' };
    default:
      return { className: 'status-queued', text: status };
  }
}

function updateJobCounts() {
  const jobs = Array.from(jobsMap.values());
  const activeCount = jobs.filter((j) => ['queued', 'resolving', 'downloading', 'paused'].includes(j.status)).length;
  const completedCount = jobs.filter((j) => j.status === 'completed').length;
  const failedCount = jobs.filter((j) => ['failed', 'cancelled'].includes(j.status)).length;
  const allCount = jobs.length;

  if (activeCountBadge) activeCountBadge.textContent = activeCount;
  if (completedCountBadge) completedCountBadge.textContent = completedCount;
  if (failedCountBadge) failedCountBadge.textContent = failedCount;
  if (allCountBadge) allCountBadge.textContent = allCount;

  if (clearCompletedBtn) clearCompletedBtn.disabled = (completedCount === 0);
  if (clearFailedBtn) clearFailedBtn.disabled = (failedCount === 0);

  recalcSpeedAndActive();
  if (statCompleted) statCompleted.textContent = `${completedCount}개`;
}

function updateStats(stats) {
  if (!stats) return;
  if (statSpeed) statSpeed.textContent = stats.total_speed_str || '0 KB/s';
  if (statActive) statActive.textContent = `${stats.downloading_count || 0}개 진행`;
  if (statCompleted) statCompleted.textContent = `${stats.completed_count || 0}개`;
}

function recalcSpeedAndActive() {
  let totalSpeed = 0;
  let activeDownloading = 0;
  jobsMap.forEach((j) => {
    if (j.status === 'downloading') {
      totalSpeed += j.speed_bps || 0;
      activeDownloading++;
    }
  });
  if (statSpeed) {
    statSpeed.textContent = formatBytes(totalSpeed) + '/s';
  }
  if (statActive) statActive.textContent = `${activeDownloading}개 진행`;
}

// ==========================================
// 4. 이벤트 바인딩 및 API 통신
// ==========================================
function initEvents() {
  const appUpdateBtn = document.getElementById('appUpdateBtn');
  if (appUpdateBtn) {
    appUpdateBtn.addEventListener('click', applyAppUpdate);
  }
  const modalCheckAppUpdateBtn = document.getElementById('modalCheckAppUpdateBtn');
  if (modalCheckAppUpdateBtn) {
    modalCheckAppUpdateBtn.addEventListener('click', () => checkAppUpdate(true));
  }
  const modalApplyAppUpdateBtn = document.getElementById('modalApplyAppUpdateBtn');
  if (modalApplyAppUpdateBtn) {
    modalApplyAppUpdateBtn.addEventListener('click', applyAppUpdate);
  }

  // 페이지당 표시 개수 선택기
  if (pageSizeSelect) {
    pageSizeSelect.addEventListener('change', (e) => {
      pageSize = parseInt(e.target.value, 10) || 10;
      currentPage = 1;
      renderJobs();
    });
  }
  // 클립보드 붙여넣기
  if (pasteBtn && urlInput) {
    pasteBtn.addEventListener('click', async () => {
      try {
        const text = await navigator.clipboard.readText();
        if (text) {
          if (urlInput.value && !urlInput.value.endsWith('\n')) {
            urlInput.value += '\n' + text.trim();
          } else {
            urlInput.value += text.trim();
          }
          urlInput.focus();
          urlInput.dispatchEvent(new Event('input'));
          showToast('클립보드에서 주소를 붙여넣었습니다.');
        }
      } catch (err) {
        showToast('클립보드 접근 권한이 필요합니다.');
      }
    });
  }

  // .torrent 파일 선택 버튼
  if (torrentFileBtn && torrentFileInput) {
    torrentFileBtn.addEventListener('click', () => {
      torrentFileInput.click();
    });

    torrentFileInput.addEventListener('change', () => {
      if (torrentFileInput.files && torrentFileInput.files.length > 0) {
        handleTorrentUpload(torrentFileInput.files[0]);
      }
    });
  }

  // 입력 길이에 따른 높이 자동 조절
  if (urlInput) {
    const adjustHeight = () => {
      urlInput.style.height = 'auto';
      const newHeight = Math.max(112, Math.min(380, urlInput.scrollHeight));
      urlInput.style.height = newHeight + 'px';
    };
    urlInput.addEventListener('input', adjustHeight);
    adjustHeight();
  }

  // 드래그 앤 드롭 지원 (웹페이지의 링크/텍스트 또는 .torrent 파일을 마우스로 끌어다 놓기)
  if (urlInput) {
    urlInput.addEventListener('dragover', (e) => {
      e.preventDefault();
      urlInput.style.borderColor = 'var(--accent-primary)';
      urlInput.style.boxShadow = '0 0 15px rgba(99, 102, 241, 0.4)';
    });

    urlInput.addEventListener('dragleave', (e) => {
      e.preventDefault();
      urlInput.style.borderColor = '';
      urlInput.style.boxShadow = '';
    });

    urlInput.addEventListener('drop', (e) => {
      e.preventDefault();
      urlInput.style.borderColor = '';
      urlInput.style.boxShadow = '';

      // .torrent 파일 직접 드롭 확인
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        let torrentFound = false;
        for (const file of e.dataTransfer.files) {
          if (file.name.toLowerCase().endsWith('.torrent')) {
            torrentFound = true;
            handleTorrentUpload(file);
          }
        }
        if (torrentFound) return;
      }

      let targetText = '';
      const html = e.dataTransfer.getData('text/html');
      if (html) {
        try {
          const parser = new DOMParser();
          const doc = parser.parseFromString(html, 'text/html');
          const a = doc.querySelector('a[href]');
          if (a) {
            const href = a.getAttribute('href');
            const linkText = a.textContent.trim();
            if (linkText && !linkText.startsWith('http://') && !linkText.startsWith('https://') && linkText.includes('.')) {
              targetText = `${href} | ${linkText}`;
            } else if (href) {
              targetText = href;
            }
          }
        } catch (err) {
          console.warn('HTML 드래그 파싱 오류:', err);
        }
      }

      if (!targetText) {
        targetText = (e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain') || '').trim();
      }

      if (targetText) {
        if (urlInput.value && !urlInput.value.endsWith('\n')) {
          urlInput.value += '\n' + targetText;
        } else {
          urlInput.value += targetText;
        }
        urlInput.focus();
        showToast('드래그한 링크를 추가했습니다.');
      }
    });
  }

async function handleTorrentUpload(file) {
  if (!file || !file.name.toLowerCase().endsWith('.torrent')) {
    showToast('.torrent 확장자 파일만 업로드할 수 있습니다.', true);
    return;
  }

  const subfolderInput = document.getElementById('subfolderInput');
  const subfolderVal = subfolderInput ? subfolderInput.value.trim() : '';

  const formData = new FormData();
  formData.append('file', file);
  if (subfolderVal) {
    formData.append('subfolder', subfolderVal);
  }

  showToast(`토렌트 파일 등록 중: ${file.name}...`);
  try {
    const res = await fetch('/api/upload-torrent', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message || '토렌트 다운로드가 추가되었습니다!');
      if (torrentFileInput) torrentFileInput.value = '';
      switchTab('active');
    } else {
      showToast(data.error || '토렌트 파일 추가 실패', true);
    }
  } catch (err) {
    showToast('토렌트 업로드 실패: ' + err.message, true);
  }
}

  // 다운로드 폼 제출
  if (downloadForm) {
    downloadForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const rawText = urlInput.value.trim();
      if (!rawText) return;

      const subfolderInput = document.getElementById('subfolderInput');
      const subfolderVal = subfolderInput ? subfolderInput.value.trim() : '';

      const teraboxPwdInput = document.getElementById('teraboxPwdInput');
      const teraboxPwdVal = teraboxPwdInput ? teraboxPwdInput.value.trim() : '';

      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="status-dot pulse"></span> 다운로드 추가 중...';

      try {
        const res = await fetch('/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: rawText,
            subfolder: subfolderVal,
            password: teraboxPwdVal,
            terabox_password: teraboxPwdVal,

          }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
          urlInput.value = '';
          if (subfolderInput) subfolderInput.value = '';
          if (teraboxPwdInput) teraboxPwdInput.value = '';
          if (data.dup_failed_count > 0 && data.added_count === 0) {
            showToast(`⚠️ 중복 링크로 확인되어 실패 목록에 등록되었습니다. 재시도 버튼으로 다시 받을 수 있습니다.`);
            switchTab('failed');
          } else if (data.dup_failed_count > 0 && data.added_count > 0) {
            showToast(`다운로드 작업 ${data.added_count}개 추가됨 (중복 ${data.dup_failed_count}개 실패 목록에 등록)`);
            switchTab('active');
          } else if (data.added_count === 0 && data.skipped_count > 0) {
            showToast(`⚠️ 이미 다운로드 완료되었거나 진행 중인 링크입니다. (${data.skipped_count}개 중복 제외)`);
          } else if (data.added_count > 0 && data.skipped_count > 0) {
            showToast(`다운로드 작업 ${data.added_count}개 추가됨 (${data.skipped_count}개 중복 제외)`);
            switchTab('active');
          } else {
            showToast(`다운로드 작업 ${data.added_count}개가 추가되었습니다!`);
            switchTab('active');
          }
        } else {
          showToast(data.error || '추가 실패', true);
        }
      } catch (err) {
        showToast('서버 통신 실패: ' + err.message, true);
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          다운로드 시작
        `;
      }
    });
  }

  // 완료 / 실패 목록 한번에 비우기 버튼
  if (clearCompletedBtn) {
    clearCompletedBtn.addEventListener('click', async () => {
      const completedJobs = Array.from(jobsMap.values()).filter((j) => j.status === 'completed');
      if (completedJobs.length === 0) {
        showToast('완료된 다운로드 작업이 없습니다.');
        return;
      }
      if (!confirm(`완료된 다운로드 목록 ${completedJobs.length}개를 모두 지우시겠습니까? (PC에 저장된 실제 파일은 유지됩니다)`)) {
        return;
      }
      try {
        const res = await fetch('/api/jobs/clear', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filter: 'completed' }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
          completedJobs.forEach((j) => jobsMap.delete(j.id));
          renderJobs();
          showToast(`완료 목록 ${data.removed_count}개를 지웠습니다.`);
        } else {
          showToast('목록 지우기 실패', true);
        }
      } catch (err) {
        showToast('서버 통신 실패: ' + err.message, true);
      }
    });
  }

  if (clearFailedBtn) {
    clearFailedBtn.addEventListener('click', async () => {
      const failedJobs = Array.from(jobsMap.values()).filter((j) => ['failed', 'cancelled'].includes(j.status));
      if (failedJobs.length === 0) {
        showToast('실패 또는 취소된 작업이 없습니다.');
        return;
      }
      if (!confirm(`실패/취소된 다운로드 목록 ${failedJobs.length}개를 모두 지우시겠습니까?`)) {
        return;
      }
      try {
        const res = await fetch('/api/jobs/clear', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filter: 'failed' }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
          failedJobs.forEach((j) => jobsMap.delete(j.id));
          renderJobs();
          showToast(`실패/취소 목록 ${data.removed_count}개를 지웠습니다.`);
        } else {
          showToast('목록 지우기 실패', true);
        }
      } catch (err) {
        showToast('서버 통신 실패: ' + err.message, true);
      }
    });
  }

  // 설정 모달 열기
  if (settingsBtn) {
    settingsBtn.addEventListener('click', openSettings);
  }
  if (closeSettingsModal) {
    closeSettingsModal.addEventListener('click', () => settingsModal.classList.remove('show'));
  }
  if (cancelSettingsBtn) {
    cancelSettingsBtn.addEventListener('click', () => settingsModal.classList.remove('show'));
  }
  if (settingsForm) {
    settingsForm.addEventListener('submit', saveSettings);

  }

  // 비밀번호 변경 확인 일치 여부 실시간 검증
  const settingAuthPassword = document.getElementById('settingAuthPassword');
  const settingAuthPasswordConfirm = document.getElementById('settingAuthPasswordConfirm');
  if (settingAuthPassword && settingAuthPasswordConfirm) {
    settingAuthPassword.addEventListener('input', validateAuthPasswordMatch);
    settingAuthPasswordConfirm.addEventListener('input', validateAuthPasswordMatch);
  }

  // 설정 모달 탭 전환 이벤트
  const settingsTabs = document.querySelectorAll('[data-settings-tab]');
  settingsTabs.forEach((btn) => {
    btn.addEventListener('click', () => {
      settingsTabs.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');

      const targetTab = btn.dataset.settingsTab;
      document.querySelectorAll('.settings-tab-pane').forEach((pane) => {
        pane.classList.remove('active');
      });

      const paneMap = {
        general: 'settingsTabGeneral',
        cloud: 'settingsTabCloud',
        automation: 'settingsTabAutomation',
        security: 'settingsTabSecurity',
        extension: 'settingsTabExtension',
        mobile: 'settingsTabMobile',
        platforms: 'settingsTabPlatforms',
      };
      const targetPane = document.getElementById(paneMap[targetTab]);
      if (targetPane) targetPane.classList.add('active');
      if (targetTab === 'mobile' && typeof window.updateIphoneFields === 'function') {
        window.updateIphoneFields();
      }
    });
  });

  // API 전송 전용 토큰 복사 및 재발급 이벤트
  document.querySelectorAll('[data-goto-settings-tab]').forEach((badge) => {
    badge.addEventListener('click', () => {
      const target = badge.dataset.gotoSettingsTab;
      const tabBtn = document.querySelector(`[data-settings-tab="${target}"]`);
      if (tabBtn) tabBtn.click();
    });
  });
  const copyApiTokenBtn = document.getElementById('copyApiTokenBtn');
  if (copyApiTokenBtn) {
    copyApiTokenBtn.addEventListener('click', async () => {
      const tokenInput = document.getElementById('settingApiToken');
      if (tokenInput && tokenInput.value) {
        try {
          await navigator.clipboard.writeText(tokenInput.value);
          showToast('API 전송 토큰이 복사되었습니다!');
        } catch {
          showToast('복사 권한이 필요합니다.');
        }
      }
    });
  }

  // 테라박스 쿠키(ndus) 안내 접기/펼치기
  const toggleTeraboxCookieHelp = document.getElementById('toggleTeraboxCookieHelp');
  const teraboxCookieHelpBox = document.getElementById('teraboxCookieHelpBox');
  const teraboxCookieHelpArrow = document.getElementById('teraboxCookieHelpArrow');
  if (toggleTeraboxCookieHelp && teraboxCookieHelpBox) {
    toggleTeraboxCookieHelp.addEventListener('click', () => {
      const isVisible = teraboxCookieHelpBox.style.display !== 'none';
      teraboxCookieHelpBox.style.display = isVisible ? 'none' : 'block';
      if (teraboxCookieHelpArrow) {
        teraboxCookieHelpArrow.textContent = isVisible ? '▼' : '▲';
      }
    });
  }

  const regenApiTokenBtn = document.getElementById('regenApiTokenBtn');
  if (regenApiTokenBtn) {
    regenApiTokenBtn.addEventListener('click', async () => {
      if (!confirm('API 전송 토큰을 새로 재발급하시겠습니까?\n기존에 등록된 스마트폰 단축어 및 북마클릿의 이전 토큰은 즉시 무효화됩니다.')) {
        return;
      }
      try {
        const res = await fetch('/api/token/regenerate', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
          const tokenInput = document.getElementById('settingApiToken');
          if (tokenInput) tokenInput.value = data.api_token;
          if (serverInfo) {
            serverInfo.api_token = data.api_token;
            serverInfo.auth_token = data.api_token;
          }
          if (typeof window.updateIphoneFields === 'function') {
            window.updateIphoneFields();
          }
          showToast('새로운 API 전송 토큰이 발급되었습니다!');
        } else {
          showToast(data.error || '토큰 재발급 실패', true);
        }
      } catch (err) {
        showToast('토큰 재발급 중 오류: ' + err.message, true);
      }
    });
  }

  // Kone.gg 감시자 UI 이벤트 바인딩
  const settingKoneAutoCheck = document.getElementById('settingKoneAutoCheck');
  const koneOptionsContainer = document.getElementById('koneOptionsContainer');
  if (settingKoneAutoCheck && koneOptionsContainer) {
    settingKoneAutoCheck.addEventListener('change', () => {
      koneOptionsContainer.style.opacity = settingKoneAutoCheck.checked ? '1' : '0.5';
      koneOptionsContainer.style.pointerEvents = settingKoneAutoCheck.checked ? 'auto' : 'none';
    });
  }

  const koneCheckNowBtn = document.getElementById('koneCheckNowBtn');
  if (koneCheckNowBtn) {
    koneCheckNowBtn.addEventListener('click', async () => {
      koneCheckNowBtn.disabled = true;
      koneCheckNowBtn.innerHTML = '<span class="status-dot pulse"></span> 확인 중...';
      try {
        const res = await fetch('/api/kone/check-now', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
          if (data.initialized) {
            showToast(`Kone 감시자가 초기화되었습니다 (기존 글 ${data.seeded_count}개 기록됨).`);
          } else if (data.new_posts_count > 0) {
            showToast(`새 글 ${data.new_posts_count}개 발견! ${data.downloads_added}개 다운로드 추가됨`);
          } else {
            showToast('새로 등록된 게시글이 없습니다.');
          }
          await updateKoneStatusUI();
        } else {
          showToast(data.message || data.error || '확인 실패', true);
        }
      } catch (err) {
        showToast('서버 통신 실패: ' + err.message, true);
      } finally {
        koneCheckNowBtn.disabled = false;
        koneCheckNowBtn.innerHTML = '⚡ 지금 즉시 새 글 확인';
      }
    });
  }

  const koneRescanLatestBtn = document.getElementById('koneRescanLatestBtn');
  if (koneRescanLatestBtn) {
    koneRescanLatestBtn.addEventListener('click', async () => {
      koneRescanLatestBtn.disabled = true;
      koneRescanLatestBtn.textContent = '스캔 중...';
      try {
        const res = await fetch('/api/kone/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ recheck_latest_count: 1 }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
          showToast(`최신 게시글 1개를 다시 스캔했습니다. (${data.downloads_added || 0}개 다운로드 추가됨)`);
          await updateKoneStatusUI();
        } else {
          showToast(data.message || data.error || '재스캔 실패', true);
        }
      } catch (err) {
        showToast('서버 통신 실패: ' + err.message, true);
      } finally {
        koneRescanLatestBtn.disabled = false;
        koneRescanLatestBtn.textContent = '🔁 최신 글 다시 스캔';
      }
    });
  }

  const koneResetWatermarkBtn = document.getElementById('koneResetWatermarkBtn');
  if (koneResetWatermarkBtn) {
    koneResetWatermarkBtn.addEventListener('click', async () => {
      if (!confirm('현재 게시판의 최신 글을 새로운 감시 기준점으로 재설정하시겠습니까?\n(현재 올라와 있는 기존 글들은 건너뛰고, 이후 새로 올라오는 글부터 다운로드합니다.)')) {
        return;
      }
      koneResetWatermarkBtn.disabled = true;
      koneResetWatermarkBtn.textContent = '재설정 중...';
      try {
        const res = await fetch('/api/kone/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ recheck_latest_count: 0 }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
          const cpTitle = data.last_checked_post && data.last_checked_post.title ? data.last_checked_post.title : '';
          showToast(cpTitle ? `기준점 재설정 완료: [${cpTitle}]` : '감시 기준점이 현재 게시판 최신 글로 재설정되었습니다.');
          await updateKoneStatusUI();
        } else {
          showToast(data.message || data.error || '기준점 재설정 실패', true);
        }
      } catch (err) {
        showToast('서버 통신 실패: ' + err.message, true);
      } finally {
        koneResetWatermarkBtn.disabled = false;
        koneResetWatermarkBtn.textContent = '📌 기준점 재설정';
      }
    });
  }
  if (false) {
    (() => {
      try {
      } finally {
      }
    });
  }

  // 크롬 확장 모달
  const extensionBtn = document.getElementById('extensionBtn');
  const extensionModal = document.getElementById('extensionModal');
  const closeExtensionModal = document.getElementById('closeExtensionModal');

  if (extensionBtn && extensionModal) {
    extensionBtn.addEventListener('click', () => {
      extensionModal.classList.add('show');
    });
  }
  if (closeExtensionModal && extensionModal) {
    closeExtensionModal.addEventListener('click', () => extensionModal.classList.remove('show'));
  }

  // 스마트폰 모달 초기화
  initIphoneModal();

  // 모달 배경 클릭 시 닫기
  const iphoneModal = document.getElementById('iphoneModal');
  [settingsModal, extensionModal, iphoneModal].forEach((m) => {
    if (m) {
      m.addEventListener('click', (e) => {
        if (e.target === m) m.classList.remove('show');
      });
    }
  });
}

function initIphoneModal() {
  const iphoneBtn = document.getElementById('iphoneBtn');
  const iphoneQuickBtn = document.getElementById('iphoneQuickBtn');
  const iphoneModal = document.getElementById('iphoneModal');
  const closeIphoneModal = document.getElementById('closeIphoneModal');
  const shortcutPreview = document.getElementById('shortcutUrlPreview');
  const bookmarkletPreview = document.getElementById('bookmarkletCodePreview');
  const androidBookmarkletPreview = document.getElementById('androidBookmarkletPreview');
  const androidShortcutUrlPreview = document.getElementById('androidShortcutUrlPreview');
  const pwaServerUrl = document.getElementById('pwaServerUrl');
  const copyShortcutBtn = document.getElementById('copyShortcutUrlBtn');
  const copyBookmarkletBtn = document.getElementById('copyBookmarkletBtn');
  const copyAndroidBookmarkletBtn = document.getElementById('copyAndroidBookmarkletBtn');
  const copyAndroidShortcutBtn = document.getElementById('copyAndroidShortcutBtn');

  function updateIphoneFields() {
    let base = window.location.origin;
    if (base.includes('localhost') || base.includes('127.0.0.1')) {
      if (serverInfo && serverInfo.connect_urls && serverInfo.connect_urls.length > 0) {
        base = serverInfo.connect_urls[0];
      }
    }
    const token = (serverInfo && (serverInfo.api_token || serverInfo.auth_token)) ? (serverInfo.api_token || serverInfo.auth_token) : '';

    if (shortcutPreview) {
      shortcutPreview.value = `${base}/add?token=${token}&url=[단축어 입력]`;
    }
    const bookmarkletCode = `javascript:(()=>{const s=window.getSelection?String(window.getSelection()).trim():'';location.href='${base}/add?token=${token}&back=1&referer='+encodeURIComponent(location.href)+'&url='+encodeURIComponent(s||location.href);})();`;
    if (bookmarkletPreview) {
      bookmarkletPreview.value = bookmarkletCode;
    }
    if (androidBookmarkletPreview) {
      androidBookmarkletPreview.value = bookmarkletCode;
    }
    if (androidShortcutUrlPreview) {
      androidShortcutUrlPreview.value = `${base}/add?token=${token}&url={text}`;
    }
    if (pwaServerUrl) {
      pwaServerUrl.textContent = base;
    }
  }

  window.updateIphoneFields = updateIphoneFields;

  function openIphoneModal() {
    updateIphoneFields();
    if (iphoneModal) iphoneModal.classList.add('show');
  }

  if (iphoneBtn) iphoneBtn.addEventListener('click', openIphoneModal);
  if (iphoneQuickBtn) iphoneQuickBtn.addEventListener('click', openIphoneModal);
  if (closeIphoneModal && iphoneModal) {
    closeIphoneModal.addEventListener('click', () => iphoneModal.classList.remove('show'));
  }

  // 탭 전환
  const iphoneTabs = document.querySelectorAll('[data-iphone-tab]');
  iphoneTabs.forEach((btn) => {
    btn.addEventListener('click', () => {
      iphoneTabs.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');

      const targetTab = btn.dataset.iphoneTab;
      document.querySelectorAll('.iphone-tab-pane').forEach((pane) => {
        pane.style.display = 'none';
      });

      if (targetTab === 'shortcut') {
        const el = document.getElementById('iphoneTabShortcut');
        if (el) el.style.display = 'block';
      } else if (targetTab === 'android') {
        const el = document.getElementById('iphoneTabAndroid');
        if (el) el.style.display = 'block';
      } else if (targetTab === 'bookmarklet') {
        const el = document.getElementById('iphoneTabBookmarklet');
        if (el) el.style.display = 'block';
      } else if (targetTab === 'pwa') {
        const el = document.getElementById('iphoneTabPwa');
        if (el) el.style.display = 'block';
      } else if (targetTab === 'external') {
        const el = document.getElementById('iphoneTabExternal');
        if (el) el.style.display = 'block';
      }
    });
  });

  // 복사 버튼
  const setupCopyBtn = (btn, input, msg) => {
    if (btn && input) {
      btn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(input.value);
          showToast(msg);
        } catch (err) {
          showToast('복사 권한이 필요합니다.');
        }
      });
    }
  };

  setupCopyBtn(copyShortcutBtn, shortcutPreview, '아이폰 단축어 주소가 복사되었습니다!');
  setupCopyBtn(copyBookmarkletBtn, bookmarkletPreview, '북마클릿 코드가 복사되었습니다!');
  setupCopyBtn(copyAndroidBookmarkletBtn, androidBookmarkletPreview, '안드로이드 북마클릿 코드가 복사되었습니다!');
  setupCopyBtn(copyAndroidShortcutBtn, androidShortcutUrlPreview, '안드로이드 HTTP Shortcuts 주소가 복사되었습니다!');
}

function initTabs() {
  const tabs = document.querySelectorAll('.jobs-header .tab-btn');
  tabs.forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      switchTab(target);
    });
  });
}

function switchTab(tab) {
  currentTab = tab;
  currentPage = 1;
  document.querySelectorAll('.tab-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  renderJobs();
}

// 작업 제어 API 호출 함수들
window.pauseJob = async function (id) {
  await fetch(`/api/jobs/${id}/pause`, { method: 'POST' });
};

window.resumeJob = async function (id) {
  await fetch(`/api/jobs/${id}/resume`, { method: 'POST' });
};

window.cancelJob = async function (id) {
  await fetch(`/api/jobs/${id}/cancel`, { method: 'POST' });
};

window.retryJob = async function (id) {
  await fetch(`/api/jobs/${id}/retry`, { method: 'POST' });
};

window.deleteJob = async function (id) {
  if (confirm('이 작업을 목록에서 삭제하시겠습니까?')) {
    await fetch(`/api/jobs/${id}`, { method: 'DELETE' });
  }
};

// ==========================================
// 5. 설정 모달
// ==========================================

async function openSettings() {
  try {
    const res = await fetch('/api/settings');
    if (res.status === 401) {
      window.location.href = '/login';
      return;
    }
    if (res.ok) {
      const data = await res.json();
      settingDownloadDir.value = data.download_dir || '';
      if (settingPort) settingPort.value = data.port || 7971;
      settingMaxWorkers.value = data.max_concurrent_downloads || 3;
      settingGofileToken.value = data.gofile_token || '';
      if (settingTeraboxCookie) settingTeraboxCookie.value = data.terabox_cookie || '';
      const settingTeraboxPassword = document.getElementById('settingTeraboxPassword');
      if (settingTeraboxPassword) settingTeraboxPassword.value = data.terabox_password || '';

      const settingTeraboxMode = document.getElementById('settingTeraboxMode');
      const settingTeraboxSavePath = document.getElementById('settingTeraboxSavePath');
      if (settingTeraboxMode && data.terabox_mode) settingTeraboxMode.value = data.terabox_mode;
      if (settingTeraboxSavePath) settingTeraboxSavePath.value = data.terabox_save_path || '/';

      const settingAutoSubfolder = document.getElementById('settingAutoSubfolder');
      const settingSubfolderRule = document.getElementById('settingSubfolderRule');
      const settingCheckDuplicateUrl = document.getElementById('settingCheckDuplicateUrl');
      if (settingAutoSubfolder) settingAutoSubfolder.checked = data.auto_subfolder !== false;
      if (settingSubfolderRule && data.subfolder_rule) settingSubfolderRule.value = data.subfolder_rule;
      if (settingCheckDuplicateUrl) settingCheckDuplicateUrl.checked = data.check_duplicate_url !== false;
      const settingCaptureSourcePage = document.getElementById('settingCaptureSourcePage');
      if (settingCaptureSourcePage) settingCaptureSourcePage.checked = Boolean(data.capture_source_page);
      const settingIgnoreSslCerts = document.getElementById('settingIgnoreSslCerts');
      if (settingIgnoreSslCerts) settingIgnoreSslCerts.checked = Boolean(data.ignore_ssl_certs);

      const settingAuthUsername = document.getElementById('settingAuthUsername');
      const settingAuthPassword = document.getElementById('settingAuthPassword');
      const settingAuthPasswordConfirm = document.getElementById('settingAuthPasswordConfirm');
      if (settingAuthUsername) settingAuthUsername.value = data.auth_username || 'admin';
      if (settingAuthPassword) settingAuthPassword.value = '';
      if (settingAuthPasswordConfirm) settingAuthPasswordConfirm.value = '';
      validateAuthPasswordMatch();

      const settingSessionLifetime = document.getElementById('settingSessionLifetime');
      if (settingSessionLifetime && data.session_lifetime_days) {
        settingSessionLifetime.value = String(data.session_lifetime_days);
      }

      const settingApiToken = document.getElementById('settingApiToken');
      if (settingApiToken) settingApiToken.value = data.api_token || '';

      // Kone.gg 설정 채우기
      const settingKoneAutoCheck = document.getElementById('settingKoneAutoCheck');
      const settingKoneBoardUrl = document.getElementById('settingKoneBoardUrl');
      const settingKoneInterval = document.getElementById('settingKoneInterval');
      const settingKoneSubfolder = document.getElementById('settingKoneSubfolder');
      const koneOptionsContainer = document.getElementById('koneOptionsContainer');

      if (settingKoneAutoCheck) {
        settingKoneAutoCheck.checked = Boolean(data.kone_auto_check);
        if (koneOptionsContainer) {
          koneOptionsContainer.style.opacity = data.kone_auto_check ? '1' : '0.5';
          koneOptionsContainer.style.pointerEvents = data.kone_auto_check ? 'auto' : 'none';
        }
      }
      if (settingKoneBoardUrl) settingKoneBoardUrl.value = data.kone_board_url || 'https://kone.gg/s/retrogame';
      if (settingKoneInterval) settingKoneInterval.value = data.kone_check_interval_sec || 180;
      if (settingKoneSubfolder) settingKoneSubfolder.checked = data.kone_subfolder !== false;

      updateKoneStatusUI();
      if (typeof data !== 'undefined' && data && data.api_token && serverInfo) {
        serverInfo.api_token = data.api_token;
        serverInfo.auth_token = data.api_token;
      }
      if (typeof window.updateIphoneFields === 'function') {
        window.updateIphoneFields();
      }

      settingsModal.classList.add('show');
    }
  } catch (err) {
    showToast('설정 조회 실패', true);
  }
}

async function saveSettings(e) {
  e.preventDefault();
  const settingAutoSubfolder = document.getElementById('settingAutoSubfolder');
  const settingSubfolderRule = document.getElementById('settingSubfolderRule');
  const settingTeraboxMode = document.getElementById('settingTeraboxMode');
  const settingTeraboxSavePath = document.getElementById('settingTeraboxSavePath');

  let newPort = null;
  if (settingPort) {
    const parsedPort = parseInt(settingPort.value, 10);
    if (isNaN(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
      showToast('포트 번호는 1 ~ 65535 사이의 숫자여야 합니다.', true);
      settingPort.focus();
      return;
    }
    newPort = parsedPort;
  }

  const payload = {
    download_dir: settingDownloadDir.value.trim(),
    max_concurrent_downloads: parseInt(settingMaxWorkers.value, 10) || 3,
    gofile_token: settingGofileToken.value.trim(),
    terabox_cookie: settingTeraboxCookie ? settingTeraboxCookie.value.trim() : '',
    terabox_password: document.getElementById('settingTeraboxPassword') ? document.getElementById('settingTeraboxPassword').value.trim() : '',
    terabox_mode: settingTeraboxMode ? settingTeraboxMode.value : 'download',
    terabox_save_path: settingTeraboxSavePath ? (settingTeraboxSavePath.value.trim() || '/') : '/',
    auto_subfolder: settingAutoSubfolder ? settingAutoSubfolder.checked : true,
    subfolder_rule: settingSubfolderRule ? settingSubfolderRule.value : 'package_or_title',
  };

  if (newPort !== null) {
    payload.port = newPort;
  }

  const settingAuthUsername = document.getElementById('settingAuthUsername');
  const settingAuthPassword = document.getElementById('settingAuthPassword');
  const settingAuthPasswordConfirm = document.getElementById('settingAuthPasswordConfirm');
  if (settingAuthUsername && settingAuthUsername.value.trim()) {
    payload.auth_username = settingAuthUsername.value.trim();
  }

  const rawNewPw = settingAuthPassword ? settingAuthPassword.value.trim() : '';
  const rawConfirmPw = settingAuthPasswordConfirm ? settingAuthPasswordConfirm.value.trim() : '';

  if (rawNewPw) {
    if (!rawConfirmPw) {
      showToast('새 비밀번호 확인을 한 번 더 입력해주세요.', true);
      if (settingAuthPasswordConfirm) settingAuthPasswordConfirm.focus();
      return;
    }
    if (rawNewPw !== rawConfirmPw) {
      showToast('새 비밀번호와 비밀번호 확인이 일치하지 않습니다.', true);
      if (settingAuthPasswordConfirm) settingAuthPasswordConfirm.focus();
      return;
    }
    payload.auth_password = rawNewPw;
    payload.auth_password_confirm = rawConfirmPw;
  }

  const settingSessionLifetime = document.getElementById('settingSessionLifetime');
  if (settingSessionLifetime) {
    payload.session_lifetime_days = parseInt(settingSessionLifetime.value, 10) || 30;
  }

  // Kone.gg 설정 저장 페이로드
  const settingKoneAutoCheck = document.getElementById('settingKoneAutoCheck');
  const settingKoneBoardUrl = document.getElementById('settingKoneBoardUrl');
  const settingKoneInterval = document.getElementById('settingKoneInterval');
  const settingKoneSubfolder = document.getElementById('settingKoneSubfolder');

  if (settingKoneAutoCheck) payload.kone_auto_check = settingKoneAutoCheck.checked;
  if (settingKoneBoardUrl) payload.kone_board_url = settingKoneBoardUrl.value.trim() || 'https://kone.gg/s/retrogame';
  if (settingKoneInterval) payload.kone_check_interval_sec = parseInt(settingKoneInterval.value, 10) || 180;
  if (settingKoneSubfolder) payload.kone_subfolder = settingKoneSubfolder.checked;

  const settingCheckDuplicateUrl = document.getElementById('settingCheckDuplicateUrl');
  if (settingCheckDuplicateUrl) payload.check_duplicate_url = settingCheckDuplicateUrl.checked;
  const settingCaptureSourcePage = document.getElementById('settingCaptureSourcePage');
  if (settingCaptureSourcePage) payload.capture_source_page = settingCaptureSourcePage.checked;
  const settingIgnoreSslCerts = document.getElementById('settingIgnoreSslCerts');
  if (settingIgnoreSslCerts) payload.ignore_ssl_certs = settingIgnoreSslCerts.checked;

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) {
      window.location.href = '/login';
      return;
    }
    const resData = await res.json().catch(() => ({}));
    if (res.ok && resData.success !== false) {
      const isPortChanged = serverInfo && newPort && serverInfo.port && newPort !== serverInfo.port;
      if (isPortChanged) {
        showToast(`설정이 저장되었습니다. 변경된 포트(${newPort})는 서버 재시작 후 적용됩니다.`);
        serverInfo.port = newPort;
      } else {
        showToast('설정이 성공적으로 저장되었습니다.');
      }
      settingsModal.classList.remove('show');
      if (downloadDirText) {
        downloadDirText.textContent = payload.download_dir;
      }
      if (serverInfo) {
        serverInfo.terabox_mode = payload.terabox_mode;
        serverInfo.terabox_password = payload.terabox_password || '';
        serverInfo.capture_source_page = Boolean(payload.capture_source_page);
      }

      const teraboxPwdInput = document.getElementById('teraboxPwdInput');
      if (teraboxPwdInput) {
        teraboxPwdInput.placeholder = 'TeraBox 암호';
      }
      if (payload.auth_username) {
        const currentUsernameText = document.getElementById('currentUsernameText');
        if (currentUsernameText) currentUsernameText.textContent = payload.auth_username;
        if (serverInfo) serverInfo.username = payload.auth_username;
      }
      updateKoneStatusUI();
      if (typeof data !== 'undefined' && data && data.api_token && serverInfo) {
        serverInfo.api_token = data.api_token;
        serverInfo.auth_token = data.api_token;
      }
      if (typeof window.updateIphoneFields === 'function') {
        window.updateIphoneFields();
      }
    } else {
      showToast(resData.error || '설정 저장 실패', true);
    }
  } catch (err) {
    showToast('설정 저장 실패', true);
  }
}

function validateAuthPasswordMatch() {
  const settingAuthPassword = document.getElementById('settingAuthPassword');
  const settingAuthPasswordConfirm = document.getElementById('settingAuthPasswordConfirm');
  const helpEl = document.getElementById('settingAuthPasswordConfirmHelp');
  if (!settingAuthPassword || !settingAuthPasswordConfirm || !helpEl) return;

  const pw = settingAuthPassword.value.trim();
  const confirm = settingAuthPasswordConfirm.value.trim();

  if (!pw && !confirm) {
    helpEl.textContent = '비밀번호 변경 시 오입력을 방지하기 위해 동일하게 한 번 더 입력해주세요.';
    helpEl.style.color = '';
    settingAuthPasswordConfirm.style.borderColor = '';
    return;
  }

  if (pw && !confirm) {
    helpEl.textContent = '새 비밀번호 확인을 한 번 더 입력해주세요.';
    helpEl.style.color = '#f59e0b';
    settingAuthPasswordConfirm.style.borderColor = '#f59e0b';
    return;
  }

  if (pw === confirm) {
    helpEl.innerHTML = '<span style="color: #34d399; font-weight: 600;">✓ 새 비밀번호가 일치합니다.</span>';
    settingAuthPasswordConfirm.style.borderColor = '#10b981';
  } else {
    helpEl.innerHTML = '<span style="color: #f87171; font-weight: 600;">✕ 새 비밀번호가 일치하지 않습니다. 다시 확인해주세요.</span>';
    settingAuthPasswordConfirm.style.borderColor = '#ef4444';
  }
}

async function updateKoneStatusUI() {
  const badge = document.getElementById('koneStatusBadge');
  const text = document.getElementById('koneStatusText');
  if (!text) return;
  try {
    const res = await fetch('/api/kone/status');
    if (!res.ok) return;
    const data = await res.json();
    if (!data.enabled) {
      if (badge) badge.style.background = '#64748b';
      text.textContent = '비활성화됨 (체크 시 활성화)';
    } else {
      if (badge) badge.style.background = '#22c55e';
      const lastTimeStr = data.last_check_time
        ? new Date(data.last_check_time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        : '기록 없음';
      const msg = data.last_status && data.last_status.message ? ` (${data.last_status.message})` : '';
      text.textContent = `감시 작동 중 • 최근 확인: ${lastTimeStr} • 누적 ${data.seen_posts_count || 0}개 확인됨${msg}`;
    }

    // 현재 감시 기준점(마지막 확인 게시글) 렌더링
    const watermarkContent = document.getElementById('koneWatermarkContent');
    if (watermarkContent) {
      const cp = data.last_checked_post;
      if (cp && cp.title) {
        const catBadge = cp.category
          ? `<span style="display:inline-block; padding:1px 6px; border-radius:4px; background:rgba(99,102,241,0.2); color:#a5b4fc; font-size:0.72rem; font-weight:600; margin-right:6px;">${escapeHtml(cp.category)}</span>`
          : '';
        const titleHtml = cp.url
          ? `<a href="${escapeHtml(cp.url)}" target="_blank" rel="noopener noreferrer" style="color:#fff; font-weight:700; text-decoration:underline;">${escapeHtml(cp.title)} ↗</a>`
          : `<strong style="color:#fff;">${escapeHtml(cp.title)}</strong>`;
        const metaParts = [];
        if (cp.id) metaParts.push(`ID: ${escapeHtml(cp.id)}`);
        if (cp.post_time) metaParts.push(`작성: ${escapeHtml(cp.post_time)}`);
        if (cp.checked_at) metaParts.push(`확인 시각: ${escapeHtml(cp.checked_at)}`);
        if (cp.message) metaParts.push(`상태: ${escapeHtml(cp.message)}`);
        watermarkContent.innerHTML = `
          <div style="display:flex; align-items:center; flex-wrap:wrap; gap:4px; margin-bottom:2px;">
            ${catBadge}${titleHtml}
          </div>
          <div style="font-size:0.75rem; color:#cbd5e1;">
            ${metaParts.join(' • ')}
          </div>
        `;
      } else if ((data.seen_posts_count || 0) > 0) {
        watermarkContent.innerHTML = `기존 게시글 <strong>${data.seen_posts_count}개</strong>가 확인 완료로 등록되어 있습니다. <strong>⚡ 지금 즉시 새 글 확인</strong>을 누르면 최신 기준점 글 제목이 표시됩니다.`;
      } else {
        watermarkContent.innerHTML = `아직 확인된 게시글 기준점이 없습니다. <strong>⚡ 지금 즉시 새 글 확인</strong> 또는 <strong>📌 기준점 재설정</strong>을 눌러주세요.`;
      }
    }

    // 최근 감시 게시글 처리 내역 렌더링
    const logsContainer = document.getElementById('koneRecentLogsContainer');
    const logsList = document.getElementById('koneRecentLogs');
    if (logsContainer && logsList) {
      if (data.recent_logs && data.recent_logs.length > 0) {
        logsContainer.style.display = 'block';
        logsList.innerHTML = data.recent_logs.slice().reverse().map((log) => {
          let icon = '📝';
          let color = '#94a3b8';
          if (log.status === 'success') { icon = '✅'; color = '#4ade80'; }
          else if (log.status === 'failed') { icon = '❌'; color = '#f87171'; }
          else if (log.status === 'duplicate') { icon = '⚠️'; color = '#cbd5e1'; }
          else if (log.status === 'no_links') { icon = 'ℹ️'; color = '#94a3b8'; }
          else if (log.status === 'seeded') { icon = '📌'; color = '#34d399'; }
          else if (log.status === 'unprocessed_links') { icon = '📦'; color = '#fbbf24'; }
          const canManualDownload = (log.status === 'unprocessed_links' || log.status === 'failed') && log.url;
          const dlBtnHtml = canManualDownload
            ? `<button type="button" class="btn btn-secondary" style="font-size:0.68rem; padding:1px 6px; margin-left:4px; flex-shrink:0;" data-kone-url="${escapeHtml(log.url || '')}" data-kone-title="${escapeHtml(log.title || '')}" data-kone-id="${escapeHtml(log.id || '')}" onclick="window.downloadKonePostFromBtn(this)">⬇️ 받기</button>`
            : '';

          return `<div style="display:flex; justify-content:space-between; align-items:center; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.04); gap:6px;">
            <span style="color:${color}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:80%;">
              ${icon} ${log.url ? `<a href="${escapeHtml(log.url)}" target="_blank" rel="noopener noreferrer" style="color:inherit; font-weight:600; text-decoration:none;">${escapeHtml(log.title || '게시글')} ↗</a>` : `<strong>${escapeHtml(log.title || '게시글')}</strong>`}: ${escapeHtml(log.message || '')}
            </span>
            <span style="color:var(--text-muted); font-size:0.7rem; flex-shrink:0; display:flex; align-items:center; gap:4px;">${dlBtnHtml}${escapeHtml(log.time || '')}</span>
          </div>`;
        }).join('');
      } else {
        logsContainer.style.display = 'none';
      }
      if (data.recent_logs && data.recent_logs.some((lg) => lg.status === 'seeded') && !window._koneUpgradingSeeded) {
        window._koneUpgradingSeeded = true;
        fetch('/api/kone/check-now', { method: 'POST' })
          .then((r) => r.ok ? updateKoneStatusUI() : null)
          .catch(() => {})
          .finally(() => { window._koneUpgradingSeeded = false; });
      }
    }
  } catch (err) {
    // 무시
  }
}

window.downloadKonePostFromBtn = async function (btn) {
  if (!btn) return;
  const url = btn.dataset.koneUrl || '';
  const title = btn.dataset.koneTitle || '';
  const id = btn.dataset.koneId || '';
  btn.disabled = true;
  btn.textContent = '추출 중...';
  try {
    await window.downloadKonePost(url, title, id);
  } finally {
    btn.disabled = false;
    btn.textContent = '⬇️ 받기';
  }
};

window.downloadKonePost = async function (url, title, id) {
  try {
    showToast(`[${title || '게시글'}] 다운로드 링크 추출 중...`);
    const res = await fetch('/api/kone/download-post', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, title, id }),
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`[${title}]에서 ${data.downloads_added}개 다운로드를 추가했습니다!`);
      await updateKoneStatusUI();
    } else {
      showToast(data.error || '다운로드 추가 실패', true);
    }
  } catch (err) {
    showToast('서버 통신 실패: ' + err.message, true);
  }
};




// ==========================================
// 6. 유틸리티 함수
// ==========================================
function showToast(message, isError = false) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  if (isError) {
    toast.style.borderColor = 'rgba(239, 68, 68, 0.4)';
    toast.style.background = '#2a1215';
  }
  toast.textContent = message;
  toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 300);
  }, 3200);
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let val = bytes;
  for (let i = 0; i < units.length; i++) {
    if (val < 1024.0 || i === units.length - 1) {
      return (i !== 0 ? val.toFixed(1) : Math.round(val)) + ' ' + units[i];
    }
    val /= 1024.0;
  }
  return val.toFixed(1) + ' PB';
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}


// ==========================================
// 본 프로그램(Remote Downloader) GitHub 릴리즈 업데이트 관리
// ==========================================
let latestAppUpdateInfo = null;

async function checkAppUpdate(force = false) {
  const appUpdateBanner = document.getElementById('appUpdateBanner');
  const appUpdateTitle = document.getElementById('appUpdateTitle');
  const appUpdateSub = document.getElementById('appUpdateSub');
  const appUpdateNotesLink = document.getElementById('appUpdateNotesLink');
  const modalCurrentAppVer = document.getElementById('modalCurrentAppVer');
  const modalLatestAppVer = document.getElementById('modalLatestAppVer');
  const modalApplyAppUpdateBtn = document.getElementById('modalApplyAppUpdateBtn');
  const appVersionStatusBadge = document.getElementById('appVersionStatusBadge');
  const modalCheckAppUpdateBtn = document.getElementById('modalCheckAppUpdateBtn');

  // 기본적으로 배너는 항상 숨김 보장
  if (appUpdateBanner) {
    appUpdateBanner.style.display = 'none';
    appUpdateBanner.classList.add('hidden');
  }

  if (force && modalCheckAppUpdateBtn) {
    modalCheckAppUpdateBtn.disabled = true;
    modalCheckAppUpdateBtn.textContent = '🔄 확인 중...';
  }

  try {
    const res = await fetch(`/api/app/update/check?force=${force ? 'true' : 'false'}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.status !== 'ok') throw new Error(data.error || '버전 확인 실패');

    latestAppUpdateInfo = data;

    if (modalCurrentAppVer) modalCurrentAppVer.textContent = data.current_version;
    if (modalLatestAppVer) modalLatestAppVer.textContent = data.latest_version || '-';

    if (data.has_update) {
      // 신규 버전이 실제로 존재할 때만 배너 표시
      if (appUpdateBanner) {
        if (appUpdateTitle) {
          appUpdateTitle.textContent = `Remote Downloader 새 버전(${data.latest_version})이 출시되었습니다!`;
        }
        if (appUpdateSub) {
          appUpdateSub.textContent = `현재 버전: ${data.current_version} · 최신 실행 파일로 즉시 업데이트할 수 있습니다.`;
        }
        if (appUpdateNotesLink && data.release_url) {
          appUpdateNotesLink.href = data.release_url;
        }
        appUpdateBanner.style.display = 'flex';
        appUpdateBanner.classList.remove('hidden');
      }

      if (appVersionStatusBadge) {
        appVersionStatusBadge.textContent = '⚡ 새 버전 업데이트 가능';
        appVersionStatusBadge.style.background = 'rgba(16, 185, 129, 0.2)';
        appVersionStatusBadge.style.color = '#34d399';
      }
      if (modalApplyAppUpdateBtn) {
        modalApplyAppUpdateBtn.style.display = 'inline-flex';
        modalApplyAppUpdateBtn.classList.remove('hidden');
      }

      if (force) {
        showToast(`새로운 ${data.latest_version} 버전이 있습니다. 원클릭 업데이트를 진행할 수 있습니다.`, 'info');
      }
    } else {
      // 최신 버전 사용 중 -> 배너 숨김 확실히 유지
      if (appUpdateBanner) {
        appUpdateBanner.style.display = 'none';
        appUpdateBanner.classList.add('hidden');
      }
      if (appVersionStatusBadge) {
        appVersionStatusBadge.textContent = '✅ 최신 버전 사용 중';
        appVersionStatusBadge.style.background = 'rgba(59, 130, 246, 0.15)';
        appVersionStatusBadge.style.color = '#60a5fa';
      }
      if (modalApplyAppUpdateBtn) {
        modalApplyAppUpdateBtn.style.display = 'none';
        modalApplyAppUpdateBtn.classList.add('hidden');
      }

      if (force) {
        showToast('이미 최신 버전의 Remote Downloader를 사용하고 있습니다.', 'success');
      }
    }
  } catch (err) {
    console.debug('프로그램 업데이트 확인 건너뜀/오류:', err);
    // 오류 발생 시에도 배너는 노출되지 않도록 강제 숨김
    if (appUpdateBanner) {
      appUpdateBanner.style.display = 'none';
      appUpdateBanner.classList.add('hidden');
    }
    if (force) {
      showToast('GitHub 최신 버전 확인에 실패했습니다. 네트워크 상태를 확인하세요.', 'error');
    }
  } finally {
    if (modalCheckAppUpdateBtn) {
      modalCheckAppUpdateBtn.disabled = false;
      modalCheckAppUpdateBtn.textContent = '🔄 업데이트 확인';
    }
  }
}

async function applyAppUpdate() {
  const targetTag = latestAppUpdateInfo?.latest_version || '새 버전';
  const isFrozen = latestAppUpdateInfo?.is_frozen ?? true;

  const msg = isFrozen
    ? `Remote Downloader를 ${targetTag} 버전으로 업데이트하시겠습니까?

- 최신 실행 파일(RemoteDownloader.exe)을 다운로드한 후
- 기존 프로그램을 안전하게 교체하고 자동 재시작합니다.`
    : `Remote Downloader를 ${targetTag} 버전으로 업데이트하시겠습니까?

- Git pull을 통해 최신 소스코드를 즉시 동기화합니다.`;

  if (!confirm(msg)) {
    return;
  }

  const appUpdateBtn = document.getElementById('appUpdateBtn');
  const modalApplyAppUpdateBtn = document.getElementById('modalApplyAppUpdateBtn');

  [appUpdateBtn, modalApplyAppUpdateBtn].forEach((btn) => {
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span>⏳ 다운로드 및 업데이트 중...</span>';
    }
  });

  showToast('최신 실행 파일을 안전하게 다운로드 중입니다. 잠시만 기다려 주세요...', 'info');

  try {
    const res = await fetch('/api/app/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag: targetTag }),
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === 'ok') {
      showToast(data.message || '업데이트 성공! 프로그램을 재시작합니다.', 'success');

      // 재시작 카운트다운 안내 모달 오버레이 노출
      showRestartOverlay(targetTag);
    } else {
      const errMsg = data.error || (res.status === 401 ? '로그인이 필요한 작업입니다.' : '업데이트 실패');
      showToast(`업데이트 실패: ${errMsg}`, 'error');
      [appUpdateBtn, modalApplyAppUpdateBtn].forEach((btn) => {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '⚡ 지금 업데이트';
        }
      });
    }
  } catch (err) {
    console.error('업데이트 요청 오류:', err);
    showToast(`업데이트 요청 중 오류가 발생했습니다: ${err.message}`, 'error');
    [appUpdateBtn, modalApplyAppUpdateBtn].forEach((btn) => {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '⚡ 지금 업데이트';
      }
    });
  }
}

function showRestartOverlay(targetTag) {
  let overlay = document.getElementById('appRestartOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'appRestartOverlay';
    overlay.style.cssText = `
      position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
      background: rgba(10, 14, 23, 0.92); backdrop-filter: blur(8px);
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      z-index: 999999; color: #fff; text-align: center; padding: 20px;
    `;
    document.body.appendChild(overlay);
  }

  let count = 5;
  const updateOverlayText = () => {
    overlay.innerHTML = `
      <div style="background: rgba(30, 41, 59, 0.9); border: 1px solid rgba(59, 130, 246, 0.4); border-radius: 16px; padding: 36px 42px; max-width: 480px; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
        <div style="font-size: 2.8rem; margin-bottom: 14px;">🚀</div>
        <h3 style="font-size: 1.35rem; font-weight: 700; margin-bottom: 8px; color: #f8fafc;">Remote Downloader 업데이트 완료</h3>
        <p style="font-size: 0.92rem; color: #94a3b8; margin-bottom: 20px; line-height: 1.6;">
          <strong>${escapeHtml(targetTag)}</strong> 버전으로 파일 교체 및 재시작이 진행 중입니다.<br>
          <span style="color: #60a5fa; font-weight: 600;">${count}초</span> 후 자동으로 페이지를 새로고침합니다.
        </p>
        <div style="display: flex; justify-content: center;">
          <div class="status-dot pulse" style="width: 12px; height: 12px; background: #34d399;"></div>
        </div>
      </div>
    `;
  };

  updateOverlayText();
  const timer = setInterval(() => {
    count--;
    if (count <= 0) {
      clearInterval(timer);
      window.location.reload();
    } else {
      updateOverlayText();
    }
  }, 1000);
}
