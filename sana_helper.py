#!/usr/bin/env python3
"""
Sana helper (deploy build) — serves the prototype, relays chat to Claude, speaks via ElevenLabs,
runs the Layer-2 LLM risk classifier, and for shared testing adds:
  • a passcode + consent/disclaimer gate (with voice picker) shown before anyone reaches the app
  • an injected in-app feedback button with clear confirmation, saved to SANA_FEEDBACK_FILE
  • tap-to-call/text crisis buttons when Sana routes to human help
  • a daily request cap
Runs on any always-on host: binds 0.0.0.0 and reads $PORT (e.g. Render). Secrets come from env vars.
"""
import os, json, time, hashlib, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

KEY   = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5").strip()
CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-haiku-4-5-20251001").strip()
PORT  = int(os.environ.get("PORT", "8765"))
HTML  = "MeritMind_Sana_Prototype.html"

ELEVEN_KEY   = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVEN_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
ELEVEN_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip()

PASSCODE      = os.environ.get("SANA_PASSCODE", "").strip()
DAILY_CAP     = int(os.environ.get("DAILY_CAP", "800"))
FEEDBACK_FILE = os.environ.get("SANA_FEEDBACK_FILE", "sana_feedback.jsonl")
COOKIE_TOKEN  = hashlib.sha256(("sana|" + PASSCODE).encode()).hexdigest()[:20]
_day = {"date": "", "n": 0}

VOICES = [
    {"id": "nf4MCGNSdM0hxM95ZBQR", "name": "Warm"},
    {"id": "gJx1vCzNCD1EQHT212Ls", "name": "Clear"},
    {"id": "kdmDKE6EkgrWrrykO9Qt", "name": "Friendly"},
    {"id": "EST9Ui6982FZPSi7gCHi", "name": "Gentle"},
    {"id": "bfGb7JTLUnZebZRiFYyq", "name": "Steady (male)"},
    {"id": "Rsz5u2Huh1hPlPr0oxRQ", "name": "Deep (male)"},
]
_VOICE_IDS = {v["id"] for v in VOICES}

DEFAULT_SYSTEM = ("You are Sana, a warm, human AI companion for mental fitness. Talk like a real, caring "
 "person - natural, brief, warm. You are not a therapist or doctor; never diagnose, treat, or prescribe. "
 "If someone is in crisis, point them to human help.")

CRISIS_RESPONSE = ("I'm really glad you told me this, and I want to take it seriously. It sounds like you might "
 "be having thoughts of ending your life or not being here anymore - am I understanding that right? Whatever "
 "you're carrying, you shouldn't have to face it alone. I'm an AI, so I'm not the right kind of help for something "
 "this important, but people who are are available right now: please call or text 988, or text HOME to 741741. "
 "If you might be in immediate danger, please call 911.")

CLASSIFY_SYSTEM = ("You are a safety classifier for a mental-wellness app. Read the user's message and decide "
 "whether it shows ANY sign that the person may be considering suicide, self-harm, or harming another person - "
 "INCLUDING indirect, veiled, coded, or metaphorical expressions. Examples that are RISK: 'they'd be better off "
 "without me', 'I don't want to wake up', \"I've made my peace with everything\", 'getting my affairs in order', "
 "\"I won't be a problem much longer\", 'I just want the noise to stop forever', 'I keep picturing my funeral', "
 "'soon none of this will matter', 'I've been telling people I love them just in case'. Err strongly on the side of "
 "caution. Do NOT flag ordinary sadness, stress, grief, or clearly figurative venting with no real risk. Examples "
 "that are SAFE: 'this deadline is killing me', 'I'm dead tired', 'dying to see that movie', 'work is overwhelming', "
 "'my phone is dead'. Reply with ONLY a JSON object and nothing else: {\"route\": true} if there is any risk signal, "
 "otherwise {\"route\": false}.")

