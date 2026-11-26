"""Google Drive as a document source, signed in with OAuth 2.0.

One Google account is connected for the whole app. The flow is the standard
authorization-code flow with PKCE and the read-only Drive scope, so RAGFlow can
read files but never change them. The client id and secret come from the
operator's own Google Cloud project, and the refresh token is kept in
`data/google_drive.json` with owner-only permissions. Nothing secret is ever
sent back to the browser.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

from backend.errors import BadRequest
from backend.extract import SUPPORTED
from backend.settings import data_dir, max_upload_bytes

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
CALLBACK_PATH = "/api/google/callback"
FOLDER = "application/vnd.google-apps.folder"
PENDING_SECONDS = 600
MAX_PENDING = 20
PAGE_SIZE = 100

# Google's own formats are exported. Each maps to (export type, file extension).
EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}

_lock = threading.RLock()
_pending: dict[str, dict] = {}


def _urls() -> dict[str, str]:
    """Google's endpoints. The environment can point them elsewhere for tests."""
    return {
        "auth": os.environ.get("GOOGLE_OAUTH_AUTH_URL") or "https://accounts.google.com/o/oauth2/v2/auth",
        "token": os.environ.get("GOOGLE_OAUTH_TOKEN_URL") or "https://oauth2.googleapis.com/token",
        "revoke": os.environ.get("GOOGLE_OAUTH_REVOKE_URL") or "https://oauth2.googleapis.com/revoke",
        "api": (os.environ.get("GOOGLE_DRIVE_API_URL") or "https://www.googleapis.com/drive/v3").rstrip("/"),
    }


def _path() -> Path:
    return data_dir() / "google_drive.json"


def _load() -> dict:
    try:
        stored = json.loads(_path().read_text())
    except (FileNotFoundError, ValueError):
        stored = {}
    return stored if isinstance(stored, dict) else {}


def _save(state: dict) -> None:
    path = _path()
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle, indent=2)
    os.replace(temp, path)


def _client(state: dict) -> tuple[str, str]:
    """Client id and secret: saved values first, then the environment."""
    client_id = state.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = state.get("client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    return client_id, client_secret


def public() -> dict:
    """What the page may show. Never a secret or a token."""
    state = _load()
    client_id, client_secret = _client(state)
    return {
        "client_id": client_id,
        "client_secret_set": bool(client_secret),
        "client_source": "saved" if state.get("client_id") else ("environment" if client_id else "none"),
        "configured": bool(client_id and client_secret),
        "connected": bool(state.get("refresh_token")),
        "account": state.get("account") or None,
        "scope": SCOPE,
        "callback_path": CALLBACK_PATH,
    }


def save_credentials(payload: dict) -> dict:
    """Save the OAuth client. A blank secret keeps the saved one. A new client id drops the old sign-in."""
    with _lock:
        state = _load()
        client_id = str(payload.get("client_id") or "").strip()
        secret = str(payload.get("client_secret") or "").strip()
        if len(client_id) > 300 or len(secret) > 300:
            raise BadRequest("That client id or secret is too long.")
        if client_id != state.get("client_id", ""):
            for key in ("refresh_token", "access_token", "expires_at", "account"):
                state.pop(key, None)
        state["client_id"] = client_id
        if secret:
            state["client_secret"] = secret
        if "client_secret" in (payload.get("clear") or []):
            state.pop("client_secret", None)
        _save(state)
    return public()


def begin(redirect_uri: str) -> str:
    """Start sign-in and return the Google URL to send the browser to."""
    redirect_uri = str(redirect_uri or "").strip()
    if not redirect_uri.endswith(CALLBACK_PATH) or not redirect_uri.startswith(("http://", "https://")):
        raise BadRequest(f"The redirect address must be this app's address followed by {CALLBACK_PATH}.")
    client_id, client_secret = _client(_load())
    if not (client_id and client_secret):
        raise BadRequest("Save the Google client id and secret first.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for key in [key for key, item in _pending.items() if item["expires"] < now]:
            del _pending[key]
        while len(_pending) >= MAX_PENDING:
            del _pending[next(iter(_pending))]
        _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "expires": now + PENDING_SECONDS}
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"{_urls()['auth']}?{query}"


def finish(code: str, state: str) -> dict:
    """Trade the code Google sent back for tokens. The state is single use."""
    with _lock:
        pending = _pending.pop(state or "", None)
    if pending is None or pending["expires"] < time.time():
        raise BadRequest("That sign-in link has expired or was not started here. Connect again.")
    saved = _load()
    client_id, client_secret = _client(saved)
    reply = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": pending["verifier"],
        }
    )
    refresh = reply.get("refresh_token") or saved.get("refresh_token")
    if not refresh:
        raise BadRequest("Google did not return a refresh token. Remove RAGFlow at myaccount.google.com/permissions and connect again.")
    with _lock:
        state_now = _load()
        state_now.update(refresh_token=refresh, **_access(reply))
        state_now.pop("account", None)
        _save(state_now)
    account = _account()
    with _lock:
        state_now = _load()
        state_now["account"] = account
        _save(state_now)
    return public()


def disconnect() -> dict:
    """Forget the sign-in, and ask Google to revoke the grant."""
    with _lock:
        state = _load()
        token = state.get("refresh_token") or state.get("access_token")
        for key in ("refresh_token", "access_token", "expires_at", "account"):
            state.pop(key, None)
        _save(state)
    if token:
        try:
            httpx.post(_urls()["revoke"], data={"token": token}, timeout=15)
        except httpx.HTTPError:
            pass  # the local sign-in is already gone; the grant can be removed from the Google account page
    return public()


