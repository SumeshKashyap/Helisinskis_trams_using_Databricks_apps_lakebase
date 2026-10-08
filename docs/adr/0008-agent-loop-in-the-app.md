# The agent runs as a tool-calling loop inside the app, on an open model, until a Supervisor Agent is available

Supersedes the agent-host part of ADR-0007. The tools stay as ADR-0007 describes (Unity Catalog functions, outside APIs through `http_request`).

On 2026-10-08 our workspace (Azure Germany West Central) refused Agent Bricks Supervisor Agents ("not available, contact sales"), even after the preview was enabled, and every Claude Foundation Model endpoint answered "rate limit of 0". Both point to an account-level gate, most likely Partner-powered AI features. The Databricks-hosted open models work and support tool calling.

So the Streamlit app runs the agent itself (`src/app/agent.py`). It sends the conversation and the tool definitions to a Foundation Model API endpoint (`/serving-endpoints/<name>/invocations`, default `databricks-gpt-oss-120b`, bundle variable `agent_endpoint`). It runs each requested tool as a parameterised query of the matching UC function on the analytics warehouse, or asks the FR-12 Genie space, and sends the results back, for at most 6 rounds. The new "Bike, walk or wait" tab also calls `bike_walk_or_wait` directly from a form, with no model involved.

## Considered Options

- **Wait for the Supervisor Agent.** It blocks FR-15 on an account or sales conversation we don't control.
- **Mosaic AI Agent Framework**: an agent logged with MLflow and served on its own Model Serving endpoint. That endpoint costs money while provisioned, and it adds a deployment step the blog doesn't need, all to host a loop of about 100 lines.
- **The OpenAI client from `get_open_ai_client()`.** It is deprecated and needs `openai` and `httpx` in the app. The SDK's own `api_client` does the same call with no new dependency.

## Consequences

- The tools are still governed by Unity Catalog: the app's service principal needs EXECUTE on the functions, USE CONNECTION on the connections and READ on the secret scope (`scripts/grant_app_access.py`).
- Only the tool names in `agent.TOOLS` can run, and only with their declared arguments, passed as query parameters. The model never writes SQL.
- One prompt (`agent.SYSTEM_PROMPT`) serves both hosts. `scripts/setup_agents.py` can still create the Supervisor Agent once it is enabled; then the app could call that endpoint instead, and this ADR gets superseded.
- gpt-oss-120b is weaker than Claude at following tone rules. The choice still comes from `decide_trip`, so a weaker model can word the answer badly but can't change the advice. Switching to Claude is a one-line variable change once the account allows it.
