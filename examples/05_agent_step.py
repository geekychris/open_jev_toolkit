"""A browser-agent step: given the goal, page and candidate elements, pick the element, the operation,
and whether the task is already done. (Text/DOM only; the MLX builds have no screenshot input.)"""
from openjev_client import Jev, choice, yesno

jev = Jev()
state = {
    "goal": "Subscribe to the newsletter with chris@example.com",
    "page": {"url": "https://example.com/blog", "title": "Blog"},
    "elements": [
        {"index": 0, "tag": "a", "text": "Home"},
        {"index": 1, "tag": "a", "text": "Pricing"},
        {"index": 2, "tag": "input", "label": "Email address", "value": ""},
        {"index": 3, "tag": "button", "text": "Subscribe"},
        {"index": 4, "tag": "a", "text": "Contact sales"},
    ],
    "recent_actions": [],
}
cands = {f"e{e['index']}": f"<{e['tag']}> {e.get('text') or e.get('label')}" for e in state["elements"]}
r = jev.ask(state,
            target=choice("Which element should the agent act on next?", cands),
            op=choice("Which operation comes next?", {"click": None, "type": "Enter text into a field", "scroll": None, "done": "The goal is achieved"}),
            done=yesno("Is the goal already achieved?"))
print("target:", r["target"].choice, cands[r["target"].choice], "| op:", r["op"].choice, "| done p:", round(r["done"].p, 3))

state["elements"][2]["value"] = "chris@example.com"
state["recent_actions"] = [{"kind": "type", "action": "e2", "page_changed": False}]
r = jev.ask(state, target=choice("Which element should the agent act on next?", cands),
            op=choice("Which operation comes next?", {"click": None, "type": "Enter text into a field", "scroll": None, "done": "The goal is achieved"}))
print("after typing -> target:", r["target"].choice, cands[r["target"].choice], "| op:", r["op"].choice)
