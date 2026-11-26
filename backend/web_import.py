"""Download a document from a web address so it can be indexed like an upload.

Pages, PDFs, Word, CSV, Markdown and plain-text files are supported. Because the
server fetches whatever address it is given, it refuses addresses that are not on
the public internet (loopback, private ranges, link-local such as cloud metadata)
unless "Allow URLs on private networks" is on in Advanced. The check runs for the
first address and again for every redirect.
"""

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from backend import advanced
from backend.errors import BadRequest
from backend.extract import SUPPORTED, read_html
from backend.settings import max_upload_bytes

MAX_REDIRECTS = 5
MAX_SECONDS = 120
MAX_NAME = 100
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
HEADERS = {"User-Agent": "RAGFlow-Importer/1.0", "Accept": "text/html,application/pdf,text/*;q=0.9,*/*;q=0.5"}

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TYPES = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "application/x-pdf": ".pdf",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/plain": ".txt",
    _DOCX: ".docx",
}
_GENERIC = {"", "application/octet-stream", "binary/octet-stream", "text/plain"}

# Tests replace this to serve pages without a network.
_transport: httpx.BaseTransport | None = None


@dataclass
class Fetched:
    filename: str
    data: bytes
    url: str


def normalise(value: str) -> str:
    """A clean http(s) address. A bare host such as example.com/page gets https://."""
    url = str(value or "").strip()
    if not url:
        raise BadRequest("Enter a web address.")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BadRequest("Only http and https addresses can be imported.")
    if not parts.hostname:
        raise BadRequest(f"{value!r} is not a web address.")
    if parts.username or parts.password:
        raise BadRequest("Addresses with a user name or password are not allowed.")
    return url


def fetch(value: str) -> Fetched:
    url = normalise(value)
    started = time.monotonic()
    with httpx.Client(transport=_transport, timeout=TIMEOUT, headers=HEADERS, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(urlsplit(url).hostname)
            try:
                with client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        if urlsplit(url).scheme not in ("http", "https"):
                            raise BadRequest("The page redirected to an address that is not http or https.")
                        continue
                    if response.status_code >= 400:
                        raise BadRequest(f"{urlsplit(url).hostname} answered HTTP {response.status_code}.")
                    data = _read(response, started)
                    return _describe(url, response.headers, data)
            except httpx.HTTPError as exc:
                raise BadRequest(f"Could not download {url}: {exc}") from exc
    raise BadRequest(f"Too many redirects (more than {MAX_REDIRECTS}).")


def _check_host(host: str | None) -> None:
    if advanced.get("url_allow_private"):
        return
    try:
        found = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise BadRequest(f"Could not find {host}: {exc.strerror or exc}") from exc
    for text in found:
        address = ipaddress.ip_address(text.split("%")[0])
        address = getattr(address, "ipv4_mapped", None) or address
        if not address.is_global or address.is_multicast:
            raise BadRequest(
                f"{host} is on a private network address, so it was not fetched. "
                "Turn on 'Allow URLs on private networks' in Advanced to import from it."
            )


def _read(response: httpx.Response, started: float) -> bytes:
    limit = max_upload_bytes()
    declared = response.headers.get("content-length", "")
    if limit and declared.isdigit() and int(declared) > limit:
        raise BadRequest(f"That file is larger than {limit // (1024 * 1024)} MB (RAGFLOW_MAX_UPLOAD_MB).")
    data = bytearray()
    for part in response.iter_bytes():
        data += part
        if limit and len(data) > limit:
            raise BadRequest(f"That file is larger than {limit // (1024 * 1024)} MB (RAGFLOW_MAX_UPLOAD_MB).")
        if time.monotonic() - started > MAX_SECONDS:
            raise BadRequest(f"The download took longer than {MAX_SECONDS} seconds.")
    if not data:
        raise BadRequest("The address returned nothing.")
    return bytes(data)


def _describe(url: str, headers: httpx.Headers, data: bytes) -> Fetched:
    parts = urlsplit(url)
    ctype = headers.get("content-type", "").split(";")[0].strip().lower()
    path_name = Path(unquote(parts.path)).name
    suffix = _suffix(ctype, Path(path_name).suffix.lower(), data)
    disposition = _disposition_name(headers.get("content-disposition", ""))
    if suffix == ".html":
        title = read_html(data)[0]
        stem = title or Path(disposition or path_name).stem or parts.hostname
    else:
        stem = Path(disposition or path_name).stem or parts.hostname
    return Fetched(clean_name(stem, suffix), data, url)


def _suffix(ctype: str, path_suffix: str, data: bytes) -> str:
    if ctype in _TYPES and ctype not in _GENERIC:
        return _TYPES[ctype]
    if path_suffix in SUPPORTED:
        return path_suffix
    if ctype == "text/plain":
        return ".txt"
    if ctype in _GENERIC:
        head = data[:2048].lower()
        if data.startswith(b"%PDF"):
            return ".pdf"
        if b"<html" in head or b"<!doctype html" in head:
            return ".html"
        sample = data[:4096].decode("utf-8", errors="replace")
        if sample.count("\ufffd") <= len(sample) // 50 and "\x00" not in sample:
            return ".txt"
    raise BadRequest(f"That address returned {ctype or 'an unknown type'}, which cannot be imported.")


def _disposition_name(header: str) -> str:
    match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)|filename=\"?([^\";]+)\"?", header, re.IGNORECASE)
    return Path(unquote((match.group(1) or match.group(2)).strip())).name if match else ""


def clean_name(stem: str, suffix: str) -> str:
    """A file name safe to store and show, ending in `suffix`."""
    stem = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", str(stem or ""))
    stem = re.sub(r"\s+", " ", stem).strip(" .")[: MAX_NAME - len(suffix)].strip(" .") or "document"
    return stem + suffix
