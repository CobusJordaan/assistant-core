/* Draadloze AI — Chat JavaScript */

let currentConvId = activeConvId;
let isStreaming = false;
let pendingImages = []; // {dataUrl, name}

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
// Image upload
// ---------------------------------------------------------------------------
function handleImageUpload(input) {
    const files = Array.from(input.files);
    input.value = ''; // reset so same file can be re-selected

    if (!visionAllowed) {
        showChatAlert('Image uploads are not enabled for your account.');
        return;
    }

    for (const file of files) {
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
            showChatAlert('Only JPG, PNG, and WebP images are allowed.');
            continue;
        }
        if (file.size > 10 * 1024 * 1024) {
            showChatAlert('Image too large (max 10MB): ' + file.name);
            continue;
        }
        if (pendingImages.length >= 4) {
            showChatAlert('Maximum 4 images per message.');
            break;
        }

        const reader = new FileReader();
        reader.onload = (e) => {
            pendingImages.push({ dataUrl: e.target.result, name: file.name });
            renderImagePreview();
        };
        reader.readAsDataURL(file);
    }
}

function removeImage(index) {
    pendingImages.splice(index, 1);
    renderImagePreview();
}

function renderImagePreview() {
    const container = document.getElementById('image-preview');
    container.innerHTML = '';
    pendingImages.forEach((img, i) => {
        const item = document.createElement('div');
        item.className = 'preview-item';

        const thumb = document.createElement('img');
        thumb.src = img.dataUrl;
        thumb.alt = img.name;

        const removeBtn = document.createElement('button');
        removeBtn.className = 'preview-remove';
        removeBtn.textContent = '\u00d7';
        removeBtn.onclick = () => removeImage(i);

        item.appendChild(thumb);
        item.appendChild(removeBtn);
        container.appendChild(item);
    });
}

function showChatAlert(msg) {
    const container = document.getElementById('chat-messages');
    const alert = document.createElement('div');
    alert.className = 'chat-error';
    alert.style.maxWidth = '800px';
    alert.style.margin = '0 auto 12px';
    alert.textContent = msg;
    container.appendChild(alert);
    scrollToBottom();
    setTimeout(() => alert.remove(), 5000);
}

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------
async function sendMessage() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    const images = pendingImages.slice(); // copy

    if ((!message && images.length === 0) || isStreaming) return;

    isStreaming = true;
    input.value = '';
    autoResize(input);
    pendingImages = [];
    renderImagePreview();
    document.getElementById('btn-send').disabled = true;

    // Remove empty state
    const empty = document.getElementById('chat-empty');
    if (empty) empty.remove();

    // Add user message to UI (with image thumbnails if any)
    appendMessage('user', message, images);

    // Show typing indicator
    const typing = document.getElementById('typing');
    typing.classList.add('visible');

    // Create assistant message placeholder
    const assistantEl = appendMessage('assistant', '');
    const contentEl = assistantEl.querySelector('.message-content');

    let fullText = '';

    try {
        const payload = {
            message: message,
            conversation_id: currentConvId,
        };
        if (images.length > 0) {
            payload.images = images.map(img => img.dataUrl);
        }

        const resp = await fetch('/portal/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
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
function appendMessage(role, content, images) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'message message-' + role;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? userInitials : 'AI';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    // Show uploaded image thumbnails for user messages
    if (images && images.length > 0) {
        const imgWrap = document.createElement('div');
        imgWrap.className = 'message-images';
        images.forEach(img => {
            const thumb = document.createElement('img');
            thumb.src = img.dataUrl;
            thumb.alt = img.name || 'uploaded image';
            thumb.onclick = () => window.open(img.dataUrl);
            imgWrap.appendChild(thumb);
        });
        contentDiv.appendChild(imgWrap);
    }

    if (content) {
        const textDiv = document.createElement('div');
        textDiv.innerHTML = renderMarkdown(content);
        contentDiv.appendChild(textDiv);
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
