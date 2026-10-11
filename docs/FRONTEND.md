# Call-taking workspace

Run `npm ci` and `npm run dev` in `frontend`. The default route opens a self-contained scripted rehearsal. The input selector also offers **Microphone / WAV** and **Transcript** modes connected to the current `/api/process` endpoint. `?view=pipeline` keeps the original pipeline inspector available.

The API defaults to `http://127.0.0.1:8000`. To connect a teammate's API, copy `frontend/.env.example` to `frontend/.env.local`, set `DISPATCHIQ_API_TARGET`, and restart Vite. The development proxy keeps frontend requests same-origin. Deployment needs equivalent `/api` routing.

The design uses one connected workspace: a white accepted incident record, a dark slate bilingual conversation panel, and a pale blue review column. Blue identifies review actions; green is reserved for accepted status. The header shows accepted location and publication state. Source inspection expands original Spanish and illustrative English inline; corrections separate the current record from the proposed replacement. Amber marks caller corrections. Desktop keeps all three visible; narrow layouts stack them. Source selection persists and is keyboard accessible. Status is communicated with words, not only color. Typography uses DM Sans and IBM Plex Mono when the Google Fonts request succeeds, with system fallbacks.

## Demo walkthrough

1. Accept the initial location, 124 Oak Street, and incident description.
2. Select Next segment for callback and caller details. Accept, edit, or dismiss each.
3. Advance again: the caller corrects the address to 142 Oak Street. The accepted address stays at 124 until reviewed.
4. Inspect the source, then accept or edit the correction.
5. Publish a partial or complete record. The Receiving desk displays a snapshot, including unresolved items at publication.
6. Return to the record and advance to the access detail. The receiving snapshot remains unchanged until the next explicit publication.
7. Reset clears all state. Demo state is memory-only and also resets on refresh.

Play scenario advances the scripted text every 6.5 seconds. Displayed timestamps are source-script timestamps, not measured latency or wall-clock call duration. The scripted rehearsal has no audio; switch input mode for microphone capture or WAV playback. Translation is illustrative, not interpreter-verified. Accepted means operator-reviewed; it does not assert translation verification or dispatch clearance.

## Backend integration boundary

`src/workspace/session.ts` owns review state and converts full API responses into proposals. `backend.ts` validates responses and handles transport. `Intake.tsx` owns capture, upload, transcript submission, serialized requests, retry, timeout, and resource cleanup.

### Live workflow

1. Switch input to Microphone / WAV and start capture or upload a synthetic WAV (maximum 30 MB). Microphone access requires localhost or HTTPS. A level meter shows input; completed recordings have native playback controls.
2. Capture sends the accumulated recording every five seconds when no request is active. Stop closes the microphone, waits for any in-flight request, then submits one final snapshot under the **same session ID**. This is polling, not streaming transcription; model processing can take longer than five seconds.
3. Transcript mode accepts the full conversation so far and repeated revisions, bypassing speech recognition and translation.
4. Changed transcripts are immutable snapshots. The API provides neither speaker attribution nor utterance timestamps. Labels describe capture/update time; evidence cannot seek to an exact audio moment.
5. Returned fields become proposals. Identical passes do not recreate reviewed proposals. Changed values supersede pending proposals without overwriting accepted values. Missing/unknown predictions withdraw pending proposals without clearing the record. Stale sequences and wrong-session responses are ignored.
6. Accept, edit, dismiss, or manually enter information. Source context distinguishes original from English and labels keyword evidence as context, not a model explanation. Incoming snapshots do not move the transcript while an older source is selected.
7. Publish a local snapshot. Receiving desk, Copy brief, and Download record use this version. Later results, decisions, or notes cannot change it without another publication. Unresolved items are frozen with the version.
8. New session clears memory, asks before discarding work, and is disabled during capture/processing. Refresh does not persist state.

Mapped API fields: location, callback, incident type, people, injuries, hazards, weapons, and caller safety. Access/whereabouts remains manual until the backend exposes it. Rule and model results receive the same human review flow.

### Remaining backend boundary

Human review, generated suggestion IDs, history, and publication are **local browser state**. No `/api/confirm` calls are made, keeping extraction separate from reviewed values. Issue #1 still defines the desired durable session API: stable source/suggestion IDs, authoritative revisions, review operations, and publication snapshots. This adapter does not implement server persistence, multi-operator coordination, or reconnect recovery. Existing pipeline `api.ts` remains unchanged. Autonomous replies from the newer backend are outside this documentation workflow.

