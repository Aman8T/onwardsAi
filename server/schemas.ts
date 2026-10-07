/**
 * Structured-output schemas for the two AI stages.
 *
 *   ReaderOutput – what one Haiku reader extracts from one source
 *   SynthOutput  – what the Sonnet synthesizer returns (normalised into a Profile by pipeline.ts)
 *
 * These are sent to the Agent SDK as JSON Schema (outputFormat: json_schema), so they stay
 * deliberately "JSON-schema friendly": no unions or transforms, and length / count limits live
 * in the descriptions (the model reads them) instead of hard .max() checks. The pipeline clamps
 * and trims afterwards, so a slightly long answer never fails a whole run. Lists use
 * `.default([])` so that zod accepts a missing list, while the JSON Schema still marks it required.
 */
import { z } from 'zod';

/* ───────────────────────── reader (Haiku, one per source) ───────────────────────── */

export const ReaderSignal = z.object({
  label: z.string().describe('The signal itself, at most 32 characters. E.g. "React", "Mentored 2 interns", "Supabase auth".'),
  detail: z
    .string()
    .describe('Where or how much, at most 40 characters. E.g. "14 repos", "motion-kit", "resume", "3 case studies".'),
});

export const ReaderSkill = z.object({
  name: z.string().describe('Canonical skill name, short (e.g. "React", "PostgreSQL", "Accessibility", "Prompt design").'),
  evidence: z
    .string()
    .describe(
      'ONE concrete, specific sentence (at most 120 characters) naming the repo, project, role or page that proves it. ' +
        'E.g. "motion-kit: spring-physics animation library for React, 212 stars".',
    ),
  strength: z
    .number()
    .describe(
      'Integer 1–5. 1 = only mentioned/listed; 2 = used once in a small or personal project; 3 = used repeatedly or in a real project; ' +
        '4 = professional depth with outcomes; 5 = standout public artifact or leadership (e.g. a library with real adoption).',
    ),
});

export const ReaderPerson = z.object({
  name: z.string().optional().describe('Full name exactly as written in the source.'),
  currentRole: z.string().optional().describe('Most recent job title, e.g. "Frontend Engineer". Students: e.g. "CS Student".'),
  yearsExperience: z
    .number()
    .optional()
    .describe('Full-time professional experience in years up to today, one decimal, computed from dates (internships excluded).'),
  location: z.string().optional().describe('City (and country if stated).'),
});

export const ReaderOutput = z.object({
  sub: z
    .string()
    .describe(
      'Stat line for this source, at most 48 characters, built from REAL counts in the source. ' +
        'E.g. "34 repos · 9 languages", "6 roles · 23 skills", "5 case studies", "1 shipped app · 3 flows".',
    ),
  summary: z.string().describe('1–3 plain sentences: what this source shows about the person. No flattery.'),
  person: ReaderPerson.optional().describe('Identity details, only if the source states them (mostly from a resume or profile page).'),
  signals: z
    .array(ReaderSignal)
    .default([])
    .describe('4–12 short, scannable signals (technologies, achievements, habits) found in this source, most important first.'),
  skills: z
    .array(ReaderSkill)
    .default([])
    .describe('Up to 25 skills this source evidences, strongest evidence first. Only what the source actually shows.'),
  notes: z
    .array(z.string())
    .default([])
    .describe(
      '2–4 concrete observations about this source (each at most 110 characters), including at least one honest improvement ' +
        'opportunity. E.g. "Tests in only 4 of 34 repos", "Impact is described but rarely measured".',
    ),
});
export type ReaderOutput = z.infer<typeof ReaderOutput>;

/* ───────────────────────── synthesizer (Sonnet, once) ───────────────────────── */

export const SynthPerson = z.object({
  name: z.string().describe('Real name if any source states it, otherwise "You".'),
  headline: z.string().describe('"<Current role> · <N.N> yrs · <City>", e.g. "Frontend Engineer · 2.4 yrs · Bengaluru". Omit unknown parts.'),
  currentRole: z.string().describe('The CURRENT role from the most recent title, without levels or grades ("Frontend Engineer II" → "Frontend Engineer"). Students: e.g. "CS Student".'),
  location: z.string().optional(),
  yearsExperience: z.number().optional().describe('Full-time professional years up to today (internships excluded), one decimal.'),
  summary: z.string().describe('Two sentences: who they are professionally and what makes their profile distinctive. Grounded, no flattery.'),
});

