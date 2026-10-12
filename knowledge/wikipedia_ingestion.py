"""Vietnamese Wikipedia ingestion into the independent V1 knowledge stores."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .chroma_store import ChromaKnowledgeStore
from .media_store import FilesystemMediaStore


WIKIPEDIA_VI = "https://vi.wikipedia.org"


class WikipediaIngestionError(ValueError):
    """The requested Vietnamese Wikipedia article cannot be ingested."""


class WikipediaClient(Protocol):
    def parse_page(self, title: str) -> Mapping[str, Any]: ...
    def list_images(self, title: str) -> list[Mapping[str, Any]]: ...
    def download(self, url: str) -> tuple[bytes, str]: ...


class MediaWikiClient:
    """Small live client; tests inject a fake instead of making network calls."""

    api_url = f"{WIKIPEDIA_VI}/w/api.php"

    def parse_page(self, title: str) -> Mapping[str, Any]:
        return self._api({"action": "parse", "page": title, "redirects": "1", "prop": "text|revid|displaytitle", "formatversion": "2"})["parse"]

    def list_images(self, title: str) -> list[Mapping[str, Any]]:
        images: list[Mapping[str, Any]] = []
        params = {"action": "query", "generator": "images", "titles": title, "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "gimlimit": "max"}
        while True:
            data = self._api(params)
            for page in data.get("query", {}).get("pages", {}).values():
                info = (page.get("imageinfo") or [{}])[0]
                if info.get("url") and info.get("mime"):
                    images.append({"title": page.get("title", ""), "url": info["url"], "mime_type": info["mime"], "license": _license(info.get("extmetadata", {}))})
            continuation = data.get("continue", {}).get("gimcontinue")
            if not continuation:
                break
            params["gimcontinue"] = continuation
        return images

    def download(self, url: str) -> tuple[bytes, str]:
        request = Request(url, headers={"User-Agent": "LumiKnowledgeIngestion/1.0"})
        with urlopen(request, timeout=30) as response:  # nosec B310 - URL comes from MediaWiki imageinfo
            return response.read(), response.headers.get_content_type()

    def _api(self, params: Mapping[str, str]) -> Mapping[str, Any]:
        query = "&".join(f"{quote(key)}={quote(value)}" for key, value in {**params, "format": "json"}.items())
        request = Request(f"{self.api_url}?{query}", headers={"User-Agent": "LumiKnowledgeIngestion/1.0"})
        with urlopen(request, timeout=30) as response:  # nosec B310 - fixed API endpoint
            data = json.loads(response.read())
        if "error" in data:
            raise WikipediaIngestionError(data["error"].get("info", "MediaWiki API error"))
        return data


@dataclass(frozen=True, slots=True)
class WikipediaIngestResult:
    document_id: str
    source_uri: str
    source_revision: str
    passages: int
    media: int
    warnings: list[str]


class WikipediaIngestionProvider:
    def __init__(self, chroma: ChromaKnowledgeStore, media_store: FilesystemMediaStore, client: WikipediaClient | None = None) -> None:
        self._chroma, self._media_store, self._client = chroma, media_store, client or MediaWikiClient()

    def ingest(self, source_uri: str) -> WikipediaIngestResult:
        canonical_uri, requested_title = _article_uri(source_uri)
        page = self._client.parse_page(requested_title)
        title = _text(page.get("title"), "Wikipedia page title")
        revision = str(page.get("revid") or "")
        if not revision:
            raise WikipediaIngestionError("Wikipedia response has no revid.")
        document_id = _id("wikipedia_vi", canonical_uri)
        document = {"document_id": document_id, "package_id": "wikipedia_vi", "title": title, "source_uri": canonical_uri, "source_revision": revision, "retrieved_at": datetime.now(timezone.utc).isoformat()}
        passages = [
            {"passage_id": _id(document_id, str(index)), "document_id": document_id, "package_id": "wikipedia_vi", "text": text, "passage_ordinal": index, "section_title": section[-1], "section_path": " > ".join(section), "source_uri": canonical_uri, "retrieved_at": document["retrieved_at"], "source_revision": revision}
            for index, (text, section) in enumerate(_paragraphs(_text(page.get("text"), "Wikipedia HTML")))
        ]
        self._chroma.stage_and_activate(document, passages)

        warnings: list[str] = []
        media_records: list[dict[str, str]] = []
        new_keys: list[str] = []
        for image in self._client.list_images(title):
            try:
                source = _text(image.get("url"), "image URL")
                media_id = _id("wikipedia_vi", source)
                data, downloaded_mime = self._client.download(source)
                mime_type = _text(image.get("mime_type") or downloaded_mime, "image MIME")
                object_key = self._media_store.put(media_id, revision, mime_type, data)
                new_keys.append(object_key)
                media_records.append({"media_id": media_id, "package_id": "wikipedia_vi", "mime_type": mime_type, "title": _file_title(image.get("title")), "source_uri": source, "source_revision": revision, "object_key": object_key, "license": str(image.get("license") or "")})
            except Exception as exc:
                warnings.append(f"image skipped: {exc}")
        try:
            old_keys = self._chroma.stage_media_and_activate(media_records)
        except Exception:
            for key in new_keys:
                self._media_store.delete(key)
            raise
        for key in old_keys:
            if key not in new_keys:
                self._media_store.delete(key)
        return WikipediaIngestResult(document_id, canonical_uri, revision, len(passages), len(media_records), warnings)


def _paragraphs(html: str) -> list[tuple[str, list[str]]]:
    soup = BeautifulSoup(html, "html.parser")
    section = ["Mở đầu"]
    result: list[tuple[str, list[str]]] = []
    for node in soup.find_all(["h2", "h3", "h4", "h5", "h6", "p"]):
        if node.name.startswith("h"):
            level = int(node.name[1]) - 1
            heading = node.get_text(" ", strip=True).replace("[ sửa | sửa mã nguồn ]", "").strip()
            if heading:
                section = section[: max(0, level - 1)] + [heading]
        else:
            text = node.get_text(" ", strip=True)
            if text:
                result.extend((part, list(section)) for part in _split_paragraph(text))
    return result


def _split_paragraph(text: str) -> list[str]:
    """Keep a paragraph whole unless it exceeds the approved 600-token cap."""

    if len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)) <= 600:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in re.split(r"(?<=[.!?…])\s+", text):
        tokens = len(re.findall(r"\w+|[^\w\s]", sentence, flags=re.UNICODE))
        if current and current_tokens + tokens > 600:
            parts.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += tokens
    if current:
        parts.append(" ".join(current))
    return parts


def _article_uri(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "vi.wikipedia.org" or not parsed.path.startswith("/wiki/"):
        raise WikipediaIngestionError("source_uri must be an https://vi.wikipedia.org/wiki/... article URL.")
    title = unquote(parsed.path.removeprefix("/wiki/")).replace("_", " ").strip()
    if not title:
        raise WikipediaIngestionError("source_uri has no Wikipedia article title.")
    return f"{WIKIPEDIA_VI}/wiki/{quote(title.replace(' ', '_'))}", title


def _id(*values: str) -> str:
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WikipediaIngestionError(f"{field} must be non-empty.")
    return value.strip()


def _file_title(value: object) -> str:
    title = _text(value, "image title")
    return title.removeprefix("File:").removeprefix("Tập tin:")


def _license(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    return str(value.get("LicenseShortName", {}).get("value", "")) if isinstance(value.get("LicenseShortName"), Mapping) else ""
