# Waypoint — final recording script

**About 8 minutes.** Read the quoted lines in your own words. The presenter types the real
commands for you; you press **Enter** and, twice, click in a browser.

## Before recording

1. Close anything personal and turn on Do Not Disturb. Keep `.env` off screen.
2. Make the terminal text large and leave room beside it for a browser window.
3. Optional: open [`docs/demo-slides.html`](demo-slides.html) in a browser, full screen
   (arrow keys move between slides).
4. From the repository root, start the presenter:

   ```bash
   docs/present.sh
   ```

5. Wait for **Ready. Start recording.** Then start your screen recording, microphone on.

You need **one terminal**. The presenter starts its own demo app and uses a fresh private
folder, so do not also run `./demo prepare` or `./demo app`.

If this machine has never run the project, install first (not on camera):

```bash
uv sync --locked --extra dev
uv run playwright install chromium
```

---

## 1. Introduction — about 30 seconds

*(Slide 1, the title — or just your face and the terminal.)*

> “Hi, I'm Susil. This is Waypoint, my submission for the computer-use automation assignment.”

> “In the next eight minutes I'll explain the problem it solves, then show it working end to
> end against a deliberately awkward demo banking app: a model learning a task, a person
> reviewing and approving it, the task replaying without a model — including when things go
> wrong — and a person taking over the live browser when it isn't safe to continue. I'll
> finish with the other features it includes.”

> “Everything you'll see runs locally. The members, accounts and login are fictional, and the
> model decisions are replayed from a real earlier recording, so there are no live model calls
> in this video.”

## 2. The problem and the idea — about 45 seconds

*(Slides 2 and 3, or just talking over the terminal.)*

> “Banks run a lot of older back-office systems that have no API. The only way to use them is
> through the screen, the way a member of staff does. If an AI agent is going to do real work
> there, something has to operate those screens for it.”

> “There are two obvious ways, and both have problems. Traditional scripts break on messy
> screens and usually only handle the happy path. Letting a model click through the screens
> every time is slow, costly and unpredictable — not something you want near a bank's money.”

> “Waypoint splits the work. A model learns the workflow **once**. That becomes a typed,
> reviewable capability that a person approves. After that, every request **replays** it
> without a model, and says exactly what happened. When it isn't safe to continue, it hands
> the **same live browser** to a person. Let me show you.”

*(Switch to the terminal.)*

## 3. The demo — 12 scenes

**Press Enter to start each scene.** Wait for the result before pressing Enter again.

### Scene 1 · Discover

**Shows:** a browser signing in, searching and opening a member's record.

> “This is the learning step. I'm replaying the decisions from an earlier live Claude
> discovery against the demo app, so there are no new model calls here. The model reads the
> page the way a screen reader does, and member names and balances are hidden from it.”

### Scene 2 · Review

**Shows:** the generated inputs, outputs and steps, and the file path. Briefly open that file.

> “Here's what it produced: what it needs, a member ID; what it returns, a balance and a
> status; and each step with a check that the step really worked. I can review all of this.”

### Scene 3 · The unapproved draft is refused

**Shows:** a refusal. This is expected.

> “A model wrote this, so it isn't trusted yet. Trying to run it before approval is blocked.”

### Scene 4 · Approve

**Prompt:** *Review complete? Enter approves this artifact.* Press Enter.

> “I've reviewed it, so I approve it. Approval is tied to the workflow's contents.
> Changing the workflow invalidates that approval.”

### Scene 5 · Replay

**Shows:** `STATUS: success`, **$4,281.19**, **active**. No browser appears; that's expected.

> “This is what an agent calls in production. It follows the approved steps and returns the
> balance — no model involved.”

### Scene 6 · Another member

**Shows:** **$912.04**, **dormant**.

> “Same capability, a different member, the correct answer. I can reuse the workflow by
> supplying another member ID.”

### Scene 7 · Member not found

**Shows:** `business_outcome`, `member_not_found`.

> “From here I'm using a reviewed version with extra handling rules. A member that doesn't
> exist isn't a crash — it's a named answer the agent can pass on.”

### Scene 8 · Recover from an interruption

**Shows:** `success` and a recorded recovery.

> “A maintenance notice pops up mid-flow. The capability knows how to dismiss it, so it still
> succeeds, and the recovery is recorded.”

### Scene 9 · Server failure

**Shows:** `failure`, `hard_failure`, the step, what it expected and what it saw. Deliberate.

> “When the application fails, Waypoint stops and says exactly where, what it expected and
> what it actually saw. It never invents a balance.”

### Scene 10 · Hand control to a person

A browser opens and pauses. Then, in order:

1. **Prompt:** *Enter = take control of the paused browser.* Press Enter.
2. In the **browser**, click **View** in member **12345**'s row. Wait for the profile page.
3. **Prompt:** *Then Enter = return control.* Back in the terminal, press Enter.

> “There are two matching links, so it stops instead of guessing. I take over this same
> browser — while I have control, the automation can't touch it — and open the right member.
> When I hand it back, Waypoint checks the page before it carries on.”

**Expect:** `success`.

### Scene 11 · An action that can't be undone