export const SynthEvidence = z.object({
  sourceId: z.string().describe('MUST be one of the source ids listed in the prompt (e.g. "resume", "github", "lovable-1").'),
  text: z
    .string()
    .describe(
      'Grounded proof, at most 90 characters, naming the repo / project / role. For gaps, state the observed absence ' +
        '(e.g. "No eval harness in any of 34 repos").',
    ),
});

export const SynthDomain = z.object({
  id: z.string().describe('kebab-case id, e.g. "frontend".'),
  name: z.string().describe('Full domain name, e.g. "Frontend Engineering".'),
  short: z.string().describe('Node label, at most 16 characters, e.g. "Frontend".'),
  score: z.number().describe('Integer 0–100 (rubric in the system prompt).'),
  summary: z.string().describe('One sentence on where they stand in this domain, grounded in evidence.'),
});

export const SynthSkill = z.object({
  id: z.string().describe('kebab-case id, unique across the graph, e.g. "react", "llm-evals".'),
  name: z.string().describe('Skill name, at most 22 characters.'),
  domainId: z.string().describe('id of one of the domains above.'),
  score: z.number().describe('Integer 0–100 (rubric in the system prompt). Gap skills score under 45.'),
  evidence: z.array(SynthEvidence).default([]).describe('1–3 evidence items, strongest first.'),
  unlocks: z
    .array(z.string())
    .default([])
    .describe('Exact titles of roles from the "roles" list that this skill unlocks or strengthens (0–3).'),
});

export const SynthRequirement = z.object({
  name: z.string().describe('Requirement name; reuse the exact skill name when it maps to a skill on the graph.'),
  skillId: z.string().optional().describe('id of the matching skill on the graph, when there is one.'),
  have: z.boolean().describe('true when they already meet it well enough (current ≥ 60 and close to target).'),
  current: z.number().describe('Integer 0–100: their level today (equal to the skill score when it maps to a skill).'),
  target: z.number().describe('Integer 0–100: the level this role expects.'),
});

export const SynthRole = z.object({
  id: z.string().describe('kebab-case id, e.g. "ai-product-engineer".'),
  title: z.string().describe('Role title, at most 26 characters.'),
  alignment: z.number().describe('Integer 0–100: honest weighted coverage of this role\'s requirements today.'),
  eta: z.string().describe('Realistic time to be hireable at ~6 hrs/week, e.g. "~4 months".'),
  why: z.string().describe('At most 160 characters, grounded in their evidence: why this role, and what stands between them and it.'),
  requirements: z.array(SynthRequirement).default([]).describe('4–7 requirements mixing strengths they have and gaps.'),
});

export const SynthSource = z.object({
  id: z.string().describe('Source id from the prompt.'),
  sub: z.string().describe('Refined stat line, at most 40 characters, real counts only, e.g. "34 repos · 9 languages".'),
  notes: z
    .array(z.string())
    .default([])
    .describe('2–3 concrete notes (each at most 90 characters); at least one is an improvement opportunity for this source.'),
});

export const SynthOutput = z.object({
  person: SynthPerson,
  mastery: z.object({
    score: z.number().describe('Integer 0–100: how complete they are for their CURRENT role at their level.'),
    insight: z.string().describe('At most 110 characters: the one thing that defines their standing, e.g. strongest asset vs biggest drag.'),
  }),
  domains: z.array(SynthDomain).default([]).describe('4–7 domains, ordered by relevance to the current role.'),
  skills: z.array(SynthSkill).default([]).describe('12–26 skills, 2–5 per domain, including 3–6 gap skills (score < 45).'),
  roles: z.array(SynthRole).default([]).describe('The 4–6 most probable NEXT roles.'),
  sources: z.array(SynthSource).default([]).describe('One entry per successfully read source.'),
});
export type SynthOutput = z.infer<typeof SynthOutput>;
