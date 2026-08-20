# Maybe Later

This file records useful ideas that are deliberately outside the current work.

## Weighted voice mixing in the MLX backend

The desktop voice mixer can produce formulas such as
`af_heart*0.6 + am_adam*0.4`, while the current MLX adapter uses only the first
voice component. Proper support would parse the weights, load each MLX voice
embedding, validate compatible languages and tensor shapes, calculate the
weighted embedding, and cache the result.

This is deferred because weighted voice mixing is not important to the current
macOS workflow. Until it is implemented, the existing limitation should remain
documented and must not be mistaken for true weighted MLX synthesis.
