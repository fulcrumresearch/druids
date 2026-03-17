---
name: regimen-test
description: >
  Step through a regimen verification file, running each command and
  checking the output matches expectations.
user-invocable: true
---

# /regimen-test

Step through a `.regimen/*.md` file and verify each step passes.

## What to do

The user will specify which regimen file to test (or you can pick one from `.regimen/`).

For each step in the file:

1. Read the prose description of what should happen.
2. Run the bash commands shown.
3. Check whether the actual output matches what the prose describes.
4. If a command has `timeout=N` on the fence line, run it with that timeout. Don't use a bunch of sleeps with random times. 
5. If something fails, debug it. Try again. Only count it as a real failure if the feature itself is broken, not the environment or the instructions. If you notice something is out of date in the regimen file.

After all steps, report the result: which steps passed, which failed, and why.

## What "verify" means

External interaction with a running system: HTTP requests, CLI commands, database queries against a running server, log observation, file output inspection.

NOT: importing modules, constructing objects in a REPL, running grep to confirm structure, verifying types or signatures exist, reading source code to check implementation. Those are code reading, not verifying. Run the commands in the file and check the output.
