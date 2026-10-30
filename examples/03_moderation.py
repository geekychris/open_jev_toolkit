"""Policy checks with your own definitions. Descriptions on options carry the policy text."""
from openjev_client import Jev, choice, yesno

jev = Jev()
POLICY = {
    "ok": "Acceptable content.",
    "spam": "Unsolicited promotion, links to buy things, scams.",
    "harassment": "Insults or attacks aimed at a person.",
    "legal_threat": "Threatens lawsuits or legal action.",
    "personal_data": "Shares someone's phone number, address, or account details.",
}
posts = [
    "Great write-up, the section on caching saved me an afternoon.",
    "CHEAP WATCHES!!! visit best-replicas dot biz for 90% off",
    "You're an idiot and everyone on this forum knows it.",
    "If you don't remove my photo by Friday my lawyer will be in touch.",
    "Call Dave on 555-0142, he lives at 12 Elm St, tell him I sent you.",
]
for p in posts:
    r = jev.ask(p, label=choice("Which policy category applies to this post?", POLICY),
                remove=yesno("Should a moderator remove this post?"))
    print(f"{r['label'].choice:<14} remove={r['remove'].p:.2f}  | {p[:60]}")
