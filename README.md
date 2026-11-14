# BhoomiCheck

Automated due diligence on a land parcel before purchase, for Warangal
(KUDA jurisdiction), Telangana, India.

## The problem this exists to catch

A plot approved only by the local body looks clean at purchase. Years
later the buyer applies to KUDA for building permission and discovers a
master-plan road runs through the land — forcing a setback surrender.
They own a plot they cannot build on.

Nothing in the sale deed says this. The information is public, in a
master plan nobody reads, and the question "does a proposed road touch
*this* survey number" is tedious enough that it mostly goes unasked.

The same shape of trap recurs: assigned land that cannot legally be
sold, an undischarged mortgage, a lake FTL buffer, a broken deed chain,
pending litigation under a name nobody cross-checked.

BhoomiCheck runs those checks and produces a report where **every claim
carries its source, its retrieval date, and a confidence level** — and
where a check that could not be performed says so, loudly, rather than
passing silently.

## What it actually does

Six specialist agents, an orchestrator, and a risk/report agent:

| Agent | Source | Answers |
|---|---|---|
| Land Records | Bhu Bharati | pattadar, extent, land type, **assigned land** |
| Zoning | KUDA master plan + road geometry | **road setback**, zone class, FTL buffer |
| Litigation | eCourts (district + high court) | possible name matches |
| Owner Background | IGRS | POA sale, seller identity, **encumbrances** |
| Growth Potential | KUDA master plan | growth corridor, nearby upgrades |
| Document Analysis | uploaded PDFs | deed chain, parties, extent mismatches |

The orchestrator is a LangGraph state machine. The four portal-backed
agents pause on a real `interrupt()`, tell the user exactly what to fetch
and from where, and resume when the file is uploaded. State is
checkpointed to SQLite, so a check can span days.

Findings are scored by rules in [`scoring/rules.yaml`](src/bhoomicheck/scoring/rules.yaml)
— not in prompts — starting at 100 and deducting per finding, so the
weighting is diffable and arguable.

## The road-setback check

This is the check the project exists for, and the one worth looking at.

It began as a vision model reading a master-plan screenshot and
returning a boolean. That answer could not be reproduced, checked, or
shown to be wrong.

