# Road to Research

**Helping students find professors, understand their recent research, discover funding, and identify current research opportunities — all in one place.**

Road to Research is a professor discovery platform built for **JacHacks A2Tech360**. It combines official university faculty information, [OpenAlex](https://openalex.org/) scholarly data, and AI-assisted web research to make finding research opportunities easier for students.

Instead of jumping between university directories, publication databases, grant websites, and individual lab pages, students can search one platform to understand:

- Who a professor is
- Which department they belong to
- What they have published recently
- Which research subfields their recent work belongs to
- Which research grants they are connected to
- Whether they have a public statement about recruiting students or researchers

---

## The Problem

Finding a research professor is surprisingly difficult.

A student may need to:

1. Search a university department website
2. Find individual faculty profiles
3. Search publication databases
4. Figure out whether two researchers with the same name are actually the same person
5. Read recent papers to understand what the professor currently works on
6. Search grant databases for funding
7. Search faculty and lab pages again to see whether the professor is accepting students

Most of this information exists, but it is spread across many different systems.

**Road to Research connects those pieces together.**

---

## How It Works

Our pipeline starts with the university rather than blindly searching researchers by name.

```mermaid
flowchart TD
    A[University] --> B[Official Faculty Directory]
    B --> C[Professor]
    C --> D[Faculty Profile]
    D --> E[Known Publication / DOI if available]

    E --> F[OpenAlex Identity Matching]
    C --> F

    F --> G[OpenAlex Author ID]
    G --> H[Recent Papers]
    H --> I[Research Subfields]
    H --> J[Linked Awards]

    J --> K[Grant Information]

    C --> L[AI Hiring Research]
    L --> M[Hiring Quote + Source]

    I --> N[Professor Profile]
    K --> N
    M --> N
