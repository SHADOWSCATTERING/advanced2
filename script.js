// --- Auth & CSV Upload Logic ---
let currentUserEmail = null;

// Override global fetch to append X-User-Email and ensure cookies are sent
const originalFetch = window.fetch;
window.fetch = async function() {
    let [resource, config] = arguments;
    if (!config) config = {};
    
    // Ensure HTTP-only cookies are sent with requests
    config.credentials = 'include';
    
    // We no longer manually send X-User-Email because backend relies on the secure session cookie.
    
    const response = await originalFetch(resource, config);
    
    // Globally intercept 401 to handle expired sessions gracefully
    if (response.status === 401 && resource !== '/api/auth/login' && resource !== '/api/auth/session') {
        currentUserEmail = null;
        const authSection = document.getElementById('auth-section');
        const authModal = document.getElementById('auth-modal');
        if (authSection) authSection.classList.remove('hidden');
        if (authModal) {
            authModal.classList.remove('hidden');
            const authErrorMsg = document.getElementById('auth-error-msg');
            if (authErrorMsg) {
                authErrorMsg.textContent = "Session expired. Please log in again.";
                authErrorMsg.classList.remove('hidden');
            }
        }
        if (typeof updateAuthUI === 'function') updateAuthUI();
    }
    
    return response;
};

