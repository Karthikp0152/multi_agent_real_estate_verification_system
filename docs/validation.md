# Validation record

What has actually been checked against real government sources, what
came of it, and what has not been checked at all.

This exists because "it runs" and "its output is true" are different
claims, and only one of them is demonstrated by a passing test suite.
Every adapter in this project reads a government page that nobody
controls; a schema written from a portal's documentation is a guess
until a real page proves it. Each entry below records the sample, the
result, and anything the validation changed.

Portal checks were performed in **November 2026**. Government portals
change; treat anything here as true of that date, not forever.

**The samples are not in this repository.** They include a prohibition
register listing named individuals' land holdings, and a digitised
master plan belonging to a third party. Neither belongs in a public
repo, so this document records the findings rather than the files.

---

## Bhu Bharati — land records · VALIDATED

**Sample:** one real "Land Details Search" result (Warangal /
Wardhannapet / Chennaram, survey 16/D).

**Result:** 13 of 15 fields read correctly. Both nulls were correct.
`notes` came back empty, meaning the schema covered everything the page
showed.

**What it changed** — three corrections, each a real defect:

1. The page carries **three** distinct classification fields — "Nature
   of Land", "Classification of Land" and "Land Type" — not one. A
   single field forced the model to choose between them, and it picked
   `"Patta"` while the page's own Land Type read `"Agriculture Lands"`.
2. `prohibited_status` is **not on this page at all**. The field stays,
   but this source cannot fill it, so the matching finding is honestly
   `not_verified` and the gap is answered elsewhere (see 22-A below).
3. Father/Husband's name **is** present. It is the standard
   disambiguator for common Indian names, which is precisely the
   weakness in name-based court searches.

---

## Road geometry — master-plan setback · VALIDATED

**Samples:** a CAD-exported KML of the Warangal proposed road network,
and a coordinate read off the Bhu Bharati GIS viewer.

**Network:** 1,223 usable segments, 61,934 vertices, seven width
classes (90/60/45/30/24/18 M plus a 33 M outlier), cross-checked
against the published KUDA legend. Three segments carried no width and
were dropped rather than defaulted — a road of unknown width cannot
support a setback claim.

**Result for survey 16/D** (17.856513 N, 79.630249 E):

| Proposed road | Width | Half-width | Distance to centreline | Result |
|---|---|---|---|---|
| 30 M | 30 | 15.0 | 207.7 m | clear by 192.7 m |
| 60 M | 60 | 30.0 | 938.8 m | clear by 908.8 m |
| 45 M | 45 | 22.5 | 1,213.7 m | clear by 1,191.2 m |
| 90 M | 90 | 45.0 | 2,220.9 m | clear by 2,175.9 m |

**What it changed:** the check had been a vision model reading a map
screenshot and returning a boolean. It is now a measurement a surveyor
can refute. Distilling the KML to geometry plus width reproduced the
same distance to within 10 cm, confirming the rounding is immaterial.

**Point versus boundary.** On the synthetic fixture, a road reported
*"clear by 77.5 m"* from the parcel's centre point proved to be **2.5 m
inside the parcel** once its real edges were used. A corridor can clip
a corner while sitting clear of the centre. Point-based clearances are
therefore reported at medium confidence with that stated; only a
boundary measurement earns high.

**Not modelled:** building-rule setbacks from the road boundary, which
are additional. Clearing the widening is not clearing the permission
problem.

---

## IGRS — Section 22-A prohibited property · VALIDATED

**Samples:** five of the seven sub-clause registers for Chennaram
(Agriculture), fetched from the public page — no login required.

| register | form | result |
|---|---|---|
| 22 A (1)(a) | FORM-I, 15 page scans | **372 rows transcribed** |
| 22 A (1)(b) | FORM-II | `Nil` — empty register |
| 22 A (1)(c) Waqf | — | *"Prohibition data is under review"* |
| 22 A (1)(c) Endowment | — | *"Prohibition data is under review"* |
| 22 A (1)(d) | FORM-V | 4 rows (surplus land, COAH, with C.C. numbers) |
| 22 A (1)(e) | FORM-VI | `NIL` — empty register |
| Court cases & Others | live HTML table | empty for this village |

