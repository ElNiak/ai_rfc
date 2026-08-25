"""One core, two frontends.

Every operation the plugin can perform lives in :mod:`ai_rfc_server.core`;
the MCP server (:mod:`ai_rfc_server.server`) and the ``arfc`` CLI
(:mod:`ai_rfc_server.cli`) are thin frontends over the same functions, so
the AI+MCP and AI+CLI experiment arms are capability-identical by
construction.
"""
