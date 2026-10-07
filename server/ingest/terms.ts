/**
 * A curated dictionary of technologies, tools and soft skills, used to pull
 * cheap "quick signals" out of text without any AI. Matching is word-boundary
 * aware (so "Java" never matches "JavaScript" and "SQL" never matches
 * "NoSQL") and case-insensitive, except for aliases prefixed with "=", which
 * are case-sensitive (for words that are also plain English: "Go", "Swift",
 * "Rust", "Spark", "RAG"…).
 */

interface TermDef {
  /** The canonical spelling that is returned to callers. */
  name: string;
  /** Extra spellings. A leading "=" makes that alias case-sensitive. */
  aliases?: string[];
  /** When true the canonical name itself is matched case-sensitively. */
  exact?: boolean;
}

const t = (name: string, aliases?: string[], exact = false): TermDef => ({ name, aliases, exact });

const TERM_DEFS: TermDef[] = [
  /* ───── languages ───── */
  t('TypeScript', ['TS']),
  t('JavaScript', ['ECMAScript', '=JS']),
  t('Python'),
  t('Java'),
  t('Kotlin'),
  t('Swift', [], true),
  t('Go', ['Golang'], true),
  t('Rust', [], true),
  t('C++', ['=Cpp']),
  t('C#', ['CSharp']),
  t('Ruby', [], true),
  t('PHP'),
  t('Scala', [], true),
  t('Dart', [], true),
  t('R language', ['=R programming']),
  t('MATLAB'),
  t('Bash', ['Shell scripting']),
  t('SQL'),
  t('Solidity'),
  t('Elixir'),
  t('Haskell'),
  t('Lua', [], true),
  t('Julia', [], true),
  t('Objective-C'),
  t('HTML', ['HTML5']),
  t('CSS', ['CSS3']),

  /* ───── frontend ───── */
  t('React', ['React.js', 'ReactJS']),
  t('React Native'),
  t('Next.js', ['NextJS', 'Next js']),
  t('Vue', ['Vue.js', 'VueJS']),
  t('Nuxt', ['Nuxt.js']),
  t('Angular', ['AngularJS']),
  t('Svelte', ['SvelteKit']),
  t('Remix', [], true),
  t('Astro', [], true),
  t('Redux', ['Redux Toolkit']),
  t('Zustand'),
  t('Tailwind CSS', ['Tailwind', 'TailwindCSS']),
  t('shadcn/ui', ['shadcn']),
  t('Material UI', ['MUI']),
  t('Chakra UI'),
  t('Sass', ['SCSS']),
  t('styled-components'),
  t('Vite'),
  t('Webpack'),
  t('Storybook'),
  t('Framer Motion'),
  t('Three.js', ['ThreeJS']),
  t('D3.js', ['D3']),
  t('WebGL'),
  t('GraphQL'),
  t('WebSockets', ['WebSocket']),
  t('PWA', ['Progressive Web App']),
  t('Accessibility', ['WCAG', 'a11y']),

  /* ───── backend / frameworks ───── */
  t('Node.js', ['NodeJS', 'Node js', '=Node']),
  t('Express', ['Express.js', 'ExpressJS'], true),
  t('NestJS', ['Nest.js']),
  t('Fastify'),
  t('Django'),
  t('Flask'),
  t('FastAPI'),
  t('Spring Boot', ['Spring']),
  t('Rails', ['Ruby on Rails'], true),
  t('Laravel'),
  t('.NET', ['ASP.NET', 'dotnet']),
  t('gRPC'),
  t('REST APIs', ['RESTful', 'REST API', '=REST']),
  t('Microservices', ['Microservice']),
  t('Serverless'),
  t('Kafka'),
  t('RabbitMQ'),
  t('Celery'),

  /* ───── mobile ───── */
  t('Flutter', [], true),
  t('Android', [], true),
  t('iOS', [], true),
  t('SwiftUI'),
  t('Jetpack Compose'),
  t('Expo'),

  /* ───── data / ML / AI ───── */
  t('Machine Learning', ['=ML']),
  t('Deep Learning'),
  t('NLP', ['Natural Language Processing']),
  t('Computer Vision'),
  t('LLM', ['LLMs', 'Large Language Models', 'Large Language Model']),
  t('RAG', ['Retrieval-Augmented Generation', 'Retrieval Augmented Generation'], true),
  t('Generative AI', ['GenAI', 'Gen AI']),
  t('AI', [], true),
  t('Prompt Engineering'),
  t('Fine-tuning', ['Fine tuning', 'Finetuning']),
  t('Embeddings', ['Vector embeddings']),
  t('Vector databases', ['Vector DB', 'Pinecone', 'Weaviate', 'Qdrant', 'Chroma', 'pgvector']),
  t('LangChain'),
  t('LlamaIndex'),
  t('OpenAI', ['GPT-4', 'ChatGPT']),
  t('Claude', [], true),
  t('Hugging Face', ['HuggingFace']),
  t('PyTorch'),
  t('TensorFlow'),
  t('Keras'),
  t('scikit-learn', ['sklearn', 'scikit learn']),
  t('XGBoost'),
  t('Pandas'),
  t('NumPy'),
  t('Jupyter'),
  t('Spark', ['PySpark', 'Apache Spark'], true),
  t('Airflow'),
  t('dbt', [], true),
  t('Snowflake'),
  t('BigQuery'),
  t('Databricks'),
  t('ETL', ['ELT', 'Data pipelines', 'Data pipeline']),
  t('Data Engineering'),
  t('Data Analysis', ['Data Analytics']),
  t('Data Science'),
  t('Data Visualization', ['Data Viz']),
  t('Statistics', ['Statistical analysis']),
  t('A/B Testing', ['A/B tests', 'AB testing', 'Experimentation']),
  t('MLOps'),
  t('Power BI', ['PowerBI']),
  t('Tableau'),
  t('Looker'),
  t('Excel', ['Google Sheets'], true),

  /* ───── cloud / devops ───── */
  t('AWS', ['Amazon Web Services']),
  t('GCP', ['Google Cloud', 'Google Cloud Platform']),
  t('Azure'),
  t('Docker'),
  t('Kubernetes', ['=K8s']),
  t('Terraform'),
  t('Ansible'),
  t('CI/CD', ['Continuous integration', 'Continuous delivery', 'Continuous deployment']),
  t('GitHub Actions'),
  t('Jenkins'),
  t('Linux'),
  t('Nginx'),
  t('Vercel'),
  t('Netlify'),
  t('Cloudflare'),
  t('Lambda', ['AWS Lambda'], true),
  t('Observability', ['Datadog', 'Grafana', 'Prometheus', 'Sentry']),
  t('DevOps'),
  t('SRE', ['Site Reliability']),
  t('Git'),
  t('GitHub', [], true),
  t('GitLab'),

  /* ───── databases ───── */
  t('PostgreSQL', ['Postgres']),
  t('MySQL'),
  t('MongoDB', ['Mongo']),
  t('Redis'),
  t('SQLite'),
  t('DynamoDB'),
  t('Elasticsearch', ['OpenSearch']),
  t('Firebase'),
  t('Supabase'),
  t('Prisma'),
  t('Drizzle'),
  t('SQLAlchemy'),
  t('NoSQL'),
  t('Database design', ['Data modeling', 'Data modelling', 'Schema design']),

  /* ───── testing / quality ───── */
  t('Jest', [], true),
  t('Vitest'),
  t('Cypress'),
  t('Playwright'),
  t('Pytest'),
  t('Selenium'),
  t('Unit testing', ['Unit tests', 'Test-driven development', 'TDD']),
  t('End-to-end testing', ['E2E testing', 'E2E tests']),
  t('Code review', ['Code reviews']),

  /* ───── security ───── */
  t('OAuth', ['OAuth2', 'OIDC']),
  t('Authentication', ['JWT', 'SSO']),
  t('Cybersecurity', ['Application security', 'AppSec', 'Penetration testing']),

  /* ───── design / product tools ───── */
  t('Figma'),
  t('Sketch', [], true),
  t('Adobe XD'),
  t('Photoshop'),
  t('Illustrator'),
  t('After Effects'),
  t('Blender', [], true),
  t('Webflow'),
  t('Framer', [], true),
  t('Design Systems', ['Design system']),
  t('UI Design', ['UI/UX', 'User interface design']),
  t('UX Design', ['UX Research', 'User research', 'Usability testing']),
  t('Prototyping', ['Wireframing', 'Wireframes']),
  t('Motion Design'),
  t('Branding', ['Brand identity']),
  t('Jira'),
  t('Notion', [], true),
  t('Linear', [], true),
  t('Slack', [], true),
  t('Confluence'),
  t('Mixpanel'),
  t('Amplitude'),
  t('Google Analytics'),
  t('Salesforce'),
  t('HubSpot'),
  t('SEO'),
  t('Content Marketing', ['Content strategy']),

  /* ───── product / business / process ───── */
  t('Product Management', ['Product Manager']),
  t('Product Strategy'),
  t('Roadmapping', ['Roadmap']),
  t('Go-to-market', ['GTM']),
  t('Stakeholder Management', ['Stakeholder communication']),
  t('Agile', ['Agile methodologies']),
  t('Scrum'),
  t('Kanban'),
  t('OKRs', ['OKR']),
  t('Project Management', ['Program management']),
  t('Business Analysis', ['Business analyst']),
  t('Financial Modeling', ['Financial modelling', 'Financial analysis']),
  t('Operations', [], true),
  t('Customer Success'),
  t('Sales', [], true),
  t('Fundraising'),
  t('Growth', [], true),
  t('Open Source', ['Open-source']),
  t('System Design', ['Systems design', 'Software architecture']),
  t('Technical Writing', ['Technical documentation']),
  t('Public Speaking'),

  /* ───── soft skills ───── */
  t('Leadership', ['Led a team', 'Team lead']),
  t('Mentoring', ['Mentorship', 'Mentored', 'Coaching']),
  t('Communication', ['Communicating']),
  t('Collaboration', ['Cross-functional']),
  t('Problem Solving', ['Problem-solving']),
  t('Ownership'),
  t('Strategic Thinking'),
  t('Negotiation'),
  t('Presentation', ['Presentations']),
  t('Time Management'),
  t('Critical Thinking'),
  t('Adaptability'),
  t('Entrepreneurship', ['Founder', 'Co-founder', 'Cofounder']),
  t('Hackathons', ['Hackathon']),
  t('Lovable', [], true),
  t('Vibe coding', ['Vibe-coding']),
  t('Stripe', [], true),
  t('Zod'),
  t('tRPC'),
];

