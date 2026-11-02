# Start here

LearningOS helps you understand, remember and independently use what you learn. The same workflow
works for a subject, an exam, an interview or professional study. You do not need an exam date or
documents to begin.

## First time: three minutes of setup

1. Open your own LearningOS folder in your AI tutor. Tell it to read AGENTS.md.
2. Say **`/start Biology`**, replacing Biology with your subject. You can also say: "I want to learn
   system design for interviews" or "Help me understand physiology."
3. Say what you want to be able to do. Add your notes or syllabus if you have them; this is optional.
4. Start answering the tutor's short diagnostic questions. You do not manage files or grading forms.

Prefer a menu? Open **LearningOS Study Menu** on the desktop and choose **10 — Get started**.
Then return to the tutor chat for teaching. The menu handles setup and timed practice; Atlas shows
your memory. Neither is a second AI chat.

## During study

Talk normally. Ask why, show your working, try examples, explain things back, and ask for help when
needed. The tutor should save meaningful learning cycles as you go, rather than waiting until the
end. Hints are useful and remain labelled as assistance. A wrong answer gives the next repair task.

## When stopping

Say **`/save`** or "Save and stop for today."

The tutor should give you a short receipt:

- **Memory saved:** responses, judgments, notes and handover were written.
- **Atlas refreshed:** the local report was regenerated, or a specific refresh problem is reported.
- **Next time:** the exact first activity is named.

You do not need to run a backup, write a checkpoint or update a chart yourself after each session.
Closing the chat window by itself is not a save command. Work already recorded survives; anything
not yet recorded may remain only in the chat.

## Next time

Say **`/continue`**. The tutor reads the handover and actual evidence, completes a matching interrupted
save if necessary, and starts with useful retrieval. For another subject, say `/start Mathematics`;
the tutor saves the current subject before switching. Each subject keeps separate evidence.

## Open your Atlas

Double-click **LearningOS** on the desktop. It builds and opens a local, offline report.
Explore the concept map, Learning evidence, revision queue, notes, sources and progress.
Click a concept to see why its learning state is recorded that way.

Saved commands rebuild the report. If the browser tab is already open, **reload it**. It is a saved
snapshot, not a live filesystem monitor. Its timestamp and pending-save notices tell you what it
contains. It cannot know about chat content the tutor never recorded.

## What gets saved?

| Saved when the tutor records it | Not automatically captured |
|---|---|
| Actual questions and learner responses | Every message in any chat |
| Feedback and explicit dimension judgments | Unsubmitted thoughts or working |
| Important explanations, notes and uncertainties | Other apps, calls or browser activity |
| Recorded hints and annotated solution steps | Proof of competence from simply reading material |
| Attempts, mistakes, reviews and next actions | Practical competence from a written explanation alone |

The files stay local and originals are preserved. If your AI tutor is hosted, material it reads
into its conversation is processed by that provider; local files do not mean local inference.

## Commands you actually need

| Say in your tutor chat | Result |
|---|---|
| `/start Biology` | Set up or select a subject |
| `/learn Genetics` | Start a focused lesson |
| `/save` | Save and leave an exact handover |
| `/continue` | Resume from saved evidence |
| `/help` | Explain this workflow |

Optional: `/today 30`, `/review`, `/test`, `/mistakes`, `/mock`, `/atlas`.
These are chat conventions understood by the tutor instructions. You can use normal sentences too.

## Different learners

Each person needs their own fresh workspace. Multiple subjects belong in one person's workspace;
multiple people's mastery must not be mixed. Copying a used folder also copies its learner memory.
For a clean installation, the setup helper uses `init-workspace --workspace <new folder>` rather than
deleting someone else's history. No account or cloud service is required.

## If something goes wrong

- No save receipt? Ask `/save`; do not assume the chat was exported.
- Interrupted session? Ask `/continue`; do not retry with a different cycle id.
- Memory saved but Atlas failed? Reopen the LearningOS shortcut to retry the report.
- A corrupt or conflicting file is reported? Keep it unchanged and let the tutor inspect it.
- No source document? Start anyway; background teaching is labelled as such.
