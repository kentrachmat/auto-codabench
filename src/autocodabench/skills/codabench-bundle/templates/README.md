# Codabench program templates

Canonical, framework-agnostic scaffolds for the two executable programs a
Codabench task can carry:

- `scoring.py` — reads ground-truth labels and the model's predictions,
  computes the competition metric(s), and writes `scores.json`.
- `ingestion.py` — for code-submission (γ-style) tasks: loads data,
  instantiates the participant's `Model`, fits, predicts, and saves
  predictions for the scoring step.

These are *templates*, not working programs: each step raises
`NotImplementedError` with a one-line instruction. Copy the relevant file
into a bundle's `scoring_program/` or `ingestion_program/` directory, fill
in the bodies, and reference it from that program's `metadata.yaml`
`command:` key.

## Path contract

Both scripts take a `--codabench` flag. With it, they use the platform's
fixed container paths; without it, they use sibling directories for local
testing. The platform paths match exactly what the autocodabench runner
(`runner/execution.py`) mounts under its docker engine and stages under
its conda fallback, so a program that runs locally through the runner runs
unchanged on Codabench:

| Role | Container path (with `--codabench`) |
|---|---|
| Ingestion input data | `/app/input_data` |
| Submitted code (ingestion) | `/app/ingested_program` |
| Scoring: predictions | `/app/input/res` |
| Scoring: reference labels | `/app/input/ref` |
| Program directory | `/app/program` |
| Output (`scores.json` / `predictions.npy`) | `/app/output` |

The `metadata.yaml` `command:` should invoke the script with `--codabench`,
for example:

```yaml
command: python3 $program/scoring.py --codabench
```
