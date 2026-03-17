# Devbox setup and file persistence

Covers the devbox provisioning flow: starting setup, uploading files via SSH/SCP, finishing setup (snapshotting), and verifying that uploaded files persist in the snapshot. Relates to `server/druids_server/api/routes/setup.py` (setup start/finish endpoints), `server/druids_server/lib/sandbox/` (sandbox provisioning and snapshotting), and the devbox model in `server/druids_server/db/models/devbox.py`.

## Setup

Start the server. This assumes a running Postgres database and a configured Morph API key.

```bash
cd /home/ubuntu/code/druids/server && uv run uvicorn druids_server.app:app --host 0.0.0.0 --port 8002 &
```

Wait for the server to be ready.

```bash timeout=30
curl -s --retry 5 --retry-connrefused https://me.uzpg.me/api/devboxes | python3 -c "import sys,json; print('Server ready')"
```

Expected: prints `Server ready`.

## Start devbox setup

POST to the setup/start endpoint to provision a new sandbox. This returns SSH credentials for accessing the sandbox.

```bash timeout=60
SETUP=$(curl -s -X POST https://me.uzpg.me/api/devbox/setup/start \
  -H "Content-Type: application/json" \
  -d '{"name": "regimen-devbox"}')
echo "$SETUP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'instance_id' in d, f'No instance_id in response: {d}'
assert 'ssh' in d, f'No ssh in response: {d}'
ssh = d['ssh']
assert ssh.get('host'), 'No SSH host'
assert ssh.get('private_key'), 'No SSH private key'
print(f'PASS: devbox provisioned, instance={d[\"instance_id\"]}')

# Save SSH key for later steps
with open('/tmp/regimen-devbox-key', 'w') as f:
    f.write(ssh['private_key'])
import os
os.chmod('/tmp/regimen-devbox-key', 0o600)
print(f'SSH_USER={ssh[\"username\"]}')
print(f'SSH_HOST={ssh[\"host\"]}')
"
SSH_USER=$(echo "$SETUP" | python3 -c "import sys,json; print(json.load(sys.stdin)['ssh']['username'])")
SSH_HOST=$(echo "$SETUP" | python3 -c "import sys,json; print(json.load(sys.stdin)['ssh']['host'])")
```

Expected: prints `PASS: devbox provisioned, instance=<id>` followed by SSH_USER and SSH_HOST values.

## Create a file via SSH

SSH into the sandbox and create a test file. This verifies SSH access works.

```bash timeout=15
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -i /tmp/regimen-devbox-key $SSH_USER@$SSH_HOST \
  "echo 'hello from devbox setup' > /home/agent/test-file.txt && cat /home/agent/test-file.txt" 2>/dev/null
```

Expected: prints `hello from devbox setup`.

## Upload a file via SCP

Create a local file and upload it to the sandbox via SCP. This verifies file transfer works.

```bash timeout=15
echo "uploaded via scp" > /tmp/regimen-upload.txt
scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -i /tmp/regimen-devbox-key \
  /tmp/regimen-upload.txt $SSH_USER@$SSH_HOST:/home/agent/uploaded.txt 2>/dev/null
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -i /tmp/regimen-devbox-key $SSH_USER@$SSH_HOST \
  "cat /home/agent/uploaded.txt" 2>/dev/null
```

Expected: prints `uploaded via scp`.

## Finish devbox setup

Snapshot the sandbox. This creates a reusable devbox that preserves all uploaded files.

```bash timeout=120
FINISH=$(curl -s -X POST https://me.uzpg.me/api/devbox/setup/finish \
  -H "Content-Type: application/json" \
  -d '{"name": "regimen-devbox"}')
echo "$FINISH" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'snapshot_id' in d, f'No snapshot_id: {d}'
print(f'PASS: snapshot created: {d[\"snapshot_id\"]}')
"
```

Expected: prints `PASS: snapshot created: <snapshot_id>`.

## Devbox appears in listing

The newly created devbox should show as ready in the devbox list.

```bash timeout=10
curl -s https://me.uzpg.me/api/devboxes | python3 -c "
import sys, json
data = json.load(sys.stdin)
found = [d for d in data['devboxes'] if d['name'] == 'regimen-devbox']
assert found, f'regimen-devbox not in list: {[d[\"name\"] for d in data[\"devboxes\"]]}'
assert found[0]['ready'], 'Devbox not ready'
assert found[0]['snapshot_id'], 'No snapshot_id'
print(f'PASS: regimen-devbox is ready with snapshot {found[0][\"snapshot_id\"]}')
"
```

Expected: prints `PASS: regimen-devbox is ready with snapshot <id>`.

## Files persist in snapshot

Create an execution using the new devbox. The agent should be able to read the files that were uploaded during setup, proving they survived the snapshot.

```bash timeout=120
SLUG=$(curl -s -X POST https://me.uzpg.me/api/executions \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
src = '''
async def program(ctx, spec=\"\", **kwargs):
    agent = await ctx.agent(\"checker\", prompt=\"Read /home/agent/test-file.txt and /home/agent/uploaded.txt using the Read tool. Report their exact contents.\")
    await ctx.wait()
'''
print(json.dumps({'devbox_name': 'regimen-devbox', 'program_source': src, 'args': {'spec': 'verify persistence'}}))")" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['execution_slug'])")
echo "Execution: $SLUG"

# Poll until the agent reads the files
for i in $(seq 1 30); do
  ACTIVITY=$(curl -s "https://me.uzpg.me/api/executions/$SLUG/activity?n=100&compact=false" 2>/dev/null)
  RESULT=$(echo "$ACTIVITY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
found_test = False
found_upload = False
for e in data.get('recent_activity', []):
    r = str(e.get('result', ''))
    if 'hello from devbox setup' in r:
        found_test = True
    if 'uploaded via scp' in r:
        found_upload = True
if found_test and found_upload:
    print('PASS: both files found in snapshot')
    exit(0)
exit(1)
" 2>/dev/null)
  if [ $? -eq 0 ]; then
    echo "$RESULT"
    break
  fi
done
```

Expected: prints `PASS: both files found in snapshot`. The agent reads both files from the snapshotted VM, confirming that files uploaded during setup persist through the snapshot/restore cycle.

## Cleanup

```bash
rm -f /tmp/regimen-devbox-key /tmp/regimen-upload.txt
pkill -f "uvicorn druids_server" 2>/dev/null
```
