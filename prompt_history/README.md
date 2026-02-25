# Prompt History

Lightweight eval log for tracking prompt iterations and their observed performance.

## Structure

```
prompt_history/
  {agent-name}/
    {YYYY-MM-DD}-{revision-desc}-{commit-hash}.md
```

Each file contains:

1. **What changed** -- the reason for the revision, what problem it addresses.
2. **Observations** -- links to reviews, screenshots, notes on how the output looks, comparisons to previous versions. Updated over time as more runs happen.

## Agents

- `demoer` -- the demo/review agent that verifies PRs end-to-end (verify.py + REVIEW_SYSTEM_PROMPT in program_utils.py)
- `monitor` -- the Sonnet monitor that watches the demoer and gates review posting (MONITOR_PROMPT in verify.py)