/* ───────────────────────── matcher ───────────────────────── */

interface Matcher {
  name: string;
  patterns: RegExp[];
}

const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\\/]/g, '\\$&');

/** Word boundary that also understands tokens ending in "+", "#" or "." (C++, C#, Node.js). */
function wordRegex(source: string, caseSensitive: boolean): RegExp {
  const body = escapeRe(source).replace(/\\\//g, '\\s?\\/\\s?').replace(/-/g, '[\\s\\-]?').replace(/ /g, '[\\s\\-]+');
  const first = '(?<![A-Za-z0-9_])';
  const last = /[A-Za-z0-9]$/.test(source) ? '(?![A-Za-z0-9_]|\\+\\+?(?![A-Za-z0-9])|#)' : '(?![A-Za-z0-9_])';
  return new RegExp(first + body + last, caseSensitive ? 'g' : 'gi');
}

function buildMatcher(def: TermDef): Matcher {
  const patterns: RegExp[] = [];
  // "Go" is also an English word, so only count it when it sits in a list of skills.
  if (def.name === 'Go') {
    patterns.push(/(?:^|[,|•·:;(/\n])\s*Go\s*(?=$|[,|•·;)/\n])/gm);
    patterns.push(wordRegex('Golang', false));
    return { name: def.name, patterns };
  }
  patterns.push(wordRegex(def.name, !!def.exact));
  for (const alias of def.aliases ?? []) {
    if (alias.startsWith('=')) patterns.push(wordRegex(alias.slice(1), true));
    else patterns.push(wordRegex(alias, !!def.exact));
  }
  return { name: def.name, patterns };
}

const MATCHERS: Matcher[] = TERM_DEFS.map(buildMatcher);

export interface TermHit {
  term: string;
  count: number;
}

/** Every dictionary term found in `text`, most frequent first (ties: earliest first). */
export function countTerms(text: string): TermHit[] {
  if (!text) return [];
  const hits: Array<TermHit & { first: number }> = [];
  for (const m of MATCHERS) {
    // Count distinct positions so "React.js" matching both the name and an alias is one hit.
    const positions = new Set<number>();
    for (const re of m.patterns) {
      re.lastIndex = 0;
      for (const match of text.matchAll(re)) positions.add(match.index ?? 0);
    }
    if (positions.size > 0) hits.push({ term: m.name, count: positions.size, first: Math.min(...positions) });
  }
  hits.sort((a, b) => b.count - a.count || a.first - b.first);
  return hits.map(({ term, count }) => ({ term, count }));
}

/** Top `limit` dictionary terms found in `text`, formatted as the term. */
export function findTerms(text: string, limit = 8): string[] {
  return countTerms(text)
    .slice(0, limit)
    .map((h) => h.term);
}

export const TERM_COUNT = TERM_DEFS.length;
