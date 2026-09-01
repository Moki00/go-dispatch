### Full Repository README.md Template

After initialization, you can replace the initial `README.md` with this structure:

```markdown
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
┌────────┴────────┐
▼ ▼
[Strands Agent Loop] ◄──► [Bedrock Knowledge Bases]
(Claude 3.5 Sonnet) (Client Runbooks & Topology)
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
- **Foundational LLM:** Amazon Bedrock (Anthropic Claude 3.5 Sonnet / Haiku)
- **Deployment Runtime:** Amazon Bedrock AgentCore
- **Knowledge Base & Vector Store:** Amazon Bedrock Knowledge Bases + Amazon DynamoDB
- **Notifications & Alerting:** Amazon SNS
- **Backend Service:** Python 3.11+, FastAPI, Boto3

---

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- AWS Account with Bedrock model access enabled (Claude 3.5 Sonnet)
- AWS CLI configured locally (`aws configure`)

### Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/Moki00/go-dispatch.git](https://github.com/Moki00/go-dispatch.git)
   cd go-dispatch

````

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

Populate your `.env` with your AWS region, Bedrock Knowledge Base IDs, and notification endpoints. 5. **Run the agent locally:**

```bash
python -m src.main

```

---

## 📂 Project Structure

```text
go-dispatch/
├── docs/
│   └── architecture_diagram.png
├── src/
│   ├── __init__.py
│   ├── config.py             # Pydantic settings & AWS environment loading
│   ├── main.py               # FastAPI webhook listener & CLI entrypoint
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── core.py           # Strands Agent initialization & system prompts
│   │   └── tools.py          # Strands @tool definitions (diagnostics, KB, SNS dispatch)
│   ├── db/
│   │   ├── __init__.py
│   │   └── dynamodb.py       # Ticket state tracking & client SLA metadata
│   └── knowledge/
│       ├── __init__.py
│       └── kb_retriever.py   # Amazon Bedrock Knowledge Bases integration
├── tests/
│   ├── __init__.py
│   ├── test_tools.py
│   └── test_agent_flow.py
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt

```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details.
