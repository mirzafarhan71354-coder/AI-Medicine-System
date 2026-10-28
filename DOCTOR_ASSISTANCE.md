# Doctor Assistance

Doctor Assistance uses an OpenAI-compatible chat-completions API. Configure the API on the server; do not put credentials in browser code or commit them:

- `CAREPOINT_AI_API_KEY` — API key for your chosen provider (required to enable responses).
- `CAREPOINT_AI_BASE_URL` — API base URL; defaults to `https://api.openai.com/v1`.
- `CAREPOINT_AI_MODEL` — model identifier; defaults to `gpt-4o-mini`.

The configured provider must support `POST /chat/completions` with the OpenAI chat-completions request and response format. Restart the application after setting the environment variables. Without an API key the Doctor Assistance interface is available, but chat returns a configuration error rather than simulated or fabricated AI responses.

If a request fails, the chat reports common provider configuration errors: HTTP 401/403 indicates a rejected API key, 404 indicates an invalid model or base URL, 429 indicates a rate limit or usage quota, and 5xx indicates a temporary provider outage. For a 429, check the provider account's billing status, remaining credits, and request limits; code changes cannot add provider credits or bypass those limits. For connection failures, check that the server can reach the configured provider.

The doctor names and specialties are the user-provided directory entries; the application does not verify credentials or availability. The chatbot is an AI medical-information assistant, not a doctor, and does not claim that a named doctor reviewed or endorsed its answers. It is instructed to stay within health-related topics, provide general information rather than diagnoses or prescriptions, and direct potentially urgent cases to emergency services. Emergency phrase detection provides immediate guidance without calling the AI provider; it is not a comprehensive medical triage system.

Chat history is held in the user's browser and sent with each request to the configured AI provider to support follow-up questions. The application does not store chatbot conversations in its database. Avoid entering identifying or sensitive personal information, and configure the AI provider according to your privacy and compliance requirements.
