"""Build the public guide locally; documentation-only dependency: reportlab.

Run from any directory. No learner files are read and no network is used.
"""
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


OUT = Path(__file__).with_name("LearningOS_Getting_Started.pdf")
W, H = 595.276, 841.89
M = 48
CW = W - M * 2
NAVY = colors.HexColor("#142C3C")
TEAL = colors.HexColor("#007A76")
INK = colors.HexColor("#263C48")
MUTED = colors.HexColor("#516570")
PALE = colors.HexColor("#EDF5F3")
LINE = colors.HexColor("#DAE3E5")
URL = "https://github.com/SolvyrEryx/LearningOS"
STYLES = {
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10.5,
                           leading=15.5, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=9.3,
                            leading=13.5, textColor=MUTED),
    "quote": ParagraphStyle("quote", fontName="Helvetica", fontSize=10,
                            leading=15, textColor=NAVY),
}


def build():
    c = canvas.Canvas(str(OUT), pagesize=(W, H), pageCompression=1, invariant=1)
    c.setTitle("LearningOS | Getting Started")
    c.setAuthor("LearningOS contributors")
    c.setSubject("Install, study, save and continue with your own learning workspace")
    y = H - 48

    def p(text, style="body", gap=10, x=M, width=CW):
        nonlocal y
        para = Paragraph(text, STYLES[style])
        _, height = para.wrap(width, H)
        if y - height < 65:
            raise ValueError(f"Guide content overflows a page near: {text[:75]}")
        para.drawOn(c, x, y - height)
        y -= height + gap

    def section(title):
        nonlocal y
        y -= 4
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(M, y - 13, title)
        y -= 27

    def code(lines):
        nonlocal y
        wrapped = []
        for line in lines:
            wrapped.extend(simpleSplit(line, "Courier", 8, CW - 24))
        height = 20 + 12 * len(wrapped)
        if y - height < 65:
            raise ValueError("Code block overflows page")
        c.setFillColor(PALE)
        c.roundRect(M, y - height, CW, height, 7, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont("Courier", 8)
        for i, line in enumerate(wrapped):
            c.drawString(M + 12, y - 17 - i * 12, line)
        y -= height + 13

    def callout(label, text):
        nonlocal y
        para = Paragraph("<b>" + escape(label) + "</b><br/>" + text, STYLES["quote"])
        _, height = para.wrap(CW - 28, H)
        height += 24
        if y - height < 65:
            raise ValueError("Callout overflows page")
        c.setFillColor(PALE)
        c.roundRect(M, y - height, CW, height, 8, fill=1, stroke=0)
        c.setFillColor(TEAL)
        c.rect(M, y - height + 8, 3, height - 16, fill=1, stroke=0)
        para.drawOn(c, M + 14, y - height + 12)
        y -= height + 17

    def begin(n, label, title, deck):
        nonlocal y
        c.setFillColor(TEAL)
        c.rect(0, H - 8, W, 8, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(TEAL)
        c.drawString(M, H - 43, "LEARNINGOS  /  GETTING STARTED")
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawRightString(W - M, H - 43, label.upper())
        c.setFont("Times-Bold", 31)
        c.setFillColor(NAVY)
        c.drawString(M, H - 94, title)
        y = H - 111
        p(deck, "small", gap=18)
        c.setStrokeColor(LINE)
        c.line(M, y + 2, W - M, y + 2)
        y -= 16
        c.setStrokeColor(LINE)
        c.line(M, 48, W - M, 48)
        c.setFont("Helvetica", 8)
        c.setFillColor(MUTED)
        c.drawString(M, 32, "LearningOS  |  Public starter guide  |  v0.3.0")
        c.drawRightString(W - M, 32, f"{n} / 6")

    def end():
        c.showPage()

    begin(1, "The idea", "Learn today. Continue tomorrow.",
          "A folder, an AI tutor and a record of what you can actually do.")
    p("LearningOS gives a file-capable AI agent a persistent learning workspace. "
      "It keeps evidence of your attempts, help, mistakes and next steps in files you own.")
    p("Use it for a subject, an interview, an exam or a skill. You can begin without "
      "a deadline, uploaded documents or a complete syllabus.")
    section("Four actions are enough")
    steps = [
        ("01", "/start Biology", "Choose a subject and a small learning goal."),
        ("02", "Study normally", "Answer, show your working, ask why and practise."),
        ("03", "/save", "Save evidence and leave the next activity."),
        ("04", "/continue", "Return to saved work and retrieve it first."),
    ]
    for num, label, desc in steps:
        c.setFillColor(TEAL)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(M, y - 13, num)
        p(f"<b>{escape(label)}</b><br/>{escape(desc)}", x=M + 35, width=CW - 35, gap=15)
    callout("Where learning happens", "Talk to your tutor in your AI agent. "
            "Open the Learning Atlas to inspect saved progress. The Atlas is an offline "
            "report; it does not run a second AI conversation.")
    p("<b>In this guide</b><br/>2 - Set up your copy &nbsp; | &nbsp; 3 - Your first lesson<br/>"
      "4 - Add material &nbsp; | &nbsp; 5 - Save and return &nbsp; | &nbsp; 6 - Get unstuck", "small")
    end()

    begin(2, "Set up", "Make it your workspace.",
          "One learner per copy. No personal data is included in the public repository.")
    section("1. Download and open the folder")
    p(f'Visit <link href="{URL}" color="#007A76">github.com/SolvyrEryx/LearningOS</link>. '
      'Choose <b>Code &gt; Download ZIP</b>, extract it, and keep the folder somewhere permanent. '
      'Or use Git:')
    code(["git clone https://github.com/SolvyrEryx/LearningOS.git", "cd LearningOS"])
    section("2. Check Python and your AI agent")
    p("Use <b>Python 3.11 or newer</b>, available as <b>python</b>. Check with "
      "<b>python --version</b>. If your system uses <b>python3</b>, substitute that name. "
      "The core toolkit needs no third-party Python packages.")
    p("Open the whole folder in an AI agent that can read and write local files and run "
      "shell commands. A pasted-text-only chat cannot maintain the folder automatically. "
      "The agent may require its own account or subscription.")
    section("3. Paste this first-session prompt")
    callout("Your tutor can run setup for you", "Read AGENTS.md and the five core files it points to. "
            "Run the two commands below, preserve existing files and report any errors. "
            "I want to learn [my subject or skill]. Help me choose a small goal, check what "
            "I already know, and teach me. Save real evidence as we go.")
    code(["python 01_AGENTS_AND_SKILLS/tools/learningos.py init-workspace",
          "python 01_AGENTS_AND_SKILLS/tools/learningos.py validate"])
    p("Run commands from the repository root. Replace the bracketed phrase with your subject. "
      "Initialisation creates neutral learner files locally and preserves existing ones. "
      "For agents without automatic AGENTS.md discovery, repeat the read request in each chat.", "small")
    end()

    begin(3, "Your first lesson", "Aim for something you can do.",
          "A small outcome and an honest attempt are enough to begin.")
    section("Tell the tutor what you want")
    callout("A useful opening", "/start Biology<br/>I have 25 minutes. I want to explain "
            "how inheritance works and solve a simple unfamiliar example. Check what I know first.")
    p("Replace Biology with your own subject. Time, purpose and materials are optional. "
      "The tutor should begin with a short diagnostic, then teach the smallest useful next piece.")
    section("Study normally")
    p("Answer in your own words, show your working and ask why. If you are stuck, ask for "
      "a hint. After an explanation, try a different problem or explain the idea back "
      "without looking. Tell the tutor when an explanation does not help.")
    code(["/learn Genetics", "/review", "/test", "/mistakes", "/today 30"])
    p("These are <b>messages in the tutor chat</b>. They are not terminal commands. "
      "You can also say: 'Quiz me on older material' or 'I have half an hour; what should I do?'")
    section("What counts as learning evidence?")
    p("The system records actual questions, responses, feedback, hints and next actions. "
      "A hinted success stays labelled as assisted. Independent success requires "
      "checked explanation and application without hints or solution exposure.")
    p("Reading an explanation, copying a solution or feeling confident does not establish "
      "mastery. Provisionally mastered concepts need repeated unaided success, including "
      "a non-familiar task and a delayed check. The mastery estimate is a heuristic, not a grade.")
    callout("Expect fallibility", "AI explanations and grading can be wrong. Ask the tutor "
            "to check disputed claims, cite your material and record uncertainty instead "
            "of treating a guess as settled knowledge.")
    end()

    begin(4, "Your material", "Bring sources when they help.",
          "A syllabus is useful. It is not a prerequisite for learning.")
    section("Copy documents into the inbox")
    code(["02_KNOWLEDGE/inbox/"])
    p("Use your file manager to copy in notes, a syllabus, textbook excerpts or past papers "
      "you are entitled to use. Then ask the tutor:")
    callout("Source setup prompt", "Assign the files in my inbox to Biology and index them. "
            "Preserve the originals, report extraction warnings, and use these sources "
            "when teaching me. Cite the relevant page or line.")
    section("Choose a readable format")
    p("<b>Markdown and plain text:</b> read locally without extra packages.<br/>"
      "<b>Text PDFs:</b> extraction uses optional <b>pypdf</b>. To enable it, explicitly run "
      "<b>python -m pip install pypdf</b> in the Python environment used for LearningOS.<br/>"
      "<b>Scanned PDFs:</b> need a separate OCR step. The tutor should report missing text.<br/>"
      "<b>Word, slides and other formats:</b> export a copy to PDF, Markdown or text.")
    p("Your source files stay unchanged. Extracted text and indexes are derived files kept "
      "beside them. Inbox assignment copies material into a subject; it does not move it.")
    section("The three folders")
    p("<b>01_AGENTS_AND_SKILLS</b> - the tutor's rules, templates, documentation and toolkit.<br/>"
      "<b>02_KNOWLEDGE</b> - your subject material and derived source text.<br/>"
      "<b>03_MEMORY</b> - attempts, learning state, mistakes, handovers and reports.")
    p("Claims drawn from indexed material use citations such as <b>[source_id p.4]</b>; "
      "plain text uses line numbers. General-background teaching should be labelled "
      "as such. Each subject keeps its own evidence.")
    callout("No documents yet?", "Start anyway. Say what you want to learn and ask for "
            "general-background teaching. Add trusted sources when they become useful.")
    end()

    begin(5, "Keep the thread", "Save. Return. See your progress.",
          "The files carry your learning history across chats and agents.")
    section("Before you stop")
    code(["/save"])
    p("Look for a short receipt with <b>memory saved</b>, <b>Atlas refreshed</b> "
      "(or the specific refresh problem), and <b>the first activity next time</b>. "
      "The tutor should also persist meaningful cycles while you study.")
    p("Closing a window is not a save command. Recorded work survives; a conversation "
      "that the tutor never wrote to the files cannot be reconstructed automatically.")
    section("When you come back")
    code(["/continue"])
    p("The tutor reads the handover and evidence, finishes a matching interrupted save "
      "if needed, and starts with retrieval. Use <b>/start Mathematics</b> to switch "
      "subjects; the tutor should save the current subject before switching.")
    section("Open your Learning Atlas")
    p("Ask <b>/atlas</b>, then open the local HTML path the tutor returns. Or run:")
    code(["python 01_AGENTS_AND_SKILLS/tools/learningos.py atlas"])
    p("Explore concepts, learning evidence, notes and due reviews. The Atlas has embedded "
      "assets and needs no server. Reload an already-open browser tab after regeneration: "
      "the report is a snapshot of recorded work, not a live view of your chat.")
    section("Optional Windows shortcuts")
    p("In the tools folder, double-click <b>Install LearningOS shortcuts.vbs</b>. "
      "The <b>LearningOS</b> shortcut opens the report; <b>LearningOS Study Menu</b> "
      "opens a numbered menu. You can also run <b>LearningOS.cmd</b> directly. "
      "Return to your AI agent for teaching.")
    p("For a local backup, ask your tutor or run:<br/>"
      "<b>python 01_AGENTS_AND_SKILLS/tools/learningos.py backup</b>", "small")
    end()

    begin(6, "When you need help", "Keep your workspace yours.",
          "Recover carefully, share the clean system, and protect your learning history.")
    section("Common fixes")
    p("<b>Missing learner files after download?</b> Run <b>init-workspace</b>, then "
      "<b>validate</b>, using the commands on page 2.<br/>"
      "<b>Tutor starts from scratch?</b> Ask it to read AGENTS.md, then use <b>/continue</b>.<br/>"
      "<b>Interrupted session?</b> Use <b>/continue</b> to inspect saved evidence and pending work.<br/>"
      "<b>No save receipt?</b> Ask <b>/save</b>.<br/>"
      "<b>Old Atlas?</b> Ask <b>/atlas</b> and reload its browser tab.<br/>"
      "<b>Corrupt or inconsistent file?</b> Keep it unchanged. Ask the tutor to run "
      "<b>validate</b> and explain the exact remedy before attempting recovery.")
    section("Local files and hosted AI")
    p("The study toolkit makes no network calls. If your AI agent uses a hosted model, "
      "content it reads into the conversation is processed by that provider. "
      "Local storage does not make inference local. Choose what you let your agent read.")
    section("Share the system, preserve personal history")
    p("Send new learners the repository link and give each person a separate fresh copy. "
      "A ZIP of your used folder contains your learning data. Git ignore rules exclude "
      "subjects, sources, profiles, events, backups and reports; they cannot prevent "
      "forced additions or remove already-tracked files. Review changes before publishing.")
    section("Read further or contribute")
    p("The repository README links to the student guide, command reference and tutor "
      "protocols. Report bugs with a minimal synthetic example and your Python version. "
      "Keep personal sources and memory out of issues and pull requests.")
    p(f'<link href="{URL}" color="#007A76"><b>github.com/SolvyrEryx/LearningOS</b></link><br/>'
      f'<link href="{URL}/issues" color="#007A76">Report a problem or suggest an improvement</link>')
    callout("MIT licensed", "LearningOS code and documentation are released under MIT. "
            "Bundled fonts keep their own license notices. Your study material retains "
            "its original rights.")
    end()
    c.save()
    print(OUT)


if __name__ == "__main__":
    build()