document.addEventListener('DOMContentLoaded', async () => {
    // Check session on load
    try {
        const res = await window.fetch('/api/auth/session');
        if (res.ok) {
            const data = await res.json();
            currentUserEmail = data.email;
            // Delay UI update slightly so elements are guaranteed to be initialized
            setTimeout(() => {
                if (typeof updateAuthUI === 'function') updateAuthUI();
                if (typeof loadEmployees === 'function') loadEmployees();
            }, 0);
        }
    } catch (e) {
        console.error("Session check failed", e);
    }

    // Auth DOM Elements
    const authSection = document.getElementById('auth-section');
    const uploadSection = document.getElementById('upload-section');
    const loggedOutUploadCta = document.getElementById('logged-out-upload-cta');
    const authModal = document.getElementById('auth-modal');
    const modalLoggedInState = document.getElementById('modal-logged-in-state');
    const modalLoggedInEmail = document.getElementById('modal-logged-in-email');
    const btnAccountIcon = document.getElementById('btn-account-icon');
    const btnOpenLoginSidebar = document.getElementById('btn-open-login-sidebar');
    const btnCloseModal = document.getElementById('btn-close-modal');
    const loginForm = document.getElementById('login-form');
    const loginEmail = document.getElementById('login-email');
    const btnLogout = document.getElementById('btn-logout');
    const uploadForm = document.getElementById('upload-form');
    const csvFile = document.getElementById('csv-file');
    const employeeListTitle = document.getElementById('employee-list-title');

    function openModal() {
        if (authModal) authModal.classList.remove('hidden');
    }

    function closeModal() {
        if (authModal) authModal.classList.add('hidden');
    }
    
    if (btnAccountIcon) btnAccountIcon.addEventListener('click', (e) => { e.preventDefault(); openModal(); });
    if (btnOpenLoginSidebar) btnOpenLoginSidebar.addEventListener('click', openModal);
    if (btnCloseModal) btnCloseModal.addEventListener('click', closeModal);
    if (authModal) authModal.addEventListener('click', (e) => {
        if (e.target === authModal) closeModal();
    });
    
    function updateAuthUI() {
        if (currentUserEmail) {
            if (authSection) authSection.classList.add('hidden');
            if (modalLoggedInState) modalLoggedInState.classList.remove('hidden');
            if (uploadSection) uploadSection.classList.remove('hidden');
            if (loggedOutUploadCta) loggedOutUploadCta.classList.add('hidden');
            if (modalLoggedInEmail) modalLoggedInEmail.textContent = currentUserEmail;
            if (employeeListTitle) employeeListTitle.textContent = "Employees";
        } else {
            if (authSection) authSection.classList.remove('hidden');
            if (modalLoggedInState) modalLoggedInState.classList.add('hidden');
            if (uploadSection) uploadSection.classList.add('hidden');
            if (loggedOutUploadCta) loggedOutUploadCta.classList.remove('hidden');
            if (modalLoggedInEmail) modalLoggedInEmail.textContent = '';
            if (employeeListTitle) employeeListTitle.textContent = "Test Cases";
        }
    }
    
    const authErrorMsg = document.getElementById('auth-error-msg');
    const authSuccessMsg = document.getElementById('auth-success-msg');
    const authForms = ['login-form', 'register-form', 'forgot-form-1', 'forgot-form-2', 'forgot-form-3'];
    
    function showAuthForm(formId) {
        authForms.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.classList.toggle('hidden', id !== formId);
        });
        if (authErrorMsg) authErrorMsg.classList.add('hidden');
        if (authSuccessMsg) authSuccessMsg.classList.add('hidden');
        
        const tabLogin = document.getElementById('btn-tab-login');
        if (tabLogin) tabLogin.classList.toggle('active', formId === 'login-form');
        const tabRegister = document.getElementById('btn-tab-register');
        if (tabRegister) tabRegister.classList.toggle('active', formId === 'register-form');
        
        const tabsContainer = document.getElementById('auth-tabs-container');
        if (tabsContainer) {
            tabsContainer.style.display = formId.startsWith('forgot-') ? 'none' : 'flex';
        }
    }

    function showAuthMsg(msg, isError) {
        if (isError) {
            if (authErrorMsg) { authErrorMsg.textContent = msg; authErrorMsg.classList.remove('hidden'); }
            if (authSuccessMsg) authSuccessMsg.classList.add('hidden');
        } else {
            if (authSuccessMsg) { authSuccessMsg.textContent = msg; authSuccessMsg.classList.remove('hidden'); }
            if (authErrorMsg) authErrorMsg.classList.add('hidden');
        }
    }

    document.getElementById('btn-tab-login')?.addEventListener('click', () => showAuthForm('login-form'));
    document.getElementById('btn-tab-register')?.addEventListener('click', () => showAuthForm('register-form'));
    document.getElementById('link-forgot-password')?.addEventListener('click', (e) => { e.preventDefault(); showAuthForm('forgot-form-1'); });
    document.getElementById('link-back-login-1')?.addEventListener('click', (e) => { e.preventDefault(); showAuthForm('login-form'); });
    document.getElementById('link-back-login-2')?.addEventListener('click', (e) => { e.preventDefault(); showAuthForm('login-form'); });
    document.getElementById('link-back-login-3')?.addEventListener('click', (e) => { e.preventDefault(); showAuthForm('login-form'); });

    // Handle Google Sign-in URL parameters
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('google_login') === 'error') {
        const reason = urlParams.get('reason') || 'Unknown error';
        openModal();
        showAuthForm('login-form');
        showAuthMsg(`Google Sign-In failed: ${reason}`, true);
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (urlParams.get('google_login') === 'success') {
        window.history.replaceState({}, document.title, window.location.pathname);
    }

    // Google Login Button
    document.getElementById('btn-google-login')?.addEventListener('click', () => {
        window.location.href = '/api/auth/google/login';
    });


    // API Handlers
    document.getElementById('login-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('login-email').value.trim();
        const password = document.getElementById('login-password').value;
        try {
            const res = await window.fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error);
            
            currentUserEmail = data.email;
            updateAuthUI();
            closeModal();
            if (typeof loadEmployees === 'function') loadEmployees();
            refreshSheetStatus();
        } catch (err) {
            showAuthMsg(err.message, true);
        }
    });

    document.getElementById('register-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            email: document.getElementById('register-email').value.trim(),
            first_name: document.getElementById('register-fname').value.trim(),
            last_name: document.getElementById('register-lname').value.trim(),
            password: document.getElementById('register-password').value,
            security_question: document.getElementById('register-sq').value,
            security_answer: document.getElementById('register-sa').value.trim()
        };
        try {
            const res = await window.fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error);
            
            showAuthForm('login-form');
            document.getElementById('login-email').value = data.email;
            showAuthMsg("Registration successful! Please log in.", false);
        } catch (err) {
            showAuthMsg(err.message, true);
        }
    });

    let resetEmail = '';
    document.getElementById('forgot-form-1')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        resetEmail = document.getElementById('forgot-email').value.trim();
        try {
            const res = await window.fetch('/api/auth/forgot-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: resetEmail })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error);
            
            document.getElementById('forgot-sq-display').textContent = data.security_question;
            showAuthForm('forgot-form-2');
        } catch (err) {
            showAuthMsg(err.message, true);
        }
    });

    let resetToken = '';
    
    document.getElementById('forgot-form-2')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            email: resetEmail,
            security_answer: document.getElementById('forgot-sa').value.trim()
        };
        try {
            const res = await window.fetch('/api/auth/verify-security-answer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error);
            
            resetToken = data.reset_token;
            showAuthForm('forgot-form-3');
        } catch (err) {
            showAuthMsg(err.message, true);
        }
    });

    document.getElementById('forgot-form-3')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            email: resetEmail,
            reset_token: resetToken,
            new_password: document.getElementById('forgot-new-password').value
        };
        try {
            const res = await window.fetch('/api/auth/reset-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error);
            
            showAuthForm('login-form');
            showAuthMsg("Password reset successfully! Please log in.", false);
        } catch (err) {
            showAuthMsg(err.message, true);
        }
    });
    
    if (btnLogout) {
        btnLogout.addEventListener('click', async () => {
            try {
                await window.fetch('/api/auth/logout', { method: 'POST' });
            } catch (e) {
                console.error("Logout failed", e);
            }
            currentUserEmail = null;
            updateAuthUI();
            closeModal();
            stopSheetPolling();
            showLinkedSheetUI(null);
            if (typeof loadEmployees === 'function') loadEmployees();
        });
    }
    
    if (uploadForm) {
        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = csvFile.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            
            const csvStatus = document.getElementById('csv-status');
            if (csvStatus) {
                csvStatus.textContent = "Uploading...";
                csvStatus.style.color = "var(--text-secondary)";
            }
            
            try {
                const res = await window.fetch(`/api/upload-csv`, {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || "Upload failed");
                
                if (csvStatus) {
                    csvStatus.textContent = "Success! " + data.message;
                    csvStatus.style.color = "var(--accent-green)";
                }
                csvFile.value = '';
                
                if (typeof loadEmployees === 'function') loadEmployees();
            } catch (err) {
                if (csvStatus) {
                    csvStatus.textContent = "Error: " + err.message;
                    csvStatus.style.color = "var(--accent-red)";
                }
            }
        });
    }

    // --- Sign in with Google ---
    document.getElementById('btn-google-login')?.addEventListener('click', () => {
        window.location.href = '/api/auth/google/login';
    });

    // --- Linked Sheet Auto-Sync ---
    const linkedSheetEmpty = document.getElementById('linked-sheet-empty');
    const linkedSheetActive = document.getElementById('linked-sheet-active');
    const linkedSheetName = document.getElementById('linked-sheet-name');
    const linkedSheetLastSynced = document.getElementById('linked-sheet-last-synced');
    let sheetPollTimer = null;

    function formatSyncTime(iso) {
        if (!iso) return 'never';
        try {
            return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) {
            return iso;
        }
    }

    function showLinkedSheetUI(status) {
        if (status && status.linked) {
            linkedSheetEmpty?.classList.add('hidden');
            linkedSheetActive?.classList.remove('hidden');
            if (linkedSheetName) linkedSheetName.textContent = status.sheet_name || status.sheet_id;
            if (linkedSheetLastSynced) linkedSheetLastSynced.textContent = formatSyncTime(status.last_synced);
        } else {
            linkedSheetEmpty?.classList.remove('hidden');
            linkedSheetActive?.classList.add('hidden');
        }
    }

    function stopSheetPolling() {
        if (sheetPollTimer) {
            clearInterval(sheetPollTimer);
            sheetPollTimer = null;
        }
    }

    async function syncLinkedSheetSilently() {
        try {
            const res = await window.fetch('/api/sheets/sync', { method: 'POST' });
            if (!res.ok) return; // e.g. nothing linked yet, or a transient error - stay quiet
            const data = await res.json();
            if (linkedSheetLastSynced) linkedSheetLastSynced.textContent = formatSyncTime(data.last_synced);
            if (typeof loadEmployees === 'function') loadEmployees();
        } catch (e) {
            // Silent by design - this runs on a background timer.
        }
    }

    function startSheetPolling() {
        stopSheetPolling();
        // Refresh on load, then keep polling every 2 minutes while the tab is open.
        syncLinkedSheetSilently();
        sheetPollTimer = setInterval(syncLinkedSheetSilently, 2 * 60 * 1000);
    }

    async function refreshSheetStatus() {
        if (!currentUserEmail) { showLinkedSheetUI(null); stopSheetPolling(); return; }
        try {
            const res = await window.fetch('/api/sheets/status');
            const data = await res.json();
            showLinkedSheetUI(data);
            if (data.linked) startSheetPolling(); else stopSheetPolling();
        } catch (e) {
            console.error('Could not load sheet status', e);
        }
    }

    document.getElementById('link-sheet-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const url = document.getElementById('link-sheet-url').value.trim();
        if (!url) return;
        const sheetStatus = document.getElementById('sheet-status');
        if (sheetStatus) { sheetStatus.textContent = 'Connecting sheet...'; sheetStatus.style.color = 'var(--text-secondary)'; }
        try {
            const res = await window.fetch('/api/sheets/link', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sheet_id: url })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to link sheet');
            const sheetStatus = document.getElementById('sheet-status');
            if (sheetStatus) { sheetStatus.textContent = 'Connected! ' + (data.message || ''); sheetStatus.style.color = 'var(--accent-green)'; }
            document.getElementById('link-sheet-url').value = '';
            showLinkedSheetUI({ linked: true, sheet_id: data.linked_sheet_id, sheet_name: data.linked_sheet_name, last_synced: data.last_synced });
            startSheetPolling();
            if (typeof loadEmployees === 'function') loadEmployees();
        } catch (err) {
            const sheetStatus = document.getElementById('sheet-status');
            if (sheetStatus) { sheetStatus.textContent = 'Error: ' + err.message; sheetStatus.style.color = 'var(--accent-red)'; }
        }
    });

    document.getElementById('btn-sync-now')?.addEventListener('click', async () => {
        const sheetStatus = document.getElementById('sheet-status');
        if (sheetStatus) { sheetStatus.textContent = 'Syncing...'; sheetStatus.style.color = 'var(--text-secondary)'; }
        try {
            const res = await window.fetch('/api/sheets/sync', { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Sync failed');
            const sheetStatus = document.getElementById('sheet-status');
            if (sheetStatus) { sheetStatus.textContent = 'Synced! ' + (data.message || ''); sheetStatus.style.color = 'var(--accent-green)'; }
            if (linkedSheetLastSynced) linkedSheetLastSynced.textContent = formatSyncTime(data.last_synced);
            if (typeof loadEmployees === 'function') loadEmployees();
        } catch (err) {
            const sheetStatus = document.getElementById('sheet-status');
            if (sheetStatus) { sheetStatus.textContent = 'Error: ' + err.message; sheetStatus.style.color = 'var(--accent-red)'; }
        }
    });

    document.getElementById('btn-unlink-sheet')?.addEventListener('click', async () => {
        try {
            await window.fetch('/api/sheets/unlink', { method: 'POST' });
            showLinkedSheetUI(null);
            stopSheetPolling();
        } catch (err) {
            console.error('Failed to unlink sheet', err);
        }
    });

    document.getElementById('sheet-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const sheetUrl = document.getElementById('sheet-url').value.trim();
        if (!sheetUrl) return;
        
        const sheetStatus = document.getElementById('sheet-status');
        if (sheetStatus) {
            sheetStatus.textContent = "Importing from Google Sheets...";
            sheetStatus.style.color = "var(--text-secondary)";
        }
        
        try {
            const res = await window.fetch('/api/shifts/import-sheet', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sheet_id: sheetUrl })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Import failed");
            
            if (sheetStatus) {
                sheetStatus.textContent = "Success! " + data.message;
                sheetStatus.style.color = "var(--accent-green)";
            }
            document.getElementById('sheet-url').value = '';
            
            if (typeof loadEmployees === 'function') loadEmployees();
        } catch (err) {
            if (sheetStatus) {
                sheetStatus.textContent = "Error: " + err.message;
                sheetStatus.style.color = "var(--accent-red)";
            }
        }
    });
    
    // Handle the redirect back from /api/auth/google/callback
    function handleGoogleLoginRedirect() {
        const params = new URLSearchParams(window.location.search);
        const status = params.get('google_login');
        if (!status) return;

        if (status === 'success') {
            showAuthMsg('Signed in with Google!', false);
        } else if (status === 'error') {
            openModal();
            showAuthMsg('Google sign-in failed: ' + (params.get('reason') || 'unknown error'), true);
        }
        // Clean the query params out of the URL without reloading the page.
        params.delete('google_login');
        params.delete('reason');
        const cleanUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '') + window.location.hash;
        window.history.replaceState({}, document.title, cleanUrl);
    }

    // Initialize Auth UI & Verify Session
    async function initSession() {
        handleGoogleLoginRedirect();
        try {
            const res = await window.fetch('/api/auth/session');
            if (res.ok) {
                const data = await res.json();
                if (data.valid) {
                    currentUserEmail = data.email;
                    localStorage.setItem('fatiguex_user_email', currentUserEmail);
                } else {
                    currentUserEmail = null;
                    localStorage.removeItem('fatiguex_user_email');
                }
            } else {
                currentUserEmail = null;
                localStorage.removeItem('fatiguex_user_email');
            }
        } catch (e) {
            console.error("Session check failed", e);
        }
        updateAuthUI();
        refreshSheetStatus();
    }
    
    initSession();

    // --- GSAP Animations (Phase 2) ---
    if (typeof gsap !== 'undefined') {
        gsap.registerPlugin(ScrollTrigger);

        // 1. Hero Section Staggered Fade-Up
        const heroElements = document.querySelectorAll('.hero .fade-in-up');
        if (heroElements.length > 0) {
            gsap.to(heroElements, {
                y: 0,
                opacity: 1,
                duration: 1,
                stagger: 0.2,
                ease: 'power3.out',
                delay: 0.1
            });
        }

        // 2. Heartbeat trace animation
        const heartbeat = document.querySelector('.heartbeat-line path');
        if (heartbeat) {
            const length = heartbeat.getTotalLength();
            gsap.set(heartbeat, { strokeDasharray: length, strokeDashoffset: length });
            gsap.to(heartbeat, {
                strokeDashoffset: 0,
                duration: 2.5,
                ease: 'power2.inOut',
                repeat: -1,
                repeatDelay: 0.5
            });
        }

        // 3. Nav Indicator Morphing
        const indicator = document.querySelector('.nav-indicator');
        const navItems = document.querySelectorAll('.nav-links a');
        
        if (indicator && navItems.length > 0) {
            gsap.set(indicator, { opacity: 1 });
            
            function moveIndicator(el) {
                if (!el) return;
                const rect = el.getBoundingClientRect();
                const parentRect = el.closest('.nav-links').getBoundingClientRect();
                gsap.to(indicator, {
                    x: rect.left - parentRect.left,
                    width: rect.width,
                    duration: 0.4,
                    ease: 'power3.out'
                });
            }
            
            // Wait for layout to settle before initial placement
            let activeNav = document.querySelector('.nav-links a.nav-highlight') || document.querySelector('.nav-links a[href="#dashboard"]') || navItems[0];
            setTimeout(() => moveIndicator(activeNav), 200);
            
            navItems.forEach(item => {
                item.addEventListener('mouseenter', (e) => moveIndicator(e.target));
                item.addEventListener('mouseleave', () => moveIndicator(activeNav));
                item.addEventListener('click', (e) => {
                    activeNav = e.target;
                    moveIndicator(activeNav);
                });
            });
        }
    }
    // --- End GSAP Animations ---

    // Mobile Navigation Toggle
    const mobileToggle = document.querySelector('.mobile-toggle');
    const navLinks = document.querySelector('.nav-links');

    if (mobileToggle) {
        mobileToggle.addEventListener('click', () => {
            navLinks.classList.toggle('active');
            
            // Animate hamburger to X
            const spans = mobileToggle.querySelectorAll('span');
            if (navLinks.classList.contains('active')) {
                spans[0].style.transform = 'rotate(45deg) translate(5px, 5px)';
                spans[1].style.opacity = '0';
                spans[2].style.transform = 'rotate(-45deg) translate(5px, -5px)';
            } else {
                spans[0].style.transform = 'none';
                spans[1].style.opacity = '1';
                spans[2].style.transform = 'none';
            }
        });
    }

    // Smooth Scrolling for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            
            // Close mobile menu if open
            if (navLinks && navLinks.classList.contains('active')) {
                mobileToggle.click();
            }

            const targetId = this.getAttribute('href');
            if (targetId === '#') return;
            
            const targetElement = document.querySelector(targetId);
            if (targetElement) {
                targetElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });

    // Intersection Observer for fade-in animations on scroll
    const observerOptions = {
        root: null,
        rootMargin: '0px',
        threshold: 0.1
    };

    const observer = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
                observer.unobserve(entry.target);
            }
        });
    }, observerOptions);

    // Apply basic fade up to sections
    document.querySelectorAll('section:not(.hero) .container').forEach(section => {
        section.style.opacity = '0';
        section.style.transform = 'translateY(30px)';
        section.style.transition = 'opacity 0.8s ease-out, transform 0.8s ease-out';
        observer.observe(section);
    });

    // Interactive Risk Assessment Card Simulation
    const riskCard = document.getElementById('dynamic-risk-card');
    const statusText = riskCard.querySelector('.status-indicator');
    
    let isDanger = false;
    
    riskCard.addEventListener('mouseenter', () => {
        if (!isDanger) {
            riskCard.classList.add('danger');
            statusText.textContent = 'Violations Detected!';
            statusText.style.color = 'var(--accent-red)';
            isDanger = true;
        } else {
            riskCard.classList.remove('danger');
            statusText.textContent = 'Shift Compliant';
            statusText.style.color = 'var(--accent-green)';
            isDanger = false;
        }
    });

    // ---------------------------------------------------------
    // Interactive Dashboard API Integration
    // ---------------------------------------------------------

    // 1. Detect API endpoint base
    const API_BASE = window.location.protocol === 'file:' 
        ? 'http://localhost:5000' 
        : '';

    // 2. Global State
    let employeesData = [];
    let selectedEmpId = null;

    // 3. Select DOM Elements
    const dbStatusDot = document.getElementById('db-status-dot');
    const dbStatusText = document.getElementById('db-status-text');
    const aiStatusDot = document.getElementById('ai-status-dot');
    const aiStatusText = document.getElementById('ai-status-text');
    const btnGenerateSchedule = document.getElementById('btn-generate-schedule');

    const generateModal = document.getElementById('generate-modal');
    const btnCloseGenerateModal = document.getElementById('btn-close-generate-modal');
    const btnCancelGenerate = document.getElementById('btn-cancel-generate');
    const btnRunGenerate = document.getElementById('btn-run-generate');
    const generateLoading = document.getElementById('generate-loading');
    const generateResults = document.getElementById('generate-results');
    const generateAssignmentsBody = document.getElementById('generate-assignments-body');
    const generateUnfilledBody = document.getElementById('generate-unfilled-body');

    const btnViewList = document.getElementById('btn-view-list');
    const btnViewHeatmap = document.getElementById('btn-view-heatmap');
    const dashboardListPanel = document.getElementById('dashboard-list-panel');
    const dashboardHeatmapPanel = document.getElementById('dashboard-heatmap-panel');
    const heatmapHead = document.getElementById('heatmap-head');
    const heatmapBody = document.getElementById('heatmap-body');
    const heatmapLoading = document.getElementById('heatmap-loading');

    const employeeSearchInput = document.getElementById('employee-search');
    const employeeListContainer = document.getElementById('employee-list-container');

    const panelEmptyState = document.getElementById('panel-empty-state');
    const panelDetailsContent = document.getElementById('panel-details-content');

    const detailEmpName = document.getElementById('detail-emp-name');
    const detailEmpMeta = document.getElementById('detail-emp-meta');
    const detailEmpRiskBadge = document.getElementById('detail-emp-risk-badge');
    const detailEmpScore = document.getElementById('detail-emp-score');
    const detailEmpScoreBar = document.getElementById('detail-emp-score-bar');

    const detailEmpContractHours = document.getElementById('detail-emp-contract-hours');
    const detailEmpMaxHours = document.getElementById('detail-emp-max-hours');
    const detailEmpRest = document.getElementById('detail-emp-rest');

    const fatigueForm = document.getElementById('fatigue-form');
    const fatigueDate = document.getElementById('fatigue-date');
    const fatigueRating = document.getElementById('fatigue-rating');
    const fatigueNotes = document.getElementById('fatigue-notes');
    const btnLogFatigue = document.getElementById('btn-log-fatigue');

    const violationsContainer = document.getElementById('violations-container');
    const detailViolationsList = document.getElementById('detail-violations-list');

    const detailAiSource = document.getElementById('detail-ai-source');
    const detailAiExplanation = document.getElementById('detail-ai-explanation');
    const detailAiUrgent = document.getElementById('detail-ai-urgent');
    const detailAiRecommendation = document.getElementById('detail-ai-recommendation');
    const groupAiUrgent = document.getElementById('group-ai-urgent');
    const groupAiRec = document.getElementById('group-ai-rec');

    const aiChatHistoryEl = document.getElementById('ai-chat-history');
    const aiChatInput = document.getElementById('ai-chat-input');
    const btnAiChatSend = document.getElementById('btn-ai-chat-send');
    let aiChatHistory = [];

    const detailRosterBody = document.getElementById('detail-roster-body');

    const shiftAssignmentForm = document.getElementById('shift-assignment-form');
    const btnValidateShift = document.getElementById('btn-validate-shift');
    const btnAssignShift = document.getElementById('btn-assign-shift');

    const restDayForm = document.getElementById('rest-day-form');

    const valResultsBox = document.getElementById('validation-results-box');
    const valRiskBadge = document.getElementById('val-risk-badge');
    const valSummaryText = document.getElementById('val-summary-text');
    const valViolationsBox = document.getElementById('val-violations-box');
    const valViolationsList = document.getElementById('val-violations-list');
    const valAiSource = document.getElementById('val-ai-source');
    const valAiExplanation = document.getElementById('val-ai-explanation');
    const valAiAlternativesGroup = document.getElementById('val-ai-alternatives-group');
    const valAiAlternatives = document.getElementById('val-ai-alternatives');

    // Tab buttons handling
    const tabButtons = document.querySelectorAll('.tab-btn');
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            tabButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            const targetTab = btn.getAttribute('data-tab');
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.add('hidden');
            });
            document.getElementById(targetTab).classList.remove('hidden');
        });
    });

    // 4. API Core Functions
    async function fetchWithTimeout(resource, options = {}) {
        const { timeout = 8000 } = options;
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeout);
        const response = await fetch(resource, {
            ...options,
            signal: controller.signal  
        });
        clearTimeout(id);
        return response;
    }

    async function checkHealth() {
        try {
            const res = await fetchWithTimeout(`${API_BASE}/api/health`, { timeout: 8000 });
            if (!res.ok) throw new Error("API unhealthy");
            const data = await res.json();
            
            // Render Database status
            if (data.database_initialized) {
                dbStatusDot.className = 'status-dot active';
                dbStatusText.textContent = 'Initialized (Seeded)';
            } else {
                dbStatusDot.className = 'status-dot inactive';
                dbStatusText.textContent = 'Missing Database';
            }

            // Render AI status
            if (data.ai_configured) {
                aiStatusDot.className = 'status-dot active';
                aiStatusText.textContent = 'Gemini AI Connected';
            } else {
                aiStatusDot.className = 'status-dot warning';
                aiStatusText.textContent = 'Template Fallback (No Key)';
            }
            return data.database_initialized ? 'initialized' : 'missing_db';
        } catch (err) {
            console.error(err);
            dbStatusDot.className = 'status-dot warning';
            dbStatusText.textContent = 'Offline (Fallback Mode)';
            aiStatusDot.className = 'status-dot warning';
            aiStatusText.textContent = 'Offline';
            return 'offline';
        }
    }

    async function loadEmployees(forceDemo = false) {
        if (forceDemo) {
            return loadDemoData();
        }
        
        try {
            const res = await fetchWithTimeout(`${API_BASE}/api/employees`, { timeout: 8000 });
            if (!res.ok) throw new Error("Could not load employees");
            employeesData = await res.json();
            
            // Get dashboard stats if database is initialized
            const dashboardRes = await fetchWithTimeout(`${API_BASE}/api/dashboard/risk-summary`, { timeout: 8000 });
            let riskSummary = {};
            if (dashboardRes.ok) {
                riskSummary = await dashboardRes.json();
            }

            renderEmployeeList(riskSummary.employee_risks || []);
        } catch (err) {
            console.error("Error loading employees:", err);
            loadDemoData();
        }
    }

    function loadDemoData() {
        console.log("Loading demo data as fallback...");
        employeesData = [
            { employee_id: "E001", name: "Jane Smith (Demo)", role: "Senior Nurse", department: "Emergency", contracted_hours: 40, max_weekly_hours: 48, min_rest_hours_required: 11 },
            { employee_id: "E004", name: "Sarah Connor (Demo)", role: "Charge Nurse", department: "Emergency", contracted_hours: 40, max_weekly_hours: 48, min_rest_hours_required: 11 },
            { employee_id: "E003", name: "Alice Johnson (Demo)", role: "Paramedic", department: "Emergency", contracted_hours: 36, max_weekly_hours: 48, min_rest_hours_required: 11 },
            { employee_id: "E002", name: "John Doe (Demo)", role: "Security Officer", department: "Security", contracted_hours: 40, max_weekly_hours: 48, min_rest_hours_required: 11 }
        ];
        
        const demoRiskSummary = {
            employee_risks: [
                { employee_id: "E001", risk_level: "Critical", fatigue_score: 85 },
                { employee_id: "E004", risk_level: "High", fatigue_score: 70 },
                { employee_id: "E003", risk_level: "Moderate", fatigue_score: 45 },
                { employee_id: "E002", risk_level: "Low", fatigue_score: 15 }
            ]
        };
        renderEmployeeList(demoRiskSummary.employee_risks);
    }

    function renderEmployeeList(riskList = []) {
        // Map risk details by employee_id for quick search
        const riskMap = {};
        riskList.forEach(item => {
            riskMap[item.employee_id] = item;
        });

        // Filter text
        const query = employeeSearchInput.value.toLowerCase().trim();

        const filtered = employeesData.filter(emp => {
            return emp.name.toLowerCase().includes(query) || 
                   (emp.role && emp.role.toLowerCase().includes(query)) ||
                   emp.employee_id.toLowerCase().includes(query);
        });

        if (filtered.length === 0) {
            employeeListContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 2rem 0;">No employees found.</div>`;
            return;
        }

        employeeListContainer.innerHTML = '';
        filtered.forEach(emp => {
            const item = document.createElement('div');
            item.className = `employee-item ${emp.employee_id === selectedEmpId ? 'selected' : ''}`;
            
            const riskInfo = riskMap[emp.employee_id] || { risk_level: 'Low', fatigue_score: 0 };
            const riskClass = riskInfo.risk_level.toLowerCase();

            item.innerHTML = `
                <div class="emp-info">
                    <h4>${emp.name}</h4>
                    <p>${emp.role || 'Employee'} | ${emp.department || 'Staff'}</p>
                </div>
                <span class="risk-badge ${riskClass}">${riskInfo.risk_level} (${Math.round(riskInfo.fatigue_score)})</span>
            `;

            item.addEventListener('click', () => {
                document.querySelectorAll('.employee-item').forEach(el => el.classList.remove('selected'));
                item.classList.add('selected');
                selectEmployee(emp.employee_id);
            });

            employeeListContainer.appendChild(item);
        });
    }

    async function selectEmployee(empId) {
        selectedEmpId = empId;
        
        // Show loading state
        panelEmptyState.classList.add('hidden');
        panelDetailsContent.classList.add('hidden');
        
        // Create or find a loader
        let loader = document.getElementById('details-loader');
        if (!loader) {
            loader = document.createElement('div');
            loader.id = 'details-loader';
            loader.style.textAlign = 'center';
            loader.style.padding = '5rem 0';
            loader.innerHTML = `<div class="loading-spinner">Analyzing fatigue risk profile...</div>`;
            panelDetailsContent.parentNode.appendChild(loader);
        }
        loader.classList.remove('hidden');

        try {
            // Fetch fatigue risk detail
            const riskRes = await fetch(`${API_BASE}/api/employees/${empId}/fatigue-risk`);
            if (!riskRes.ok) throw new Error("Failed to load fatigue risk");
            const riskData = await riskRes.json();

            // Fetch schedule
            const scheduleRes = await fetch(`${API_BASE}/api/employees/${empId}/schedule`);
            if (!scheduleRes.ok) throw new Error("Failed to load employee schedule");
            const scheduleData = await scheduleRes.json();

            loader.classList.add('hidden');
            panelDetailsContent.classList.remove('hidden');

            renderEmployeeDetails(riskData, scheduleData);
        } catch (err) {
            console.error(err);
            loader.classList.add('hidden');
            panelEmptyState.classList.remove('hidden');
            alert("Error loading employee fatigue data. Please try again.");
        }
    }

    function renderEmployeeDetails(riskData, scheduleData) {
        const emp = scheduleData.employee;
        const shifts = scheduleData.shifts;

        // Render header
        detailEmpName.textContent = emp.name;
        detailEmpMeta.textContent = `${emp.role || 'Employee'} | ${emp.department || 'Staff'}`;
        
        const riskLevel = riskData.risk_level;
        const riskClass = riskLevel.toLowerCase();
        detailEmpRiskBadge.textContent = `${riskLevel} Risk`;
        detailEmpRiskBadge.className = `risk-badge ${riskClass}`;

        // Render fatigue score meter
        const score = Math.round(riskData.fatigue_score);
        detailEmpScore.textContent = `${score} / 100`;
        detailEmpScoreBar.className = `score-bar ${riskClass}`;
        detailEmpScoreBar.style.width = `${score}%`;

        // Render contract stats
        detailEmpContractHours.textContent = `${emp.contracted_hours}h`;
        detailEmpMaxHours.textContent = `${emp.max_weekly_hours}h`;
        detailEmpRest.textContent = `${emp.min_rest_hours_required}h`;

        // Render violations
        const violations = riskData.violations || [];
        if (violations.length === 0) {
            violationsContainer.classList.add('hidden');
            detailViolationsList.innerHTML = '';
        } else {
            violationsContainer.classList.remove('hidden');
            detailViolationsList.innerHTML = '';
            violations.forEach(v => {
                const card = document.createElement('div');
                card.className = `violation-item ${v.severity.toLowerCase()}`;
                card.innerHTML = `
                    <div>
                        <span class="violation-lbl">${v.rule_name}</span>
                        <div class="violation-desc">${v.detail}</div>
                    </div>
                    <span class="risk-badge ${v.severity.toLowerCase()}">${v.severity}</span>
                `;
                detailViolationsList.appendChild(card);
            });
        }

        // Render AI explanation box
        const ai = riskData.ai_explanation || {};
        if (ai.source === 'ai') {
            detailAiSource.textContent = 'Google Gemini';
            detailAiSource.className = 'ai-source-badge ai';
        } else {
            detailAiSource.textContent = 'Rule Explainer (Fallback)';
            detailAiSource.className = 'ai-source-badge';
        }

        detailAiExplanation.textContent = ai.explanation || 'No explanation available.';
        
        if (ai.most_urgent_issue && ai.most_urgent_issue !== 'None detected.') {
            groupAiUrgent.classList.remove('hidden');
            detailAiUrgent.textContent = ai.most_urgent_issue;
        } else {
            groupAiUrgent.classList.add('hidden');
        }

        if (ai.recommendation) {
            groupAiRec.classList.remove('hidden');
            detailAiRecommendation.textContent = ai.recommendation;
        } else {
            groupAiRec.classList.add('hidden');
        }

        // Reset AI Chat
        aiChatHistory = [];
        if (aiChatHistoryEl) aiChatHistoryEl.innerHTML = '';
        if (aiChatInput) aiChatInput.value = '';

        // Render Roster table
        if (shifts.length === 0) {
            detailRosterBody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">No shifts scheduled in this window.</td></tr>`;
        } else {
            detailRosterBody.innerHTML = '';
            // Sort shifts chronologically
            shifts.forEach(s => {
                const isRestDay = s.shift_type === 'Rest Day';
                const badgeClass = isRestDay ? 'rest-day' : (s.shift_type.toLowerCase() === 'night' ? 'critical' : 'moderate');
                const timeDisplay = isRestDay ? 'All Day' : `${s.start_time} - ${s.end_time}`;
                const locationDisplay = isRestDay ? (s.location || '—') : `${s.location || '—'} <span style="color: var(--text-secondary); font-size: 0.8rem; display: block;">${s.department || ''}</span>`;
                
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td><strong>${s.shift_date}</strong></td>
                    <td><span class="risk-badge ${badgeClass}" style="text-transform: capitalize;">${s.shift_type}</span></td>
                    <td>${timeDisplay}</td>
                    <td style="display: flex; justify-content: space-between; align-items: center;">
                        <div>${locationDisplay}</div>
                        <button class="btn-remove-shift" data-shift-id="${s.shift_id}" title="Remove Shift">🗑</button>
                    </td>
                `;
                detailRosterBody.appendChild(row);
            });
            
            // Attach event listeners for delete buttons
            document.querySelectorAll('.btn-remove-shift').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    const shiftId = e.currentTarget.getAttribute('data-shift-id');
                    if (confirm('Are you sure you want to remove this shift?')) {
                        await deleteShift(shiftId);
                    }
                });
            });
        }

        // Reset forms and result views
        shiftAssignmentForm.reset();
        valResultsBox.classList.add('hidden');
    }

    // 5. Shift Validation & Assignment Actions
    async function validateShift(e) {
        if (!selectedEmpId) return;
        
        const dateVal = document.getElementById('shift-date').value;
        const typeVal = document.getElementById('shift-type').value;
        const startVal = document.getElementById('start-time').value;
        const endVal = document.getElementById('end-time').value;
        const locVal = document.getElementById('location').value;
        const deptVal = document.getElementById('department').value;

        if (!dateVal || !typeVal || !startVal || !endVal) {
            alert("Please fill out Shift Date, Shift Type, Start Time, and End Time.");
            return;
        }

        btnValidateShift.disabled = true;
        btnValidateShift.textContent = 'Checking...';

        try {
            const res = await fetch(`${API_BASE}/api/shifts/validate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    employee_id: selectedEmpId,
                    shift_date: dateVal,
                    shift_type: typeVal,
                    start_time: startVal,
                    end_time: endVal,
                    location: locVal,
                    department: deptVal
                })
            });

            const data = await res.json();
            btnValidateShift.disabled = false;
            btnValidateShift.textContent = 'Dry-Run Check';

            if (!res.ok) {
                alert(`Validation failed: ${data.error || 'Unknown error'}`);
                return;
            }

            // Render validation results box
            valResultsBox.classList.remove('hidden');
            
            const projectedRisk = data.projected_risk_level;
            const projectedClass = projectedRisk.toLowerCase();
            valRiskBadge.textContent = `${projectedRisk} Projected`;
            valRiskBadge.className = `risk-badge ${projectedClass}`;

            if (data.safe_to_assign) {
                valSummaryText.innerHTML = `🟢 This shift is <strong style="color: var(--accent-green);">safe to assign</strong>. It does not introduce any fatigue risk violations.`;
                valViolationsBox.classList.add('hidden');
            } else {
                valSummaryText.innerHTML = `⚠️ This shift is <strong style="color: var(--accent-red);">not recommended</strong>. It introduces new safety violations.`;
                valViolationsBox.classList.remove('hidden');
                
                valViolationsList.innerHTML = '';
                const newViolations = data.would_introduce_violations || [];
                newViolations.forEach(v => {
                    const li = document.createElement('li');
                    li.innerHTML = `<strong>${v.rule_name}</strong>: ${v.detail}`;
                    valViolationsList.appendChild(li);
                });
            }

            // AI explanation of projected risk
            const ai = data.ai_explanation || {};
            if (ai.source === 'ai') {
                valAiSource.textContent = 'Google Gemini';
                valAiSource.className = 'ai-source-badge ai';
            } else {
                valAiSource.textContent = 'Rule Explainer (Fallback)';
                valAiSource.className = 'ai-source-badge';
            }
            valAiExplanation.textContent = ai.explanation || 'No explanation available.';

            // Render Alternatives
            const alts = data.safer_alternatives || [];
            if (alts.length === 0) {
                valAiAlternativesGroup.classList.add('hidden');
            } else {
                valAiAlternativesGroup.classList.remove('hidden');
                valAiAlternatives.innerHTML = '';
                alts.forEach(alt => {
                    const card = document.createElement('div');
                    card.className = 'alt-card';
                    card.innerHTML = `
                        <div class="alt-header">
                            <span>💡 ${alt.option}</span>
                            <span class="risk-badge ${alt.projected_risk_level.toLowerCase()}" style="font-size: 0.65rem; padding: 0.1rem 0.5rem;">${alt.projected_risk_level} Risk</span>
                        </div>
                        <div class="alt-desc">
                            Shift: ${alt.shift_date} ${alt.start_time}-${alt.end_time} (${alt.shift_type}). Reason: ${alt.reason}
                        </div>
                    `;
                    valAiAlternatives.appendChild(card);
                });
            }

            // Scroll results box into view
            valResultsBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        } catch (err) {
            console.error(err);
            btnValidateShift.disabled = false;
            btnValidateShift.textContent = 'Dry-Run Check';
            alert("Connection error occurred. Could not validate shift.");
        }
    }

    async function assignShift(e) {
        e.preventDefault();
        if (!selectedEmpId) return;

        const dateVal = document.getElementById('shift-date').value;
        const typeVal = document.getElementById('shift-type').value;
        const startVal = document.getElementById('start-time').value;
        const endVal = document.getElementById('end-time').value;
        const locVal = document.getElementById('location').value;
        const deptVal = document.getElementById('department').value;

        // Generate unique shift id (random string for capstone purposes)
        const shiftId = 'S' + Math.floor(Math.random() * 1000000);

        btnAssignShift.disabled = true;
        btnAssignShift.textContent = 'Assigning...';

        try {
            const res = await fetch(`${API_BASE}/api/shifts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shift_id: shiftId,
                    employee_id: selectedEmpId,
                    shift_date: dateVal,
                    shift_type: typeVal,
                    start_time: startVal,
                    end_time: endVal,
                    location: locVal,
                    department: deptVal
                })
            });

            const data = await res.json();
            btnAssignShift.disabled = false;
            btnAssignShift.textContent = 'Assign Shift';

            if (res.status === 409) {
                // Hard conflict overlap
                const confirmForce = confirm(
                    `⚠️ Assignment Blocked: This shift overlaps with another shift for this employee.\n\n` +
                    `AI Explanation:\n"${data.ai_explanation?.explanation || data.error}"\n\n` +
                    `Do you want to override and assign anyway? (Not recommended)`
                );
                
                if (confirmForce) {
                    // Re-send with force: true
                    await forceAssignShift(shiftId, dateVal, typeVal, startVal, endVal, locVal, deptVal);
                }
                return;
            }

            if (!res.ok) {
                alert(`Failed to assign shift: ${data.error || 'Unknown error'}`);
                return;
            }

            // Success
            let successMsg = `🟢 Shift assigned successfully!`;
            if (data.fatigue_warnings && data.fatigue_warnings.length > 0) {
                successMsg += `\n⚠️ Note: This introduces Soft Fatigue warnings (Projected: ${data.projected_risk_level} Risk).`;
            }
            alert(successMsg);

            // Reload employee list & details
            const initialized = await checkHealth();
            if (initialized) {
                await loadEmployees();
                await selectEmployee(selectedEmpId);
            }

        } catch (err) {
            console.error(err);
            btnAssignShift.disabled = false;
            btnAssignShift.textContent = 'Assign Shift';
            alert("Connection error occurred. Could not assign shift.");
        }
    }

    async function forceAssignShift(shiftId, dateVal, typeVal, startVal, endVal, locVal, deptVal) {
        btnAssignShift.disabled = true;
        btnAssignShift.textContent = 'Overriding...';

        try {
            const res = await fetch(`${API_BASE}/api/shifts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shift_id: shiftId,
                    employee_id: selectedEmpId,
                    shift_date: dateVal,
                    shift_type: typeVal,
                    start_time: startVal,
                    end_time: endVal,
                    location: locVal,
                    department: deptVal,
                    force: true
                })
            });

            const data = await res.json();
            btnAssignShift.disabled = false;
            btnAssignShift.textContent = 'Assign Shift';

            if (!res.ok) {
                alert(`Force assignment failed: ${data.error || 'Unknown error'}`);
                return;
            }

            alert(`🟢 Shift assigned successfully via override!`);

            // Reload employee list & details
            const initialized = await checkHealth();
            if (initialized) {
                await loadEmployees();
                await selectEmployee(selectedEmpId);
            }
        } catch (err) {
            console.error(err);
            btnAssignShift.disabled = false;
            btnAssignShift.textContent = 'Assign Shift';
            alert("Connection error occurred during override.");
        }
    }

    // 5.5 Shift Removal and Rest Day API
    async function deleteShift(shiftId) {
        if (!selectedEmpId) return;
        try {
            const res = await fetch(`${API_BASE}/api/shifts/${shiftId}`, { method: 'DELETE' });
            if (!res.ok) {
                const data = await res.json();
                alert(`Failed to delete shift: ${data.error || 'Unknown error'}`);
                return;
            }
            // Reload employee list & details
            const initialized = await checkHealth();
            if (initialized) {
                await loadEmployees();
                await selectEmployee(selectedEmpId);
            }
        } catch (err) {
            console.error(err);
            alert("Connection error occurred. Could not delete shift.");
        }
    }

    async function addRestDay(e) {
        e.preventDefault();
        if (!selectedEmpId) return;

        const dateVal = document.getElementById('rest-date').value;
        const notesVal = document.getElementById('rest-notes').value;
        const shiftId = 'R' + Math.floor(Math.random() * 1000000);

        const btn = document.getElementById('btn-add-rest-day');
        btn.disabled = true;
        btn.textContent = 'Adding...';

        try {
            const res = await fetch(`${API_BASE}/api/shifts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shift_id: shiftId,
                    employee_id: selectedEmpId,
                    shift_date: dateVal,
                    shift_type: 'Rest Day',
                    start_time: '00:00',
                    end_time: '00:00',
                    location: notesVal,
                    department: ''
                })
            });

            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Add Rest Day';

            if (!res.ok) {
                alert(`Failed to add rest day: ${data.error || 'Unknown error'}`);
                return;
            }

            alert(`🟢 Rest Day added successfully!`);
            restDayForm.reset();

            // Reload employee list & details
            const initialized = await checkHealth();
            if (initialized) {
                await loadEmployees();
                await selectEmployee(selectedEmpId);
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.textContent = 'Add Rest Day';
            alert("Connection error occurred.");
        }
    }

    // 7. Event Listeners
    employeeSearchInput.addEventListener('keyup', () => {
        renderEmployeeList();
    });

    btnValidateShift.addEventListener('click', validateShift);
    shiftAssignmentForm.addEventListener('submit', assignShift);
    if (restDayForm) {
        restDayForm.addEventListener('submit', addRestDay);
    }
    if (fatigueForm) {
        fatigueForm.addEventListener('submit', logFatigueRating);
    }

    // Dynamic Tab Switching
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('tab-btn')) {
            const container = e.target.closest('.tab-container');
            if (!container) return;
            container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            container.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            
            e.target.classList.add('active');
            const targetId = e.target.getAttribute('data-tab');
            const targetContent = document.getElementById(targetId);
            if (targetContent) targetContent.classList.remove('hidden');
        }
    });

    async function logFatigueRating(e) {
        e.preventDefault();
        if (!selectedEmpId) return;

        btnLogFatigue.disabled = true;
        btnLogFatigue.textContent = 'Logging...';

        const payload = {
            report_date: fatigueDate.value,
            fatigue_rating: parseInt(fatigueRating.value),
            notes: fatigueNotes.value
        };

        try {
            const res = await fetchWithTimeout(`${API_BASE}/api/employees/${selectedEmpId}/subjective-fatigue`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                timeout: 5000
            });
            
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.error || "Failed to log rating");
            }
            
            alert("🟢 Fatigue rating logged successfully.");
            fatigueForm.reset();
            fatigueDate.value = new Date().toISOString().split('T')[0];
            
            await selectEmployee(selectedEmpId);
        } catch (err) {
            console.error(err);
            alert("Error logging rating: " + err.message);
        } finally {
            btnLogFatigue.disabled = false;
            btnLogFatigue.textContent = 'Log Rating';
        }
    }

    // AI Chat Logic
    if (btnAiChatSend && aiChatInput) {
        btnAiChatSend.addEventListener('click', sendAiChatMessage);
        aiChatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendAiChatMessage();
        });
    }

    async function sendAiChatMessage() {
        const msg = aiChatInput.value.trim();
        if (!msg || !selectedEmpId) return;
        
        aiChatInput.value = '';
        btnAiChatSend.disabled = true;
        
        aiChatHistory.push({ role: 'user', content: msg });
        renderAiChat();
        
        const loadingId = 'ai-loading-' + Date.now();
        aiChatHistoryEl.innerHTML += `<div id="${loadingId}" style="color: var(--text-secondary); margin-bottom: 8px; font-size: 0.85rem; font-style: italic;">AI is thinking...</div>`;
        aiChatHistoryEl.scrollTop = aiChatHistoryEl.scrollHeight;

        try {
            const res = await fetchWithTimeout(`${API_BASE}/api/ai/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    employee_id: selectedEmpId,
                    history: aiChatHistory.slice(0, -1),
                    message: msg
                }),
                timeout: 30000
            });
            if (!res.ok) throw new Error("Chat failed");
            const data = await res.json();
            
            const loadingEl = document.getElementById(loadingId);
            if (loadingEl) loadingEl.remove();

            aiChatHistory.push({ role: 'assistant', content: data.reply });
            renderAiChat();
        } catch (err) {
            console.error(err);
            const loadingEl = document.getElementById(loadingId);
            if (loadingEl) loadingEl.remove();
            
            aiChatHistory.push({ role: 'assistant', content: "Sorry, I encountered an error communicating with the AI." });
            renderAiChat();
        } finally {
            btnAiChatSend.disabled = false;
            aiChatInput.focus();
        }
    }
    
    function renderAiChat() {
        if (!aiChatHistoryEl) return;
        aiChatHistoryEl.innerHTML = '';
        aiChatHistory.forEach(msg => {
            const div = document.createElement('div');
            div.style.padding = '8px 12px';
            div.style.borderRadius = '6px';
            div.style.marginBottom = '8px';
            if (msg.role === 'user') {
                div.style.background = 'rgba(255,255,255,0.1)';
                div.style.alignSelf = 'flex-end';
                div.innerHTML = `<strong style="color: var(--accent-blue);">You:</strong> ${msg.content}`;
            } else {
                div.style.background = 'rgba(0,204,102,0.1)';
                div.style.alignSelf = 'flex-start';
                div.innerHTML = `<strong style="color: var(--accent-green);">AI:</strong> ${msg.content}`;
            }
            aiChatHistoryEl.appendChild(div);
        });
        aiChatHistoryEl.scrollTop = aiChatHistoryEl.scrollHeight;
    }

    // Auto-Generate Schedule Logic
    let currentDraftAssignments = [];
    if (btnGenerateSchedule) {
        btnGenerateSchedule.addEventListener('click', () => {
            generateModal.classList.remove('hidden');
            generateResults.classList.add('hidden');
            generateLoading.classList.add('hidden');
            btnRunGenerate.classList.remove('hidden');
            btnRunGenerate.textContent = 'Run Generation';
            btnRunGenerate.disabled = false;
            currentDraftAssignments = [];
        });
    }

    const closeGenerateModal = () => generateModal.classList.add('hidden');
    if (btnCloseGenerateModal) btnCloseGenerateModal.addEventListener('click', closeGenerateModal);
    if (btnCancelGenerate) btnCancelGenerate.addEventListener('click', closeGenerateModal);

    if (btnRunGenerate) {
        btnRunGenerate.addEventListener('click', async () => {
            if (btnRunGenerate.textContent === 'Accept All Assignments') {
                btnRunGenerate.textContent = 'Saving...';
                btnRunGenerate.disabled = true;
                try {
                    for (const shift of currentDraftAssignments) {
                        shift.shift_id = 'S' + Math.floor(Math.random() * 1000000);
                        shift.force = true; // force if any overlap generated
                        await fetchWithTimeout(`${API_BASE}/api/shifts`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(shift)
                        });
                    }
                    alert("Draft schedule saved successfully!");
                    closeGenerateModal();
                    loadEmployees();
                } catch (err) {
                    console.error(err);
                    alert("Error saving schedule.");
                    btnRunGenerate.textContent = 'Accept All Assignments';
                    btnRunGenerate.disabled = false;
                }
                return;
            }

            generateLoading.classList.remove('hidden');
            generateResults.classList.add('hidden');
            btnRunGenerate.disabled = true;
            btnRunGenerate.textContent = 'Generating...';

            try {
                // Mock week dates
                const nextWeek = new Date();
                nextWeek.setDate(nextWeek.getDate() + (7 - nextWeek.getDay()) + 1); // Next Monday
                const d1 = nextWeek.toISOString().split('T')[0];
                const nextWeek2 = new Date(nextWeek); nextWeek2.setDate(nextWeek2.getDate() + 1);
                const d2 = nextWeek2.toISOString().split('T')[0];

                const openShifts = [
                    { shift_date: d1, start_time: "07:00", end_time: "15:00", shift_type: "Day", department: "Emergency" },
                    { shift_date: d1, start_time: "15:00", end_time: "23:00", shift_type: "Evening", department: "Emergency" },
                    { shift_date: d1, start_time: "23:00", end_time: "07:00", shift_type: "Night", department: "Emergency" },
                    { shift_date: d2, start_time: "09:00", end_time: "17:00", shift_type: "Day", department: "Security" },
                    { shift_date: d2, start_time: "23:00", end_time: "07:00", shift_type: "Night", department: "Security" }
                ];

                const res = await fetchWithTimeout(`${API_BASE}/api/schedule/generate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ open_shifts: openShifts }),
                    timeout: 15000
                });

                if (!res.ok) throw new Error("Failed to generate schedule");
                const data = await res.json();
                currentDraftAssignments = data.assigned_shifts;
                
                generateAssignmentsBody.innerHTML = '';
                data.assigned_shifts.forEach(s => {
                    generateAssignmentsBody.innerHTML += `
                        <tr>
                            <td style="padding: 10px;">${s.shift_date}</td>
                            <td style="padding: 10px;">${s.start_time} - ${s.end_time}</td>
                            <td style="padding: 10px;">${s.shift_type}</td>
                            <td style="padding: 10px;">${s.employee_name}</td>
                        </tr>
                    `;
                });

                generateUnfilledBody.innerHTML = '';
                if (data.unassigned_shifts.length === 0) {
                    generateUnfilledBody.innerHTML = `<tr><td colspan="3" style="padding: 10px; color: var(--text-secondary);">All shifts successfully assigned!</td></tr>`;
                } else {
                    data.unassigned_shifts.forEach(s => {
                        generateUnfilledBody.innerHTML += `
                            <tr>
                                <td style="padding: 10px;">${s.shift_date}</td>
                                <td style="padding: 10px;">${s.start_time} - ${s.end_time}</td>
                                <td style="padding: 10px;">${s.shift_type}</td>
                            </tr>
                        `;
                    });
                }

                generateLoading.classList.add('hidden');
                generateResults.classList.remove('hidden');
                
                btnRunGenerate.textContent = 'Accept All Assignments';
                btnRunGenerate.disabled = false;
            } catch (err) {
                console.error(err);
                alert("Error during schedule generation.");
                generateLoading.classList.add('hidden');
                btnRunGenerate.textContent = 'Run Generation';
                btnRunGenerate.disabled = false;
            }
        });
    }

    // Heatmap View Logic
    if (btnViewList && btnViewHeatmap) {
        btnViewList.addEventListener('click', () => {
            btnViewList.classList.add('active-view');
            btnViewHeatmap.classList.remove('active-view');
            dashboardListPanel.classList.remove('hidden');
            dashboardHeatmapPanel.classList.add('hidden');
        });

        btnViewHeatmap.addEventListener('click', () => {
            btnViewHeatmap.classList.add('active-view');
            btnViewList.classList.remove('active-view');
            dashboardListPanel.classList.add('hidden');
            dashboardHeatmapPanel.classList.remove('hidden');
            loadHeatmapData();
        });
    }

    async function loadHeatmapData() {
        heatmapLoading.classList.remove('hidden');
        heatmapHead.innerHTML = '';
        heatmapBody.innerHTML = '';
        
        try {
            const res = await fetchWithTimeout(`${API_BASE}/api/dashboard/heatmap`, { timeout: 15000 });
            if (!res.ok) throw new Error("Failed to load heatmap");
            const data = await res.json();
            
            // Handle empty case
            if (!data.dates || data.dates.length === 0) {
                heatmapBody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 2rem;">No shift data available for the heatmap.</td></tr>`;
                return;
            }

            
            // Render Headers
            let headHtml = '<th style="padding: 10px; position: sticky; left: 0; background: var(--surface-color); z-index: 10;">Employee</th>';
            data.dates.forEach(d => {
                const dateObj = new Date(d);
                const dayName = dateObj.toLocaleDateString('en-US', { weekday: 'short' });
                const monthDay = dateObj.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                headHtml += `<th style="padding: 10px; text-align: center; min-width: 90px;">${dayName}<br><span style="font-size: 0.8rem; font-weight: normal; color: var(--text-secondary);">${monthDay}</span></th>`;
            });
            heatmapHead.innerHTML = headHtml;

            // Render Body
            let bodyHtml = '';
            data.heatmap.forEach(emp => {
                bodyHtml += `<tr>`;
                bodyHtml += `<td style="padding: 10px; position: sticky; left: 0; background: var(--surface-color); z-index: 10; font-weight: 600; border-right: 1px solid rgba(255,255,255,0.1);">${emp.employee_name}</td>`;
                data.dates.forEach(d => {
                    const risk = emp.daily_risks[d];
                    let bgColor = 'rgba(255,255,255,0.02)';
                    if (risk.has_shift) {
                        if (risk.level === 'Critical') bgColor = 'rgba(255, 77, 77, 0.4)';
                        else if (risk.level === 'High') bgColor = 'rgba(255, 153, 51, 0.4)';
                        else if (risk.level === 'Moderate' || risk.level === 'Medium') bgColor = 'rgba(255, 204, 0, 0.4)';
                        else bgColor = 'rgba(0, 204, 102, 0.2)';
                    }
                    bodyHtml += `
                        <td style="padding: 10px; text-align: center;">
                            <div style="background: ${bgColor}; border-radius: 4px; padding: 10px; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 40px; ${risk.has_shift ? 'border: 1px solid rgba(255,255,255,0.1);' : ''}">
                                ${risk.has_shift ? `<span style="font-size: 0.85rem; font-weight: bold;">${risk.score}</span>` : '<span style="color: var(--text-secondary); font-size: 0.8rem;">Rest</span>'}
                            </div>
                        </td>
                    `;
                });
                bodyHtml += `</tr>`;
            });
            heatmapBody.innerHTML = bodyHtml;

        } catch (err) {
            console.error(err);
            heatmapBody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--accent-red); padding: 2rem;">Error loading heatmap data.</td></tr>`;
        } finally {
            heatmapLoading.classList.add('hidden');
        }
    }

    // 8. Auto Startup
    checkHealth().then(status => {
        if (status === 'initialized') {
            loadEmployees();
        } else if (status === 'missing_db') {
            // DB is missing, prompt to setup database in the employee list container
            employeeListContainer.innerHTML = `
                <div style="text-align: center; color: var(--text-secondary); padding: 2rem 0;">
                    <p style="margin-bottom: 15px;">Database not initialized.</p>
                    <button class="btn btn-primary btn-sm" id="btn-init-prompt">Initialize Database</button>
                </div>
            `;
            const btnInitPrompt = document.getElementById('btn-init-prompt');
            if (btnInitPrompt) {
                btnInitPrompt.addEventListener('click', seedDatabase);
            }
        } else {
            // Offline/timeout -> Fallback to demo mode
            loadEmployees(true);
        }
    });
});
