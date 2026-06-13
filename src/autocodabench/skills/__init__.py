"""Packaged skills — the versioned behavioral contracts for each agent phase.

Each ``<name>/SKILL.md`` is loaded by :mod:`autocodabench.agent.prompts`
(and symlinked into ``.claude/skills/`` for Claude Code surfaces by the
experiment harness). The sibling READMEs document provenance.

This ``__init__`` exists so ``importlib.resources.files()`` resolves the
directory as a regular package (a concrete path, not a MultiplexedPath).
"""
