/* Draadloze AI — Chat JavaScript */

let currentConvId = activeConvId;
let isStreaming = false;

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    // Render existing messages
    if (existingMessages && existingMessages.length > 0) {
        const empty = document.getElementById('chat-empty');
        if (empty) empty.remove();
        existingMessages.forEach(msg => {
            const el = document.getElementById('msg-' + msg.id);
            if (el) el.innerHTML = renderMarkdown(msg.content);
        });
        scrollToBottom();
    }
    document.getElementById('chat-input').focus();
});

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------
async function sendMessage() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message || isStreaming) return;

    isStreaming = true;
    input.value = '';
    autoResize(input);
    document.getElementById('btn-send').disabled = true;

    // Remove empty state
    const empty = document.getElementById('chat-empty');
    if (empty) empty.remove();

    // Add user message to UI
    appendMessage('user', message);

    // Show typing indicator
    const typing = document.getElementById('typing');
    typing.classList.add('visible');

    // Create assistant message placeholder
    const assistantEl = appendMessage('assistant', '');
    const contentEl = assistantEl.querySelector('.message-content');

    let fullText = '';

    try {
        const resp = await fetch('/portal/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                conversation_id: currentConvId,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || 'Request failed');
        }

        // Get conversation ID from header
        const newConvId = resp.headers.get('X-Conversation-Id');
        if (newConvId) {
            currentConvId = parseInt(newConvId);
            // Reload sidebar to show new conversation
            if (!activeConvId) {
                updateSidebar();
            }
        }

        // Read SSE stream
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = line.slice(6);
                if (data === '[DONE]') continue;

                try {
                    const parsed = JSON.parse(data);
                    if (parsed.error) {
                        fullText += parsed.error;
                        contentEl.innerHTML = '<div class="chat-error">' + escapeHtml(parsed.error) + '</div>';
                    } else if (parsed.content) {
                        fullText += parsed.content;
                        contentEl.innerHTML = renderMarkdown(fullText);
                    }
                } catch (e) { /* skip parse errors */ }

                scrollToBottom();
            }
        }
    } catch (err) {
        contentEl.innerHTML = '<div class="chat-error">' + escapeHtml(err.message) + '</div>';
    }

    typing.classList.remove('visible');
    isStreaming = false;
    document.getElementById('btn-send').disabled = false;
    input.focus();
    scrollToBottom();
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------
function appendMessage(role, content) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'message message-' + role;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? userInitials : 'AI';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    if (content) {
        contentDiv.innerHTML = renderMarkdown(content);
    }

    div.appendChild(avatar);
    div.appendChild(contentDiv);
    container.appendChild(div);
    return div;
}

function scrollToBottom() {
    const container = document.getElementById('chat-messages');
    container.scrollTop = container.scrollHeight;
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// ---------------------------------------------------------------------------
// Markdown rendering (simple)
// ---------------------------------------------------------------------------
function renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);

    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        return '<pre><code>' + code.trim() + '</code></pre>';
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Images (from AI Router image generation)
    html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, url) => {
        return '<img src="' + url + '" alt="' + alt + '" onclick="window.open(this.src)">';
    });

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Line breaks
    html = html.replace(/\n/g, '<br>');

    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------
function newChat() {
    window.location.href = '/portal';
}

async function deleteConv(e, convId) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm('Delete this conversation?')) return;

    try {
        await fetch('/portal/api/conversations/' + convId, { method: 'DELETE' });
        if (currentConvId === convId) {
            window.location.href = '/portal';
        } else {
            e.target.closest('.conv-item').remove();
        }
    } catch (err) {
        alert('Failed to delete: ' + err.message);
    }
}

async function updateSidebar() {
    try {
        const r = await fetch('/portal/api/conversations');
        const data = await r.json();
        const list = document.getElementById('conv-list');
        list.innerHTML = '';
        (data.conversations || []).forEach(c => {
            const a = document.createElement('a');
            a.href = '/portal/c/' + c.id;
            a.className = 'conv-item' + (c.id === currentConvId ? ' active' : '');
            a.innerHTML = '<span class="conv-title">' + escapeHtml(c.title) + '</span>' +
                '<button class="conv-delete" onclick="deleteConv(event,' + c.id + ')" title="Delete">&times;</button>';
            list.appendChild(a);
        });
    } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Mobile sidebar
// ---------------------------------------------------------------------------
function toggleSidebar() {
    document.getElementById('chat-sidebar').classList.toggle('open');
    document.getElementById('sidebar-overlay').classList.toggle('visible');
}
