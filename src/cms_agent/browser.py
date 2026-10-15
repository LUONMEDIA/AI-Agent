"""Optional Playwright CMS adapter. Configured from a PRIVATE selector profile.

The adapter deliberately knows no real portal URL or credentials. It will
raise HOLD if page identity, selector mapping, search completeness, file
download, or status readback cannot be verified.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin, urlparse
import json
import os

from .engine import Hold, Item, RemoteRecord


class BrowserCMS:
    def __init__(self):
        # Imported only for a deliberate --live invocation.
        from playwright.sync_api import sync_playwright

        profile_path = os.getenv("CMS_AGENT_PROFILE")
        state_path = os.getenv("CMS_AGENT_AUTH_STATE")
        if not profile_path or not state_path:
            raise Hold("PRIVATE_PROFILE_AND_AUTH_STATE_REQUIRED")
        profile_file, auth_file = Path(profile_path), Path(state_path)
        if not profile_file.is_file() or not auth_file.is_file():
            raise Hold("PROFILE_OR_AUTH_STATE_MISSING")
        self.config = json.loads(profile_file.read_text(encoding="utf-8"))
        self.origin = self._url_config("origin").rstrip("/")
        self.module = self._text_config("module")
        self.selectors = self.config.get("selectors")
        if not isinstance(self.selectors, dict):
            raise Hold("SELECTORS_NOT_CONFIGURED")
        for key in ("module_marker", "search_input", "search_submit", "search_done_marker", "search_rows",
                    "row_number", "row_detail_link", "number_detail",
                    "module_detail", "fingerprint_detail", "status_detail",
                    "attachment_link", "form_number", "form_title",
                    "form_fingerprint", "form_pdf", "save_button",
                    "forward_button", "approve_button"):
            self._selector(key)
        self._pw = sync_playwright().start()
        self._browser = None
        try:
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(storage_state=str(auth_file),
                                                       accept_downloads=False)
            self.page = self._context.new_page()
        except Exception:
            if self._browser:
                self._browser.close()
            self._pw.stop()
            raise

    def _text_config(self, key: str) -> str:
        value = self.config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise Hold("MISSING_PROFILE_" + key.upper())
        return value.strip()

    def _url_config(self, key: str) -> str:
        value = self._text_config(key)
        u = urlparse(value)
        if u.scheme != "https" or not u.netloc or u.username or u.password:
            raise Hold("URL_MUST_BE_HTTPS")
        return value

    def _url(self, name: str) -> str:
        url = self._url_config(name)
        self._assert_origin(url)
        return url

    def _assert_origin(self, url: str) -> None:
        base = urlparse(self.origin)
        actual = urlparse(url)
        if (actual.scheme, actual.netloc) != (base.scheme, base.netloc):
            raise Hold("URL_OUTSIDE_APPROVED_ORIGIN")

    def _selector(self, key: str) -> str:
        value = self.selectors.get(key)
        if not isinstance(value, str) or not value.strip():
            raise Hold("MISSING_SELECTOR_" + key.upper())
        return value

    def _text(self, key: str) -> str:
        locator = self.page.locator(self._selector(key))
        if locator.count() != 1:
            raise Hold("AMBIGUOUS_SELECTOR_" + key.upper())
        try:
            return locator.input_value().strip()
        except Exception:
            return locator.inner_text().strip()

    def _assert_module(self) -> None:
        if self._text("module_marker") != self.module:
            raise Hold("WRONG_CMS_MODULE")

    def _goto(self, url: str) -> None:
        self._assert_origin(url)
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._assert_origin(self.page.url)
        self._assert_module()

    def _record_url(self, href: str) -> str:
        url = urljoin(self.page.url, href)
        self._assert_origin(url)
        return url

    def find(self, item: Item) -> list[RemoteRecord]:
        if item.target_module != self.module:
            raise Hold("BATCH_REQUIRES_CORRECT_MODULE_PROFILE")
        self._goto(self._url("search_url"))
        self.page.locator(self._selector("search_input")).fill(item.document_number)
        self.page.locator(self._selector("search_submit")).click()
        self.page.locator(self._selector("search_done_marker")).wait_for(state="visible", timeout=15000)
        self._assert_module()
        next_page = self.selectors.get("pagination_next")
        if next_page:
            next_locator = self.page.locator(next_page)
            if next_locator.count() and next_locator.is_visible() and next_locator.is_enabled():
                raise Hold("PAGINATED_RESULTS_NOT_EXHAUSTED")
        rows = self.page.locator(self._selector("search_rows"))
        if rows.count() > 100:
            raise Hold("SEARCH_RESULT_LIMIT_EXCEEDED")
        matches = []
        for index in range(rows.count()):
            row = rows.nth(index)
            number = row.locator(self._selector("row_number")).inner_text().strip()
            if number != item.document_number:
                continue
            link = row.locator(self._selector("row_detail_link"))
            if link.count() != 1:
                raise Hold("AMBIGUOUS_RECORD_LINK")
            href = link.get_attribute("href")
            if not href:
                raise Hold("MISSING_RECORD_LINK")
            matches.append(self._record_url(href))
        return [self.readback(url) for url in matches]

    def readback(self, record_id: str) -> RemoteRecord:
        self._goto(record_id)
        number = self._text("number_detail")
        module = self._text("module_detail")
        if module != self.module:
            raise Hold("REMOTE_MODULE_MISMATCH")
        fingerprint = self._text("fingerprint_detail")
        status_raw = self._text("status_detail")
        if status_raw in self.config.get("draft_states", []):
            status = "DRAFT"
        elif status_raw in self.config.get("published_states", []):
            status = "PUBLISHED"
        else:
            raise Hold("UNKNOWN_CMS_STATUS")
        link = self.page.locator(self._selector("attachment_link"))
        if link.count() != 1:
            raise Hold("ATTACHMENT_LINK_NOT_UNIQUE")
        href = link.get_attribute("href")
        if not href:
            raise Hold("ATTACHMENT_LINK_MISSING")
        file_url = self._record_url(href)
        response = self._context.request.get(file_url, timeout=30000)
        self._assert_origin(response.url)
        if not response.ok:
            raise Hold("ATTACHMENT_DOWNLOAD_FAILED")
        body = response.body()
        max_bytes = int(self.config.get("max_attachment_bytes", 25_000_000))
        if not body.startswith(b"%PDF-") or len(body) > max_bytes:
            raise Hold("ATTACHMENT_INVALID_OR_TOO_LARGE")
        return RemoteRecord(record_id, number, module, fingerprint,
                            sha256(body).hexdigest(), status)

    def create_draft(self, item: Item) -> str:
        if item.target_module != self.module:
            raise Hold("WRONG_TARGET_MODULE")
        self._goto(self._url("create_url"))
        self.page.locator(self._selector("form_number")).fill(item.document_number)
        self.page.locator(self._selector("form_title")).fill(item.title)
        self.page.locator(self._selector("form_fingerprint")).fill(item.fingerprint)
        selectors_for_fields = self.config.get("metadata_fields", {})
        if not isinstance(selectors_for_fields, dict):
            raise Hold("METADATA_SELECTORS_INVALID")
        for key, value in item.fields.items():
            locator = selectors_for_fields.get(key)
            if not locator:
                raise Hold("METADATA_FIELD_UNMAPPED")
            self.page.locator(locator).fill(value)
        self.page.locator(self._selector("form_pdf")).set_input_files(str(item.source_path))
        self.page.locator(self._selector("save_button")).click()
        self.page.wait_for_load_state("domcontentloaded")
        if self.page.url == self._url("create_url"):
            raise Hold("SAVE_DID_NOT_NAVIGATE_TO_RECORD")
        self._assert_origin(self.page.url)
        return self.page.url

    def publish_step1(self, record_id: str) -> None:
        existing = self.readback(record_id)
        if existing.status != "DRAFT":
            raise Hold("RECORD_NOT_DRAFT_BEFORE_APPROVAL")
        # Workflow-specific approval transitions must be mapped and verified
        # with a real test system before profile is permitted in production.
        if not isinstance(self.config.get("forwarded_states"), list) or not self.config["forwarded_states"]:
            raise Hold("FORWARDED_STATES_NOT_CONFIGURED")
        self.page.locator(self._selector("forward_button")).click()
        self.page.wait_for_load_state("domcontentloaded")
        self._assert_origin(self.page.url)
        self._assert_module()
        if self._text("status_detail") not in self.config["forwarded_states"]:
            raise Hold("FORWARDED_STATE_NOT_VERIFIED")
        self.page.locator(self._selector("approve_button")).click()
        self.page.wait_for_load_state("domcontentloaded")
        self._assert_origin(self.page.url)

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._pw.stop()
