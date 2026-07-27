export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function renderMarkdown(rawValue) {
  let text = escapeHtml(rawValue);
  const blocks = [];

  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, language, code) => {
    const index = blocks.length;
    blocks.push(
      '<div class="code-block">'
      + `<div class="code-header"><span class="code-lang">${language.trim() || 'text'}</span>`
      + '<button type="button" class="copy-code">Copy</button></div>'
      + `<pre>${code.trim()}</pre></div>`,
    );
    return `\x00BLOCK${index}\x00`;
  });

  text = text.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
  text = text.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  text = text.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  text = text.replace(/^---$/gm, '<hr>');
  text = text.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  text = text.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  text = text.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  text = text.replace(/((?:^[-*+] .+\n?)+)/gm, (match) => {
    const items = match.trim().split('\n')
      .map((line) => `<li>${line.replace(/^[-*+] /, '')}</li>`).join('');
    return `<ul>${items}</ul>`;
  });
  text = text.replace(/((?:^\d+\. .+\n?)+)/gm, (match) => {
    const items = match.trim().split('\n')
      .map((line) => `<li>${line.replace(/^\d+\. /, '')}</li>`).join('');
    return `<ol>${items}</ol>`;
  });
  text = text.split(/\n\n+/).map((paragraph) => {
    const clean = paragraph.trim();
    if (!clean) return '';
    if (/^<(h[1-3]|ul|ol|div|blockquote|hr)/.test(clean)) return clean;
    return `<p>${clean.replace(/\n/g, '<br>')}</p>`;
  }).join('');

  blocks.forEach((block, index) => {
    text = text.replace(`\x00BLOCK${index}\x00`, block);
  });
  return text;
}

export function stripMarkdownForSpeech(text) {
  return String(text || '')
    .replace(/```[\s\S]*?```/g, 'code block. ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*\*(.+?)\*\*\*/g, '$1')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/^>\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/---/g, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .trim();
}
