"""A stand-in for Ollama used by the Windows CI test (stdlib only).

Answers like Ollama on 127.0.0.1:11434: lists one installed model, plans and replies in
Arabic, and refuses chat requests that do not ask for the larger context window.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "gemma4:12b"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self._json(200, {"status": "Ollama is running"})
        if self.path == "/api/tags":
            return self._json(200, {"models": [{"name": MODEL}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path != "/api/chat":
            return self._json(404, {"error": "not found"})
        if (req.get("options") or {}).get("num_ctx", 0) < 16384:
            return self._json(400, {"error": "context window too small"})
        prompt = req["messages"][-1]["content"]
        if "You are the planner" in prompt:
            request = prompt.split("<request>")[-1].split("</request>")[0].strip()
            if "المفكرة" in request and "notepad" in prompt:
                # PC control test: open Notepad, then type the text after the colon.
                steps = [{"agent": "pc", "action": "open_app", "params": {"app": "notepad"}, "description": "فتح المفكرة"},
                         {"agent": "pc", "action": "type_text", "params": {"text": request.split(":", 1)[-1].strip()},
                          "description": "كتابة النص"}]
            else:
                steps = [{"agent": "manager", "action": "chat", "params": {"message": request}, "description": "الرد"}]
            text = json.dumps({"summary": "خطة", "clarification": "", "steps": steps}, ensure_ascii=False)
        else:
            text = "1. مخبزة صغيرة\n2. توصيل طلبات\n3. متجر إلكتروني للملابس"
        self._json(200, {"model": MODEL, "message": {"role": "assistant", "content": text},
                         "prompt_eval_count": 50, "eval_count": 20, "done": True})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
