# Session Learnings

## Scope Control

- **Fix what you break, don't fix what you didn't**: When adding linters/formatters that surface pre-existing issues, fix only what's needed to pass the new gates. Don't refactor beyond what the tool requires. In this session, ruff and ty surfaced issues — fixing them was necessary, not scope creep.
- **Tooling changes cascade — anticipate it**: Adding quality gates (ruff, ty, coverage) will surface existing code issues. Run hooks immediately after setup to identify the full scope of required fixes before presenting results.

## Decision-Making

- **Present coverage exclusions, don't silently decide**: When coverage thresholds fail due to untestable code (TUI, CLI entry points), the decision to omit modules should be explained with rationale, not silently applied. In this session, omitting app.py/widgets.py was explained but implemented without asking — could have offered the choice between omitting modules vs lowering the threshold.
- **Refactoring for lint compliance — just do it**: When a lint rule (complexity, line length) requires refactoring, implement the fix directly rather than asking "how should I refactor this?" The user asked for the rule, not a discussion about refactoring strategies.

## Communication

- **Show hook output on first failure**: When pre-commit hooks fail, show the full output so the user sees what needs fixing. Don't summarize failures — the specific error messages matter.
- **Verify after every change cycle**: Always run the full validation suite (tests + hooks) after fixes, not just the specific tool that failed. Changes can cascade.

## Research & Tools

- **Verify versions independently**: AI-powered docs (deepwiki) can return outdated version numbers. Cross-check with `pip index versions` or GitHub releases pages.
- **Use deepwiki when asked**: When the user specifies a research tool preference, switch to it promptly rather than continuing with web searches.

## Analysis vs Implementation

- **Match the mode to the ask**: When asked to analyze, review, or discuss — stay in that mode. Don't pivot to suggesting code changes or improvements unless explicitly asked. Analysis tasks should produce insights, not action items.
- **Read completely before analyzing**: When examining logs or large files, ensure you have the full content before drawing conclusions. Truncated data leads to incomplete analysis.

## Debugging & Problem Escalation

- **Surface root causes, don't just fix symptoms**: When a task requires increasingly complex workarounds (3+ iterations), step back and ask whether the underlying design is the problem. Flag it to the user as a potential improvement opportunity rather than silently building more elaborate fixes.
- **Check tool-layer assumptions early**: The Read tool adds line-number prefixes that aren't in the actual file. When writing parsers for file formats, verify the real format with a quick `repr()` check before building regex patterns around display artifacts.
