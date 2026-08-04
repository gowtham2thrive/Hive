document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById("searchInput");
    const searchResults = document.getElementById("searchResults");
    const searchLoading = document.getElementById("searchLoading");
    
    const emptyState = document.getElementById("emptyState");
    const modelDetails = document.getElementById("modelDetails");
    const modelTitle = document.getElementById("modelTitle");
    const modelMeta = document.getElementById("modelMeta");
    const fileList = document.getElementById("fileList");
    const filesLoading = document.getElementById("filesLoading");
    
    const downloadsContainer = document.getElementById("downloadsContainer");
    
    // Navigation
    const navDiscover = document.getElementById("nav-discover");
    const navDownloads = document.getElementById("nav-downloads");
    const navChat = document.getElementById("nav-chat");
    const viewDiscover = document.getElementById("view-discover");
    const viewDownloads = document.getElementById("view-downloads");
    const viewChat = document.getElementById("view-chat");
    const downloadBadge = document.getElementById("download-badge");



    // --- View Switching ---
    const allViews = [viewDiscover, viewDownloads, viewChat];
    const allNavs = [navDiscover, navDownloads, navChat];

    function switchView(view) {
        // Deactivate all
        allNavs.forEach(n => n.classList.remove("active"));
        allViews.forEach(v => {
            v.classList.remove("active");
            v.classList.add("hidden");
        });

        // Activate selected
        if (view === 'discover') {
            navDiscover.classList.add("active");
            viewDiscover.classList.remove("hidden");
            requestAnimationFrame(() => viewDiscover.classList.add("active"));
        } else if (view === 'downloads') {
            navDownloads.classList.add("active");
            viewDownloads.classList.remove("hidden");
            requestAnimationFrame(() => viewDownloads.classList.add("active"));
        } else if (view === 'chat') {
            navChat.classList.add("active");
            viewChat.classList.remove("hidden");
            requestAnimationFrame(() => viewChat.classList.add("active"));
            // Initialize chat on first switch
            if (!chatInitialized) initChat();
        }
    }

    navDiscover.addEventListener("click", () => switchView('discover'));
    navDownloads.addEventListener("click", () => switchView('downloads'));
    navChat.addEventListener("click", () => switchView('chat'));

    let currentModelId = null;

    // --- Search functionality ---
    let searchTimeout = null;
    searchInput.addEventListener("input", (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            performSearch(e.target.value);
        }, 500); // debounce
    });

    searchInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            clearTimeout(searchTimeout);
            performSearch(searchInput.value);
        }
    });

    async function performSearch(query) {
        query = query.trim();
        if (!query) {
            searchResults.innerHTML = "";
            return;
        }

        searchResults.innerHTML = "";
        searchLoading.classList.remove("hidden");

        try {
            const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
            const models = await res.json();
            
            searchLoading.classList.add("hidden");

            if (!Array.isArray(models)) {
                // Bug 16: escape error message
                const errorLi = document.createElement("li");
                errorLi.style.cssText = "padding:1rem;color:red;font-size:0.85rem";
                errorLi.textContent = `Error: ${models.detail || 'Unknown error'}`;
                searchResults.appendChild(errorLi);
                return;
            }
            
            if (models.length === 0) {
                searchResults.innerHTML = "<li style='padding:1rem;color:var(--text-secondary);font-size:0.85rem'>No models found.</li>";
                return;
            }

            // Bug 2: Use textContent instead of innerHTML for user-controlled data
            models.forEach(model => {
                const li = document.createElement("li");
                li.className = "model-item";

                const titleDiv = document.createElement("div");
                titleDiv.className = "model-item-title";
                titleDiv.textContent = model.id;

                const statsDiv = document.createElement("div");
                statsDiv.className = "model-item-stats";
                statsDiv.innerHTML = `
                    <span><i data-feather="download"></i>${formatNumber(model.downloads)}</span>
                    <span><i data-feather="heart"></i>${formatNumber(model.likes)}</span>
                `;

                li.appendChild(titleDiv);
                li.appendChild(statsDiv);

                li.addEventListener("click", () => {
                    document.querySelectorAll(".model-item").forEach(el => el.classList.remove("active"));
                    li.classList.add("active");
                    loadModelDetails(model);
                });
                searchResults.appendChild(li);
            });
            feather.replace(); // re-render icons
        } catch (err) {
            searchLoading.classList.add("hidden");
            // Bug 16: escape error message
            const errorLi = document.createElement("li");
            errorLi.style.cssText = "padding:1rem;color:red;font-size:0.85rem";
            errorLi.textContent = `Error: ${err.message}`;
            searchResults.appendChild(errorLi);
        }
    }

    // --- Model Details ---
    async function loadModelDetails(modelInfo) {
        currentModelId = modelInfo.id;
        
        emptyState.classList.add("hidden");
        modelDetails.classList.remove("hidden");
        
        modelTitle.textContent = modelInfo.id;

        // Bug 2: Use safe DOM construction for author
        modelMeta.innerHTML = "";
        const authorSpan = document.createElement("span");
        authorSpan.innerHTML = '<i data-feather="user" style="width:14px;height:14px;vertical-align:-2px;margin-right:4px;"></i>';
        authorSpan.appendChild(document.createTextNode(modelInfo.author || 'Unknown'));
        const dlSpan = document.createElement("span");
        dlSpan.innerHTML = '<i data-feather="download" style="width:14px;height:14px;vertical-align:-2px;margin-right:4px;"></i>';
        dlSpan.appendChild(document.createTextNode(formatNumber(modelInfo.downloads)));
        modelMeta.appendChild(authorSpan);
        modelMeta.appendChild(dlSpan);
        
        fileList.innerHTML = "";
        filesLoading.classList.remove("hidden");
        feather.replace();

        try {
            // Bug 3: Don't use encodeURIComponent on path-style model IDs
            // The FastAPI route uses {repo_id:path} which expects literal slashes
            const res = await fetch(`/api/model/${modelInfo.id}`);
            const data = await res.json();
            
            filesLoading.classList.add("hidden");
            
            if (data.files.length === 0) {
                fileList.innerHTML = "<li class='file-item' style='justify-content:center;color:var(--text-secondary);'>No GGUF files found.</li>";
                return;
            }

            data.files.forEach(filename => {
                const li = document.createElement("li");
                li.className = "file-item";
                
                const nameDiv = document.createElement("div");
                nameDiv.className = "file-name";
                nameDiv.textContent = filename;
                
                const btn = document.createElement("button");
                btn.className = "download-btn";
                btn.innerHTML = `<i data-feather="download-cloud"></i> Download`;
                btn.addEventListener("click", () => handleDownloadClick(filename));

                li.appendChild(nameDiv);
                li.appendChild(btn);
                fileList.appendChild(li);
            });
            feather.replace();

        } catch (err) {
            filesLoading.classList.add("hidden");
            // Bug 16: escape error
            const errorLi = document.createElement("li");
            errorLi.className = "file-item";
            errorLi.style.color = "red";
            errorLi.textContent = `Error: ${err.message}`;
            fileList.appendChild(errorLi);
        }
    }

    // --- Download ---
    async function handleDownloadClick(filename) {
        if (!currentModelId) return;

        try {
            const response = await fetch("/api/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    repo_id: currentModelId, 
                    filename: filename
                })
            });
            const data = await response.json();
            console.log(data);
        } catch (err) {
            console.error("Failed to start download:", err);
            alert("Failed to start download");
        }
    }

    // --- WebSockets (Progress) ---
    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/progress`;
        const ws = new WebSocket(wsUrl);

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            updateDownloadsUI(data);
        };

        ws.onclose = () => {
            console.log("WebSocket closed, reconnecting...");
            setTimeout(connectWebSocket, 3000);
        };
        
        ws.onerror = (err) => {
            console.error("WebSocket error:", err);
        };
    }

    // --- Cancel / Pause / Resume Download ---
    window.cancelDownload = async function(filename) {
        try {
            await fetch("/api/cancel", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename })
            });
        } catch (err) {
            console.error("Failed to cancel download:", err);
        }
    };

    window.pauseDownload = async function(filename) {
        try {
            await fetch("/api/pause", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename })
            });
        } catch (err) {
            console.error("Failed to pause download:", err);
        }
    };

    window.resumeDownload = async function(filename, repoId) {
        try {
            await fetch("/api/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    repo_id: repoId, 
                    filename: filename
                })
            });
        } catch (err) {
            console.error("Failed to resume download:", err);
        }
    };

    window.openFolder = async function(filename) {
        try {
            await fetch("/api/open_folder", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename })
            });
        } catch (err) {
            console.error("Failed to open folder:", err);
        }
    };

    window.deleteFile = async function(filename) {
        if(!confirm(`Are you sure you want to delete ${filename}?`)) return;
        try {
            await fetch("/api/delete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename })
            });
            // Immediately remove it from the UI so we don't wait for websocket
            const card = document.querySelector(`.download-card[data-filename="${CSS.escape(filename)}"]`);
            if (card) card.remove();
        } catch (err) {
            console.error("Failed to delete file:", err);
        }
    };

    window.dismissFile = async function(filename) {
        try {
            await fetch("/api/dismiss", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename })
            });
            const card = document.querySelector(`.download-card[data-filename="${CSS.escape(filename)}"]`);
            if (card) card.remove();
        } catch (err) {
            console.error("Failed to dismiss file:", err);
        }
    };

    // Bug 1: Build download cards using safe DOM construction instead of innerHTML
    function createDownloadCard(filename, info) {
        const card = document.createElement("div");
        card.className = "download-card";
        card.dataset.filename = filename;

        // Header
        const header = document.createElement("div");
        header.className = "download-header";

        const fnameSpan = document.createElement("span");
        fnameSpan.className = "download-filename";
        fnameSpan.textContent = filename; // Safe: textContent, not innerHTML

        const actions = document.createElement("div");
        actions.className = "download-actions";

        // Folder button
        const folderBtn = document.createElement("button");
        folderBtn.className = "action-btn folder-btn";
        folderBtn.title = "Show in Folder";
        folderBtn.style.display = "none";
        folderBtn.innerHTML = '<i data-feather="folder"></i>';
        folderBtn.addEventListener("click", () => openFolder(filename));

        // Resume button
        const resumeBtn = document.createElement("button");
        resumeBtn.className = "action-btn resume-btn";
        resumeBtn.title = "Resume Download";
        resumeBtn.style.display = "none";
        resumeBtn.innerHTML = '<i data-feather="play"></i>';
        resumeBtn.addEventListener("click", () => resumeDownload(filename, info.repo_id));

        // Pause button
        const pauseBtn = document.createElement("button");
        pauseBtn.className = "action-btn pause-btn";
        pauseBtn.title = "Pause Download";
        pauseBtn.innerHTML = '<i data-feather="pause"></i>';
        pauseBtn.addEventListener("click", () => pauseDownload(filename));

        // Cancel button
        const cancelBtn = document.createElement("button");
        cancelBtn.className = "action-btn cancel-btn";
        cancelBtn.title = "Cancel Download";
        cancelBtn.innerHTML = '<i data-feather="x"></i>';
        cancelBtn.addEventListener("click", () => cancelDownload(filename));

        // Delete button
        const deleteBtn = document.createElement("button");
        deleteBtn.className = "action-btn delete-btn";
        deleteBtn.title = "Delete File";
        deleteBtn.style.display = "none";
        deleteBtn.innerHTML = '<i data-feather="trash-2"></i>';
        deleteBtn.addEventListener("click", () => deleteFile(filename));

        // Dismiss button
        const dismissBtn = document.createElement("button");
        dismissBtn.className = "action-btn dismiss-btn";
        dismissBtn.title = "Dismiss";
        dismissBtn.style.display = "none";
        dismissBtn.innerHTML = '<i data-feather="x-circle"></i>';
        dismissBtn.addEventListener("click", () => dismissFile(filename));

        actions.appendChild(folderBtn);
        actions.appendChild(resumeBtn);
        actions.appendChild(pauseBtn);
        actions.appendChild(cancelBtn);
        actions.appendChild(deleteBtn);
        actions.appendChild(dismissBtn);

        header.appendChild(fnameSpan);
        header.appendChild(actions);

        // Status
        const statusDiv = document.createElement("div");
        statusDiv.className = "download-status";
        statusDiv.setAttribute("data-status", "");

        // Progress bar
        const progressContainer = document.createElement("div");
        progressContainer.className = "progress-bar-container";
        const progressBar = document.createElement("div");
        progressBar.className = "progress-bar";
        progressBar.setAttribute("data-progress", "");
        progressContainer.appendChild(progressBar);

        // Stats
        const stats = document.createElement("div");
        stats.className = "download-stats";
        const completedSpan = document.createElement("span");
        completedSpan.setAttribute("data-completed", "");
        completedSpan.textContent = "0 B";
        const totalSpan = document.createElement("span");
        totalSpan.setAttribute("data-total", "");
        totalSpan.textContent = "Unknown";
        stats.appendChild(completedSpan);
        stats.appendChild(totalSpan);

        card.appendChild(header);
        card.appendChild(statusDiv);
        card.appendChild(progressContainer);
        card.appendChild(stats);

        return card;
    }

    function updateDownloadsUI(activeDownloads) {
        const filenames = Object.keys(activeDownloads);
        
        if (filenames.length === 0) {
            downloadsContainer.innerHTML = "<div class='empty-downloads'>No local models or active downloads.</div>";
            downloadBadge.classList.add("hidden");
            return;
        }

        // Calculate badge count (active downloads)
        let activeCount = 0;
        filenames.forEach(filename => {
            const status = activeDownloads[filename].status;
            if (status === "downloading" || status === "starting" || status === "paused") {
                activeCount++;
            }
        });
        
        if (activeCount > 0) {
            downloadBadge.textContent = activeCount;
            downloadBadge.classList.remove("hidden");
        } else {
            downloadBadge.classList.add("hidden");
        }

        const currentElements = Array.from(downloadsContainer.children);
        
        // Remove empty placeholder
        if (currentElements.length === 1 && currentElements[0].classList.contains("empty-downloads")) {
            downloadsContainer.innerHTML = "";
        }
        
        // Remove stale cards
        currentElements.forEach(card => {
            if (card.classList.contains("empty-downloads")) return;
            const fname = card.dataset.filename;
            if (!activeDownloads[fname]) {
                card.remove();
            }
        });

        filenames.forEach(filename => {
            const info = activeDownloads[filename];
            let card = downloadsContainer.querySelector(`[data-filename="${CSS.escape(filename)}"]`);
            
            if (!card) {
                card = createDownloadCard(filename, info);
                downloadsContainer.appendChild(card);
                feather.replace(); // render new icons
            }

            const statusEl = card.querySelector("[data-status]");
            const progressEl = card.querySelector("[data-progress]");
            const completedEl = card.querySelector("[data-completed]");
            const totalEl = card.querySelector("[data-total]");
            const cancelBtn = card.querySelector(".cancel-btn");
            const pauseBtn = card.querySelector(".pause-btn");
            const resumeBtn = card.querySelector(".resume-btn");
            const folderBtn = card.querySelector(".folder-btn");
            const deleteBtn = card.querySelector(".delete-btn");
            const dismissBtn = card.querySelector(".dismiss-btn");

            statusEl.textContent = info.status.charAt(0).toUpperCase() + info.status.slice(1);
            
            if (info.status === "completed" || info.status === "canceled" || info.status.startsWith("error") || info.status === "paused") {
                if (info.status === "completed") {
                    card.classList.add("completed");
                    card.classList.remove("canceled", "error", "paused");
                    progressEl.style.width = "100%";
                } else if (info.status === "canceled") {
                    card.classList.add("canceled");
                    card.classList.remove("completed", "error", "paused");
                } else if (info.status === "paused") {
                    card.classList.add("paused");
                    card.classList.remove("completed", "error", "canceled");
                } else {
                    card.classList.add("error");
                    card.classList.remove("completed", "canceled", "paused");
                }
                
                // Hide pause/resume/cancel button if not actively downloading
                if (pauseBtn) pauseBtn.style.display = "none";
                if (resumeBtn) resumeBtn.style.display = "none";
                if (cancelBtn) cancelBtn.style.display = "none";
                if (folderBtn) folderBtn.style.display = "none";
                if (deleteBtn) deleteBtn.style.display = "none";
                if (dismissBtn) dismissBtn.style.display = "none";
                
                if (info.status === "paused") {
                    if (resumeBtn) resumeBtn.style.display = "flex";
                    if (cancelBtn) cancelBtn.style.display = "flex";
                } else if (info.status === "completed") {
                    if (folderBtn) folderBtn.style.display = "flex";
                    if (deleteBtn) deleteBtn.style.display = "flex";
                } else if (info.status.startsWith("error") || info.status === "canceled") {
                    if (dismissBtn) dismissBtn.style.display = "flex"; // Allow dismissing the partial/failed file
                }
                
                const percent = info.total > 0 ? (info.completed / info.total) * 100 : 0;
                if (info.status !== "completed") {
                    progressEl.style.width = `${percent}%`;
                }
                completedEl.textContent = formatBytes(info.completed);
                totalEl.textContent = formatBytes(info.total);
            } else {
                card.classList.remove("completed", "canceled", "error", "paused");
                if (pauseBtn) pauseBtn.style.display = "flex";
                if (resumeBtn) resumeBtn.style.display = "none";
                if (cancelBtn) cancelBtn.style.display = "flex";
                if (folderBtn) folderBtn.style.display = "none";
                if (deleteBtn) deleteBtn.style.display = "none";
                if (dismissBtn) dismissBtn.style.display = "none";
                
                const percent = info.total > 0 ? (info.completed / info.total) * 100 : 0;
                progressEl.style.width = `${percent}%`;
                completedEl.textContent = formatBytes(info.completed);
                totalEl.textContent = formatBytes(info.total);
            }
        });
    }

    // --- Helpers ---
    function formatNumber(num) {
        if (num === undefined || num === null) return "0";
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'k';
        return num.toString();
    }

    function formatBytes(bytes, decimals = 2) {
        if (!+bytes) return '0 B';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
    }

    // --- Config and Path Selection ---
    const downloadPathInput = document.getElementById("downloadPathInput");
    const btnBrowsePath = document.getElementById("btnBrowsePath");
    const btnSavePath = document.getElementById("btnSavePath");

    async function loadConfig() {
        try {
            const res = await fetch("/api/config");
            const data = await res.json();
            if (data.models_dir) {
                downloadPathInput.value = data.models_dir;
            }
        } catch (err) {
            console.error("Failed to load config:", err);
        }
    }

    if (btnBrowsePath) {
        btnBrowsePath.addEventListener("click", async () => {
            btnBrowsePath.disabled = true;
            btnBrowsePath.innerHTML = '<div class="spinner" style="width:14px;height:14px;"></div>';
            try {
                const res = await fetch("/api/choose_directory");
                const data = await res.json();
                if (data.directory) {
                    downloadPathInput.value = data.directory;
                }
            } catch (err) {
                console.error("Failed to choose directory:", err);
            }
            btnBrowsePath.innerHTML = '<i data-feather="folder"></i>';
            feather.replace();
            btnBrowsePath.disabled = false;
        });
    }

    if (btnSavePath) {
        btnSavePath.addEventListener("click", async () => {
            const newPath = downloadPathInput.value.trim();
            if (!newPath) return;
            
            btnSavePath.textContent = "Saving...";
            btnSavePath.disabled = true;
            
            try {
                const res = await fetch("/api/config", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ models_dir: newPath })
                });
                if (res.ok) {
                    btnSavePath.textContent = "Saved!";
                    btnSavePath.style.background = "var(--success, #10b981)";
                    // The websocket will send the updated local models list in a second
                } else {
                    btnSavePath.textContent = "Failed";
                    btnSavePath.style.background = "var(--error, #ef4444)";
                }
            } catch (err) {
                console.error("Failed to save config:", err);
                btnSavePath.textContent = "Failed";
                btnSavePath.style.background = "var(--error, #ef4444)";
            }
            
            setTimeout(() => {
                btnSavePath.textContent = "Save";
                btnSavePath.style.background = "var(--accent)";
                btnSavePath.disabled = false;
            }, 2000);
        });
    }

    // Load initial config
    loadConfig();

    // Start WebSocket
    connectWebSocket();

    // ═══════════════════════════════════════════════════════════════════
    // CHAT MODULE
    // ═══════════════════════════════════════════════════════════════════

    let chatInitialized = false;

    // ── Chat State ────────────────────────────────────────────────────
    const chatState = {
        ws: null,
        conversations: [],
        currentConversationId: null,
        isStreaming: false,
        isModelLoaded: false,
        modelInfo: {},
        settings: { temperature: 0.7 },
        reconnectAttempts: 0,
        maxReconnectAttempts: 10,
    };

    // ── Chat DOM ──────────────────────────────────────────────────────
    const chatDom = {
        messagesContainer: null,
        messageInput: null,
        sendBtn: null,
        stopBtn: null,
        newChatBtn: null,
        conversationList: null,
        searchInput: null,
        chatTitle: null,
        settingsPanel: null,
        settingsToggle: null,
        settingsClose: null,
        modelSelect: null,
        browseBtn: null,
        loadBtn: null,
        unloadBtn: null,
        modelDot: null,
        modelText: null,
        modelInfoCard: null,
        loadingOverlay: null,
        temperatureSlider: null,
        temperatureValue: null,
        toast: null,
        modelBadge: null,
    };

    function initChat() {
        chatInitialized = true;

        // Bind DOM refs
        chatDom.messagesContainer = document.getElementById('chatMessagesContainer');
        chatDom.messageInput = document.getElementById('chatMessageInput');
        chatDom.sendBtn = document.getElementById('chatSendBtn');
        chatDom.stopBtn = document.getElementById('chatStopBtn');
        chatDom.newChatBtn = document.getElementById('chat-new-btn');
        chatDom.conversationList = document.getElementById('chatConversationList');
        chatDom.searchInput = document.getElementById('chatSearchInput');
        chatDom.chatTitle = document.getElementById('chatTitle');
        chatDom.settingsPanel = document.getElementById('chatSettingsPanel');
        chatDom.settingsToggle = document.getElementById('chat-settings-toggle');
        chatDom.settingsClose = document.getElementById('chatSettingsClose');
        chatDom.modelSelect = document.getElementById('chatModelSelect');
        chatDom.browseBtn = document.getElementById('chatBrowseBtn');
        chatDom.loadBtn = document.getElementById('chatLoadModelBtn');
        chatDom.unloadBtn = document.getElementById('chatUnloadModelBtn');
        chatDom.modelDot = document.getElementById('chatModelDot');
        chatDom.modelText = document.getElementById('chatModelText');
        chatDom.modelInfoCard = document.getElementById('chatModelInfoCard');
        chatDom.loadingOverlay = document.getElementById('chatLoadingOverlay');
        chatDom.temperatureSlider = document.getElementById('chatTemperatureSlider');
        chatDom.temperatureValue = document.getElementById('chatTemperatureValue');
        chatDom.toast = document.getElementById('chatToast');
        chatDom.modelBadge = document.getElementById('chatModelBadge');

        // Wire up events
        chatDom.sendBtn.addEventListener('click', chatSendMessage);
        chatDom.stopBtn.addEventListener('click', chatStopGeneration);
        chatDom.newChatBtn.addEventListener('click', chatNewChat);
        chatDom.settingsToggle.addEventListener('click', () => chatDom.settingsPanel.classList.toggle('open'));
        chatDom.settingsClose.addEventListener('click', () => chatDom.settingsPanel.classList.remove('open'));
        chatDom.modelBadge.addEventListener('click', () => {
            chatDom.settingsPanel.classList.add('open');
            switchView('chat');
        });

        chatDom.temperatureSlider.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value);
            chatState.settings.temperature = val;
            chatDom.temperatureValue.textContent = e.target.value;

            // Dynamic mood label
            const label = document.getElementById('chatTemperatureLabel');
            if (label) {
                if (val <= 0.1)      label.textContent = 'Deterministic';
                else if (val <= 0.4) label.textContent = 'Precise';
                else if (val <= 0.8) label.textContent = 'Balanced';
                else if (val <= 1.3) label.textContent = 'Creative';
                else                 label.textContent = 'Wild';
            }
        });

        chatDom.loadBtn.addEventListener('click', chatLoadModel);
        chatDom.unloadBtn.addEventListener('click', chatUnloadModel);
        chatDom.browseBtn.addEventListener('click', chatBrowseFile);
        chatDom.searchInput.addEventListener('input', (e) => chatRenderConversationList(e.target.value));

        // Textarea auto-resize + keyboard shortcuts
        chatDom.messageInput.addEventListener('input', chatAutoResize);
        chatDom.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                chatSendMessage();
            }
            // Plain Enter without shift also sends (optional — like most chat UIs)
            if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
                e.preventDefault();
                chatSendMessage();
            }
        });

        // Scroll detection for smart auto-scroll
        chatDom.messagesContainer.addEventListener('scroll', () => {
            if (chatState.isStreaming) {
                const el = chatDom.messagesContainer;
                const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
                chatUserScrolledUp = dist > 150;
            }
        });

        // Initialize
        chatShowWelcome();
        chatConnectWebSocket();
        chatLoadConversations();
        chatCheckModelStatus();
        chatRefreshModelList();
    }

    // ── Chat WebSocket ────────────────────────────────────────────────

    function chatConnectWebSocket() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${location.host}/ws/chat`;

        chatState.ws = new WebSocket(wsUrl);

        chatState.ws.onopen = () => {
            chatState.reconnectAttempts = 0;
            console.log('[Hive Chat] WebSocket connected');
        };

        chatState.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                chatHandleWSMessage(data);
            } catch (err) {
                console.error('[Hive Chat] Parse error:', err);
            }
        };

        chatState.ws.onclose = () => {
            console.log('[Hive Chat] WebSocket disconnected');
            if (chatState.isStreaming) chatResetStreamingState();
            if (chatState.reconnectAttempts < chatState.maxReconnectAttempts) {
                const delay = Math.min(1000 * Math.pow(2, chatState.reconnectAttempts), 10000);
                chatState.reconnectAttempts++;
                setTimeout(chatConnectWebSocket, delay);
            }
        };

        chatState.ws.onerror = (err) => console.error('[Hive Chat] WS error:', err);
    }

    function chatHandleWSMessage(data) {
        // Reset inactivity watchdog on any message
        if (chatState.isStreaming && chatState._inactivityTimeout) {
            clearTimeout(chatState._inactivityTimeout);
            chatState._inactivityTimeout = chatStartInactivityWatchdog();
        }

        switch (data.type) {
            case 'token':
                chatAppendToken(data.content);
                break;
            case 'status':
                chatUpdateStreamingStatus(data.message);
                break;
            case 'done':
                chatFinishStreaming(data.conversation_id, data.full_content);
                break;
            case 'conversation_created':
                chatHandleConversationCreated(data.conversation);
                break;
            case 'heartbeat':
                break;
            case 'cancelled':
                chatFinishStreaming();
                chatShowToast('Generation stopped', 'info');
                break;
            case 'error':
                chatFinishStreaming();
                chatShowToast(data.message, 'error');
                break;
        }
    }

    // ── Chat Send / Cancel ────────────────────────────────────────────

    function chatSendMessage() {
        const content = chatDom.messageInput.value.trim();
        if (!content || chatState.isStreaming) return;

        if (!chatState.isModelLoaded) {
            chatShowToast('Load a model first (click ⚙ Settings → Model)', 'error');
            return;
        }

        if (!chatState.ws || chatState.ws.readyState !== WebSocket.OPEN) {
            chatShowToast('Reconnecting to server...', 'error');
            return;
        }

        chatAddMessageToUI('user', content);
        chatDom.messageInput.value = '';
        chatAutoResize();

        chatStartStreaming();

        try {
            chatState.ws.send(JSON.stringify({
                conversation_id: chatState.currentConversationId,
                message: content,
                temperature: chatState.settings.temperature,
            }));
        } catch (err) {
            chatResetStreamingState();
            chatShowToast('Failed to send — reconnecting...', 'error');
        }
    }

    function chatStopGeneration() {
        if (!chatState.isStreaming) return;
        if (chatState.ws && chatState.ws.readyState === WebSocket.OPEN) {
            try { chatState.ws.send(JSON.stringify({ type: 'cancel' })); } catch {}
        }
        chatDom.stopBtn.style.display = 'none';

        // Show cancelling indicator
        const streamingMsg = document.getElementById('chat-streaming-message');
        if (streamingMsg) {
            const timerEl = streamingMsg.querySelector('.chat-elapsed-timer');
            if (timerEl) {
                timerEl.textContent = 'cancelling…';
                timerEl.style.display = 'inline';
                timerEl.classList.add('cancelling');
            }
            if (chatCurrentAssistantMessage && chatStreamBuffer.length === 0) {
                chatCurrentAssistantMessage.innerHTML =
                    '<div class="cancelling-indicator"><span class="cancel-pulse"></span> Cancelling…</div>';
            }
        }

        chatState._cancelTimeout = setTimeout(() => {
            if (chatState.isStreaming) {
                if (chatState.ws && chatState.ws.readyState === WebSocket.OPEN) {
                    try { chatState.ws.send(JSON.stringify({ type: 'cancel' })); } catch {}
                }
                chatResetStreamingState();
                chatShowToast('Generation timed out', 'info');
            }
        }, 8000);
    }

    // ── Chat Streaming ────────────────────────────────────────────────

    let chatCurrentAssistantMessage = null;
    let chatStreamBuffer = '';
    let chatLastRenderedLength = 0;
    let chatRenderScheduled = false;
    let chatStableHtmlCache = '';
    let chatStableBufferIndex = 0;
    let chatInCodeFence = false;
    let chatRenderRAF = null;
    let chatLastRenderTime = 0;
    let chatCurrentRenderInterval = 120;
    let chatUserScrolledUp = false;
    const CHAT_INACTIVITY_TIMEOUT_MS = 20000;

    function chatStartInactivityWatchdog() {
        return setTimeout(() => {
            if (chatState.isStreaming) {
                chatResetStreamingState();
                chatShowToast('No response from server — try again', 'error');
            }
        }, CHAT_INACTIVITY_TIMEOUT_MS);
    }

    function chatFindStableSplitPoint() {
        let fenceOpen = chatInCodeFence;
        let lastSafeBreak = chatStableBufferIndex;
        const text = chatStreamBuffer;
        let i = chatStableBufferIndex;
        while (i < text.length - 1) {
            if (text[i] === '`' && i + 2 < text.length && text[i+1] === '`' && text[i+2] === '`'
                && (i === 0 || text[i-1] === '\n')) {
                fenceOpen = !fenceOpen;
                i += 3;
                while (i < text.length && text[i] !== '\n') i++;
                if (i < text.length) i++;
                continue;
            }
            if (!fenceOpen && text[i] === '\n' && text[i+1] === '\n') {
                lastSafeBreak = i + 2;
            }
            i++;
        }
        return { splitPoint: lastSafeBreak, fenceState: fenceOpen };
    }

    function chatDoIncrementalRender() {
        if (!chatCurrentAssistantMessage || chatStreamBuffer.length <= chatLastRenderedLength) return;
        const { splitPoint, fenceState } = chatFindStableSplitPoint();
        if (splitPoint > chatStableBufferIndex) {
            const newChunk = chatStreamBuffer.substring(chatStableBufferIndex, splitPoint);
            chatStableHtmlCache += MarkdownRenderer.render(newChunk);
            chatStableBufferIndex = splitPoint;
            chatInCodeFence = fenceState;
        }
        const activeTail = chatStreamBuffer.substring(chatStableBufferIndex);
        const tailHtml = activeTail ? MarkdownRenderer.render(activeTail) : '';
        chatCurrentAssistantMessage.innerHTML = chatStableHtmlCache + tailHtml;
        chatLastRenderedLength = chatStreamBuffer.length;
        chatScrollToBottom();

        if (chatStreamBuffer.length > 20000) chatCurrentRenderInterval = 400;
        else if (chatStreamBuffer.length > 5000) chatCurrentRenderInterval = 250;
        else chatCurrentRenderInterval = 120;
    }

    function chatScheduleRender() {
        if (!chatState.isStreaming) return;
        chatRenderRAF = requestAnimationFrame((ts) => {
            if (!chatState.isStreaming || !chatCurrentAssistantMessage) return;
            if (ts - chatLastRenderTime >= chatCurrentRenderInterval) {
                chatDoIncrementalRender();
                chatLastRenderTime = ts;
            }
            chatScheduleRender();
        });
    }

    function chatStartStreaming() {
        chatState.isStreaming = true;
        chatDom.sendBtn.style.display = 'none';
        chatDom.stopBtn.style.display = 'flex';
        chatStreamBuffer = '';
        chatLastRenderedLength = 0;
        chatRenderScheduled = false;
        chatUserScrolledUp = false;
        chatStableHtmlCache = '';
        chatStableBufferIndex = 0;
        chatInCodeFence = false;
        chatCurrentRenderInterval = 120;
        chatLastRenderTime = 0;

        chatState._inactivityTimeout = chatStartInactivityWatchdog();
        chatState._streamStartTime = Date.now();
        chatState._elapsedInterval = setInterval(chatUpdateElapsedDisplay, 1000);

        const msgEl = chatCreateMessageElement('assistant', '');
        msgEl.id = 'chat-streaming-message';
        const contentEl = msgEl.querySelector('.chat-message-content');
        contentEl.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
        chatDom.messagesContainer.appendChild(msgEl);
        chatCurrentAssistantMessage = contentEl;
        chatScrollToBottom(true);
        chatScheduleRender();
    }

    function chatUpdateElapsedDisplay() {
        if (!chatState.isStreaming) return;
        const elapsed = Math.round((Date.now() - chatState._streamStartTime) / 1000);
        if (elapsed >= 3) {
            const streamingMsg = document.getElementById('chat-streaming-message');
            if (streamingMsg) {
                const timerEl = streamingMsg.querySelector('.chat-elapsed-timer');
                if (timerEl && !timerEl.classList.contains('cancelling')) {
                    timerEl.textContent = `${elapsed}s`;
                    timerEl.style.display = 'inline';
                }
            }
        }
    }

    function chatUpdateStreamingStatus(message) {
        if (chatCurrentAssistantMessage && chatLastRenderedLength === 0 && message) {
            const indicator = chatCurrentAssistantMessage.querySelector('.typing-indicator');
            if (indicator) {
                indicator.innerHTML = `<span></span><span></span><span></span><span class="typing-label">${MarkdownRenderer.escapeHtml(message)}</span>`;
            }
        }
    }

    function chatAppendToken(token) {
        if (!chatCurrentAssistantMessage) return;
        chatStreamBuffer += token;
        if (chatLastRenderedLength === 0 && !chatRenderScheduled) {
            chatRenderScheduled = true;
            requestAnimationFrame(() => {
                if (chatCurrentAssistantMessage && chatStreamBuffer) {
                    chatCurrentAssistantMessage.innerHTML = MarkdownRenderer.render(chatStreamBuffer);
                    chatLastRenderedLength = chatStreamBuffer.length;
                    chatScrollToBottom();
                }
                chatRenderScheduled = false;
            });
        }
    }

    function chatFinishStreaming(convId, fullContent) {
        chatState.isStreaming = false;
        chatDom.sendBtn.style.display = 'flex';
        chatDom.stopBtn.style.display = 'none';

        if (chatState._cancelTimeout) { clearTimeout(chatState._cancelTimeout); chatState._cancelTimeout = null; }
        if (chatState._inactivityTimeout) { clearTimeout(chatState._inactivityTimeout); chatState._inactivityTimeout = null; }
        if (chatState._elapsedInterval) { clearInterval(chatState._elapsedInterval); chatState._elapsedInterval = null; }
        if (chatRenderRAF) { cancelAnimationFrame(chatRenderRAF); chatRenderRAF = null; }

        if (chatCurrentAssistantMessage && fullContent) {
            chatStreamBuffer = fullContent;
            chatCurrentAssistantMessage.innerHTML = MarkdownRenderer.render(fullContent);
        } else if (chatCurrentAssistantMessage && chatStreamBuffer) {
            chatCurrentAssistantMessage.innerHTML = MarkdownRenderer.render(chatStreamBuffer);
        } else if (chatCurrentAssistantMessage) {
            chatCurrentAssistantMessage.innerHTML = '<p class="md-paragraph" style="color: var(--text-secondary); font-style: italic;">No response generated. Try rephrasing your message.</p>';
        }

        const streamingMsg = document.getElementById('chat-streaming-message');
        if (streamingMsg) {
            const timerEl = streamingMsg.querySelector('.chat-elapsed-timer');
            if (timerEl) timerEl.style.display = 'none';
            streamingMsg.removeAttribute('id');
        }

        chatCurrentAssistantMessage = null;
        chatStreamBuffer = '';
        chatLastRenderedLength = 0;
        chatRenderScheduled = false;
        chatStableHtmlCache = '';
        chatStableBufferIndex = 0;
        chatInCodeFence = false;

        if (convId && !chatState.currentConversationId) {
            chatState.currentConversationId = convId;
        }
        chatLoadConversations();
        chatScrollToBottom(true);
    }

    function chatResetStreamingState() {
        chatState.isStreaming = false;
        chatDom.sendBtn.style.display = 'flex';
        chatDom.stopBtn.style.display = 'none';
        if (chatState._cancelTimeout) { clearTimeout(chatState._cancelTimeout); chatState._cancelTimeout = null; }
        if (chatState._inactivityTimeout) { clearTimeout(chatState._inactivityTimeout); chatState._inactivityTimeout = null; }
        if (chatState._elapsedInterval) { clearInterval(chatState._elapsedInterval); chatState._elapsedInterval = null; }
        if (chatRenderRAF) { cancelAnimationFrame(chatRenderRAF); chatRenderRAF = null; }
        if (chatCurrentAssistantMessage && chatStreamBuffer) {
            chatCurrentAssistantMessage.innerHTML = MarkdownRenderer.render(chatStreamBuffer);
        }
        const streamingMsg = document.getElementById('chat-streaming-message');
        if (streamingMsg) {
            const timerEl = streamingMsg.querySelector('.chat-elapsed-timer');
            if (timerEl) timerEl.style.display = 'none';
            streamingMsg.removeAttribute('id');
        }
        chatCurrentAssistantMessage = null;
        chatStreamBuffer = '';
        chatLastRenderedLength = 0;
        chatRenderScheduled = false;
        chatStableHtmlCache = '';
        chatStableBufferIndex = 0;
        chatInCodeFence = false;
    }

    // ── Chat Messages UI ──────────────────────────────────────────────

    function chatCreateMessageElement(role, content) {
        const div = document.createElement('div');
        div.className = `chat-message ${role}`;

        const avatarText = role === 'user' ? 'U' : '⬡';
        const roleLabel = role === 'user' ? 'You' : 'Hive';

        div.innerHTML = `
            <div class="chat-message-avatar">${avatarText}</div>
            <div class="chat-message-body">
                <div class="chat-message-role">${roleLabel}${role === 'assistant' ? ' <span class="chat-elapsed-timer"></span>' : ''}</div>
                <div class="chat-message-content">${role === 'user' ? MarkdownRenderer.escapeHtml(content) : MarkdownRenderer.render(content)}</div>
            </div>
            <div class="chat-message-actions">
                <button class="chat-msg-copy-btn" title="Copy" onclick="navigator.clipboard.writeText(this.closest('.chat-message').querySelector('.chat-message-content').innerText).then(()=>this.textContent='Copied!').then(()=>setTimeout(()=>this.textContent='Copy',1500))">Copy</button>
            </div>
        `;
        return div;
    }

    function chatAddMessageToUI(role, content) {
        const msgEl = chatCreateMessageElement(role, content);
        chatDom.messagesContainer.appendChild(msgEl);
        chatScrollToBottom(true);
    }

    function chatRenderMessages(messages) {
        chatDom.messagesContainer.innerHTML = '';
        if (messages.length === 0) {
            chatShowWelcome();
            return;
        }
        messages.forEach(msg => chatAddMessageToUI(msg.role, msg.content));
        chatScrollToBottom();
    }

    function chatShowWelcome() {
        chatDom.messagesContainer.innerHTML = `
            <div class="chat-welcome-screen">
                <div class="chat-welcome-icon">⬡</div>
                <h1 class="chat-welcome-title">Hive Chat</h1>
                <p class="chat-welcome-subtitle">Chat with your local GGUF models. Load a model from settings and start a conversation — everything runs on your machine.</p>
                <div class="chat-welcome-tips">
                    <div class="chat-tip-card" onclick="document.getElementById('chatMessageInput').value='Explain quantum computing in simple terms'; document.getElementById('chatMessageInput').focus();">
                        <div class="chat-tip-card-icon">💡</div>
                        <div class="chat-tip-card-text">Explain quantum computing in simple terms</div>
                    </div>
                    <div class="chat-tip-card" onclick="document.getElementById('chatMessageInput').value='Write a Python function to sort a list'; document.getElementById('chatMessageInput').focus();">
                        <div class="chat-tip-card-icon">🐍</div>
                        <div class="chat-tip-card-text">Write a Python function to sort a list</div>
                    </div>
                    <div class="chat-tip-card" onclick="document.getElementById('chatMessageInput').value='What are the best practices for REST API design?'; document.getElementById('chatMessageInput').focus();">
                        <div class="chat-tip-card-icon">🌐</div>
                        <div class="chat-tip-card-text">Best practices for REST API design</div>
                    </div>
                </div>
            </div>
        `;
    }

    function chatScrollToBottom(force = false) {
        const el = chatDom.messagesContainer;
        if (!el) return;
        const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
        if (force || dist < 150) {
            chatUserScrolledUp = false;
            requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
        }
    }

    function chatAutoResize() {
        const ta = chatDom.messageInput;
        if (!ta) return;
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
    }

    // ── Chat Conversations ────────────────────────────────────────────

    async function chatLoadConversations() {
        try {
            const res = await fetch('/api/chat/conversations');
            chatState.conversations = await res.json();
            chatRenderConversationList();
        } catch (err) {
            console.error('[Hive Chat] Failed to load conversations:', err);
        }
    }

    function chatRenderConversationList(filter = '') {
        const list = chatDom.conversationList;
        if (!list) return;

        if (chatState.conversations.length === 0) {
            list.innerHTML = '<div class="chat-empty-convs">No conversations yet</div>';
            return;
        }

        const filtered = filter
            ? chatState.conversations.filter(c => c.title.toLowerCase().includes(filter.toLowerCase()))
            : chatState.conversations;

        if (filtered.length === 0) {
            list.innerHTML = `<div class="chat-empty-convs">${filter ? 'No matches found' : 'No conversations yet'}</div>`;
            return;
        }

        list.innerHTML = filtered.map(conv => `
            <div class="chat-conv-item ${conv.id === chatState.currentConversationId ? 'active' : ''}"
                 data-id="${conv.id}">
                <span class="chat-conv-title">${MarkdownRenderer.escapeHtml(conv.title)}</span>
                <button class="chat-conv-delete" data-delete-id="${conv.id}" title="Delete">✕</button>
            </div>
        `).join('');

        // Event delegation for clicks
        list.querySelectorAll('.chat-conv-item').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.closest('.chat-conv-delete')) return;
                chatSelectConversation(item.dataset.id);
            });
        });
        list.querySelectorAll('.chat-conv-delete').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                chatDeleteConversation(btn.dataset.deleteId);
            });
        });
    }

    async function chatSelectConversation(convId) {
        chatState.currentConversationId = convId;
        chatRenderConversationList();
        const conv = chatState.conversations.find(c => c.id === convId);
        if (conv) chatDom.chatTitle.textContent = conv.title;
        try {
            const res = await fetch(`/api/chat/conversations/${convId}`);
            const data = await res.json();
            chatRenderMessages(data.messages);
        } catch (err) {
            console.error('[Hive Chat] Failed to load conversation:', err);
        }
    }

    function chatNewChat() {
        chatState.currentConversationId = null;
        chatDom.chatTitle.textContent = 'New Chat';
        chatRenderConversationList();
        chatShowWelcome();
        chatDom.messageInput.focus();
    }

    function chatHandleConversationCreated(conv) {
        chatState.currentConversationId = conv.id;
        chatDom.chatTitle.textContent = conv.title;
    }

    async function chatDeleteConversation(convId) {
        try {
            await fetch(`/api/chat/conversations/${convId}`, { method: 'DELETE' });
            if (chatState.currentConversationId === convId) chatNewChat();
            await chatLoadConversations();
        } catch (err) {
            chatShowToast('Failed to delete conversation', 'error');
        }
    }

    // ── Chat Model Management ─────────────────────────────────────────

    async function chatCheckModelStatus() {
        try {
            const res = await fetch('/api/chat/model/status');
            const info = await res.json();
            chatState.isModelLoaded = info.loaded;
            chatState.modelInfo = info;
            chatUpdateModelUI(info);
        } catch (err) {
            console.error('[Hive Chat] Model status check failed:', err);
        }
    }

    function chatUpdateModelUI(info) {
        if (info.loaded) {
            chatDom.modelDot.classList.add('loaded');
            chatDom.modelText.textContent = info.filename || 'Model loaded';
            chatDom.modelInfoCard.innerHTML = `
                <div class="chat-model-info-row"><span class="label">Model</span><span class="value">${MarkdownRenderer.escapeHtml(info.filename)}</span></div>
                <div class="chat-model-info-row"><span class="label">Size</span><span class="value">${info.size_mb} MB</span></div>
                <div class="chat-model-info-row"><span class="label">Context</span><span class="value">${info.n_ctx} tokens</span></div>
                <div class="chat-model-info-row"><span class="label">Threads</span><span class="value">${info.n_threads || '—'}</span></div>
                <div class="chat-model-info-row"><span class="label">Batch</span><span class="value">${info.n_batch || '—'}</span></div>
                <div class="chat-model-info-row"><span class="label">Load time</span><span class="value">${info.load_time_sec}s</span></div>
            `;
            chatDom.modelInfoCard.style.display = 'block';
            chatDom.unloadBtn.style.display = 'block';
            chatDom.messageInput.disabled = false;
            chatDom.messageInput.placeholder = 'Send a message…';
            chatDom.sendBtn.disabled = false;
        } else {
            chatDom.modelDot.classList.remove('loaded');
            chatDom.modelText.textContent = 'No model loaded';
            chatDom.modelInfoCard.style.display = 'none';
            chatDom.unloadBtn.style.display = 'none';
            chatDom.messageInput.disabled = true;
            chatDom.messageInput.placeholder = 'Load a model in settings to chat...';
            chatDom.sendBtn.disabled = true;
        }
    }

    async function chatRefreshModelList() {
        try {
            const res = await fetch('/api/chat/model/list');
            const models = await res.json();
            const select = chatDom.modelSelect;
            if (!select) return;
            // Keep current selection if possible
            const currentVal = select.value;
            select.innerHTML = '<option value="">Select a local model...</option>';
            models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.path;
                opt.textContent = `${m.filename} (${m.size_mb} MB)`;
                select.appendChild(opt);
            });
            if (currentVal) select.value = currentVal;
        } catch (err) {
            console.error('[Hive Chat] Failed to list models:', err);
        }
    }

    async function chatLoadModel() {
        const path = chatDom.modelSelect.value;
        if (!path) {
            chatShowToast('Select a model first', 'error');
            return;
        }

        chatDom.loadingOverlay.classList.add('visible');

        try {
            const res = await fetch('/api/chat/model/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path }),
            });
            const data = await res.json();
            if (res.ok) {
                chatShowToast(`Model loaded: ${data.model.filename}`, 'success');
                chatState.isModelLoaded = true;
                chatState.modelInfo = data.model;
                chatUpdateModelUI(data.model);
            } else {
                chatShowToast(data.detail || 'Failed to load model', 'error');
            }
        } catch (err) {
            chatShowToast('Failed to load model', 'error');
        } finally {
            chatDom.loadingOverlay.classList.remove('visible');
        }
    }

    async function chatUnloadModel() {
        try {
            await fetch('/api/chat/model/unload', { method: 'POST' });
            chatState.isModelLoaded = false;
            chatState.modelInfo = {};
            chatUpdateModelUI({ loaded: false });
            chatShowToast('Model unloaded', 'info');
        } catch (err) {
            chatShowToast('Failed to unload model', 'error');
        }
    }

    async function chatBrowseFile() {
        try {
            const res = await fetch('/api/chat/model/pick');
            const data = await res.json();
            if (data.selected && data.path) {
                // Add as custom option and select it
                const opt = document.createElement('option');
                opt.value = data.path;
                opt.textContent = data.path.split(/[/\\]/).pop();
                chatDom.modelSelect.appendChild(opt);
                chatDom.modelSelect.value = data.path;
                chatShowToast('File selected', 'success');
            }
        } catch (err) {
            chatShowToast('File picker failed', 'error');
        }
    }

    // ── Chat Toast ────────────────────────────────────────────────────

    let chatToastTimeout = null;
    function chatShowToast(message, type = 'info') {
        const t = chatDom.toast;
        if (!t) return;
        clearTimeout(chatToastTimeout);
        t.textContent = message;
        t.className = 'chat-toast visible ' + type;
        chatToastTimeout = setTimeout(() => {
            t.classList.remove('visible');
        }, 3000);
    }

});
