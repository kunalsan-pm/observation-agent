## Information about the data

- **3 organizations** (`ORG-AAA`, `ORG-BBB`, `ORG-CCC`) running **5 projects** (`PRJ-1` … `PRJ-5`).
- Each project has its own observation **template** (topics → questions, some marked `is_critical`), its own cadence policy (`cadence_days`), and its own scoring threshold.
- **Field observers** complete observations against **associates**; each observation produces ~7–12 question-level responses (0–100 score + short comment) plus possibly a free-text **coach note**.

Today's date for this exercise is **2026-05-12** (the fixture data is anchored to it).

### How the fields hang together

```
Organization (org_id)
└── Project (project_id, org_id, cadence_days, score_threshold, template_id)
    ├── Template (template_id)
    │   └── Topic (topic_id)
    │       └── Question (question_id, is_critical)
    └── User (user_id, org_id, project_id, role, manager_id)
        ├── role = manager  ──┐
        ├── role = observer   │  managers own associates via manager_id
        └── role = associate ─┘
            └── Observation (observation_id, subject_id=associate, observer_id, project_id, template_id, completed_at, overall_score)
                ├── Response (one row per question, see shape below)
                └── CoachNote (observation_id, subject_id, author_id, body)
```

**Observation response row** (`observation_responses.parquet`, ~9,000 rows) :

| Column            | Type       | Notes                                                |
| ----------------- | ---------- | ---------------------------------------------------- |
| `response_id`     | str        | `RSP-XXXXXXX`                                        |
| `observation_id`  | str        | `OBS-XXXXXX` — joins to `observations.json`          |
| `project_id`      | str        | denormalized from the parent observation             |
| `org_id`          | str        | denormalized                                         |
| `subject_user_id` | str        | the associate being observed                         |
| `topic_id`        | str        | e.g. `safety_procedures`                             |
| `question_id`     | str        | e.g. `Q-101`                                         |
| `question_text`   | str        | denormalized from the template                       |
| `is_critical`     | bool       | denormalized from the template's question definition |
| `score`           | float      | 0–100                                                |
| `completed_at`    | ISO string | matches the parent observation                       |
| `comment`         | str        | short free-text per-response comment                 |

Other things to keep in mind:

- A **project** picks the template, cadence, and threshold that apply to every observation under it.
- An **associate**'s manager is the user whose `user_id` matches the associate's `manager_id`.

---

## The questions

Three groups below. **Aim to get your agent answering at least one from each group well** — depth on the right things beats breadth done shallow. You don't need to grind through all eleven.

**Output requirement:** for every question the agent answers, it must also write the answer to a markdown file in `outputs/`. The chat response can be a short summary; the markdown file is the durable artifact with the full answer, citations (observation IDs, associate IDs, note IDs), and any supporting detail.

### Lookups

1. **How many organizations are in the system, and what industries do they cover?**
2. **What cadence policy and score threshold does each project under ORG-AAA use?**
3. **Who are the managers responsible for associates in PRJ-1?**

### Connections

4. **Show me everything about observation OBS-000042: who was observed, by whom, when, the overall score, and any coach notes attached to it.**
5. **Which observers in PRJ-3 have completed the most observations in the last 30 days?**

### Patterns

6. **In ORG-AAA, which associates have a sustained pattern of low scoring on a specific topic over their last several observations?** Pick a reasonable definition of "sustained" and defend it, and back the answer with specific observation IDs.

7. **Across the whole dataset, are there any recent critical-question failures (i.e. a critical-question score that should worry a safety manager)?** List the associate, the question, the observation, and the date.

8. **In ORG-CCC, find any associates whose coach notes suggest a recurring concern — not a single bad day, but a pattern.** Cite the specific notes.

9. **Which associates haven't been observed in longer than their project's cadence policy allows, and how far past due are they?**

10. **Are there any associates whose recent observation scores have declined meaningfully compared to their own earlier baseline (not the cohort)?** Be specific about what "meaningfully" means.

11. **Pick one flagged associate from question 6, 7, 8, 9, or 10 and draft a short coaching-action proposal for their manager — what the pattern is, the evidence (observation IDs, scores, note IDs), and a concrete next step.** Write it to `outputs/`.
