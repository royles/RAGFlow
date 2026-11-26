"""Provider credentials and the LLM, embedding, and re-rank model choices.

Saved as JSON in the data directory with owner-only permissions. The API never
sends a secret back to the browser; it only reports whether one is set.
"""

import copy
import json
import os
import re
import threading

from backend.errors import BadRequest
from backend.settings import data_dir

ROLE_PROVIDERS = {
    "llm": ("none", "bedrock", "openai"),
    "embedding": ("local", "bedrock", "openai"),
    "rerank": ("none", "bedrock", "openai"),
}
ROLES = tuple(ROLE_PROVIDERS)

SECRET_FIELDS = {
    "bedrock": ("access_key_id", "secret_access_key", "session_token"),
    "openai": ("api_key",),
}

LOCAL_MODEL = "local-hashing"
LOCAL_DIM = 384

DEFAULTS = {
    "bedrock": {"region": "", "access_key_id": "", "secret_access_key": "", "session_token": ""},
    "openai": {"base_url": "", "api_key": ""},
    "llm": {"provider": "none", "model": "", "base_url": "", "region": ""},
    "embedding": {"provider": "local", "model": LOCAL_MODEL, "dimensions": LOCAL_DIM, "base_url": "", "region": ""},
    "rerank": {"provider": "none", "model": "", "base_url": "", "region": ""},
}

_lock = threading.Lock()
_REGION = re.compile(r"^[a-z0-9-]+$")


def _path():
    return data_dir() / "model_settings.json"


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        stored = json.loads(_path().read_text())
    except (FileNotFoundError, ValueError):
        return cfg
    for section, fields in DEFAULTS.items():
        saved = stored.get(section)
        if isinstance(saved, dict):
            for key in fields:
                if key in saved:
                    cfg[section][key] = saved[key]
    return cfg


def save(cfg: dict) -> None:
    path = _path()
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(cfg, handle, indent=2)
    os.replace(temp, path)


def save_update(payload: dict) -> dict:
    with _lock:
        cfg = apply_update(load(), payload)
        save(cfg)
    return public(cfg)


def apply_update(current: dict, payload: dict) -> dict:
    """Return `current` with `payload` applied and checked. Nothing is saved.

    A blank secret keeps the stored value. To remove one, list it in
    payload["clear"] as "provider.field".
    """
    if not isinstance(payload, dict):
        raise BadRequest("Settings must be an object.")
    cfg = copy.deepcopy(current)

    for provider, secrets in SECRET_FIELDS.items():
        section = payload.get(provider)
        if not isinstance(section, dict):
            continue
        for key in DEFAULTS[provider]:
            if key not in section:
                continue
            value = str(section[key] or "").strip()
            if key in secrets and not value:
                continue
            cfg[provider][key] = value

    for item in payload.get("clear") or []:
        provider, _, field = str(item).partition(".")
        if field in SECRET_FIELDS.get(provider, ()):
            cfg[provider][field] = ""

    for role in ROLES:
        section = payload.get(role)
        if not isinstance(section, dict):
            continue
        for key in DEFAULTS[role]:
            if key in section:
                cfg[role][key] = str(section[key] or "").strip() if key in ("base_url", "region") else section[key]

    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    bedrock, openai = cfg["bedrock"], cfg["openai"]
    if bedrock["region"] and not _REGION.match(bedrock["region"]):
        raise BadRequest("The AWS region should look like us-east-1.")
    has_key, has_secret = bool(bedrock["access_key_id"]), bool(bedrock["secret_access_key"])
    if has_key != has_secret:
        raise BadRequest("Set both the AWS access key id and secret access key, or neither.")
    if bedrock["session_token"] and not has_key:
        raise BadRequest("A session token needs an access key id and secret access key.")
    if openai["base_url"] and not openai["base_url"].startswith(("http://", "https://")):
        raise BadRequest("The OpenAI base URL must start with http:// or https://.")
    for role in ROLES:
        url = str(cfg[role]["base_url"] or "").strip()
        if url and not url.startswith(("http://", "https://")):
            raise BadRequest(f"The {role} base URL must start with http:// or https://.")
        cfg[role]["base_url"] = url
        region = str(cfg[role]["region"] or "").strip()
        if region and not _REGION.match(region):
            raise BadRequest(f"The {role} AWS region should look like us-west-2.")
        cfg[role]["region"] = region

    for role, providers in ROLE_PROVIDERS.items():
        section = cfg[role]
        provider = str(section["provider"] or "").strip()
        if provider not in providers:
            raise BadRequest(f"{role} provider must be one of: {', '.join(providers)}.")
        section["provider"] = provider
        section["model"] = str(section["model"] or "").strip()

    for role in ("llm", "rerank"):
        section = cfg[role]
        if section["provider"] == "none":
            section["model"] = ""
        elif not section["model"]:
            raise BadRequest(f"Enter a {role} model id.")

    embedding = cfg["embedding"]
    if embedding["provider"] == "local":
        embedding["model"] = LOCAL_MODEL
        embedding["dimensions"] = LOCAL_DIM
        return
    if not embedding["model"]:
        raise BadRequest("Enter an embedding model id.")
    try:
        dimensions = int(embedding["dimensions"])
    except (TypeError, ValueError):
        raise BadRequest("Embedding dimensions must be a whole number.") from None
    if dimensions < 8 or dimensions > 4096:
        raise BadRequest("Embedding dimensions must be between 8 and 4096.")
    embedding["dimensions"] = dimensions


