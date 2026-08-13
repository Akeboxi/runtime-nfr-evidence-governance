# Figure contract: configuration robustness landscape

- Figure ID: proposed Figure 6 / `configuration_robustness_v1`.
- Scientific question: how much does card disposition migrate when plausible evidence-gate and candidate-generation settings vary around the frozen protocol?
- Claim supported: the reported routing distribution is a versioned configuration outcome whose stability can be quantified; the figure does not identify an optimal configuration.
- Primary comparison: `Cmin × Rt` at `W=24 h`, `q=0.95`, `k=3.0`, measured by migration rate from the frozen routes.
- Secondary comparisons: migration under `W`, `q`, and `k` one-factor changes with all other settings frozen.
- Data source: machine-generated `robustness_grid.csv` and `robustness_summary.json` from this experiment.
- Visual form: panel A heatmap for the primary landscape; panels B–D compact point/line summaries for `W`, `q`, and `k`; an in-figure note reports the minimum/median/maximum non-numeric-disposition range across the full grid.
- Encodings: color represents route-migration percentage; numeric cell labels provide exact values; the frozen cell receives a non-color outline/marker.
- Accessibility: colorblind-safe sequential palette, readable grayscale ordering, no red–green opposition, exact annotations, minimum 8-pt final text.
- Output size: 175 mm wide; editable SVG and PDF, 600-dpi TIFF, and PNG preview.
- Prohibited interpretations: parameter optimization, performance improvement, causal effect, cross-organization external validity, or independence of card-level observations.

