# Free Buffy — fast + deep free orchestrator

Runs on the free models already wired into this repo (agent ids
from `common/src/constants/free-agents.ts`, models from
`common/src/constants/freebuff-models.ts`). Default = fast row;
deep mode = strongest free row you name.

## Free models covered (add/remove by editing `FREE_MODELS` below)

| Agent id | Model | Notes |
| --- | --- | --- |
| `base2-free-solar-mini4` | `upstage/solar-mini4` | Fast default |
| `base2-free-solar-pro4` | `upstage/solar-pro4` | Deep default |
| `base2-free-deepseek-flash` | DeepSeek V4 Flash | Fast free |
| `base2-free-luna` | GPT-5.6 Luna | Free tier |
| `base2-free-luna-6` | GPT-6 Luna | Free tier |
| `base2-free-mimo` | MiMo 2.6 Flash | Free tier |
| `base2-free-glm-5-3-flash` | GLM 5.3 Flash | Daily premium pool free tier |
| `base2-free-deepseek` | DeepSeek V4 Pro | Deep free |
| `base2-free-gpt-6-1-sol` | GPT-6.1 Sol | Free promo tier |
| `base3-free-gemini-3-8-flash` / `base3-free-gemini-3-7-flash` | Gemini Flash | Free tier |
| `base2-free-kimi-k3` / kimi variants | Kimi | Free tier where available |

## Modes

- **fast** — picks the fastest free row, answer-first, minimal tool fan-out.
- **deep** — picks the strongest free row you name, full tool fan-out, research
  subagents when the task needs it.

## Interfaces

- **Freebuff/Codebuff runtime** — this is a normal Codebuff agent definition;
  Freebuff's runtime can spawn it like any other agent id.
- **Continue** — drop a `continue` descriptor in this dir; Continue picks up
  local agent definitions per its docs.
- **Copilot-style workflow** — any workflow that consumes an agent definition
  can read this file; the model + instructions are self-contained here.

## Caveat

"Free" here means free according to the repo's free-mode allowlist
(`FREE_MODE_AGENT_MODELS` in `common/src/constants/free-agents.ts`). Whether
a row is actually free for you 24/7 depends on the live platform (account tier,
referral rewards, off-peak windows, queue position). This agent does not change
that — it just routes across the free rows that exist.

---

# Build inventory (verified from this repo)

## Agent definition supplies

- base2-free-*: `createBase2('free', { model, reasoningOptions?, noReview?, providerOptions? })`
- base3-free-*: `createBase3CliRoot({ model, isFreebuff: true, reasoningOptions?, noReview? })`
- Both return `Omit<SecretAgentDefinition, 'id'>`; you add `id` + `displayName`.
- `SecretAgentDefinition` = `AgentDefinition` minus `toolNames` (so `toolNames?` is allowed too).

Model id constants live in `common/src/constants/freebuff-models.ts`; the free-mode allowlist and root id maps are in `common/src/constants/free-agents.ts`. Use the same `FREEBUFF_*_MODEL_ID` constants the existing agents use so the definitions stay consistent with the repo's model catalog.

Free model ids confirmed present in this repo:

- `FREEBUFF_SOLAR_MINI_4_MODEL_ID` = `upstage/solar-mini4`
- `FREEBUFF_SOLAR_PRO_4_MODEL_ID` = `upstage/solar-pro4`
- `FREEBUFF_DEEPSEEK_V4_FLASH_MODEL_ID` (DeepSeek V4 Flash)
- `FREEBUFF_DEEPSEEK_V4_PRO_MODEL_ID` (DeepSeek V4 Pro)
- `FREEBUFF_GPT_5_6_LUNA_MODEL_ID` (GPT-5.6 Luna, effort high)
- `FREEBUFF_GPT_6_LUNA_MODEL_ID` (GPT-6 Luna, effort high)
- `FREEBUFF_GPT_61_SOL_MODEL_ID` (GPT-6.1 Sol, reason effort constant)
- `FREEBUFF_MIMO_V25_MODEL_ID` (MiMo 2.6 Flash)
- `FREEBUFF_GLM_V53_FLASH_MODEL_ID` (GLM 5.3 Flash)
- `FREEBUFF_GEMINI_38_FLASH_MODEL_ID` = `google/gemini-3.8-flash`
- `FREEBUFF_GEMINI_3_7_FLASH_MODEL_ID` = `google/gemini-3.7-flash`
- `FREEBUFF_GEMINI_3_6_FLASH_MODEL_ID` = `google/gemini-3.6-flash`
- `FREEBUFF_KIMI_K3_MODEL_ID` = `moonshotai/kimi-k3` (base2-free-kimi-k3 exists in allowlist)
- `FREEBUFF_KIMI_K3_ECO_MODEL_ID` = `crof/kimi-k3-eco` (base2-free-kimi-k3-eco exists)
- `FREEBUFF_SPACE_BUNNY_ALPHA_MODEL_ID` (Space Bunny Alpha)
- `FREEBUFF_MUSE_SPARK_12_CONTRIBUTOR_MODEL_ID` / `...13` (Muse Spark 1.2 / 1.3, noReview)
- `FREEBUFF_OX_ALPHA_MODEL_ID` (Ox Alpha)

Note: GPT-6.1 Sol and a few provisioned tiers are paid-only on every surface per the model file; free-mode admission is the gate for those.

## ModelName union (for any new .agents/ definition)

`agents/types/agent-definition.ts` exposes `ModelName` including openai/gpt-*, anthropic/*, google/gemini-*, qwen/*, deepseek/*, mimo/*, moonshotai/*, z-ai/glm-*, minimax/*, plus `(string & {})`. So a local `.agents/` orchestrator can name any of these as its `model`.

## Build target

- Root orchestrator: `.agents/free-buffy.ts` with a `model` from the union, fast + deep mode switch in instructions, spawnableAgents pointing at the free base2/base3 twins.
- Continue descriptor: `.agents/continue.json` (or `.continue/continuation.json` at repo root) pointing at the local agent + a model.
- Copilot-style: the agent definition itself is the interface; Copilot is the editor extension, not something to configure here.

## Typecheck

Run `bun --cwd agents x tsc --noEmit -p tsconfig.json` after creating files; the `.agents/` dir is NOT in `agents/tsconfig.json` include, so a `.agents/` file won't typecheck under that project. Instead, typecheck the new `.agents/` file standalone via the repo root tsconfig that resolves `@codebuff/common` and `agents/types` paths, OR keep the orchestrator minimal enough that it only imports from `@codebuff/common/constants/freebuff-models` and `agents/types/agent-definition`.

## Restore tasks (separate from the agent build)

- `events.json` and TelegrAI `dashboard.html`: not in this repo's git tree at any depth, not on disk. Need the source TelegrAI project and ideally the content/schematic to recreate them.