A browser fills in a new account and pauses before **Confirm**. Then, in order:

1. Press Enter to take control.
2. Check it shows **12345**, **Money Market**, **250.00**. Click **Confirm once**.
   An error page appears — that is the point. **Don't click again or reload.**
3. Back in the terminal, press Enter to return control.

> “Opening an account changes the system, so this demo pauses for a person before Confirm.
> I've set the app to save the account and then lose the response. It looks
> like a failure, but retrying could open a second account. Instead, Waypoint checks the
> account records, finds exactly one new account matching this deposit, and returns it.”

**Expect:** `success`, an account ID, and **ADOPTED: true**.

### Scene 12 · Evidence

**Shows:** the list of runs from this take and the folder path.

> “Every run keeps the exact capability, its result, the checks, and anything a person did.
> Saved screenshots and page snapshots have sensitive values redacted.”

## 4. Everything else it can do — about 90 seconds

No more scenes: after scene 12, keep the terminal on screen and **talk through these**. Where a
file is listed, you can open it in your editor while you speak — it is saved evidence from real
runs, so nothing needs to execute. Skip any you are short on time for.

### Safety, all the way through

> “You've seen some of the safety rules already. Every action goes through a policy engine:
> it can only visit allowed addresses, and it judges risk by what an action *does*, not what
> the button is called — that's why Confirm needed a person. Member data is classified before
> the model or the logs ever see it, and credentials are typed straight into the right field
> while being kept out of model prompts and run evidence.”

### When discovery itself gets stuck

> “Handoff isn't only for replay. If discovery stops making progress — the screen doesn't
> change, it keeps going back and forth, or the model gives up — it hands the same live
> browser to a person. What they did is logged, and the draft carries a gap that blocks
> approval until someone writes that step properly. So a draft never silently skips work a
> person did.”

### Measuring reliability

**Optional to show:** `evidence/stability/lookup_member_balance-1.3.0-…/report.md`

> “One green run doesn't prove much, so Waypoint can replay every declared case several times.
> This report shows sixteen runs: whether each did what its case said, whether the same inputs
> gave the same answer every time, and how long each took. Runs where we broke the app on
> purpose are reported but never counted. A capability can require a minimum confidence
> before it runs unattended — version 1.3.0 refuses to run on its own until it's been measured.”

### Tools an agent can call

**Optional to show:** `evidence/agent/lookup.json`

> “Approved capabilities are published as a catalog of typed tools. Drafts never appear there.
> In this recording, Claude was asked for a member's balance: it picked the lookup tool, called
> it by name, Waypoint replayed it, and Claude answered from the result — without touching the
> UI itself.”

### A renamed button

**Optional to show:** `evidence/runs/showcase-assisted-drift/` — `assist_steps_1.json` and
`proposal/lookup_member_balance-1.3.1.json`

> “Real applications get updated. Here the Search button was renamed to Continue, so the
> recorded step couldn't find it. If a capability allows it, Waypoint may ask a model one narrow
> question: *which control is it now?* The model can only point at an element; the answer is
> checked before use, it happens at most once per run, and never on a step that can't be
> undone. The run also writes a proposed repair for a person to review.
> It doesn't install or approve that repair automatically.”

### Generated tests

> “Finally, it can turn a capability into code: a regression test that replays every declared
> case — so a broken capability fails a CI build — and a readable Playwright version, so you
> can see exactly what it clicks.”

## 5. Closing — about 20 seconds

*(Optional: the last slide, **Recap**.)*

> “So that's Waypoint: a model learns a task once, a person approves it, and from then on it
> replays reliably without a model — and hands the live session to a person when it isn't
> safe. Tenant variants and desktop apps are designed but not built; the report explains the
> trade-offs. Thanks for watching.”

Stop recording. The presenter stops its demo app and keeps the evidence.

---

## Optional: a short agent clip

Record separately after the main take. It starts its own app:

```bash
uv run python scripts/agent_demo.py --cassette evidence/agent/lookup.json
```

**Shows:** the capability measured, the agent's tool call, the returned balance, the answer.
Trim the measurement wait in editing.

> “Here's an agent using it: it calls the lookup by name and gets the answer back, without
> ever touching the UI. This replays a saved agent conversation against the demo app.”

## If something goes wrong

| Problem | What to do |
|---|---|
| Scene 3 or 9 shows a failure | Expected. Keep going. |
| You're running long | Cut section 4 down to "Measuring reliability" and "Tools an agent can call". |
| The presenter stops with an error | Stop recording, press **q** if prompted, and run `docs/present.sh` again for a fresh take. |
| In scene 10 or 11 you pressed Enter before clicking | The run asks for a person again, and the presenter stops after a minute with a clear message. Start a fresh take. |
| You can't see the browser in scene 10 or 11 | Click the Chromium icon in the Dock. |
| You want to stop | Type **q** at any prompt, or press Control-C. The demo app stops too. |

To practise the automatic scenes without recording, run `docs/present.sh --rehearse`. It skips
scenes 10 and 11, so practise those two clicks in one normal run before you record.

For full feature-by-feature testing, use the [manual walkthrough](manual-walkthrough.md).
