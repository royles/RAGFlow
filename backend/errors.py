"""Errors the API and the MCP tools both understand."""


class BadRequest(Exception):
    """The caller asked for something the service will not do."""


class NotFound(Exception):
    """The knowledge base or document is not in the catalog."""
