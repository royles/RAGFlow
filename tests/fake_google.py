"""A tiny stand-in for Google's OAuth and Drive endpoints.

It checks what a real Google would: the client id and secret, the redirect
address, and that the PKCE verifier matches the challenge from the sign-in URL.
"""

import base64
import hashlib

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

CLIENT_ID = "test-client.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret"

app = FastAPI()
codes: dict[str, dict] = {}
valid_tokens: set[str] = set()
revoked: list[str] = []
calls: list[dict] = []
state = {"counter": 0, "refresh_revoked": False}

FOLDER = "application/vnd.google-apps.folder"
FILES = {
    "folder1": {"name": "Handbook", "mimeType": FOLDER, "parents": ["root"]},
    "f_notes": {"name": "notes.txt", "mimeType": "text/plain", "parents": ["root"], "body": b"The Dublin warehouse code is DUB-14."},
    "f_photo": {"name": "photo.png", "mimeType": "image/png", "parents": ["root"], "body": b"\x89PNG"},
    "g_doc": {"name": "Quarterly plan", "mimeType": "application/vnd.google-apps.document", "parents": ["root"], "body": b"Plan: ship the Dublin warehouse in Q3."},
    "g_sheet": {"name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet", "parents": ["root"], "body": b"item,cost\nrack,100\n"},
    "g_form": {"name": "Survey", "mimeType": "application/vnd.google-apps.form", "parents": ["root"]},
    "f_policy": {"name": "policy.md", "mimeType": "text/markdown", "parents": ["folder1"], "body": b"# Policy\nBadges are required on site."},
    "f_shared": {"name": "shared.txt", "mimeType": "text/plain", "parents": [], "shared": True, "body": b"Shared with me."},
}


def reset() -> None:
    codes.clear()
    valid_tokens.clear()
    revoked.clear()
    calls.clear()
    state.update(counter=0, refresh_revoked=False)


def issue_code(auth_query: dict) -> str:
    """What Google does after the user approves: remember the challenge and hand back a code."""
    state["counter"] += 1
    code = f"code-{state['counter']}"
    codes[code] = {
        "challenge": auth_query["code_challenge"],
        "redirect_uri": auth_query["redirect_uri"],
        "client_id": auth_query["client_id"],
    }
    return code


def expire_access_tokens() -> None:
    valid_tokens.clear()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _new_token() -> str:
    state["counter"] += 1
    token = f"at-{state['counter']}"
    valid_tokens.add(token)
    return token


@app.get("/auth")
async def auth(request: Request):
    """The consent screen, with the user always approving. Used when driving the UI by hand."""
    query = dict(request.query_params)
    code = issue_code(query)
    return RedirectResponse(f"{query['redirect_uri']}?code={code}&state={query['state']}", status_code=302)


@app.post("/token")
async def token(
    grant_type: str = Form(...),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
):
    calls.append({"path": "token", "grant": grant_type})
    if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if grant_type == "authorization_code":
        issued = codes.pop(code, None)
        if not issued:
            return JSONResponse({"error": "invalid_grant", "error_description": "Bad code"}, status_code=400)
        if issued["redirect_uri"] != redirect_uri or issued["challenge"] != _challenge(code_verifier):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE or redirect mismatch"}, status_code=400)
        return {"access_token": _new_token(), "refresh_token": "rt-1", "expires_in": 3600, "scope": "drive.readonly"}
    if grant_type == "refresh_token":
        if state["refresh_revoked"] or refresh_token != "rt-1":
            return JSONResponse({"error": "invalid_grant", "error_description": "Token has been revoked"}, status_code=400)
        return {"access_token": _new_token(), "expires_in": 3600}
    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


@app.post("/revoke")
async def revoke(token: str = Form(...)):
    revoked.append(token)
    return {}


def _authorise(request: Request) -> None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or header[7:] not in valid_tokens:
        raise HTTPException(status_code=401, detail="Invalid Credentials")


@app.get("/drive/v3/about")
async def about(request: Request):
    _authorise(request)
    return {"user": {"displayName": "Dana Test", "emailAddress": "dana@example.com"}}


@app.get("/drive/v3/files")
async def list_files(request: Request):
    _authorise(request)
    query = request.query_params["q"]
    calls.append({"path": "list", "q": query})
    matches = []
    for file_id, item in FILES.items():
        if "sharedWithMe = true" in query:
            keep = item.get("shared", False)
        elif " in parents" in query:
            parent = query.split("'")[1]
            keep = parent in item["parents"]
        elif "name contains" in query:
            keep = query.split("'")[1].lower() in item["name"].lower()
        else:
            keep = False
        if keep:
            matches.append({"id": file_id, "name": item["name"], "mimeType": item["mimeType"], "size": str(len(item.get("body", b""))) or None})
    return {"files": matches}


@app.get("/drive/v3/files/{file_id}")
async def get_file(file_id: str, request: Request):
    _authorise(request)
    item = FILES.get(file_id)
    if item is None:
        return JSONResponse({"error": {"code": 404, "message": "File not found: " + file_id}}, status_code=404)
    if request.query_params.get("alt") == "media":
        calls.append({"path": "download", "id": file_id})
        return Response(item["body"], media_type=item["mimeType"])
    return {"id": file_id, "name": item["name"], "mimeType": item["mimeType"], "size": str(len(item.get("body", b"")))}


@app.get("/drive/v3/files/{file_id}/export")
async def export_file(file_id: str, request: Request):
    _authorise(request)
    calls.append({"path": "export", "id": file_id, "mimeType": request.query_params.get("mimeType")})
    return Response(FILES[file_id]["body"], media_type=request.query_params.get("mimeType", "text/plain"))
