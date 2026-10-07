"""Skill-term extraction used for the cheap "quick signals" and the live signal cloud.

A curated dictionary of technologies, tools and soft skills is matched against text without any
AI. Matching is word-boundary aware (so "Java" never matches "JavaScript" and "SQL" never matches
"NoSQL") and case-insensitive, except for aliases prefixed with "=", which are case-sensitive
(for words that are also plain English: "Go", "Swift", "Rust", "Spark", "RAG"...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TermDef:
    """One dictionary entry."""

    name: str
    """The canonical spelling that is returned to callers."""
    aliases: tuple[str, ...] = ()
    """Extra spellings. A leading "=" makes that alias case-sensitive."""
    exact: bool = False
    """When true the canonical name (and its plain aliases) is matched case-sensitively."""


def _t(name: str, aliases: tuple[str, ...] | list[str] = (), exact: bool = False) -> TermDef:
    return TermDef(name, tuple(aliases), exact)


TERM_DEFS: tuple[TermDef, ...] = (
    # languages
    _t("TypeScript", ["TS"]),
    _t("JavaScript", ["ECMAScript", "=JS"]),
    _t("Python"),
    _t("Java"),
    _t("Kotlin"),
    _t("Swift", [], True),
    _t("Go", ["Golang"], True),
    _t("Rust", [], True),
    _t("C++", ["=Cpp"]),
    _t("C#", ["CSharp"]),
    _t("Ruby", [], True),
    _t("PHP"),
    _t("Scala", [], True),
    _t("Dart", [], True),
    _t("R language", ["=R programming"]),
    _t("MATLAB"),
    _t("Bash", ["Shell scripting"]),
    _t("SQL"),
    _t("Solidity"),
    _t("Elixir"),
    _t("Haskell"),
    _t("Lua", [], True),
    _t("Julia", [], True),
    _t("Objective-C"),
    _t("HTML", ["HTML5"]),
    _t("CSS", ["CSS3"]),
    # frontend
    _t("React", ["React.js", "ReactJS"]),
    _t("React Native"),
    _t("Next.js", ["NextJS", "Next js"]),
    _t("Vue", ["Vue.js", "VueJS"]),
    _t("Nuxt", ["Nuxt.js"]),
    _t("Angular", ["AngularJS"]),
    _t("Svelte", ["SvelteKit"]),
    _t("Remix", [], True),
    _t("Astro", [], True),
    _t("Redux", ["Redux Toolkit"]),
    _t("Zustand"),
    _t("Tailwind CSS", ["Tailwind", "TailwindCSS"]),
    _t("shadcn/ui", ["shadcn"]),
    _t("Material UI", ["MUI"]),
    _t("Chakra UI"),
    _t("Sass", ["SCSS"]),
    _t("styled-components"),
    _t("Vite"),
    _t("Webpack"),
    _t("Storybook"),
    _t("Framer Motion"),
    _t("Three.js", ["ThreeJS"]),
    _t("D3.js", ["D3"]),
    _t("WebGL"),
    _t("GraphQL"),
    _t("WebSockets", ["WebSocket"]),
    _t("PWA", ["Progressive Web App"]),
    _t("Accessibility", ["WCAG", "a11y"]),
    # backend / frameworks
    _t("Node.js", ["NodeJS", "Node js", "=Node"]),
    _t("Express", ["Express.js", "ExpressJS"], True),
    _t("NestJS", ["Nest.js"]),
    _t("Fastify"),
    _t("Django"),
    _t("Flask"),
    _t("FastAPI"),
    _t("Spring Boot", ["Spring"]),
    _t("Rails", ["Ruby on Rails"], True),
    _t("Laravel"),
    _t(".NET", ["ASP.NET", "dotnet"]),
    _t("gRPC"),
    _t("REST APIs", ["RESTful", "REST API", "=REST"]),
    _t("Microservices", ["Microservice"]),
    _t("Serverless"),
    _t("Kafka"),
    _t("RabbitMQ"),
    _t("Celery"),
    # mobile
    _t("Flutter", [], True),
    _t("Android", [], True),
    _t("iOS", [], True),
    _t("SwiftUI"),
    _t("Jetpack Compose"),
    _t("Expo"),
    # data / ML / AI
    _t("Machine Learning", ["=ML"]),
    _t("Deep Learning"),
    _t("NLP", ["Natural Language Processing"]),
    _t("Computer Vision"),
    _t("LLM", ["LLMs", "Large Language Models", "Large Language Model"]),
    _t("RAG", ["Retrieval-Augmented Generation", "Retrieval Augmented Generation"], True),
    _t("Generative AI", ["GenAI", "Gen AI"]),
    _t("AI", [], True),
    _t("Prompt Engineering"),
    _t("Fine-tuning", ["Fine tuning", "Finetuning"]),
    _t("Embeddings", ["Vector embeddings"]),
    _t("Vector databases", ["Vector DB", "Pinecone", "Weaviate", "Qdrant", "Chroma", "pgvector"]),
    _t("LangChain"),
    _t("LlamaIndex"),
    _t("OpenAI", ["GPT-4", "ChatGPT"]),
    _t("Claude", [], True),
    _t("Hugging Face", ["HuggingFace"]),
    _t("PyTorch"),
    _t("TensorFlow"),
    _t("Keras"),
    _t("scikit-learn", ["sklearn", "scikit learn"]),
    _t("XGBoost"),
    _t("Pandas"),
    _t("NumPy"),
    _t("Jupyter"),
    _t("Spark", ["PySpark", "Apache Spark"], True),
    _t("Airflow"),
    _t("dbt", [], True),
    _t("Snowflake"),
    _t("BigQuery"),
    _t("Databricks"),
    _t("ETL", ["ELT", "Data pipelines", "Data pipeline"]),
    _t("Data Engineering"),
    _t("Data Analysis", ["Data Analytics"]),
    _t("Data Science"),
    _t("Data Visualization", ["Data Viz"]),
    _t("Statistics", ["Statistical analysis"]),
    _t("A/B Testing", ["A/B tests", "AB testing", "Experimentation"]),
    _t("MLOps"),
    _t("Power BI", ["PowerBI"]),
    _t("Tableau"),
    _t("Looker"),
    _t("Excel", ["Google Sheets"], True),
    # cloud / devops
    _t("AWS", ["Amazon Web Services"]),
    _t("GCP", ["Google Cloud", "Google Cloud Platform"]),
    _t("Azure"),
    _t("Docker"),
    _t("Kubernetes", ["=K8s"]),
    _t("Terraform"),
    _t("Ansible"),
    _t("CI/CD", ["Continuous integration", "Continuous delivery", "Continuous deployment"]),
    _t("GitHub Actions"),
    _t("Jenkins"),
    _t("Linux"),
    _t("Nginx"),
    _t("Vercel"),
    _t("Netlify"),
    _t("Cloudflare"),
    _t("Lambda", ["AWS Lambda"], True),
    _t("Observability", ["Datadog", "Grafana", "Prometheus", "Sentry"]),
    _t("DevOps"),
    _t("SRE", ["Site Reliability"]),
    _t("Git"),
    _t("GitHub", [], True),
    _t("GitLab"),
    # databases
    _t("PostgreSQL", ["Postgres"]),
    _t("MySQL"),
    _t("MongoDB", ["Mongo"]),
    _t("Redis"),
    _t("SQLite"),
    _t("DynamoDB"),
    _t("Elasticsearch", ["OpenSearch"]),
    _t("Firebase"),
    _t("Supabase"),
    _t("Prisma"),
    _t("Drizzle"),
    _t("SQLAlchemy"),
    _t("NoSQL"),
    _t("Database design", ["Data modeling", "Data modelling", "Schema design"]),
    # testing / quality
    _t("Jest", [], True),
    _t("Vitest"),
    _t("Cypress"),
    _t("Playwright"),
    _t("Pytest"),
    _t("Selenium"),
    _t("Unit testing", ["Unit tests", "Test-driven development", "TDD"]),
    _t("End-to-end testing", ["E2E testing", "E2E tests"]),
    _t("Code review", ["Code reviews"]),
    # security
    _t("OAuth", ["OAuth2", "OIDC"]),
    _t("Authentication", ["JWT", "SSO"]),
    _t("Cybersecurity", ["Application security", "AppSec", "Penetration testing"]),
    # design / product tools
    _t("Figma"),
    _t("Sketch", [], True),
    _t("Adobe XD"),
    _t("Photoshop"),
    _t("Illustrator"),
    _t("After Effects"),
    _t("Blender", [], True),
    _t("Webflow"),
    _t("Framer", [], True),
    _t("Design Systems", ["Design system"]),
    _t("UI Design", ["UI/UX", "User interface design"]),
    _t("UX Design", ["UX Research", "User research", "Usability testing"]),
    _t("Prototyping", ["Wireframing", "Wireframes"]),
    _t("Motion Design"),
    _t("Branding", ["Brand identity"]),
    _t("Jira"),
    _t("Notion", [], True),
    _t("Linear", [], True),
    _t("Slack", [], True),
    _t("Confluence"),
    _t("Mixpanel"),
    _t("Amplitude"),
    _t("Google Analytics"),
    _t("Salesforce"),
    _t("HubSpot"),
    _t("SEO"),
    _t("Content Marketing", ["Content strategy"]),
    # product / business / process
    _t("Product Management", ["Product Manager"]),
    _t("Product Strategy"),
    _t("Roadmapping", ["Roadmap"]),
    _t("Go-to-market", ["GTM"]),
    _t("Stakeholder Management", ["Stakeholder communication"]),
    _t("Agile", ["Agile methodologies"]),
    _t("Scrum"),
    _t("Kanban"),
    _t("OKRs", ["OKR"]),
    _t("Project Management", ["Program management"]),
    _t("Business Analysis", ["Business analyst"]),
    _t("Financial Modeling", ["Financial modelling", "Financial analysis"]),
    _t("Operations", [], True),
    _t("Customer Success"),
    _t("Sales", [], True),
    _t("Fundraising"),
    _t("Growth", [], True),
    _t("Open Source", ["Open-source"]),
    _t("System Design", ["Systems design", "Software architecture"]),
    _t("Technical Writing", ["Technical documentation"]),
    _t("Public Speaking"),
    # soft skills
    _t("Leadership", ["Led a team", "Team lead"]),
    _t("Mentoring", ["Mentorship", "Mentored", "Coaching"]),
    _t("Communication", ["Communicating"]),
    _t("Collaboration", ["Cross-functional"]),
    _t("Problem Solving", ["Problem-solving"]),
    _t("Ownership"),
    _t("Strategic Thinking"),
    _t("Negotiation"),
    _t("Presentation", ["Presentations"]),
    _t("Time Management"),
    _t("Critical Thinking"),
    _t("Adaptability"),
    _t("Entrepreneurship", ["Founder", "Co-founder", "Cofounder"]),
    _t("Hackathons", ["Hackathon"]),
    _t("Lovable", [], True),
    _t("Vibe coding", ["Vibe-coding"]),
    _t("Stripe", [], True),
    _t("Zod"),
    _t("tRPC"),
)

TERM_COUNT = len(TERM_DEFS)

# --------------------------------------------------------------------------- matcher

_LOOKBEHIND = r"(?<![A-Za-z0-9_])"
_LAST_ALNUM = r"(?![A-Za-z0-9_]|\+\+?(?![A-Za-z0-9])|#)"
_LAST_OTHER = r"(?![A-Za-z0-9_])"
# "Go" is also an English word, so it only counts when it sits in a list of skills (after a list separator
# or at the start of a line, before the next separator or the end of the line). Only HORIZONTAL space may
# sit around it: "\n" is itself a separator, so allowing `\s` here found the same matches but rescanned a
# long run of blank lines from every position in it (quadratic: 40k newlines took about 8 seconds).
_GO_LIST = re.compile(r"(?:^|[,|•·:;(/\n])[^\S\n]*Go[^\S\n]*(?=$|[,|•·;)/\n])", re.MULTILINE)


def _word_regex(source: str, case_sensitive: bool) -> re.Pattern[str]:
    """Word boundary that also understands tokens ending in "+", "#" or "." (C++, C#, Node.js)."""
    body: list[str] = []
    for ch in source:
        if ch == "/":
            body.append(r"\s?/\s?")
        elif ch == "-":
            body.append(r"[\s\-]?")
        elif ch == " ":
            body.append(r"[\s\-]+")
        else:
            body.append(re.escape(ch))
    last = _LAST_ALNUM if source[-1].isascii() and source[-1].isalnum() else _LAST_OTHER
    return re.compile(_LOOKBEHIND + "".join(body) + last, 0 if case_sensitive else re.IGNORECASE)


@dataclass(frozen=True)
class _Matcher:
    name: str
    patterns: tuple[re.Pattern[str], ...]
    # Longest alphanumeric chunk (lower-cased) of each spelling. A text that contains none of them
    # cannot match, which lets most of the ~450 patterns skip the regex scan. Empty = always scan.
    keys: tuple[str, ...]


def _keys_for(sources: list[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for source in sources:
        chunks = re.findall(r"[A-Za-z0-9]+", source)
        if not chunks:
            return ()
        keys.append(max(chunks, key=len).lower())
    return tuple(keys)


def _build_matcher(d: TermDef) -> _Matcher:
    if d.name == "Go":
        return _Matcher(d.name, (_GO_LIST, _word_regex("Golang", False)), ("go",))
    patterns = [_word_regex(d.name, d.exact)]
    sources = [d.name]
    for alias in d.aliases:
        if alias.startswith("="):
            patterns.append(_word_regex(alias[1:], True))
            sources.append(alias[1:])
        else:
            patterns.append(_word_regex(alias, d.exact))
            sources.append(alias)
    return _Matcher(d.name, tuple(patterns), _keys_for(sources))


_MATCHERS: tuple[_Matcher, ...] = tuple(_build_matcher(d) for d in TERM_DEFS)


@dataclass(frozen=True)
class TermHit:
    term: str
    count: int


def count_terms(text: str) -> list[TermHit]:
    """Every dictionary term found in `text`, most frequent first (ties: earliest first)."""
    if not text:
        return []
    lowered = text.lower()
    hits: list[tuple[str, int, int]] = []
    for m in _MATCHERS:
        if m.keys and not any(k in lowered for k in m.keys):
            continue
        # Count distinct positions so "React.js" matching both the name and an alias is one hit.
        positions = {match.start() for rx in m.patterns for match in rx.finditer(text)}
        if positions:
            hits.append((m.name, len(positions), min(positions)))
    hits.sort(key=lambda h: (-h[1], h[2]))
    return [TermHit(term, count) for term, count, _ in hits]


def find_terms(text: str, limit: int = 8) -> list[str]:
    """Top `limit` dictionary terms found in `text`, formatted as the term."""
    return [h.term for h in count_terms(text)[:limit]]
