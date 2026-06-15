# Shopping Assistant — Databricks AI Application

An AI-powered shopping assistant built on Databricks, using Flask + LangGraph + RAG pipeline. Currently scoped to bags, wallets, and accessories.

---

## Architecture

```
Flask API  →  LangGraph 10-node DAG  →  Databricks Vector Search  →  Response
                    │
                    ├── Input Guardrail
                    ├── Intent Classifier
                    ├── Personalisation
                    ├── Product Search
                    ├── Result Validator
                    ├── Constraint Relaxer
                    ├── Reranker
                    ├── Response Generator  (chat / product detail / search results)
                    ├── Output Guardrail
                    └── Clarification (conditional)
```

**LLM:** `databricks-meta-llama-3-1-8b-instruct`  
**Embeddings:** `databricks-bge-large-en` (1024-dim)  
**Vector Store:** Databricks Vector Search (primary) + Pinecone (fallback)  
**Auth:** Google OAuth 2.0 + JWT (15-min access + 7-day refresh tokens)

---

## Project Structure

```
.
├── app.py                  # Flask entry point + API routes
├── app.yaml                # Databricks Apps configuration
├── databricks.yml          # Asset Bundles (dev / test / prod targets)
├── requirements.txt
├── .env.example            # Required environment variables (copy to .env)
│
├── core/                   # Application logic
│   ├── workflow.py         # LangGraph DAG definition + all node methods
│   ├── nodes.py            # ResponseGenerator and supporting node classes
│   ├── models.py           # Pydantic models (GraphState, SearchPreferences, etc.)
│   ├── guardrails.py       # Input + output guardrail logic
│   ├── memory_manager.py   # Conversation memory + auto-summarisation
│   ├── pref_intent_normalizer.py  # Intent classification + preference extraction
│   ├── prompt_loader.py    # Loads prompt templates from core/prompts/
│   ├── semantic_cache.py   # Cosine-similarity semantic cache (≥0.95 threshold)
│   ├── observability.py    # Request traces and metrics store
│   ├── performance.py      # Per-node latency tracking
│   ├── rag_utils.py        # RAG helper utilities
│   │
│   ├── auth/               # JWT + Google OAuth helpers
│   ├── evals/              # Evaluation runners and evaluators
│   ├── extended_tools/     # MCP tool integrations
│   ├── personalization/    # Personalisation engine (preference learning, Delta storage)
│   ├── prompts/            # Prompt templates (.txt files, one per LLM call)
│   └── vector_store/       # VectorStoreInterface + Databricks/Pinecone adapters
│
├── static/                 # Frontend (HTML + CSS + JS — no framework)
├── templates/              # Flask HTML templates (login page)
├── config/                 # YAML configuration files
│
├── tests/                  # All tests
│   └── personalization/    # Personalisation-specific test scenarios
│
├── scripts/                # Utility, setup, and deployment scripts
│   ├── deploy.ps1          # Databricks Asset Bundle deploy helper
│   ├── bundle-manager.ps1  # Bundle management utilities
│   └── setup_personalization_db.sql  # Creates personalisation Delta tables
│
└── docs/                   # Documentation and reference materials
    └── images/             # Architecture diagrams and screenshots
```

---

## Local Development Setup

### Prerequisites

- Python 3.11+
- Databricks CLI (`pip install databricks-cli`)
- Access to the Databricks workspace

### 1. Clone and install

```bash
git clone <repo-url>
cd shopping-assistant
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in all values
```

### 3. Run locally

```bash
python app.py
# App available at http://localhost:5000
```

---

## Deploying to Databricks

### Deploy to dev (each developer gets their own instance)

```bash
databricks bundle deploy --target dev
databricks bundle run shopping-assistant-app --target dev
```

### Deploy to test / prod

```bash
databricks bundle deploy --target test
databricks bundle deploy --target prod
```

---

## Developer Workflow

```
main  ←── protected, merged via PR only
  │
  ├── dev/<your-name>/<feature>   # your working branch
```

1. Create a branch from `main`
2. Develop in your Databricks Git Folder (linked to this repo)
3. Deploy to your personal `dev` target for live testing
4. Open a PR → review → merge to `main`
5. CI deploys `main` to `test`, then `prod`

---

## Key Configuration Files

| File | Purpose |
|---|---|
| `databricks.yml` | Asset Bundle config — targets, app config, resource definitions |
| `app.yaml` | Databricks Apps runtime config (entrypoint, permissions) |
| `config/environment_vars.yml` | Non-secret environment variable defaults |
| `config/vector_store_config.yml` | Vector store backend selection |
| `core/prompts/*.txt` | All LLM prompt templates — edit without touching code |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in all values. See `.env.example` for descriptions of each variable.

**Never commit `.env` to Git.** It is excluded by `.gitignore`.

---

## Running Tests

```bash
# All tests
python -m pytest tests/

# Specific suite
python -m pytest tests/test_guardrails.py
python -m pytest tests/personalization/
```
