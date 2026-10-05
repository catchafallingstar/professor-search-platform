"""One process-wide lock shared by every path that can reach the local Ollama model.

services/ai.jac (the by-llm fallback chain) and services/llm_local.py (the Scholar-pick raw HTTP
call) used to lock independently, so the pipeline could fire both at the local model at once.
Ollama then queues one request behind the other on the GPU; the queued side's HTTP call looks
like a timeout/connection error to its caller, which read as "local model offline" and rested the
model for the cloud fallback - even though it was only ever busy, never actually down.
"""
import threading

LOCK = threading.Lock()
