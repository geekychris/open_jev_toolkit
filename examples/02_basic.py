"""The three question types through the Python client: route, flag, score."""
from openjev_client import Jev, choice, score, yesno

jev = Jev()
tickets = [
    "I was charged twice for my order last week and nobody has replied.",
    "The package says delivered but it's not on my porch. Can you check with the courier?",
    "After the latest update the app crashes every time I open the settings page.",
    "Just wanted to say thanks, the replacement arrived quickly!",
]
for t in tickets:
    r = jev.ask(t,
                route=choice("Which team should handle this?", ["billing", "shipping", "technical", "no action needed"]),
                angry=yesno("Is the customer angry or frustrated?"),
                urgency=score("How urgent is this?", ["can wait", "this week", "today", "right now"]))
    print(f"{r.elapsed_ms:6.0f} ms  route={r['route'].choice:<16} conf={r['route'].confidence:.2f}  "
          f"angry={r['angry'].p:.2f}  urgency={r['urgency'].score:.2f} ({r['urgency'].choice})  | {t[:50]}")
