'use strict';

const records = {
  saved: {
    label: 'WHERE WE STOPPED',
    title: ['Find the gap', 'in the reasoning.'],
    attempt: 'Applied the formula, but could not derive it from orthogonality.',
    evidence: 'Recall passed. Derivation failed. Transfer not yet tested.',
    next: 'Rebuild the derivation, then solve an unfamiliar case without hints.',
    margin: 'A right answer still needs a reason.',
    footer: 'Saved for your next session'
  },
  next: {
    label: 'WHERE WE PICK UP',
    title: ['Rebuild the idea', 'from its foundations.'],
    attempt: 'Check orthogonality before returning to the Fourier derivation.',
    evidence: 'Record explanation and application separately, with all hints visible.',
    next: 'Test an unfamiliar case, then schedule delayed retrieval.',
    margin: 'Return to the first broken step.',
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