def list_files(parent: str = "", query: str = "", page_token: str = "") -> dict:
    """One page of a folder, of the files shared with the user, or of a search."""
    parent = (parent or "root").strip()
    if query.strip():
        term = _quote(query.strip()[:200])
        q = f"name contains '{term}' and trashed = false"
    elif parent == "shared":
        q = "sharedWithMe = true and trashed = false"
    else:
        q = f"'{_quote(parent)}' in parents and trashed = false"
    params = {
        "q": q,
        "pageSize": PAGE_SIZE,
        "orderBy": "folder,name",
        "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    if page_token:
        params["pageToken"] = page_token
    data = _api("GET", "/files", params=params).json()
    files = [_describe(item) for item in data.get("files", [])]
    return {"files": files, "next_page_token": data.get("nextPageToken") or ""}


def fetch(file_id: str) -> tuple[str, bytes]:
    """File name and bytes, with Google Docs, Sheets and Slides exported as text."""
    file_id = _file_id(file_id)
    meta = _api("GET", f"/files/{file_id}", params={"fields": "id,name,mimeType,size", "supportsAllDrives": "true"}).json()
    name = str(meta.get("name") or "document")
    mime = str(meta.get("mimeType") or "")
    if mime in EXPORTS:
        export_type, extension = EXPORTS[mime]
        response = _api(
            "GET", f"/files/{file_id}/export", params={"mimeType": export_type, "supportsAllDrives": "true"}
        )
        if not name.lower().endswith(extension):
            name += extension
        return name, response.content
    if mime == FOLDER or mime.startswith("application/vnd.google-apps."):
        raise BadRequest(f"{name} is a Google {mime.rsplit('.', 1)[-1]}, which cannot be imported.")
    if Path(name).suffix.lower() not in SUPPORTED:
        raise BadRequest(f"{name}: unsupported file type {Path(name).suffix or '(none)'}.")
    limit = max_upload_bytes()
    if limit and int(meta.get("size") or 0) > limit:
        raise BadRequest(f"{name} is larger than {limit // (1024 * 1024)} MB (RAGFLOW_MAX_UPLOAD_MB).")
    response = _api("GET", f"/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"})
    return name, response.content


def _describe(item: dict) -> dict:
    mime = str(item.get("mimeType") or "")
    name = str(item.get("name") or "")
    folder = mime == FOLDER
    return {
        "id": item["id"],
        "name": name,
        "mime_type": mime,
        "is_folder": folder,
        "size": int(item["size"]) if str(item.get("size") or "").isdigit() else None,
        "modified": item.get("modifiedTime") or "",
        "importable": mime in EXPORTS or (not folder and not mime.startswith("application/vnd.google-apps.") and Path(name).suffix.lower() in SUPPORTED),
    }


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


_URL_ID = re.compile(r"/(?:document|spreadsheets|presentation|file)/d/([A-Za-z0-9_-]+)|[?&]id=([A-Za-z0-9_-]+)")


def id_from_url(url: str) -> str:
    """The Drive file id in a docs.google.com or drive.google.com link, or "" for any other address."""
    parts = urlsplit(str(url))
    host = (parts.hostname or "").lower()
    if host not in ("docs.google.com", "drive.google.com"):
        return ""
    match = _URL_ID.search(parts.path + ("?" + parts.query if parts.query else ""))
    return (match.group(1) or match.group(2)) if match else ""


def _file_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or not all(char.isalnum() or char in "-_" for char in value):
        raise BadRequest("That is not a Google Drive file id.")
    return value


def _access(reply: dict) -> dict:
    return {
        "access_token": reply.get("access_token", ""),
        "expires_at": time.time() + int(reply.get("expires_in") or 3600),
    }


def _token_request(form: dict) -> dict:
    try:
        response = httpx.post(_urls()["token"], data=form, timeout=30)
    except httpx.HTTPError as exc:
        raise BadRequest(f"Could not reach Google to sign in: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200:
        if body.get("error") == "invalid_grant" and form.get("grant_type") == "refresh_token":
            _forget_tokens()
            raise BadRequest("Google Drive access was revoked or has expired. Connect again.")
        detail = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
        raise BadRequest(f"Google refused the sign-in: {detail}")
    return body


def _forget_tokens() -> None:
    with _lock:
        state = _load()
        for key in ("refresh_token", "access_token", "expires_at", "account"):
            state.pop(key, None)
        _save(state)


def _token(force: bool = False) -> str:
    """A current access token, refreshed when it is about to expire."""
    with _lock:
        state = _load()
        if not state.get("refresh_token"):
            raise BadRequest("Google Drive is not connected. Connect it on the Google Drive page.")
        if not force and state.get("access_token") and float(state.get("expires_at") or 0) - time.time() > 60:
            return state["access_token"]
        client_id, client_secret = _client(state)
        reply = _token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": state["refresh_token"],
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )
        state.update(_access(reply))
        _save(state)
        return state["access_token"]


def _api(method: str, path: str, params: dict | None = None) -> httpx.Response:
    for attempt in range(2):
        try:
            response = httpx.request(
                method,
                _urls()["api"] + path,
                params=params,
                headers={"Authorization": f"Bearer {_token(force=attempt == 1)}"},
                timeout=120,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise BadRequest(f"Could not reach Google Drive: {exc}") from exc
        if response.status_code == 401 and attempt == 0:
            continue
        break
    if response.status_code >= 400:
        raise BadRequest(f"Google Drive said: {_error_message(response)}")
    return response


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
        message = error.get("message") if isinstance(error, dict) else error
    except (ValueError, AttributeError):
        message = None
    return str(message or f"HTTP {response.status_code}")


def _account() -> dict | None:
    try:
        user = _api("GET", "/about", params={"fields": "user(displayName,emailAddress)"}).json().get("user") or {}
    except BadRequest:
        return None
    return {"email": user.get("emailAddress", ""), "name": user.get("displayName", "")}