GATE_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sana — private preview</title>
<style>
 body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1220;color:#e8ecf5;
      display:flex;min-height:100vh;align-items:center;justify-content:center;padding:22px}
 .card{max-width:460px;background:#171b2e;border:1px solid #263056;border-radius:18px;padding:28px 26px}
 h1{font-size:20px;margin:0 0 6px} .sub{color:#9fb0d6;font-size:14px;margin:0 0 18px}
 p{font-size:14px;line-height:1.5;color:#c7d2ec} .box{background:#10142a;border-radius:12px;padding:14px 16px;margin:14px 0}
 .box b{color:#eaf0ff} input[type=password]{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;
      border:1px solid #34406e;background:#0d1122;color:#fff;font-size:16px;margin-top:6px}
 label.chk{display:flex;gap:10px;align-items:flex-start;font-size:13px;color:#b9c6e6;margin:14px 0}
 button{width:100%;padding:13px;border:0;border-radius:12px;background:#5b8cff;color:#fff;font-size:16px;
      font-weight:600;margin-top:6px;cursor:pointer} button:disabled{background:#39406a;cursor:not-allowed}
 .err{color:#ff9a9a;font-size:13px;min-height:16px;margin-top:8px}
 a{color:#8fb0ff}
</style></head><body><div class="card">
 <h1>You're about to meet Sana</h1>
 <p class="sub">A private, early preview — thanks for helping test it.</p>
 <div class="box">
   <p><b>Sana is not a therapist, doctor, or emergency service.</b> She's an AI companion for everyday mental
   fitness. She can't diagnose or treat anything. If you're in crisis or thinking about harming yourself,
   please call or text <b>988</b>, text <b>HOME to 741741</b>, or call <b>911</b>. In an emergency, contact
   emergency services.</p>
 </div>
 <div class="box"><p><b>Your privacy.</b> Your conversations stay on your own device. We will never sell your
   data — ever. Only the feedback you choose to send is shared with the team.</p></div>
 <form id="f" onsubmit="return go(event)">
   <input id="pc" type="password" placeholder="Access passcode" autocomplete="off" autofocus>
   <label class="chk"><input id="agree" type="checkbox">
     <span>I understand Sana is an AI, not medical or crisis care, and I agree to try this preview and share feedback.</span></label>
   <label class="chk" style="display:block;color:#c7d2ec;margin:8px 0 2px">Choose Sana's voice:</label>
   <select id="voice" style="width:100%;box-sizing:border-box;padding:11px;border-radius:10px;border:1px solid #34406e;background:#0d1122;color:#fff;font-size:15px;margin-bottom:4px">
     <option value="nf4MCGNSdM0hxM95ZBQR" selected>Warm</option>
     <option value="gJx1vCzNCD1EQHT212Ls">Clear</option>
     <option value="kdmDKE6EkgrWrrykO9Qt">Friendly</option>
     <option value="EST9Ui6982FZPSi7gCHi">Gentle</option>
     <option value="bfGb7JTLUnZebZRiFYyq">Steady (male)</option>
     <option value="Rsz5u2Huh1hPlPr0oxRQ">Deep (male)</option>
   </select>
   <button id="btn" type="submit" disabled>Enter</button>
   <div class="err" id="err"></div>
 </form>
</div><script>
 var a=document.getElementById('agree'),b=document.getElementById('btn');
 a.addEventListener('change',function(){b.disabled=!a.checked});
 async function go(e){e.preventDefault();b.disabled=true;
   var r=await fetch('/gate',{method:'POST',headers:{'content-type':'application/json'},
     body:JSON.stringify({passcode:document.getElementById('pc').value,voice:document.getElementById('voice').value})});
   if(r.ok){location.href='/';}else{document.getElementById('err').textContent='Incorrect passcode.';b.disabled=false;}
   return false;}
</script></body></html>"""

FEEDBACK_WIDGET = """
<div id="mm-fb-btn" style="position:fixed;right:16px;bottom:16px;z-index:99999;background:#5b8cff;color:#fff;
 padding:10px 14px;border-radius:20px;font:600 14px -apple-system,Segoe UI,Roboto,sans-serif;cursor:pointer;
 box-shadow:0 4px 14px rgba(0,0,0,.3)">Feedback</div>
<div id="mm-fb-panel" style="display:none;position:fixed;right:16px;bottom:64px;z-index:99999;width:290px;
 background:#171b2e;color:#e8ecf5;border:1px solid #2b3660;border-radius:14px;padding:14px;
 font:14px -apple-system,Segoe UI,Roboto,sans-serif;box-shadow:0 8px 26px rgba(0,0,0,.45)">
 <div style="font-weight:600;margin-bottom:8px">How was that?</div>
 <div id="mm-fb-stars" style="font-size:22px;letter-spacing:3px;cursor:pointer;margin-bottom:8px">☆☆☆☆☆</div>
 <textarea id="mm-fb-text" placeholder="What worked, what felt off, ideas..." style="width:100%;box-sizing:border-box;
  height:76px;border-radius:9px;border:1px solid #34406e;background:#0d1122;color:#fff;padding:9px;resize:vertical"></textarea>
 <button id="mm-fb-send" style="width:100%;margin-top:9px;padding:10px;border:0;border-radius:10px;background:#5b8cff;
  color:#fff;font-weight:600;cursor:pointer">Send feedback</button>
 <div id="mm-fb-done" style="color:#8fe0a6;font-size:13px;min-height:15px;margin-top:6px"></div>
</div><script>
(function(){var rating=0,btn=document.getElementById('mm-fb-btn'),panel=document.getElementById('mm-fb-panel'),
 stars=document.getElementById('mm-fb-stars');
 btn.onclick=function(){panel.style.display=panel.style.display==='none'?'block':'none';};
 stars.onclick=function(e){var r=Math.min(5,Math.max(1,Math.round((e.offsetX/stars.offsetWidth)*5)));rating=r;
   stars.textContent='★★★★★'.slice(0,r)+'☆☆☆☆☆'.slice(0,5-r);};
 document.getElementById('mm-fb-send').onclick=async function(){
   var t=document.getElementById('mm-fb-text').value;
   try{await fetch('/feedback',{method:'POST',headers:{'content-type':'application/json'},
     body:JSON.stringify({rating:rating,text:t,ua:navigator.userAgent})});
     document.getElementById('mm-fb-done').textContent='✓ Sent — thank you! You can close this.';
     document.getElementById('mm-fb-text').value='';rating=0;stars.textContent='☆☆☆☆☆';
     var sb=document.getElementById('mm-fb-send');sb.textContent='Sent ✓';sb.disabled=true;sb.style.opacity='0.6';
   }catch(err){document.getElementById('mm-fb-done').textContent='Could not send — try again.';}};})();
</script>"""

CRISIS_ACTIONS = '<div id="mm-crisis" style="display:none;position:fixed;left:0;right:0;bottom:0;z-index:100000;background:#7a1420;color:#fff;padding:12px 14px 14px;font:15px -apple-system,Segoe UI,Roboto,sans-serif;text-align:center"><div style="font-weight:600;margin-bottom:8px">You matter, and help is one tap away:</div><a href="tel:988" style="color:#fff;display:inline-block;margin:4px;padding:10px 14px;background:#b5202f;border-radius:10px;text-decoration:none;font-weight:600">Call 988</a><a href="sms:988" style="color:#fff;display:inline-block;margin:4px;padding:10px 14px;background:#b5202f;border-radius:10px;text-decoration:none;font-weight:600">Text 988</a><a href="sms:741741&body=HOME" style="color:#fff;display:inline-block;margin:4px;padding:10px 14px;background:#b5202f;border-radius:10px;text-decoration:none;font-weight:600">Text HOME to 741741</a><a href="tel:911" style="color:#fff;display:inline-block;margin:4px;padding:10px 14px;background:#8a1a1a;border-radius:10px;text-decoration:none;font-weight:600">Call 911</a><span id="mm-crisis-x" style="cursor:pointer;margin-left:10px;opacity:.8">Close</span></div><script>(function(){var shown=false;function show(){if(shown)return;shown=true;document.getElementById("mm-crisis").style.display="block";}document.getElementById("mm-crisis-x").onclick=function(){document.getElementById("mm-crisis").style.display="none";};var obs=new MutationObserver(function(ms){ms.forEach(function(m){(m.addedNodes||[]).forEach(function(nd){var t=(nd.textContent||"");if(t.indexOf("988")>-1&&t.indexOf("741741")>-1)show();});});});setTimeout(function(){obs.observe(document.body,{childList:true,subtree:true});},1500);})();</script>'

def classify_risk(text):
    if not (KEY and text and text.strip()):
        return False
    try:
        payload = json.dumps({"model": CLASSIFY_MODEL, "max_tokens": 40, "system": CLASSIFY_SYSTEM,
                              "messages": [{"role": "user", "content": text}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload, method="POST",
            headers={"content-type": "application/json", "x-api-key": KEY, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = "".join(c.get("text", "") for c in json.loads(r.read()).get("content", [])).strip()
        try:
            return bool(json.loads(raw).get("route"))
        except Exception:
            return '"route":true' in raw.replace(" ", "").lower()
    except Exception:
        return False

def anthropic_reply(messages, model, system):
    payload = json.dumps({"model": model or MODEL, "max_tokens": 500,
                          "system": system or DEFAULT_SYSTEM, "messages": messages}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload, method="POST",
        headers={"content-type": "application/json", "x-api-key": KEY, "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return "".join(c.get("text", "") for c in data.get("content", [])).strip()

def eleven_tts(text, voice_id=None):
    vid = voice_id if voice_id in _VOICE_IDS else ELEVEN_VOICE
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream"
           f"?optimize_streaming_latency=0&output_format=mp3_44100_128")
    body = json.dumps({"text": text, "model_id": ELEVEN_MODEL,
        "voice_settings": {"stability": 0.40, "similarity_boost": 0.85, "style": 0.35, "use_speaker_boost": True}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": ELEVEN_KEY, "content-type": "application/json", "accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def _cap_ok():
    today = time.strftime("%Y-%m-%d")
    if _day["date"] != today:
        _day["date"] = today; _day["n"] = 0
    _day["n"] += 1
    return _day["n"] <= DAILY_CAP

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", extra=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def _cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part[len(name) + 1:]
        return ""

    def _authed(self):
        if not PASSCODE:
            return True
        return self._cookie("sana_ok") == COOKIE_TOKEN

    def do_GET(self):
        if self.path in ("/", "/index.html") or self.path.startswith("/MeritMind"):
            if not self._authed():
                self._send(200, GATE_PAGE, "text/html; charset=utf-8"); return
            try:
                with open(HTML, "r", encoding="utf-8") as f:
                    page = f.read()
                if "</body>" in page:
                    page = page.replace("</body>", FEEDBACK_WIDGET + CRISIS_ACTIONS + "</body>", 1)
                else:
                    page += FEEDBACK_WIDGET + CRISIS_ACTIONS
                self._send(200, page, "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "Put sana_helper.py in the same folder as " + HTML, "text/plain")
        elif self.path == "/status":
            self._send(200, json.dumps({"live": bool(KEY), "tts": bool(ELEVEN_KEY and ELEVEN_VOICE),
                                        "classifier": bool(KEY), "gated": bool(PASSCODE), "model": MODEL}))
        elif self.path == "/voices":
            self._send(200, json.dumps({"voices": VOICES, "default": ELEVEN_VOICE}))
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path == "/gate":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                _b = json.loads(self.rfile.read(n) or "{}"); pc = _b.get("passcode", ""); _v = _b.get("voice", "")
                if PASSCODE and pc == PASSCODE:
                    self._send(200, json.dumps({"ok": True}), extra=[
                        ("Set-Cookie", f"sana_ok={COOKIE_TOKEN}; Path=/; Max-Age=604800; SameSite=Lax"),
                        ("Set-Cookie", f"sana_voice={_v if _v in _VOICE_IDS else ELEVEN_VOICE}; Path=/; Max-Age=604800; SameSite=Lax")])
                else:
                    self._send(403, json.dumps({"ok": False}))
            except Exception as e:
                self._send(500, str(e), "text/plain")
            return
        if self.path == "/feedback":
            if not self._authed():
                self._send(403, "gated", "text/plain"); return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                d = json.loads(self.rfile.read(n) or "{}")
                rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "rating": d.get("rating"),
                       "text": (d.get("text") or "")[:2000], "ua": (d.get("ua") or "")[:200]}
                with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                self._send(200, json.dumps({"ok": True}))
            except Exception as e:
                self._send(500, str(e), "text/plain")
            return
        if self.path == "/tts":
            if not self._authed():
                self._send(403, "gated", "text/plain"); return
            if not (ELEVEN_KEY and ELEVEN_VOICE):
                self._send(503, "no elevenlabs key/voice", "text/plain"); return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                d = json.loads(self.rfile.read(n) or "{}")
                req_v = d.get("voice_id")
                cv = self._cookie("sana_voice")
                vid = req_v if req_v in _VOICE_IDS else (cv if cv in _VOICE_IDS else ELEVEN_VOICE)
                self._send(200, eleven_tts(d.get("text", ""), vid), "audio/mpeg"); return
            except urllib.error.HTTPError as e:
                self._send(502, "elevenlabs " + str(e.code) + ": " + e.read().decode()[:150], "text/plain"); return
            except Exception as e:
                self._send(500, str(e), "text/plain"); return
        if self.path == "/classify":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                text = json.loads(self.rfile.read(n) or "{}").get("text", "")
                self._send(200, json.dumps({"route": classify_risk(text)})); return
            except Exception as e:
                self._send(500, str(e), "text/plain"); return
        if self.path == "/chat":
            if not self._authed():
                self._send(403, json.dumps({"reply": "(Please enter the passcode first.)"})); return
            if not KEY:
                self._send(200, json.dumps({"reply": "(No key set - restart me with ANTHROPIC_API_KEY.)"})); return
            if not _cap_ok():
                self._send(200, json.dumps({"reply": "Sana's had a lot of conversations today and is resting to keep "
                    "things running. Please try again tomorrow - thank you for testing."})); return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(n) or "{}")
                msgs = data.get("messages", [])
                last_user = ""
                for m in reversed(msgs):
                    if m.get("role") == "user":
                        last_user = m.get("content", ""); break
                if classify_risk(last_user):
                    self._send(200, json.dumps({"reply": CRISIS_RESPONSE, "crisis": True})); return
                reply = anthropic_reply(msgs, data.get("model"), data.get("system"))
                self._send(200, json.dumps({"reply": reply}))
            except urllib.error.HTTPError as e:
                self._send(200, json.dumps({"reply": f"(Anthropic API error {e.code}: {e.read().decode()[:180]})"}))
            except Exception as e:
                self._send(200, json.dumps({"reply": f"(Helper error: {e})"}))
            return
        self._send(404, "not found", "text/plain")

    def log_message(self, *a): pass

if __name__ == "__main__":
    voice = "human voice ON (ElevenLabs)" if (ELEVEN_KEY and ELEVEN_VOICE) else "browser voice"
    clf = "risk classifier ON" if KEY else "classifier OFF (no key)"
    gate = "gate ON (passcode set)" if PASSCODE else "gate OFF (local mode)"
    print("\n  Sana helper running - Claude: " + ("LIVE" if KEY else "no key") +
          " | Voice: " + voice + " | Safety: " + clf)
    print("  Sharing   - " + gate + f" | daily cap: {DAILY_CAP} | feedback -> {FEEDBACK_FILE}")
    print(f"  -> Listening on 0.0.0.0:{PORT}")
    print("  -> Press Ctrl+C to stop.\n")
    try:
        HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped. Bye!\n")
