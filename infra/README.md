# Go-Dispatch — Real AWS Persistence & Paging

Bedrock (the agent brain) works with the current credentials. To make the two
**degraded-mode** paths real as well, you need DynamoDB tables (ticket
persistence) and an SNS topic (technician paging). Both are currently blocked
because the IAM user `ashvinkumar287@gmail.com` has **no** DynamoDB or SNS
permissions.

## Step 1 — Attach the IAM policy (admin action, one time)

The access keys came from an admin account, so an admin must attach
[`iam-policy-go-dispatch.json`](./iam-policy-go-dispatch.json) to the user. It is
least-privilege: DynamoDB CRUD scoped to the two `GoDispatch_*` tables, and SNS
scoped to the single `GoDispatch_Alerts` topic.

**Console:** IAM → Users → `ashvinkumar287@gmail.com` → *Add permissions* →
*Create inline policy* → *JSON* tab → paste the file → name it `GoDispatchOps` → *Create*.

**CLI (if the admin has one configured):**

```bash
aws iam put-user-policy \
  --user-name ashvinkumar287@gmail.com \
  --policy-name GoDispatchOps \
  --policy-document file://infra/iam-policy-go-dispatch.json
```

## Step 2 — Provision the resources (one command)

```bash
# create both tables + the SNS topic, and patch .env with the topic ARN
venv/Scripts/python -m scripts.provision_aws

# optional: also subscribe your email so a real page is delivered
venv/Scripts/python -m scripts.provision_aws --email you@example.com
```

The script is idempotent (safe to re-run) and prints `[BLOCKED]` with the exact
missing permission if Step 1 hasn't taken effect yet.

## Step 3 — Verify it's real

```bash
venv/Scripts/python -m scripts.aws_probe        # tables EXIST, topic candidate listed
printf "4\nq\n" | PYTHONIOENCODING=utf-8 venv/Scripts/python -m src.main --cli
```

On a Tier 4 run you should now see the ticket persist (no `dynamodb:UpdateItem`
AccessDenied warning) and — if you subscribed and confirmed an email — an actual
dispatch page, with the tool reporting **"Paged via Amazon SNS"** instead of
**"NO PAGE SENT"**.

## What gets created & cost

| Resource | Name | Billing |
|----------|------|---------|
| DynamoDB table | `GoDispatch_Tickets` | On-demand (PAY_PER_REQUEST) — no idle cost |
| DynamoDB table | `GoDispatch_Clients` | On-demand — no idle cost |
| SNS topic | `GoDispatch_Alerts` | Free until you publish; email delivery is free |

Everything is in `eu-north-1` (matching `.env`), tears down cleanly, and stays
within AWS free-tier usage for a demo.
