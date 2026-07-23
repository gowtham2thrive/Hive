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
    const viewDiscover = document.getElementById("view-discover");
    const viewDownloads = document.getElementById("view-downloads");
    const downloadBadge = document.getElementById("download-badge");



    // --- View Switching (Bug 14: fixed transition/display conflict) ---
    function switchView(view) {
        if (view === 'discover') {
            navDiscover.classList.add("active");
            navDownloads.classList.remove("active");
            viewDiscover.classList.remove("hidden");
            // Use rAF to ensure display change applies before opacity transition
            requestAnimationFrame(() => {
                viewDiscover.classList.add("active");
            });
            viewDownloads.classList.remove("active");
            const handleDiscoverTransition = (e) => {
                if (e.propertyName === "opacity" && !viewDownloads.classList.contains("active")) {
                    viewDownloads.classList.add("hidden");
                    viewDownloads.removeEventListener("transitionend", handleDiscoverTransition);
                }
            };
            viewDownloads.addEventListener("transitionend", handleDiscoverTransition);
        } else {
            navDownloads.classList.add("active");
            navDiscover.classList.remove("active");
            viewDownloads.classList.remove("hidden");
            // small delay to allow display:block before opacity transition
            requestAnimationFrame(() => {
                viewDownloads.classList.add("active");
            });
            viewDiscover.classList.remove("active");
            const handleDownloadsTransition = (e) => {
                if (e.propertyName === "opacity" && !viewDiscover.classList.contains("active")) {
                    viewDiscover.classList.add("hidden");
                    viewDiscover.removeEventListener("transitionend", handleDownloadsTransition);
                }
            };
            viewDiscover.addEventListener("transitionend", handleDownloadsTransition);
        }
    }

    navDiscover.addEventListener("click", () => switchView('discover'));
    navDownloads.addEventListener("click", () => switchView('downloads'));

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
});
