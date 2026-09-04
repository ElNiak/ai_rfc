"""GEPA optimization of the ai-rfc plugin's skill texts.

The four skills an agent reads during a reconstruction are the artifact under
optimization: a search backend proposes rewrites, the harness runs them as
offline campaigns, and the campaigns' own measurements are the score. Nothing
here is imported by the plugin or the substrate.
"""
