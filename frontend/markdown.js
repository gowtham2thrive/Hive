/**
 * markdown.js — Lightweight Markdown-to-HTML converter for Hive Chat.
 * Supports: headings, bold, italic, inline code, code blocks, lists, links, blockquotes, tables, horizontal rules.
 * Sanitizes output to prevent XSS.
 */

const MarkdownRenderer = (() => {
    /**
     * Escape HTML special characters to prevent XSS.
     */
    function escapeHtml(text) {
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return text.replace(/[&<>"']/g, c => map[c]);
    }

    /**
     * Render inline markdown (bold, italic, code, links, strikethrough).
     */
    function renderInline(text) {
        let result = escapeHtml(text);

        // Inline code (must come before bold/italic to avoid conflicts)
        result = result.replace(/`([^`]+?)`/g, '<code class="md-inline-code">$1</code>');

        // Bold + Italic
        result = result.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');

        // Bold
        result = result.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Italic
        result = result.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Strikethrough
        result = result.replace(/~~(.+?)~~/g, '<del>$1</del>');

        // Links [text](url)
        result = result.replace(
            /\[([^\]]+)\]\(([^)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
        );

        return result;
    }

    /**
     * Simple syntax highlighting for code blocks.
     */
    function highlightCode(code, lang) {
        let escaped = escapeHtml(code);

        const keywords = {
            'python': /\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|yield|lambda|pass|break|continue|and|or|not|in|is|None|True|False|self|async|await|raise|print)\b/g,
            'javascript': /\b(const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|class|extends|new|this|super|import|export|default|from|try|catch|finally|throw|async|await|typeof|instanceof|null|undefined|true|false|console|document|window)\b/g,
            'js': /\b(const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|class|extends|new|this|super|import|export|default|from|try|catch|finally|throw|async|await|typeof|instanceof|null|undefined|true|false|console|document|window)\b/g,
            'typescript': /\b(const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|class|extends|new|this|super|import|export|default|from|try|catch|finally|throw|async|await|typeof|instanceof|null|undefined|true|false|interface|type|enum|implements|public|private|protected|readonly|abstract)\b/g,
            'html': /(&lt;\/?)([\w-]+)/g,
            'css': /\b(color|background|margin|padding|border|font|display|position|width|height|flex|grid|transition|animation|transform|opacity|z-index|overflow|cursor|text-align)\b/g,
            'bash': /\b(sudo|apt|pip|npm|cd|ls|mkdir|rm|cp|mv|echo|cat|grep|chmod|chown|curl|wget|git|docker|python|node)\b/g,
            'shell': /\b(sudo|apt|pip|npm|cd|ls|mkdir|rm|cp|mv|echo|cat|grep|chmod|chown|curl|wget|git|docker|python|node)\b/g,
        };

        const normalizedLang = (lang || '').toLowerCase().trim();

        if (keywords[normalizedLang]) {
            escaped = escaped.replace(keywords[normalizedLang], '<span class="hl-keyword">$&</span>');
        }

        // Strings (double and single quoted)
        escaped = escaped.replace(/(["'])(?:(?=(\\?))\2.)*?\1/g, '<span class="hl-string">$&</span>');

        // Comments (single-line)
        escaped = escaped.replace(/(\/\/.*$|#.*$)/gm, '<span class="hl-comment">$&</span>');

        // Numbers
        escaped = escaped.replace(/\b(\d+\.?\d*)\b/g, '<span class="hl-number">$&</span>');

        return escaped;
    }

    /**
     * Parse a markdown table block into HTML.
     */
    function parseTable(lines) {
        const rows = lines.filter(l => l.trim() && !l.trim().match(/^\|[\s\-:|]+\|$/));
        if (rows.length === 0) return '';

        let html = '<div class="md-table-wrapper"><table class="md-table">';

        rows.forEach((row, i) => {
            const cells = row.split('|').slice(1, -1).map(c => c.trim());
            const tag = i === 0 ? 'th' : 'td';

            if (i === 0) html += '<thead>';
            if (i === 1) html += '<tbody>';

            html += '<tr>';
            cells.forEach(cell => {
                html += `<${tag}>${renderInline(cell)}</${tag}>`;
            });
            html += '</tr>';

            if (i === 0) html += '</thead>';
        });

        html += '</tbody></table></div>';
        return html;
    }

    /**
     * Main render function: converts markdown string to HTML.
     */
    function render(markdown) {
        if (!markdown) return '';

        const lines = markdown.split('\n');
        const htmlParts = [];
        let i = 0;

        while (i < lines.length) {
            const line = lines[i];
            const trimmed = line.trim();

            // ── Code blocks ───────────────────────────────
            if (trimmed.startsWith('```')) {
                const lang = trimmed.slice(3).trim();
                const codeLines = [];
                i++;
                while (i < lines.length && !lines[i].trim().startsWith('```')) {
                    codeLines.push(lines[i]);
                    i++;
                }
                i++; // skip closing ```
                const code = codeLines.join('\n');
                const highlighted = highlightCode(code, lang);
                htmlParts.push(
                    `<div class="md-code-block">` +
                    `<div class="md-code-header">` +
                    `<span class="md-code-lang">${escapeHtml(lang || 'text')}</span>` +
                    `<button class="md-copy-btn" onclick="navigator.clipboard.writeText(this.closest('.md-code-block').querySelector('code').textContent).then(()=>{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1500)})">Copy</button>` +
                    `</div>` +
                    `<pre><code>${highlighted}</code></pre></div>`
                );
                continue;
            }

            // ── Tables ────────────────────────────────────
            if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
                const tableLines = [];
                while (i < lines.length && lines[i].trim().startsWith('|')) {
                    tableLines.push(lines[i]);
                    i++;
                }
                htmlParts.push(parseTable(tableLines));
                continue;
            }

            // ── Headings ──────────────────────────────────
            const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
            if (headingMatch) {
                const level = headingMatch[1].length;
                htmlParts.push(`<h${level} class="md-heading">${renderInline(headingMatch[2])}</h${level}>`);
                i++;
                continue;
            }

            // ── Horizontal rule ───────────────────────────
            if (trimmed.match(/^(-{3,}|\*{3,}|_{3,})$/)) {
                htmlParts.push('<hr class="md-hr">');
                i++;
                continue;
            }

            // ── Blockquote ────────────────────────────────
            if (trimmed.startsWith('>')) {
                const quoteLines = [];
                while (i < lines.length && lines[i].trim().startsWith('>')) {
                    quoteLines.push(lines[i].trim().replace(/^>\s?/, ''));
                    i++;
                }
                htmlParts.push(`<blockquote class="md-blockquote">${renderInline(quoteLines.join(' '))}</blockquote>`);
                continue;
            }

            // ── Unordered list ────────────────────────────
            if (trimmed.match(/^[-*+]\s+/)) {
                const listItems = [];
                while (i < lines.length && lines[i].trim().match(/^[-*+]\s+/)) {
                    listItems.push(lines[i].trim().replace(/^[-*+]\s+/, ''));
                    i++;
                }
                const items = listItems.map(item => `<li>${renderInline(item)}</li>`).join('');
                htmlParts.push(`<ul class="md-list">${items}</ul>`);
                continue;
            }

            // ── Ordered list ──────────────────────────────
            if (trimmed.match(/^\d+\.\s+/)) {
                const listItems = [];
                while (i < lines.length && lines[i].trim().match(/^\d+\.\s+/)) {
                    listItems.push(lines[i].trim().replace(/^\d+\.\s+/, ''));
                    i++;
                }
                const items = listItems.map(item => `<li>${renderInline(item)}</li>`).join('');
                htmlParts.push(`<ol class="md-list">${items}</ol>`);
                continue;
            }

            // ── Empty line ────────────────────────────────
            if (trimmed === '') {
                i++;
                continue;
            }

            // ── Paragraph ─────────────────────────────────
            const paraLines = [];
            while (i < lines.length && lines[i].trim() !== '' && !lines[i].trim().startsWith('#') && !lines[i].trim().startsWith('```') && !lines[i].trim().startsWith('|') && !lines[i].trim().startsWith('>') && !lines[i].trim().match(/^[-*+]\s+/) && !lines[i].trim().match(/^\d+\.\s+/)) {
                paraLines.push(lines[i].trim());
                i++;
            }
            if (paraLines.length > 0) {
                htmlParts.push(`<p class="md-paragraph">${renderInline(paraLines.join(' '))}</p>`);
            }
        }

        return htmlParts.join('\n');
    }

    return { render, escapeHtml, renderInline };
})();
