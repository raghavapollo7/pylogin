import argparse
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from groq import Groq
from groq import APIStatusError
from playwright.sync_api import sync_playwright

API = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
SYSTEM = '''You are a site-agnostic browser test agent. Return exactly ONE JSON object per turn with fields: action, url, index, role, name, label, text, value, key, seconds, verdict, reason, evidence. Include ALL fields every time; set every unused field to null. Example click: {"action":"click","url":null,"index":37,"role":null,"name":null,"label":null,"text":null,"value":null,"key":null,"seconds":null,"verdict":null,"reason":null,"evidence":null}. Example done: {"action":"done","url":null,"index":null,"role":null,"name":null,"label":null,"text":null,"value":null,"key":null,"seconds":null,"verdict":"fail","reason":"The form is blocked","evidence":null}. Never use shorthand like {"click":37}; action names are values of "action". Actions: goto(url), click/fill/select/assert_visible (index from interactive list OR role+name/label/text; fill/select require value), press(key), wait(seconds 0..3), assert_text(text), assert_url(url substring), done(verdict pass|fail, reason, evidence optional). Observe supplied data; never invent credentials. Controls marked unavailable were tried and produced no visible page change: do not choose them. After an action feedback says no navigation or state change, do not repeat it; choose another route or report the blocker. A PASS needs a code-verified assertion; if none, perform one. If blocked, fail specifically. One JSON object only.'''
SELECTOR = "a,button,input,textarea,select,[role='button'],[role='link'],[role='checkbox'],[role='radio'],[role='tab'],[contenteditable='true']"
FIELDS = {"action", "url", "index", "role", "name", "label", "text", "value", "key", "seconds", "verdict", "reason", "evidence"}
ACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["goto", "click", "fill", "select", "press", "wait", "assert_text", "assert_url", "assert_visible", "done"]},
        "url": {"type": ["string", "null"]}, "index": {"type": ["integer", "null"], "minimum": 0},
        "role": {"type": ["string", "null"]}, "name": {"type": ["string", "null"]},
        "label": {"type": ["string", "null"]}, "text": {"type": ["string", "null"]},
        "value": {"type": ["string", "null"]}, "key": {"type": ["string", "null"]},
        "seconds": {"type": ["number", "null"], "minimum": 0, "maximum": 3},
        "verdict": {"type": ["string", "null"], "enum": ["pass", "fail", None]},
        "reason": {"type": ["string", "null"]}, "evidence": {"type": ["string", "null"]},
    },
    "required": ["action", "url", "index", "role", "name", "label", "text", "value", "key", "seconds", "verdict", "reason", "evidence"],
}


class GroqAPIError(RuntimeError):
    pass


def parse(raw):
    a = json.loads(raw)
    if not isinstance(a, dict): raise ValueError("Expected a JSON object")
    a = {k: v for k, v in a.items() if v is not None}
    if "action" not in a:
        shorthand = next((k for k in a if k in {"goto", "click", "fill", "select", "press", "wait", "assert_text", "assert_url", "assert_visible", "done"}), None)
        if shorthand: raise ValueError(f"Missing required 'action' field; use {{\"action\":\"{shorthand}\", ...}} (for example, click uses {{\"action\":\"click\",\"index\":37}})")
        raise ValueError("Missing required 'action' field")
    extras = set(a) - FIELDS
    if extras: raise ValueError("Unsupported field(s): " + ", ".join(sorted(extras)))
    k = a.get("action")
    if k not in {"goto", "click", "fill", "select", "press", "wait", "assert_text", "assert_url", "assert_visible", "done"}: raise ValueError("Unknown action")
    if "index" in a and (type(a["index"]) is not int or a["index"] < 0): raise ValueError("index must be a nonnegative integer")
    if k == "goto" and not isinstance(a.get("url"), str): raise ValueError("goto requires url")
    if k in {"click", "fill", "select", "assert_visible"} and not ("index" in a or a.get("label") or a.get("text") or (a.get("role") and a.get("name") is not None)): raise ValueError("Action needs a target")
    if k in {"fill", "select"} and not isinstance(a.get("value"), str): raise ValueError("value required")
    if k == "press" and not isinstance(a.get("key"), str): raise ValueError("key required")
    if k in {"assert_text", "assert_url"} and not isinstance(a.get("text" if k == "assert_text" else "url"), str): raise ValueError("assertion value required")
    if k == "done" and (a.get("verdict") not in {"pass", "fail"} or not isinstance(a.get("reason"), str)): raise ValueError("done needs verdict and reason")
    if k == "wait" and (type(a.get("seconds", 1)) not in (int, float) or not 0 <= a.get("seconds", 1) <= 3): raise ValueError("wait seconds out of range")
    return a


