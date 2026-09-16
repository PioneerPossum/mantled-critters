---
name: magi-custom-model-goal
description: "Gil's future goal to train/compile custom models for the Hailo-10H nodes (melchior/balthazar) instead of only using pre-built model-zoo weights — not started, revisit as the magi-cluster project matures"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-16T03:37:43.247Z
---

Mentioned 2026-09-15, alongside the local-LLM feasibility check-in: Gil wants to eventually **build custom models** for the magi-cluster's Hailo-10H hardware, not just consume pre-compiled model-zoo weights (YOLO detection models, the ~1.5B Qwen2.5-class GenAI models — see [[magi-local-llm-feasibility]]).

**Status: aspirational, not started.** Explicitly framed as "a fun project to revisit soon as we move along this project and grow" — not an immediate priority, but something to keep in view.

**What this would actually require (not yet researched in depth):** Hailo's **Dataflow Compiler** — the host-side toolchain for compiling custom-trained neural networks into `.hef` files runnable on Hailo hardware. This is distinct from the Model Zoo (pre-built weights) and from HailoRT (the on-device runtime) — see [[magi-local-llm-feasibility]] for the distinction, which came up when confirming melchior only needs the Model Zoo package, not the full Dataflow Compiler/AI Software Suite, for the current LLM-feasibility work.

**How to apply:** don't scope this into current build tasks unprompted — it's explicitly a "someday" item. Worth resurfacing once the local-LLM work ([[magi-local-llm-feasibility]]) and the detection pipeline are both stable and Gil has bandwidth to look at model training/compilation. Also worth connecting to [[magi-github-sync]] — Gil has floated pitching this whole project's capabilities to Hailo, and "we trained/compiled our own custom model for this hardware" would be a much stronger part of that pitch than only using off-the-shelf model-zoo weights.
