"""Use OpenJev as a fast judge of another model's output: groundedness and a rubric score."""
from openjev_client import Jev, choice, score, yesno

jev = Jev()
source = ("The Model 3 heat pump was introduced in 2021. It reduces winter range loss by about 10% "
          "compared with resistive heating. It is not available on vehicles built before 2021.")
answers = {
    "faithful": "The heat pump arrived in 2021 and cuts winter range loss by roughly 10% versus resistive heating.",
    "hallucinated": "The heat pump arrived in 2019, doubles winter range, and can be retrofitted to any older car.",
    "partial": "It came out in 2021. It also makes the car charge faster in cold weather.",
}
for name, ans in answers.items():
    state = {"source": source, "answer": ans}
    r = jev.ask(state,
                grounded=yesno("Is every claim in the answer supported by the source?"),
                verdict=choice("How does the answer relate to the source?", {
                    "grounded": "All claims are supported.",
                    "partly_grounded": "Some claims are supported, at least one is not.",
                    "contradicts": "At least one claim contradicts the source."}),
                quality=score("Rate the answer's faithfulness to the source.", ["poor", "fair", "good", "excellent"]))
    print(f"{name:<13} grounded={r['grounded'].p:.2f}  verdict={r['verdict'].choice:<16} quality={r['quality'].score:.2f}")