def ask(messages, model, key):
    chat = [{"role": "system", "content": SYSTEM}] + messages
    client = Groq(api_key=key)
    try:
        out = client.chat.completions.create(
            model=model, max_tokens=700, messages=chat, temperature=0.1,
            reasoning_effort="low",
            response_format={"type": "json_schema", "json_schema": {"name": "browser_action", "strict": True, "schema": ACTION_SCHEMA}},
        )
    except APIStatusError as e:
        detail = str(e)
        hint = ""
        if e.status_code == 403 and "1010" in detail:
            hint = " Groq's edge blocked this HTTP client fingerprint; the official SDK is in use now, so check proxy/VPN/network filtering if this persists."
        elif e.status_code == 403:
            hint = " Check that this model is enabled for your Groq project/organization, or set GROQ_MODEL to a model you can access."
        elif e.status_code == 401:
            hint = " Check that GROQ_API_KEY is valid and copied correctly."
        raise GroqAPIError(f"Groq API HTTP {e.status_code}: {detail}.{hint}") from e
    return out.choices[0].message.content or "", out.usage.total_tokens if out.usage else 0


def observe(page):
    elements = []
    loc = page.locator(SELECTOR)
    try: count = min(loc.count(), 250)
    except Exception: count = 0
    for i in range(count):
        if len(elements) >= 80: break
        e = loc.nth(i)
        try:
            if not e.is_visible(): continue
            tag = e.evaluate("x=>x.tagName.toLowerCase()")
            labels = e.evaluate("x => x.labels ? Array.from(x.labels).map(y => y.innerText).join(' ') : ''")
            name = (e.get_attribute("aria-label") or e.get_attribute("name") or labels or e.inner_text(timeout=300) or e.get_attribute("placeholder") or "").strip().replace("\n", " ")[:100]
            value = e.input_value(timeout=100) if tag in {"input", "textarea", "select"} else ""
            elements.append({"index": i, "role": e.get_attribute("role") or {"a":"link","button":"button","input":"textbox","textarea":"textbox","select":"combobox"}.get(tag, tag), "name": name, "type": e.get_attribute("type") or "", "value": value[:80]})
        except Exception: pass
    try: text = page.locator("body").inner_text(timeout=600)[:1200]
    except Exception: text = ""
    return {"url": page.url, "title": page.title(), "interactive": elements, "visible_text": text}


def mark_blocked_controls(observation, blocked_actions):
    for item in observation["interactive"]:
        for action in blocked_actions.values():
            if (("index" in action and item["index"] == action["index"])
                or (action.get("role") == item["role"] and action.get("name") == item["name"] and action.get("role"))
                or (action.get("label") and action["label"] == item["name"])):
                item["unavailable"] = "previous click caused no page change"
    return observation


def target(page, a):
    if "index" in a: return page.locator(SELECTOR).nth(a["index"])
    if a.get("role") and "name" in a: return page.get_by_role(a["role"], name=a["name"], exact=True).first
    if a.get("label"): return page.get_by_label(a["label"], exact=True).first
    return page.get_by_text(a["text"], exact=True).first