It is now computed. [`geo/road_network.py`](src/bhoomicheck/geo/road_network.py)
loads the proposed road network (each segment carrying its width from
the plan's own layer naming), projects it onto a local metric frame
centred on the parcel, and measures the distance to every road class —
from a single coordinate, or, better, from the parcel's boundary ring.
A real result:

```
Proposed road   Width   Half-width   Distance to centreline   Result
   30 M            30         15.0                    207.7   clear by 192.7 m
   60 M            60         30.0                    938.8   clear by 908.8 m
   45 M            45         22.5                  1,213.7   clear by 1,191.2 m
   90 M            90         45.0                  2,220.9   clear by 2,175.9 m
```

So the report says *"the nearest proposed road is a 30 M sub-arterial
whose centreline is 207.7 m away; the corridor extends 15 m either side;
clear by 192.7 m"* — a claim a surveyor can confirm or refute. Where the
measurement contradicts the screenshot reading, the finding says so.

No geospatial dependency: the mathematics is a local projection and
point-to-segment distance, short enough to read. Pulling in GEOS and
PROJ for two functions would have hidden the only interesting part.

**Point versus boundary.** Measuring from one coordinate is easier to
obtain but can understate the risk: a corridor can clip a parcel's
corner while sitting comfortably clear of its centre. On the test
fixture a road reported *"clear by 77.5 m"* from the centre point turned
out to be **2.5 m inside the parcel** once its real edges were used. So
a point-based clearance is reported at medium confidence with that
limitation stated; only a boundary-based one earns high confidence. A
centreline that crosses the parcel outright is distinguished from one
that merely reaches over an edge, because the whole corridor width cuts
through the land.

**What it does not do.** It measures the road corridor only.
Building-rule setbacks from the road boundary are additional and are not
modelled, so a parcel reported clear has cleared the widening, not the
whole permission problem. And it gives *precision*, not *accuracy*: it
can tell you a centreline is 207.7 m away but not that the centreline
was digitised in the right place.

## Design rules

1. **Every agent returns a Pydantic schema, never free text.** A
   validation failure retries with the error fed back to the model.
2. **One adapter per government source**, supporting `api`, `scrape` or
   `manual` modes. Manual means human-in-the-loop: the system says
   exactly what to fetch, then parses the upload.
3. **Provenance on every fact** — source, document or URL, fetch date,
   and the source's own "last updated" date where it states one. A
   report claim without provenance is a bug.
4. **Confidence on every finding.** Telangana records migrated from
   Dharani to Bhu Bharati are messy; an agent that cannot express
   uncertainty lies.
5. **Scoring rules in YAML**, auditable and testable.
6. **Fail loudly.** Missing data is reported as "not verified", never
   silently treated as "no problem found". A parcel outside the mapped
   area does not get reported as comfortably clear of roads.

## Guardrails

- **No captcha or OTP bypass**, ever. Captcha-gated portals are
  manual-mode permanently, not until someone writes a scraper. This is a
  legal and ethical line, not a technical limitation.
- **Name-based court matches are reported as possible, unconfirmed
  matches** requiring human verification — never as verdicts about a
  named person.
- **Every report ships a disclaimer**: automated decision-support from
  public records that may be incomplete or outdated; not legal advice;
  consult a licensed advocate before purchase.
- Scrapers rate-limit and respect robots.txt. Secrets live in `.env`.

## What is validated, and what is not

Being specific about this, because "it runs" and "its output is true"
are different claims.

| | Status |
|---|---|
| Geometry engine | **Validated.** Hand-checkable synthetic fixture (point and boundary modes); real-world result reproduced from source data |
| Bhu Bharati extraction | **Validated** against a real result page (13/15 fields, both nulls correct). Produced three schema corrections |
| KUDA, eCourts, IGRS extraction | **Not validated.** `parse()` is implemented and field labels are best-effort; those adapters say so in their docstrings |
| Orchestration, scoring, reporting | 200 tests, including two-process SQLite resume |

Unreadable fields become `null`, which becomes a `not_verified` finding
— never a guess. The extraction prompt is explicit that a wrong value
becomes a false statement in a report someone may buy land on.

## Running it

Requires Python 3.11+. A Google Gemini API key in `.env` as
`GEMINI_API_KEY` is needed for document and screenshot extraction.

```bash
python -m venv venv && source venv/bin/activate
pip install -e '.[dev]'
pytest                                    # 200 tests, no network, no portals
```

Browser UI:

```bash
uvicorn bhoomicheck.api.app:app --factory --port 8000
```

CLI:

```bash
python -m bhoomicheck \
  --survey-no 16/D --village Chennaram --mandal Wardhannapet \
  --seller-name "<as printed on the deed>" \
  --boundary "17.8562,79.6299; 17.8562,79.6306; 17.8568,79.6306" \
  --html report.html \
  sale_deed.pdf
```

### Road network data

Not shipped in this repository. Point the tool at a distilled network:

```bash
python scripts/distil_road_network.py plan.kml ~/.bhoomicheck/road_network.json \
  --label "..." --source "..."
```

`BHOOMICHECK_ROAD_NETWORK` overrides the location. Without it the
setback check falls back to reading a map screenshot — visibly, at lower
confidence, rather than failing. Tests use a synthetic fixture and never
need real plan data.

## Stack

Python 3.11+ · Pydantic v2 · LangGraph · FastAPI · pytest

LangGraph over CrewAI deliberately: it hides less. The HTML report and
the geometry are both dependency-free, because each would otherwise have
added a library (WeasyPrint, Shapely) to serve one function.

---

Automated decision-support based on public records that may be
incomplete or outdated. Not legal advice. Consult a licensed advocate
before any purchase decision.
