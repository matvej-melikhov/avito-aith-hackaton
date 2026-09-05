#!/usr/bin/env python3
"""OpenAI-compatible LLM client. Default provider DeepSeek (решение команды)."""
import json, os, re, ssl, time, urllib.request

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()

BASE  = os.environ.get("GRADER_BASE_URL", "https://api.deepseek.com/v1")
KEY   = os.environ.get("GRADER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("GRADER_MODEL", "deepseek-chat")

class NoKey(RuntimeError): pass

def chat(system, user, temperature=0.0, max_tokens=4000, retries=3):
    if not KEY:
        raise NoKey("set GRADER_API_KEY (and optionally GRADER_BASE_URL / GRADER_MODEL)")
    body = json.dumps({
        "model": MODEL, "temperature": temperature, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    last = None
    for a in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180, context=_CTX) as r:
                return json.load(r)["choices"][0]["message"]["content"]
        except Exception as e:
            last = e; time.sleep(2 * (a + 1))
    raise last

def chat_json(system, user, **kw):
    txt = chat(system, user, **kw)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError(f"no JSON in reply: {txt[:300]}")
    return json.loads(m.group(0))
