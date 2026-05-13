---
name: obsidian-crm-data-contract
description: >-
  Shared Hermes data contract for the Obsidian revenue CRM: required fields,
  HubSpot-style associations, source/provenance rules, pipeline stages, next
  actions, and handoff blocks. Use by both jerme (data feeder) and Archap
  (vault librarian) before creating or updating CRM notes.
version: 1.0.0
author: local
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [obsidian, crm, revenue, data-contract, hubspot, handoff]
    related_skills: [archap-librarian, jerme-board-operator]
---

# Obsidian CRM Data Contract

This is the **shared contract** between **jerme** and **Archap**.

- **jerme** gathers and structures facts, sources, angles, people, and next actions.
- **Archap** writes the Obsidian vault records and preserves provenance/timelines.
- Both agents must use the **same fields**, **same association logic**, and **same no-fiction rule**.

Vault root comes from `OBSIDIAN_VAULT`. Canonical docs in the vault:

- `_MOCs/CRM — Object model (HubSpot-aligned).md`
- `_MOCs/CRM — Jerme and Archap handoff.md`
- `_MOCs/CRM — Deal coordination (read before outreach).md`
- `_MOCs/CRM — View caps and rollups.md`
- `Revenue Cockpit.md`

## Non-negotiables

1. **No invented CRM state.** Unknown values are `TBD` or empty, never guessed.
2. **Every fact has provenance.** Use `source_refs`, `origin_summary`, and wikilinks.
3. **Deal is SSOT for associations.** Company/person rollups are views or caches.
4. **Active revenue records need a next action.** Missing `next_action` / due date is a defect or an explicit `TBD` with an Activity to resolve.
5. **Timeline is append-only.** Corrections are new rows, never silent rewrites.

## Object Types and Folders

| type | folder | purpose |
|------|--------|---------|
| `company` | `Companies/` | Account / organization |
| `person` | `People/` | Contact / buyer / champion / stakeholder |
| `deal` | `Deals/` | Opportunity / pipeline row |
| `meeting` | `Meetings/` | Call/event/conversation evidence |
| `activity` | `Activities/` | Revenue-moving task |
| `source` | `Sources/` | Origin surface: Luma, referral, inbound, outbound list, research |

## Required Provenance Block

Every object should carry:

```yaml
origin_date: YYYY-MM-DD
origin_summary: ""
requested_by: ""
sourced_by: ""
hermes_profile: jerme | archap | manual
source_refs: []
spawned_from_meeting: ""
```

For `activity`, `spawned_from_meeting` is optional; for `source`, `source_url` is preferred.

## Deal Fields

Minimum viable active deal:

```yaml
type: deal
id: ""
status: discovery          # discovery | active | stalled | won | lost | archived
stage: source              # source | researched | outreach | conversation | qualified | proposal | negotiation | closed_won | closed_lost
source_type: ""            # outbound | inbound | referral | event | partner | ecosystem | research
source_id: ""
amount: ""
currency: USD
close_date: ""
probability: 0
priority: 3                # 1 high, 2 medium, 3 normal
next_action: ""
next_action_due: ""
last_touch: ""
primary_company_id: ""
associated_company_ids: []
primary_contact_id: ""
associated_contact_ids: []
primary_owner: ""
cc_people: []
parent_deal_id: ""
child_deal_ids: []
```

Deal body must include:

- `## Association summary (human-readable)`
- `## Stakeholders (internal + external)`
- `## Deal timeline (append-only)`
- `## Qualification`
- `## Comms map`
- `## Next steps`

## Association Rules

- Deal → Company: exactly one `primary_company_id` once real; optional `associated_company_ids`.
- Deal → Person: optional `primary_contact_id`; optional `associated_contact_ids`.
- Company ↔ Person: many-to-many with `associated_contact_ids` and `associated_company_ids`.
- Deal ↔ Deal: parent/child only unless a future peer-link field is added. No cycles.
- Activity can link to many deals/companies/people via `associated_*_ids`.
- Source explains why a record exists; link source notes in bodies when material.

## Pipeline Stage Exit Criteria

| stage | exit criteria |
|-------|---------------|
| `source` | Source captured; target company/person exists or is queued |
| `researched` | Pain hypothesis + route-in documented |
| `outreach` | First contact sent or intro requested |
| `conversation` | Human exchange started or meeting booked |
| `qualified` | Primary company/contact/owner + use case + next action |
| `proposal` | Proposal/scope/pricing in timeline |
| `negotiation` | Decision process/blockers documented |
| `closed_won` | Amount + close date + handoff notes |
| `closed_lost` | Loss reason + reusable learning |

## Handoff Block (jerme → Archap)

Use this exact shape when creating/updating records:

```text
ENTITY: company | person | deal | source | activity | meeting
NAME:
ID:
REQUESTED_BY:
SOURCE:
FACTS:
- ...
OPEN_QUESTIONS:
- ...
RELATED:
- ...
NEXT_REVENUE_ACTION:
NEXT_ACTION_DUE:

# Deal-only fields
STAGE:
STATUS:
SOURCE_TYPE:
SOURCE_ID:
PRIMARY_COMPANY_ID:
ASSOCIATED_COMPANY_IDS:
PRIMARY_CONTACT_ID:
ASSOCIATED_CONTACT_IDS:
PRIMARY_OWNER:
PARENT_DEAL_ID:
```

## Quality Gate

Before saying “done,” check:

- Does the object have `type`, `id`, `tags`, and provenance?
- If active, does it have a next action and owner?
- If deal, is the timeline append-only and comms map present?
- Are wikilinks present for graph navigation?
- Are unknowns marked as `TBD`, not fabricated?
