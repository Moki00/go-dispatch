"""Go-Dispatch: one-shot AWS provisioning for real persistence + paging.

Creates (idempotently):
  * DynamoDB table  GoDispatch_Tickets   (PK: ticket_id, on-demand billing)
  * DynamoDB table  GoDispatch_Clients   (PK: client_id, on-demand billing)
  * SNS topic       GoDispatch_Alerts    (returns existing ARN if already there)

Then patches the local .env so SNS_DISPATCH_TOPIC_ARN points at the new topic,
so escalate_to_technician actually pages.

Prerequisite: the IAM user/role must have the permissions in
infra/iam-policy-go-dispatch.json attached. Without them every call below
returns AccessDenied and nothing is created.

Usage:
    venv/Scripts/python -m scripts.provision_aws
    venv/Scripts/python -m scripts.provision_aws --email you@example.com
    venv/Scripts/python -m scripts.provision_aws --no-write-env
"""

import argparse
import re
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from src.config import get_settings

settings = get_settings()
REGION = settings.aws_region
TOPIC_NAME = "GoDispatch_Alerts"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def log(msg):
    print(msg, flush=True)


def create_table(ddb, name, key_attr):
    """Create an on-demand table keyed on ``key_attr`` (string). Idempotent."""
    try:
        ddb.describe_table(TableName=name)
        log(f"[DDB] '{name}' already exists — skipping.")
        return
    except ddb.exceptions.ResourceNotFoundException:
        pass  # fall through to create
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        log(f"[DDB] '{name}' describe failed ({code}); attempting create anyway.")

    log(f"[DDB] Creating '{name}' (PK={key_attr}, PAY_PER_REQUEST)...")
    ddb.create_table(
        TableName=name,
        AttributeDefinitions=[{"AttributeName": key_attr, "AttributeType": "S"}],
        KeySchema=[{"AttributeName": key_attr, "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.get_waiter("table_exists").wait(TableName=name)
    log(f"[DDB] '{name}' is ACTIVE.")


def create_topic(sns):
    """CreateTopic is idempotent: returns the ARN whether new or pre-existing."""
    log(f"[SNS] Ensuring topic '{TOPIC_NAME}'...")
    arn = sns.create_topic(Name=TOPIC_NAME)["TopicArn"]
    log(f"[SNS] Topic ARN: {arn}")
    return arn


def subscribe_email(sns, arn, email):
    log(f"[SNS] Subscribing {email} (email) to {TOPIC_NAME}...")
    sns.subscribe(TopicArn=arn, Protocol="email", Endpoint=email, ReturnSubscriptionArn=True)
    log(f"[SNS] Confirmation email sent to {email} — click the link to activate paging.")


def patch_env(arn):
    if not ENV_PATH.exists():
        log(f"[ENV] {ENV_PATH} not found — set SNS_DISPATCH_TOPIC_ARN={arn} manually.")
        return
    text = ENV_PATH.read_text(encoding="utf-8")
    if re.search(r"^SNS_DISPATCH_TOPIC_ARN=", text, flags=re.MULTILINE):
        text = re.sub(
            r"^SNS_DISPATCH_TOPIC_ARN=.*$",
            f"SNS_DISPATCH_TOPIC_ARN={arn}",
            text,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip("\n") + f"\nSNS_DISPATCH_TOPIC_ARN={arn}\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    log(f"[ENV] Patched {ENV_PATH.name}: SNS_DISPATCH_TOPIC_ARN={arn}")


def main():
    parser = argparse.ArgumentParser(description="Provision Go-Dispatch AWS resources.")
    parser.add_argument("--email", help="Email address to subscribe for real dispatch paging.")
    parser.add_argument("--no-write-env", action="store_true", help="Do not patch .env with the topic ARN.")
    args = parser.parse_args()

    log(f"== Go-Dispatch provisioning (region={REGION}, account via STS) ==")
    try:
        ident = boto3.client("sts", region_name=REGION).get_caller_identity()
        log(f"[STS] Acting as {ident['Arn']}")
    except ClientError as e:
        log(f"[STS] Could not resolve identity: {e}")

    ddb = boto3.client("dynamodb", region_name=REGION)
    sns = boto3.client("sns", region_name=REGION)

    try:
        create_table(ddb, settings.dynamodb_tickets_table, "ticket_id")
        create_table(ddb, settings.dynamodb_clients_table, "client_id")
        arn = create_topic(sns)
        if args.email:
            subscribe_email(sns, arn, args.email)
        if not args.no_write_env:
            patch_env(arn)
        log("\n[OK] Provisioning complete. Restart the CLI/server to pick up the new .env.")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        msg = e.response.get("Error", {}).get("Message", str(e))
        log(f"\n[BLOCKED] {code}: {msg}")
        log("→ Attach infra/iam-policy-go-dispatch.json to this IAM user, then re-run.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
