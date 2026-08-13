# Anonymous blinded human-routing supplement v1

This supplement contains the deidentified materials and outputs for the 32-card blinded disposition experiment reported in manuscript v0.34.

## Contents

- `data/blinded_cards.json`: the 32 cards as shown before protocol disclosure.
- `data/protocol_answer_key.csv`: blind-card identifier, frozen protocol choice, route, and prespecified difficulty.
- `data/blinded_responses_deidentified.csv`: 96 stage-1 choices and confidence ratings; comments and timing fields are omitted.
- `data/post_reveal_responses_deidentified.csv`: 96 structured post-reveal records without free text.
- `analysis/`: aggregate tables, summary, report, and figure QA contract.
- `scripts/`: analysis and plotting code.
- `figures/`: submission figure in SVG, PDF, and PNG.

## Reproduction

Run from the supplement root:

`python scripts/analyze_responses.py --responses data/blinded_responses_deidentified.csv --answer-key data/protocol_answer_key.csv --output-dir reproduced_analysis --bootstrap-reps 10000 --seed 20260814`

The primary result is 58/96 blinded agreement with the frozen protocol; the card-cluster bootstrap 95% interval is 50.0%–70.8%. Post-reveal records are explanatory only because the protocol choice was explicitly displayed.

No names, email addresses, registry entries, source-card identifiers, source event identifiers, absolute paths, or free-text responses are included.
