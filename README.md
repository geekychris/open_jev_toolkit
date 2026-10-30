# OpenJev toolkit

Run [OpenJev](https://huggingface.co/openjev) locally on Apple silicon, with an installer, a server
script, a small Python client and worked examples.

OpenJev is a **decision model**, not a chat model. You send a *state* (text, or JSON such as a log
tail, a ticket or a DOM) and typed questions with your own labels. You get back calibrated answers
from one forward pass per question:

| type | you send | you get |
|---|---|---|
| `choice` | up to 52 labels (more run in passes), optional descriptions | winning label, probability per label, confidence |
| `noul` (yes/no) | a statement or question | probability of yes |
| `score` | ordered levels, lowest first | expected level, distribution, confidence |

You don't train it per task: labels and policy text go in the request.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/geekychris/open_jev_toolkit/main/scripts/install.sh | bash
```

This clones into `~/open_jev_toolkit` (override with `OPENJEV_HOME`) and runs the installer from
there. From a checkout, run `scripts/install.sh` directly. Pass options after `bash -s --`:

```bash
curl -fsSL .../scripts/install.sh | bash -s -- --profile flash-4bit
```

The installer:
- installs `uv` if it's missing;
- builds `runtime/venv` on Python 3.12, using the MLX stack versions the model cards were validated with;
- downloads the model at a pinned revision, plus the upstream helper (`shim.py`, whose sha256 is checked);
- runs a self-check.

Everything it creates lives under `runtime/`, which is git-ignored.

### Profiles

| profile | build | disk | minimum RAM | notes |
|---|---|---|---|---|
| `27b-8bit` | `openjev/openjev-MLX` | 27 GB | 64 GB | default on 64 GB or more; same accuracy as the served 16-bit model (84.0% on the card's 10k set) |
| `27b-4bit` | `openjev/openjev-MLX-4bit` | 15 GB | 32 GB | default on 32 GB or more |
| `flash-8bit` | `openjev/OpenJev-Flash-9B-MLX` | 10 GB | 24 GB | 9B; 81.4% on JevBench |
| `flash-4bit` | `openjev/OpenJev-Flash-9B-MLX-4bit` | 5 GB | 16 GB | default below 32 GB; 80.5% on JevBench; fastest |

Every profile carries the calibration constants (`READOUT_T`, `READOUT_NOUL_T`) that its model card
gives. You can install several profiles and switch between them with `serve.sh start --profile ...`.

The MLX builds are **text only**: they take JSON, DOM and logs, but not screenshots. Screenshot input
needs the full model on vLLM with an NVIDIA GPU; see `serve/SERVE.md` in `openjev/openjev`.

## Run

```bash
scripts/serve.sh start            # background, waits until ready; http://127.0.0.1:3000
scripts/serve.sh status | logs | stop | restart
scripts/serve.sh start --profile 27b-8bit --port 3001
scripts/serve.sh fg               # run in the foreground
```

Environment variables:
- `OPENJEV_PORT`, `OPENJEV_HOST`: where the server listens.
- `SHIM_TOKEN`: if set, requests must send `Authorization: Bearer <token>`. Set it before binding anything other than localhost.

Endpoints: `POST /v1/systemone`, `GET /v1/version`.

## Use it

Raw HTTP: see `examples/01_curl.sh`.

Python, with the stdlib-only client `openjev_client`. The installer puts it in the venv; you can also
`pip install -e .`, or copy the one file into another project:

```python
from openjev_client import Jev, choice, yesno, score

jev = Jev()   # $OPENJEV_URL or http://127.0.0.1:3000
r = jev.ask({"step": "verify", "log_tail": log},
            kind=choice("What kind of failure is this?", {"compile": "...", "test_assertion": "...", "environment": "..."}),
            transient=yesno("Is this failure likely to pass on a plain retry?"))
r["kind"].choice, r["kind"].confidence, r["transient"].p
```

Shell:

```bash
echo "undefined: OrderStatusPending" | runtime/venv/bin/openjev choose "What kind of failure?" compile test lint network
runtime/venv/bin/openjev check "Is the customer angry?" < ticket.txt
```

## Examples

`examples/run_all.sh` runs all of them.

| file | shows |
|---|---|
| `01_curl.sh` | the raw API: three question types in one request |
| `02_basic.py` | ticket routing, sentiment, urgency |
| `03_moderation.py` | your own policy as option descriptions |
| `04_llm_judge.py` | judging another model's answer: grounded, verdict, quality |
| `05_agent_step.py` | a browser-agent step: which element, which operation, done or not |
| `06_build_failure_triage.py` | classifying a failed build/test log and choosing retry, fix code, fix env or escalate; includes expected labels, so it doubles as an eval |

Measured on an M4 Max (128 GB). The MLX stand-in runs one prefill at a time, so the questions in a
request are serialised:

| profile | 3 short questions (02) | 3 questions over a ~10-line log (06) | 06 next-action correct |
|---|---|---|---|
| `flash-4bit` | ~0.4 s | ~0.9 s | 5/7 |
| `27b-8bit` | ~1.3 s | ~3.3 s | 6/7 |

The 27B also handles cases where judgment matters: it answers "no action needed" for a thank-you
note, "partly grounded" for a half-true answer, and "escalate" when an agent is going in circles.
Flash is the choice for hot loops; 27B is the choice when a wrong call is expensive.

## Prompting vs fine-tuning

Start with prompting: write labels with one-line descriptions, and use `yesno` for a single property.
Fine-tuning is worth considering only after you have a labelled set from your own pipeline and a
measured gap. `06_build_failure_triage.py` is the shape of that measurement.

Two levers come before fine-tuning:
- **Option descriptions.** They carry your definitions. Most errors come from ambiguous labels.
- **Question decomposition.** Ask "is the agent going in circles?" as its own question rather than
  hoping a four-way `next` choice infers it from history.

## Next: agentic builds (cloud_stack)

cloud_stack already has a `jev` agent backing (`internal/agent/backing_jev.go`). Its wire format
(questions as a list, `kind: bool`, `answers[]` with `answer`/`confidence`) is not OpenJev's
`/v1/systemone` shape, so connecting the two needs an adapter on one side. Candidate decision points:
- classifying verify failures to drive the repair-loop `gate`;
- deciding progress vs escalation on repeat iterations;
- a second-opinion risk score before `approve-risk`;
- triage to choose the stack profile and workflow;
- detecting whether a session turn is done instead of using `autoAdvanceDelaySeconds`.

## Licences

- OpenJev weights: CC BY-NC 4.0, for non-commercial use. A commercial licence is available from support@loopai.com.
- Upstream `helper/` files: Apache 2.0.
- This toolkit: Apache 2.0.