**Transcription accuracy on the 372-row scan:** the row count matches
the document's own final Sl.No.; `total_extent` read `232.16`, exactly
as the form's footer prints it; rows spot-checked across the full range
(`8/1`, `10/A/3`, `49/53/B/2`, `254/110/E`, `279A`, `464`) each
appeared exactly once; nothing was flagged unreadable. Truncation is
the failure mode that would matter most — a model stopping at row 40
reports a listed parcel as absent — and it did not occur.

**Result:** survey 16/D appears in none of the five published
registers. Reported as `not_verified`, not `verified_ok`, because two
registers are not published at all — the parcel cannot be cleared
against a register that does not publicly exist.

**What it changed:**

- **Vision, not text search.** The published PDFs are page scans with
  an OCR layer of very poor quality. Measured on the (a) list, the
  mandal name `WARDHANNAPET` appears as **seven different strings**
  (including `!?ARDHANNAPEI` and `/ARDHANNAPEI`), and `TG` appears as
  `T6` as often as `TG`. A text search returning "not found" would be
  indistinguishable from the OCR having mangled the number — reporting
  a listed parcel as clean.
- **Seven registers, four proformas, three behaviours.** They are not
  one kind of thing: scanned tables, a live HTML table whose survey
  number is split across two columns, and notices that the register is
  unpublished.
- **"Not published" ≠ "not fetched."** An unpublished register is not
  demanded from the user — they cannot obtain it — but it still blocks
  a clean verdict. The report names the department as the blocker.
- **`Nil` means empty, not unreadable.** A register stating there is
  nothing to read is a real answer; treating it as a failed parse would
  nag the user to re-fetch a page that already answered.

---

## eCourts — litigation · VALIDATED

**Sample:** the Warangal District Court party-name search form, and a
populated results table.

**What the form actually permits:** the party-name search makes
**Year a required field** and runs against **one court complex at a
time**. There is no "all years" or "all courts" option.

**What it changed — the most important correction in the project.**
The agent had been reporting `verified_ok` — *"no cases naming X as a
party were found"* — off a single search. A buyer reads that as "no
litigation"; the truth was "none in 2025, in one complex, among pending
cases only." It is now `not_verified` and scoped to exactly what was
searched.

**Results table:** `Serial Number | Case Type/Case Number/Case Year |
Petitioner versus Respondent | View`. There is **no status column** — a
row's status is whichever radio produced it, so a Pending-only search
silently omits every disposed matter. CNR number and filing date are
not on the list at all; they sit behind a click per case, so a name
returning forty rows would mean forty clicks.

**Extraction result:** read `year_searched: 2025`, `court_complex:
"Warangal, PDJ Court Complex"`, `status_filter: "pending"`, split the
combined case cell into number and type, and correctly left
`cnr_number` and `filing_date` null.

**A second defect this exposed:** the sample was a *Case Type* search,
so the name box was empty, and the agent emitted `2 case(s) naming ""
as a party were found` — a name-match finding identifying nobody. It
now refuses to attribute unattributable rows.

---

## Not validated

| source | status |
|---|---|
| **KUDA / 1acre master plan** | Field names unconfirmed against a real screenshot. Lower priority than it was: road geometry superseded `road_widening_affects_parcel`, its load-bearing field. It still supplies zone classification, FTL buffer and growth signals, none of which carry a verdict on their own. |
| **IGRS encumbrance result page** | Navigation, login and captcha confirmed; the *result* page unseen. The "By Property" search — the survey-number path this project wants — was **broken** at time of checking: selecting it leaves the Document Number form in place. An EC lookup therefore needs a document number from the sale deed. |

---

## How to re-run a validation

```bash
python scripts/verify_extraction.py <source> <file> \
    --survey-no 16/D --village Chennaram --mandal Wardhannapet
```

`<source>` is one of `land_records`, `prohibited`, `litigation`,
`owner_background`, `kuda`. It prints every field with nulls flagged,
the row count for list transcriptions, and the findings that result —
so a schema mismatch shows up as nulls rather than as a plausible wrong
answer.
