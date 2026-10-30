"""Browser DOM and UI interaction regression tests."""

import json
import os
import re
import subprocess
import tempfile
import unittest

from downloader.browser_solver import find_browser_path


class UIDomTests(unittest.TestCase):
    def setUp(self):
        # A separate profile prevents --dump-dom from being forwarded to an
        # already running personal browser, which produces no test output.
        profile = tempfile.TemporaryDirectory(
            prefix="remote_downloader_ui_", ignore_cleanup_errors=True
        )
        self.addCleanup(profile.cleanup)
        self.browser_profile = profile.name

    def test_app_js_syntax_and_ui_execution(self):
        """Verify static/js/app.js executes without syntax errors and responds to UI clicks."""
        browser = find_browser_path()
        if not browser:
            self.skipTest("No Chrome or Edge browser installed for headless DOM test")

        with open("templates/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        mock_header = """
<script>
window.__js_errors = [];
window.onerror = function(msg, url, lineNo, colNo, error) {
  window.__js_errors.push({msg: msg, line: lineNo, col: colNo});
  return false;
};
window.fetch = function(url) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: function() {
      if (url.includes('/api/status')) return Promise.resolve({jobs: [], active_count: 0, waiting_count: 0, completed_count: 0, failed_count: 0, upload_stats: {}, port: 7971});
      if (url.includes('/api/server-info') || url.includes('/api/info')) return Promise.resolve({app_name: 'RemoteDownloader', port: 7971, capture_source_page: false});
      if (url.includes('/api/kone/status')) return Promise.resolve({enabled: false, last_check_time: null, seen_posts_count: 0, recent_logs: []});
      if (url.includes('/api/settings')) return Promise.resolve({download_dir: 'C:/downloads', capture_source_page: false});
      return Promise.resolve({success: true});
    }
  });
};
</script>
"""
        html = html.replace("<head>", "<head>" + mock_header)
        html = html.replace(
            'href="/static/css/style.css"', 'href="../static/css/style.css"'
        )
        html = re.sub(
            r'<script\s+src="/static/js/app\.js[^"]*"',
            '<script src="../static/js/app.js"',
            html,
        )
        test_script = """
<script>
document.addEventListener('DOMContentLoaded', async function() {
  await new Promise(r => setTimeout(r, 60));
  var results = { errors: window.__js_errors };
  var settingsBtn = document.getElementById('settingsBtn');
  var modal = document.getElementById('settingsModal');
  if (settingsBtn) settingsBtn.click();
  await new Promise(r => setTimeout(r, 60));
  results.modal_open = modal && modal.classList.contains('show');
  var cancelBtn = document.getElementById('cancelSettingsBtn');
  if (cancelBtn) cancelBtn.click();
  results.modal_closed = modal && !modal.classList.contains('show');
  var div = document.createElement('div');
  div.id = 'regression-ui-result';
  div.textContent = JSON.stringify(results);
  document.body.appendChild(div);
});
</script>
"""
        html = html.replace("</body>", test_script + "</body>")
        test_html = os.path.abspath("templates/_test_dom.html")
        with open(test_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            res = subprocess.run(
                [
                    browser,
                    "--headless",
                    f"--user-data-dir={self.browser_profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--dump-dom",
                    "--virtual-time-budget=2000",
                    f"file:///{test_html.replace(os.sep, '/')}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            match = re.search(r'<div id="regression-ui-result">(.*?)</div>', res.stdout)
            self.assertIsNotNone(
                match,
                f"DOM test div was not rendered; exit={res.returncode}; stderr={res.stderr[-2000:]}",
            )
            assert match is not None
            data = json.loads(match.group(1))
            self.assertEqual(
                data.get("errors"),
                [],
                f"JavaScript errors encountered: {data.get('errors')}",
            )
            self.assertTrue(
                data.get("modal_open"),
                "Settings modal did not open on settingsBtn click",
            )
            self.assertTrue(
                data.get("modal_closed"),
                "Settings modal did not close on cancelSettingsBtn click",
            )
        finally:
            if os.path.exists(test_html):
                os.remove(test_html)

    def test_extension_popup_layout_and_interaction(self):
        """Verify extension popup layout order, openFolderBtn removal, and settings modal."""
        with open("extension/popup.html", "r", encoding="utf-8") as f:
            html = f.read()

        # 1. '폴더 열기' button is deleted
        self.assertNotIn("openFolderBtn", html, "openFolderBtn should be deleted")
        self.assertNotIn("폴더 열기", html, "Text '폴더 열기' should be deleted")

        # 2. Layout hierarchy check:
        # sendCurrentTabBtn appears before direct-add-section
        # direct-add-section appears before jobs-section
        pos_tab_btn = html.find('id="sendCurrentTabBtn"')
        pos_direct_add = html.find('class="section direct-add-section"')
        pos_jobs = html.find('class="section jobs-section"')

        self.assertNotEqual(pos_tab_btn, -1, "sendCurrentTabBtn not found")
        self.assertNotEqual(pos_direct_add, -1, "direct-add-section not found")
        self.assertNotEqual(pos_jobs, -1, "jobs-section not found")

        self.assertLess(
            pos_tab_btn,
            pos_direct_add,
            "sendCurrentTabBtn must come before direct-add-section",
        )
        self.assertLess(
            pos_direct_add,
            pos_jobs,
            "direct-add-section must come before jobs-section (대기열)",
        )

        # 3. Settings button in header right
        pos_header = html.find('class="popup-header"')
        pos_settings_btn = html.find('id="settingsBtn"')
        self.assertNotEqual(pos_settings_btn, -1, "settingsBtn not found")
        self.assertLess(
            pos_header,
            pos_settings_btn,
            "settingsBtn must be inside or after popup-header",
        )
        self.assertLess(
            pos_settings_btn,
            pos_tab_btn,
            "settingsBtn must be in top header before quick action",
        )

        # 4. Settings modal exists
        self.assertIn('id="settingsModal"', html, "settingsModal must exist")
        self.assertIn('id="closeSettingsModal"', html, "closeSettingsModal must exist")

        # 5. manualTeraboxMode removed from extension popup (uses server default)
        self.assertNotIn(
            "manualTeraboxMode",
            html,
            "manualTeraboxMode should be removed from extension popup",
        )
        self.assertNotIn(
            "테라박스:",
            html,
            "TeraBox mode dropdown should be removed from extension popup",
        )

        # 6. Headless browser check on popup.html
        browser = find_browser_path()
        if not browser:
            return

        mock_header = """
