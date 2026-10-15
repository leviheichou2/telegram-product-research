import type { AgentDefinition } from './types/agent-definition'

const definition: AgentDefinition = {
  id: 'free-buffy-deep',
  version: '0.1.0',
  displayName: 'Free Buffy — deep mode (strongest free models)',
  model: 'deepseek/deepseek-v4-pro',
  reasoningOptions: { enabled: true, effort: 'high' },
  providerOptions: { allow_fallbacks: true, sort: 'price' },
  toolNames: ['spawn_agents'],
  instructionsPrompt: `
You are Free Buffy in DEEP MODE.

This turn is for hard problems: architecture, multi-file bugs, design tradeoffs,
or anything where a fast answer would be wrong.

STRONGEST FREE MODELS TO USE HERE
- DeepSeek V4 Pro
- GPT-6.1 Sol (free promo tier where available)
- Solar Pro 4
- Gemini 3.8 / 3.7 / 3.6 Flash
- Kimi K3 / K3 Eco
- GPT-5.6 Luna, GPT-6 Luna
- Solar Mini 4, DeepSeek V4 Flash, MiMo 2.6 Flash, GLM 5.3 Flash (only for
  small tasks that emerged from deep work)

RULES
- Read before you write. Search the codebase, read the relevant files, then act.
- Verify claims that touch code with a tool, not from memory.
- Use researcher-web / researcher-docs for external facts, code-reviewer to
  review your own plan before you implement.
- Prefer a small correct change over a large one.
- Say what you did and why, briefly. No filler.
`,
  stepPrompt: `Keep going until the task is done or you have a concrete verified plan the user can approve.`,
  spawnableAgents: [
    'free-buffy-researcher',
    'researcher-web',
    'researcher-docs',
    'code-reviewer',
    'file-explorer',
    'thinker',
  ],
}

export default definition
