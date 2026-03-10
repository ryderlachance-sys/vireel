(function () {
  var scriptOrigin = (function () {
    try {
      var s = document.currentScript && document.currentScript.src;
      if (s) { var u = new URL(s); return u.origin; }
    } catch (e) {}
    return '';
  })();
  // PROD_API_BASE_URL: use window.__API_BASE__ when set (e.g. Vercel build injects deployed backend URL); else script origin or local default.
  const API_BASE = (typeof window !== 'undefined' && window.__API_BASE__ !== undefined) ? window.__API_BASE__ : (scriptOrigin || 'http://127.0.0.1:8000');
  if (!API_BASE || API_BASE === 'null' || API_BASE === 'file://') {
    console.warn('[UI] API_BASE looks wrong:', API_BASE, '- set window.__API_BASE__ or use the app from the server');
  }
  const urlInput = document.getElementById('youtubeUrl');
  const maxClipsInput = document.getElementById('maxClips');

  if (urlInput) {
    urlInput.addEventListener('paste', function (e) {
      var pasted = '';
      try {
        pasted = (e.clipboardData && e.clipboardData.getData && e.clipboardData.getData('text')) || '';
      } catch (err) {
        console.warn('[UI] url paste clipboard read failed', err);
      }
      console.log('[UI] url paste clipboard_len=' + pasted.length + ' pasted="' + pasted + '"');
      e.preventDefault();
      urlInput.value = pasted;
      setTimeout(function () {
        var valueLen = urlInput.value.length;
        console.log('[UI] url after paste value_len=' + valueLen + ' value="' + urlInput.value + '"');
        if (pasted.length > 0 && valueLen < pasted.length) {
          alert('URL may have been truncated on paste (clipboard had ' + pasted.length + ' chars, input has ' + valueLen + '). Please try again or type the URL.');
        }
      }, 0);
    });
    urlInput.addEventListener('input', function () {
      console.log('[UI] url input value_len=' + urlInput.value.length);
    });
  }
  const clipSecondsInput = document.getElementById('clipSeconds');
  const generateBtn = document.getElementById('generateBtn');
  const progressSection = document.getElementById('progressSection');
  const progressFill = document.getElementById('progressFill');
  const progressStage = document.getElementById('progressStage');
  const progressLog = document.getElementById('progressLog');
  const cancelJobBtn = document.getElementById('cancelJobBtn');
  const resultsSection = document.getElementById('resultsSection');
  const clipsGrid = document.getElementById('clipsGrid');
  const panelGenerate = document.getElementById('panelGenerate');
  const panelStorymode = document.getElementById('panelStorymode');
  const storymodeGeneratePanel = document.getElementById('storymodeGeneratePanel');
  const storymodeLibraryPanel = document.getElementById('storymodeLibraryPanel');
  const libraryRefreshBtn = document.getElementById('libraryRefreshBtn');
  const libraryGrid = document.getElementById('libraryGrid');
  const libraryEmpty = document.getElementById('libraryEmpty');
  const librarySortSelect = document.getElementById('librarySortSelect');
  const libraryDebugLine = document.getElementById('libraryDebugLine');
  const librarySearchInput = document.getElementById('librarySearchInput');
  const librarySelectModeBtn = document.getElementById('librarySelectModeBtn');
  const libraryBulkBar = document.getElementById('libraryBulkBar');
  const libraryBulkCount = document.getElementById('libraryBulkCount');
  const libraryBulkDownload = document.getElementById('libraryBulkDownload');
  const libraryBulkPost = document.getElementById('libraryBulkPost');
  const libraryBulkDelete = document.getElementById('libraryBulkDelete');
  const libraryBulkClear = document.getElementById('libraryBulkClear');
  const libraryTiktokStatus = document.getElementById('libraryTiktokStatus');
  const libraryTiktokConnect = document.getElementById('libraryTiktokConnect');
  const libraryTiktokSetupPanel = document.getElementById('libraryTiktokSetupPanel');
  const libraryTiktokSetupStatus = document.getElementById('libraryTiktokSetupStatus');
  const libraryTiktokSetupBaseUrl = document.getElementById('libraryTiktokSetupBaseUrl');
  const libraryTiktokSetupPortalPrefix = document.getElementById('libraryTiktokSetupPortalPrefix');
  const libraryTiktokSetupRedirectUri = document.getElementById('libraryTiktokSetupRedirectUri');
  const libraryTiktokSetupCopyPrefix = document.getElementById('libraryTiktokSetupCopyPrefix');
  const libraryTiktokSetupCopyRedirect = document.getElementById('libraryTiktokSetupCopyRedirect');
  const libraryTiktokSetupOpenJson = document.getElementById('libraryTiktokSetupOpenJson');
  const libraryTiktokSetupToggle = document.getElementById('libraryTiktokSetupToggle');
  const libraryPanelTopN = document.getElementById('libraryPanelTopN');
  const libraryPanelSelectTopN = document.getElementById('libraryPanelSelectTopN');
  const libraryPanelDownloadTopN = document.getElementById('libraryPanelDownloadTopN');
  var libraryViewIndices = [];
  /** LIBRARY_SELECTION_MODE: when true, card click toggles multi-select; checkboxes visible */
  var librarySelectionMode = false;
  const librarySidePanel = document.getElementById('librarySidePanel');
  const libraryPanelCount = document.getElementById('libraryPanelCount');
  const libraryPanelActive = document.getElementById('libraryPanelActive');
  const libraryPanelTitles = document.getElementById('libraryPanelTitles');
  const libraryPanelCaption = document.getElementById('libraryPanelCaption');
  const libraryPanelHashtags = document.getElementById('libraryPanelHashtags');
  const libraryPanelCopyCaption = document.getElementById('libraryPanelCopyCaption');
  const libraryPanelCopyHashtags = document.getElementById('libraryPanelCopyHashtags');
  const libraryPanelCopyAll = document.getElementById('libraryPanelCopyAll');
  const libraryPanelCopyPath = document.getElementById('libraryPanelCopyPath');
  const libraryPanelDownloadPostPack = document.getElementById('libraryPanelDownloadPostPack');
  const libraryPanelFilename = document.getElementById('libraryPanelFilename');
  const libraryPanelRename = document.getElementById('libraryPanelRename');
  const libraryPanelPrev = document.getElementById('libraryPanelPrev');
  const libraryPanelNext = document.getElementById('libraryPanelNext');
  var libraryClipsList = [];
  var librarySelectedIndices = [];
  var libraryActiveIndex = 0;
  const winScoresDetails = document.getElementById('winScoresDetails');
  const winScoresTableWrap = document.getElementById('winScoresTableWrap');
  const backendStatus = document.getElementById('backendStatus');
  const backendOfflineMsg = document.getElementById('backendOfflineMsg');
  const pingBackendBtn = document.getElementById('pingBackendBtn');
  const pingOut = document.getElementById('pingOut');
  const serverLogsDetails = document.getElementById('serverLogsDetails');
  const serverLogsPre = document.getElementById('serverLogsPre');
  const resultsJobHeader = document.getElementById('resultsJobHeader');
  const resultsMismatchBanner = document.getElementById('resultsMismatchBanner');
  const jobsCardsWrap = document.getElementById('jobsCardsWrap');
  const jobsListEmpty = document.getElementById('jobsListEmpty');
  const progressErrorHelp = document.getElementById('progressErrorHelp');
  const copyErrorBtn = document.getElementById('copyErrorBtn');
  const activityList = document.getElementById('activityList');
  const logsDrawer = document.getElementById('logsDrawer');
  const viewLogsBtn = document.getElementById('viewLogsBtn');
  const copyLogsBtn = document.getElementById('copyLogsBtn');
  const progressStepper = document.getElementById('progressStepper');
  const toastContainer = document.getElementById('toastContainer');
  var jobsListPollIntervalId = null;
  var JOBS_POLL_MS = 1500;
  const batchUrls = document.getElementById('batchUrls');
  const queueStartBtn = document.getElementById('queueStartBtn');
  const queueTableBody = document.getElementById('queueTableBody');
  const queueEmpty = document.getElementById('queueEmpty');
  const queueTable = document.getElementById('queueTable');
  var queuePollTimer = null;
  const accountLoginWrap = document.getElementById('accountLoginWrap');
  const accountDashboardWrap = document.getElementById('accountDashboardWrap');
  const accountLoginError = document.getElementById('accountLoginError');
  const loginForm = document.getElementById('loginForm');
  const registerForm = document.getElementById('registerForm');
  const accountLogoutBtn = document.getElementById('accountLogoutBtn');
  const accountRefreshUsageBtn = document.getElementById('accountRefreshUsageBtn');
  const accountAdminWrap = document.getElementById('accountAdminWrap');
  const adminSetPlanBtn = document.getElementById('adminSetPlanBtn');
  const adminSetPlanOut = document.getElementById('adminSetPlanOut');
  var currentUser = null;
  var currentPlan = null;
  const storyInput = document.getElementById('storyInput');
  const storyVoice = document.getElementById('storyVoice');
  const storySpeed = document.getElementById('storySpeed');
  const storySpeedValue = document.getElementById('storySpeedValue');
  const storyTargetLength = document.getElementById('storyTargetLength');
  const storyCleanSplitBtn = document.getElementById('storyCleanSplitBtn');
  const storyOutput = document.getElementById('storyOutput');
  const storyRewriteBtn = document.getElementById('storyRewriteBtn');
  const storyHooksBtn = document.getElementById('storyHooksBtn');
  const storyHooksOutput = document.getElementById('storyHooksOutput');
  const storyHooksStatus = document.getElementById('storyHooksStatus');
  const storyPlayAllBtn = document.getElementById('storyPlayAllBtn');
  const storyStopAllBtn = document.getElementById('storyStopAllBtn');
  const storyModeFull = document.getElementById('storyModeFull');
  const storyModeParts = document.getElementById('storyModeParts');
  const storyFullVideoSection = document.getElementById('storyFullVideoSection');
  const storyPartsSection = document.getElementById('storyPartsSection');
  const storyMaxLength = document.getElementById('storyMaxLength');
  const storyAutoFit = document.getElementById('storyAutoFit');
  const storyCleanFormatBtn = document.getElementById('storyCleanFormatBtn');
  const storyEstimateBtn = document.getElementById('storyEstimateBtn');
  const storyPlayFullBtn = document.getElementById('storyPlayFullBtn');
  const storyStopBtn = document.getElementById('storyStopBtn');
  const storyFullStatus = document.getElementById('storyFullStatus');
  const storyTeleprompter = document.getElementById('storyTeleprompter');
  const storyAudioPlayer = document.getElementById('storyAudioPlayer');
  const storyDownloadWav = document.getElementById('storyDownloadWav');
  const storyGameplaySelect = document.getElementById('storyGameplaySelect');
  const storyGameplayPreview = document.getElementById('storyGameplayPreview');
  const storyRenderBtn = document.getElementById('storyRenderBtn');
  const storyRenderStatus = document.getElementById('storyRenderStatus');
  const storyRenderedPreview = document.getElementById('storyRenderedPreview');
  const storyDownloadMp4 = document.getElementById('storyDownloadMp4');
  const storyOpenTiktokBtn = document.getElementById('storyOpenTiktokBtn');
  const storyCopyCaptionBtn = document.getElementById('storyCopyCaptionBtn');
  const storyConfirmPostedBtn = document.getElementById('storyConfirmPostedBtn');
  const rvbAutoDownload = document.getElementById('rvbAutoDownload');
  const rvbAutoOpenTikTok = document.getElementById('rvbAutoOpenTikTok');
  const storyRedditProgress = document.getElementById('storyRedditProgress');
  const storyRedditProgressFill = document.getElementById('storyRedditProgressFill');
  const storyRedditStatus = document.getElementById('storyRedditStatus');
  const storyBackgroundUrl = document.getElementById('storyBackgroundUrl');
  const storyAddBackgroundBtn = document.getElementById('storyAddBackgroundBtn');
  const storyAddBackgroundStatus = document.getElementById('storyAddBackgroundStatus');
  const storyUseOpenAICheckbox = document.getElementById('storyUseOpenAICheckbox');
  const storyOpenAIRow = document.getElementById('storyOpenAIRow');

  var backendOnline = false;
  var storyAudioObjectUrl = null;
  var storyGameplayManifest = [];
  var storyLastRender = null;
  var healthFailures = 0;
  var lastHealthOkTime = 0;
  var HEALTH_TIMEOUT_MS = 12000;
  var HEALTH_FAILURES_OFFLINE = 3;
  var HEALTH_OK_MAX_AGE_ONLINE_MS = 60000;
  var HEALTH_OK_MAX_AGE_BUSY_MS = 120000;
  var currentJobId = null;
  var _libraryRefreshedForJobId = null;
  var progressES = null;  // singleton: only one EventSource at a time; close before opening new
  var logsRefreshTimer = null;

  function closeProgressES() {
    try {
      if (progressES) {
        progressES.close();
        console.log('[SSE] close');
      }
    } catch (e) {}
    progressES = null;
  }
  var lastKnownLogLines = [];
  var healthPollIntervalMs = 5000;
  var healthPollIntervalId = null;
  const lastHealthOkEl = document.getElementById('lastHealthOk');

  var STAGE_LABELS = { download: 'Downloading', transcribe: 'Transcribing', select: 'Selecting clips', clip: 'Creating clips', done: 'Done', idle: 'Starting', error: 'Error' };
  function stageToLabel(stage) {
    if (!stage) return '—';
    var key = (stage + '').toLowerCase().split(/[\s_]+/)[0];
    return STAGE_LABELS[key] || stage;
  }

  function showToast(message, type) {
    if (!toastContainer) return;
    var el = document.createElement('div');
    el.className = 'toast toast-' + (type || 'info');
    el.textContent = message;
    toastContainer.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 4000);
  }

  if (urlInput) {
    try {
      var saved = localStorage.getItem('clipper_last_url');
      if (saved && saved.trim()) urlInput.value = saved;
    } catch (e) {}
  }

  function esc(s) {
    if (s == null || s === undefined) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function setBackendStatus(level, port, lastOkTime) {
    if (!backendStatus) return;
    var label = 'Backend: ' + (level === 'online' ? 'Online' : level === 'busy' ? 'Busy' : 'Offline');
    if (level === 'online' && port != null && port !== '') {
      var host = '127.0.0.1';
      label += ' (' + host + ':' + port + ')';
    }
    backendStatus.textContent = label;
    backendStatus.classList.remove('online', 'degraded', 'busy', 'offline');
    backendStatus.classList.add(level === 'online' ? 'online' : level === 'busy' ? 'busy' : 'offline');
    if (lastHealthOkEl) {
      lastHealthOkEl.textContent = lastOkTime > 0
        ? 'last health OK: ' + new Date(lastOkTime).toLocaleTimeString()
        : '';
    }
  }

  function setHealthPollInterval(ms) {
    healthPollIntervalMs = ms;
    if (healthPollIntervalId) clearInterval(healthPollIntervalId);
    healthPollIntervalId = setInterval(checkBackend, ms);
  }

  var PING_TIMEOUT_MS = 2000;

  async function pingBackend() {
    if (!pingOut) return;
    var requestUrl = API_BASE + '/api/health';
    var lines = [
      'timestamp: ' + new Date().toISOString(),
      'window.location.origin: ' + (window.location.origin || '(empty)'),
      'request URL: ' + requestUrl,
      ''
    ];
    pingOut.textContent = lines.join('\n') + 'Pinging…\n';
    var controller = new AbortController();
    var timeoutId = setTimeout(function () { controller.abort(); }, PING_TIMEOUT_MS);
    try {
      var res = await fetch(requestUrl, { cache: 'no-store', signal: controller.signal });
      clearTimeout(timeoutId);
      var body = '';
      try {
        var data = await res.json();
        body = JSON.stringify(data, null, 2);
      } catch (_) {
        body = await res.text().catch(function () { return '(could not read body)'; });
      }
      lines.push('status code: ' + res.status);
      lines.push('response body:');
      lines.push(body);
      pingOut.textContent = lines.join('\n');
    } catch (e) {
      clearTimeout(timeoutId);
      var errName = (e && e.name) || 'Error';
      var errMsg = (e && e.message) || String(e);
      var isTimeout = errName === 'AbortError';
      if (isTimeout) {
        lines.push('Result: timeout');
        lines.push('timeout: yes (' + PING_TIMEOUT_MS + 'ms)');
      }
      lines.push('error: ' + errName + ' - ' + errMsg);
      if (!isTimeout) lines.push('timeout: no');
      pingOut.textContent = lines.join('\n');
    }
  }

  if (pingBackendBtn) {
    pingBackendBtn.addEventListener('click', function () {
      pingBackend();
    });
  }

  function checkBackend() {
    var controller = new AbortController();
    var timeoutId = setTimeout(function () { controller.abort(); }, HEALTH_TIMEOUT_MS);
    fetch(API_BASE + '/api/health', { cache: 'no-store', signal: controller.signal })
      .then(function (r) { clearTimeout(timeoutId); return r.ok ? r.json() : Promise.reject(new Error('health not ok')); })
      .then(function (data) {
        healthFailures = 0;
        backendOnline = true;
        lastHealthOkTime = Date.now();
        var port = data && (data.port != null) ? data.port : null;
        setBackendStatus('online', port, lastHealthOkTime);
        if (backendOfflineMsg) backendOfflineMsg.hidden = true;
        if (generateBtn) generateBtn.disabled = false;
        if (queueStartBtn) queueStartBtn.disabled = false;
      })
      .catch(function () {
        clearTimeout(timeoutId);
        healthFailures += 1;
        var now = Date.now();
        var ago = lastHealthOkTime > 0 ? (now - lastHealthOkTime) : Infinity;
        var level = 'offline';
        if (ago < HEALTH_OK_MAX_AGE_ONLINE_MS) {
          level = 'online';
        } else if (ago < HEALTH_OK_MAX_AGE_BUSY_MS) {
          level = 'busy';
        }
        if (healthFailures >= HEALTH_FAILURES_OFFLINE) level = 'offline';
        setBackendStatus(level, null, lastHealthOkTime);
        if (level === 'offline') {
          backendOnline = false;
          if (backendOfflineMsg) backendOfflineMsg.hidden = true;
          if (generateBtn) generateBtn.disabled = false;
          if (queueStartBtn) queueStartBtn.disabled = false;
          if (serverLogsDetails && serverLogsDetails.open) refreshServerLogs();
        } else {
          if (backendOfflineMsg) backendOfflineMsg.hidden = true;
        }
      });
  }

  function refreshServerLogs() {
    if (!serverLogsPre) return;
    serverLogsPre.textContent = '(Server log unavailable)';
    serverLogsPre.scrollTop = serverLogsPre.scrollHeight;
  }

  function startLogsRefresh() {
    refreshServerLogs();
    if (logsRefreshTimer) clearInterval(logsRefreshTimer);
    logsRefreshTimer = setInterval(refreshServerLogs, 2000);
  }

  function stopLogsRefresh() {
    if (logsRefreshTimer) {
      clearInterval(logsRefreshTimer);
      logsRefreshTimer = null;
    }
  }

  /* No backend health poll – like normal sites, you only see an error when an action fails */

  if (serverLogsDetails) {
    serverLogsDetails.addEventListener('toggle', function () {
      if (serverLogsDetails.open) {
        startLogsRefresh();
      } else {
        stopLogsRefresh();
      }
    });
  }

  var FREE_LIMIT_TOTAL = 9999; // Raised for local/dev use (default was 3)
  var PAYWALL_STORAGE_KEY = 'vireel_clips_total';

  function getClipsUsedTotal() {
    try {
      var n = parseInt(localStorage.getItem(PAYWALL_STORAGE_KEY), 10);
      return isNaN(n) ? 0 : Math.max(0, n);
    } catch (e) { return 0; }
  }
  function setClipsUsedTotal(n) {
    try { localStorage.setItem(PAYWALL_STORAGE_KEY, String(Math.max(0, n))); } catch (e) {}
  }

  function showTab(name) {
    document.querySelectorAll('.tabs .tab').forEach(function (t) {
      if (t.getAttribute('data-tab')) {
        t.classList.toggle('active', t.getAttribute('data-tab') === name);
        t.setAttribute('aria-selected', t.getAttribute('data-tab') === name ? 'true' : 'false');
      }
    });
    document.querySelectorAll('.tab-panel').forEach(function (p) {
      var panelId = 'panel' + name.charAt(0).toUpperCase() + name.slice(1);
      p.classList.toggle('active', p.id === panelId);
    });
    if (name === 'library') loadLibrary(false, 0, true, undefined, true);
  }

  function fetchOpts() {
    return { cache: 'no-store', credentials: 'include' };
  }

  function refreshAuth() {
    fetch(API_BASE + '/api/me', fetchOpts())
      .then(function (r) {
        if (r.ok) return r.json();
        if (r.status === 401) return Promise.reject(new Error('not_logged_in'));
        return Promise.reject(new Error('me failed'));
      })
      .then(function (data) {
        currentUser = data.user || null;
        currentPlan = data.plan || null;
        if (accountLoginWrap) accountLoginWrap.hidden = true;
        if (accountDashboardWrap) accountDashboardWrap.hidden = false;
        if (accountLoginError) { accountLoginError.hidden = true; accountLoginError.textContent = ''; }
        if (accountAdminWrap) accountAdminWrap.hidden = currentUser !== 'admin';
        if (document.getElementById('accountUser')) document.getElementById('accountUser').textContent = currentUser || '—';
        if (document.getElementById('accountPlan')) document.getElementById('accountPlan').textContent = currentPlan || '—';
        loadUsage();
      })
      .catch(function () {
        currentUser = null;
        currentPlan = null;
        if (accountLoginWrap) accountLoginWrap.hidden = false;
        if (accountDashboardWrap) accountDashboardWrap.hidden = true;
        if (accountAdminWrap) accountAdminWrap.hidden = true;
      });
  }

  function loadUsage() {
    if (!currentUser) return;
    fetch(API_BASE + '/api/usage', fetchOpts())
      .then(function (r) {
        if (!r.ok) return Promise.reject(new Error('usage failed'));
        return r.json();
      })
      .then(function (d) {
        var clipsToday = document.getElementById('accountClipsToday');
        var maxClips = document.getElementById('accountMaxClips');
        var remainingClips = document.getElementById('accountRemainingClips');
        var minutesToday = document.getElementById('accountMinutesToday');
        var maxMinutes = document.getElementById('accountMaxMinutes');
        var remainingMinutes = document.getElementById('accountRemainingMinutes');
        var watermarkEl = document.getElementById('accountWatermark');
        if (clipsToday) clipsToday.textContent = d.clips_today != null ? d.clips_today : 0;
        if (maxClips) maxClips.textContent = d.max_clips_per_day != null ? d.max_clips_per_day : 0;
        if (remainingClips) remainingClips.textContent = d.remaining_clips != null ? d.remaining_clips : 0;
        if (minutesToday) minutesToday.textContent = (d.minutes_today != null ? Number(d.minutes_today).toFixed(1) : '0');
        if (maxMinutes) maxMinutes.textContent = d.max_minutes_per_day != null ? d.max_minutes_per_day : 0;
        if (remainingMinutes) remainingMinutes.textContent = (d.remaining_minutes != null ? Number(d.remaining_minutes).toFixed(1) : '0');
        if (watermarkEl) watermarkEl.textContent = d.watermark_forced ? 'Forced ON (Free plan)' : 'Optional';
      })
      .catch(function () {});
  }

  if (loginForm) {
    loginForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var username = document.getElementById('loginUsername') && document.getElementById('loginUsername').value.trim();
      var password = document.getElementById('loginPassword') && document.getElementById('loginPassword').value;
      if (!username) return;
      if (accountLoginError) { accountLoginError.hidden = true; accountLoginError.textContent = ''; }
      fetch(API_BASE + '/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username, password: password || '' }),
        credentials: 'include',
        cache: 'no-store'
      })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || d.error || 'Login failed'); });
          return r.json();
        })
        .then(function () { refreshAuth(); })
        .catch(function (err) {
          if (accountLoginError) { accountLoginError.textContent = err.message || 'Login failed'; accountLoginError.hidden = false; }
        });
    });
  }
  if (registerForm) {
    registerForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var username = document.getElementById('registerUsername') && document.getElementById('registerUsername').value.trim();
      var password = document.getElementById('registerPassword') && document.getElementById('registerPassword').value;
      if (!username) return;
      if (accountLoginError) { accountLoginError.hidden = true; accountLoginError.textContent = ''; }
      fetch(API_BASE + '/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username, password: password || '' }),
        credentials: 'include',
        cache: 'no-store'
      })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || d.error || 'Register failed'); });
          return r.json();
        })
        .then(function () { refreshAuth(); })
        .catch(function (err) {
          if (accountLoginError) { accountLoginError.textContent = err.message || 'Register failed'; accountLoginError.hidden = false; }
        });
    });
  }
  if (accountLogoutBtn) {
    accountLogoutBtn.addEventListener('click', function () {
      fetch(API_BASE + '/api/logout', { method: 'POST', credentials: 'include', cache: 'no-store' })
        .then(function () { refreshAuth(); })
        .catch(function () { refreshAuth(); });
    });
  }
  if (accountRefreshUsageBtn) {
    accountRefreshUsageBtn.addEventListener('click', function () { loadUsage(); });
  }
  if (adminSetPlanBtn) {
    adminSetPlanBtn.addEventListener('click', function () {
      var username = document.getElementById('adminUsername') && document.getElementById('adminUsername').value.trim();
      var plan = document.getElementById('adminPlan') && document.getElementById('adminPlan').value;
      if (!username) { if (adminSetPlanOut) { adminSetPlanOut.textContent = 'Enter username'; adminSetPlanOut.hidden = false; } return; }
      if (adminSetPlanOut) adminSetPlanOut.textContent = '…';
      fetch(API_BASE + '/api/admin/users/' + encodeURIComponent(username) + '/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan: plan }),
        credentials: 'include',
        cache: 'no-store'
      })
        .then(function (r) {
          return r.json().then(function (d) {
            if (!r.ok) throw new Error(d.detail || d.error || 'Failed');
            return d;
          });
        })
        .then(function (d) {
          if (adminSetPlanOut) { adminSetPlanOut.textContent = 'Set ' + (d.user || username) + ' to ' + (d.plan || plan); adminSetPlanOut.hidden = false; }
        })
        .catch(function (err) {
          if (adminSetPlanOut) { adminSetPlanOut.textContent = err.message || 'Failed'; adminSetPlanOut.hidden = false; }
        });
    });
  }
  document.querySelectorAll('.tab').forEach(function (btn) {
    var tab = btn.getAttribute('data-tab');
    if (tab === 'account') {
      btn.addEventListener('click', function () {
        refreshAuth();
      });
    }
  });

  var headerOpenAppBtn = document.getElementById('headerOpenAppBtn');
  if (headerOpenAppBtn) {
    headerOpenAppBtn.addEventListener('click', function () {
      var el = document.getElementById('app-card');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  (function () {
    var WPM = 150;
    var storyModeChunks = [];
    var playAllStopped = false;
    var fullPlayScrollId = null;

    var syn = typeof window !== 'undefined' && window.speechSynthesis;
    if (syn && storyVoice) {
      function populateStoryVoices() {
        var voices = syn.getVoices();
        storyVoice.innerHTML = '';
        if (voices.length === 0) {
          storyVoice.appendChild(new Option('Loading voices…', ''));
          return;
        }
        voices.forEach(function (v, i) {
          var label = (v.name || 'Voice') + ' — ' + (v.lang || '');
          storyVoice.appendChild(new Option(label, String(i)));
        });
      }
      populateStoryVoices();
      if (syn.onvoiceschanged !== undefined) syn.onvoiceschanged = populateStoryVoices;
    }
    if (storySpeed && storySpeedValue) {
      storySpeed.addEventListener('input', function () {
        storySpeedValue.textContent = Number(storySpeed.value).toFixed(1);
      });
      storySpeedValue.textContent = Number(storySpeed.value).toFixed(1);
    }

    function cleanStory(text) {
      if (typeof text !== 'string') return '';
      var t = text
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .replace(/^[>\*\_\-]+/gm, '')
        .replace(/\s+/g, ' ')
        .trim();
      return t;
    }

    function wordCount(str) {
      return (str.match(/\S+/g) || []).length;
    }

    function cleanAndFormat(text) {
      if (typeof text !== 'string') return '';
      var t = cleanStory(text);
      var sentences = t.split(/(?<=[.!?])\s+/).filter(function (s) { return s.trim().length > 0; });
      if (sentences.length === 0) return t;
      var paras = [];
      for (var k = 0; k < sentences.length; k += 2) {
        paras.push(sentences.slice(k, k + 2).join(' '));
      }
      return paras.join('\n\n').replace(/\s{2,}/g, ' ').trim();
    }

    var HORROR_KEYWORDS = /\b(knock|light|dark|door|silence|shadow|inside)\b/i;
    var HORROR_TIME = /\b(\d{1,2}:\d{2}|AM|PM)\b/i;

    function horrorFormat(text) {
      if (typeof text !== 'string') return '';
      var t = text.replace(/\s+/g, ' ').trim();
      if (!t) return '';
      var sentences = t.split(/(?<=[.!?])\s+/).filter(function (s) { return s.trim().length > 0; });
      if (sentences.length === 0) return t;
      var lines = [];
      var i, j;
      for (i = 0; i < sentences.length; i++) {
        var sent = sentences[i].trim();
        if (wordCount(sent) > 18) {
          var parts = sent.split(/(\s*,\s*|\s+and\s+|\s+but\s+|\s+or\s+|\s+so\s+|\s+yet\s+)/i);
          var chunk = '';
          var chunkWords = 0;
          for (j = 0; j < parts.length; j++) {
            var p = parts[j];
            var w = wordCount(p);
            if (chunkWords + w > 18 && chunk.trim()) {
              lines.push(chunk.trim());
              chunk = p;
              chunkWords = w;
            } else {
              chunk = chunk + p;
              chunkWords = wordCount(chunk);
            }
          }
          if (chunk.trim()) lines.push(chunk.trim());
        } else {
          lines.push(sent);
        }
        var isDramatic = HORROR_KEYWORDS.test(sent) || HORROR_TIME.test(sent);
        if (isDramatic) lines.push('');
      }
      var paras = [];
      var current = [];
      for (i = 0; i < lines.length; i++) {
        if (lines[i] === '') {
          if (current.length) { paras.push(current); current = []; }
        } else {
          current.push(lines[i]);
        }
      }
      if (current.length) paras.push(current);
      for (i = 0; i < paras.length; i++) {
        var para = paras[i];
        var added = false;
        for (j = para.length - 1; j >= 0 && !added; j--) {
          var line = para[j];
          if (!/\.\.\.|!$/.test(line) && /[.!?]$/.test(line)) {
            para[j] = line.replace(/([.!?])$/, '$1...');
            added = true;
          }
        }
      }
      return paras.map(function (p) { return p.join('\n'); }).join('\n\n').replace(/\s{2,}/g, ' ').trim();
    }

    function formatDuration(sec) {
      var m = Math.floor(sec / 60);
      var s = Math.round(sec % 60);
      return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function splitIntoChunks(text, targetSec) {
      if (!text || !targetSec) return [];
      var targetWords = Math.floor((targetSec / 60) * WPM);
      if (targetWords < 1) targetWords = 1;
      var sentences = text.split(/(?<=[.!?])\s+/).filter(function (s) { return s.trim().length > 0; });
      if (sentences.length === 0) {
        if (text.trim()) return [{ text: text.trim(), wordCount: wordCount(text), estSec: Math.round((wordCount(text) / WPM) * 60) }];
        return [];
      }
      var chunks = [];
      var current = [];
      var currentWords = 0;
      for (var i = 0; i < sentences.length; i++) {
        var s = sentences[i].trim();
        if (!s) continue;
        var w = wordCount(s);
        if (currentWords + w > targetWords && current.length > 0) {
          var chunkText = current.join(' ');
          chunks.push({
            text: chunkText,
            wordCount: currentWords,
            estSec: Math.round((currentWords / WPM) * 60)
          });
          current = [];
          currentWords = 0;
        }
        current.push(s);
        currentWords += w;
      }
      if (current.length > 0) {
        var chunkText = current.join(' ');
        chunks.push({
          text: chunkText,
          wordCount: wordCount(chunkText),
          estSec: Math.round((wordCount(chunkText) / WPM) * 60)
        });
      }
      return chunks;
    }

    function speakChunk(chunkText, onEnd) {
      var syn = window.speechSynthesis;
      if (!syn) return;
      syn.cancel();
      var u = new SpeechSynthesisUtterance(chunkText);
      var rate = (storySpeed && storySpeed.value) ? parseFloat(storySpeed.value, 10) : 1;
      u.rate = Math.max(0.5, Math.min(2, rate));
      var voices = syn.getVoices();
      var idx = (storyVoice && storyVoice.value !== '') ? parseInt(storyVoice.value, 10) : 0;
      if (voices[idx]) u.voice = voices[idx];
      if (typeof onEnd === 'function') u.onend = onEnd;
      syn.speak(u);
    }

    var HOOK_TEMPLATES = [
      'Wait until you hear what happened with {topic}.',
      'This {topic} story went viral for a reason.',
      'You won\'t believe this {topic} story.',
      'So this {topic} thing happened and I had to share.',
      'POV: {topic} and it gets wild.',
      'Nobody talks about {topic} enough.',
      'This is the {topic} story that changed everything.',
      'I still can\'t get over this {topic} moment.',
      'Why did nobody warn me about {topic}.',
      'The {topic} saga continues.',
      'Story time: {topic}.',
      'If you\'ve ever wondered about {topic}, here it is.'
    ];

    function deriveTopic(text) {
      if (!text || typeof text !== 'string') return 'this';
      var trimmed = text.trim();
      var firstLine = trimmed.split(/\n/)[0] || '';
      firstLine = firstLine.trim();
      if (firstLine.length > 0 && firstLine.length <= 50 && /[.!?]/.test(firstLine) === false) return firstLine;
      var words = (trimmed.match(/\S+/g) || []).slice(0, 5);
      return words.length ? words.join(' ') : 'this';
    }

    function rewritePipeline(text) {
      if (typeof text !== 'string') return '';
      var t = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(/\s+/g, ' ').trim();
      var fillers = /\b(just|really|basically|kind of|sort of|I mean|you know|actually|literally)\b/gi;
      t = t.replace(fillers, '');
      t = t.replace(/\s{2,}/g, ' ');
      var sentences = t.split(/(?<=[.!?])\s+/).filter(function (s) { return s.trim().length > 0; });
      var out = [];
      for (var i = 0; i < sentences.length; i++) {
        var s = sentences[i].trim();
        if (wordCount(s) > 22) {
          var parts = s.split(/,/);
          var acc = '';
          for (var j = 0; j < parts.length; j++) {
            var seg = parts[j].trim();
            if (j > 0) seg = seg.replace(/^\s*/, '');
            if (acc && wordCount(acc + ' ' + seg) > 22) {
              if (acc) out.push(acc + (acc.match(/[.!?]$/) ? '' : '.'));
              acc = seg;
            } else acc = acc ? acc + ', ' + seg : seg;
          }
          if (acc) out.push(acc + (acc.match(/[.!?]$/) ? '' : '.'));
        } else out.push(s);
      }
      var paras = [];
      for (var k = 0; k < out.length; k += 2) {
        paras.push(out.slice(k, k + 2).join(' '));
      }
      t = paras.join('\n\n').replace(/\s+([.,!?])/g, '$1').replace(/\s{2,}/g, ' ').trim();
      return t;
    }

    if (storyRewriteBtn && storyInput) {
      storyRewriteBtn.addEventListener('click', function () {
        var raw = (storyInput.value || '').trim();
        if (!raw) return;
        storyInput.value = rewritePipeline(raw);
      });
    }

    if (storyHooksBtn && storyInput && storyHooksOutput && storyHooksStatus) {
      storyHooksBtn.addEventListener('click', function () {
        var raw = (storyInput.value || '').trim();
        var topic = deriveTopic(raw);
        if (!topic) topic = 'this';
        var used = {};
        var hooks = [];
        while (hooks.length < 5 && hooks.length < HOOK_TEMPLATES.length) {
          var i = Math.floor(Math.random() * HOOK_TEMPLATES.length);
          if (used[i]) continue;
          used[i] = true;
          hooks.push(HOOK_TEMPLATES[i].replace(/\{topic\}/g, topic));
        }
        storyHooksOutput.innerHTML = '';
        storyHooksStatus.textContent = '';
        hooks.forEach(function (hook) {
          var pill = document.createElement('button');
          pill.type = 'button';
          pill.className = 'storymode-hook-pill';
          pill.textContent = hook;
          pill.addEventListener('click', function () {
            navigator.clipboard.writeText(hook).then(function () {
              storyHooksStatus.textContent = 'Copied hook.';
              storyHooksStatus.className = 'storymode-hooks-status storymode-hooks-status-ok';
              setTimeout(function () { storyHooksStatus.textContent = ''; storyHooksStatus.className = 'storymode-hooks-status'; }, 2000);
            }).catch(function () { storyHooksStatus.textContent = 'Copy failed.'; storyHooksStatus.className = 'storymode-hooks-status storymode-hooks-status-err'; });
          });
          storyHooksOutput.appendChild(pill);
        });
      });
    }

    if (storyPlayAllBtn && storyStopAllBtn) {
      storyPlayAllBtn.addEventListener('click', function () {
        if (storyModeChunks.length === 0) {
          if (storyOutput) storyOutput.innerHTML = '<p class="storymode-empty">Run Clean &amp; Split first.</p>';
          return;
        }
        playAllStopped = false;
        var index = 0;
        function playNext() {
          if (playAllStopped || index >= storyModeChunks.length) return;
          speakChunk(storyModeChunks[index].text, function () {
            index++;
            if (index < storyModeChunks.length && !playAllStopped) playNext();
          });
        }
        playNext();
      });
      storyStopAllBtn.addEventListener('click', function () {
        playAllStopped = true;
        if (window.speechSynthesis) window.speechSynthesis.cancel();
      });
    }

    if (storyModeFull && storyModeParts && storyFullVideoSection && storyPartsSection) {
      function updateStoryModeVisibility() {
        var full = storyModeFull.checked;
        storyFullVideoSection.hidden = !full;
        storyPartsSection.hidden = full;
      }
      storyModeFull.addEventListener('change', updateStoryModeVisibility);
      storyModeParts.addEventListener('change', updateStoryModeVisibility);
      updateStoryModeVisibility();
    }

    if (storyCleanFormatBtn && storyInput) {
      var storyHorrorModeEl = document.getElementById('storyHorrorMode');
      storyCleanFormatBtn.addEventListener('click', function () {
        var raw = (storyInput.value || '').trim();
        if (!raw) return;
        var formatted = storyHorrorModeEl && storyHorrorModeEl.checked
          ? horrorFormat(cleanStory(raw))
          : cleanAndFormat(raw);
        storyInput.value = formatted;
        if (storyTeleprompter) {
          storyTeleprompter.textContent = storyInput.value;
          storyTeleprompter.scrollTop = 0;
        }
      });
    }

    if (storyEstimateBtn && storyInput && storyFullStatus && storyMaxLength) {
      storyEstimateBtn.addEventListener('click', function () {
        var text = (storyInput.value || '').trim();
        if (!text) {
          storyFullStatus.textContent = 'Paste or clean text first.';
          return;
        }
        var words = wordCount(text);
        var estSec = Math.round((words / WPM) * 60);
        var maxSec = parseInt(storyMaxLength.value, 10) || 300;
        var msg = 'Estimated: ' + estSec + ' sec (' + formatDuration(estSec) + ').';
        if (estSec > maxSec) {
          msg += ' Too long for ' + formatDuration(maxSec) + '. Consider trimming or increasing speed.';
        }
        storyFullStatus.textContent = msg;
      });
    }

    function stopFullPlayback() {
      if (fullPlayScrollId != null) {
        cancelAnimationFrame(fullPlayScrollId);
        fullPlayScrollId = null;
      }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      if (storyAudioPlayer) {
        storyAudioPlayer.pause();
        storyAudioPlayer.removeAttribute('src');
        storyAudioPlayer.load();
      }
      if (storyAudioObjectUrl) {
        try { URL.revokeObjectURL(storyAudioObjectUrl); } catch (e) {}
        storyAudioObjectUrl = null;
      }
      if (storyDownloadWav) {
        storyDownloadWav.removeAttribute('href');
        storyDownloadWav.hidden = true;
      }
      if (storyFullStatus) storyFullStatus.textContent = 'Stopped.';
    }

    if (storyPlayFullBtn && storyInput && storyFullStatus && storyTeleprompter && storyAudioPlayer) {
      storyPlayFullBtn.addEventListener('click', function () {
        var text = (storyInput.value || '').trim();
        if (!text) {
          storyFullStatus.textContent = 'Paste or clean text first.';
          return;
        }
        storyTeleprompter.textContent = text;
        storyTeleprompter.scrollTop = 0;
        storyFullStatus.textContent = 'Generating audio…';
        if (storyAudioObjectUrl) {
          try { URL.revokeObjectURL(storyAudioObjectUrl); } catch (e) {}
          storyAudioObjectUrl = null;
        }
        if (storyDownloadWav) {
          storyDownloadWav.removeAttribute('href');
          storyDownloadWav.hidden = true;
        }
        fetch('/api/tts_offline', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: text })
        })
          .then(function (res) {
            if (!res.ok) {
              return res.text().then(function (text) {
                var data = {};
                try { data = JSON.parse(text); } catch (e) {}
                var msg = data.detail || data.error || 'TTS failed (' + res.status + ')';
                if (res.status === 503) {
                  msg = 'Offline voice not set up. ' + (data.detail || data.error || 'Set PIPER_BIN and PIPER_MODEL. See README.');
                }
                throw new Error(msg);
              });
            }
            return Promise.all([ res.blob(), Promise.resolve(res.headers.get('X-Filename') || 'story.wav') ]);
          })
          .then(function (arr) {
            var blob = arr[0];
            var filename = arr[1] || 'story.wav';
            storyAudioObjectUrl = URL.createObjectURL(blob);
            storyAudioPlayer.src = storyAudioObjectUrl;
            if (storyDownloadWav) {
              storyDownloadWav.href = storyAudioObjectUrl;
              storyDownloadWav.download = filename;
              storyDownloadWav.hidden = false;
            }
            storyFullStatus.textContent = 'Playing (offline neural voice)…';
            storyAudioPlayer.play().catch(function (e) {
              if (storyFullStatus) storyFullStatus.textContent = 'Play error: ' + (e.message || 'unknown');
            });
          })
          .catch(function (err) {
            if (storyFullStatus) storyFullStatus.textContent = 'Error: ' + (err.message || 'offline TTS failed');
          });
        storyAudioPlayer.onended = function () {
          if (storyFullStatus) storyFullStatus.textContent = 'Finished.';
        };
        storyAudioPlayer.onerror = function () {
          if (storyFullStatus) storyFullStatus.textContent = 'Playback error.';
        };
      });
    }

    if (storyStopBtn) {
      storyStopBtn.addEventListener('click', function () {
        stopFullPlayback();
      });
    }

    function refreshGameplayDropdown(selectId) {
      if (!storyGameplaySelect) return;
      if (storyRenderBtn) storyRenderBtn.disabled = true;
      fetch(API_BASE + '/api/backgrounds', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (data) {
          var list = Array.isArray(data.gameplay) ? data.gameplay : [];
          storyGameplayManifest = list;
          storyGameplaySelect.innerHTML = '';
          if (list.length === 0) {
            storyGameplaySelect.innerHTML = '<option value="">No gameplay videos found. Add .mp4 files to assets/gameplay or paste a YouTube link to add one.</option>';
            if (storyRenderBtn) storyRenderBtn.disabled = true;
            updateGameplayPreview();
            return Promise.resolve(null);
          }
          list.forEach(function (e) {
            var opt = document.createElement('option');
            opt.value = e.id || '';
            opt.textContent = e.name || e.id || e.file || '';
            storyGameplaySelect.appendChild(opt);
          });
          if (selectId) {
            storyGameplaySelect.value = selectId;
          } else {
            storyGameplaySelect.value = list[0].id || '';
          }
          if (storyRenderBtn) storyRenderBtn.disabled = !storyGameplaySelect.value;
          updateGameplayPreview();
          return selectId || storyGameplaySelect.value;
        })
        .catch(function () {
          storyGameplaySelect.innerHTML = '<option value="">Failed to load</option>';
          if (storyRenderBtn) storyRenderBtn.disabled = true;
        });
    }
    (function initGameplayManifest() {
      if (!storyGameplaySelect) return;
      refreshGameplayDropdown();
    })();
    (function initRedditOpenAICheckbox() {
      if (!storyUseOpenAICheckbox || !storyOpenAIRow) return;
      fetch(API_BASE + '/api/reddit/config', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (data) {
          var enabled = !!data.openai_enabled;
          storyUseOpenAICheckbox.checked = true;
          storyUseOpenAICheckbox.disabled = enabled;
          storyOpenAIRow.style.display = enabled ? '' : 'none';
          if (enabled && storyUseOpenAICheckbox.parentElement) {
            var lbl = storyUseOpenAICheckbox.parentElement;
            if (lbl.childNodes && lbl.childNodes.length > 1 && lbl.childNodes[1].nodeType === 3) lbl.childNodes[1].nodeValue = ' Use OpenAI (always on)';
          }
        })
        .catch(function () {
          storyUseOpenAICheckbox.checked = false;
          storyOpenAIRow.style.display = 'none';
        });
    })();
    function updateGameplayPreview() {
      if (!storyGameplayPreview || !storyGameplaySelect) return;
      var id = storyGameplaySelect.value;
      var e = storyGameplayManifest.find(function (x) { return (x.id || '') === id; });
      if (e && e.file) {
        storyGameplayPreview.src = '/assets/gameplay/' + encodeURIComponent(e.file);
        storyGameplayPreview.style.display = 'block';
      } else {
        storyGameplayPreview.removeAttribute('src');
        storyGameplayPreview.style.display = 'none';
      }
    }
    if (storyGameplaySelect) {
      storyGameplaySelect.addEventListener('change', function () {
        updateGameplayPreview();
        if (storyRenderBtn) storyRenderBtn.disabled = !storyGameplaySelect.value;
      });
    }

    if (storyAddBackgroundBtn && storyBackgroundUrl && storyAddBackgroundStatus) {
      storyAddBackgroundBtn.addEventListener('click', function () {
        var url = (storyBackgroundUrl.value || '').trim();
        if (!url) {
          storyAddBackgroundStatus.textContent = 'Enter a YouTube URL.';
          return;
        }
        storyAddBackgroundBtn.disabled = true;
        storyAddBackgroundStatus.textContent = 'Downloading… Can take 1–2 min for long videos.';
        var controller = new AbortController();
        var timeoutId = setTimeout(function () {
          controller.abort();
        }, 620000);
        fetch(API_BASE + '/api/backgrounds/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url }),
          signal: controller.signal
        })
          .then(function (r) {
            clearTimeout(timeoutId);
            return r.json().then(function (data) {
              if (!r.ok) throw new Error(data.detail || data.error || 'Add failed');
              return data;
            });
          })
          .then(function (data) {
            storyAddBackgroundStatus.textContent = 'Added: ' + (data.name || data.id || '');
            storyBackgroundUrl.value = '';
            refreshGameplayDropdown(data.id);
          })
          .catch(function (err) {
            clearTimeout(timeoutId);
            if (err.name === 'AbortError') {
              storyAddBackgroundStatus.textContent = 'Request timed out (10 min). Try a shorter video or try again.';
            } else {
              storyAddBackgroundStatus.textContent = 'Error: ' + (err.message || 'failed');
            }
          })
          .finally(function () {
            storyAddBackgroundBtn.disabled = false;
          });
      });
    }

    (function initRvbToggles() {
      try {
        if (rvbAutoDownload) {
          rvbAutoDownload.checked = localStorage.getItem('rvbAutoDownload') === '1';
          rvbAutoDownload.addEventListener('change', function () {
            try { localStorage.setItem('rvbAutoDownload', rvbAutoDownload.checked ? '1' : '0'); } catch (e) {}
          });
        }
        if (rvbAutoOpenTikTok) {
          rvbAutoOpenTikTok.checked = localStorage.getItem('rvbAutoOpenTikTok') === '1';
          rvbAutoOpenTikTok.addEventListener('change', function () {
            try { localStorage.setItem('rvbAutoOpenTikTok', rvbAutoOpenTikTok.checked ? '1' : '0'); } catch (e) {}
          });
        }
      } catch (e) {}
    })();

    function triggerRvbDownload() {
      if (storyLastRender && storyLastRender.mp4_url && storyDownloadMp4) {
        var a = document.createElement('a');
        a.href = storyLastRender.mp4_url;
        a.download = storyLastRender.filename || 'reddit_video.mp4';
        a.click();
      }
    }

    if (storyRenderBtn && storyInput && storyRenderStatus && storyRenderedPreview && storyGameplaySelect) {
      storyRenderBtn.addEventListener('click', function () {
        var text = (storyInput.value || '').trim();
        if (!text) {
          storyRenderStatus.textContent = 'Paste story text first.';
          return;
        }
        var gameplayId = (storyGameplaySelect.value || '').trim();
        if (!gameplayId) {
          storyRenderStatus.textContent = 'Select a gameplay background.';
          return;
        }
        storyRenderBtn.disabled = true;
        storyLastRender = null;
        if (storyDownloadMp4) { storyDownloadMp4.hidden = true; storyDownloadMp4.removeAttribute('href'); }
        storyRenderedPreview.removeAttribute('src');
        storyRenderedPreview.hidden = true;
        if (storyRedditProgress) { storyRedditProgress.hidden = false; }
        if (storyRedditStatus) storyRedditStatus.textContent = 'Starting…';
        if (storyRedditProgressFill) storyRedditProgressFill.style.width = '0%';
        storyRenderStatus.textContent = '';

        var useOpenAI = !!(storyUseOpenAICheckbox && (storyUseOpenAICheckbox.checked || storyUseOpenAICheckbox.disabled));
        fetch(API_BASE + '/api/reddit/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ story_text: text, gameplay: gameplayId, options: { use_openai: useOpenAI } })
        })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) throw new Error(data.detail || data.error || 'Generate failed');
              return data;
            });
          })
          .then(function (data) {
            var jobId = data.job_id;
            if (!jobId) throw new Error('No job_id');
            var pollStart = Date.now();
            var POLL_TIMEOUT_MS = 120000;
            var poll = function () {
              if (Date.now() - pollStart > POLL_TIMEOUT_MS) {
                storyRenderStatus.textContent = 'Request timed out (120s). Try again.';
                if (storyRedditProgress) storyRedditProgress.hidden = true;
                storyRenderBtn.disabled = false;
                return;
              }
              fetch(API_BASE + '/api/reddit/status/' + encodeURIComponent(jobId), { cache: 'no-store' })
                .then(function (r) { return r.json(); })
                .then(function (st) {
                  if (storyRedditStatus) storyRedditStatus.textContent = st.message || st.stage || '';
                  if (storyRedditProgressFill) storyRedditProgressFill.style.width = (st.progress != null ? st.progress : 0) + '%';
                  if (st.done) {
                    if (st.error) {
                      var errMsg = st.error;
                      if (/PIPER|piper|tts/i.test(errMsg)) {
                        errMsg = errMsg + ' Set PIPER_BIN and PIPER_MODEL (see webapp/README.txt — Offline Neural Voice Setup).';
                      }
                      storyRenderStatus.textContent = 'Error: ' + errMsg;
                      if (storyRedditProgress) storyRedditProgress.hidden = true;
                    } else {
                      storyRenderStatus.textContent = 'Done.';
                      var outputFile = st.output_file || (st.render_id || 'reddit_video') + '.mp4';
                      var renderId = st.render_id || '';
                      console.log('[REDDIT_UI] render_complete file=' + outputFile);
                      var baseUrl = (st.mp4_url && st.mp4_url.indexOf('/') === 0) ? (API_BASE.replace(/\/$/, '') + st.mp4_url) : (st.mp4_url || '');
                      var url = baseUrl ? (baseUrl + (baseUrl.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now() + (renderId ? '&r=' + renderId : '')) : '';
                      if (storyRenderedPreview && url) {
                        storyRenderedPreview.src = url;
                        storyRenderedPreview.load();
                        storyRenderedPreview.hidden = false;
                        console.log('[PREVIEW] loading=' + url);
                      } else if (storyRenderedPreview) {
                        storyRenderedPreview.removeAttribute('src');
                        storyRenderedPreview.hidden = true;
                      }
                      if (storyDownloadMp4 && baseUrl) {
                        storyDownloadMp4.href = baseUrl + (baseUrl.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now() + (renderId ? '&r=' + renderId : '');
                        storyDownloadMp4.download = outputFile;
                        storyDownloadMp4.hidden = false;
                      }
                      if (storyRedditProgress) storyRedditProgress.hidden = true;
                      storyLastRender = { mp4_url: baseUrl, filename: outputFile };
                      showTab('library');
                      loadLibrary(false, 0, true, outputFile);
                      // Keep refreshing so the new video shows up (file may appear after FFmpeg finishes writing)
                      setTimeout(function () { loadLibrary(false, 0, true, outputFile); }, 1500);
                      setTimeout(function () { loadLibrary(false, 0, true, outputFile); }, 3000);
                      setTimeout(function () { loadLibrary(false, 0, true); }, 5000);
                    }
                    storyRenderBtn.disabled = false;
                    return;
                  }
                  setTimeout(poll, 1500);
                })
                .catch(function (err) {
                  storyRenderStatus.textContent = 'Error: ' + (err.message || 'status check failed');
                  if (storyRedditProgress) storyRedditProgress.hidden = true;
                  storyRenderBtn.disabled = false;
                });
            };
            poll();
          })
          .catch(function (err) {
            var msg = err.message || 'generate failed';
            if (/PIPER|piper|tts_unavailable/i.test(msg)) {
              msg = msg + ' Set PIPER_BIN and PIPER_MODEL to your Piper path and voice model (see webapp/README.txt — Offline Neural Voice Setup).';
            }
            storyRenderStatus.textContent = 'Error: ' + msg;
            if (storyRedditProgress) storyRedditProgress.hidden = true;
            storyRenderBtn.disabled = false;
          });
      });
    }
    if (storyDownloadMp4) {
      storyDownloadMp4.addEventListener('click', function () {
        triggerRvbDownload();
      });
    }
    if (storyOpenTiktokBtn) {
      storyOpenTiktokBtn.addEventListener('click', function () {
        window.open('https://www.tiktok.com/upload', '_blank');
      });
    }
    if (storyCopyCaptionBtn) {
      storyCopyCaptionBtn.addEventListener('click', function () {
        var text = 'Part of a crazy story… 😳\n\n#reddit #redditstories #storytime';
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            if (storyRenderStatus) storyRenderStatus.textContent = 'Caption copied to clipboard.';
          }).catch(function () {
            if (storyRenderStatus) storyRenderStatus.textContent = 'Copy failed. Select and Ctrl+C.';
          });
        } else {
          if (storyRenderStatus) storyRenderStatus.textContent = 'Select the caption and press Ctrl+C.';
        }
      });
    }
    if (storyConfirmPostedBtn && storyRenderStatus) {
      storyConfirmPostedBtn.addEventListener('click', function () {
        storyRenderStatus.textContent = 'Posted. Next one.';
      });
    }

    if (storyCleanSplitBtn && storyInput && storyOutput) {
      storyCleanSplitBtn.addEventListener('click', function () {
        var raw = (storyInput.value || '').trim();
        if (!raw) {
          storyOutput.innerHTML = '<p class="storymode-empty">Paste some text first.</p>';
          storyModeChunks = [];
          return;
        }
        var cleaned = cleanStory(raw);
        storyInput.value = cleaned;
        var targetSec = parseInt(storyTargetLength && storyTargetLength.value ? storyTargetLength.value : 60, 10);
        var chunks = splitIntoChunks(cleaned, targetSec);
        storyModeChunks = chunks;
        storyOutput.innerHTML = '';
        if (chunks.length === 0) {
          storyOutput.innerHTML = '<p class="storymode-empty">No content after cleaning.</p>';
          return;
        }
        chunks.forEach(function (chunk, i) {
          var partNum = i + 1;
          var block = document.createElement('div');
          block.className = 'storymode-chunk';
          block.innerHTML =
            '<div class="storymode-chunk-header">' +
              '<span class="storymode-chunk-part">Part ' + partNum + '</span> ' +
              '<span class="storymode-chunk-meta">' + chunk.wordCount + ' words · ~' + chunk.estSec + ' sec</span> ' +
              '<button type="button" class="storymode-play-btn" data-chunk-index="' + i + '">Play</button>' +
            '</div>' +
            '<details class="storymode-chunk-preview"><summary>Text preview</summary><p class="storymode-chunk-text">' + esc(chunk.text) + '</p></details>';
          var playBtn = block.querySelector('.storymode-play-btn');
          if (playBtn) {
            playBtn.addEventListener('click', function () {
              var idx = parseInt(playBtn.getAttribute('data-chunk-index'), 10);
              if (chunks[idx]) speakChunk(chunks[idx].text);
            });
          }
          storyOutput.appendChild(block);
        });
      });
    }
  })();

  function refreshJobsList() {
    if (!jobsCardsWrap && !jobsListEmpty) return;
    fetch(API_BASE + '/api/jobs', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('jobs list failed')); })
      .then(function (data) {
        var list = (data && data.jobs) ? data.jobs : [];
        if (jobsCardsWrap) jobsCardsWrap.innerHTML = '';
        if (jobsListEmpty) jobsListEmpty.hidden = list.length > 0;
        if (!jobsCardsWrap) return;
        list.forEach(function (job) {
          var shortId = (job.id || '').substring(0, 6);
          var status = job.status || 'queued';
          var progress = job.progress != null ? Number(job.progress) : 0;
          var stage = (job.stage || job.message || '—').substring(0, 32);
          var clipsLabel = (job.clips_count != null) ? (job.clips_count + (job.max_clips ? '/' + job.max_clips : '')) : '—';
          var card = document.createElement('div');
          card.className = 'job-card';
          var pillClass = 'pill-' + status;
          var cancelHtml = status === 'running' ? '<button type="button" class="job-card-cancel-btn" data-job-id="' + esc(job.id) + '" aria-label="Cancel">×</button>' : '';
          if (status === 'error' && (job.source_url || '').trim()) {
            cancelHtml = '<button type="button" class="job-card-retry-btn btn btn-ghost btn-sm" data-source-url="' + esc(job.source_url || '') + '">Retry</button>';
          }
          card.innerHTML =
            '<span class="job-card-id">' + esc(shortId) + '</span>' +
            '<span class="job-card-pill ' + pillClass + '">' + esc(status) + '</span>' +
            '<div class="job-card-cancel-wrap">' + cancelHtml + '</div>' +
            '<span class="job-card-stage">' + esc(stage) + '</span>' +
            '<div class="job-card-bar-wrap"><div class="job-card-bar"><div class="job-card-bar-fill" style="width:' + progress + '%"></div></div></div>' +
            '<span class="job-card-clips">Clips: ' + esc(clipsLabel) + '</span>';
          var cancelBtn = card.querySelector('.job-card-cancel-btn');
          if (cancelBtn) {
            cancelBtn.addEventListener('click', function () {
              var jid = cancelBtn.getAttribute('data-job-id');
              if (!jid) return;
              cancelBtn.disabled = true;
              fetch(API_BASE + '/api/jobs/' + jid + '/cancel', { method: 'POST', cache: 'no-store' })
                .then(function (res) {
                  return res.json().then(function (d) {
                    if (!res.ok) throw new Error(d.error || d.detail || 'Cancel failed');
                    return d;
                  });
                })
                .then(function () { refreshJobsList(); })
                .catch(function (err) { alert(err.message || 'Cancel failed'); })
                .finally(function () { cancelBtn.disabled = false; });
            });
          }
          var retryBtn = card.querySelector('.job-card-retry-btn');
          if (retryBtn && urlInput) {
            retryBtn.addEventListener('click', function () {
              var srcUrl = retryBtn.getAttribute('data-source-url') || '';
              if (!srcUrl) return;
              urlInput.value = srcUrl;
              showTab('generate');
              if (generateBtn && !generateBtn.disabled) generateBtn.click();
            });
          }
          jobsCardsWrap.appendChild(card);
        });
      })
      .catch(function () {
        if (jobsListEmpty) { jobsListEmpty.hidden = false; jobsListEmpty.textContent = 'Could not load jobs.'; }
      });
  }

  function startJobsListPoll() {
    if (jobsListPollIntervalId) return;
    refreshJobsList();
    jobsListPollIntervalId = setInterval(refreshJobsList, JOBS_POLL_MS);
  }

  function stopJobsListPoll() {
    if (jobsListPollIntervalId) {
      clearInterval(jobsListPollIntervalId);
      jobsListPollIntervalId = null;
    }
  }

  document.querySelectorAll('.tabs .tab').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = btn.getAttribute('data-tab');
      if (!tab) return;
      closeProgressES();
      showTab(tab);
      if (tab === 'generate') {
        startJobsListPoll();
      } else if (tab === 'library') {
        loadLibrary(false, 0, true, undefined, true);
      }
      if (queuePollTimer) { clearInterval(queuePollTimer); queuePollTimer = null; }
    });
  });

  var generateLibraryBtn = document.getElementById('generateLibraryBtn');
  if (generateLibraryBtn) {
    generateLibraryBtn.addEventListener('click', function () {
      showTab('library');
    });
  }

  function loadQueue() {
    if (!queueTableBody && !queueEmpty) return;
    fetch(API_BASE + '/api/queue', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('queue failed')); })
      .then(function (data) {
        var items = (data && data.items) ? data.items : [];
        if (queueTable) queueTable.hidden = items.length === 0;
        if (queueEmpty) queueEmpty.hidden = items.length > 0;
        if (!queueTableBody) return;
        queueTableBody.innerHTML = '';
        items.forEach(function (it, i) {
          var tr = document.createElement('tr');
          var url = (it.url || '').substring(0, 60) + ((it.url || '').length > 60 ? '…' : '');
          var jobId = it.job_id || '—';
          var status = it.status || '—';
          var err = (it.error || '—').substring(0, 80) + ((it.error || '').length > 80 ? '…' : '');
          tr.innerHTML =
            '<td class="queue-url" title="' + esc(it.url || '') + '">' + esc(url) + '</td>' +
            '<td>' + esc(it.video_id || '—') + '</td>' +
            '<td>' + esc(jobId) + '</td>' +
            '<td class="queue-status queue-status-' + esc(status) + '">' + esc(status) + '</td>' +
            '<td class="queue-error" title="' + esc(it.error || '') + '">' + esc(err) + '</td>' +
            '<td>' + (it.status === 'error' ? '<button type="button" class="queue-retry-btn" data-index="' + i + '">Retry</button>' : '—') + '</td>';
          queueTableBody.appendChild(tr);
        });
        queueTableBody.querySelectorAll('.queue-retry-btn').forEach(function (b) {
          b.onclick = function () {
            var idx = parseInt(b.getAttribute('data-index'), 10);
            if (isNaN(idx) || idx < 0) return;
            queueStartBtn.disabled = true;
            fetch(API_BASE + '/api/queue/retry', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ index: idx }),
              cache: 'no-store'
            })
              .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
              .then(function () { loadQueue(); })
              .catch(function () { loadQueue(); })
              .then(function () { queueStartBtn.disabled = false; });
          };
        });
      })
      .catch(function () {
        if (queueEmpty) { queueEmpty.hidden = false; queueEmpty.textContent = 'Could not load queue.'; }
        if (queueTable) queueTable.hidden = true;
      });
  }

  if (queueStartBtn) {
    queueStartBtn.addEventListener('click', function () {
      var raw = (batchUrls && batchUrls.value) ? batchUrls.value : '';
      var urls = raw.split(/\r?\n/).map(function (s) { return s.trim(); }).filter(Boolean);
      if (urls.length === 0) {
        alert('Paste at least one YouTube URL (one per line).');
        return;
      }
      queueStartBtn.disabled = true;
      fetch(API_BASE + '/api/queue/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls: urls }),
        cache: 'no-store'
      })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('add failed')); })
        .then(function (data) {
          if (batchUrls) batchUrls.value = '';
          loadQueue();
        })
        .catch(function (err) {
          alert(err.message || 'Failed to add to queue.');
        })
        .then(function () { queueStartBtn.disabled = false; });
    });
  }

  function log(msg) {
    if (progressLog) {
      progressLog.textContent += msg + '\n';
      progressLog.scrollTop = progressLog.scrollHeight;
    }
    updateActivityFromLog(progressLog ? progressLog.textContent : '');
  }

  function setProgress(pct, stage, message) {
    if (progressFill) progressFill.style.width = pct + '%';
    if (progressStage) progressStage.textContent = message || stageToLabel(stage) || stage || '—';
    updateStepper(stage);
  }

  function setProgressLog(lines) {
    if (progressLog && lines && lines.length) {
      progressLog.textContent = lines.join('\n');
      progressLog.scrollTop = progressLog.scrollHeight;
      updateActivityFromLog(lines.join('\n'));
    }
  }

  function updateActivityFromLog(logText) {
    if (!activityList) return;
    var lines = (logText || '').split('\n').filter(function (l) { return l.trim(); });
    var events = [];
    var keywords = ['download', 'transcrib', 'select', 'render', 'clip', 'done', 'error', 'cancel', 'start', 'finish'];
    for (var i = lines.length - 1; i >= 0 && events.length < 6; i--) {
      var line = lines[i].trim();
      if (line.length > 80) line = line.substring(0, 77) + '…';
      events.unshift(line);
    }
    activityList.innerHTML = events.slice(-6).map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('');
  }

  function updateStepper(stage) {
    if (!progressStepper) return;
    var key = (stage || '').toLowerCase().split(/[\s_]+/)[0];
    var order = ['download', 'transcribe', 'select', 'render', 'done'];
    var idx = order.indexOf(key);
    if (idx === -1 && (key === 'clip' || key === 'rendering')) idx = 3;
    if (idx === -1 && (key === 'doctor' || key === 'idle' || key === 'sent' || key === 'starting')) idx = 0;
    if (idx === -1) idx = 0;
    progressStepper.querySelectorAll('.stepper-step').forEach(function (el, i) {
      el.classList.remove('active', 'done');
      if (i < idx) el.classList.add('done');
      else if (i === idx) el.classList.add('active');
    });
  }

  if (viewLogsBtn && logsDrawer) {
    viewLogsBtn.addEventListener('click', function () {
      logsDrawer.hidden = !logsDrawer.hidden;
      viewLogsBtn.textContent = logsDrawer.hidden ? 'View logs' : 'Hide logs';
    });
  }
  if (copyLogsBtn && progressLog) {
    copyLogsBtn.addEventListener('click', function () {
      var text = progressLog.textContent || '';
      if (!text.trim()) return;
      navigator.clipboard.writeText(text).then(function () { showToast('Logs copied to clipboard.'); }).catch(function () { alert('Copy failed.'); });
    });
  }

  if (!generateBtn) {
    console.error('[UI] generateBtn not found - cannot attach Generate handler');
  } else {
  generateBtn.addEventListener('click', async function () {
    if (generateBtn.disabled) return;
    if (getClipsUsedTotal() >= FREE_LIMIT_TOTAL) {
      var paywallEl = document.getElementById('paywallModal');
      if (paywallEl) paywallEl.hidden = false;
      return;
    }
    generateBtn.disabled = true;
    var urlEl = document.querySelector('#youtubeUrl') || document.querySelector('input[name="url"]');
    var urlRaw = (urlEl && urlEl.value != null ? urlEl.value : '');
    var url = urlRaw.trim();
    console.log('[UI] generate_click url_len=' + url.length + ' url=' + url);
    if (!url) {
      generateBtn.disabled = false;
      alert('Please enter a YouTube URL.');
      return;
    }

    closeProgressES();
    currentJobId = null;
    if (cancelJobBtn) cancelJobBtn.hidden = true;
    if (progressSection) progressSection.hidden = false;
    if (resultsSection) resultsSection.hidden = true;
    if (progressErrorHelp) progressErrorHelp.hidden = true;
    if (copyErrorBtn) copyErrorBtn.hidden = true;
    if (progressLog) progressLog.textContent = '';
    if (clipsGrid) clipsGrid.innerHTML = '';
    if (resultsJobHeader) resultsJobHeader.innerHTML = '';
    if (resultsMismatchBanner) { resultsMismatchBanner.hidden = true; resultsMismatchBanner.textContent = ''; }
    var srcLineEl = document.getElementById('resultsSourceLine');
    if (srcLineEl) srcLineEl.textContent = '';
    if (winScoresTableWrap) winScoresTableWrap.innerHTML = '';
    setProgress(0, 'idle', 'Sent URL: ' + url + ' (len=' + url.length + ')');

    let jobId;
    var clientRequestId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : '';
    var payload = {
      url: url,
      max_clips: parseInt(maxClipsInput && maxClipsInput.value, 10) || 6,
      clip_seconds: parseInt(clipSecondsInput && clipSecondsInput.value, 10) || 45,
      use_ollama: false,
      watermark: false,
      client_request_id: clientRequestId || undefined
    };
    if (!payload.client_request_id) delete payload.client_request_id;
    try { localStorage.setItem('clipper_last_url', url); } catch (e) {}
    console.log('[UI] POST /api/jobs sending url_len=' + url.length + ' body.url_len=' + payload.url.length);
    try {
      const res = await fetch(API_BASE + '/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        credentials: 'include',
        cache: 'no-store'
      }).catch(function () {
        throw new Error('Backend unreachable. Start the server (run webapp\\run_web.bat from the Vireel folder) and try again.');
      });
      if (res.status === 401) {
        alert('Please log in first.');
        refreshAuth();
        showTab('account');
        generateBtn.disabled = false;
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(function () { return {}; });
        const msg = err.error === 'missing_url' ? 'Please enter a YouTube URL.' : err.error === 'quota_exceeded' ? (err.detail || 'Daily quota exceeded.') : (err.detail || err.error || ('Failed to create job (HTTP ' + res.status + ')'));
        throw new Error(msg);
      }
      const data = await res.json();
      jobId = data.job_id;
      currentJobId = jobId;
      if (cancelJobBtn) cancelJobBtn.hidden = false;
      setHealthPollInterval(10000);
      const sourceVideoId = (data.source_video_id || 'unknown').replace(/</g, '&lt;');
      const sourceUrl = (data.source_url || '').replace(/</g, '&lt;');
      if (resultsJobHeader) {
        resultsJobHeader.innerHTML = '<div class="results-job-id">Current job: <code>' + jobId + '</code></div>' +
          '<div class="results-job-source">Source: <code>' + sourceVideoId + '</code></div>' +
          (sourceUrl ? '<div class="results-job-url">URL: <code>' + sourceUrl + '</code></div>' : '');
      }
      progressSection.querySelector('.progress-stage') && (progressSection.querySelector('.progress-stage').textContent = 'Generating for: ' + sourceVideoId);
      clipsGrid.innerHTML = '';

      closeProgressES();
      progressES = new EventSource(API_BASE + '/api/jobs/' + jobId + '/events');
      console.log('[SSE] open', jobId);
      progressES.addEventListener('progress', function (e) {
        try {
          const d = JSON.parse(e.data || '{}');
          setProgress(d.progress || 0, d.stage, d.message);
        } catch (_) {}
      });
      progressES.onerror = function () { closeProgressES(); };

      var jobPollStart = Date.now();
      var JOB_POLL_TIMEOUT_MS = 600000;
      function poll() {
        if (Date.now() - jobPollStart > JOB_POLL_TIMEOUT_MS) {
          closeProgressES();
          setHealthPollInterval(5000);
          log('Job status timed out (10 min). Try again.');
          if (progressErrorHelp) {
            progressErrorHelp.hidden = false;
            var titleEl = document.getElementById('progressErrorTitle');
            if (titleEl) titleEl.textContent = 'Request timed out.';
          }
          generateBtn.disabled = false;
          if (cancelJobBtn) cancelJobBtn.hidden = true;
          return;
        }
        return fetch(API_BASE + '/api/jobs/' + jobId + '?t=' + Date.now(), { cache: 'no-store' })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j.log_lines && j.log_lines.length) {
              setProgressLog(j.log_lines);
            }
            setProgress(j.progress || 0, j.stage, j.message);

            var completed = (j.state === 'done') || (j.state === 'done_pending_exit' && (j.progress === 100 || j.stage === 'done'));
            if (completed) {
              closeProgressES();
              setHealthPollInterval(5000);
              setProgress(100, 'done', j.message || 'Done');
              renderWinScores(j.win_scores);
              showTab('library');
              generateBtn.disabled = false;
              if (cancelJobBtn) cancelJobBtn.hidden = true;
              showToast('Job finished! Clips are in Library.', 'success');
              console.log('[LIB] auto_refresh=1 reason=render_complete job=' + (jobId || ''));
              var used = getClipsUsedTotal();
              setClipsUsedTotal(used + (j.clips && j.clips.length ? j.clips.length : 1));
              loadLibrary(false, 0, true, undefined, true, 0);
              return;
            }
            if (j.state === 'error') {
              closeProgressES();
              setHealthPollInterval(5000);
              setProgressLog(j.log_lines || []);
              log('Error: ' + (j.error || 'Unknown'));
              if (progressErrorHelp) {
                progressErrorHelp.hidden = false;
                var titleEl = document.getElementById('progressErrorTitle');
                if (titleEl) titleEl.textContent = (j.stage === 'download' ? 'YouTube download failed.' : 'Job failed.');
              }
              if (copyErrorBtn) copyErrorBtn.hidden = false;
              if (j.clips && j.clips.length) {
                var srcLine = document.getElementById('resultsSourceLine');
                if (srcLine) srcLine.textContent = (j.source_video_id ? 'Source: ' + String(j.source_video_id).replace(/</g, '&lt;') : '');
                renderClips(j.clips, true);
                resultsSection.hidden = false;
                renderWinScores(j.win_scores);
                showTab('library');
                loadLibrary(false, 0, true, undefined, true);
              }
              generateBtn.disabled = false;
              if (cancelJobBtn) cancelJobBtn.hidden = true;
              showToast('Job failed. See Progress for details.', 'error');
              return;
            }
            if (j.state === 'canceled') {
              closeProgressES();
              setHealthPollInterval(5000);
              setProgress(0, 'canceled', 'Canceled.');
              log('Job canceled.');
              generateBtn.disabled = false;
              if (cancelJobBtn) cancelJobBtn.hidden = true;
              return;
            }
            setTimeout(poll, 600);
          })
          .catch(function (err) {
            setHealthPollInterval(5000);
            closeProgressES();
            log(err.message || 'Backend unreachable. Start the server (run webapp\\run_web.bat) and try again.');
            generateBtn.disabled = false;
            if (cancelJobBtn) cancelJobBtn.hidden = true;
          });
      }
      setTimeout(poll, 400);
    } catch (err) {
      setHealthPollInterval(5000);
      closeProgressES();
      log(err.message || 'Backend unreachable. Start the server (run webapp\\run_web.bat) and try again.');
      generateBtn.disabled = false;
      if (cancelJobBtn) cancelJobBtn.hidden = true;
    }
  });
  }

  if (cancelJobBtn && generateBtn) {
    cancelJobBtn.addEventListener('click', function () {
      if (!currentJobId) return;
      cancelJobBtn.disabled = true;
      fetch(API_BASE + '/api/jobs/' + currentJobId + '/cancel', { method: 'POST', cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(r.status === 400 ? 'Job not running' : 'Cancel failed')); })
        .then(function () {
          closeProgressES();
          currentJobId = null;
          setProgress(0, 'idle', 'Canceled.');
          log('Job canceled.');
          generateBtn.disabled = false;
          cancelJobBtn.hidden = true;
        })
        .catch(function (err) {
          closeProgressES();
          log(err.message || 'Cancel failed.');
          cancelJobBtn.disabled = false;
        })
        .finally(function () {
          cancelJobBtn.disabled = false;
        });
    });
  }

  if (copyErrorBtn && progressLog) {
    copyErrorBtn.addEventListener('click', function () {
      var text = progressLog.textContent || '';
      if (!text.trim()) return;
      navigator.clipboard.writeText(text).then(function () { showToast('Error copied to clipboard.'); }).catch(function () { alert('Copy failed.'); });
    });
  }

  var generateCookiesLink = document.getElementById('generateCookiesLink');
  if (generateCookiesLink) {
    generateCookiesLink.addEventListener('click', function (e) {
      e.preventDefault();
      alert('Put a cookies.txt file in your Vireel project folder (same folder as clip.py).\n\nSee COOKIES_SETUP.txt in that folder for step-by-step instructions.');
    });
  }

  window.addEventListener('beforeunload', function () { closeProgressES(); });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) closeProgressES();
  });

  function renderWinScores(winScores) {
    if (!winScoresDetails || !winScoresTableWrap) return;
    if (!winScores || !winScores.length) {
      winScoresTableWrap.innerHTML = '<p class="win-scores-empty">No scoring debug data for this run.</p>';
      return;
    }
    let html = '<table class="win-scores-table"><thead><tr><th>Start</th><th>End</th><th>Total</th><th>Old</th><th>Hook</th><th>Payoff</th><th>Bad End</th><th>Topic</th></tr></thead><tbody>';
    winScores.forEach(function (row) {
      const t0 = row.t0 != null ? Number(row.t0).toFixed(1) : '—';
      const t1 = row.t1 != null ? Number(row.t1).toFixed(1) : '—';
      const total = row.total_score != null ? Number(row.total_score).toFixed(2) : '—';
      const old = row.old_score != null ? Number(row.old_score).toFixed(2) : '—';
      const hook = row.hook != null ? Number(row.hook).toFixed(2) : '—';
      const payoff = row.payoff != null ? Number(row.payoff).toFixed(2) : '—';
      const badEnd = row.bad_end != null ? Number(row.bad_end).toFixed(2) : '—';
      const topic = row.topic_penalty != null ? Number(row.topic_penalty).toFixed(2) : '—';
      html += '<tr><td>' + t0 + '</td><td>' + t1 + '</td><td>' + total + '</td><td>' + old + '</td><td>' + hook + '</td><td>' + payoff + '</td><td>' + badEnd + '</td><td>' + topic + '</td></tr>';
    });
    html += '</tbody></table>';
    winScoresTableWrap.innerHTML = html;
  }

  function renderClips(clips, isResults) {
    clipsGrid.innerHTML = '';
    if (isResults && currentJobId) {
      var allowed = clips.filter(function (c) {
        var pathOk = (c.url || '').indexOf('/jobs/' + currentJobId + '/') !== -1;
        var idOk = !c.job_id || c.job_id === currentJobId;
        return pathOk && idOk;
      });
      if (resultsMismatchBanner) {
        if (allowed.length < clips.length) {
          resultsMismatchBanner.textContent = 'Mismatch: clip does not belong to current job.';
          resultsMismatchBanner.hidden = false;
        } else {
          resultsMismatchBanner.hidden = true;
        }
      }
      clips = allowed;
    }
    var origin = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin.replace(/\/$/, '') : '';
    clips.forEach(function (c) {
      const card = document.createElement('div');
      card.className = 'clip-card';
      const clipUrl = (c.url || '/outputs/' + c.file);
      const absUrl = origin ? (origin + (clipUrl.indexOf('/') === 0 ? clipUrl : '/' + clipUrl)) : clipUrl;
      const title = (c.title || c.caption || 'Clip').replace(/</g, '&lt;');
      const hashtags = (c.hashtags || '').replace(/</g, '&lt;');
      const vid = (c.source_video_id || '').replace(/</g, '&lt;');
      let subsHtml = '';
      if (c.subs_status || c.subtitle_action != null) {
        var action = c.subtitle_action || (c.subs_status === 'burned' ? 'burn' : 'skip');
        var conf = c.baked_confidence != null ? Number(c.baked_confidence).toFixed(2) : (c.subs_score != null ? Number(c.subs_score).toFixed(2) : '');
        subsHtml = '<div class="subs-info">subtitle_action: ' + esc(action) +
          (conf !== '' ? ' · baked_confidence: ' + conf : '') +
          (c.subs_reason ? ' · ' + esc(c.subs_reason) : '') + '</div>';
      }
      const sourceBadge = (isResults && vid) ? ('<div class="source-badge">Source: ' + vid + '</div>') : '';
      card.innerHTML =
        '<video class="video-preview-9-16" src="' + absUrl + '" controls preload="metadata"></video>' +
        '<div class="info">' +
        (sourceBadge) +
        '<div class="title">' + title + '</div>' +
        (hashtags ? '<div class="hashtags">' + hashtags + '</div>' : '') +
        subsHtml +
        '<a class="download" href="' + absUrl + '" download="' + (c.file || 'clip.mp4') + '">Download</a>' +
        '</div>';
      clipsGrid.appendChild(card);
    });
  }

  function formatMtime(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    return d.toLocaleString();
  }

  function formatSize(bytes) {
    if (bytes == null) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  }

  function getActiveLibraryClip() {
    if (librarySelectedIndices.length === 0) return null;
    var idx = librarySelectedIndices[libraryActiveIndex];
    if (idx == null) idx = librarySelectedIndices[0];
    return libraryClipsList[idx] || null;
  }

  function slugForFilename(s) {
    if (!s || typeof s !== 'string') return '';
    return s.toLowerCase()
      .replace(/[^a-z0-9\s-]/g, '')
      .replace(/[-\s]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 56);
  }

  function suggestedNameForClip(c) {
    if (!c) return '';
    var titles = c.titles && Array.isArray(c.titles) ? c.titles : [];
    var first = (titles[0] || c.title || c.caption || '').trim();
    if (!first && c.file) first = c.file.replace(/\.mp4$/i, '');
    return slugForFilename(first) || (c.file ? c.file.replace(/\.mp4$/i, '') : '');
  }

  function updateLibraryPanel() {
    if (!librarySidePanel) return;
    libraryGrid.querySelectorAll('.library-card-wrap').forEach(function (wrap) {
      var idx = parseInt(wrap.getAttribute('data-clip-index'), 10);
      var isSelected = librarySelectedIndices.indexOf(idx) !== -1;
      wrap.classList.toggle('selected', isSelected);
      var cb = wrap.querySelector('.library-card-checkbox');
      if (cb) cb.checked = isSelected;
    });
    if (librarySelectedIndices.length === 0) {
      librarySidePanel.hidden = true;
      return;
    }
    librarySidePanel.hidden = false;
    if (libraryPanelCount) libraryPanelCount.textContent = librarySelectedIndices.length + ' selected';
    var active = getActiveLibraryClip();
    if (!active) return;
    if (libraryPanelActive) libraryPanelActive.textContent = active.file || '—';
    var titles = active.titles && Array.isArray(active.titles) ? active.titles : (active.title ? [active.title] : []);
    if (libraryPanelTitles) {
      libraryPanelTitles.innerHTML = titles.length ? titles.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') : '<li>—</li>';
    }
    if (libraryPanelCaption) libraryPanelCaption.textContent = active.caption || '—';
    if (libraryPanelHashtags) libraryPanelHashtags.textContent = active.hashtags || '—';
    if (libraryPanelFilename) libraryPanelFilename.value = suggestedNameForClip(active);
    updateBulkBar();
  }

  function buildLibraryViewIndices() {
    var list = libraryClipsList;
    var sortBy = (librarySortSelect && librarySortSelect.value) || 'newest';
    var searchQuery = (librarySearchInput && librarySearchInput.value) ? librarySearchInput.value.trim().toLowerCase() : '';
    var indices = list.map(function (_, i) { return i; });
    if (sortBy === 'score') {
      indices.sort(function (a, b) {
        var sa = list[a].score; var sb = list[b].score;
        if (sa == null && sb == null) return 0;
        if (sa == null) return 1; if (sb == null) return -1;
        return sb - sa;
      });
    } else {
      indices.sort(function (a, b) { return (list[b].mtime || 0) - (list[a].mtime || 0); });
    }
    if (searchQuery) {
      indices = indices.filter(function (i) {
        var c = list[i];
        var file = (c.file || '').toLowerCase();
        var title = (c.suggested_title || '').toLowerCase();
        var src = (c.source_video_id || '').toLowerCase();
        var cap = (c.caption || '').toLowerCase();
        return file.indexOf(searchQuery) !== -1 || title.indexOf(searchQuery) !== -1 || src.indexOf(searchQuery) !== -1 || cap.indexOf(searchQuery) !== -1;
      });
    }
    return indices;
  }

  /** BULK_ACTION_BAR: show when 1+ selected; wire Download/Post/Delete/Clear */
  function refreshTiktokStatus() {
    if (!libraryTiktokStatus) return;
    fetch(API_BASE + '/api/tiktok/status', { cache: 'no-store' })
      .then(function (res) { return res.json().catch(function () { return {}; }); })
      .then(function (data) {
        if (data.connected && data.account && data.account.display_name) {
          libraryTiktokStatus.textContent = 'TikTok: ' + data.account.display_name;
        } else if (data.connected) {
          libraryTiktokStatus.textContent = 'TikTok: Connected';
        } else {
          libraryTiktokStatus.textContent = 'TikTok: Not connected';
        }
      })
      .catch(function () { libraryTiktokStatus.textContent = 'TikTok: —'; });
  }

  /** TIKTOK_SETUP_HELPER: fetch /api/tiktok/setup and status, fill setup panel. */
  function refreshTiktokSetupPanel() {
    if (!libraryTiktokSetupPanel) return;
    var statusEl = libraryTiktokSetupStatus;
    var baseUrlEl = libraryTiktokSetupBaseUrl;
    var prefixEl = libraryTiktokSetupPortalPrefix;
    var redirectEl = libraryTiktokSetupRedirectUri;
    if (libraryTiktokSetupOpenJson) libraryTiktokSetupOpenJson.href = API_BASE + '/api/tiktok/setup';
    fetch(API_BASE + '/api/tiktok/setup', { cache: 'no-store' })
      .then(function (res) { return res.json().catch(function () { return {}; }); })
      .then(function (setup) {
        if (baseUrlEl) baseUrlEl.textContent = setup.app_base_url || '—';
        if (prefixEl) prefixEl.textContent = setup.portal_url_prefix || '—';
        if (redirectEl) redirectEl.textContent = setup.portal_redirect_uri || '—';
        return fetch(API_BASE + '/api/tiktok/status', { cache: 'no-store' })
          .then(function (r) { return r.json().catch(function () { return {}; }); });
      })
      .then(function (statusData) {
        if (statusEl) {
          if (statusData.connected && statusData.account && statusData.account.display_name) {
            statusEl.textContent = 'Connected as ' + statusData.account.display_name;
          } else if (statusData.connected) {
            statusEl.textContent = 'Connected';
          } else {
            statusEl.textContent = 'Not connected';
          }
        }
      })
      .catch(function () {
        if (statusEl) statusEl.textContent = '—';
        if (baseUrlEl) baseUrlEl.textContent = '—';
        if (prefixEl) prefixEl.textContent = '—';
        if (redirectEl) redirectEl.textContent = '—';
      });
  }

  function updateBulkBar() {
    if (!libraryBulkBar) return;
    var n = librarySelectedIndices.length;
    if (n === 0) {
      libraryBulkBar.hidden = true;
      return;
    }
    libraryBulkBar.hidden = false;
    if (libraryBulkCount) libraryBulkCount.textContent = n + ' selected';
  }

  function renderLibraryGrid() {
    if (!libraryGrid) return;
    var list = libraryClipsList;
    libraryViewIndices = buildLibraryViewIndices();
    libraryGrid.innerHTML = '';
    for (var j = 0; j < libraryViewIndices.length; j++) {
      var idx = libraryViewIndices[j];
      var c = list[idx];
      var wrap = document.createElement('div');
      wrap.className = 'library-card-wrap' + (librarySelectionMode ? ' library-selection-mode' : '');
      if (librarySelectedIndices.indexOf(idx) !== -1) wrap.classList.add('selected');
      wrap.setAttribute('data-clip-index', String(idx));
      var card = document.createElement('div');
      card.className = 'clip-card library-card';
      var url = c.url || '/outputs/' + c.file;
      var caption = (c.caption || '').replace(/</g, '&lt;');
      var captionStyle = (c.caption_style || 'hook_only').replace(/</g, '&lt;');
      var hashtags = (c.hashtags || '').replace(/</g, '&lt;');
      var srcVid = (c.source_video_id || '').replace(/</g, '&lt;');
      var sourceLabel = srcVid ? ('<div class="source-badge library-source">Source: ' + srcVid + '</div>') : '';
      var scoreVal = c.score != null ? Number(c.score) : null;
      var scoreBadge = scoreVal != null
        ? '<span class="clip-score-badge score-' + (scoreVal >= 70 ? 'high' : scoreVal >= 40 ? 'mid' : 'low') + '">' + esc(String(scoreVal)) + '</span>'
        : '<span class="clip-score-badge">—</span>';
      var titleOrFile = (c.suggested_title || c.file || '').replace(/</g, '&lt;');
      card.innerHTML =
        '<input type="checkbox" class="library-card-checkbox" aria-label="Select clip" ' + (librarySelectedIndices.indexOf(idx) !== -1 ? 'checked' : '') + '>' +
        '<video class="video-preview-9-16" src="' + url + '" controls preload="metadata"></video>' +
        '<div class="info">' +
        '<div class="filename">' + titleOrFile + ' ' + scoreBadge + '</div>' +
        (sourceLabel) +
        '<div class="meta">' + formatMtime(c.mtime) + (c.size != null ? ' · ' + formatSize(c.size) : '') + '</div>' +
        '<div class="tiktok-prep">' +
        '<div class="tiktok-prep-title">TikTok Prep</div>' +
        '<div class="tiktok-caption">' + (caption || '—') + '</div>' +
        '<div class="tiktok-style">Style: ' + captionStyle + '</div>' +
        '<button type="button" class="copy-caption-btn">Copy caption</button> ' +
        '<button type="button" class="copy-hashtags-btn">Copy hashtags</button>' +
        '</div>' +
        '<a class="download" href="' + url + '" download="' + (c.file || 'clip.mp4') + '">Download</a> ' +
        '<button type="button" class="delete-btn">Delete</button>' +
        '</div>';
      wrap.appendChild(card);
      var cb = card.querySelector('.library-card-checkbox');
      if (cb) {
        cb.addEventListener('click', function (ev) { ev.stopPropagation(); });
        cb.addEventListener('change', function (i) { return function () {
          var pos = librarySelectedIndices.indexOf(i);
          if (pos === -1) librarySelectedIndices.push(i); else librarySelectedIndices.splice(pos, 1);
          libraryActiveIndex = 0;
          updateLibraryPanel();
        }; }(idx));
      }
      wrap.addEventListener('click', function (i, checkEl) { return function (e) {
        if (e.target.closest('button') || e.target.closest('a') || e.target.closest('video') || e.target.closest('input[type="checkbox"]')) return;
        if (librarySelectionMode) {
          var pos = librarySelectedIndices.indexOf(i);
          if (pos === -1) librarySelectedIndices.push(i); else librarySelectedIndices.splice(pos, 1);
          wrap.classList.toggle('selected', librarySelectedIndices.indexOf(i) !== -1);
          if (checkEl) checkEl.checked = librarySelectedIndices.indexOf(i) !== -1;
        } else {
          librarySelectedIndices = [i];
          libraryActiveIndex = 0;
        }
        updateLibraryPanel();
      }; }(idx, cb));
      card.querySelector('.copy-caption-btn').addEventListener('click', function (cc) { return function () {
        navigator.clipboard.writeText(cc.caption || '').then(function () { alert('Caption copied.'); }).catch(function () { alert('Copy failed.'); });
      }; }(c));
      card.querySelector('.copy-hashtags-btn').addEventListener('click', function (cc) { return function () {
        navigator.clipboard.writeText(cc.hashtags || '').then(function () { alert('Hashtags copied.'); }).catch(function () { alert('Copy failed.'); });
      }; }(c));
      var deleteBtn = card.querySelector('.delete-btn');
      deleteBtn.addEventListener('click', function (cc) { return function () {
        if (!window.confirm('Delete ' + (cc.file || 'this clip') + '?')) return;
        var jobId = cc.job_id || ''; var file = cc.file || '';
        if (!jobId || !file) { alert('Cannot delete: missing job or file.'); return; }
        document.querySelectorAll('video').forEach(function (v) { v.pause(); v.removeAttribute('src'); v.load(); });
        fetch(API_BASE + '/api/jobs/' + encodeURIComponent(jobId) + '/clips/' + encodeURIComponent(file), { method: 'DELETE' })
          .then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (data) {
              if (res.ok && data.ok) {
                loadLibrary(false, 0, true, undefined, true);
                var skipped = data.skipped_locked || (data.skipped_in_use && data.skipped_in_use.length) || 0;
                if (skipped) showToast(skipped + ' file(s) could not be deleted (in use). Close preview and try again.', 'error');
                else if (data.deleted && data.deleted.length) showToast('Clip deleted.', 'success');
              } else alert(data.detail || data.error || ('Delete failed' + (res.ok ? '' : ' (HTTP ' + res.status + ')')));
            });
          })
          .catch(function () { alert('Backend unreachable or delete failed. Check server window.'); });
      }; }(c));
      libraryGrid.appendChild(wrap);
    }
    updateLibraryPanel();
  }

  function loadLibrary(isRetry, retryCount, selectNewest, expectFilename, doScan, jobCompleteAttempt) {
    librarySelectedIndices = [];
    libraryActiveIndex = 0;
    if (loadLibrary._retryTimer) {
      clearTimeout(loadLibrary._retryTimer);
      loadLibrary._retryTimer = null;
    }
    var retryNum = (retryCount != null && typeof retryCount === 'number') ? retryCount : 0;
    var maxRetries = (expectFilename != null && expectFilename !== '') ? 14 : 5;
    var retryDelayMs = (expectFilename != null && expectFilename !== '') ? 500 : 300;
    var useScan = doScan !== false;
    if (libraryEmpty) {
      libraryEmpty.textContent = useScan ? 'Scanning…' : 'Loading…';
      libraryEmpty.hidden = false;
      var oldPath = libraryEmpty.querySelector('.library-scan-path');
      if (oldPath) oldPath.remove();
    }
    if (libraryGrid) libraryGrid.innerHTML = '';
    var url = API_BASE + '/api/clips?' + (useScan ? 'scan=1&' : '') + '_=' + Date.now();
    console.log('[UI] GET ' + (useScan ? '/api/clips?scan=1' : '/api/clips') + (isRetry ? ' (retry ' + retryNum + '/' + maxRetries + ')' : ''));
    fetch(url, { cache: 'no-store' })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (body) {
          if (!r.ok) {
            throw new Error(body && (body.detail || body.error) || ('HTTP ' + r.status));
          }
          return body;
        });
      })
      .then(function (data) {
        var list = Array.isArray(data) ? data : (data && data.clips) || [];
        var jobsMeta = (data && data.jobs_meta) || {};
        var sortVal = (librarySortSelect && librarySortSelect.value) || 'newest';
        if (jobCompleteAttempt !== undefined && jobCompleteAttempt !== null) {
          console.log('[LIB] attempt=' + (jobCompleteAttempt + 1) + ' count=' + list.length);
        }
        console.log('[LIBRARY_UI] items=' + list.length + ' Sort=' + sortVal);
        if (retryNum > 0) console.log('[LIBRARY_UI] retry_attempt=' + retryNum);
        if (libraryDebugLine) {
          if (data.scan_dir_jobs || data.scan_dir_renders) {
            var dirs = (data.scan_dir_jobs || '') + (data.scan_dir_renders ? ' + ' + (data.scan_dir_renders || '') : '');
            libraryDebugLine.textContent = list.length ? 'Found ' + list.length + ' in ' + dirs : 'Scanning: ' + dirs;
            libraryDebugLine.hidden = false;
          } else {
            libraryDebugLine.hidden = true;
          }
        }
        libraryGrid.innerHTML = '';
        if (list.length === 0) {
          libraryGrid.innerHTML = '';
          libraryEmpty.hidden = false;
          var jobIds = Object.keys(jobsMeta || {});
          var scanPath = (data.scan_dir_jobs || data.scan_dir_renders) ? ((data.scan_dir_jobs || '') + (data.scan_dir_renders ? ' and ' + (data.scan_dir_renders || '') : '')) : '';
          if (jobCompleteAttempt !== undefined && jobCompleteAttempt !== null && jobCompleteAttempt < 2) {
            libraryEmpty.textContent = 'Checking again… (clips may still be saving) ' + (jobCompleteAttempt > 0 ? '(attempt ' + (jobCompleteAttempt + 1) + '/3)' : '');
            var nextAttempt = jobCompleteAttempt + 1;
            var delayMs = jobCompleteAttempt === 0 ? 750 : 1500;
            loadLibrary._retryTimer = setTimeout(function () {
              loadLibrary._retryTimer = null;
              loadLibrary(false, 0, true, undefined, true, nextAttempt);
            }, delayMs);
          } else if ((jobIds.length > 0 || expectFilename) && retryNum < maxRetries) {
            libraryEmpty.textContent = 'Checking again… (clips may still be saving) ' + (retryNum > 0 ? '(' + retryNum + '/' + maxRetries + ')' : '');
            if (expectFilename && retryNum > 0) console.log('[LIBRARY_UI] retry_attempt=' + retryNum);
            loadLibrary._retryTimer = setTimeout(function () {
              loadLibrary._retryTimer = null;
              loadLibrary(true, retryNum + 1, true, expectFilename, true);
            }, retryDelayMs);
          } else if (jobIds.length > 0) {
            libraryEmpty.textContent = 'No clips in those jobs. Click Refresh to scan again.';
          } else if (!expectFilename && retryNum < 1) {
            libraryEmpty.textContent = 'Scanning again in a moment… (clips may still be saving)';
            loadLibrary._retryTimer = setTimeout(function () {
              loadLibrary._retryTimer = null;
              loadLibrary(true, retryNum + 1, true, undefined, true);
            }, 2000);
          } else {
            libraryEmpty.textContent = 'No clips yet. Run a Generate job, then open Library—or click Refresh to scan.';
          }
          if (scanPath && libraryEmpty) {
            var extra = document.createElement('span');
            extra.className = 'library-scan-path';
            extra.style.cssText = 'display:block;margin-top:8px;font-size:12px;color:var(--muted, #888);word-break:break-all;';
            var sample = data.renders_dir_sample && data.renders_dir_sample.length ? data.renders_dir_sample.join(', ') : '';
            var jobDirs = data.job_dirs_sample && data.job_dirs_sample.length ? data.job_dirs_sample : [];
            var pathLine = 'Looking in: ' + scanPath + (sample ? '. Renders: ' + sample : '.');
            if (jobDirs.length) pathLine += ' Job folders: ' + jobDirs.slice(0, 5).join(', ') + (jobDirs.length > 5 ? '…' : '') + ' (no .mp4 in clips/).';
            else if (!sample) pathLine += ' No job folders or MP4s in renders.';
            extra.textContent = pathLine;
            if (!libraryEmpty.querySelector('.library-scan-path')) libraryEmpty.appendChild(extra);
          }
          if (librarySidePanel) librarySidePanel.hidden = true;
          libraryClipsList = [];
          librarySelectedIndices = [];
          return;
        }
        libraryClipsList = list;
        renderLibraryGrid();
        libraryEmpty.hidden = true;
        var foundExpected = expectFilename && list.some(function (c) { return (c.file || '') === expectFilename; });
        if (expectFilename && !foundExpected && retryNum < maxRetries) {
          console.log('[LIBRARY_UI] retry_attempt=' + (retryNum + 1) + ' (file not in list yet)');
          loadLibrary._retryTimer = setTimeout(function () {
            loadLibrary._retryTimer = null;
            loadLibrary(true, retryNum + 1, true, expectFilename, true);
          }, retryDelayMs);
        }
        if (selectNewest && list.length > 0 && libraryViewIndices.length > 0) {
          if (expectFilename && foundExpected) {
            var idx = list.findIndex(function (c) { return (c.file || '') === expectFilename; });
            if (idx !== -1 && libraryViewIndices.indexOf(idx) !== -1) {
              librarySelectedIndices = [idx];
              libraryActiveIndex = 0;
            } else {
              librarySelectedIndices = [libraryViewIndices[0]];
              libraryActiveIndex = 0;
            }
          } else {
            librarySelectedIndices = [libraryViewIndices[0]];
            libraryActiveIndex = 0;
          }
        }
        updateLibraryPanel();
        refreshTiktokStatus();
      })
      .catch(function (err) {
        var msg = err && err.message || 'Backend unreachable or failed to load clips. Check server window.';
        if (libraryEmpty) {
          libraryEmpty.innerHTML = msg + ' <button type="button" class="btn btn-secondary library-try-again-btn" style="margin-top:0.5rem;">Try again</button>';
          libraryEmpty.hidden = false;
          var tryAgainBtn = libraryEmpty.querySelector('.library-try-again-btn');
          if (tryAgainBtn) {
            tryAgainBtn.onclick = function () { loadLibrary(false, 0, true, undefined, true); };
          }
        }
      });
  }

  if (libraryRefreshBtn) libraryRefreshBtn.addEventListener('click', function () { loadLibrary(false, 0, true, undefined, true); });
  if (libraryTiktokConnect) {
    libraryTiktokConnect.addEventListener('click', function () {
      window.location.href = API_BASE + '/api/tiktok/connect';
    });
  }
  if (libraryTiktokSetupToggle && libraryTiktokSetupPanel) {
    libraryTiktokSetupToggle.addEventListener('click', function () {
      var visible = !libraryTiktokSetupPanel.hidden;
      libraryTiktokSetupPanel.hidden = visible;
      if (!libraryTiktokSetupPanel.hidden) refreshTiktokSetupPanel();
    });
  }
  if (libraryTiktokSetupCopyPrefix && libraryTiktokSetupPortalPrefix) {
    libraryTiktokSetupCopyPrefix.addEventListener('click', function () {
      var t = libraryTiktokSetupPortalPrefix.textContent;
      if (t && t !== '—') navigator.clipboard.writeText(t).then(function () { showToast('URL Prefix copied.', 'success'); }).catch(function () {});
    });
  }
  if (libraryTiktokSetupCopyRedirect && libraryTiktokSetupRedirectUri) {
    libraryTiktokSetupCopyRedirect.addEventListener('click', function () {
      var t = libraryTiktokSetupRedirectUri.textContent;
      if (t && t !== '—') navigator.clipboard.writeText(t).then(function () { showToast('Redirect URI copied.', 'success'); }).catch(function () {});
    });
  }
  if (librarySortSelect) librarySortSelect.addEventListener('change', function () { renderLibraryGrid(); });
  if (librarySearchInput) librarySearchInput.addEventListener('input', function () { renderLibraryGrid(); });
  if (librarySelectModeBtn) {
    librarySelectModeBtn.addEventListener('click', function () {
      librarySelectionMode = !librarySelectionMode;
      librarySelectModeBtn.textContent = librarySelectionMode ? 'Cancel Select' : 'Select';
      var pl = document.getElementById('panelLibrary');
      if (pl) pl.classList.toggle('library-selection-mode', librarySelectionMode);
      renderLibraryGrid();
    });
  }
  if (libraryBulkClear) {
    libraryBulkClear.addEventListener('click', function () {
      librarySelectedIndices = [];
      libraryActiveIndex = 0;
      updateLibraryPanel();
    });
  }
  if (libraryBulkDownload) {
    libraryBulkDownload.addEventListener('click', function () {
      if (librarySelectedIndices.length === 0) { showToast('Select one or more clips first.', 'error'); return; }
      var filenames = librarySelectedIndices.map(function (i) { return libraryClipsList[i].file || ''; }).filter(Boolean);
      if (filenames.length === 0) { showToast('No clip filenames to pack.', 'error'); return; }
      libraryBulkDownload.disabled = true;
      fetch(API_BASE + '/api/post-pack', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filenames: filenames }), cache: 'no-store' })
        .then(function (res) {
          if (!res.ok) return res.json().then(function (d) { throw new Error(d.error || d.detail || 'Download failed'); });
          return res.blob();
        })
        .then(function (blob) {
          var url = URL.createObjectURL(blob);
          var a = document.createElement('a');
          a.href = url;
          a.download = 'post_pack.zip';
          a.click();
          URL.revokeObjectURL(url);
          showToast('Download started.', 'success');
        })
        .catch(function (err) { showToast(err.message || 'Download failed.', 'error'); })
        .finally(function () { libraryBulkDownload.disabled = false; });
    });
  }
  if (libraryBulkPost) {
    libraryBulkPost.addEventListener('click', function () {
      if (librarySelectedIndices.length === 0) { showToast('Select one or more clips first.', 'error'); return; }
      /* PHASE 1 = first selected clip only. FUTURE_BULK_AUTPOST_QUEUE = phase 2 full bulk. */
      var firstIdx = librarySelectedIndices[0];
      var clip = libraryClipsList[firstIdx];
      var filename = clip && clip.file ? clip.file : '';
      if (!filename) { showToast('No clip file to post.', 'error'); return; }
      var caption = (clip.caption || '').trim() || (clip.titles && clip.titles[0]) || '';
      libraryBulkPost.disabled = true;
      var statusEl = libraryTiktokStatus || document.getElementById('libraryTiktokStatus');
      if (statusEl) statusEl.textContent = 'Posting to TikTok...';
      fetch(API_BASE + '/api/tiktok/post_clip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clip_path: filename, title: caption || undefined, caption: caption || undefined, privacy_level: 'PUBLIC_TO_EVERYONE' }),
        cache: 'no-store'
      })
        .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
        .then(function (result) {
          if (result.ok && result.data && result.data.success) {
            showToast('Posted successfully', 'success');
            if (statusEl) statusEl.textContent = 'Posted successfully';
          } else {
            var errMsg = (result.data && (result.data.detail || result.data.error)) || 'Post failed';
            showToast(errMsg, 'error');
            if (statusEl) statusEl.textContent = errMsg;
          }
        })
        .catch(function (err) {
          var errMsg = err.message || 'Post failed';
          showToast(errMsg, 'error');
          if (statusEl) statusEl.textContent = errMsg;
        })
        .finally(function () {
          libraryBulkPost.disabled = false;
          if (statusEl) setTimeout(function () { refreshTiktokStatus(); }, 3000);
        });
    });
  }
  if (libraryBulkDelete) {
    libraryBulkDelete.addEventListener('click', function () {
      if (librarySelectedIndices.length === 0) return;
      if (!window.confirm('Delete ' + librarySelectedIndices.length + ' selected clip(s)?')) return;
      var toDelete = librarySelectedIndices.slice();
      document.querySelectorAll('video').forEach(function (v) { v.pause(); v.removeAttribute('src'); v.load(); });
      libraryBulkDelete.disabled = true;
      var done = 0;
      var failed = 0;
      function tryNext() {
        if (toDelete.length === 0) {
          libraryBulkDelete.disabled = false;
          loadLibrary(false, 0, true, undefined, true);
          if (failed) showToast(done + ' deleted. ' + failed + ' failed.', 'error');
          else if (done) showToast(done + ' clip(s) deleted.', 'success');
          return;
        }
        var idx = toDelete.shift();
        var c = libraryClipsList[idx];
        var jobId = c.job_id || '';
        var file = c.file || '';
        if (!jobId || !file) { failed++; tryNext(); return; }
        fetch(API_BASE + '/api/jobs/' + encodeURIComponent(jobId) + '/clips/' + encodeURIComponent(file), { method: 'DELETE' })
          .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) {
            if (res.ok && data.ok && data.deleted && data.deleted.length) done++; else failed++;
            tryNext();
          }); })
          .catch(function () { failed++; tryNext(); });
      }
      tryNext();
    });
  }

  if (libraryPanelSelectTopN) {
    libraryPanelSelectTopN.addEventListener('click', function () {
      var n = parseInt(libraryPanelTopN && libraryPanelTopN.value ? libraryPanelTopN.value : '10', 10);
      if (isNaN(n) || n < 1) n = 10;
      var list = libraryClipsList;
      var indices = list.map(function (_, i) { return i; });
      indices.sort(function (a, b) {
        var sa = list[a].score; var sb = list[b].score;
        if (sa == null && sb == null) return 0;
        if (sa == null) return 1; if (sb == null) return -1;
        return sb - sa;
      });
      librarySelectedIndices = indices.slice(0, n);
      libraryActiveIndex = 0;
      updateLibraryPanel();
    });
  }
  if (libraryPanelDownloadTopN) {
    libraryPanelDownloadTopN.addEventListener('click', function () {
      var n = parseInt(libraryPanelTopN && libraryPanelTopN.value ? libraryPanelTopN.value : '10', 10);
      if (isNaN(n) || n < 1) n = 10;
      var list = libraryClipsList;
      var indices = list.map(function (_, i) { return i; });
      indices.sort(function (a, b) {
        var sa = list[a].score; var sb = list[b].score;
        if (sa == null && sb == null) return 0;
        if (sa == null) return 1; if (sb == null) return -1;
        return sb - sa;
      });
      var topIndices = indices.slice(0, n);
      var filenames = topIndices.map(function (i) { return list[i].file || ''; }).filter(Boolean);
      if (filenames.length === 0) {
        alert('No clips to pack.');
        return;
      }
      libraryPanelDownloadTopN.disabled = true;
      fetch(API_BASE + '/api/post-pack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filenames: filenames }),
        cache: 'no-store'
      })
        .then(function (res) {
          if (!res.ok) return res.json().then(function (d) { throw new Error(d.error || d.detail || 'Download failed'); });
          return res.blob();
        })
        .then(function (blob) {
          var url = URL.createObjectURL(blob);
          var a = document.createElement('a');
          a.href = url;
          a.download = 'post_pack_top' + n + '.zip';
          a.click();
          URL.revokeObjectURL(url);
        })
        .catch(function (err) {
          alert(err.message || 'Download failed.');
        })
        .finally(function () {
          libraryPanelDownloadTopN.disabled = false;
        });
    });
  }

  if (libraryPanelCopyCaption) {
    libraryPanelCopyCaption.addEventListener('click', function () {
      var c = getActiveLibraryClip();
      if (!c) return;
      navigator.clipboard.writeText(c.caption || '').then(function () { alert('Caption copied.'); }).catch(function () { alert('Copy failed.'); });
    });
  }
  if (libraryPanelCopyHashtags) {
    libraryPanelCopyHashtags.addEventListener('click', function () {
      var c = getActiveLibraryClip();
      if (!c) return;
      navigator.clipboard.writeText(c.hashtags || '').then(function () { alert('Hashtags copied.'); }).catch(function () { alert('Copy failed.'); });
    });
  }
  if (libraryPanelCopyAll) {
    libraryPanelCopyAll.addEventListener('click', function () {
      var c = getActiveLibraryClip();
      if (!c) return;
      var text = (c.caption || '') + '\n\n' + (c.hashtags || '');
      navigator.clipboard.writeText(text).then(function () { alert('Caption + hashtags copied.'); }).catch(function () { alert('Copy failed.'); });
    });
  }
  if (libraryPanelCopyPath) {
    libraryPanelCopyPath.addEventListener('click', function () {
      var c = getActiveLibraryClip();
      if (!c) return;
      var path = (c.url || '/outputs/' + (c.file || '')).replace(/^\//, '');
      var full = API_BASE.replace(/\/$/, '') + '/' + path;
      navigator.clipboard.writeText(full).then(function () { alert('File path copied.'); }).catch(function () { alert('Copy failed.'); });
    });
  }
  if (libraryPanelRename) {
    libraryPanelRename.addEventListener('click', function () {
      var active = getActiveLibraryClip();
      if (!active) {
        alert('Select a clip first.');
        return;
      }
      var raw = (libraryPanelFilename && libraryPanelFilename.value) ? libraryPanelFilename.value.trim() : '';
      if (!raw) raw = suggestedNameForClip(active);
      if (!raw) {
        alert('Enter a filename.');
        return;
      }
      if (!raw.toLowerCase().endsWith('.mp4')) raw = raw + '.mp4';
      libraryPanelRename.disabled = true;
      fetch(API_BASE + '/api/rename-clip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old: active.file, new: raw }),
        cache: 'no-store'
      })
        .then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok) throw new Error(data.error || data.detail || 'Rename failed');
            return data;
          });
        })
        .then(function (data) {
          var idx = librarySelectedIndices[libraryActiveIndex];
          if (idx == null) idx = librarySelectedIndices[0];
          var newName = data.new;
          libraryClipsList[idx].file = newName;
          libraryClipsList[idx].url = '/outputs/jobs/' + (active.job_id || '') + '/clips/' + newName;
          var wrap = libraryGrid.querySelector('.library-card-wrap[data-clip-index="' + idx + '"]');
          if (wrap) {
            var card = wrap.querySelector('.clip-card');
            var fnDiv = card && card.querySelector('.filename');
            if (fnDiv) {
              var scoreVal = libraryClipsList[idx].score;
              var badge = scoreVal != null
                ? '<span class="clip-score-badge score-' + (scoreVal >= 70 ? 'high' : scoreVal >= 40 ? 'mid' : 'low') + '">' + esc(String(scoreVal)) + '</span>'
                : '<span class="clip-score-badge">—</span>';
              fnDiv.innerHTML = esc(newName) + ' ' + badge;
            }
            var vid = card && card.querySelector('video');
            if (vid) vid.src = libraryClipsList[idx].url;
            var dl = card && card.querySelector('a.download');
            if (dl) {
              dl.href = libraryClipsList[idx].url;
              dl.download = newName;
            }
          }
          updateLibraryPanel();
        })
        .catch(function (err) {
          alert(err.message || 'Rename failed.');
        })
        .finally(function () {
          libraryPanelRename.disabled = false;
        });
    });
  }
  if (libraryPanelDownloadPostPack) {
    libraryPanelDownloadPostPack.addEventListener('click', function () {
      if (librarySelectedIndices.length === 0) {
        alert('Select one or more clips first.');
        return;
      }
      var filenames = librarySelectedIndices.map(function (i) { return libraryClipsList[i].file || ''; }).filter(Boolean);
      if (filenames.length === 0) {
        alert('No clip filenames to pack.');
        return;
      }
      libraryPanelDownloadPostPack.disabled = true;
      fetch(API_BASE + '/api/post-pack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filenames: filenames }),
        cache: 'no-store'
      })
        .then(function (res) {
          if (!res.ok) return res.json().then(function (d) { throw new Error(d.error || d.detail || 'Download failed'); });
          return res.blob();
        })
        .then(function (blob) {
          var url = URL.createObjectURL(blob);
          var a = document.createElement('a');
          a.href = url;
          a.download = 'post_pack.zip';
          a.click();
          URL.revokeObjectURL(url);
        })
        .catch(function (err) {
          alert(err.message || 'Download failed.');
        })
        .finally(function () {
          libraryPanelDownloadPostPack.disabled = false;
        });
    });
  }
  if (libraryPanelPrev) {
    libraryPanelPrev.addEventListener('click', function () {
      if (librarySelectedIndices.length === 0) return;
      libraryActiveIndex = (libraryActiveIndex - 1 + librarySelectedIndices.length) % librarySelectedIndices.length;
      updateLibraryPanel();
    });
  }
  if (libraryPanelNext) {
    libraryPanelNext.addEventListener('click', function () {
      if (librarySelectedIndices.length === 0) return;
      libraryActiveIndex = (libraryActiveIndex + 1) % librarySelectedIndices.length;
      updateLibraryPanel();
    });
  }

  var libraryDeleteAllBtn = document.getElementById('libraryDeleteAllBtn');
  if (!libraryDeleteAllBtn) {
    console.error('[UI] libraryDeleteAllBtn not found - Delete All not wired');
  } else {
    libraryDeleteAllBtn.addEventListener('click', function () {
      if (!window.confirm('Delete all jobs and clips? This cannot be undone.')) return;
      document.querySelectorAll('video').forEach(function (v) { v.pause(); v.removeAttribute('src'); v.load(); });
      fetch(API_BASE + '/api/jobs/all', { method: 'DELETE', cache: 'no-store' })
        .then(function (r) {
          return r.json().catch(function () { return { ok: false, error: r.statusText || 'HTTP ' + r.status }; }).then(function (data) {
            if (r.ok && data.ok) {
              loadLibrary(false, 0, true, undefined, true);
              var jobs = data.deleted_jobs || 0;
              var clips = data.deleted_clips || 0;
              var renders = data.deleted_renders || 0;
              var parts = [];
              if (jobs > 0) parts.push(jobs + ' job(s)');
              if (clips > 0) parts.push(clips + ' clip(s)');
              if (renders > 0) parts.push(renders + ' render(s)');
              var skipped = data.skipped_locked || (data.skipped_in_use && data.skipped_in_use.length) || 0;
              if (skipped) showToast((parts.length ? 'Deleted ' + parts.join(', ') + '. ' : '') + skipped + ' file(s) could not be deleted (in use). Close previews and retry.', 'error');
              else if (parts.length) showToast('Deleted ' + parts.join(', ') + '.', 'success');
            } else showToast(data.detail || data.error || 'Delete all failed.', 'error');
          });
        })
        .catch(function () {
          showToast('Backend unreachable or delete failed.', 'error');
        });
    });
  }

  var panelLibrary = document.getElementById('panelLibrary');
  if (panelLibrary && panelLibrary.classList.contains('active')) {
    loadLibrary(false, 0, true, undefined, true);
  }
  if (panelGenerate && panelGenerate.classList.contains('active')) {
    startJobsListPoll();
  }

  var paywallModal = document.getElementById('paywallModal');
  if (paywallModal) {
    var closePaywall = function () { paywallModal.hidden = true; };
    paywallModal.querySelector('.paywall-modal-close').addEventListener('click', closePaywall);
    paywallModal.querySelector('.paywall-modal-backdrop').addEventListener('click', closePaywall);
  }
})();
