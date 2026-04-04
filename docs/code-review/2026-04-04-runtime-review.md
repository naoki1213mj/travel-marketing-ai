# Code Review: Runtime Security and Performance

**Ready for Production**: No
**Critical Issues**: 1

## Priority 1 (Must Fix) ⛔

- Broken access control across conversations and approvals. Conversations are stored and queried under a shared `demo-user` partition, and API routes do not bind reads or mutations to a caller identity. This allows cross-session history disclosure and unauthorized refinement/approval/evaluation writes. See `src/conversations.py`, `src/api/conversations.py`, `src/api/chat.py`, and `src/api/evaluate.py`.

## Security Findings

- High: Cross-conversation IDOR and shared tenant partitioning leak prompts, artifacts, and approval links, and permit unauthorized mutations.
- Medium: Manager approval URLs trust `X-Forwarded-Host` / `X-Forwarded-Proto`, enabling header-based approval-link poisoning.
- Medium: API auth is insecure by default because `API_KEY` is optional and production readiness checks do not require any auth setting.

## Performance Findings

- High: `src/conversations.py` uses the synchronous Cosmos client from async request paths, blocking the event loop on save, read, list, and replay operations.
- High: Conversation persistence rewrites and upserts the full message/artifact document every turn, causing write amplification and growing restore payloads.
- Medium: The frontend polls full conversation documents every 5 seconds with cache-busting while manager/background updates are pending.
- Medium: `/api/evaluate` has no request size caps and executes multiple remote evaluators sequentially, then schedules another heavy Foundry evaluation path.

## Residual Risks / Gaps

- No request-size limits were found for chat/evaluation JSON bodies.
- I did not execute load tests or hostile API probes, so concurrency impact and exploitability are based on code inspection.
- The review focused on the runtime paths requested by the user and adjacent approval/history flows, not the full agent/tool stack.