<script>
window.__js_errors = [];
window.onerror = function(msg, url, lineNo, colNo, error) {
  window.__js_errors.push({msg: msg, line: lineNo, col: colNo});
  return false;
};
// Mock chrome APIs
window.chrome = {
  storage: {
    local: {
      get: function(keys, cb) { cb && cb({}); return Promise.resolve({}); },
      set: function(items, cb) { cb && cb(); return Promise.resolve(); }
    }
  },
  tabs: {
    query: function(opts, cb) { cb && cb([{url: 'https://example.com/test', title: 'Test'}]); }
  }
};
window.fetch = function() {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: function() { return Promise.resolve({ jobs: [], download_dir: 'C:/downloads', success: true }); }
  });
};
</script>
"""
        test_html_content = html.replace("<head>", "<head>" + mock_header)
        test_script = """
<script>
document.addEventListener('DOMContentLoaded', async function() {
  await new Promise(r => setTimeout(r, 60));
  var results = { errors: window.__js_errors };
  var settingsBtn = document.getElementById('settingsBtn');
  var modal = document.getElementById('settingsModal');
  if (settingsBtn) settingsBtn.click();
  await new Promise(r => setTimeout(r, 60));
  results.modal_open = modal && modal.classList.contains('show');
  var closeBtn = document.getElementById('closeSettingsModal');
  if (closeBtn) closeBtn.click();
  results.modal_closed = modal && !modal.classList.contains('show');
  var div = document.createElement('div');
  div.id = 'extension-ui-result';
  div.textContent = JSON.stringify(results);
  document.body.appendChild(div);
});
</script>
"""
        test_html_content = test_html_content.replace(
            "</body>", test_script + "</body>"
        )
        test_path = os.path.abspath("extension/_test_popup_dom.html")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(test_html_content)
        try:
            res = subprocess.run(
                [
                    browser,
                    "--headless",
                    f"--user-data-dir={self.browser_profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--dump-dom",
                    "--virtual-time-budget=2000",
                    f"file:///{test_path.replace(os.sep, '/')}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            match = re.search(r'<div id="extension-ui-result">(.*?)</div>', res.stdout)
            self.assertIsNotNone(
                match,
                f"DOM test div was not rendered in popup; exit={res.returncode}; stderr={res.stderr[-2000:]}",
            )
            assert match is not None
            data = json.loads(match.group(1))
            self.assertEqual(
                data.get("errors"),
                [],
                f"JavaScript errors in popup: {data.get('errors')}",
            )
            self.assertTrue(
                data.get("modal_open"),
                "Settings modal did not open on settingsBtn click in popup",
            )
            self.assertTrue(
                data.get("modal_closed"),
                "Settings modal did not close on closeSettingsModal click in popup",
            )
        finally:
            if os.path.exists(test_path):
                os.remove(test_path)

    def test_pagination_dom_and_logic(self):
        """Verify 10-item pagination structure in index.html and popup.html."""
        with open("templates/index.html", "r", encoding="utf-8") as f:
            index_html = f.read()
        self.assertIn('id="jobsPaginationWrapper"', index_html)
        self.assertIn('id="pageSizeSelect"', index_html)
        self.assertIn('id="paginationControls"', index_html)
        self.assertIn('id="paginationInfo"', index_html)
        self.assertIn('value="10"', index_html)

        with open("extension/popup.html", "r", encoding="utf-8") as f:
            popup_html = f.read()
        self.assertIn('id="popupPagination"', popup_html)
        self.assertIn('id="popupPrevBtn"', popup_html)
        self.assertIn('id="popupNextBtn"', popup_html)
        self.assertIn('id="popupPageText"', popup_html)

        browser = find_browser_path()
        if not browser:
            return

        mock_header = """
