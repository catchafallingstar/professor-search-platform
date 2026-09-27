# Road to Research
Live on : https://preview-jac-sbx-38b138156c9546d881e84ebecc5fd447.jachammer.app/

**Helping students find professors, understand their recent research, discover funding, and identify current research opportunities in one place.**

Road to Research is a professor discovery platform built for **JacHacks A2Tech360** using **Jac, JacHammer, OpenAlex, and AI-assisted research**.

## Links

- **Presentation / Intro Slides:** [Road to Research on Canva](https://canva.link/kha6sadbovr6n2u)
- **Repository:** [github.com/catchafallingstar/professor-search-platform](https://github.com/catchafallingstar/professor-search-platform)

## Intro Slides

Our presentation introduces the problem behind Road to Research, the workflow we designed to solve professor identity matching, and how Jac, OpenAlex, and AI work together in the platform.

The slides cover:

- Why finding research opportunities is difficult for students
- Why professor identity matching is harder than simply searching a name
- Our university-first faculty discovery workflow
- Matching faculty to OpenAlex using institution and a known publication when available
- Recent paper and research-subfield discovery
- Grant discovery through OpenAlex awards
- AI-assisted hiring research
- Why Jac's graph model fits universities, professors, papers, subfields, grants, and hiring signals
- Deployment through JacHammer

**Slides:** [Open the Road to Research presentation](https://canva.link/kha6sadbovr6n2u)

---

## The Problem

Finding a research professor is harder than it should be.

A student may need to search a university directory, visit individual faculty pages, search publication databases, inspect grant information, and then search lab websites to see whether a professor is recruiting students.

Most of this information already exists, but it is scattered across different systems.

**Road to Research brings those pieces together.**

---

## What It Does

Road to Research lets students search professors by:

- University
- Department
- Recent research field or subfield
- Grant availability
- Whether a public hiring statement was found

Each professor profile can include:

- Name, title, university, and department
- Official faculty and lab links
- Recent publications
- Research subfields from recent papers
- Grant information
- An explicit hiring/recruiting quote and its source, when available

---

## How It Works

```text
University
   ↓
Official Faculty Directory
   ↓
Professor
   ↓
Faculty Profile
   ↓
Known publication / DOI if available
   ↓
OpenAlex identity match
   ↓
Stable OpenAlex Author ID
   ↓
Recent papers
   ↓
Research subfields
   ↓
Awards / grants
   ↓
AI hiring research
   ↓
Professor profile
```

### 1. Start with the university

We begin with official university faculty pages instead of searching arbitrary researcher names.

The university gives us:

- Professor name
- Academic title
- Department
- Faculty profile URL
- Lab or personal site when available
- ORCID when available
- Up to three publication anchors when available

The university is our source for **who the professor is and where they currently work**.

### 2. Match the professor to OpenAlex

Professor identity matching was one of the hardest parts of the project.

Searching only by name can return the wrong researcher, especially for common names. We therefore use the strongest available identity anchor:

1. **Known DOI from the faculty page**
2. **Known paper title from the faculty page**
3. **Professor name + institution**

When a faculty page gives us a known paper, we resolve that work in OpenAlex and find the professor in its authorship list.

If no paper is available, we fall back to name + institution.

If the result is still ambiguous, we leave the professor unresolved instead of attaching the wrong research record.

Once matched, we save the **OpenAlex Author ID** and use that stable identifier for future requests.

### 3. Retrieve recent papers and subfields

Using the OpenAlex Author ID, we retrieve papers from the most recent five-year period.

For each paper we store information such as:

- OpenAlex Work ID
- DOI
- Title
- Publication date
- Journal or conference
- Citation count
- OpenAlex research subfield

The professor profile displays the distinct subfields represented by those recent papers.

The **department** comes from the university.

The **research subfields** come from the professor's recent publications.

### 4. Discover grants

While retrieving papers, we also inspect OpenAlex award links.

When an award is found, we fetch the award record and check whether the professor is actually listed as:

- PI
- Co-PI
- Investigator

This prevents us from claiming that a professor owns a grant simply because one of their papers acknowledged it.

The pipeline can also inspect institution-level awards and match investigators back to professors in the directory.

### 5. Research hiring statements with AI

Hiring information is usually unstructured and spread across faculty pages, lab websites, and "Join Us" pages.

For this step, Road to Research uses an LLM-backed research workflow.

Given the professor's:

- Name
- University
- Department
- Faculty URL
- Lab URL

the system looks for an explicit public recruiting statement.

If one is found, we store:

- The hiring quote
- Source URL
- Source page title
- Date checked

If no explicit statement is found, we do **not** assume the professor is not hiring.

---

## Why Jac?

Road to Research is built primarily in **Jac** because the data is naturally graph-shaped.

```text
Institution
    │
    └── Professor
           │
           ├── Faculty Publication Anchor
           ├── Paper
           │      └── Subfield
           ├── Grant
           └── Hiring Signal
```

The graph schema is defined in `services/models.jac`.

### Core Jac nodes

- `Institution`
- `Professor`
- `FacultyPublicationAnchor`
- `Paper`
- `Subfield`
- `Grant`
- `HiringSignal`

### Core graph relationships

```text
Institution --HasProfessor--> Professor
Professor --HasAnchor--> FacultyPublicationAnchor
Professor --Authored--> Paper
Paper --HasSubfield--> Subfield
Professor --HasGrant--> Grant
Professor --HasHiringSignal--> HiringSignal
```

Jac handles the graph model, application workflow, backend services, and frontend components.

Python helper modules are used mainly for lower-level HTTP/network utilities and supporting tasks.

---

## Important Project Modules

```text
main.jac
    Application routes and top-level UI

services/models.jac
    Graph nodes, edges, and persistence model

services/pipeline.jac
    Professor identity matching, papers, subfields, grants, and enrichment

services/queue.jac
    University ingestion and background processing pipeline

services/directory.jac
    Professor search, filters, and profile retrieval

services/crawler.jac
    Faculty-directory and faculty-profile extraction

services/openalex.jac
    OpenAlex service interface

services/hiring.jac
    LLM-backed hiring research

services/persist.jac
    Directory snapshot export and restore

components/SearchPage.jac
    Search interface

components/ProfilePage.jac
    Professor profile

components/PipelineAdmin.jac
    Pipeline administration interface
```

---

## Biggest Technical Challenge: Identity Resolution

We initially considered two approaches.

### Professor-first

```text
Professor name
    ↓
OpenAlex
```

This made paper retrieval easy, but common names created identity problems.

### University-first

```text
University
    ↓
Research output
```

This gave us a reliable institution, but made it harder to determine which papers and subfields belonged to each individual professor.

### Our solution: combine both

```text
Official university
    ↓
Known professor
    ↓
Faculty page
    ↓
Known paper when available
    ↓
OpenAlex identity
    ↓
Stable Author ID
    ↓
Recent research
```

The university tells us **who the professor is**.

OpenAlex tells us **which scholarly record belongs to them**.

That separation became the foundation of the platform.

---

## Tech Stack

- **Jac / Jaseci**
- **JacHammer**
- **OpenAlex**
- **Jac `by llm()` / LLM-assisted research**
- React runtime
- jac-shadcn
- Tailwind CSS
- HugeIcons

---

## Running Locally

Install project dependencies:

```bash
jac install
```

Start the app:

```bash
jac start --dev main.jac
```

Then open:

```text
http://localhost:8000
```

---

## Environment Configuration

Example:

```bash
# OpenAlex
OPENALEX_MAILTO=your-email@example.com
OPENALEX_API_KEY=your_openalex_key

# LLM
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=your_key
```

The project can also use other supported LLM provider keys such as:

```bash
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
GOOGLE_API_KEY=
```

Multiple OpenAlex keys are supported by the built-in key pool.

Optional:

```bash
# Disable automatic pipeline startup
PIPELINE_AUTOSTART=0

# Enable institution-wide grant lookup
GRANTS_ENABLED=1
```

---

## Data Persistence

The live directory is represented in Jac's graph model.

The application also supports snapshot persistence so collected information can be restored after a sandbox or preview restart.

Stored data can include:

- Universities
- Professors
- Faculty publication anchors
- Papers
- Research subfields
- Grants
- Hiring statements

---

## What We Learned

The hardest part of academic discovery is often not finding data — it is **connecting the correct data to the correct person**.

Stable identifiers such as OpenAlex Author IDs, Work IDs, DOIs, ORCIDs, OpenAlex Institution IDs, and ROR IDs are much safer than relying only on names.

We also found that structured data and AI are best used for different jobs:

```text
Structured academic data
    → OpenAlex

Unstructured hiring research
    → AI
```

---

## What's Next

Future work could include:

- Expanding to more universities
- Adding more faculty-directory adapters
- Improving grant discovery
- More frequent automatic refreshes
- Better research-subfield filtering
- Saved professor lists
- Personalized research recommendations
- Notifications when new recruiting statements appear
- Graduate advisor and PhD opportunity discovery

---

## Built for JacHacks A2Tech360

Road to Research was built with **Jac, JacHammer, OpenAlex, and AI-assisted research** for JacHacks A2Tech360.

> **Our goal is to make the road from "I want to do research" to "I found a professor I want to work with" much shorter.**
