# CR Automation Pipeline — Google ADK Orchestrator

Google ADK-based orchestrator for the CR Automation pipeline. Uses ADK's `SequentialAgent` with conditional routing nodes for intelligent retry loops.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Google ADK SequentialAgent                     │
├──────────┬──────────┬──────────┬──────────┬──────────┬──────────┤
│ Step 1   │ Step 2   │ Step 3   │ Step 4   │ Step 5   │ Router5  │
│ Parser   │ Analyzer │ Rules    │ JSON     │ CIQ QA   │ →3 or →6 │
├──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤
│ Step 6   │ Step 7   │ Router7  │ Step 8   │ Step 9   │ Router9  │
│ Workflow │ MOP QA   │ →6 or →8 │ Simulator│ Exec QA  │ →6/8/10  │
├──────────┴──────────┴──────────┴──────────┴──────────┴──────────┤
│ Step 10: Final Report                                            │
└─────────────────────────────────────────────────────────────────┘
```

## Feedback Loops

| Test Step | Failure Type        | Routes To | Why                              |
|-----------|--------------------|-----------|---------------------------------|
| Step 5    | Validation failure | Step 3    | Fix validation rules             |
| Step 7    | MOP gen failure    | Step 6    | Fix workflow YAML                |
| Step 9    | YAML parse error   | Step 6    | Fix workflow structure            |
| Step 9    | Simulator error    | Step 8    | Fix mock responses               |

Failure classification uses pattern matching on error messages to auto-detect the right retry target.

## Usage

### CLI
```bash
python main.py \
  --ciq path/to/ciq.xlsx \
  --uc-name DSR_1058_IFC_CAPABILITY_MODIFICATION \
  --api-key sk-or-... \
  --model openrouter/google/gemini-2.5-flash
```

### Library
```python
from src.orchestrator.runner import PipelineRunner

runner = PipelineRunner(config={
    "default_model": "openrouter/google/gemini-2.5-flash",
    "openrouter_api_key": "sk-or-...",
    "ciq_processor_jar": "./lib/ciq-processor.jar",
    "cli_automation_jar": "./lib/cli-automation-standalone.jar",
})

run_id = await runner.start({
    "uc_name": "DSR_1058_IFC_CAPABILITY_MODIFICATION",
    "ciq_file_path": "/path/to/ciq.xlsx",
})

# Respond to milestones
runner.respond_milestone(run_id, step_num=3, decision="continue")

# Check status
status = runner.get_status(run_id)
```

## Dependencies

- `google-adk>=2.11.0` — Agent Development Kit
- `cr_automation-agentic` — Existing pipeline agents (referenced via sys.path)

## Project Structure

```
cr_automation_agentic_google_adk/
├── main.py                         # CLI entry point
├── README.md
├── src/
│   ├── orchestrator/
│   │   ├── pipeline_graph.py       # ADK agent graph definition
│   │   └── runner.py               # Pipeline runner with session management
│   └── nodes/
│       └── step_nodes.py           # Step node implementations + failure routing
└── config/
```
