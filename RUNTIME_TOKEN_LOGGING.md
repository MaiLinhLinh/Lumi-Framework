# Runtime token logs

Normal end-to-end runs print `[LLM:USAGE]` at INFO level to the server terminal
through the existing `lumi.trace` handler. No benchmark runner or extra flag is
needed. Restart the server after updating the code.

The agents are `task_manager`, `assembly`, `search_information`, `search_image`,
`draw_image` and `gemini_live`. Each completed model call is logged before output
validation, so discovery calls and responses rejected during correction are
included. An SDK error without a response is reported by existing error logs;
its token consumption cannot be recovered locally.

Fields:

- `input_tokens`: the provider's prompt/input count, including context sent again.
- `thinking_tokens`: the provider's reasoning/thought count.
- `output_tokens`: generated output **excluding thinking**, including tool calls
  or image/audio output when the provider counts them.
- `output_tokens_including_thinking`: output plus thinking, kept separate from
  `output_tokens`.
- `total_tokens`: the provider's reported total, not a locally estimated count.

Missing metadata is `N/A`, never an assumed zero. In particular, Cerebras output
excluding thinking stays unknown when reasoning metadata is missing.

Illustrative line (not a measured run):

```text
INFO | turn=abcd | [LLM:USAGE] agent=assembly api=cerebras model=example run=run-123 attempt=1 input_tokens=100 thinking_tokens=240 output_tokens=60 output_tokens_including_thinking=300 total_tokens=400
```

Calls carry the current trace `turn` and available plan/run/task/attempt IDs.
Live records carry a session ID and `scope=provider_update`: these are provider
usage updates, not independent calls to sum. A completed Live turn with no usage
metadata emits `scope=turn_complete` with `N/A`. There is no automatic combined
end-to-end token total. Embedding calls are outside these generative-agent logs.

Provider field definitions: [Cerebras reasoning](https://inference-docs.cerebras.ai/capabilities/reasoning),
[Gemini Live usage](https://ai.google.dev/api/live#UsageMetadata),
[Gemini Interactions schema](https://ai.google.dev/static/api/interactions.openapi.json).
