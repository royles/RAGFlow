"""Advanced overrides, grouped, with the environment variable each one replaces.

A value saved here wins over the environment variable, which wins over the
default. Saving a blank removes the override, so the environment applies again.
Secrets are stored with owner-only permissions and never sent back.

Saved values live in `data/advanced_settings.json`, so they survive restarts.
`apply()` writes them into the process environment at startup and after every
save, so anything that reads the variable directly, such as a worker process,
sees the same value. The environment's own value is remembered and put back when
an override is removed.
"""

import json
import os
import re
import threading

from backend.errors import BadRequest
from backend.settings import data_dir

_HOST = re.compile(r"^[A-Za-z0-9.\-\[\]:*]+$")
_lock = threading.RLock()
_originals: dict[str, str | None] = {}

# type: int (whole number, zero or more), url, hosts (comma-separated), secret
SETTINGS = [
    {
        "key": "max_upload_mb",
        "env": "RAGFLOW_MAX_UPLOAD_MB",
        "group": "Uploads and indexing",
        "label": "Largest upload (MB)",
        "type": "int",
        "max": 100_000,
        "default": 0,
        "help": "Rejects any single file bigger than this. 0 means no limit. Files are read into memory, so cap it if the runtime has little RAM.",
    },
    {
        "key": "max_chunks",
        "env": "RAGFLOW_MAX_CHUNKS",
        "group": "Uploads and indexing",
        "label": "Most chunks per upload",
        "type": "int",
        "max": 10_000_000,
        "default": 0,
        "help": "Rejects a file that would produce more chunks than this. 0 means no limit. Every chunk is embedded, so huge files can run into the platform's request timeout.",
    },
    {
        "key": "audit_retention_days",
        "env": "RAGFLOW_AUDIT_RETENTION_DAYS",
        "group": "Audit log",
        "label": "Keep answers for (days)",
        "type": "int",
        "max": 36_500,
        "default": 0,
        "restart": True,
        "help": "Answers, ratings and scores older than this are deleted when the app starts. 0 keeps everything.",
    },
    {
        "key": "qdrant_url",
        "env": "QDRANT_URL",
        "group": "Vector store",
        "label": "Qdrant server URL",
        "type": "url",
        "default": "",
        "help": "Use a shared Qdrant server instead of the index stored in the project. Blank uses the embedded index. Existing knowledge bases stay in the store they were created in, so switching hides them until you switch back.",
    },
    {
        "key": "qdrant_api_key",
        "env": "QDRANT_API_KEY",
        "group": "Vector store",
        "label": "Qdrant API key",
        "type": "secret",
        "default": "",
        "help": "Sent to the Qdrant server above. Leave blank for a server that needs none.",
    },
    {
        "key": "mcp_allowed_hosts",
        "env": "RAGFLOW_MCP_ALLOWED_HOSTS",
        "group": "Agents (MCP)",
        "label": "Allowed hosts for /mcp",
        "type": "hosts",
        "default": "",
        "restart": True,
        "help": "Comma-separated host names that may call /mcp. Blank allows any host on Cloudera AI, where the platform proxy sets the name. Loopback and CDSW_MASTER_IP are always allowed.",
    },
]
_BY_KEY = {item["key"]: item for item in SETTINGS}

# Fixed when the app starts. Shown for reference, not editable here.
READ_ONLY = [
    ("RAGFLOW_DATA_DIR", "Data directory", "Where the catalog, audit log, index and saved settings live. It cannot be changed from inside itself."),
    ("CDSW_APP_PORT", "Application port", "Set by Cloudera AI. The UI, API and /mcp are served on 127.0.0.1 at this port."),
    ("CDSW_MASTER_IP", "Master address", "Set by Cloudera AI. Workers started with cdsw.workers talk back to this address."),
]


def _path():
    return data_dir() / "advanced_settings.json"


