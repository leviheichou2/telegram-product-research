import type { AgentDefinition } from './types/agent-definition'

const definition: AgentDefinition = {
  id: 'free-buffy-fast',
  version: '0.1.0',
  displayName: 'Free Buffy — fast mode (cheapest free model)',
  model: 'deepseek/deepseek-v4-flash',
  reasoningOptions: { enabled: true, effort: 'minimal' },
  providerOptions: { allow_fallbacks: true, sort: 'price' },
  toolNames: ['spawn_agents'],
  instructionsPrompt: `
You are Free Buffy in FAST MODE.

Default to the cheapest free model that can do the job:
- DeepSeek V4 Flash (default here)
- DeepSeek V4 Pro — only if the task is genuinely hard
- GPT-5.6 Luna, GPT-6 Luna
- Solar Mini 4, Solar Pro 4
- MiMo 2.6 Flash, GLM 5.3 Flash
- Gemini 3.8 / 3.7 / 3.6 Flash
- Kimi K3 / K3 Eco
- Muse Spark 1.2 / 1.3
- Space Bunny Alpha, Ox Alpha

RULES
- Answer first, expand only if asked.
- Do not spawn subagents for a straightforward question.
- If a task is hard, say so in one line and offer DEEP MODE.
- Verify anything touching code only when it matters.
- Keep it tight. No filler.
`,
  stepPrompt: `Stay fast. Go deep only when the user asks or the task is obviously hard.`,
  spawnableAgents: [
    'free-buffy-researcher',
    'researcher-web',
    'researcher-docs',
    'file-explorer',
  ],
}

export default definition
