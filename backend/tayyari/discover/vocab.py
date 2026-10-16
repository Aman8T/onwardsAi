"""The skill vocabulary Discover reads postings with: about 400 skill terms across professions, each with aliases.

A posting "names" a skill when one of its terms appears on word boundaries. The vocabulary is deliberately concrete
(``Financial modelling``, ``Tally``, ``Patient care``, ``React``), not generic (no "communication" or "teamwork": every
posting says those). A term that is also an everyday word is left out or spelt with context: Go is matched as ``golang``,
Excel as ``Microsoft Excel``, Swift as ``SwiftUI``. A name written ``!Name`` is shown as ``Name`` but is not itself matched.

The matcher is one compiled pattern built from a trie of every term, so a posting is read in a single pass. Text is
compared in lower case with hyphens read as spaces (``front-end`` and ``front end`` are one term); that mapping is one
character to one character, so positions in the prepared text are positions in the original.

No model is involved. A person's own skill names are added at request time as extra terms (`TermMatcher`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from tayyari.core.text import slug

_RAW: Final = """
## engineering
JavaScript | javascript, js, ecmascript, es6
TypeScript | typescript
Python | python3, python 3
Java | core java, j2ee, jdk
Kotlin | kotlin
!Swift | swiftui, swift programming, swift language
!Go | golang, go programming
Rust | rust
C++ | c++, cpp
C# | c#, csharp
!C | c programming, c language, embedded c
PHP | php
Ruby | ruby
Scala | scala
!R | r programming, r language, rstudio
MATLAB | matlab
Perl | perl
Dart | dart
Shell scripting | bash, shell scripting, shell script, bash scripting
SQL | sql, t-sql, pl/sql
HTML | html, html5
CSS | css, css3
Objective-C | objective-c
Elixir | elixir
React | react, reactjs, react.js
Next.js | next.js, nextjs
Vue.js | vue, vue.js, vuejs
Angular | angular, angularjs, angular.js
Svelte | svelte, sveltekit
Redux | redux
jQuery | jquery
Tailwind CSS | tailwind, tailwind css
Sass | sass, scss
Webpack | webpack
Vite | vite
Storybook | storybook
Web accessibility | accessibility, a11y, wcag, aria
Responsive design | responsive design, responsive web design, mobile first
Progressive web apps | pwa, progressive web app, progressive web apps
GraphQL | graphql
Web performance | web performance, core web vitals, page speed
Node.js | node.js, nodejs, node js
Express.js | express.js, expressjs
Django | django
Flask | flask
FastAPI | fastapi
Spring Boot | spring boot, spring framework, spring mvc
.NET | .net, dotnet, .net core, asp.net, asp.net core
Ruby on Rails | rails, ruby on rails
Laravel | laravel
REST APIs | rest api, rest apis, restful, restful api, restful apis, rest services, api design, api development
gRPC | grpc, protobuf, protocol buffers
Microservices | microservices, microservice, service oriented architecture
WebSockets | websocket, websockets
System design | system design, software architecture, solution architecture
Distributed systems | distributed systems, distributed computing
Event-driven architecture | event driven architecture, event driven, event sourcing
Serverless | serverless, aws lambda, cloud functions
Object-oriented design | oop, object oriented programming, object oriented design, design patterns
Data structures and algorithms | data structures, algorithms
Code review | code review, code reviews, pull requests
Technical leadership | technical leadership, tech lead, technical mentoring
## data stores
PostgreSQL | postgresql, postgres
MySQL | mysql, mariadb
MongoDB | mongodb, mongo
Redis | redis, memcached
DynamoDB | dynamodb
Cassandra | cassandra
Oracle Database | oracle database, oracle db, oracle 19c
SQL Server | sql server, mssql, microsoft sql server
SQLite | sqlite
NoSQL | nosql
Elasticsearch | elasticsearch, opensearch, solr
Kafka | kafka, apache kafka
RabbitMQ | rabbitmq, message queues, sqs
Snowflake | snowflake
BigQuery | bigquery
Redshift | redshift
Databricks | databricks
Data warehousing | data warehouse, data warehousing, data warehouses
ETL | etl, elt, data integration
Prisma | prisma
## cloud and devops
AWS | aws, amazon web services, ec2, s3
Azure | azure, microsoft azure
Google Cloud | gcp, google cloud, google cloud platform
Docker | docker, containers, containerization
Kubernetes | kubernetes, k8s, helm
Terraform | terraform, infrastructure as code, iac, cloudformation
Ansible | ansible
Jenkins | jenkins
GitHub Actions | github actions, gitlab ci
CI/CD | ci/cd, cicd, continuous integration, continuous delivery, continuous deployment
Linux | linux, unix
Nginx | nginx
Git | git, github, gitlab, bitbucket, version control
Observability | observability, prometheus, grafana, datadog, new relic, monitoring and alerting
Site reliability | sre, site reliability, reliability engineering
Incident response | incident response, incident management, on call, on-call, postmortems
Cloud networking | vpc, load balancing, load balancers, dns, cdn
Cloud cost management | finops, cloud cost optimization, cost optimization
## mobile
Android | android, android sdk
iOS | ios, ios development, cocoa
React Native | react native
Flutter | flutter
Jetpack Compose | jetpack compose
Mobile development | mobile development, mobile app development, mobile apps
## data and ai
Machine learning | machine learning, ml, ml models, ai/ml
Deep learning | deep learning, neural networks
NLP | nlp, natural language processing
Computer vision | computer vision, image recognition
PyTorch | pytorch
TensorFlow | tensorflow, keras
scikit-learn | scikit-learn, sklearn
Pandas | pandas
NumPy | numpy
Apache Spark | apache spark, pyspark, spark sql
Airflow | airflow, apache airflow
dbt | dbt
Data engineering | data engineering, data pipelines, data pipeline
Data modelling | data modelling, data modeling, dimensional modelling, dimensional modeling
MLOps | mlops, model deployment, model serving
LLMs | llm, llms, large language models, large language model, gpt, openai api
Prompt engineering | prompt engineering, prompt design, prompting
RAG | rag, retrieval augmented generation, vector databases, vector database, embeddings
Generative AI | generative ai, genai, gen ai
Artificial intelligence | artificial intelligence
Statistics | statistics, statistical analysis, statistical modelling, statistical modeling, hypothesis testing
A/B testing | a/b testing, a/b tests, ab testing, split testing
Experimentation | experimentation, experiment design
Data analysis | data analysis, data analytics, exploratory data analysis
Data visualization | data visualization, data visualisation, dashboarding
Tableau | tableau
Power BI | power bi, powerbi
Looker | looker, looker studio, data studio
Microsoft Excel | microsoft excel, ms excel, advanced excel, excel formulas, excel spreadsheets, vlookup, pivot tables
Google Analytics | google analytics, ga4
Predictive modelling | predictive modelling, predictive modeling, forecasting models, time series
Recommendation systems | recommendation systems, recommender systems, ranking models
Jupyter | jupyter, jupyter notebooks
SAS | sas
SPSS | spss
Hadoop | hadoop, hdfs
Business intelligence | business intelligence, bi tools
## quality
Test automation | test automation, automated testing, automation testing, automated tests
Unit testing | unit testing, unit tests
Integration testing | integration testing, integration tests, end to end testing, e2e testing
Jest | jest, react testing library, vitest, mocha
Cypress | cypress
Selenium | selenium, webdriver
Playwright | playwright
pytest | pytest
JUnit | junit, testng
Manual testing | manual testing, exploratory testing, test cases
Performance testing | performance testing, load testing, jmeter, k6
API testing | api testing, postman
Quality assurance | quality assurance, qa, software testing
Test planning | test planning, test strategy, test plans
## security
Application security | application security, appsec, secure coding, owasp
Penetration testing | penetration testing, pentesting, ethical hacking, red team
Network security | network security, firewalls, firewall
SIEM | siem, splunk, security operations, soc analyst
Identity and access management | identity and access management, iam, sso, oauth, active directory
Cryptography | cryptography, encryption
Vulnerability management | vulnerability management, vulnerability assessment
Cloud security | cloud security
Security compliance | soc 2, iso 27001, pci dss, grc, security audits
## hardware and embedded
Embedded systems | embedded systems, embedded software, microcontrollers, arduino
Firmware | firmware, rtos, device drivers
PCB design | pcb design, pcb layout, altium, circuit design
FPGA | fpga, verilog, vhdl, rtl design
CAD | cad, autocad, solidworks, catia, creo
PLC programming | plc, plc programming, scada, hmi
Robotics | robotics, ros
IoT | iot, internet of things
Mechanical design | mechanical design, gd&t, product design engineering
Simulation | simulation, ansys, simulink, finite element analysis, fea
Electrical engineering | electrical engineering, power systems, electrical design
## design
UX design | ux design, user experience design, user experience, ux
UI design | ui design, user interface design, visual interface design
Figma | figma, figjam
Adobe XD | adobe xd
Adobe Photoshop | photoshop, adobe photoshop
Adobe Illustrator | illustrator, adobe illustrator
Adobe InDesign | indesign, adobe indesign
After Effects | after effects, motion graphics
Prototyping | prototyping, interactive prototypes, prototypes
User research | user research, ux research, user interviews, customer interviews
Usability testing | usability testing, usability studies
Wireframing | wireframing, wireframes
Design systems | design systems, design system, component libraries, component library
Interaction design | interaction design, ixd
Motion design | motion design, animation, animations
Information architecture | information architecture, ia
Visual design | visual design, graphic design
Brand design | brand design, brand identity, branding
Typography | typography
3D modelling | 3d modelling, 3d modeling, blender, maya, 3d design
Video editing | video editing, premiere pro, final cut, davinci resolve
Illustration | illustration, digital illustration
Design thinking | design thinking
Service design | service design
Figma-to-code | design to code, design handoff, design handoffs
## product and project
Product management | product management, product manager, product strategy, product ownership
Roadmapping | roadmapping, product roadmap, roadmap planning
User stories | user stories, acceptance criteria, prds, prd, product requirements
Backlog management | backlog management, backlog grooming, backlog prioritization, sprint planning
Product analytics | product analytics, mixpanel, amplitude, funnel analysis, cohort analysis
Customer discovery | customer discovery, discovery research
Go-to-market | go to market, gtm, product launches, product launch
Pricing strategy | pricing strategy, pricing and packaging
Stakeholder management | stakeholder management, stakeholder communication, cross functional collaboration
Agile | agile, agile methodologies
Scrum | scrum, scrum master
Kanban | kanban
Jira | jira, confluence
OKRs | okrs, okr
Market research | market research, market analysis
Competitive analysis | competitive analysis, competitor analysis
Project management | project management, project planning, project delivery, pmp, prince2
Program management | program management, programme management
Risk management | risk management, risk assessment
Change management | change management
Business analysis | business analysis, business analyst, requirements gathering, requirement analysis
Process improvement | process improvement, process optimization, process optimisation, continuous improvement, kaizen
!Strategy | business strategy, strategic planning
## marketing
SEO | seo, search engine optimization, search engine optimisation, on page seo, technical seo
SEM | sem, search engine marketing, ppc, pay per click
Google Ads | google ads, adwords
Meta Ads | meta ads, facebook ads, instagram ads
Performance marketing | performance marketing, paid media, paid acquisition, paid social
Content marketing | content marketing
Email marketing | email marketing, email campaigns, newsletters
Marketing automation | marketing automation, hubspot, marketo, mailchimp, clevertap, moengage
Social media marketing | social media marketing, social media management, social media, community management
Influencer marketing | influencer marketing, influencer partnerships
Brand management | brand management, brand strategy, brand marketing
Growth marketing | growth marketing, growth hacking, user acquisition
Lifecycle marketing | lifecycle marketing, retention marketing, crm marketing
CRM | crm, customer relationship management
Marketing analytics | marketing analytics, attribution
Campaign management | campaign management, marketing campaigns
Public relations | public relations, pr, media relations, press releases, press release
Affiliate marketing | affiliate marketing
App store optimization | aso, app store optimization
Conversion rate optimization | cro, conversion rate optimization, conversion optimization
Demand generation | demand generation, demand gen, lead nurturing
Product marketing | product marketing
Content strategy | content strategy, editorial calendar
Digital marketing | digital marketing, online marketing
Programmatic advertising | programmatic advertising, programmatic, dsp
Account-based marketing | account based marketing, abm
Event marketing | event marketing, event management
## sales
B2B sales | b2b sales, enterprise sales, saas sales
Inside sales | inside sales, telesales, tele sales
Field sales | field sales, outside sales
Account management | account management, account manager, client management
Lead generation | lead generation, lead gen, prospecting, outbound sales, outbound
Cold calling | cold calling, cold calls, cold emailing
Salesforce | salesforce, sfdc
Negotiation | negotiation, negotiating contracts
Sales forecasting | sales forecasting, pipeline management, sales pipeline
Channel sales | channel sales, channel partners, distributor management, dealer management
Key account management | key account management, key accounts
Sales operations | sales operations, sales ops, revenue operations, revops
Solution selling | solution selling, consultative selling
Business development | business development
Customer success | customer success, customer retention, churn reduction
Upselling | upselling, cross selling, cross-selling
Retail sales | retail sales, store operations, retail operations
Territory management | territory management, territory planning
Sales enablement | sales enablement, sales training
RFP response | rfp, rfps, tender, tenders, proposal writing, bid management
## finance and accounting
Financial reporting | financial reporting, financial statements, management reporting
Financial analysis | financial analysis, variance analysis
Financial modelling | financial modelling, financial modeling, dcf, three statement model
Budgeting | budgeting, budget management, budget planning
FP&A | fp&a, financial planning and analysis, financial planning
Accounts payable | accounts payable, ap process, vendor payments
Accounts receivable | accounts receivable, collections, billing and collections
General ledger | general ledger, journal entries
Reconciliation | reconciliation, reconciliations, bank reconciliation
Month-end close | month end close, month end, period close, financial close
Audit | audit, auditing, internal audit, statutory audit, internal controls
Taxation | taxation, income tax, direct tax, indirect tax, tax compliance, transfer pricing
GST | gst, gst returns, gstr
TDS | tds, tds returns
Tally | tally erp, tally prime, tally erp 9
SAP | sap, sap erp, sap fico, sap s/4hana
Oracle ERP | oracle erp, oracle fusion, oracle financials, netsuite
QuickBooks | quickbooks, xero, zoho books
Ind AS | ind as, indian accounting standards
IFRS | ifrs
GAAP | gaap, us gaap
Treasury | treasury, cash management, liquidity management
Cash flow management | cash flow, cash flow management, working capital
Credit analysis | credit analysis, credit appraisal, credit underwriting, credit risk
Investment analysis | investment analysis, investment research, equity research
Valuation | valuation, valuations, mergers and acquisitions, m&a
Cost accounting | cost accounting, costing, cost control
Bookkeeping | bookkeeping, book keeping
Regulatory compliance | regulatory compliance, rbi, sebi, irdai, compliance monitoring
KYC and AML | kyc, aml, anti money laundering, know your customer
Banking operations | banking operations, retail banking, branch banking, loan processing
Trade finance | trade finance, letters of credit
Insurance underwriting | insurance underwriting, underwriting, claims processing, claims management
Wealth management | wealth management, financial advisory, portfolio management, mutual funds
Payroll | payroll, payroll processing
Personal financial planning | personal finance, retirement planning
## people and hr
Talent acquisition | talent acquisition, recruitment, recruiting, end to end recruitment
Candidate sourcing | candidate sourcing, talent sourcing, headhunting, boolean search
Interviewing | interviewing, candidate screening, campus hiring
Employee onboarding | employee onboarding, induction
Employee engagement | employee engagement, employee experience
Performance management | performance management, performance reviews, appraisal, appraisals
Compensation and benefits | compensation and benefits, c&b, total rewards
HRIS | hris, workday, successfactors, hr systems
Employee relations | employee relations, grievance handling, industrial relations
Learning and development | learning and development, l&d, employee training, training and development
HR business partnering | hr business partner, hrbp, business partnering
Labour law | labour law, labor law, labour laws, statutory compliance, pf and esi
Succession planning | succession planning, talent management
Diversity and inclusion | diversity and inclusion, dei
Organisational development | organisational development, organizational development
Workforce planning | workforce planning, headcount planning, manpower planning
Applicant tracking systems | applicant tracking system, applicant tracking systems, ats
HR operations | hr operations, hr administration, hr policies
## operations and supply chain
Supply chain management | supply chain management, supply chain, scm
Logistics | logistics, freight, transportation management
Procurement | procurement, purchasing, strategic sourcing
Vendor management | vendor management, supplier management, vendor development
Inventory management | inventory management, inventory control, stock control
Warehouse management | warehouse management, warehouse operations, wms
Demand planning | demand planning, supply planning, s&op
Production planning | production planning, production scheduling, ppc planning
Quality control | quality control, quality inspection
Lean manufacturing | lean manufacturing, lean, 5s
Six Sigma | six sigma, green belt, black belt
Operations management | operations management, operational excellence
ERP systems | erp, erp systems, erp implementation
Fleet management | fleet management, fleet operations
Last-mile delivery | last mile delivery, last mile, delivery operations
Import and export | import export, exim, customs clearance, customs compliance
Health and safety | health and safety, ehs, hse, workplace safety
Maintenance planning | maintenance planning, preventive maintenance, plant maintenance, maintenance management
Facilities management | facilities management, facility management
Order fulfilment | order fulfilment, order fulfillment, order processing
ISO 9001 | iso 9001, quality management system, qms
Process engineering | process engineering, process design
Manufacturing | manufacturing, shop floor, assembly line
Cost reduction | cost reduction, cost savings, cost optimisation
## customer support
Customer support | customer support, customer service, customer care, client support
Technical support | technical support, tech support, l1 support, l2 support, application support
Ticketing systems | zendesk, freshdesk, servicenow, ticketing system, ticketing systems, help desk, helpdesk
Live chat support | live chat, chat support, chat and email support
Escalation handling | escalation handling, escalations, issue escalation
Call centre operations | call centre, call center, bpo, voice process, non voice process, inbound calls
Complaint resolution | complaint resolution, complaint handling, query resolution
Customer onboarding | customer onboarding, client onboarding
SLA management | sla, slas, sla management
Knowledge base | knowledge base, knowledge base articles, help center content
Customer satisfaction | csat, nps, customer satisfaction
Troubleshooting | troubleshooting, root cause analysis, debugging
## healthcare
Patient care | patient care, bedside care, patient assessment
Clinical documentation | clinical documentation, medical records, case notes
Nursing | nursing, registered nurse, staff nurse, bls, acls
Medical coding | medical coding, icd 10, icd-10, cpt coding
Medical billing | medical billing, revenue cycle management, rcm
Pharmacy | pharmacy, pharmacist, dispensing
Clinical research | clinical research, clinical trials, good clinical practice, gcp guidelines
EHR systems | ehr, emr, electronic health records, epic systems
Phlebotomy | phlebotomy, sample collection
Public health | public health, community health, epidemiology
Medical devices | medical devices, medical device
Healthcare compliance | hipaa, nabh, healthcare compliance, jci
Radiology | radiology, x ray, mri, ct scan
Physiotherapy | physiotherapy, physical therapy, rehabilitation
Pharmacovigilance | pharmacovigilance, drug safety
Laboratory diagnostics | laboratory diagnostics, pathology, lab testing, clinical laboratory
Patient counselling | patient counselling, patient counseling, health education
Infection control | infection control, infection prevention
Healthcare administration | healthcare administration, hospital administration, hospital operations
Mental health counselling | counselling, counseling, psychotherapy, mental health
Dental care | dental, dentistry, dental hygiene
Telemedicine | telemedicine, telehealth
## education
Teaching | teaching, classroom teaching, lecturing
Curriculum design | curriculum design, curriculum development, syllabus design
Lesson planning | lesson planning, lesson plans
Classroom management | classroom management
Instructional design | instructional design, learning design, learning experience design
E-learning | e learning, elearning, lms, moodle, articulate storyline, course authoring
Student assessment | student assessment, assessments, grading, evaluation of students
Tutoring | tutoring, mentoring students, one on one teaching
Educational technology | edtech, educational technology
Training delivery | training delivery, corporate training, facilitation, train the trainer
Academic advising | academic advising, student counselling, career counselling
Special education | special education, special needs, inclusive education
Early childhood education | early childhood education, preschool, montessori
Academic research | academic research, research methodology
!Coaching | executive coaching, career coaching
## legal
Contract drafting | contract drafting, drafting agreements, drafting contracts, legal drafting
Contract review | contract review, contract management, contract negotiation
Legal research | legal research
Litigation | litigation, civil litigation, court appearances, dispute resolution, arbitration
Corporate law | corporate law, corporate governance, company law, company secretarial
Intellectual property | intellectual property, patent, patents
Data privacy | data privacy, gdpr, dpdp, privacy compliance
Employment law | employment law
Due diligence | due diligence, legal due diligence
Paralegal support | paralegal, legal assistance, legal documentation
Regulatory affairs | regulatory affairs, regulatory filings
## content and communications
Content writing | content writing, content creation, article writing, blog writing, blogging
Copywriting | copywriting, ad copy, marketing copy
Editing | editing, copy editing, copyediting, proofreading
Technical writing | technical writing, technical documentation, api documentation
Journalism | journalism, news reporting, news writing
UX writing | ux writing, microcopy, content design
Storytelling | storytelling
Video production | video production, videography, filmmaking
Photography | photography, photo editing, lightroom
Podcasting | podcast, podcasting, audio editing
Scriptwriting | scriptwriting, screenwriting
Translation | translation, localization, localisation, transcreation
Public speaking | public speaking, presentation skills
Communications strategy | communications strategy, corporate communications, internal communications
Social media content | social media content, content calendar
Microsoft Office | microsoft office, ms office, powerpoint, microsoft word, ms word
"""

_PREP_MAP: Final = str.maketrans({"-": " ", "‐": " ", "‑": " ", "–": " ", "—": " "})
_BOUNDARY_BEFORE: Final = r"(?<![a-z0-9])"
_BOUNDARY_AFTER: Final = r"(?![a-z0-9])"


def prep(text: str) -> str:
    """The form text is matched in: lower case, hyphens and dashes read as spaces. One character in, one out, so a
    position in the result is the same position in ``text``."""
    low = text.lower()
    if len(low) != len(
        text
    ):  # a few characters change length when lowered; keep positions by lowering one by one
        low = "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
    return low.translate(_PREP_MAP)


def prep_term(term: str) -> str:
    """A search term in matching form: `prep` with runs of spaces collapsed and the ends trimmed."""
    return " ".join(prep(term).split())


def _trie_regex(words: list[str]) -> str:
    """One regular expression for all ``words`` (a trie, so the engine does not try every word at every position)."""
    root: dict[str, Any] = {}
    for word in words:
        node = root
        for char in word:
            node = node.setdefault(char, {})
        node[""] = {}

    def emit(node: dict[str, Any]) -> str:
        has_end = "" in node
        branches = [re.escape(char) + emit(child) for char, child in sorted(node.items()) if char != ""]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "|".join(branches)
        if len(branches) == 1 and not has_end:
            return body
        return f"(?:{body})" + ("?" if has_end else "")

    return emit(root)


class TermMatcher:
    """Finds a set of terms in prepared text, on word boundaries, longest term first.

    ``terms`` maps a term (already `prep_term`'d) to the id it stands for. `scan` yields ``(id, position)`` for every
    appearance.
    """

    def __init__(self, terms: Mapping[str, str]) -> None:
        self._ids = dict(terms)
        self._pattern: re.Pattern[str] | None = None
        if self._ids:
            self._pattern = re.compile(
                _BOUNDARY_BEFORE + "(?:" + _trie_regex(sorted(self._ids)) + ")" + _BOUNDARY_AFTER
            )

    def __len__(self) -> int:
        return len(self._ids)

    def scan(self, prepared: str) -> Iterator[tuple[str, int]]:
        if self._pattern is None:
            return
        for match in self._pattern.finditer(prepared):
            yield self._ids[match.group(0)], match.start()


@dataclass(frozen=True, slots=True)
class SkillDef:
    id: str
    name: str
    family: str


def _parse(raw: str) -> tuple[dict[str, SkillDef], dict[str, str]]:
    skills: dict[str, SkillDef] = {}
    terms: dict[str, str] = {}
    family = ""
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("## "):
            family = line[3:].strip()
            continue
        name_part, _, alias_part = line.partition("|")
        name = name_part.strip()
        matched_name = not name.startswith("!")
        name = name.lstrip("!").strip()
        skill_id = slug(name)
        if skill_id in skills:
            raise ValueError(f"duplicate skill {name!r}")
        skills[skill_id] = SkillDef(skill_id, name, family)
        words = [a for a in (part.strip() for part in alias_part.split(",")) if a]
        if matched_name:
            words.append(name)
        for word in words:
            term = prep_term(word)
            if not term:
                continue
            if terms.get(term, skill_id) != skill_id:
                raise ValueError(f"the term {term!r} belongs to {terms[term]!r} and {skill_id!r}")
            terms[term] = skill_id
    return skills, terms


GENERAL: Final = (
    "Git",
    "Agile",
    "Scrum",
    "Kanban",
    "Jira",
    "Code review",
    "Artificial intelligence",
    "Diversity and inclusion",
)
"""Skills nearly every posting in a field lists, or that come from boilerplate (equal-opportunity statements): they say
little about whether a role fits, so the quick fit does not count them, as asked or as missing."""

ANY_OF: Final[dict[str, tuple[str, ...]]] = {
    "frontend-frameworks": ("React", "Angular", "Vue.js", "Svelte"),
    "cloud-platforms": ("AWS", "Azure", "Google Cloud"),
    "relational-databases": ("PostgreSQL", "MySQL", "Oracle Database", "SQL Server"),
    "document-databases": ("MongoDB", "DynamoDB", "Cassandra"),
    "message-queues": ("Kafka", "RabbitMQ"),
    "bi-tools": ("Tableau", "Power BI", "Looker"),
    "accounting-software": ("Tally", "QuickBooks", "SAP", "Oracle ERP"),
    "cross-platform-mobile": ("Flutter", "React Native"),
    "ci-servers": ("Jenkins", "GitHub Actions"),
}
"""Skills postings list as alternatives ("React, Angular or Vue"). The ones a posting names count as one ask, met when
the profile shows any of them; they are shown together ("Angular or Vue.js") when none is."""

IMPLIES: Final[dict[str, tuple[str, ...]]] = {
    "TypeScript": ("JavaScript",),
    "React": ("JavaScript", "HTML", "CSS"),
    "Next.js": ("React", "JavaScript", "HTML", "CSS"),
    "Vue.js": ("JavaScript", "HTML", "CSS"),
    "Angular": ("TypeScript", "JavaScript", "HTML", "CSS"),
    "Svelte": ("JavaScript", "HTML", "CSS"),
    "React Native": ("React", "JavaScript"),
    "Node.js": ("JavaScript",),
    "Django": ("Python",),
    "Flask": ("Python",),
    "FastAPI": ("Python",),
    "Spring Boot": ("Java",),
    "PostgreSQL": ("SQL",),
    "MySQL": ("SQL",),
    "SQL Server": ("SQL",),
    "Oracle Database": ("SQL",),
    "Tailwind CSS": ("CSS",),
    "Sass": ("CSS",),
    "Kubernetes": ("Docker",),
    "Jest": ("Unit testing",),
}
"""A skill a profile shows that plainly includes another: TypeScript is JavaScript with types, Next.js is built on React.
Only inclusions nobody would argue with; a shown skill never implies a neighbouring one (React does not imply Angular)."""


class Vocabulary:
    """The skills, their terms, and a matcher over all of them, with the three lists the quick fit counts by."""

    def __init__(self, raw: str = _RAW) -> None:
        self.skills, self.terms = _parse(raw)
        self.matcher = TermMatcher(self.terms)
        known = raw == _RAW

        def ids(names: tuple[str, ...]) -> tuple[str, ...]:
            found = tuple(slug(name) for name in names)
            if known:
                unknown = [
                    name for name, skill_id in zip(names, found, strict=True) if skill_id not in self.skills
                ]
                if unknown:
                    raise ValueError(f"not in the vocabulary: {unknown}")
            return tuple(skill_id for skill_id in found if skill_id in self.skills)

        self.general: frozenset[str] = frozenset(ids(GENERAL))
        self.group_of: dict[str, str] = {}
        self.group_members: dict[str, tuple[str, ...]] = {}
        for key, names in ANY_OF.items():
            members = ids(names)
            self.group_members[f"any:{key}"] = members
            for member in members:
                self.group_of[member] = f"any:{key}"
        self.implied_by: dict[str, tuple[str, ...]] = {}
        for source_name, target_names in IMPLIES.items():
            for source in ids((source_name,)):
                for target in ids(target_names):
                    self.implied_by[target] = (*self.implied_by.get(target, ()), source)

    def name_of(self, skill_id: str) -> str | None:
        found = self.skills.get(skill_id)
        return None if found is None else found.name

    def lookup(self, term: str) -> str | None:
        """The skill a term stands for (``Postgres`` is PostgreSQL), or None. A person who writes the bare name of a
        skill the matcher only knows in context (``Go``, ``R``) is understood too: those names are not matched in
        postings, but a profile may use them."""
        prepared = prep_term(term)
        found = self.terms.get(prepared)
        if found is not None:
            return found
        skill_id = slug(prepared)
        known = self.skills.get(skill_id)
        return skill_id if known is not None and prep_term(known.name) == prepared else None

    def scan(self, prepared: str) -> Iterator[tuple[str, int]]:
        return self.matcher.scan(prepared)


@lru_cache(maxsize=1)
def vocabulary() -> Vocabulary:
    """The shared vocabulary (compiled once per process)."""
    return Vocabulary()


__all__ = [
    "ANY_OF",
    "GENERAL",
    "IMPLIES",
    "SkillDef",
    "TermMatcher",
    "Vocabulary",
    "prep",
    "prep_term",
    "vocabulary",
]
