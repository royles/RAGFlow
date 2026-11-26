"""MCP tools for agents that need a grounded answer.

The tools call the same functions as the REST API. An agent can list the
knowledge bases, read the matching passages, or ask for an answer written
from those passages.

The endpoint is mounted at /mcp on the FastAPI application (Streamable HTTP).
"""

import os

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from backend import service
from backend.errors import BadRequest, NotFound

mcp = MCPServer(
    "RAGFlow",
    instructions=(
        "RAGFlow answers questions from knowledge bases the user has built. "
        "Call list_knowledge_bases, then ask_knowledge_base. "
        "The answer is grounded in retrieved passages. "
        "Use search_knowledge_base when you want the passages and will write the answer yourself."
    ),
)


def transport_security() -> TransportSecuritySettings:
    """Allow loopback, plus any hosts named in the environment.

    On Cloudera AI the public application hostname is assigned at deploy time
    and the process only binds to loopback. Host checking is turned off in
    that case unless RAGFLOW_MCP_ALLOWED_HOSTS is set.
    """
    extra = [item.strip() for item in os.environ.get("RAGFLOW_MCP_ALLOWED_HOSTS", "").split(",") if item.strip()]
    if os.environ.get("CDSW_APP_PORT", "").strip() and not extra:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"]
    origins = [
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "http://localhost",
        "http://localhost:*",
        "http://[::1]",
        "http://[::1]:*",
    ]
    for item in extra:
        hosts.append(item)
        if ":" not in item and not item.endswith(":*"):
            hosts.append(f"{item}:*")
        origins.extend([f"http://{item}", f"https://{item}", f"http://{item}:*", f"https://{item}:*"])
    master = os.environ.get("CDSW_MASTER_IP", "").strip()
    if master:
        hosts.extend([master, f"{master}:*"])
        origins.extend([f"http://{master}", f"http://{master}:*", f"https://{master}", f"https://{master}:*"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _guard(func):
    try:
        return func()
    except (BadRequest, NotFound) as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def list_knowledge_bases() -> dict:
    """List knowledge bases, including chunking, nearest-neighbour settings, and model ids."""
    return {"knowledge_bases": service.list_knowledge_bases()}


@mcp.tool()
def search_knowledge_base(knowledge_base: str, question: str, top_k: int = 5) -> dict:
    """Retrieve the passages most relevant to a question. Does not call a chat model.

    knowledge_base: knowledge base id, or its exact name when the name is unique.
    top_k: how many passages to return, from 1 to 20.
    """
    return _guard(lambda: service.search(knowledge_base, question, top_k))


@mcp.tool()
def ask_knowledge_base(knowledge_base: str, question: str, top_k: int = 5) -> dict:
    """Answer a question from one knowledge base, grounded in retrieved passages.

    When the knowledge base has a Bedrock chat model, that model writes the
    answer and is told to use only the passages. Otherwise the passages
    themselves are returned. knowledge_base is an id or a unique name.
    """
    return _guard(lambda: service.ask(knowledge_base, question, top_k))
