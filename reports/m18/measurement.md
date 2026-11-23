# M18 image comparison

Source: real local GPU worker, 2026-09-13; seed 618092026, identical rewritten
prompt, 1024×1024, guidance zero, sequence limit 512. Infrastructure rate
EUR1.46988/hour. Raw measurements remain in BRAIN/m18-benchmark.json.

The original request came from a private owner session; its live images and
judgments are kept privately. In this public import the example was regenerated
with a neutral subject, and the linked images are neutral pictures drawn by code,
not model generations. Request (translated from French): create an image of a
rabbit jumping with a hedgehog, both wearing orange boots and purple beanies.

Rewritten prompt:

```
Subjects: A rabbit and a hedgehog jumping together.
Attributes: Both subjects are wearing orange boots and purple beanies.
Scene: A dynamic setting capturing the motion of the jump.
Style: Vibrant, playful, and whimsical illustration.
```

| Steps | Worker seconds | Jumping rabbit and hedgehog | Orange boots | Purple beanies | Image |
|---|---:|---|---|---|---|
| 4 | 3.1594 | present | present | present | [4 steps](4-steps.png) |
| 8 | 4.9288 | present | present | present | [8 steps](8-steps.png) |

These findings are the implementing agent's direct visual inspection of both
original PNG outputs, not independent human scores. Both satisfy the three visible
constraints on this one prompt/seed. Four steps remains the default: equal
observed constraint fidelity with lower latency. This small comparison does not
establish a general quality advantage. The public J14 journey completed in this session. Its vision-provider judgment
found all three constraints present (3/3). The original prompt, rewritten prompt,
random seed, and individual judgments are recorded in [J14 evidence](j14.json);
see the [replay image](j14.png) (drawn by code). This is one sample, not a general guarantee
of constraint adherence. Image iteration returned a distinct image reference.
J12 succeeded on the third bounded run after two real search retry timeouts,
as recorded in the journey report. CI verification is still pending.

The conservative rewrite reservation was EUR0.020744. Each image call stayed
below the EUR0.05 combined reservation. No external image generator was used.
