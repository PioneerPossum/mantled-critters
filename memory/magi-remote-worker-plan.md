---
name: magi-remote-worker-plan
description: "Plan (not yet built) to onboard Gil's personal devices with NPU/GPU (M1 MacBook now, M4 later, any Mac/Windows/Linux machine) as opportunistic Tailscale-connected LLM worker nodes for magi-cluster"
metadata: 
  node_type: memory
  type: project
  originSessionId: f6269a19-bce7-578b-b30c-0c70fe46905a
  modified: 2026-09-18T13:36:56.500Z
---

Discussed 2026-09-18, alongside setting up HTTPS certs on the dashboard's Tailscale proxy (see [[magi-cluster-status]] for that work and the cert-expiry/CT-log caveats found). Gil wants to eventually extend magi-cluster beyond melchior/balthazar to include any personal computer with an NPU or GPU — starting with his **M1 MacBook** (planned upgrade to an M4-series machine later, funds permitting), and in principle any other Mac/Windows/Linux machine — as additional compute for the cluster.

**Status: planned, not started.** Explicitly deferred — Gil wants the architecture documented now, execution later.

## Decided scope

1. **Role: a third local-LLM agent, not overflow/burst compute.** The Mac would run its own local LLM, the same way melchior/balthazar do via [[magi-local-llm-feasibility]]'s Option A pattern — but likely a genuinely bigger/better model than the Pi's 1.5B Qwen2.5-Coder, since Apple Silicon's on-device ML (MLX, Core ML) has real headroom the Hailo-10H doesn't. This is a capability upgrade, not just more of the same.
2. **Resource policy: opportunistic only.** This is Gil's daily-use laptop, not a dedicated node — it must never compete with Gil's own active use. The cluster should only be able to reach it when it's plugged in, idle, and not being actively used. This needs a real availability-signaling mechanism (see "Open design questions" below) — nothing like melchior/balthazar's always-on heartbeat assumption applies here.
3. **Cross-platform**: Tailscale's client is uniform across Mac/Windows/Linux, so the same onboarding pattern should work for any future machine regardless of OS — but the actual local-LLM tooling choice will likely differ per OS (Mac: MLX or Ollama; Windows: Ollama; Linux: Ollama or llama.cpp) and isn't decided yet.

## Recommended architecture (from Tailscale's own AI-infrastructure docs, read 2026-09-18)