def public(cfg: dict) -> dict:
    """The settings as the browser sees them: no secret values."""
    bedrock, openai = cfg["bedrock"], cfg["openai"]
    return {
        "bedrock": {
            "region": bedrock["region"],
            "access_key_id_set": bool(bedrock["access_key_id"]),
            "secret_access_key_set": bool(bedrock["secret_access_key"]),
            "session_token_set": bool(bedrock["session_token"]),
            "credential_source": "saved" if bedrock["access_key_id"] else "default",
            "effective_region": effective_region(cfg),
        },
        "openai": {
            "base_url": openai["base_url"],
            "api_key_set": bool(openai["api_key"]),
            "api_key_source": _openai_key_source(cfg),
            "effective_base_url": openai["base_url"] or os.environ.get("OPENAI_BASE_URL", "").strip(),
        },
        **{
            role: {
                **cfg[role],
                "effective_base_url": openai_base_url(cfg, role),
                "effective_region": effective_region(cfg, role),
            }
            for role in ROLES
        },
    }


def effective_region(cfg: dict, role: str | None = None) -> str:
    """The role's own region, else the shared Bedrock region, else the environment."""
    return (
        (cfg[role]["region"] if role else "")
        or cfg["bedrock"]["region"]
        or os.environ.get("AWS_REGION", "").strip()
        or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        or "us-east-1"
    )


def bedrock_credentials(cfg: dict, role: str) -> dict:
    bedrock = cfg["bedrock"]
    return {
        "region": effective_region(cfg, role),
        "access_key_id": bedrock["access_key_id"] or None,
        "secret_access_key": bedrock["secret_access_key"] or None,
        "session_token": bedrock["session_token"] or None,
    }


def openai_base_url(cfg: dict, role: str) -> str:
    """The role's own base URL, else the shared one, else OPENAI_BASE_URL. Blank means api.openai.com."""
    return cfg[role]["base_url"] or cfg["openai"]["base_url"] or os.environ.get("OPENAI_BASE_URL", "").strip()


def openai_credentials(cfg: dict, role: str) -> dict:
    base_url = openai_base_url(cfg, role) or None
    api_key = cfg["openai"]["api_key"] or os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        if not base_url:
            raise BadRequest("Add an OpenAI API key, or a base URL for a server that needs none.")
        api_key = "not-needed"
    return {"base_url": base_url, "api_key": api_key}


def redact(text: str, cfg: dict) -> str:
    for provider, fields in SECRET_FIELDS.items():
        for field in fields:
            secret = cfg[provider][field]
            if secret:
                text = text.replace(secret, "***")
    return text


def _openai_key_source(cfg: dict) -> str:
    if cfg["openai"]["api_key"]:
        return "saved"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "environment"
    return "none"
