/**
 * The question each node seeds into the chat (see the "Node actions" board),
 * and the CTA label shown on its popover.
 */
import type { ChatOrigin, Domain, Profile, Role, RoleRequirement, Skill, SourceSummary } from '../../shared/types';
import { rankLabel } from './layout';

export interface NodeAction {
  /** Button label on the popover. */
  cta: string;
  /** The message sent to the chat when the button is pressed. */
  question: string;
  origin: ChatOrigin;
}

export function skillAction(skill: Skill, domainName: string): NodeAction {
  return {
    cta: 'Help me prepare for this topic',
    question: `Help me prepare for ${skill.name}. I'm at ${skill.score}/100 — build me a plan using my own projects.`,
    origin: { kind: 'skill', refId: skill.id, title: skill.name, caption: `${domainName} · ${skill.score}/100` },
  };
}

export function domainAction(domain: Domain): NodeAction {
  return {
    cta: 'Help me prepare for this topic',
    question: `Help me level up in ${domain.name} — I'm at ${domain.score}/100. Where should I focus first, and what should I build?`,
    origin: { kind: 'domain', refId: domain.id, title: domain.name, caption: `Domain · ${domain.score}/100` },
  };
}

export function sourceAction(source: SourceSummary): NodeAction {
  const where = source.url ? ` (${source.url.replace(/^https?:\/\//, '')})` : '';
  let question: string;
  switch (source.kind) {
    case 'github':
      question = `Help me improve my GitHub${where}. What should I fix first, repo by repo?`;
      break;
    case 'resume':
      question = 'Help me improve my resume. What would make it land better for my next role?';
      break;
    case 'lovable':
      question = `Help me improve my Lovable project${where}. How do I turn it into stronger evidence of my skills?`;
      break;
    case 'linkedin':
      question = 'Help me improve my LinkedIn profile. What should my headline and About section say?';
      break;
    default:
      question = `Help me improve my ${source.label}${where}. What would make it stronger evidence of my skills?`;
  }
  return {
    cta: `Help me improve my ${source.kind === 'resume' ? 'resume' : source.label}`,
    question,
    origin: { kind: 'source', refId: source.id, title: source.label, caption: source.sub },
  };
}

/**
 * A role: the question opens a Prepare thread (blocks, not prose). `standing` is the role's place among the
 * person's roles by alignment (`roleStanding` in lib/layout): the caption says "closest of your 5 roles", never a
 * percentage.
 */
export function roleAction(role: Role, standing: { rank: number; count: number }): NodeAction {
  return {
    cta: 'How do I prepare for this role?',
    question: `How do I prepare for the ${role.title} role?`,
    origin: { kind: 'role', refId: role.id, title: role.title, caption: `${rankLabel(standing.rank, standing.count)} · ${role.eta}` },
  };
}

export function requirementAction(req: RoleRequirement, role: Role): NodeAction {
  if (req.have) {
    return {
      cta: 'Help me show this off',
      question: `How do I show off my ${req.name} (${req.current}/100) so it counts for the ${role.title} role?`,
      origin: { kind: 'requirement', refId: req.skillId ?? req.name, title: req.name, caption: `${role.title} · strength` },
    };
  }
  return {
    cta: 'Help me prepare for this topic',
    question: `Help me prepare for ${req.name} for the ${role.title} role. I'm at ${req.current}/100 and the role expects about ${req.target}.`,
    origin: { kind: 'requirement', refId: req.skillId ?? req.name, title: req.name, caption: `${role.title} · gap ${req.current}→${req.target}` },
  };
}

export function youAction(profile: Profile): NodeAction {
  return {
    cta: 'What should I focus on next?',
    question: 'Look at my whole skill graph. What should I focus on next, and why?',
    origin: { kind: 'you', title: profile.person.currentRole, caption: `Role mastery · ${profile.mastery.score}` },
  };
}

export function freeAction(): NodeAction {
  return { cta: 'Ask Leafout', question: '', origin: { kind: 'free', title: 'Open question', caption: 'Ask anything' } };
}