Relevant docs: [connect-inference-servers](https://tailscale.com/docs/use-cases/ai-infrastructure-access/connect-inference-servers) (directly useful), [secure-ai-training-cluster](https://tailscale.com/docs/use-cases/ai-infrastructure-access/secure-ai-training-cluster) (mostly Kubernetes-specific, not applicable — magi-cluster doesn't run K8s — and explicitly doesn't cover inter-node data-plane security, which is the actually-relevant question for us and would need its own design), [centralize-llm-access-and-spending](https://tailscale.com/docs/use-cases/ai-infrastructure-access/centralize-llm-access-and-spending) (not relevant — for managing paid cloud API costs across teams, doesn't apply to self-hosted local models), [share-private-game-server](https://tailscale.com/docs/use-cases/personal-or-at-home-use/share-private-game-server) (general pattern confirmation only — private tailnet-only access without opening ports, which magi-cluster already follows correctly for the dashboard).

**The actually-useful pattern**: Tailscale's tag-based device identity + deny-by-default grants model, instead of ad-hoc device access:
- Tag new worker devices (e.g. `tag:magi-worker`) via a reusable/tagged auth key at onboarding, rather than manual interactive `tailscale up` login each time.
- Define explicit grants in the tailnet's ACL/policy: only the dashboard/orchestrator (audire-videre) can reach `tag:magi-worker` devices, only on the specific port their local-LLM API listens on — nothing else, matching [[feedback-scoped-permissions]]'s established preference for narrow access over broad grants.
- This is a genuinely good fit for Gil's existing security posture in this project (deploy-key-scoped GitHub access, narrow Bash permission rules, etc.) — same philosophy, different layer.

## Open design questions, not yet resolved (for whenever this gets built)

1. **Availability signaling**: how does the Mac announce "I'm idle, plugged in, and available" to the rest of the cluster? Options to consider when this is picked up: a lightweight agent process that checks macOS's own idle-time/power-source APIs and only starts/exposes the local-LLM service when conditions are met (rather than running it constantly and gating access at the network layer alone) — probably the right call, since it also saves Gil's battery/resources when the laptop isn't meant to be helping.
2. **Failover interaction**: does the Mac participate in the existing melchior/balthazar heartbeat/failover system ([[magi-failover]]), or is it treated as a distinct, separate tier (opportunistic bonus capacity, not a failover target melchior/balthazar could depend on, since it's not guaranteed to be present)? Leaning toward the latter — the failover system's whole design assumes its two members are reliably present, which the Mac explicitly isn't.
3. **Dashboard integration**: how does the dashboard's chat UI (currently a straight route to balthazar, falling back to melchior) decide whether to offer the Mac as a third option, and only when it's actually online/available?
4. **Model choice for the Mac**: not decided — worth evaluating MLX-native models vs. just running Ollama the same way balthazar's `hailo-ollama` experiment did, once this is picked up.

**How to apply:** don't start building any of this until Gil explicitly says go — this file exists so the next session (or this one, later) doesn't have to re-derive the Tailscale research or re-litigate the three scope decisions already made (third-agent role, opportunistic-only resource policy, tag-based onboarding).

## 2026-09-27 — scope expanded (still planning, not built)

Gil asked how to use the cluster + MacBook/other laptops to reduce Claude coding/task calls, with **local-first routing and an online fallback** when a request exceeds the cluster's current capacity, advertised on Tailscale. Also asked for a **full dashboard revamp** (request submission, history, per-model compute power, "how the cluster works in theory" diagrams, live node health for every device), **themed like Neon Genesis Evangelion's NERV/MAGI technical screens** — fitting, since the node names already are the MAGI (Melchior/Balthasar/Caspar=repositorium). Gil also asked about trying **Claude Design** (claude.ai/design) for the visual design; he has never used it.

Proposed shape given to Gil: an OpenAI-compatible router on audire-videre (tiers: Hailo Pi nodes for triage/tools/short answers → opportunistic laptop worker for small code tasks → online fallback), router decisions shown on the dashboard as a MAGI "vote". Caveats told to him: more Claude Code sessions share the same plan pool (don't add usage); an automated online fallback should use an API key (separate pay-as-you-go billing), not the subscription login piped through a gateway. Open question: MacBook RAM (decides model size).

## 2026-09-27 — workers assigned
- **MacBook Pro** (M1 Pro, 16 GB), Claude Code session `macbookpro-<your-tailnet>-ts-net-kind-gadget`.
- **Desktop PC** `desktop-uddm435` (Ryzen 7 9800X3D, 64 GB RAM, Radeon RX 9070 XT 16 GB + 2 GB integrated Radeon), session `desktop-uddm435-quirky-bumblebee`. Not the WoL PC.
- The Wake-on-LAN machine was audire-videre itself. It has a Quadro T1000 Mobile 4 GB, but no NVIDIA driver is installed (no nvidia-smi; needs sudo) — possible small tier later.
- Both sessions were sent setup briefs via SendMessage. Contract: an OpenAI-compatible server on the LAN IP, and idle-gated announces to [[magi-router]] `/router/announce` (names `macbook` / `desktop`, tier gpu). The token is NOT sent over messages — Gil copies `~/.config/magi-router/token` to each worker himself.

## 2026-09-27 — MacBook worker BUILT (not yet announcing)
- **Model:** mlx-lm 0.31.3, `mlx-community/Qwen2.5-Coder-14B-Instruct-4bit` (7.7 GB). About 11 tok/s steady; model load takes about 20 s.
- **Network:** LAN IP 192.168.8.178. mlx_lm listens on 127.0.0.1:8812. The controller's proxy on :8811 allows only 192.168.8.136 and returns 403 to everyone else.
- **Controller:** user LaunchAgent `com.magi.worker` (stdlib Python). Manual switch: `~/.local/share/magi-worker/magi-worker-ctl on|off|status` (flag file `~/.config/magi-worker/disabled`).
- **Availability (all required):** on AC, HIDIdleTime ≥ 300 s, memory pressure level 1, and ≥ 35% free memory to start. Any input stops the model; an in-flight request then fails and the router escalates.
- **Blocked on:** Gil copying the router token to `~/.config/magi-worker/router_token` (chmod 600). It is picked up automatically within 5 s.
- **Testing:** end-to-end tests need the Mac on AC power and left untouched for 5 min.

## 2026-09-27 — Desktop worker benchmarked (not yet announcing)
- **Stack:** Windows 11 Home, Ollama 0.32.15 (AMD AI Bundle, ROCm on gfx1201). Endpoint 192.168.8.216:11500 (LAN-bound). Scheduled task `magi-worker` running `magi-worker.ps1`; token path `C:\Users\gil\.config\magi-worker\router_token`.
- **qwen2.5-coder:14b q4_K_M:** 11.2 GB, 100% on GPU, ~59 tok/s generation, ~50 tok/s at 7k context. CHOSEN.
- **qwen3-coder:30b:** only 78% on GPU and much slower on long prompts; rejected.
- **Idle gate:** ≥300 s no input, no other process using the GPU heavily, ≥90% of the model on GPU.
- **Announce:** asked for tok_s 59, max_tokens 4096, max_chars 40000, timeout_s 300.
- **Blockers:** (1) Gil has to place the token; (2) the session's own end-to-end test (idle_seconds=0) was denied by its classifier, so the LAN listener is unverified; (3) firewall CONFIRMED: inbound rule "magi-worker (router only)" allows TCP 11500 only from .136 and only for ollama.exe; the server binds only to .216. Model id `qwen2.5-coder:14b-instruct-q4_K_M`.
- [[magi-router]] now prefers the higher tok_s worker within a tier.

## 2026-09-28 — MacBook 14B doesn't fit in practice
- The token is in place, and the Mac announced once successfully (00:25:38).
- The 14B model then hit memory-pressure level 2 within 30-70 s of every start and looped about 7 times. Swap was already 8.4/9.2 GB used with Gil's normal apps.
- The worker now backs off exponentially after pressure stops (10 → 20 → 40 → 60 min; resets after 15 min without pressure).
- Proposed: switch to Qwen2.5-Coder-7B-Instruct-4bit (~4.3 GB). Waiting for Gil's OK because it's a new download.
- The Mac is secondary now that the desktop does ~55-59 tok/s; its value is when the desktop is busy (gaming).

## 2026-09-28 — MacBook on 7B, VERIFIED through the router
- Model `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` (4 GB). ~33-35 tok/s measured by the Mac session; announces tok_s 34. First real announce at 05:32 CDT.
- Router test forcing model "macbook": 163 tokens in 12 s, OK.
- Auto routing of code went to the desktop first (tok_s ordering works). Desktop took 17 s for 363 tokens, likely a cold reload after its 10 min OLLAMA_KEEP_ALIVE.
- A 15-min memory-pressure soak was still in progress at that point; the Mac session reports only on failure.

## 2026-09-28 (later) — Mac memory rule relaxed (Gil approved directly to the Mac session)
- Even the 7B tripped memory-pressure "warn" (level 2) every few minutes with Gil's apps open.
- The worker now stops only on "critical" (level 4) pressure. Keyboard/mouse input still stops it within ~5 s through the idle gate.
- Critical-pressure backoff: 3 min, doubling to 1 h, resetting after 15 min running clean.
- The critical-only rule has not yet been soak-tested under sustained AC + idle; the Mac went on battery around 15:00 CDT.

## 2026-09-29 — Desktop re-check
- A session named "macbook-runner-v1" is actually running on the Windows DESKTOP (it confirmed this; it can't rename itself). Don't mix up the machines' records by session name.
- qwen3-coder:30b re-benchmarked: 82% on GPU, 44 tok/s vs the 14B's 59.5. Rejected again (below the 90% GPU gate). The 14B stays active.
- Two announce timeouts at ~10:22/10:47 were NOT router restarts; the likely cause is the GL-router path (announces arrive from 192.168.8.1). Log timestamps if it recurs.
