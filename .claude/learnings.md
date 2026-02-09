# Session Learnings

## Scope Control

- **Fix what you break, don't fix what you didn't**: When linters/formatters surface pre-existing issues, fix only what's needed to pass the new gates.
- **Tooling changes cascade — anticipate it**: Adding quality gates will surface existing issues. Run hooks immediately after setup to identify the full scope before presenting results.

## Decision-Making

- **Present coverage exclusions, don't silently decide**: When coverage fails due to untestable code, explain rationale and offer choices (omit modules vs lower threshold) rather than silently applying exclusions.
- **Refactoring for lint compliance — just do it**: When a lint rule requires refactoring, implement directly rather than asking how to refactor.

## Communication

- **Show hook output on first failure**: Show full output so the user sees what needs fixing. Don't summarize — specific error messages matter.
- **Verify after every change cycle**: Run the full validation suite after fixes, not just the specific tool that failed.

## Research & Tools

- **Verify versions independently**: AI-powered docs (deepwiki) can return outdated info. Cross-check with `pip index versions` or GitHub releases.
- **Use deepwiki when asked**: When the user specifies a research tool preference, switch promptly.
- **Search for prior art before attempting fixes**: When hitting platform-specific behavior, search GitHub issues, related projects, and docs BEFORE writing code. User shouldn't need to point you to the right resources.

## Analysis vs Implementation

- **Match the mode to the ask**: When asked to analyze or review, stay in that mode. Don't pivot to code changes unless asked.
- **Read completely before analyzing**: Ensure full content before drawing conclusions. Truncated data leads to incomplete analysis.

## Debugging & Problem Escalation

- **Surface root causes, don't just fix symptoms**: When a task requires 3+ iteration attempts, step back and investigate the platform/framework layer before trying another application-level fix.
- **Diagnose before coding**: When behavior differs from expectation (e.g., Shift+Enter = Enter), write a minimal diagnostic FIRST to understand what the platform actually provides, then design the fix around observed reality.
- **Verify Win32 API details**: Always confirm which DLL exports a function (`GetAsyncKeyState` is in `user32.dll`, not `kernel32.dll`). Wrong DLL = runtime `AttributeError`.
- **Check tool-layer assumptions early**: The Read tool adds line-number prefixes not in the actual file. Verify real formats before building patterns around display artifacts.
