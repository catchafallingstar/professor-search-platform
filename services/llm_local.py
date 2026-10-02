"""Direct Ollama call for a local model reached through a tunnel (e.g. trycloudflare.com).

Why not by llm: Cloudflare quick tunnels drop a request that sends no bytes for ~100 s, and
"thinking" models (qwen3) spend that time reasoning before any JSON appears. Here the request
streams (bytes flow continuously, so the tunnel stays open), thinking is switched off, and the
reply is constrained to a JSON schema. Used only when LLM_MODEL is an ollama/... model.
"""

import json
import os
import urllib.request

PUBLICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "papers": {"type": "array", "maxItems": 5, "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "year": {"type": "integer"},
                           "doi": {"type": "string"}, "confidence": {"type": "number"}},
            "required": ["title", "year", "doi", "confidence"]}},
        "reasoning": {"type": "string"},
    },
    "required": ["papers", "reasoning"],
}

PUBLICATION_PROMPT = (
    "ACCURACY FIRST. List publications of this specific professor ONLY if you are confident they were "
    "written by this exact person: same name, at this university, working in this department's field. "
    "Many people share a name - a work by a same-named person at another institution or in another field "
    "is WRONG and must be left out. It is completely fine, and often correct, that the professor has no "
    "publications you know of (many humanities and arts faculty publish books, exhibitions or performances, "
    "or nothing indexed); in that case return an empty list. Never guess, never invent titles or DOIs. "
    "confidence is 0 to 1 that the work is by THIS person at THIS university in THIS field. At most 5 works."
)


class LocalLLMError(Exception):
    pass


def configured():
    return os.environ.get("LLM_MODEL", "").startswith("ollama") and bool(os.environ.get("OLLAMA_API_BASE"))


def model_name():
    return os.environ.get("LLM_MODEL", "").split("/", 1)[-1]


def chat_json(system, user, schema, max_tokens=800, timeout=300):
    base = os.environ.get("OLLAMA_API_BASE", "").rstrip("/")
    body = json.dumps({
        "model": model_name(), "stream": True, "think": False, "format": schema,
        "options": {"temperature": 0, "num_predict": max_tokens},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(base + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": "curl/8.5.0"})
    text = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise LocalLLMError(chunk["error"])
                text.append((chunk.get("message") or {}).get("content") or "")
                if chunk.get("done"):
                    break
    except LocalLLMError:
        raise
    except Exception as e:
        raise LocalLLMError(f"{type(e).__name__}: {str(e)[:200]}")
    raw = "".join(text).strip()
    try:
        return json.loads(raw)
    except ValueError:
        raise LocalLLMError(f"not JSON: {raw[:200]}")


def find_publications(name, university, department, title, page_text=""):
    user = (f"Professor: {name}\nTitle: {title}\nUniversity: {university}\nDepartment: {department}\n"
            f"Faculty page text (may be empty):\n{(page_text or '')[:6000]}")
    return chat_json(PUBLICATION_PROMPT, user, PUBLICATION_SCHEMA)