<script>
window.__js_errors = [];
window.onerror = function(msg, url, lineNo, colNo, error) {
  window.__js_errors.push({msg: msg, line: lineNo, col: colNo});
  return false;
};
window.fetch = function(url) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: function() {
      if (url.includes('/api/status')) return Promise.resolve({jobs: [], active_count: 0, waiting_count: 0, completed_count: 0, failed_count: 0, upload_stats: {}, port: 7971});
      if (url.includes('/api/server-info') || url.includes('/api/info')) return Promise.resolve({app_name: 'RemoteDownloader', port: 7971, capture_source_page: false});
      if (url.includes('/api/kone/status')) return Promise.resolve({enabled: false, last_check_time: null, seen_posts_count: 0, recent_logs: []});
      if (url.includes('/api/settings')) return Promise.resolve({download_dir: 'C:/downloads', capture_source_page: false});
      return Promise.resolve({success: true});
    }
  });
};
</script>
"""
        html = index_html.replace("<head>", "<head>" + mock_header)
        html = html.replace(
            'href="/static/css/style.css"', 'href="../static/css/style.css"'
        )
        html = re.sub(
            r'<script\s+src="/static/js/app\.js[^"]*"',
            '<script src="../static/js/app.js"',
            html,
        )
        test_script = """
<script>
document.addEventListener('DOMContentLoaded', async function() {
  await new Promise(r => setTimeout(r, 60));
  var testResults = { errors: window.__js_errors };
  try {
    for (var i = 1; i <= 25; i++) {
      jobsMap.set('job-' + i, {
        id: 'job-' + i,
        status: 'completed',
        filename: 'file_' + i + '.zip',
        total_str: '100 MB',
        completed_at: '2026-10-29 12:00:' + (i < 10 ? '0' + i : i),
        progress: 100
      });
    }
    switchTab('completed');
    var renderedCards = document.querySelectorAll('#jobsList .job-item');
    testResults.page1_card_count = renderedCards.length;
    var info = document.getElementById('paginationInfo');
    testResults.info_text = info ? info.textContent : '';
    var nextBtn = document.querySelector('#paginationControls .btn-page-nav:last-child');
    testResults.has_next_btn = !!nextBtn;
    if (nextBtn) nextBtn.click();
    var page2Cards = document.querySelectorAll('#jobsList .job-item');
    testResults.page2_card_count = page2Cards.length;
  } catch (e) {
    testResults.caught = String(e);
  }
  var div = document.createElement('div');
  div.id = 'pagination-test-result';
  div.textContent = JSON.stringify(testResults);
  document.body.appendChild(div);
});
</script>
"""
        html = html.replace("</body>", test_script + "</body>")
        test_html = os.path.abspath("templates/_test_pagination_dom.html")
        with open(test_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            res = subprocess.run(
                [
                    browser,
                    "--headless",
                    f"--user-data-dir={self.browser_profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--dump-dom",
                    "--virtual-time-budget=2000",
                    f"file:///{test_html.replace(os.sep, '/')}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            match = re.search(
                r'<div id="pagination-test-result">(.*?)</div>', res.stdout
            )
            self.assertIsNotNone(
                match, f"Pagination test div was not rendered; exit={res.returncode}"
            )
            assert match is not None
            data = json.loads(match.group(1))
            self.assertEqual(data.get("errors"), [])
            self.assertIsNone(data.get("caught"))
            self.assertEqual(data.get("page1_card_count"), 10)
            self.assertIn("1 - 10", data.get("info_text", ""))
            self.assertTrue(data.get("has_next_btn"))
            self.assertEqual(data.get("page2_card_count"), 10)
        finally:
            if os.path.exists(test_html):
                os.remove(test_html)


if __name__ == "__main__":
    unittest.main()
