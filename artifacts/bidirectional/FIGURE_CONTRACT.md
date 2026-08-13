# Figure contract: bidirectional protocol contract test

- Figure ID: proposed Figure 5 / `bidirectional_conformance_v1`.
- Scientific question: does a changed interface preserve both admission and refusal boundaries, route all four governance states, enforce precedence, and fail closed on missing provenance?
- Claim supported: the executable contract suite covers both sides of the interface boundary and matches all prespecified expectations. It does not establish external validity or empirical accuracy.
- Data source: `case_results.json` and `summary.json` from this experiment.
- Visual form: panel A horizontal case matrix showing expected/observed agreement and whether the outcome is interface refusal, one of four routes, or construction failure; panel B four-state routed-case counts plus explicit annotations for the pre-routing refusal and construction failure.
- Encodings: route colors remain consistent with prior governance figures; exact labels and counts are printed; agreement is indicated redundantly by a check marker rather than color alone.
- Accessibility: colorblind-safe palette, grayscale-distinguishable labels, no red–green opposition, minimum 8-pt final text.
- Output size: 175 mm wide; editable SVG and PDF, 600-dpi TIFF, and PNG preview.
- Prohibited interpretations: external validity, threshold correctness, utility, effect size, model performance, or population inference.

