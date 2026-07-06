# biz-booking-agent

Simple ReAct agent
Agent generated with `agents-cli` version `1.0.0`

## Project Structure

```
biz-booking-agent/
├── app/         # Core agent code
│   ├── agent.py               # Main agent logic
│   ├── fast_api_app.py        # FastAPI Backend server
│   └── app_utils/             # App utilities and helpers
├── tests/                     # Unit, integration, and load tests
├── GEMINI.md                  # AI-assisted development guide
└── pyproject.toml             # Project dependencies
```

> 💡 **Tip:** Use [Antigravity CLI](https://antigravity.google/) for AI-assisted development - project context is pre-configured in `GEMINI.md`.

## Requirements

Before you begin, ensure you have:
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)


## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
agents-cli install
```

Test the agent with a local web server:

```bash
agents-cli playground
```

You can also use features from the [ADK](https://adk.dev/) CLI with `uv run adk`.

## Commands

| Command              | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                                                         |
| `agents-cli playground` | Launch local development environment                                                  |
| `agents-cli lint`    | Run code quality checks                                                               |
| `agents-cli eval`    | Evaluate agent behavior (generate, grade, analyze, and more — see `agents-cli eval --help`) |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests                                                        |
| `agents-cli deploy`  | Deploy agent to Agent Runtime                                                                |
| `agents-cli publish gemini-enterprise` | Register deployed agent to Gemini Enterprise                    || [A2A Inspector](https://github.com/a2aproject/a2a-inspector) | Launch A2A Protocol Inspector                                                        |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

---

## Development

Edit your agent logic in `app/agent.py` and test with `agents-cli playground` - it auto-reloads on save.

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

To add CI/CD and Terraform, run `agents-cli scaffold enhance`.
To set up your production infrastructure, run `agents-cli infra cicd`.

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging.

## A2A Inspector

This agent supports the [A2A Protocol](https://a2a-protocol.org/). Use the [A2A Inspector](https://github.com/a2aproject/a2a-inspector) to test interoperability.
See the [A2A Inspector docs](https://github.com/a2aproject/a2a-inspector) for details.

## 📱 WhatsApp Webhook Integration

This agent features a built-in Meta WhatsApp Cloud API Webhook to automate clinical bookings directly via WhatsApp chats.

### Webhook Configuration Properties

Set the following variables in your local `.env` file:
```env
# Verification handshake secret (arbitrary string matching Meta configuration)
WHATSAPP_VERIFY_TOKEN=your_secure_verify_token_123

# Meta Graph API configuration (for outbound message replies)
WHATSAPP_API_TOKEN=your_meta_graph_api_access_token
WHATSAPP_PHONE_NUMBER_ID=your_sender_phone_number_id
```

### Local Webhook Verification & Testing

#### 1. Handshake Verification (GET /webhook)
Simulate Meta's verification webhook handshake:
```bash
curl "http://localhost:8000/webhook?hub.mode=subscribe&hub.verify_token=your_secure_verify_token_123&hub.challenge=987654321"
```
*Expected Output:* `987654321` (plain text challenge value).

#### 2. Send Mock WhatsApp Chat Notification (POST /webhook)
Simulate an incoming text message from a customer:
```bash
curl -X POST "http://localhost:8000/webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [
      {
        "id": "12345",
        "changes": [
          {
            "value": {
              "messaging_product": "whatsapp",
              "messages": [
                {
                  "from": "923001234567",
                  "id": "wamid.abc123xyz",
                  "timestamp": "1665096238",
                  "text": {
                    "body": "I want to book an appointment for teeth cleaning next Monday"
                  },
                  "type": "text"
                }
              ]
            },
            "field": "messages"
          }
        ]
      }
    ]
  }'
```
*Expected Output:*
- Backend logs show session creation mapping to the phone number `wa-923001234567`.
- The booking agent processes the request, routing it correctly.
- If Graph credentials are set, it automatically replies back to the sender's phone.

---

## 🖼️ Assets

### Cover Banner
![SmartDentist Cover Banner](assets/cover_page_banner.png)

### Agent Workflow Architecture Diagram
![SmartDentist Architecture Diagram](assets/architecture_diagram.png)