def execute(page, a):
    k = a["action"]
    if k == "goto": page.goto(a["url"], wait_until="domcontentloaded", timeout=20000); return "Navigated", None
    if k == "click": target(page,a).click(timeout=8000); return "Clicked", None
    if k == "fill": target(page,a).fill(a["value"],timeout=8000); return "Filled", None
    if k == "select": target(page,a).select_option(label=a["value"],timeout=8000); return "Selected", None
    if k == "press": page.keyboard.press(a["key"]); return "Pressed key", None
    if k == "wait": page.wait_for_timeout(int(a.get("seconds",1)*1000)); return "Waited", None
    if k == "assert_text": page.get_by_text(a["text"],exact=False).first.wait_for(state="visible",timeout=8000); return "Verified text", {"type":"text","value":a["text"]}
    if k == "assert_url":
        if a["url"] not in page.url: raise AssertionError(f"Expected URL containing {a['url']}; got {page.url}")
        return "Verified URL", {"type":"url","value":a["url"]}
    if k == "assert_visible": target(page,a).wait_for(state="visible",timeout=8000); return "Verified visibility", {"type":"visible","value":a.get("name",a.get("label",a.get("text",str(a.get("index")))))}
    raise ValueError("done is handled by runner")


def run_case(url, test, variables, headless, limit, folder, model, key):
    start=time.time(); log=[]; verified=[]; tokens=0; verdict="fail"; reason="Step limit reached"; repeats={}; blocked_actions={}; feedback=""
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=headless); page=browser.new_page(viewport={"width":1280,"height":800}); page.set_default_timeout(8000)
        try: page.goto(url,wait_until="domcontentloaded",timeout=20000)
        except Exception as e: feedback="Initial navigation error: "+str(e)[:250]
        for label in ["Accept all","Accept","Allow all","I agree","Got it"]:
            try:
                b=page.get_by_role("button",name=re.compile("^"+re.escape(label)+"$",re.I)).first
                if b.is_visible(timeout=200): b.click(timeout=600); break
            except Exception: pass
        for step in range(1,limit+1):
            obs=mark_blocked_controls(observe(page),blocked_actions); shot=str(Path(folder)/f"step_{step:02}.png")
            try: page.screenshot(path=shot,full_page=False,timeout=4000)
            except Exception: shot=None
            # Keep each turn independent so prior page snapshots do not consume the TPM budget.
            current={"test":test,"start_url":url,"vars":variables,"observation":obs,
                     "verified_assertions":verified[-5:],"previous_feedback":feedback,
                     "blocked_controls":[a for a in blocked_actions.values()]}
            messages=[{"role":"user","content":json.dumps(current,ensure_ascii=False)}]
            action=None; raw=""; error=""
            for _ in range(3):
                try:
                    raw,n=ask(messages,model,key); tokens+=n; action=parse(raw)
                    signature=json.dumps(action,sort_keys=True)
                    if signature in blocked_actions:
                        error="This exact action already produced no navigation or new tab. Choose a different action."
                        messages += [{"role":"assistant","content":raw},{"role":"user","content":error+" Return a different JSON action, or done with a specific failure reason."}]
                        action=None
                        continue
                    break
                except Exception as e:
                    error=str(e)[:300]; messages += [{"role":"assistant","content":raw},{"role":"user","content":"Invalid JSON/action: "+error+". Return one valid action JSON."}]
                    if isinstance(e, GroqAPIError): break
            halt=False
            if action is None:
                result=("Groq request failed: " if error.startswith("Groq API") else "No valid recovery action: ")+error; reason=result
            else:
                sig=json.dumps(action,sort_keys=True); repeats[sig]=repeats.get(sig,0)+1
                if action["action"] == "done":
                    if action["verdict"] == "pass" and not verified:
                        result="PASS rejected: no code-verified assertion"; feedback=result
                    else: verdict=action["verdict"]; reason=action["reason"]; result=reason
                elif repeats[sig] >= 3:
                    result="Loop guard stopped the run: same action repeated three times"; reason=result; feedback=result; halt=True
                else:
                    try:
                        open_pages=set(page.context.pages)
                        before_url=page.url
                        result,assertion=execute(page,action)
                        verified.append(assertion) if assertion else None
                        if action["action"] == "click": page.wait_for_timeout(300)
                        new_pages=[p for p in page.context.pages if p not in open_pages]
                        if new_pages:
                            page=new_pages[-1]
                            try: page.wait_for_load_state("domcontentloaded",timeout=5000)
                            except Exception: pass
                            result += "; switched to newly opened tab " + page.url
                        elif action["action"] == "click" and page.url == before_url:
                            after=observe(page)
                            changed=(after["visible_text"] != obs["visible_text"] or after["interactive"] != obs["interactive"])
                            if changed:
                                result += "; page content changed without navigation"
                            else:
                                result += "; no page change, navigation, or new tab occurred; choose another control and do not repeat this click"
                                blocked_actions[sig]=action
                        feedback="Action succeeded: "+result
                    except Exception as e: result="Action failed: "+str(e)[:350]; feedback=result
            log.append({"step":step,"observation":obs,"action":action or raw,"result":result,"screenshot":shot})
            if action and action["action"] == "done" and (action["verdict"] == "fail" or verified): break
            if action is None or halt: break
        browser.close()
    return {"url":url,"test":test,"verdict":verdict,"reason":reason,"verified_assertions":verified,"steps":log,"total_seconds":round(time.time()-start,2),"tokens":tokens}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--url"); ap.add_argument("--test"); ap.add_argument("--file"); ap.add_argument("--headless",action="store_true"); ap.add_argument("--max-steps",type=int,default=25); ap.add_argument("--vars",action="append",default=[]); ap.add_argument("--out",default="reports")
    a=ap.parse_args()
    if not a.file and not (a.url and a.test): ap.error("provide --url and --test, or --file")
    if a.max_steps < 1: ap.error("--max-steps must be positive")
    key=os.getenv("GROQ_API_KEY")
    if not key: ap.error("set GROQ_API_KEY")
    variables={}
    for pair in a.vars:
        if "=" not in pair: ap.error("--vars format is key=value")
        k,v=pair.split("=",1); variables[k]=v
    cases=[]
    if a.file:
        for line in Path(a.file).read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                if "|" not in line: ap.error("file lines must be URL | test")
                u,t=line.split("|",1); cases.append((u.strip(),t.strip()))
    else: cases=[(a.url,a.test)]
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True); results=[]
    for i,(url,test) in enumerate(cases,1):
        folder=out/f"case_{i:02}"; folder.mkdir(exist_ok=True)
        r=run_case(url,test,variables,a.headless,a.max_steps,str(folder),os.getenv("GROQ_MODEL",MODEL),key); results.append(r)
        print(f"{r['verdict'].upper()}: {test} — {r['reason']} ({r['total_seconds']}s, {r['tokens']} tokens)")
    (out/"report.json").write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding="utf-8")
    sections=[]
    for i,r in enumerate(results,1):
        steps="".join(f"<li><b>Step {s['step']}</b>: {html.escape(str(s['action']))}<br>{html.escape(s['result'])}"+(f'<br><a href="case_{i:02}/{Path(s["screenshot"]).name}">Screenshot</a>' if s.get("screenshot") else "")+"</li>" for s in r["steps"])
        sections.append(f"<section><h2 class='{r['verdict']}'>{r['verdict'].upper()} — {html.escape(r['test'])}</h2><p>{html.escape(r['reason'])}</p><p>{len(r['steps'])} steps; {r['total_seconds']}s; {r['tokens']} tokens</p><ol>{steps}</ol></section>")
    style="body{font:16px system-ui;max-width:1000px;margin:2rem auto}section{border-bottom:1px solid #ccc;padding:1rem}.pass{color:green}.fail{color:#b00}li{margin:.8rem}"
    (out/"report.html").write_text("<!doctype html><meta charset='utf-8'><title>Browser report</title><style>"+style+"</style><h1>Browser test report</h1>"+"".join(sections),encoding="utf-8")
    if any(r["verdict"]!="pass" for r in results): sys.exit(1)


if __name__ == "__main__": main()
