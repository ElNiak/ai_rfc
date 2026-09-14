"""One core, two frontends.

Every operation the plugin can perform lives in :mod:`ai_rfc.server.core`;
the MCP server (:mod:`ai_rfc.server.server`) and the ``ai-rfc`` CLI's agent
verbs (:mod:`ai_rfc.agent`, mounted through :mod:`ai_rfc.cli`) are thin
frontends over the same functions, so the AI+MCP and AI+CLI experiment arms
are capability-identical by construction.

The CLI frontend used to live here too, as ``ai_rfc.server.cli`` — a second
argparse tree with its own hyphenated spelling of each verb. It was deleted
once the grouped verbs mounted into the one door: two parsers is what
"capability-identical by construction" was there to rule out.
"""
