# Waypoint — demo recording script

Allow **12–15 minutes**, depending on browser waits. This recording is optional submission
material. Complete the [README setup](../README.md#setup) first. Spoken lines are in quotes;
the remaining text describes commands and on-screen actions.

---

## Before you press record

Screen layout: **terminal 1** (small, the app), **terminal 2** (main, replay), **terminal 3**
(the operator, used from scene 7), and room for a browser window beside them.

In **every** terminal, enter your cloned repository directory. If setup used
`UV_PROJECT_ENVIRONMENT`, export that same path here before activating:

```bash
source "${UV_PROJECT_ENVIRONMENT:-.venv}/bin/activate"
```

Terminal 1 — start the app fresh (a restart also clears any sub-accounts from rehearsals):

```bash
make app
```

Terminal 2 — a quick pre-flight (not recorded):

```bash
waypoint version
waypoint approvals
```

The supplied lookup 1.2.0 and sub-account 1.0.0 releases should already be approved; lookup
1.1.0 remains a draft. Extra drafts from rehearsals are harmless because this script selects
versions explicitly. If an irreversible rehearsal ended early, inspect `waypoint intervene intents`:
an unresolved intent makes the next run reconcile before starting new work. For an independent
fixture rehearsal, stop all runs and the app, archive the `.waypoint` directory, then restart
the app so browser state and operation history both start fresh.

Tips: make the terminal font large; replay prints full JSON, so pause on `"status"` and
`"outputs"`; handoff runs open a visible browser by themselves.

---

## Scene 1 — The problem (0:00–0:45)

Show the README top.

> "Banks run a long tail of back-office systems with no API — the only way in is the UI.
> Waypoint lets an AI agent drive those screens reliably and cheaply: a model figures out a
> flow **once**, that flow is compiled into a typed, versioned capability, and every run after
> that replays it with **no model in the loop**. When replay meets a state it won't act on, it
> hands the live session to a person."

> "One rule runs through the whole design: the system never acts on the absence of evidence."

## Scene 2 — A deliberately hostile legacy app (0:45–1:45)

Open [the local fixture](http://127.0.0.1:8080) in the browser. Sign in with
**operator1 / changeme** (fictional fixture credentials).

> "This is the stand-in for a 2006-era core banking console. It's built to defeat naive
> automation."

- Search member **12345**. Point at the grid: "Eight identical **View** links — you can't click
  'the View link'; you have to find the one in this member's row."
- Open the record. Click the **Accounts** tab: "Tabs are postbacks — the URL never changes."
- Point at **Mark for Review**: "A plain link that changes state — a GET that mutates. No list of
  dangerous button names would catch it."
- Point at the small unlabeled icon: "And a control with no name at all."

## Scene 3 — Discovery, once (1:45–3:15)

> "Discovery uses Claude Haiku 4.5. I recorded that live run earlier; here it replays from its
> cassette, so you see exactly the model's decisions without calling the API."

Terminal 2:

```bash
WAYPOINT_DEMO_DIR="$(mktemp -d)"
waypoint discover --capability-id lookup_member_balance \
  --goal "Look up member {{member_id}} and read their current savings balance" \
  --entry http://127.0.0.1:8080/console \
  --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal \
  --llm cassette --cassette evidence/runs/showcase-discovery-haiku/cassette.json \
  --headed --root "$WAYPOINT_DEMO_DIR/capabilities" --version 1.0.0
```

While the browser moves:

> "Member IDs, names and balances are redacted from the model's view. The discovery loop checks
> proposed expectations against the screen and gives the model corrective feedback while the
> page is still open."

When it prints `draft written ... no open gates`:

> "The result is a **draft** artifact: typed inputs and outputs, locators and checkpoints.
> Member-specific checks bind to the input reference, so another member can use the same flow."

Keep this terminal open: the same `WAYPOINT_DEMO_DIR` is used in scenes 4 and 5.
For another take, start scene 3 again with a fresh directory.

*(optional)* Show the new discovery run's `transcript.json` and the generated artifact at
`$WAYPOINT_DEMO_DIR/capabilities/lookup_member_balance/1.0.0.json`.

## Scene 4 — Drafts don't run (3:15–4:00)

```bash
waypoint replay lookup_member_balance --root "$WAYPOINT_DEMO_DIR/capabilities" \
  --version 1.0.0 --input member_id=12345
```

Point at `"status": "failure"` and `"code": "artifact_not_approved"`.

> "A draft is refused before a browser even starts. Approval is a person's decision, and it's
> bound to the artifact's content — edit one byte after approval and it's a draft again."

```bash
waypoint approvals --root "$WAYPOINT_DEMO_DIR/capabilities"
```

Open the generated artifact and review its input references, checkpoints and extraction targets.
Then approve that exact version:

```bash
waypoint approve lookup_member_balance --root "$WAYPOINT_DEMO_DIR/capabilities" \
  --version 1.0.0 --note "reviewed the newly discovered flow and extraction targets"
```

> "This is the artifact the discovery run just produced. Now that I've reviewed it, I approve
> that version for replay."

## Scene 5 — Replay with no model (4:00–5:30)

```bash
waypoint replay lookup_member_balance --root "$WAYPOINT_DEMO_DIR/capabilities" \
  --version 1.0.0 --input member_id=12345
```

Point at `"status": "success"` and `"outputs"`: **$4,281.19**, **active**.

```bash
waypoint replay lookup_member_balance --root "$WAYPOINT_DEMO_DIR/capabilities" \
  --version 1.0.0 --input member_id=67890
```

> "Same artifact, a different member, a different correct answer: **$912.04**, **dormant**."

> "That completes discovery, approval and replay of the generated artifact. For the remaining
> scenarios I switch to the supplied version 1.2.0, built from the handwritten base and reviewed
> with explicit business outcomes and recovery rules."

```bash
waypoint replay lookup_member_balance --version 1.2.0 --input member_id=00000
```

> "No such member isn't a crash — it's a named business outcome, `member_not_found`, and it
> exits 0. The caller switches on it."

*(optional)* Open the evidence folder from one of the successful lookups above, rather than
the not-found run: its `result.json` redacts the balance, and screenshots mask sensitive data.
"The caller gets the balance. The evidence file keeps it redacted."

## Scene 6 — Things going wrong (5:30–7:00)

```bash
waypoint replay lookup_member_balance --version 1.2.0 --input member_id=12345 --inject interstitial
```

> "A maintenance notice appears mid-flow. The artifact declares how to dismiss it: success, with
> the recovery recorded — `recoveries` is the flakiness signal."

```bash
waypoint replay lookup_member_balance --version 1.2.0 --input member_id=12345 --inject 500
```

> "A server error is a failure — and the failure says what it expected, `member_detail_loaded`,
> and what it actually saw, `server_error`. Enough to debug without a transcript."

```bash
waypoint replay lookup_member_balance --version 1.2.0 --input member_id=12345 --inject wrong_member
```

> "The right screen for the wrong member. A checkpoint that only asked 'is this a member profile?'
> would pass. This one asserts which member — so the run escalates instead of reading someone
> else's balance."

## Scene 7 — Handing the session to a person (7:00–9:00)

Terminal 2:

```bash
waypoint replay lookup_member_balance --version 1.2.0 \
  --input member_id=12345 --inject ambiguous --handoff
```

> "Now the member's row has two identical View links. Waypoint won't guess — it escalates, and
> with `--handoff` it keeps the browser open for a person."

Terminal 3 (replace `INTERVENTION_ID` with the ID from `list`):

```bash
waypoint intervene list
waypoint intervene take INTERVENTION_ID
```

> "Taking control moves a lease. From this moment the run holds no token — even a stale piece of
> its own code couldn't act."

In the browser, click **View** in the row showing **12345**. Then terminal 3:

```bash
waypoint intervene return INTERVENTION_ID
```

Back in terminal 2, point at `"status": "success"`.

> "When control comes back, the run doesn't assume anything. It re-checks the screen and resumes
> only from a declared, state-complete checkpoint — otherwise it would escalate again."

*(optional)* Show `evidence/runs/<run_id>/human/actions.jsonl`: "What the person did is logged —
the click, the navigation — redacted."

## Scene 8 — The irreversible step (9:00–11:15)

> "The hardest case: opening a sub-account. It commits, and it can't be undone."

Terminal 2:

```bash
waypoint replay open_sub_account --version 1.0.0 --handoff --input member_id=12345 \
  --input "account_type=Money Market" --input initial_deposit=250.00 --inject commit_then_drop
```

> "It runs unattended, so Confirm is never clicked by the agent — it escalates for a person. And
> I've told the server to commit, then lose the response."

Terminal 3: `waypoint intervene list`, then `waypoint intervene take INTERVENTION_ID`. In the browser, click
**Confirm** — you'll see a **502 error page**.

> "From my side, that looks like it failed. It might have. A system that retried now could open
> a second account."

```bash
waypoint intervene return INTERVENTION_ID
```

Point at `"adopted": true`, the reconciliation verdict `completed`, and `"account_id"`.

> "Waypoint never repeats an irreversible step to find out whether it worked. It reads the
> member's accounts grid and asks: is there exactly one account showing *this* deposit, created
> *after* the attempt? Yes — so it adopts that account instead of clicking again. If the evidence
> were ambiguous — two candidates, an unreadable date — the answer would be Unknown, and Unknown
> escalates."

*(optional)* In the browser, open member 12345 → Accounts: exactly one Money Market account.

## Scene 9 — Evidence and safety (11:15–12:00)

Show `evidence/README.md` in GitHub or the editor.

> "Each replay saves its artifact, screenshots, sanitized snapshots, events and result.
> The index distinguishes live discovery recordings from replay showcases and explains
> which artifact each used."

Show the successful local test output, or the GitHub Actions run for the revision being demonstrated:

> "Policy checks run inside the browser adapter for both discovery and replay. The 429-test
> suite covers approval, redaction, recovery and handoff without calling a model API."

## Scene 10 — Close (12:00–12:30)

> "Not built: tenant variants, the capability catalog, and a desktop adapter beyond its interface.
> The design and the trade-offs are in REPORT.md. Thanks for watching."

---

## If something goes wrong on camera

| Problem | Fix |
|---|---|
| `waypoint` is not found | Enter the repository, set the same environment path used during setup, and repeat the activation command. |
| Sign-on fails | Check `.env` matches the fixture credentials. Custom credentials must also be exported in the app terminal. |
| A handoff run is waiting and you want out | `waypoint intervene abort INTERVENTION_ID` (exit 3, with evidence). |
| An irreversible run reconciles before starting | Inspect `waypoint intervene intents`; follow the fixture rehearsal reset described above if starting an independent take. |
| Cassette reports "screen no longer matches" | Check that the running app matches this checkout, then restart it. A changed UI may require a new live discovery recording. |
