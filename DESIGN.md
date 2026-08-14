# Delta Code product presentation system

## Product position

Delta Code is **the AI review bot for breaking API changes**. It behaves like
Dependabot for external API and SDK migrations: it watches authoritative
sources, identifies affected repositories and call sites, generates and
verifies a bounded patch, opens a draft pull request, and waits for a developer
decision.

The product is not presented as a generic coding agent or a generic API test
dashboard. Deterministic migration evidence is the trust layer within the
workflow.

## Message hierarchy

Use this order on public and onboarding surfaces:

1. **Outcome:** a verified migration draft PR appears when an external API
   change affects the repository.
2. **AI contribution:** GPT-4o interprets change context, generates the bounded
   patch and tests, and reviews the result.
3. **Evidence boundary:** deterministic systems own provenance, call sites,
   patch policy, sandbox results, and Git state.
4. **Human control:** Delta Code prepares and recommends; the developer
   approves, revises, snoozes, or declines.

Preferred short lines:

- “The AI review bot for breaking API changes.”
- “Dependabot finds version bumps. Delta Code ships API migrations.”
- “AI proposes. Evidence proves. You decide.”
- “From provider change to verified draft PR.”

Avoid leading with “API regression testing,” “evidence-first verification,” or
“model gateway.” Those describe supporting capabilities rather than the full
product outcome.

## Visual direction

The interface should resemble a high-trust code-review agent:

- graphite and midnight surfaces instead of generic white SaaS cards;
- electric blue for automation and model activity;
- mint for deterministic checks and verified evidence;
- amber for uncertainty and developer attention;
- coral only for blocked or failed states;
- Space Grotesk for product headlines, Manrope for interface copy, and
  JetBrains Mono for source IDs, files, commands, hashes, and model labels;
- thin audit lines, compact state chips, restrained glows, and visible focus;
- asymmetric editorial layouts anchored by real review artifacts;
- motion that communicates progress or provenance, never decorative churn.

## Required UI patterns

### AI activity

Label model-generated content as `AI generated`, `AI review`, or `GPT-4o`.
Never style it as verified evidence. Use the electric-blue treatment.

### Verified evidence

Use the mint treatment for checks that actually ran, captured source hashes,
known call sites, and exact patch artifacts. State the command or evidence
source whenever space permits.

### Uncertainty

Use amber and direct language. Unsupported coverage, missing environments, and
model ambiguity must remain visible rather than collapsing into a confidence
score.

### Human decision

Approve, revise, snooze, decline, retry, and publish actions must remain
explicit. Generated work never appears merged or production-ready by default.

## Landing-page composition

Public pages should use these objects instead of abstract illustrations:

- a provider-change event;
- affected call-site evidence;
- an AI-generated patch and tests;
- a sandbox check list;
- a draft pull-request card;
- a developer decision state.

At least one sentence on every primary public page must explain the distinction
between AI interpretation and deterministic proof.

## Accessibility and behavior

- Preserve visible keyboard focus and the skip link.
- Maintain a minimum 44px target for primary actions.
- Do not rely on color alone for AI, verified, uncertain, or failed states.
- Respect reduced-motion preferences.
- Keep body text at readable contrast in both themes.
- Do not place secrets, model keys, repository credentials, or private source
  content in browser-visible configuration.
