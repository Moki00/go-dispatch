"""
Generates docs/architecture_diagram.png for Go-Dispatch.
"""
import os
from pathlib import Path

# Ensure Graphviz bin is discoverable on Windows
graphviz_bin = r"C:\Program Files\Graphviz\bin"
if os.path.exists(graphviz_bin) and graphviz_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] += os.pathsep + graphviz_bin

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import SNS
from diagrams.aws.ml import Bedrock
from diagrams.onprem.client import Users
from diagrams.programming.framework import Fastapi

# Ensure docs directory exists
docs_dir = Path(__file__).resolve().parent if Path(__file__).name != "generate_diagram.py" else Path("docs")
docs_dir.mkdir(exist_ok=True)

graph_attr = {
    "fontsize": "16",
    "bgcolor": "white",
    "pad": "0.5"
}

with Diagram(
    "Go-Dispatch - Autonomous Zero-Distraction Triage Architecture",
    show=False,
    filename="architecture_diagram",
    outformat="png",
    graph_attr=graph_attr,
    direction="LR"
):
    sources = Users("Inbound Telemetry\n(SNMP / Webhooks / SMS)")

    with Cluster("AWS Cloud Environment"):
        api_server = Fastapi("Ingestion API\n(FastAPI / ECS / Lambda)")

        with Cluster("Amazon Bedrock AgentCore Runtime"):
            agent_loop = Bedrock("Strands Agent Loop\n(Claude 3.5 Sonnet)")

        with Cluster("Data & Knowledge Tier"):
            kb = Bedrock("Bedrock Knowledge Base\n(Client Runbooks & Topology)")
            db = Dynamodb("DynamoDB\n(Ticket & SLA State)")

        alerts = SNS("Amazon SNS\n(Critical SMS & Push Dispatch)")

    technician = Users("Field Technician\n(Mobile Mobilization)")

    # Data Flow
    sources >> Edge(label="Event Payload") >> api_server
    api_server >> Edge(label="Invoke Orchestrator") >> agent_loop

    # Bidirectional lookups
    agent_loop >> Edge(label="Query Topology / SLA") >> kb
    agent_loop >> Edge(label="Update Ticket (Quiet Mode)") >> db

    # Critical Escalation
    agent_loop >> Edge(color="firebrick", style="bold", label="Tier 4 Outage Alert") >> alerts
    alerts >> Edge(color="firebrick", style="bold", label="Instant Dossier") >> technician