The workspace has no CAD vendor connection, unit assignments, escalation advice, or emergency response actions. No measured time-saving claim is displayed.

## Validation

`npm test` covers scripted/live correction protection, repeated proposals, rejected/edited decisions, wrong-session and stale results, withdrawals, publication isolation, malformed API responses, transcript/WAV request contracts, microphone permission denial, late permission grants, and final-capture cleanup. Microphone tests use mocked Web Audio; they do not verify real device behavior. `npm run build` checks TypeScript and produces the Vite build.

Browser visual/interaction QA and end-to-end model processing remain unverified. The host is below the user's 20 GiB free-space threshold, so no automated browser was launched. Scripted rehearsal runs without a backend; other modes require the configured API.

## Design exclusions

Keep the interface grounded in call-taking. Exclude promotional headlines and slogans from the working screen, decorative section numbers, fake operator avatars, decorative heading icons, nested cards around every update, pill badges for ordinary labels, invented metrics, gradient/glass effects, and an all-over accent-color wash. Use plain labels, ruled rows, neutral working surfaces, and color tied to a meaningful state. Preserve visible focus, readable supporting text, and textual state labels. A functional three-pane workspace is appropriate here; a blanket ban on three columns is not.

The second design pass replaces the slogan with the call ID, flattens review cards into rows, removes numbered headings/avatar/extra icons, and enlarges supporting text. The dark conversation pane remains distinct from the accepted record. These decisions were informed by the [anti-AI-slop design catalog](https://github.com/prangishviliAbe/agent-skills/blob/main/anti-ai-slop-design/SKILL.md), rather than treating every recommendation in that catalog as a requirement.

The third pass follows the [Lenny’s Newsletter article](https://www.lennysnewsletter.com/p/how-to-turn-your-ai-into-a-world) by developing the selected direction further rather than adding decorative effects. Its visual identity is an incident editor: connected panes, document-like record fields, explicit proposed changes, and inspectable provenance. Published-record status compares accepted values, operator notes, and unresolved items against the shared version; pending suggestions remain visible in their own queue. No claim of a screenshot-based critique is made while browser testing is blocked by disk space.

## Microinteraction pass

| Before | After | Why |
| --- | --- | --- |
| Abrupt pointer press | 120 ms, 0.98-scale feedback | Acknowledge the press without delaying the action |
| Accepted text changes silently | 180 ms field/check acknowledgement | Direct attention to the changed record |
| Source toggle lacks a directional cue | Rotating chevron and 160 ms excerpt reveal | Connect the control to its source |
| View changes abruptly | 140 ms opacity transition and active underline | Maintain orientation without moving the workspace |
| Publish/copy only reports in the status line | Inline Published version / Copied labels | Confirm success beside the action |
| Reviewed row removes the focused control | Keyboard focus advances to the next proposal or empty state | Preserve review continuity |

Motion uses transforms and opacity, never delays state updates, cancels when interrupted, and is suppressed for keyboard input and reduced-motion preferences. No animation library was added. Visual motion QA remains pending under the disk-space safeguard.

## Live-workspace usability pass

| Before | After | Why |
| --- | --- | --- |
| New transcript could pull the reader away | Follow pauses when scrolling back; Jump to latest includes arrival count | Keep earlier information readable during incoming updates |
| Source navigation could scroll the whole page | Source selection scrolls only the conversation pane | Preserve the operator's workspace position |
| Switching views lost transcript position | Reading position is restored on return | Avoid repeatedly finding the same information |
| Superseded proposal could remove an active editor | Preserve draft, explain the change, offer explicit manual save | Protect operator work while leaving newer proposals pending |
| Cancel/save could lose editing focus | Escape cancels; manual save/cancel restores the edit control | Keep keyboard navigation predictable |
| Current record appeared crossed out before review | Show current value without strikethrough | Accepted information remains valid until the operator changes it |

Source inspection can be exited with Jump to latest. Transcript and record scroll areas contain their own scroll gestures. Supporting review labels and edit targets have been enlarged. Browser interaction QA remains outstanding due to the disk-space safeguard.