def _load() -> dict:
    try:
        stored = json.loads(_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return {key: str(value) for key, value in stored.items() if key in _BY_KEY and value not in (None, "")}


def _save(values: dict) -> None:
    path = _path()
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(values, handle, indent=2)
    os.replace(temp, path)


def _number(text: str, spec: dict) -> int | None:
    try:
        value = int(str(text).strip())
    except ValueError:
        return None
    return value if 0 <= value <= spec.get("max", 10**9) else None


def _environment(spec: dict) -> str:
    """The variable as the operator set it, ignoring anything apply() wrote."""
    env = spec["env"]
    original = _originals[env] if env in _originals else os.environ.get(env)
    return (original or "").strip()


def apply() -> None:
    """Put saved overrides into the process environment, and restore the rest."""
    with _lock:
        saved = _load()
        for spec in SETTINGS:
            env = spec["env"]
            if spec["key"] in saved:
                _originals.setdefault(env, os.environ.get(env))
                os.environ[env] = saved[spec["key"]]
            elif env in _originals:
                original = _originals.pop(env)
                if original is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = original


def get(key: str):
    """The value in force: saved override, then the environment, then the default."""
    spec = _BY_KEY[key]
    raw = _load().get(key)
    if raw is None:
        raw = _environment(spec)
    if spec["type"] == "int":
        value = _number(raw, spec) if raw else None
        return spec["default"] if value is None else value
    return raw or spec["default"]


def hosts() -> list[str]:
    return [item.strip() for item in str(get("mcp_allowed_hosts")).split(",") if item.strip()]


def public() -> dict:
    """Every setting with where its value comes from. Secrets only say whether they are set."""
    saved = _load()
    items = []
    for spec in SETTINGS:
        secret = spec["type"] == "secret"
        env_value = _environment(spec)
        source = "saved" if spec["key"] in saved else ("environment" if env_value else "default")
        items.append(
            {
                "key": spec["key"],
                "env": spec["env"],
                "group": spec["group"],
                "label": spec["label"],
                "help": spec["help"],
                "type": spec["type"],
                "default": spec["default"],
                "restart": bool(spec.get("restart")),
                "source": source,
                "value": "" if secret else saved.get(spec["key"], ""),
                "env_value": "" if secret else env_value,
                "is_set": bool(saved.get(spec["key"]) or env_value) if secret else None,
            }
        )
    groups: list[dict] = []
    for item in items:
        if not groups or groups[-1]["name"] != item["group"]:
            groups.append({"name": item["group"], "settings": []})
        groups[-1]["settings"].append(item)
    return {
        "groups": groups,
        "environment": [
            {"env": env, "label": label, "help": text, "value": os.environ.get(env, "").strip() or ("data" if env == "RAGFLOW_DATA_DIR" else "")}
            for env, label, text in READ_ONLY
        ],
    }


def update(payload: dict) -> dict:
    """Apply `values` (key to text, blank removes the override) and `clear` (secret keys to remove)."""
    values = payload.get("values", {})
    clear = payload.get("clear", [])
    if not isinstance(values, dict) or not isinstance(clear, list):
        raise BadRequest("values must be an object and clear a list.")
    unknown = [key for key in [*values, *clear] if key not in _BY_KEY]
    if unknown:
        raise BadRequest(f"Unknown setting: {unknown[0]}.")
    with _lock:
        saved = _load()
        for key, raw in values.items():
            spec = _BY_KEY[key]
            text = str(raw if raw is not None else "").strip()
            if not text:
                if spec["type"] != "secret":
                    saved.pop(key, None)
                continue
            saved[key] = _validate(spec, text)
        for key in clear:
            saved.pop(key, None)
        _save(saved)
        apply()
    return public()


def _validate(spec: dict, text: str) -> str:
    label = spec["label"]
    if spec["type"] == "int":
        value = _number(text, spec)
        if value is None:
            raise BadRequest(f"{label} must be a whole number from 0 to {spec['max']}.")
        return str(value)
    if spec["type"] == "url":
        if not re.match(r"^https?://[^\s/]+(/\S*)?$", text):
            raise BadRequest(f"{label} must start with http:// or https://.")
        return text.rstrip("/")
    if spec["type"] == "hosts":
        items = [item.strip() for item in text.split(",") if item.strip()]
        if not items or not all(_HOST.match(item) for item in items):
            raise BadRequest(f"{label} must be comma-separated host names, such as ragflow.example.com.")
        return ",".join(items)
    if len(text) > 500:
        raise BadRequest(f"{label} is too long.")
    return text
