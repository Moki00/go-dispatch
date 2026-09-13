# Go-Dispatch 🚀

### Automated Zero-Distraction Triage & Mobilization Agent

> Built for the **AWS Agents for Humans Hackathon** (Track: Professional Agents)

**Go-Dispatch** is an autonomous operational IT agent built with the **Strands Agents SDK** and deployed via **Amazon Bedrock AgentCore**. It runs silently in the background—auto-resolving transient network alarms, drafting context-rich customer replies from runbooks, and proactively monitoring SLA deadlines. It cuts through noise to mobilize the field technician _only_ when physical dispatch or high-stakes authorization is required.

---

## 🏗️ Architecture
```

[Inbound Webhook / Ticket / Ping / SMS]
         │
         ▼
[Amazon Bedrock AgentCore]
         │
┌────────┴──────────────────────────┐
▼                                   ▼
[Strands Agent Loop] ◄──► [Bedrock Knowledge Bases]
(Claude 4.5 Sonnet) (Client Runbooks & Topology)
│
├── Tier 1: Auto-resolve & verify (Silent)
├── Tier 2: Async draft & queue (Silent)
├── Tier 3: SLA warning alert (Push)
└── Tier 4: Hardware / Site Outage (Immediate Dispatch via SNS)

````

---

## ⚡ Features

- **Zero-Distraction Background Triage:** Absorbs transient alerts and auto-verifies service restores without pinging the technician.
- **Runbook-Aware Context Engine:** Leverages Bedrock Knowledge Bases to query customer network topology, IP schemas, and SLA agreements dynamically.
- **Autonomous SLA Defense:** Tracks countdown timers on active tickets and generates ready-to-send responses before contractual breach.
- **Sub-Minute Field Dossier Generation:** Compiles site addresses, gateway IPs, equipment serials, and failure state telemetry instantly upon site-down detection.
- **Human-in-the-Loop Interrupts:** Integrates native Strands interruption hooks before triggering physical dispatches or billable service orders.

---

## 🛠️ Tech Stack

- **Agent Framework:** [Strands Agents SDK](https://github.com/aws/strands-agents)
- **Foundational LLM:** Amazon Bedrock (Anthropic Claude 4.5 Sonnet)
- **Deployment Runtime:** Amazon Bedrock AgentCore
- **Knowledge Base & Vector Store:** Amazon Bedrock Knowledge Bases + Amazon DynamoDB
- **Notifications & Alerting:** Amazon SNS
- **Backend Service:** Python 3.11+, FastAPI, Boto3

---

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- AWS Account with Bedrock model access enabled (Claude Sonnet 4.5, via a cross-region inference profile)
- AWS CLI configured locally (`aws configure`)

### Installation

1. **Clone the repository:**
```bash
git clone [https://github.com/Moki00/go-dispatch.git](https://github.com/Moki00/go-dispatch.git)
cd go-dispatch

```

2. **Create and activate a virtual environment:**

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

```

3. **Install dependencies:**

```bash
pip install -r requirements.txt

```

4. **Configure environment variables:**

```bash
cp .env.example .env

```

Populate your `.env` with your AWS region, Bedrock Knowledge Base IDs, and notification endpoints. 

5. **Run the interactive demo (CLI):**

> On Windows, set `PYTHONIOENCODING=utf-8` first, so the Rich panels and emoji render instead of crashing on cp1252.

```bash
# Windows PowerShell
$env:PYTHONIOENCODING = "utf-8"

# Autonomous 4-tier triage harness (agent runs hands-off)
python -m src.main --cli

# Same harness, but pause for human approval before any physical dispatch (Tier 3/4)
python -m src.main --cli --hitl
```

6. **Or run the API server:**

```bash
python -m src.main            # FastAPI on http://localhost:8000  (Swagger UI at /docs)
```

See **[DEMO.md](DEMO.md)** for a full presenter runbook and pitch script.

---

## 📂 Project Structure

```text
go-dispatch/
├── docs/
│   └── architecture_diagram.png
├── src/
│   ├── __init__.py
│   ├── config.py             # Pydantic settings & AWS environment loading
│   ├── main.py               # FastAPI webhook listener & CLI demo harness (--cli / --hitl)
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── core.py           # Strands Agent initialization & system prompts
│   │   ├── approval.py       # Human-in-the-Loop dispatch approval hook (Strands BeforeToolCall)
│   │   └── tools.py          # Strands @tool definitions (diagnostics, KB, SNS dispatch)
│   ├── db/
│   │   ├── __init__.py
│   │   └── dynamodb.py       # Ticket state tracking & client SLA metadata
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── sla_monitor.py    # Autonomous SLA countdown daemon
│   └── knowledge/
│       ├── __init__.py
│       └── kb_retriever.py   # Amazon Bedrock Knowledge Bases integration
├── scripts/
│   ├── aws_probe.py          # Read-only check of AWS creds / tables / topic
│   └── provision_aws.py      # Idempotent DynamoDB + SNS provisioning
├── infra/
│   ├── iam-policy-go-dispatch.json   # Least-privilege IAM policy for DynamoDB + SNS
│   └── README.md             # Real persistence & paging setup steps
├── tests/
│   ├── __init__.py
│   ├── test_tools.py
│   └── test_agent_flow.py
├── .env.example
├── .gitignore
├── DEMO.md                   # Presenter runbook & pitch script
├── LICENSE
├── README.md
└── requirements.txt

```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](https://github.com/Moki00/go-dispatch/blob/main/LICENSE) file for details.
