# AgentLoop unit cases

These tests cover the second-stage no-tool chat loop with a fake LLM and in-memory
session store.

- Return assistant text from a successful LLM call.
- Append user and assistant messages to the session in order.
- Pass loaded history into ContextBuilder before appending the current user input.
- Call the LLM through the provider protocol with `tools=None`.
- Preserve a complete interaction when the LLM raises by appending a user message
  and an assistant error marker.
