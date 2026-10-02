"""Local LLM (Ollama, e.g. behind a trycloudflare tunnel) used as a VERIFIER, never as a memory.

Retrieve-then-verify:
  1. Python fetches real OpenAlex author candidates for the professor (name search) with their
     institutions, topics and a few recent work titles.
  2. The local model only CHOOSES among those candidates (or none), given the professor's
     department and university. It cannot invent papers: every option is real OpenAlex data.
  3. Python re-checks the pick (name match + the candidate's institution history) before use.

Transport: streamed request (bytes flow, so a Cloudflare quick tunnel does not cut it at ~100 s),
"think": false, and the reply constrained to a JSON schema. Reasoning models (deepseek-r1) may
still wrap output in <think>...</think>; that block is stripped before parsing.
"""

import json
import os
import re
import urllib.request


class LocalLLMError(Exception):
    pass


def base_url():
    return os.environ.get("OLLAMA_API_BASE", "").strip().rstrip("/")


def configured():
    return os.environ.get("LLM_MODEL", "").strip().startswith("ollama") and bool(base_url())


def model_name():
    return os.environ.get("LLM_MODEL", "").strip().split("/", 1)[-1]


def chat_json(system, user, schema, max_tokens=None, timeout=400):
    # Reasoning models (deepseek-r1, qwq) ignore "think": false and spend tokens reasoning first;
    # give them room so the JSON answer is not cut off. OLLAMA_MAX_TOKENS overrides.
    if max_tokens is None:
        reasoning = any(k in model_name().lower() for k in ("deepseek-r1", "qwq", "-r1", "thinking"))
        max_tokens = int(os.environ.get("OLLAMA_MAX_TOKENS", "") or (3000 if reasoning else 600))
    body = json.dumps({
        "model": model_name(), "stream": True, "think": False, "format": schema,
        "options": {"temperature": 0, "num_predict": max_tokens},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(base_url() + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": "curl/8.5.0"})
    text, thinking, reason_done = [], [], ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise LocalLLMError(chunk["error"])
                msg = chunk.get("message") or {}
                text.append(msg.get("content") or "")
                thinking.append(msg.get("thinking") or "")
                if chunk.get("done"):
                    reason_done = chunk.get("done_reason") or ""
                    break
    except LocalLLMError:
        raise
    except Exception as e:
        raise LocalLLMError(f"{type(e).__name__}: {str(e)[:200]}")
    raw = re.sub(r"(?is)<think>.*?</think>", "", "".join(text)).strip()
    for source in (raw, "".join(thinking)):      # answer first; a reasoning model may leave JSON in its thoughts
        for m in reversed(list(re.finditer(r"\{[^{}]*\}", source, re.S))):
            try:
                return json.loads(m.group(0))
            except ValueError:
                continue
    hint = " (token limit reached while reasoning; raise OLLAMA_MAX_TOKENS)" if reason_done == "length" else ""
    raise LocalLLMError(f"no JSON in reply{hint}: {raw[:150]}")


# ---------- task: which OpenAlex candidate is this professor? ----------

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string"},          # candidate letter, or "NONE"
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["choice", "confidence", "reason"],
}

PICK_PROMPT = (
    "You verify academic identities. You are given ONE professor (name, title, department, university) and "
    "a list of real OpenAlex author records labelled A, B, C... with their institutions, research topics and "
    "recent work titles. Decide which record is this professor. ACCURACY FIRST: pick a record only if its "
    "institutions include the professor's university (now or in the past) AND its topics and works fit the "
    "professor's department. If no record clearly fits, or two fit equally, answer NONE - that is a correct "
    "and acceptable answer. Never guess. Return JSON: {\"choice\": \"A\" | \"B\" | ... | \"NONE\", "
    "\"confidence\": 0..1, \"reason\": one short sentence}."
)


def describe(cands):
    """Candidates as compact text the model can read (letters A, B, C...)."""
    lines = []
    for i, c in enumerate(cands):
        lines.append(f"{chr(65 + i)}. {c['name']} | works: {c['works_count']} | institutions: {', '.join(c['institutions']) or 'unknown'}\n"
                     f"   topics: {', '.join(c['topics']) or 'none listed'}\n"
                     f"   recent works: {' ; '.join(c['works']) or 'none listed'}")
    return "\n".join(lines)


def pick_candidate(prof, cands):
    """Returns {"choice": letter or "NONE", "confidence", "reason"}."""
    user = (f"Professor: {prof['name']}\nTitle: {prof.get('title', '')}\nDepartment: {prof.get('department', '')}\n"
            f"University: {prof.get('university', '')}\n\nOpenAlex author records:\n{describe(cands)}")
    ans = chat_json(PICK_PROMPT, user, PICK_SCHEMA)
    choice = str(ans.get("choice") or "NONE").strip().upper()[:4]
    try:
        conf = float(ans.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf > 1:                                   # small models often answer 95 for 0.95
        conf = conf / 100.0
    return {"choice": choice, "confidence": max(0.0, min(conf, 1.0)), "reason": str(ans.get("reason") or "")[:300]}
