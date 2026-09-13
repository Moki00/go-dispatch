"""Read-only AWS reconnaissance for Go-Dispatch provisioning.

Checks caller identity and whether the DynamoDB tables + SNS topic already
exist in the configured region. Makes NO mutating calls.
"""

import boto3
from botocore.exceptions import ClientError

from src.config import get_settings

settings = get_settings()
REGION = settings.aws_region


def line(msg):
    print(msg, flush=True)


def main():
    line(f"== Go-Dispatch AWS probe (region={REGION}) ==")

    # 1. Who am I?
    try:
        ident = boto3.client("sts", region_name=REGION).get_caller_identity()
        line(f"[STS] Account={ident['Account']}  Arn={ident['Arn']}")
    except ClientError as e:
        line(f"[STS] ERROR: {e}")

    # 2. DynamoDB tables
    ddb = boto3.client("dynamodb", region_name=REGION)
    for tbl in (settings.dynamodb_tickets_table, settings.dynamodb_clients_table):
        try:
            desc = ddb.describe_table(TableName=tbl)["Table"]
            line(f"[DDB] '{tbl}' EXISTS  status={desc['TableStatus']}  items={desc.get('ItemCount', '?')}")
        except ddb.exceptions.ResourceNotFoundException:
            line(f"[DDB] '{tbl}' MISSING (needs creation)")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            line(f"[DDB] '{tbl}' check FAILED: {code} — {e.response.get('Error', {}).get('Message', '')}")

    # 3. SNS topics
    sns = boto3.client("sns", region_name=REGION)
    configured = settings.sns_dispatch_topic_arn
    line(f"[SNS] SNS_DISPATCH_TOPIC_ARN configured = {configured!r}")
    try:
        topics = []
        paginator = sns.get_paginator("list_topics")
        for page in paginator.paginate():
            topics.extend(page.get("Topics", []))
        dispatch_topics = [t["TopicArn"] for t in topics if "Dispatch" in t["TopicArn"] or "GoDispatch" in t["TopicArn"]]
        line(f"[SNS] total topics visible: {len(topics)}")
        if dispatch_topics:
            for arn in dispatch_topics:
                line(f"[SNS]   candidate: {arn}")
        else:
            line("[SNS]   no GoDispatch/Dispatch topic found (needs creation)")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        line(f"[SNS] list_topics FAILED: {code} — {e.response.get('Error', {}).get('Message', '')}")


if __name__ == "__main__":
    main()
