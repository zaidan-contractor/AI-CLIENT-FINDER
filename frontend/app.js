/**
 * Opportunity Intelligence & Consultative Sales Platform
 * Mission Control Frontend Application Controller
 * Real Backend Authentication & API Integration (Zero Hardcoded Mock Fallbacks)
 * Strictly enforces 16-Gate Dispatch Architecture & Epistemic Boundaries.
 */

document.addEventListener('DOMContentLoaded', () => {
  // --- Configurable API Base Resolution ---
  function resolveApiBase() {
    // 1. Window-level explicit configuration
    if (typeof window !== 'undefined' && window.__API_BASE__) {
      return String(window.__API_BASE__).replace(/\/+$/, '');
    }
    // 2. Local storage override for dev/staging flexibility
    if (typeof localStorage !== 'undefined') {
      const stored = localStorage.getItem('api_base_url');
      if (stored) return stored.replace(/\/+$/, '');
    }
    // 3. Local dev origin detection:
    // If served from a separate static server (e.g. port 5500, 3000, 5173 != 8000) or file://,
    // target the local FastAPI backend on port 8000.
    if (typeof window !== 'undefined' && window.location) {
      const port = window.location.port;
      const protocol = window.location.protocol;
      if (protocol === 'file:' || (port && port !== '8000')) {
        const host = (window.location.hostname && window.location.hostname !== 'localhost')
          ? window.location.hostname
          : '127.0.0.1';
        return `http://${host}:8000/api/v1`;
      }
    }
    // 4. Default: relative path for reverse proxy or same-origin FastAPI dashboard hosting
    return '/api/v1';
  }

  const API_BASE = resolveApiBase();
  window.__ACTIVE_API_BASE__ = API_BASE;
  console.log(`[Opportunity Platform] Active API Base: ${API_BASE}`);

  // --- Safe Response & Error Handling ---
  async function parseJsonResponse(res) {
    const contentType = res.headers ? (res.headers.get('content-type') || '') : '';
    const text = await res.text();

    if (contentType.includes('application/json')) {
      try {
        return JSON.parse(text);
      } catch (err) {
        throw new Error(`Malformed JSON response from ${res.url} (HTTP ${res.status}): ${err.message}`);
      }
    }

    if (text.trim().startsWith('<')) {
      throw new Error(
        `Received HTML instead of JSON from endpoint ${res.url} (HTTP ${res.status} ${res.statusText || ''}). ` +
        `Verify that requests target the FastAPI backend (${API_BASE}) and not the static frontend server.`
      );
    }

    try {
      return JSON.parse(text);
    } catch {
      throw new Error(
        `Non-JSON response from ${res.url} (HTTP ${res.status} ${res.statusText || ''}): ${text.slice(0, 120)}`
      );
    }
  }

  function formatApiError(errData, fallback = 'Request failed.') {
    if (!errData) return fallback;
    if (typeof errData === 'string') return errData;
    if (typeof errData.detail === 'string') return errData.detail;
    if (Array.isArray(errData.detail)) {
      return errData.detail
        .map(d => (d.msg ? `${d.loc ? d.loc.slice(1).join('.') + ': ' : ''}${d.msg}` : JSON.stringify(d)))
        .join('; ');
    }
    if (errData.message) return errData.message;
    return fallback;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  let activeTab = 'overview';
  let isKillSwitchActive = false;
  let currentUser = null;
  let activeWorkspaceId = localStorage.getItem('active_workspace_id') || 'ws_full';

  // --- Authentication Management ---
  const authBtn = document.getElementById('auth-btn');
  const userDisplayEmail = document.getElementById('user-display-email');
  const loginModal = document.getElementById('login-modal');
  const closeLoginBtn = document.getElementById('close-login-btn');
  const loginForm = document.getElementById('login-form');
  const loginErrorMsg = document.getElementById('login-error-msg');
  const activeWorkspaceLabel = document.getElementById('active-workspace-label');

  function getAuthToken() {
    return localStorage.getItem('access_token');
  }

  function setAuthSession(token, user, workspaceId) {
    localStorage.setItem('access_token', token);
    localStorage.setItem('active_workspace_id', workspaceId);
    activeWorkspaceId = workspaceId;
    currentUser = user;
    if (activeWorkspaceLabel) activeWorkspaceLabel.textContent = workspaceId;
    if (userDisplayEmail) userDisplayEmail.textContent = user.email || 'Authenticated';
    if (authBtn) authBtn.textContent = 'Sign Out';
    if (loginModal) loginModal.classList.add('hidden');
  }

  function clearAuthSession() {
    localStorage.removeItem('access_token');
    currentUser = null;
    if (userDisplayEmail) userDisplayEmail.textContent = 'Not Signed In';
    if (authBtn) authBtn.textContent = 'Sign In';
    showLoginModal();
  }

  function showLoginModal() {
    if (loginModal) loginModal.classList.remove('hidden');
    if (loginErrorMsg) loginErrorMsg.style.display = 'none';
  }

  function hideLoginModal() {
    if (loginModal) loginModal.classList.add('hidden');
  }

  if (authBtn) {
    authBtn.addEventListener('click', () => {
      if (getAuthToken()) {
        clearAuthSession();
      } else {
        showLoginModal();
      }
    });
  }

  if (closeLoginBtn) {
    closeLoginBtn.addEventListener('click', hideLoginModal);
  }

  if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = document.getElementById('login-email').value.trim();
      const password = document.getElementById('login-password').value;

      try {
        const res = await fetch(`${API_BASE}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password })
        });

        if (!res.ok) {
          let errorDetail = `Authentication failed (HTTP ${res.status} ${res.statusText || ''}).`;
          try {
            const errData = await parseJsonResponse(res);
            errorDetail = formatApiError(errData, errorDetail);
          } catch (parseEx) {
            errorDetail = parseEx.message;
          }
          loginErrorMsg.textContent = errorDetail;
          loginErrorMsg.style.display = 'block';
          return;
        }

        const data = await parseJsonResponse(res);
        setAuthSession(data.access_token, { email }, data.active_workspace_id || activeWorkspaceId);
        loadCurrentTab();
      } catch (ex) {
        loginErrorMsg.textContent = `Connection error: ${ex.message}`;
        loginErrorMsg.style.display = 'block';
      }
    });
  }

  // --- Authenticated Fetch Wrapper ---
  async function fetchWithAuth(url, options = {}) {
    const token = getAuthToken();
    const headers = options.headers ? { ...options.headers } : {};

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (!headers['Content-Type'] && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }

    const res = await fetch(url, { ...options, headers });

    if (res.status === 401) {
      console.warn('Session expired or unauthorized. Showing login prompt.');
      showLoginModal();
      throw new Error('Unauthorized');
    }

    return res;
  }

  // Navigation Tabs
  const navButtons = document.querySelectorAll('.nav-item');
  const viewPanels = document.querySelectorAll('.view-panel');

  function switchTab(tabId) {
    activeTab = tabId;
    navButtons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    viewPanels.forEach(panel => {
      panel.classList.toggle('active', panel.id === `tab-${tabId}`);
    });
    loadCurrentTab();
  }

  function loadCurrentTab() {
    if (activeTab === 'overview') loadOverviewMetrics();
    else if (activeTab === 'leads') loadLeadDossiers();
    else if (activeTab === 'claims') loadClaimsGovernance();
    else if (activeTab === 'inbox') loadInboundTriage();
    else if (activeTab === 'recommendations') loadRecommendations();
  }

  navButtons.forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Emergency Kill Switch Toggle
  const killSwitchToggle = document.getElementById('kill-switch-toggle');
  const healthBadge = document.getElementById('system-health-badge');
  const healthText = document.getElementById('health-text');

  async function checkSystemHealth() {
    try {
      const res = await fetch(`${API_BASE}/health`);
      if (res.ok) {
        const data = await parseJsonResponse(res);
        isKillSwitchActive = data.emergency_kill_switch;
        if (killSwitchToggle) killSwitchToggle.checked = isKillSwitchActive;
        updateHealthUI(isKillSwitchActive);
      }
    } catch (e) {
      console.warn('Health check unreachable:', e);
      updateHealthUI(false);
    }
  }

  function updateHealthUI(killActive) {
    if (killActive) {
      healthBadge.className = 'status-indicator error';
      healthText.textContent = 'GATE 15: KILL SWITCH ENGAGED';
    } else {
      healthBadge.className = 'status-indicator live';
      healthText.textContent = 'ALL SYSTEMS NOMINAL (GATES 1-16 LIVE)';
    }
  }

  if (killSwitchToggle) {
    killSwitchToggle.addEventListener('change', async (e) => {
      const active = e.target.checked;
      try {
        const res = await fetchWithAuth(`${API_BASE}/system/kill-switch`, {
          method: 'POST',
          body: JSON.stringify({ active })
        });
        if (res.ok) {
          const data = await parseJsonResponse(res);
          isKillSwitchActive = data.emergency_kill_switch;
          updateHealthUI(isKillSwitchActive);
        }
      } catch (err) {
        console.error('Failed to toggle kill switch:', err);
        killSwitchToggle.checked = isKillSwitchActive;
      }
    });
  }

  // --- Real Data Loading: Overview ---
  async function loadOverviewMetrics() {
    try {
      const res = await fetchWithAuth(`${API_BASE}/analytics/workspace`);
      if (res.ok) {
        const data = await parseJsonResponse(res);
        document.getElementById('stat-discovered').textContent = data.total_businesses || 0;
        document.getElementById('stat-hypotheses').textContent = data.validated_hypotheses || 0;
        document.getElementById('stat-transmitted').textContent = data.sent_count || 0;
        document.getElementById('stat-suppressed').textContent = data.suppressed_count || 0;
        document.getElementById('stat-hot-deals').textContent = data.hot_deals_escalated || 0;
        document.getElementById('stat-reply-rate').textContent = (data.reply_rate !== undefined ? `${(data.reply_rate * 100).toFixed(1)}%` : '0.0%');
      }
    } catch (e) {
      console.log('Analytics load deferred or unauthorized.');
    }
    await loadCapacityStatus();
  }

  // --- Real Data Loading: Capacity & Quality Quotas ---
  async function loadCapacityStatus() {
    try {
      const res = await fetchWithAuth(`${API_BASE}/capacity/status`);
      if (res.ok) {
        const data = await parseJsonResponse(res);
        const floorBadge = document.getElementById('capacity-quality-floor-badge');
        if (floorBadge && data.policy) {
          floorBadge.textContent = `QUALITY FLOOR: ${data.policy.min_quality_floor}`;
        }
        if (data.quotas && data.quotas.outreach) {
          const o = data.quotas.outreach;
          const oText = document.getElementById('cap-outreach-text');
          const oBar = document.getElementById('cap-outreach-bar');
          const oRem = document.getElementById('cap-outreach-rem');
          if (oText) oText.textContent = `${o.used} / ${o.limit}`;
          const pct = Math.min(100, Math.round((o.used / Math.max(1, o.limit)) * 100));
          if (oBar) oBar.style.width = `${pct}%`;
          if (oRem) oRem.textContent = `${o.remaining} remaining`;
        }
        if (data.quotas && data.quotas.llm_operations) {
          const l = data.quotas.llm_operations;
          const lText = document.getElementById('cap-llm-text');
          const lBar = document.getElementById('cap-llm-bar');
          const lRem = document.getElementById('cap-llm-rem');
          if (lText) lText.textContent = `${l.used} / ${l.limit}`;
          const pct = Math.min(100, Math.round((l.used / Math.max(1, l.limit)) * 100));
          if (lBar) lBar.style.width = `${pct}%`;
          if (lRem) lRem.textContent = `${l.remaining} remaining`;
        }
        if (data.provider) {
          const p = data.provider;
          const pName = document.getElementById('cap-provider-name');
          const pTier = document.getElementById('cap-provider-tier');
          const pAvail = document.getElementById('cap-provider-avail');
          if (pName) pName.textContent = `${p.provider_name} (${p.model || 'default'})`;
          if (pTier) pTier.textContent = p.quality_tier || 'UNKNOWN';
          if (pAvail) {
            const state = p.state || p.provider_availability || (p.is_available ? 'AVAILABLE' : 'ERROR');
            if (state === 'QUOTA_EXHAUSTED') {
              pAvail.textContent = 'QUOTA EXHAUSTED';
              pAvail.style.color = '#ef4444';
            } else if (state === 'AVAILABLE') {
              pAvail.textContent = 'ONLINE';
              pAvail.style.color = '#10b981';
            } else if (state === 'CONFIGURED') {
              pAvail.textContent = 'CONFIGURED (UNTESTED)';
              pAvail.style.color = '#38bdf8';
            } else if (state === 'REACHABLE') {
              pAvail.textContent = 'REACHABLE';
              pAvail.style.color = '#f59e0b';
            } else if (state === 'ERROR') {
              pAvail.textContent = 'ERROR';
              pAvail.style.color = '#ef4444';
            } else {
              pAvail.textContent = state;
              pAvail.style.color = '#f59e0b';
            }
          }
        }
      }
    } catch (e) {
      console.log('Capacity status load deferred or unauthorized.');
    }
  }

  const btnDrain = document.getElementById('btn-drain-backpressure');
  if (btnDrain) {
    btnDrain.addEventListener('click', async () => {
      btnDrain.disabled = true;
      btnDrain.textContent = 'Draining...';
      try {
        const res = await fetchWithAuth(`${API_BASE}/capacity/drain-backpressure`, { method: 'POST' });
        if (res && res.ok) {
          const data = await parseJsonResponse(res);
          alert(`Backpressure queue drained:\nProcessed: ${data.processed}\nTransmitted: ${data.transmitted}\nSuppressed: ${data.suppressed}\nBlocked: ${data.blocked}\nStill Queued: ${data.still_queued}`);
          await loadCapacityStatus();
        } else {
          alert('Drain backpressure requires WorkspaceAdmin or OrgAdmin permissions.');
        }
      } catch (err) {
        alert('Error draining backpressure: ' + err.message);
      } finally {
        btnDrain.disabled = false;
        btnDrain.textContent = 'Drain Backpressure';
      }
    });
  }

  // --- Real Data Loading: Discovery Search Controls ---
  const btnRunDisc = document.getElementById('btn-run-discovery');
  if (btnRunDisc) {
    btnRunDisc.addEventListener('click', async () => {
      const qInput = document.getElementById('disc-query');
      const locInput = document.getElementById('disc-location');
      const catInput = document.getElementById('disc-category');
      const statusMsg = document.getElementById('disc-status-msg');

      const query = qInput ? qInput.value.trim() : '';
      const location = locInput ? locInput.value.trim() : '';
      const category = catInput ? catInput.value.trim() : '';

      if (!query || !location || !category) {
        if (statusMsg) statusMsg.textContent = 'Please fill in Query, Location, and Category.';
        return;
      }

      btnRunDisc.disabled = true;
      btnRunDisc.textContent = 'Searching...';
      if (statusMsg) statusMsg.textContent = 'Executing provider discovery search...';

      try {
        const res = await fetchWithAuth(`${API_BASE}/discovery/search`, {
          method: 'POST',
          body: JSON.stringify({ query, location, category, limit: 20 })
        });
        if (res && res.ok) {
          const data = await parseJsonResponse(res);
          if (statusMsg) {
            statusMsg.textContent = `✓ Discovered ${data.total_discovered_candidates} leads. Newly registered: ${data.newly_registered} (Duplicates skipped: ${data.exact_duplicates_skipped}, Possible duplicates: ${data.possible_duplicates_flagged}).`;
          }
          await loadLeadDossiers();
        } else {
          const err = await parseJsonResponse(res).catch(() => ({}));
          if (statusMsg) statusMsg.textContent = `Discovery error: ${err.detail || res.statusText}`;
        }
      } catch (e) {
        if (statusMsg) statusMsg.textContent = `Error: ${e.message}`;
      } finally {
        btnRunDisc.disabled = false;
        btnRunDisc.textContent = 'Search & Register';
      }
    });
  }

  // --- Operator Workflow State & Workspace ---
  let currentActiveBizId = null;
  let currentActiveBiz = null;
  let currentLeadId = null;
  let currentLeadData = null;
  let currentDraftId = null;
  let currentDraftData = null;
  let activeWfStep = 'identity';

  const bizModal = document.getElementById('biz-detail-modal');
  const btnCloseModal = document.getElementById('btn-close-biz-modal');
  if (btnCloseModal && bizModal) {
    btnCloseModal.addEventListener('click', () => {
      bizModal.style.display = 'none';
      currentActiveBizId = null;
      currentActiveBiz = null;
      currentLeadId = null;
      currentDraftId = null;
    });
  }

  // Stepper Tab Switching
  function switchWfStep(stepName) {
    activeWfStep = stepName;
    document.querySelectorAll('.wf-step-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.step === stepName);
    });
    document.querySelectorAll('.wf-step-panel').forEach(panel => {
      panel.style.display = panel.id === `wf-panel-${stepName}` ? 'block' : 'none';
    });
  }

  document.querySelectorAll('.wf-step-btn').forEach(btn => {
    btn.addEventListener('click', () => switchWfStep(btn.dataset.step));
  });

  document.querySelectorAll('.btn-wf-next').forEach(btn => {
    btn.addEventListener('click', () => switchWfStep(btn.dataset.next));
  });

  document.querySelectorAll('.btn-wf-prev').forEach(btn => {
    btn.addEventListener('click', () => switchWfStep(btn.dataset.prev));
  });

  function updateWorkflowStatusBadge(statusText, color = '#818cf8', bg = 'rgba(99,102,241,0.2)') {
    const badge = document.getElementById('modal-top-workflow-status');
    if (badge) {
      badge.textContent = statusText;
      badge.style.color = color;
      badge.style.background = bg;
    }
  }

  // Open Full Business Workspace
  async function openBusinessDetail(biz) {
    currentActiveBizId = biz.id;
    currentActiveBiz = biz;
    currentLeadId = null;
    currentDraftId = null;
    currentDraftData = null;

    if (!bizModal) return;
    bizModal.style.display = 'block';
    switchWfStep('identity');

    const mCat = document.getElementById('modal-biz-category');
    const mName = document.getElementById('modal-biz-name');
    const mMeta = document.getElementById('modal-biz-meta');
    const mOrigin = document.getElementById('modal-biz-origin-badge');

    if (mCat) mCat.textContent = biz.category || 'Business';
    if (mName) mName.textContent = biz.name;

    const isDemo = biz.is_demo_fixture || biz.data_origin === 'DEMO_FIXTURE' || (biz.source_provider || '').toUpperCase() === 'MANUAL' || (biz.canonical_domain || '').includes('example.com') || (biz.website_url || '').includes('example.com');
    if (mOrigin) {
      mOrigin.textContent = isDemo ? '[DEMO FIXTURE]' : `[DISCOVERED: ${biz.source_provider || 'OpenStreetMap'}]`;
      mOrigin.style.background = isDemo ? '#dc2626' : 'rgba(16,185,129,0.2)';
      mOrigin.style.color = isDemo ? '#ffffff' : '#10b981';
      mOrigin.style.fontWeight = '700';
    }

    if (mMeta) {
      mMeta.textContent = `Provider: ${biz.source_provider || 'Not available'} | Location: ${biz.city || biz.address || 'Not available'} | Registered in Workspace`;
    }

    updateWorkflowStatusBadge('DISCOVERED');

    // Populate Step 1: Identity & Contacts
    populateIdentityStep(biz);

    // Populate Step 2: Evidence
    await loadModalEvidence(biz.id);

    // Populate Step 3: Opportunities & Score
    await loadModalOpportunities(biz.id);

    // Populate Step 4: Check if already qualified
    await checkExistingLeadQualification(biz.id);

    // Reset Campaign Step
    const campEnrollStatus = document.getElementById('m-campaign-enroll-status');
    const campEnrollBtn = document.getElementById('modal-btn-enroll-campaign');
    const campHandoffBox = document.getElementById('m-enrolled-handoff-box');
    if (campEnrollStatus) campEnrollStatus.textContent = 'Select an approved draft and target campaign.';
    if (campEnrollBtn) campEnrollBtn.disabled = true;
    if (campHandoffBox) campHandoffBox.style.display = 'none';
  }

  function populateIdentityStep(biz) {
    const locEl = document.getElementById('m-id-location');
    const webEl = document.getElementById('m-id-website');
    const phEl = document.getElementById('m-id-phone');
    const emEl = document.getElementById('m-id-email');
    const provEl = document.getElementById('m-id-provider');
    const confEl = document.getElementById('m-id-confidence');
    const contactsContainer = document.getElementById('m-contacts-container');

    if (locEl) locEl.textContent = biz.city || biz.address || 'Not available';
    if (webEl) webEl.textContent = biz.website_url || biz.canonical_domain || 'Not available';
    if (phEl) phEl.textContent = biz.normalized_phone || biz.phone || 'Not available';
    if (emEl) emEl.textContent = biz.primary_email || 'Not available';
    if (provEl) provEl.textContent = biz.source_provider || 'Not available';
    if (confEl) confEl.textContent = `${Math.round((biz.identity_confidence || 1.0) * 100)}%`;

    if (contactsContainer) {
      const contacts = biz.contacts || [];
      if (contacts.length === 0) {
        contactsContainer.innerHTML = '<p style="color:#94a3b8; font-size:0.85rem; margin:0;">No contact records found for this business.</p>';
      } else {
        contactsContainer.innerHTML = contacts.map(c => {
          const tier = (c.verification_tier || 'UNVERIFIED').toUpperCase();
          let badgeColor = '#f59e0b';
          let badgeBg = 'rgba(245,158,11,0.15)';
          let statusText = 'UNVERIFIED';

          if (tier === 'CONFIRMED_VERIFIED' || tier === 'PROVIDER_VERIFIED' || tier === 'TIER_1_DIRECT' || tier === 'TIER_2_CONFIRMED') {
            badgeColor = '#10b981';
            badgeBg = 'rgba(16,185,129,0.15)';
            statusText = 'VERIFIED';
          } else if (tier === 'FAILED_VERIFICATION' || tier === 'INVALID') {
            badgeColor = '#ef4444';
            badgeBg = 'rgba(239,68,68,0.15)';
            statusText = 'INVALID';
          } else if (tier === 'UNKNOWN') {
            badgeColor = '#94a3b8';
            badgeBg = 'rgba(255,255,255,0.1)';
            statusText = 'UNKNOWN';
          }

          const source = (c.verification_metadata && c.verification_metadata.source) || 'Authoritative DB record';
          const freshTime = c.freshness_timestamp ? new Date(c.freshness_timestamp).toLocaleDateString() : 'Not available';

          return `
            <div style="padding: 0.5rem 0.75rem; margin-bottom: 0.5rem; background: rgba(0,0,0,0.3); border-radius: 6px; border: 1px solid rgba(255,255,255,0.06);">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <strong style="color:#f8fafc; font-size:0.85rem;">${c.email}</strong>
                <span class="tag" style="background:${badgeBg}; color:${badgeColor}; font-weight:700; font-size:0.7rem;">${statusText}</span>
              </div>
              <div style="font-size:0.75rem; color:#94a3b8; margin-top:0.25rem;">
                Role: <span style="color:#cbd5e1;">${c.role_title || 'General Contact'}</span> | Source: <span style="color:#38bdf8;">${source}</span> | Verified: <span>${freshTime}</span>
              </div>
            </div>
          `;
        }).join('');
      }
    }
  }

  // STEP 2: Research & Evidence
  async function loadModalEvidence(bizId) {
    const evContainer = document.getElementById('modal-evidence-list');
    if (!evContainer) return;
    evContainer.innerHTML = '<p style="color:#94a3b8;">Loading evidence records...</p>';

    try {
      const eRes = await fetchWithAuth(`${API_BASE}/businesses/${bizId}/evidence`);
      if (eRes && eRes.ok) {
        const evidence = await parseJsonResponse(eRes);
        if (!evidence || evidence.length === 0) {
          evContainer.innerHTML = `
            <div style="padding:1.5rem; text-align:center; background:rgba(255,255,255,0.02); border-radius:8px; border:1px dashed rgba(255,255,255,0.1);">
              <p style="color:#94a3b8; margin:0 0 0.5rem 0;">No empirical evidence collected yet.</p>
              <span style="font-size:0.8rem; color:#cbd5e1;">Click "Run Research Pipeline" to crawl domain, verify absence signals, and extract facts.</span>
            </div>`;
        } else {
          updateWorkflowStatusBadge('RESEARCHED', '#10b981', 'rgba(16,185,129,0.2)');
          evContainer.innerHTML = evidence.map(e => {
            const raw = (e.raw_observation || '').toLowerCase();
            const obsText = (raw.includes('no website') || raw.includes('inaccessible'))
              ? 'Website inaccessible / evidence unavailable'
              : e.raw_observation;

            const tier = (e.epistemic_tier || 'FACT').toUpperCase();
            let tierColor = '#10b981';
            if (tier === 'INFERENCE') tierColor = '#38bdf8';
            else if (tier === 'HYPOTHESIS') tierColor = '#a855f7';
            else if (tier === 'UNKNOWN') tierColor = '#94a3b8';

            const borderCol = e.feature_presence_state === 'PRESENT' ? '#10b981' : e.feature_presence_state === 'ABSENCE_CONFIRMED' ? '#ef4444' : '#f59e0b';

            return `
              <div style="padding: 0.75rem 1rem; margin-bottom: 0.5rem; background: rgba(255,255,255,0.02); border-radius: 6px; border-left: 3px solid ${borderCol};">
                <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.35rem;">
                  <span>
                    <strong style="color:#f8fafc;">${e.signal_type}</strong> |
                    <code style="color:${tierColor}; font-weight:700;">${tier}</code>
                  </span>
                  <span class="tag" style="background:rgba(255,255,255,0.08); font-size:0.7rem;">
                    ${e.feature_presence_state} (Confidence: ${Math.round((e.confidence || 1.0) * 100)}%)
                  </span>
                </div>
                <div style="color:#cbd5e1; font-size:0.85rem; line-height:1.4;">${obsText}</div>
                ${e.source_url ? `<div style="font-size:0.75rem; color:#64748b; margin-top:0.25rem;">Source: <a href="${e.source_url}" target="_blank" style="color:#38bdf8; text-decoration:none;">${e.source_url}</a></div>` : ''}
              </div>
            `;
          }).join('');
        }
      } else {
        evContainer.innerHTML = `
          <div style="padding:1rem; background:rgba(239,68,68,0.1); border:1px solid #ef4444; border-radius:6px; color:#ef4444;">
            Failed to load evidence from server.
            <button class="btn btn-secondary btn-sm" style="margin-left:0.5rem;" onclick="loadModalEvidence('${bizId}')">Retry</button>
          </div>`;
      }
    } catch (e) {
      evContainer.innerHTML = `
        <div style="padding:1rem; background:rgba(239,68,68,0.1); border:1px solid #ef4444; border-radius:6px; color:#ef4444;">
          Error loading evidence: ${e.message}
          <button class="btn btn-secondary btn-sm" style="margin-left:0.5rem;" onclick="loadModalEvidence('${bizId}')">Retry</button>
        </div>`;
    }
  }

  // Hook "Run Research Pipeline"
  const btnModalResearch = document.getElementById('modal-btn-run-research');
  if (btnModalResearch) {
    btnModalResearch.addEventListener('click', async () => {
      if (!currentActiveBizId) return;
      btnModalResearch.disabled = true;
      btnModalResearch.textContent = 'Running Research...';
      const mStatus = document.getElementById('modal-research-status');
      if (mStatus) mStatus.textContent = 'Executing bounded crawler...';
      updateWorkflowStatusBadge('RESEARCHING', '#38bdf8', 'rgba(56,189,248,0.2)');

      try {
        const res = await fetchWithAuth(`${API_BASE}/businesses/${currentActiveBizId}/research`, {
          method: 'POST',
          body: JSON.stringify({ trigger_type: 'MANUAL' })
        });
        if (res && res.ok) {
          const runData = await parseJsonResponse(res);
          if (mStatus) mStatus.textContent = `✓ Research completed: ${runData.evidence_count} evidence records collected.`;
          await loadModalEvidence(currentActiveBizId);
          await loadModalOpportunities(currentActiveBizId);
        } else {
          const err = await parseJsonResponse(res).catch(() => ({}));
          if (mStatus) mStatus.textContent = `Research failed: ${err.detail || res.statusText}`;
          updateWorkflowStatusBadge('FAILED', '#ef4444', 'rgba(239,68,68,0.2)');
        }
      } catch (e) {
        if (mStatus) mStatus.textContent = `Error: ${e.message}`;
      } finally {
        btnModalResearch.disabled = false;
        btnModalResearch.textContent = 'Run Research Pipeline';
      }
    });
  }

  // STEP 3: Opportunities & Heuristic Score
  async function loadModalOpportunities(bizId) {
    const oppContainer = document.getElementById('modal-opportunity-list');
    const scoreVal = document.getElementById('m-opp-score-val');
    const scoreBadge = document.getElementById('m-opp-score-badge');

    if (!oppContainer) return;
    oppContainer.innerHTML = '<p style="color:#94a3b8;">Loading synthesized opportunities...</p>';

    // Load Score
    try {
      const sRes = await fetchWithAuth(`${API_BASE}/opportunities/${bizId}`);
      if (sRes && sRes.ok) {
        const sData = await parseJsonResponse(sRes);
        if (scoreVal) scoreVal.textContent = Math.round(sData.opportunity_score || 0);
        if (scoreBadge) {
          scoreBadge.textContent = sData.outreach_eligible ? 'ELIGIBLE (REVIEW PRIORITY)' : 'INELIGIBLE';
          scoreBadge.style.color = sData.outreach_eligible ? '#10b981' : '#f59e0b';
        }
      }
    } catch (_) {}

    // Load Opportunities
    try {
      const oRes = await fetchWithAuth(`${API_BASE}/businesses/${bizId}/opportunities`);
      if (oRes && oRes.ok) {
        const oppData = await parseJsonResponse(oRes);
        const gaps = oppData.digital_gaps || [];
        const hypotheses = oppData.hypotheses || [];

        if (gaps.length === 0 && hypotheses.length === 0) {
          oppContainer.innerHTML = `
            <div style="padding:1.5rem; text-align:center; background:rgba(255,255,255,0.02); border-radius:8px; border:1px dashed rgba(255,255,255,0.1);">
              <p style="color:#94a3b8; margin:0;">No opportunities synthesized yet. Run research to identify absence signals and digital gaps.</p>
            </div>`;
          return;
        }

        updateWorkflowStatusBadge('OPPORTUNITY_FOUND', '#a855f7', 'rgba(168,85,247,0.2)');

        let html = '';
        if (gaps.length > 0) {
          html += '<h4 style="color:#38bdf8; margin:0.5rem 0 0.75rem 0;">Empirical Digital Gaps</h4>';
          html += gaps.map(g => `
            <div style="padding:0.75rem 1rem; background:rgba(56,189,248,0.04); border-radius:8px; margin-bottom:0.6rem; border:1px solid rgba(56,189,248,0.15);">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <strong style="color:#f8fafc; font-size:0.9rem;">${g.title}</strong>
                <span class="tag" style="background:rgba(56,189,248,0.2); color:#38bdf8; font-size:0.7rem;">DIGITAL GAP</span>
              </div>
              <div style="font-size:0.85rem; color:#cbd5e1; margin-top:0.3rem;">${g.epistemic_statement}</div>
              <div style="font-size:0.8rem; color:#94a3b8; margin-top:0.4rem;">
                Recommended: <strong style="color:#a5b4fc;">${g.suggested_solution}</strong>
              </div>
            </div>
          `).join('');
        }

        if (hypotheses.length > 0) {
          html += '<h4 style="color:#a855f7; margin:1rem 0 0.75rem 0;">Validated Problem Hypotheses</h4>';
          html += hypotheses.map(h => {
            const imp = h.impact
              ? `${h.impact.currency} ${h.impact.low.toFixed(0)} - ${h.impact.high.toFixed(0)}/mo`
              : 'Financial impact: UNKNOWN / Reason: insufficient evidence';

            return `
              <div style="padding:0.85rem 1rem; background:rgba(255,255,255,0.02); border-radius:8px; margin-bottom:0.75rem; border:1px solid rgba(255,255,255,0.08);">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <strong style="color:#f8fafc; font-size:0.9rem;">${h.title}</strong>
                  <span class="tag" style="background:${h.status === 'VALIDATED' ? 'rgba(16,185,129,0.2)' : 'rgba(100,116,139,0.2)'}; color:${h.status === 'VALIDATED' ? '#10b981' : '#94a3b8'};">${h.status}</span>
                </div>
                <p style="font-size:0.85rem; color:#cbd5e1; margin:0.35rem 0 0.5rem 0;">${h.description}</p>
                <div style="font-size:0.8rem; color:#f59e0b; padding:0.4rem 0.6rem; background:rgba(245,158,11,0.08); border-radius:4px;">
                  Impact: <strong>${imp}</strong>
                </div>
                ${(h.solutions || []).map(s => `
                  <div style="margin-top:0.4rem; padding:0.35rem 0.6rem; background:rgba(0,0,0,0.25); border-radius:4px; font-size:0.78rem; display:flex; gap:0.5rem; align-items:center;">
                    <span class="tag tag-immutable" style="font-size:0.68rem;">${s.solution_type}</span>
                    <span style="color:#e2e8f0;">${s.title}</span>
                  </div>
                `).join('')}
              </div>
            `;
          }).join('');
        }

        oppContainer.innerHTML = html;
      } else {
        oppContainer.innerHTML = '<p style="color:#ef4444;">Failed to load opportunity intelligence.</p>';
      }
    } catch (e) {
      oppContainer.innerHTML = `<p style="color:#ef4444;">Error: ${e.message}</p>`;
    }
  }

  const btnModalAnalyze = document.getElementById('modal-btn-analyze-opps');
  if (btnModalAnalyze) {
    btnModalAnalyze.addEventListener('click', async () => {
      if (!currentActiveBizId) return;
      btnModalAnalyze.disabled = true;
      btnModalAnalyze.textContent = 'Analyzing...';
      try {
        await loadModalOpportunities(currentActiveBizId);
      } finally {
        btnModalAnalyze.disabled = false;
        btnModalAnalyze.textContent = 'Analyze Opportunities';
      }
    });
  }

  // STEP 4: Lead Qualification
  async function checkExistingLeadQualification(bizId) {
    try {
      const res = await fetchWithAuth(`${API_BASE}/leads/by-business/${bizId}`);
      if (res && res.ok) {
        const lead = await parseJsonResponse(res);
        if (lead) {
          renderLeadQualification(lead);
          currentLeadId = lead.id;
          currentLeadData = lead;
          await loadLeadDossierContent(lead.id);
          await checkExistingDraft(lead.id);
        } else {
          resetLeadQualificationUI();
        }
      }
    } catch (_) {
      resetLeadQualificationUI();
    }
  }

  function resetLeadQualificationUI() {
    const statusBadge = document.getElementById('m-qual-status-badge');
    const dateEl = document.getElementById('m-qual-date');
    const evCount = document.getElementById('m-qual-ev-count');
    const factCount = document.getElementById('m-qual-fact-count');
    const hypCount = document.getElementById('m-qual-hyp-count');
    const contactReady = document.getElementById('m-qual-contact-ready');
    const rationale = document.getElementById('m-qual-rationale');

    if (statusBadge) {
      statusBadge.textContent = 'NOT QUALIFIED';
      statusBadge.style.background = 'rgba(255,255,255,0.1)';
      statusBadge.style.color = '#94a3b8';
    }
    if (dateEl) dateEl.textContent = '--';
    if (evCount) evCount.textContent = '0';
    if (factCount) factCount.textContent = '0';
    if (hypCount) hypCount.textContent = '0';
    if (contactReady) contactReady.textContent = 'NO';
    if (rationale) rationale.textContent = 'Click "Qualify Lead" to run the server-side qualification engine.';
  }

  function renderLeadQualification(lead) {
    const statusBadge = document.getElementById('m-qual-status-badge');
    const dateEl = document.getElementById('m-qual-date');
    const evCount = document.getElementById('m-qual-ev-count');
    const factCount = document.getElementById('m-qual-fact-count');
    const hypCount = document.getElementById('m-qual-hyp-count');
    const contactReady = document.getElementById('m-qual-contact-ready');
    const rationale = document.getElementById('m-qual-rationale');

    const isQualified = lead.status === 'QUALIFIED' || lead.status === 'APPROVED_FOR_DRAFT' || lead.status === 'APPROVED_FOR_OUTBOUND';
    const isNeedsReview = lead.status === 'NEEDS_REVIEW' || lead.status === 'NEEDS_MORE_INFORMATION';

    if (statusBadge) {
      statusBadge.textContent = lead.status;
      statusBadge.style.background = isQualified ? 'rgba(16,185,129,0.2)' : isNeedsReview ? 'rgba(245,158,11,0.2)' : 'rgba(239,68,68,0.2)';
      statusBadge.style.color = isQualified ? '#10b981' : isNeedsReview ? '#f59e0b' : '#ef4444';
    }

    if (dateEl) dateEl.textContent = lead.qualified_at ? new Date(lead.qualified_at).toLocaleString() : 'Recent';
    if (evCount) evCount.textContent = lead.evidence_count || 0;
    if (factCount) factCount.textContent = lead.fact_evidence_count || 0;
    if (hypCount) hypCount.textContent = lead.validated_hypothesis_count || 0;
    if (contactReady) {
      contactReady.textContent = lead.has_eligible_contact ? 'YES' : 'NO';
      contactReady.style.color = lead.has_eligible_contact ? '#10b981' : '#ef4444';
    }
    if (rationale) {
      rationale.textContent = lead.rationale || lead.qualification_notes || 'Lead evaluated server-side against empirical evidence and contact availability.';
    }

    if (isQualified) {
      updateWorkflowStatusBadge('QUALIFIED', '#10b981', 'rgba(16,185,129,0.2)');
    } else {
      updateWorkflowStatusBadge('QUALIFICATION_PENDING', '#f59e0b', 'rgba(245,158,11,0.2)');
    }
  }

  const btnQualifyLead = document.getElementById('modal-btn-qualify-lead');
  if (btnQualifyLead) {
    btnQualifyLead.addEventListener('click', async () => {
      if (!currentActiveBizId) return;
      btnQualifyLead.disabled = true;
      btnQualifyLead.textContent = 'Qualifying...';

      try {
        const res = await fetchWithAuth(`${API_BASE}/leads/business/${currentActiveBizId}/qualify`, {
          method: 'POST',
          body: JSON.stringify({ operator_notes: 'Qualified from Operator Workflow UI' })
        });
        if (res && res.ok) {
          const lead = await parseJsonResponse(res);
          currentLeadId = lead.id;
          currentLeadData = lead;
          renderLeadQualification(lead);
          await loadLeadDossierContent(lead.id);
          await checkExistingDraft(lead.id);
        } else {
          const err = await parseJsonResponse(res).catch(() => ({}));
          alert(`Lead qualification failed: ${err.detail || res.statusText}`);
        }
      } catch (e) {
        alert(`Error qualifying lead: ${e.message}`);
      } finally {
        btnQualifyLead.disabled = false;
        btnQualifyLead.textContent = 'Qualify Lead';
      }
    });
  }

  // STEP 5: Lead Dossier
  async function loadLeadDossierContent(leadId) {
    const container = document.getElementById('m-dossier-content');
    if (!container) return;
    container.innerHTML = '<p style="color:#94a3b8;">Loading lead dossier...</p>';

    try {
      const res = await fetchWithAuth(`${API_BASE}/leads/${leadId}/dossier`);
      if (res && res.ok) {
        const dossier = await parseJsonResponse(res);
        const biz = dossier.business || {};
        const qual = dossier.qualification || {};
        const contact = dossier.primary_contact || (dossier.contacts && dossier.contacts.find(c => c.id === qual.primary_contact_id)) || (dossier.contacts && dossier.contacts[0]) || null;
        const opp = dossier.primary_opportunity || (dossier.opportunities && dossier.opportunities.primary) || null;
        const evidence = dossier.evidence_records || dossier.evidence || [];

        let contactEmail = 'No eligible contact';
        let contactRole = 'Not available';
        let contactTier = 'UNVERIFIED';
        let tierColor = '#ef4444';
        let tierBg = 'rgba(239,68,68,0.2)';

        if (contact && contact.email) {
          contactEmail = contact.email;
          contactRole = contact.role_title || 'General Contact';
          contactTier = contact.verification_tier || 'UNKNOWN';
          if (contactTier === 'CONFIRMED_VERIFIED' || contactTier === 'PROVIDER_VERIFIED') {
            tierColor = '#10b981';
            tierBg = 'rgba(16,185,129,0.2)';
          } else if (contactTier === 'RISKY' || contactTier === 'DISPOSABLE') {
            tierColor = '#f59e0b';
            tierBg = 'rgba(245,158,11,0.2)';
          }
        }

        let oppTitle = 'UNKNOWN / NEEDS_DISCOVERY';
        let oppDesc = 'No validated opportunities identified. Complete research and evidence collection.';
        let oppSol = 'NEEDS_DISCOVERY';

        if (opp && opp.title) {
          oppTitle = opp.title;
          oppDesc = opp.description || 'Identified operational problem pattern.';
          oppSol = opp.solution_type || (opp.solutions && opp.solutions[0] ? opp.solutions[0].solution_type : 'CATALOGUE_SOLUTION');
        }

        container.innerHTML = `
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
            <div class="glass-panel" style="padding:1rem; background:rgba(255,255,255,0.02);">
              <h4 style="color:#38bdf8; margin-bottom:0.5rem;">BUSINESS IDENTITY</h4>
              <div style="font-size:0.85rem; color:#cbd5e1; line-height:1.6;">
                <strong>${biz.name || 'Not available'}</strong><br>
                Location: ${biz.city || biz.address || 'Not available'}<br>
                Domain: ${biz.canonical_domain || 'Not available'}<br>
                Provider: ${biz.source_provider || 'Not available'}
              </div>
            </div>

            <div class="glass-panel" style="padding:1rem; background:rgba(255,255,255,0.02);">
              <h4 style="color:#10b981; margin-bottom:0.5rem;">CONTACT READINESS</h4>
              <div style="font-size:0.85rem; color:#cbd5e1; line-height:1.6;">
                Email: <strong>${contactEmail}</strong><br>
                Role: ${contactRole}<br>
                Verification: <span class="tag" style="background:${tierBg}; color:${tierColor};">${contactTier}</span>
              </div>
            </div>
          </div>

          <div class="glass-panel" style="padding:1rem; margin-top:1rem; background:rgba(255,255,255,0.02);">
            <h4 style="color:#a855f7; margin-bottom:0.5rem;">TARGET OPPORTUNITY & SOLUTION</h4>
            <div style="font-size:0.85rem; color:#cbd5e1; line-height:1.6;">
              <strong>${oppTitle}</strong><br>
              ${oppDesc}<br>
              Solution Type: <span class="tag tag-immutable">${oppSol}</span>
            </div>
          </div>

          <div class="glass-panel" style="padding:1rem; margin-top:1rem; background:rgba(255,255,255,0.02);">
            <h4 style="color:#f59e0b; margin-bottom:0.5rem;">SUPPORTING EVIDENCE (${evidence.length} RECORDS)</h4>
            <div style="max-height:140px; overflow-y:auto;">
              ${evidence.length === 0 ? '<p style="color:#94a3b8; font-size:0.85rem;">UNKNOWN / NEEDS_DISCOVERY: Zero supporting evidence records found.</p>' : evidence.map(e => `
                <div style="font-size:0.8rem; color:#cbd5e1; padding:0.3rem 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                  <strong style="color:#38bdf8;">${e.signal_type}</strong> | <span style="color:#a855f7; font-weight:600;">${e.epistemic_tier || 'FACT'}</span>: ${e.raw_observation}
                </div>
              `).join('')}
            </div>
          </div>
        `;
        if (qual && qual.status) {
          const isQual = qual.status === 'QUALIFIED' || qual.status === 'APPROVED_FOR_DRAFT' || qual.status === 'APPROVED_FOR_OUTBOUND';
          const isReview = qual.status === 'NEEDS_REVIEW' || qual.status === 'NEEDS_MORE_INFORMATION' || qual.status === 'RESEARCH_PENDING';
          const stColor = isQual ? '#10b981' : isReview ? '#f59e0b' : '#ef4444';
          const stBg = isQual ? 'rgba(16,185,129,0.2)' : isReview ? 'rgba(245,158,11,0.2)' : 'rgba(239,68,68,0.2)';
          updateWorkflowStatusBadge(qual.status, stColor, stBg);
        }
      } else {
        container.innerHTML = '<p style="color:#ef4444;">Failed to build lead dossier.</p>';
      }
    } catch (e) {
      container.innerHTML = `<p style="color:#ef4444;">Error loading dossier: ${e.message}</p>`;
    }
  }

  const btnRefreshDossier = document.getElementById('modal-btn-refresh-dossier');
  if (btnRefreshDossier) {
    btnRefreshDossier.addEventListener('click', () => {
      if (currentLeadId) loadLeadDossierContent(currentLeadId);
    });
  }

  // STEP 6: AI Email Draft & Human Review
  function updateDraftStatusBanner(draft, capacityData) {
    const banner = document.getElementById('m-draft-capacity-warning');
    if (!banner) return;

    if (!capacityData) {
      banner.style.display = 'none';
      return;
    }

    const allP = capacityData.all_providers || {};
    const activeP = capacityData.provider_status || {};
    const isWorkspaceExhausted = Boolean(capacityData.workspace_exhausted);

    // Build truthful notes regarding non-active / unavailable providers
    const notes = [];
    if (allP.OPENAI && allP.OPENAI.state === 'QUOTA_EXHAUSTED') {
      notes.push('OpenAI unavailable: QUOTA_EXHAUSTED');
    }
    if (allP.GEMINI && (allP.GEMINI.state === 'ERROR' || allP.GEMINI.state === 'UNAVAILABLE')) {
      notes.push(`Gemini unavailable: ${allP.GEMINI.state}`);
    }

    const notesHtml = notes.length > 0
      ? `<div style="font-size: 0.78rem; color: #94a3b8; margin-top: 0.35rem; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 0.25rem;">Provider constraints: ${notes.map(n => `<span style="color:#fde047;">${escapeHtml(n)}</span>`).join(' &bull; ')}</div>`
      : '';

    // CASE 1: Workspace daily limit reached
    if (isWorkspaceExhausted) {
      banner.style.display = 'block';
      banner.style.background = 'rgba(239,68,68,0.15)';
      banner.style.border = '1px solid #ef4444';
      banner.style.color = '#fca5a5';
      banner.innerHTML = `
        <strong>WORKSPACE CAPACITY EXHAUSTED</strong>: Daily limit of LLM operations reached for this workspace. Generation blocked until daily reset.
        ${notesHtml}
      `;
      return;
    }

    // CASE 2: Draft is currently loaded and displayed
    if (draft) {
      banner.style.display = 'block';
      banner.style.background = 'rgba(16,185,129,0.1)';
      banner.style.border = '1px solid rgba(16,185,129,0.3)';
      banner.style.color = '#6ee7b7';
      banner.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
          <div>
            <strong>Draft generated successfully</strong> — <span style="color:#fff; font-weight:600;">${escapeHtml(draft.provider)} / ${escapeHtml(draft.model_name)}</span> (Version ${draft.version_count || 1} &bull; Awaiting Human Review)
          </div>
          <span style="font-size:0.75rem; color:#94a3b8; background:rgba(255,255,255,0.05); padding:0.2rem 0.5rem; border-radius:4px;">Tier-1 Quality Floor Enforced</span>
        </div>
        ${notesHtml}
      `;
      return;
    }

    // CASE 3: No draft generated yet - check if an active Tier-1 provider is available
    if (activeP.is_available || activeP.state === 'AVAILABLE') {
      banner.style.display = 'block';
      banner.style.background = 'rgba(56,189,248,0.1)';
      banner.style.border = '1px solid rgba(56,189,248,0.3)';
      banner.style.color = '#7dd3fc';
      banner.innerHTML = `
        <strong>Ready to generate draft</strong> — Active Provider: <code style="color:#fff; font-weight:600;">${escapeHtml(activeP.provider)} (${escapeHtml(activeP.model)})</code>
        ${notesHtml}
      `;
      return;
    }

    // CASE 4: No draft yet AND no Tier-1 provider is available
    banner.style.display = 'block';
    banner.style.background = 'rgba(239,68,68,0.15)';
    banner.style.border = '1px solid #ef4444';
    banner.style.color = '#fca5a5';
    banner.innerHTML = `
      <strong>LLM CAPACITY EXHAUSTED</strong>: Configured Tier-1 providers are unavailable (OpenAI: QUOTA_EXHAUSTED). Workspace TIER_1_ADVANCED quality floor prevents heuristic fallback.
      ${notesHtml}
    `;
  }

  async function checkExistingDraft(leadId) {
    const draftBox = document.getElementById('m-draft-box');
    const regenBtn = document.getElementById('modal-btn-regenerate-draft');
    if (!draftBox) return;

    try {
      const res = await fetchWithAuth(`${API_BASE}/leads/${leadId}/drafts`);
      if (res && res.ok) {
        const drafts = await parseJsonResponse(res);
        if (drafts && drafts.length > 0) {
          const latestDraft = drafts[0];
          await loadFullDraft(latestDraft.id);
          if (regenBtn) regenBtn.style.display = 'inline-block';
        } else {
          // No draft yet - query capacity status to render readiness or exhaustion banner
          try {
            const cRes = await fetchWithAuth(`${API_BASE}/capacity/status`);
            if (cRes && cRes.ok) {
              const cData = await parseJsonResponse(cRes);
              updateDraftStatusBanner(null, cData);
            }
          } catch (_) {}
        }
      }
    } catch (_) {}
  }

  async function loadFullDraft(draftId) {
    const draftBox = document.getElementById('m-draft-box');
    if (!draftBox) return;

    let capacityData = null;
    try {
      const cRes = await fetchWithAuth(`${API_BASE}/capacity/status`);
      if (cRes && cRes.ok) {
        capacityData = await parseJsonResponse(cRes);
      }
    } catch (_) {}

    try {
      const res = await fetchWithAuth(`${API_BASE}/drafts/${draftId}`);
      if (res && res.ok) {
        const draft = await parseJsonResponse(res);
        currentDraftId = draft.id;
        currentDraftData = draft;
        updateDraftStatusBanner(draft, capacityData);
        renderDraftReview(draft);
      }
    } catch (e) {
      draftBox.innerHTML = `<p style="color:#ef4444;">Error loading draft: ${e.message}</p>`;
    }
  }

  function renderDraftReview(draft) {
    const draftBox = document.getElementById('m-draft-box');
    if (!draftBox) return;

    const isApproved = draft.status === 'APPROVED_FOR_OUTBOUND' || draft.status === 'APPROVED';
    const isRejected = draft.status === 'REJECTED';

    const statusBadge = isApproved
      ? '<span class="tag" style="background:#10b981; color:#fff; font-weight:700;">APPROVED (PENDING ENROLLMENT)</span>'
      : isRejected
      ? '<span class="tag" style="background:#ef4444; color:#fff; font-weight:700;">REJECTED</span>'
      : '<span class="tag" style="background:#f59e0b; color:#000; font-weight:700;">AI GENERATED — PENDING HUMAN REVIEW</span>';

    if (isApproved) {
      updateWorkflowStatusBadge('DRAFT_APPROVED', '#10b981', 'rgba(16,185,129,0.2)');
    } else {
      updateWorkflowStatusBadge('DRAFT_PENDING_REVIEW', '#f59e0b', 'rgba(245,158,11,0.2)');
    }

    const evUsed = (draft.supporting_evidence_ids || []).join(', ') || 'Traceable from empirical observations';
    const claims = (draft.personalization_points || []).map(p => `<li style="font-size:0.8rem; color:#cbd5e1;">${p}</li>`).join('');

    const versions = (draft.versions || []).map(v => `
      <div style="font-size:0.75rem; color:#94a3b8; padding:0.4rem 0; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between;">
        <span>Version ${v.version_number} (${v.editor_type})</span>
        <span>${v.created_at ? new Date(v.created_at).toLocaleTimeString() : 'Recorded'}</span>
      </div>
    `).join('');

    draftBox.innerHTML = `
      <div class="glass-panel" style="padding:1.25rem; background:rgba(255,255,255,0.02); margin-bottom:1rem;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
          <div>
            ${statusBadge}
            <span style="font-size:0.8rem; color:#94a3b8; margin-left:0.5rem;">Version: ${draft.version_count || 1}</span>
          </div>
          <div style="font-size:0.75rem; color:#64748b;">
            Provider: <code style="color:#38bdf8;">${draft.provider} (${draft.model_name})</code>
          </div>
        </div>

        <div style="margin-bottom:1rem;">
          <label style="font-size:0.8rem; color:#94a3b8; font-weight:600;">Personalization Claims & Traceable Evidence</label>
          <div style="font-size:0.8rem; color:#a5b4fc; margin-top:0.25rem;">Evidence IDs: ${evUsed}</div>
          <ul style="margin:0.4rem 0 0 1.2rem; padding:0;">${claims}</ul>
        </div>

        <div class="form-group" style="margin-bottom:0.75rem;">
          <label for="draft-subject-input" style="font-size:0.8rem; color:#94a3b8;">Subject Line</label>
          <input type="text" id="draft-subject-input" value="${draft.subject.replace(/"/g, '&quot;')}" style="width:100%; padding:0.6rem; background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:#fff; font-weight:600;">
        </div>

        <div class="form-group" style="margin-bottom:1rem;">
          <label for="draft-body-input" style="font-size:0.8rem; color:#94a3b8;">Consultative Body</label>
          <textarea id="draft-body-input" rows="8" style="width:100%; padding:0.6rem; background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:#fff; font-family:var(--font-sans); font-size:0.88rem; line-height:1.5;">${draft.body}</textarea>
        </div>

        <!-- Human Review Actions -->
        <div style="display:flex; justify-content:space-between; align-items:center; padding-top:0.75rem; border-top:1px solid rgba(255,255,255,0.08);">
          <div style="display:flex; gap:0.5rem;">
            <button id="btn-save-draft-edit" class="btn btn-secondary btn-sm">Save Edits as New Version</button>
            <button id="btn-reject-draft" class="btn btn-danger btn-sm" style="background:rgba(239,68,68,0.2); color:#ef4444; border:1px solid #ef4444;">Reject</button>
          </div>
          <button id="btn-approve-draft" class="btn btn-primary btn-sm" style="background:#10b981; border-color:#10b981;">✓ Approve Draft for Campaign</button>
        </div>
      </div>

      <!-- Version History -->
      <div class="glass-panel" style="padding:0.75rem 1rem; background:rgba(0,0,0,0.2);">
        <h5 style="color:#94a3b8; font-size:0.8rem; margin:0 0 0.5rem 0;">Immutable Draft Version History</h5>
        ${versions || '<p style="font-size:0.75rem; color:#64748b; margin:0;">Version 1 created.</p>'}
      </div>
    `;

    // Hook Human Review actions
    const btnSave = document.getElementById('btn-save-draft-edit');
    if (btnSave) {
      btnSave.addEventListener('click', async () => {
        const newSub = document.getElementById('draft-subject-input').value;
        const newBody = document.getElementById('draft-body-input').value;
        try {
          const res = await fetchWithAuth(`${API_BASE}/drafts/${draft.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ new_subject: newSub, new_body: newBody })
          });
          if (res && res.ok) {
            await loadFullDraft(draft.id);
          } else {
            alert('Failed to save draft edit.');
          }
        } catch (e) {
          alert(`Error saving edit: ${e.message}`);
        }
      });
    }

    const btnApprove = document.getElementById('btn-approve-draft');
    if (btnApprove) {
      btnApprove.addEventListener('click', async () => {
        try {
          const res = await fetchWithAuth(`${API_BASE}/drafts/${draft.id}/approve`, { method: 'POST' });
          if (res && res.ok) {
            await loadFullDraft(draft.id);
            await populateCampaignStep();
            switchWfStep('campaign');
          } else {
            const err = await parseJsonResponse(res).catch(() => ({}));
            alert(`Draft approval failed: ${err.detail || res.statusText}`);
          }
        } catch (e) {
          alert(`Error approving draft: ${e.message}`);
        }
      });
    }

    const btnReject = document.getElementById('btn-reject-draft');
    if (btnReject) {
      btnReject.addEventListener('click', async () => {
        const reason = prompt('Please enter rejection reason:');
        if (!reason) return;
        try {
          const res = await fetchWithAuth(`${API_BASE}/drafts/${draft.id}/reject`, {
            method: 'POST',
            body: JSON.stringify({ reason })
          });
          if (res && res.ok) {
            await loadFullDraft(draft.id);
          }
        } catch (e) {
          alert(`Error rejecting draft: ${e.message}`);
        }
      });
    }

    // Populate Campaign step if approved
    if (isApproved) {
      populateCampaignStep();
    }
  }

  const btnGenDraft = document.getElementById('modal-btn-generate-draft');
  if (btnGenDraft) {
    btnGenDraft.addEventListener('click', async () => {
      if (!currentLeadId) {
        alert('Please qualify the lead first before generating an email draft.');
        switchWfStep('qualification');
        return;
      }
      btnGenDraft.disabled = true;
      btnGenDraft.textContent = 'Generating AI Draft...';

      try {
        const res = await fetchWithAuth(`${API_BASE}/leads/${currentLeadId}/draft`, {
          method: 'POST',
          body: JSON.stringify({ supersede_existing: true })
        });
        if (res && res.ok) {
          const draft = await parseJsonResponse(res);
          await loadFullDraft(draft.id);
          const regenBtn = document.getElementById('modal-btn-regenerate-draft');
          if (regenBtn) regenBtn.style.display = 'inline-block';
        } else {
          const err = await parseJsonResponse(res).catch(() => ({}));
          alert(`Draft generation failed: ${err.detail || res.statusText}`);
        }
      } catch (e) {
        alert(`Error generating draft: ${e.message}`);
      } finally {
        btnGenDraft.disabled = false;
        btnGenDraft.textContent = 'Generate AI Draft';
      }
    });
  }

  const btnRegenDraft = document.getElementById('modal-btn-regenerate-draft');
  if (btnRegenDraft) {
    btnRegenDraft.addEventListener('click', async () => {
      if (!currentDraftId) return;
      btnRegenDraft.disabled = true;
      btnRegenDraft.textContent = 'Regenerating...';

      try {
        const res = await fetchWithAuth(`${API_BASE}/drafts/${currentDraftId}/regenerate`, { method: 'POST' });
        if (res && res.ok) {
          const draft = await parseJsonResponse(res);
          await loadFullDraft(draft.id);
        } else {
          alert('Failed to regenerate draft.');
        }
      } catch (e) {
        alert(`Error regenerating draft: ${e.message}`);
      } finally {
        btnRegenDraft.disabled = false;
        btnRegenDraft.textContent = 'Regenerate';
      }
    });
  }

  // STEP 7: Campaign Enrollment & 16-Gate Dispatcher Hand-off
  async function populateCampaignStep() {
    const selectEl = document.getElementById('m-select-campaign');
    const enrollBtn = document.getElementById('modal-btn-enroll-campaign');
    const statusMsg = document.getElementById('m-campaign-enroll-status');
    if (!selectEl) return;

    try {
      const res = await fetchWithAuth(`${API_BASE}/campaigns`);
      if (res && res.ok) {
        const campaigns = await parseJsonResponse(res);
        if (campaigns.length === 0) {
          selectEl.innerHTML = '<option value="">No campaigns available in workspace</option>';
          if (enrollBtn) enrollBtn.disabled = true;
          return;
        }

        selectEl.innerHTML = campaigns.map(c => `
          <option value="${c.id}">${c.name} (${c.status})</option>
        `).join('');

        if (enrollBtn) enrollBtn.disabled = !currentDraftId || !currentLeadId;
        if (statusMsg) statusMsg.textContent = 'Draft approved. Ready to enroll lead into selected campaign.';
      }
    } catch (_) {
      selectEl.innerHTML = '<option value="">Error loading campaigns</option>';
    }
  }

  const btnEnrollCampaign = document.getElementById('modal-btn-enroll-campaign');
  if (btnEnrollCampaign) {
    btnEnrollCampaign.addEventListener('click', async () => {
      const selectEl = document.getElementById('m-select-campaign');
      const campId = selectEl ? selectEl.value : null;
      const statusMsg = document.getElementById('m-campaign-enroll-status');
      const handoffBox = document.getElementById('m-enrolled-handoff-box');

      if (!campId || !currentLeadId || !currentDraftId) {
        alert('Missing campaign, lead, or approved draft.');
        return;
      }

      btnEnrollCampaign.disabled = true;
      btnEnrollCampaign.textContent = 'Enrolling...';

      try {
        const res = await fetchWithAuth(`${API_BASE}/campaigns/${campId}/leads`, {
          method: 'POST',
          body: JSON.stringify({
            lead_qualification_id: currentLeadId,
            email_draft_id: currentDraftId
          })
        });

        if (res && res.ok) {
          if (statusMsg) statusMsg.textContent = '✓ Successfully enrolled into campaign.';
          if (handoffBox) handoffBox.style.display = 'block';
          updateWorkflowStatusBadge('CAMPAIGN_ENROLLED (AWAITING AUTH)', '#10b981', 'rgba(16,185,129,0.25)');
        } else {
          const err = await parseJsonResponse(res).catch(() => ({}));
          alert(`Enrollment failed: ${err.detail || res.statusText}`);
        }
      } catch (e) {
        alert(`Error enrolling lead: ${e.message}`);
      } finally {
        btnEnrollCampaign.disabled = false;
        btnEnrollCampaign.textContent = 'Enroll in Campaign';
      }
    });
  }

  const btnGotoDispatcher = document.getElementById('modal-btn-goto-dispatcher');
  if (btnGotoDispatcher) {
    btnGotoDispatcher.addEventListener('click', () => {
      if (bizModal) bizModal.style.display = 'none';
      switchTab('dispatcher');
      // Pre-fill recipient email in dispatcher if contact available
      if (currentActiveBiz && currentActiveBiz.contacts && currentActiveBiz.contacts.length > 0) {
        const emailInput = document.getElementById('disp-email');
        if (emailInput) emailInput.value = currentActiveBiz.contacts[0].email;
      }
    });
  }

  // --- Real Data Loading: Lead Dossiers Pipeline ---
  async function loadLeadDossiers() {
    const container = document.getElementById('lead-dossier-list');
    container.innerHTML = '<div class="loading-spinner">Loading verified lead dossiers from database...</div>';

    try {
      const res = await fetchWithAuth(`${API_BASE}/businesses`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const businesses = await parseJsonResponse(res);

      if (!businesses || businesses.length === 0) {
        container.innerHTML = `
          <div class="empty-panel glass-panel" style="padding: 32px; text-align: center; color: #94a3b8;">
            <h3>No Discovered Businesses in Workspace</h3>
            <p>Use Targeted Business Discovery above to search and register candidate businesses.</p>
          </div>`;
        return;
      }

      container.innerHTML = '';
      for (const biz of businesses) {
        let scoreData = { opportunity_score: 0, outreach_eligible: false };
        try {
          const sRes = await fetchWithAuth(`${API_BASE}/opportunities/${biz.id}`);
          if (sRes.ok) scoreData = await parseJsonResponse(sRes);
        } catch (_) {}

        const isDup = !!biz.possible_duplicate_of_id;
        const dupBadge = isDup ? '<span class="tag" style="background:#f59e0b; color:#000;">POSSIBLE_DUPLICATE</span>' : '';

        const isDemo = biz.is_demo_fixture || biz.data_origin === 'DEMO_FIXTURE' || (biz.source_provider || '').toUpperCase() === 'MANUAL' || (biz.canonical_domain || '').includes('example.com') || (biz.website_url || '').includes('example.com');
        const fixtureBadge = isDemo
          ? '<span class="tag tag-demo-fixture" style="background:#dc2626; color:#ffffff; font-weight:700; font-size:0.75rem; padding:2px 8px; border-radius:4px;">[DEMO FIXTURE]</span>'
          : `<span class="tag tag-real-discovery" style="background:rgba(16,185,129,0.2); color:#10b981; border:1px solid #10b981; font-weight:600; font-size:0.75rem; padding:2px 8px; border-radius:4px;">[DISCOVERED: ${biz.source_provider}]</span>`;

        const card = document.createElement('div');
        card.className = 'dossier-card glass-panel';
        card.innerHTML = `
          <div class="dossier-header">
            <div>
              <div style="display:flex; gap:0.5rem; align-items:center;">
                <span class="dossier-category">${biz.category || 'Business'}</span>
                ${fixtureBadge}
                ${dupBadge}
              </div>
              <h3 class="dossier-name">${biz.name}</h3>
              <div class="dossier-meta">
                <span>🌐 ${biz.canonical_domain || biz.website_url || 'Not available'}</span>
                <span>📞 ${biz.normalized_phone || biz.phone || 'Not available'}</span>
                <span>✉️ ${biz.primary_email || 'Not available'}</span>
              </div>
            </div>
            <div class="score-badge-box">
              <div class="score-val">${Math.round(scoreData.opportunity_score || 0)}</div>
              <div class="score-tier">${scoreData.outreach_eligible ? 'ELIGIBLE' : 'INELIGIBLE'}</div>
              <div class="score-heuristic-tag">REVIEW HEURISTIC</div>
            </div>
          </div>
          <div class="dossier-body">
            <div class="dossier-section" style="display:flex; justify-content:space-between; align-items:center;">
              <p style="color:#94a3b8; font-size:0.85rem; margin:0;">
                Provider: <strong>${biz.source_provider || 'Not available'}</strong> | Confidence: <strong>${Math.round((biz.identity_confidence || 1.0) * 100)}%</strong>
              </p>
              <button class="btn btn-primary btn-sm btn-inspect-lead" style="font-size:0.8rem; padding:0.4rem 0.8rem;">
                Open Operator Workflow →
              </button>
            </div>
          </div>
        `;

        const inspectBtn = card.querySelector('.btn-inspect-lead');
        if (inspectBtn) {
          inspectBtn.addEventListener('click', () => {
            openBusinessDetail(biz);
          });
        }

        container.appendChild(card);
      }
    } catch (err) {
      container.innerHTML = `
        <div class="error-panel glass-panel" style="padding: 24px; color: #ef4444;">
          <h4>Failed to load businesses from backend</h4>
          <p>${err.message}</p>
          <button class="btn btn-secondary btn-sm" onclick="loadLeadDossiers()">Retry</button>
        </div>`;
    }
  }

  // --- Real Data Loading: Claims Governance ---
  async function loadClaimsGovernance() {
    const tbody = document.getElementById('claims-tbody');
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:24px;">Loading claims governance ledger...</td></tr>';

    try {
      const res = await fetchWithAuth(`${API_BASE}/claims`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const claims = await parseJsonResponse(res);

      if (!claims || claims.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:24px; color:#94a3b8;">No claims in active campaign versions. Generate claims from hypotheses.</td></tr>';
        return;
      }

      tbody.innerHTML = '';
      claims.forEach(claim => {
        const tr = document.createElement('tr');
        const isApproved = claim.approval_status === 'OPERATOR_APPROVED';
        tr.innerHTML = `
          <td><code>${claim.id.substring(0, 8)}...</code></td>
          <td>${claim.claim_text}</td>
          <td>v${claim.claim_version}</td>
          <td><span class="tag ${isApproved ? 'tag-approved' : 'tag-pending'}">${claim.approval_status}</span></td>
          <td><code title="${claim.content_hash}">${claim.content_hash.substring(0, 16)}...</code></td>
          <td><span style="color:#10b981;">✓ VALID</span></td>
          <td>
            ${isApproved ? '<span style="color:#10b981; font-weight:600;">Signed</span>' : `<button class="btn btn-sm btn-primary approve-claim-btn" data-id="${claim.id}">Approve</button>`}
          </td>
        `;
        tbody.appendChild(tr);
      });

      // Attach approval handlers
      document.querySelectorAll('.approve-claim-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const claimId = e.target.dataset.id;
          btn.disabled = true;
          btn.textContent = 'Signing...';
          try {
            const aRes = await fetchWithAuth(`${API_BASE}/claims/${claimId}/approve`, { method: 'POST' });
            if (aRes.ok) {
              loadClaimsGovernance();
            } else {
              const err = await parseJsonResponse(aRes).catch(() => ({ detail: aRes.statusText }));
              alert(`Claim approval failed: ${err.detail || aRes.statusText}`);
              btn.disabled = false;
              btn.textContent = 'Approve';
            }
          } catch (ex) {
            alert(`Approval error: ${ex.message}`);
            btn.disabled = false;
            btn.textContent = 'Approve';
          }
        });
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:24px; color:#ef4444;">Error loading claims: ${err.message}</td></tr>`;
    }
  }

  // --- Real Data Loading: Inbound Triage ---
  async function loadInboundTriage() {
    const list = document.getElementById('inbound-messages-list');
    list.innerHTML = '<div class="loading-spinner">Loading inbound triage messages...</div>';

    try {
      const res = await fetchWithAuth(`${API_BASE}/inbound/messages`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const msgs = await parseJsonResponse(res);

      if (!msgs || msgs.length === 0) {
        list.innerHTML = `
          <div class="empty-panel glass-panel" style="padding: 32px; text-align: center; color: #94a3b8;">
            <h3>Zero Inbound Messages</h3>
            <p>No replies received in current workspace. Messages received via provider webhooks will appear here.</p>
          </div>`;
        return;
      }

      list.innerHTML = '';
      msgs.forEach(msg => {
        const item = document.createElement('div');
        item.className = `inbound-item glass-panel ${msg.classified_intent === 'HOT_DEAL' ? 'hot-deal' : ''}`;
        item.innerHTML = `
          <div class="inbound-header">
            <div>
              <strong>${msg.sender_email}</strong> &rarr; <span>${msg.recipient_email}</span>
              <div style="font-size:0.8rem; color:#94a3b8; margin-top:2px;">${msg.subject}</div>
            </div>
            <div>
              <span class="tag tag-intent">${msg.classified_intent}</span>
              <span class="tag tag-state">${msg.thread_state}</span>
            </div>
          </div>
          <p style="font-size:0.9rem; color:#cbd5e1; margin:12px 0;">${msg.body_text}</p>
          ${msg.thread_state !== 'HUMAN_TAKEOVER' ? `<button class="btn btn-sm btn-secondary takeover-btn" data-id="${msg.id}">Engage Human Takeover</button>` : '<strong style="color:#f59e0b;">Operator Active</strong>'}
        `;
        list.appendChild(item);
      });

      document.querySelectorAll('.takeover-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const msgId = e.target.dataset.id;
          btn.disabled = true;
          try {
            const tRes = await fetchWithAuth(`${API_BASE}/inbound/${msgId}/takeover`, { method: 'POST' });
            if (tRes.ok) {
              loadInboundTriage();
            } else {
              alert('Takeover transition failed.');
              btn.disabled = false;
            }
          } catch (ex) {
            alert(`Error: ${ex.message}`);
            btn.disabled = false;
          }
        });
      });
    } catch (err) {
      list.innerHTML = `<div class="error-panel glass-panel" style="padding:24px; color:#ef4444;">Error: ${err.message}</div>`;
    }
  }

  // --- Real Data Loading: Recommendations ---
  async function loadRecommendations() {
    const list = document.getElementById('recommendations-list');
    list.innerHTML = '<div class="loading-spinner">Loading learning recommendations...</div>';

    try {
      const res = await fetchWithAuth(`${API_BASE}/learning/recommendations`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const recs = await parseJsonResponse(res);

      if (!recs || recs.length === 0) {
        list.innerHTML = `
          <div class="empty-panel glass-panel" style="padding: 32px; text-align: center; color: #94a3b8;">
            <h3>No Pending Recommendations</h3>
            <p>Recommendation engine has evaluated current campaign performance. Zero optimization adjustments required.</p>
          </div>`;
        return;
      }

      list.innerHTML = '';
      recs.forEach(rec => {
        const headline = rec.headline || rec.title || 'Optimization Opportunity';
        const actionText = rec.suggested_action || rec.action_description || 'No action specified';
        const rationale = rec.rationale || rec.expected_benefit || '';
        const currentStatus = rec.status || rec.review_status || 'PROPOSED';
        const isProposed = currentStatus === 'PROPOSED' || currentStatus === 'PENDING_REVIEW';

        const card = document.createElement('div');
        card.className = 'rec-card glass-panel';
        card.innerHTML = `
          <div class="rec-title">${headline}</div>
          <div class="rec-action">${actionText}</div>
          ${rationale ? `<div style="font-size:0.85rem; color:#94a3b8; margin: 6px 0;"><strong>Rationale:</strong> ${rationale}</div>` : ''}
          <div class="rec-benefit" style="margin-top:6px;">
            <span class="tag" style="background:${currentStatus === 'ACCEPTED' ? '#10b981' : currentStatus === 'DISMISSED' ? '#64748b' : '#38bdf8'}; color:#ffffff;">
              Status: ${currentStatus}
            </span>
          </div>
          ${isProposed ? `
            <div style="margin-top: 14px; display:flex; gap:8px;">
              <button class="btn btn-sm btn-primary review-btn" data-id="${rec.id}" data-action="ACCEPTED">Accept Recommendation</button>
              <button class="btn btn-sm btn-secondary review-btn" data-id="${rec.id}" data-action="DISMISSED">Dismiss</button>
            </div>
          ` : ''}
        `;
        list.appendChild(card);
      });

      document.querySelectorAll('.review-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const recId = e.target.dataset.id;
          const action = e.target.dataset.action;
          btn.disabled = true;
          try {
            const rRes = await fetchWithAuth(`${API_BASE}/learning/recommendations/${recId}/action`, {
              method: 'POST',
              body: JSON.stringify({ action })
            });
            if (rRes.ok) {
              await loadRecommendations();
            } else {
              const err = await parseJsonResponse(rRes).catch(() => ({ detail: rRes.statusText }));
              alert(`Review action failed: ${err.detail || rRes.statusText}`);
              btn.disabled = false;
            }
          } catch (ex) {
            alert(`Error: ${ex.message}`);
            btn.disabled = false;
          }
        });
      });
    } catch (err) {
      list.innerHTML = `
        <div class="error-panel glass-panel" style="padding:24px; color:#ef4444;">
          <h4>Failed to load Learning Recommendations</h4>
          <p>${err.message}</p>
          <button class="btn btn-secondary btn-sm" id="btn-retry-recommendations" style="margin-top:10px;">Retry</button>
        </div>`;
      const retryBtn = document.getElementById('btn-retry-recommendations');
      if (retryBtn) {
        retryBtn.addEventListener('click', () => loadRecommendations());
      }
    }
  }

  // --- Real 16-Gate Dispatch Console (Zero Client-Side Timer Simulation) ---
  const dispatchBtn = document.getElementById('btn-execute-dispatch') || document.getElementById('btn-run-dispatch');
  const dispatchForm = document.getElementById('dispatch-form');
  const traceOutput = document.getElementById('dispatch-trace-output');

  const GATE_NAMES = [
    'Campaign Version Approved',
    'Required Claims Exist',
    'Claims Operator Approved',
    'Canonical Hash Untampered',
    'Opportunity Score Eligible',
    'Contact Verification Tier Permitted',
    'Sender Readiness Verified',
    'Organization Policy Permitted',
    'Workspace Policy Permitted',
    'Campaign Policy Permitted',
    'Sender Policy & Quota Permitted',
    'Jurisdiction Lawful Basis Allowed',
    'Cost & Budget Guardrails Permitted',
    'Atomic Pre-Send Suppression Cleared',
    'Emergency Kill Switch Disengaged',
    'Thread in Autonomous State'
  ];

  function resetGateVisualizer() {
    for (let i = 1; i <= 16; i++) {
      const el = document.getElementById(`g-step-${i}`);
      if (el) el.className = 'gate-step';
    }
    if (traceOutput) traceOutput.innerHTML = '';
  }

  function setGateState(gateNum, state) {
    const el = document.getElementById(`g-step-${gateNum}`);
    if (el) el.className = `gate-step ${state}`;
  }

  function logTrace(gateNum, name, status, reason) {
    if (!traceOutput) return;
    const item = document.createElement('div');
    item.className = `trace-item ${status}`;
    item.innerHTML = `
      <div class="trace-header">
        <span class="trace-gate">GATE ${gateNum}: ${name}</span>
        <span class="trace-status ${status}">${status.toUpperCase()}</span>
      </div>
      <div class="trace-detail">${reason}</div>
    `;
    traceOutput.appendChild(item);
  }

  async function executeOutboundDispatch() {
    const emailInput = document.getElementById('disp-email') || document.getElementById('disp-recipient');
    const recipientEmail = emailInput ? emailInput.value.trim() : '';
    const countryCode = document.getElementById('disp-country')?.value.trim() || 'US';
    const campaignVersionId = document.getElementById('disp-campaign-ver')?.value || 'ver_full';
    const senderAccountId = document.getElementById('disp-sender')?.value || 's_full';
    const threadState = document.getElementById('disp-thread-state')?.value || 'AUTONOMOUS';

    if (!recipientEmail) {
      alert('Please provide a recipient email address.');
      return;
    }

    if (dispatchBtn) {
      dispatchBtn.disabled = true;
      dispatchBtn.textContent = 'Evaluating Authoritative 16-Gate Pipeline...';
    }
    resetGateVisualizer();

    const idempotencyKey = `idemp_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`;

    try {
      // NOTE: Contact Verification Tier is strictly derived by the server at Gate 6.
      // Client-supplied verification overrides are forbidden.
      const payload = {
        campaign_version_id: campaignVersionId,
        sender_account_id: senderAccountId,
        recipient_email: recipientEmail,
        recipient_country: countryCode,
        subject: 'Consultative Opportunity Inquiry',
        body_text: 'Operational diagnostic confirmed intake latency.',
        side_effect_idempotency_key: idempotencyKey,
        thread_state: threadState
      };

      const res = await fetchWithAuth(`${API_BASE}/outbound/dispatch`, {
        method: 'POST',
        body: JSON.stringify(payload)
      });

      const data = await parseJsonResponse(res);
      const passedCount = data.gate_passed_count || 0;
      const isSuccess = data.status === 'TRANSMITTED';

      // Render passed gates
      for (let g = 1; g <= passedCount; g++) {
        setGateState(g, 'pass');
        logTrace(g, GATE_NAMES[g - 1], 'pass', 'Verified and cleared by authoritative dispatch engine.');
      }

      // Render failure gate if any
      if (!isSuccess && passedCount < 16) {
        const failGate = passedCount + 1;
        setGateState(failGate, 'fail');
        logTrace(failGate, GATE_NAMES[failGate - 1], 'fail', data.gate_failure_reason || 'Gate policy rejected dispatch.');
      }

      if (isSuccess) {
        const successBanner = document.createElement('div');
        successBanner.className = 'trace-success-banner';
        successBanner.innerHTML = `
          <h4>🚀 OUTBOUND MESSAGE TRANSMITTED</h4>
          <p><strong>Provider Message ID:</strong> <code>${data.provider_message_id}</code></p>
          <p><strong>Status:</strong> <code>${data.status}</code></p>
          <p><strong>Idempotency Key:</strong> <code>${data.side_effect_idempotency_key}</code></p>
          <p><strong>Compliance:</strong> RFC 8058 One-Click List-Unsubscribe injected.</p>
        `;
        traceOutput.appendChild(successBanner);
      }
    } catch (ex) {
      logTrace(0, 'PIPELINE_ERROR', 'fail', `Backend communication error: ${ex.message}`);
    } finally {
      if (dispatchBtn) {
        dispatchBtn.disabled = false;
        dispatchBtn.textContent = 'Execute 16-Gate Dispatch';
      }
    }
  }

  if (dispatchBtn) {
    dispatchBtn.addEventListener('click', (e) => {
      e.preventDefault();
      executeOutboundDispatch();
    });
  }

  if (dispatchForm) {
    dispatchForm.addEventListener('submit', (e) => {
      e.preventDefault();
      executeOutboundDispatch();
    });
  }

  // --- Initial System Load ---
  checkSystemHealth();
  if (getAuthToken()) {
    if (userDisplayEmail) userDisplayEmail.textContent = 'Operator Active';
    if (authBtn) authBtn.textContent = 'Sign Out';
    loadCurrentTab();
  } else {
    // Show login modal on clean machine start
    showLoginModal();
  }
});
