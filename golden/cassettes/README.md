# Cassettes (recorded LLM responses)

One JSON file per model request, named by the SHA-256 of the request (role, messages with
images replaced by their digest, output schema, temperature). The P2 end-to-end check
replays them (`LLM_MODE=replay`), so golden runs are repeatable and use no quota
(CLAUDE.md rule 7, docs/14 §4).

- Record: put real `GROQ_API_KEY` / `GEMINI_API_KEY` in `.env`, then
  `uv run --no-project python scripts/e2e.py --record`.
- Files contain the prompt text, the response text, token usage and the model name. No
  headers, keys or image bytes are stored. `_unavailable_<role>` markers record that a role
  had no key during recording, so replay skips it the same way.
- A changed prompt is a cassette miss: the stage fails with a clear message; re-record.
