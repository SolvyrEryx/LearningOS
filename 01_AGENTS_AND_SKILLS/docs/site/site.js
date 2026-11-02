'use strict';

const records = {
  saved: {
    label: 'WHERE WE STOPPED',
    title: ['You solved it', 'with a hint.'],
    attempt: 'Solved the cross after a hint about dominant alleles.',
    evidence: 'Hinted success. Independent application still to check.',
    next: 'Explain the result, then try a different cross without help.',
    margin: 'The hint stays in the record.',
    footer: 'Saved for your next session'
  },
  next: {
    label: 'WHERE WE PICK UP',
    title: ['Before we go on,', 'try it from memory.'],
    attempt: 'Explain how you chose the alleles in the last cross.',
    evidence: 'A new, unaided attempt. Your tutor checks the reasoning.',
    next: 'Try a different cross, then return for a delayed check.',
    margin: 'A first question, ready when you are.',
    footer: 'Read from your saved handover'
  }
};

document.querySelectorAll('[data-session]').forEach(button => {
  button.addEventListener('click', () => {
    const record = records[button.dataset.session];
    document.querySelectorAll('[data-session]').forEach(tab => {
      const active = tab === button;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-pressed', String(active));
    });
    document.getElementById('note-label').textContent = record.label;
    const title = document.getElementById('note-title');
    title.replaceChildren(document.createTextNode(record.title[0]), document.createElement('br'), document.createTextNode(record.title[1]));
    for (const key of ['attempt', 'evidence', 'next']) {
      document.getElementById('note-' + key).textContent = record[key];
    }
    document.getElementById('margin-note').textContent = record.margin;
    document.getElementById('note-footer').textContent = record.footer;
  });
});

const copyButton = document.getElementById('copy-prompt');
const copyStatus = document.getElementById('copy-status');
copyButton.addEventListener('click', async () => {
  const prompt = document.getElementById('starter-prompt');
  try {
    await navigator.clipboard.writeText(prompt.innerText);
    copyStatus.textContent = 'Copied. Paste into your tutor.';
    copyButton.textContent = 'Copied ✓';
    setTimeout(() => { copyButton.textContent = 'Copy prompt ↗'; }, 2500);
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(prompt);
    selection.removeAllRanges();
    selection.addRange(range);
    copyStatus.textContent = 'Text selected. Press Ctrl+C or ⌘C.';
  }
});
