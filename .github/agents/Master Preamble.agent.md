---
name: Master Preamble
description: Describe what this custom agent does and when to use it.
argument-hint: The inputs this agent expects, e.g., "a task to implement" or "a question to answer".
# tools: ['vscode', 'execute', 'read', 'agent', 'edit', 'search', 'web', 'todo'] # specify the tools this agent can use. If not set, all enabled tools are allowed.
---

<!-- Tip: Use /create-agent in chat to generate content with agent assistance -->

You MUST follow PRODUCTION_ROADMAP.md as the authoritative execution contract.

Before implementing anything, you MUST perform a Roadmap Alignment Check.

Current roadmap state:
- Phase: Immediate Execution
- Sprint: Sprint 1
- Active objectives:
  1. Execution Stabilization
  2. Narrator Raw Leak Reduction

This means the current sprint is ONLY allowed to improve:
- execution timeout/date-type stability
- narrator raw output quality and sanitizer dependency

You are NOT allowed to:
- redesign semantic architecture
- redesign retrieval ranking
- redesign policy engine
- redesign evaluation system
- redesign rollout governance
- perform broad observability platform work
- refactor unrelated modules “while you are here”

STRICT RULES:
1. QueryPlan-first architecture must remain intact.
2. Validation -> compiler -> executor separation must remain intact.
3. No direct prompt-to-SQL changes.
4. No scope creep outside Sprint 1.
5. If you detect adjacent issues, list them under "Out-of-Scope Recommendations" and DO NOT implement them.

Before coding, output this section:

## Roadmap Alignment Check
1. Which roadmap section/program does this task belong to?
2. Why is this task valid in Sprint 1?
3. Which modules are in scope?
4. Which modules are explicitly out of scope?
5. What scope-creep risks exist?

Then proceed only if the task is aligned.