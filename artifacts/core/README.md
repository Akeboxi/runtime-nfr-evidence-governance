# Runtime NFR anonymous reviewer artifact v0.21

This deterministic package supports the evidence-responsibility governance claims in the manuscript. It contains 8,668 pseudonymized cards, row-level expert and manual-audit evidence, the RCAEval refusal-path chain, and non-core prediction/topology boundary outputs.

Run from the extracted directory:

```text
python reproduce_summary.py --check
```

The command verifies the internal manifest and rebuilds the frozen counts. To regenerate RCAEval derivatives from a separately downloaded public archive, install the project Python dependencies and run `python rcaeval/rebuild_from_public_source.py --source-root <RE1-OB-root>`.

Names, e-mail addresses, private identity maps, original free text, signed declarations, absolute paths, credentials, and original AIOps event identifiers are excluded. Raw RCAEval identifiers remain only where needed to reproduce the public benchmark adapter.
