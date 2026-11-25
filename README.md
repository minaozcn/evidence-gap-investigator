# Evidence-Gap Investigator

An LLM agent that investigates past IT incidents from a MySQL database, compares possible root causes as competing hypotheses, and says clearly **what evidence is missing** when the data is not enough for a conclusion.

The goal is an agent that does not overstate certainty: a missing log is treated as a data gap, not as proof, and every claim must point to a real evidence record.

> 🚧 **Work in progress.** The agent core runs from the command line. The FastAPI backend and React interface are planned but not started yet. All incident data is synthetic.

## How It Works

1. The agent receives an incident key (e.g. `INC-1004`).
2. It gathers data with three read-only tools:
   - `get_incident` – the incident record (without its root cause)
   - `get_evidence` – observations, negative findings and **data gaps** (returned separately as `GAP-` IDs)
   - `find_similar_incidents` – resolved past incidents from the same service or with shared title keywords
3. It submits a structured result with the `submit_investigation` tool: hypotheses with status and confidence, supporting / contradicting evidence, related history, a verdict (`conclusive` / `inconclusive`) and next checks.
4. The result is **strictly validated** before it is accepted. On the first invalid submission the list of violations is sent back to the model for one correction; a second invalid submission marks the run as `failed`.

Examples of validation rules:
- Evidence can only be cited by `E-` IDs that the tools actually returned in this run; `GAP-` IDs can never be used as evidence.
- A hypothesis can only be `ruled_out` with a contradicting observation, never with a negative finding alone.
- `conclusive` requires exactly one leading hypothesis with high confidence; an `inconclusive` result must include next checks.
- Past incidents alone can't justify medium or high confidence.

## Features

- Agent loop written from scratch, without an agent framework (automatic tool calling is disabled; the loop is controlled in code)
- Provider-independent LLM layer (`investigator/llm/base.py`) with a Gemini adapter
- Hard limits: max 8 model turns per investigation, max 6 tool calls per turn and 20 in total, cached duplicate calls, bounded retries for rate limits / 5xx
- Read-only database user (`SELECT` only) and parameterized queries
- Answer-leak protection: root causes of the target incident are never returned by the tools, and the expected outcomes live in a test fixture that only the evaluation script reads
- Secret redaction in error messages and saved run files
- Evaluation script that compares runs with expected outcomes
- 100 tests (`unittest`); offline tests use a scripted fake LLM, database tests run against a read-only MySQL user

## Results So Far

Three open incidents in the synthetic dataset were investigated with the real Gemini API (`gemini-3.8-flash`); all three passed the evaluation:

| Incident | Expected | Result |
|---|---|---|
| INC-1003 | conclusive – config change | ✅ conclusive, `config_change`; a misleading similar past incident was correctly weakened |
| INC-1004 | inconclusive – data missing | ✅ inconclusive, highest confidence `medium`, data gaps used only for next checks |
| INC-1008 | conclusive – expired credential | ✅ conclusive, `expired_credential` after one self-correction |

Total: 7 model requests for the three runs. This is a small synthetic benchmark, not a general accuracy measure. The keyword-based matching for "next checks" in the evaluation can still produce false matches.

## Tech Stack

- Python 3 (developed on 3.14)
- MySQL 8 + PyMySQL
- Google Gemini API (`google-genai`)
- `unittest`

## Project Structure

```
├── investigator/
│   ├── investigation.py   # Agent loop, submit tool, run saving
│   ├── validation.py      # Strict validation of the submitted result
│   ├── tools.py           # Read-only database tools
│   ├── db.py              # MySQL connection (UTC session)
│   ├── redact.py          # Removes secrets from messages
│   └── llm/               # Provider-independent interface + Gemini adapter
├── scripts/
│   ├── investigate.py     # Investigate one incident (uses the real API)
│   ├── evaluate.py        # Evaluate saved runs
│   └── smoke_test.py      # Quick end-to-end check
├── db/                    # Schema, synthetic seed data, follow-up evidence
├── tests/                 # Unit, integration and security tests
└── docs/                  # Design decisions and progress log (in Turkish)
```

## Getting Started

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Load `db/schema.sql` and `db/seed.sql` into MySQL, then create a read-only user:
   ```sql
   CREATE USER 'investigator'@'localhost' IDENTIFIED BY '<password>';
   GRANT SELECT ON evidence_gap.* TO 'investigator'@'localhost';
   ```
3. Copy `.env.example` to `.env` and fill in the Gemini API key and database password.
4. Run the tests (no API usage):
   ```bash
   python -m unittest discover -s tests -t . -v
   ```
5. Investigate an incident and evaluate it (uses the real API):
   ```bash
   python -m scripts.investigate INC-1003
   python -m scripts.evaluate
   ```

## Roadmap

- [x] Database schema and synthetic incident data
- [x] LLM provider layer and smoke test
- [x] Read-only investigation tools
- [x] Agent loop, validation and evaluation
- [ ] Storing investigation results in the database
- [ ] FastAPI backend
- [ ] React interface

## Known Limitations

- Only a Gemini adapter is implemented; other providers would need their own adapter.
- The dataset is small and synthetic (8 incidents, 41 evidence records).
- Results are saved as JSON files in `runs/`, not in the database yet.
- The similarity search is keyword-based, not semantic.

## Author

Mina Özcan – [GitHub](https://github.com/minaozcn)
