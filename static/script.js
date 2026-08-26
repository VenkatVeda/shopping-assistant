// API Configuration
// Use relative path for Databricks Apps deployment
// When running locally, you can change this to 'http://localhost:8000/api'
const API_BASE_URL = '/api';


// State
let chatHistory = [];
let sessionId = generateSessionId();
let currentProducts = [];  // Store current products for reference
let selectedProductId = null;  // Track selected product
let searchPerformed = false;  // Track if any search has been performed
let wishlistItems = [];       // Cached wishlist from server
let wishlistIds = new Set();  // product_id set for O(1) lookup
let cartItems = [];           // Cached cart from server
let cartIds = new Set();      // product_id set for O(1) lookup
let activeTab = 'products';   // 'products' | 'wishlist' | 'cart'

// Check authentication on page load
document.addEventListener('DOMContentLoaded', () => {
    displayUserInfo();
    loadWishlist();
    loadCart();

    const sendBtn = document.getElementById('sendBtn');
    const clearBtn = document.getElementById('clearBtn');
    const userInput = document.getElementById('userInput');

    sendBtn.addEventListener('click', handleSend);
    clearBtn.addEventListener('click', handleClear);
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleSend();
    });

    // Wire delete-confirm input
    const deleteInp = document.getElementById('deleteConfirmInput');
    if (deleteInp) {
        deleteInp.addEventListener('input', () => {
            document.getElementById('deleteConfirmBtn').disabled = (deleteInp.value.trim() !== 'DELETE');
        });
    }
});

// Get cookie value
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Display user info in header (populates avatar dropdown)
function displayUserInfo() {
    const userName    = getCookie('user_name');
    const userEmail   = getCookie('user_email');
    const userPicture = getCookie('user_picture');

    if (!userName) return;

    const name    = decodeURIComponent(userName);
    const email   = userEmail ? decodeURIComponent(userEmail) : '';
    const picture = (userPicture && userPicture !== 'undefined') ? decodeURIComponent(userPicture) : '';

    // Header avatar button
    const headerUser = document.getElementById('headerUser');
    const headerAvatar = document.getElementById('headerAvatar');
    const headerUserName = document.getElementById('headerUserName');
    if (picture) {
        headerAvatar.src = picture;
        headerAvatar.style.display = 'block';
    } else {
        headerAvatar.style.display = 'none';
    }
    headerUserName.textContent = name.split(' ')[0];
    headerUser.style.display = 'flex';

    // Dropdown header
    const dropdownAvatar = document.getElementById('dropdownAvatar');
    if (picture) { dropdownAvatar.src = picture; dropdownAvatar.style.display = 'block'; }
    else { dropdownAvatar.style.display = 'none'; }
    document.getElementById('dropdownName').textContent  = name;
    document.getElementById('dropdownEmail').textContent = email;
}

// ── Dropdown ──────────────────────────────────────────────────────────────
let _dropdownOpen = false;

function toggleDropdown(e) {
    e.stopPropagation();
    _dropdownOpen ? closeDropdown() : openDropdown();
}

function openDropdown() {
    const btn = document.getElementById('userAvatarBtn');
    const dd  = document.getElementById('userDropdown');
    const rect = btn.getBoundingClientRect();
    dd.style.top   = (rect.bottom + 8) + 'px';
    dd.style.right = (window.innerWidth - rect.right) + 'px';
    dd.style.display = 'block';
    _dropdownOpen = true;
}

function closeDropdown() {
    document.getElementById('userDropdown').style.display = 'none';
    _dropdownOpen = false;
}

document.addEventListener('click', () => { if (_dropdownOpen) closeDropdown(); });

// ── Side Panel (Profile / Preferences) ──────────────────────────────────
function openPanel(type) {
    closeDropdown();
    const panel = document.getElementById('sidePanel');
    const overlay = document.getElementById('sidePanelOverlay');
    const title = document.getElementById('sidePanelTitle');
    const body  = document.getElementById('sidePanelBody');

    if (type === 'profile') {
        title.textContent = '👤 My Profile';
        body.innerHTML = '<div class="panel-loading">Loading…</div>';
        panel.style.display = 'flex';
        overlay.style.display = 'block';
        loadUserProfile(body);
    } else if (type === 'preferences') {
        title.textContent = '🎯 My Preferences';
        body.innerHTML = '<div class="panel-loading">Loading…</div>';
        panel.style.display = 'flex';
        overlay.style.display = 'block';
        loadPreferences(body);
    }
}

function closePanel() {
    document.getElementById('sidePanel').style.display = 'none';
    document.getElementById('sidePanelOverlay').style.display = 'none';
}

async function loadUserProfile(body) {
    try {
        const resp = await fetch(`${API_BASE_URL}/user/profile`, { credentials: 'include' });
        if (!resp.ok) throw new Error('Not authenticated');
        const d = await resp.json();
        body.innerHTML = `
            <div class="profile-avatar-wrap">
                ${d.picture ? `<img src="${d.picture}" class="profile-avatar">` : '<div class="profile-avatar-placeholder">👤</div>'}
            </div>
            <table class="profile-table">
                <tr><td class="profile-label">Name</td><td>${d.name || '—'}</td></tr>
                <tr><td class="profile-label">Email</td><td>${d.email || '—'}</td></tr>
                <tr><td class="profile-label">Country</td><td>${d.country || '—'}</td></tr>
            </table>`;
    } catch (err) {
        body.innerHTML = '<div class="panel-error">Could not load profile.</div>';
    }
}

async function loadPreferences(body) {
    try {
        const resp = await fetch(`${API_BASE_URL}/user/preferences`, { credentials: 'include' });
        if (!resp.ok) throw new Error();
        const d = await resp.json();
        const pref = d.preferences || {};
        const keys = Object.keys(pref).filter(k => pref[k] !== null && pref[k] !== '' && !(Array.isArray(pref[k]) && pref[k].length === 0));
        const rows = keys.length
            ? keys.map(k => `<tr><td class="profile-label">${k}</td><td>${Array.isArray(pref[k]) ? pref[k].join(', ') : pref[k]}</td></tr>`).join('')
            : '<tr><td colspan="2" class="pref-empty">No preferences learned yet.</td></tr>';
        body.innerHTML = `
            <p class="pref-hint">These preferences are learned from your conversations.</p>
            <table class="profile-table">${rows}</table>
            <button class="pref-clear-btn" onclick="clearPreferences()">🗑️ Clear All Preferences</button>
            <div id="prefMsg" class="pref-msg" style="display:none"></div>`;
    } catch {
        body.innerHTML = '<div class="panel-error">Could not load preferences.</div>';
    }
}

async function clearPreferences() {
    const msg = document.getElementById('prefMsg');
    try {
        const resp = await fetch(`${API_BASE_URL}/user/preferences`, { method: 'DELETE', credentials: 'include' });
        if (!resp.ok) throw new Error();
        msg.textContent = 'Preferences cleared.';
        msg.className   = 'pref-msg pref-msg--ok';
        msg.style.display = 'block';
        setTimeout(() => loadPreferences(document.getElementById('sidePanelBody')), 800);
    } catch {
        msg.textContent = 'Failed to clear preferences.';
        msg.className   = 'pref-msg pref-msg--err';
        msg.style.display = 'block';
    }
}

// ── Delete Account Modal ──────────────────────────────────────────────────
function openDeleteModal() {
    closeDropdown();
    document.getElementById('deleteConfirmInput').value = '';
    document.getElementById('deleteConfirmBtn').disabled = true;
    const msg = document.getElementById('deleteModalMsg');
    msg.style.display = 'none';
    document.getElementById('deleteModal').style.display = 'flex';
}

function closeDeleteModal() {
    document.getElementById('deleteModal').style.display = 'none';
}

async function submitDeleteRequest() {
    const msg = document.getElementById('deleteModalMsg');
    msg.style.display = 'none';
    try {
        const resp = await fetch(`${API_BASE_URL}/account/delete-request`, {
            method: 'POST',
            credentials: 'include',
        });
        const d = await resp.json();
        if (!resp.ok) {
            msg.textContent = d.error || 'Request failed.';
            msg.className   = 'modal-msg modal-msg--err';
            msg.style.display = 'block';
            return;
        }
        msg.textContent = `✅ Request received (ID: ${d.request_id}). Your data will be deleted within 30 days. You will now be logged out.`;
        msg.className   = 'modal-msg modal-msg--ok';
        msg.style.display = 'block';
        document.getElementById('deleteConfirmBtn').disabled = true;
        setTimeout(handleLogout, 4000);
    } catch {
        msg.textContent = 'Network error — please try again.';
        msg.className   = 'modal-msg modal-msg--err';
        msg.style.display = 'block';
    }
}

// Handle logout
async function handleLogout() {
    try {
        await fetch(`${API_BASE_URL}/logout`, { method: 'POST' });
        window.location.href = '/login';
    } catch (error) {
        console.error('Logout error:', error);
        window.location.href = '/login';
    }
}

// Generate unique session ID
function generateSessionId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Handle send message
async function handleSend() {
    const userInput = document.getElementById('userInput');
    const query = userInput.value.trim();

    if (!query) return;

    // Add user message to chat
    addMessageToChat('user', query);
    userInput.value = '';

    // Show loading only when NOT already in product discussion mode.
    // If the user is asking a follow-up about a selected product we must NOT
    // wipe the right panel — the answer goes to chat only.
    if (!selectedProductId) {
        showLoading();
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000); // 60-second timeout

    try {
        // Call API
        const response = await fetch(`${API_BASE_URL}/search`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'include',  // Include cookies for authentication
            signal: controller.signal,
            body: JSON.stringify({
                query: query,
                session_id: sessionId
            })
        });

        clearTimeout(timeoutId);

        if (response.status === 401) {
            // Token expired, redirect to login
            window.location.href = '/login';
            return;
        }

        const data = await response.json();

        // Add assistant response
        addMessageToChat('assistant', data.message || 'Found results!');

        // Check if we're in product discussion mode
        const inProductDiscussionMode = data.product_discussion_mode || false;
        const productContext = data.product_context || null;

        if (inProductDiscussionMode && productContext) {
            const wasAlreadyInDiscussionMode = !!selectedProductId;
            selectedProductId = data.selected_product_id;
            if (wasAlreadyInDiscussionMode) {
                // Follow-up question — answer is in chat, panel stays untouched.
                // (showLoading was skipped so nothing to hide either.)
            } else {
                // First entry into discussion mode — render the product panel.
                displaySingleProduct(productContext);
            }
        } else if (data.results && data.results.length > 0) {
            // Display normal results - mark that search was performed
            selectedProductId = null;
            searchPerformed = true;
            displayResults(data.results, data.preferences, data.top_products, data.more_products);
            // Loading will be hidden by displayResults
        } else if (data.preferences) {
            // Search was attempted (has preferences) but no results - show "no results"
            selectedProductId = null;
            searchPerformed = true;
            displayResults([], data.preferences, [], []);
            // Loading will be hidden by displayResults
        } else {
            // Chat message or clarification - hide loading immediately and keep initial screen
            hideLoading();
        }

    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            addMessageToChat('assistant', 'The search is taking longer than expected. Please try again.');
        } else {
            console.error('Error:', error);
            addMessageToChat('assistant', 'Sorry, I encountered an error. Please try again.');
        }
        hideLoading();
    }
}

// Add message to chat
function addMessageToChat(role, content) {
    const chatContainer = document.getElementById('chatContainer');
    const placeholder = chatContainer.querySelector('.chat-placeholder');

    if (placeholder) {
        placeholder.remove();
    }

    const messageDiv = document.createElement('div');
    messageDiv.className = 'chat-message';

    const messageContent = document.createElement('div');
    messageContent.className = `message-${role}`;

    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = role === 'user' ? 'You' : (role === 'system' ? 'System' : 'Assistant');

    const text = document.createElement('div');
    // For assistant and system messages, render markdown; for user messages, use plain text
    if (role === 'assistant' || role === 'system') {
        text.innerHTML = markdownToHtml(content);
    } else {
        text.textContent = content;
    }

    messageContent.appendChild(label);
    messageContent.appendChild(text);
    messageDiv.appendChild(messageContent);

    chatContainer.appendChild(messageDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;

    if (role !== 'system') {
        chatHistory.push({ role, content });
    }
}

// Show loading state
function showLoading() {
    const resultsContainer = document.getElementById('resultsContainer');
    resultsContainer.innerHTML = `
        <div class="loading">
            <div class="loading-spinner"></div>
            <p>Searching for products...</p>
        </div>
    `;
}

// Hide loading state
function hideLoading() {
    const resultsContainer = document.getElementById('resultsContainer');
    const loading = resultsContainer.querySelector('.loading');
    if (loading) {
        loading.remove();
    }
}

// Display results
function displayResults(results, preferences, topProducts = null, moreProducts = null) {
    const resultsContainer = document.getElementById('resultsContainer');
    
    // Use new format if available, otherwise fall back to old format
    const allProducts = results || [];
    const top3 = topProducts || allProducts.slice(0, 3);
    const remaining = moreProducts || allProducts.slice(3);
    
    // Store current products
    currentProducts = allProducts;

    if (!allProducts || allProducts.length === 0) {
        // Only show "no results" message if a search was actually performed
        if (searchPerformed) {
            resultsContainer.innerHTML = `
                <div class="no-results">
                    <div class="no-results-icon">🔍</div>
                    <h3>No products found</h3>
                    <p>Try adjusting your search criteria or browse with a broader query.</p>
                </div>
            `;
        }
        // Otherwise keep the existing content (don't clear the panel)
        return;
    }

    // Build preference summary
    const prefParts = [];
    if (preferences) {
        if (preferences.price_min || preferences.price_max) {
            prefParts.push(`💰 $${preferences.price_min || 0} - $${preferences.price_max || '∞'}`);
        }
        if (preferences.categories && preferences.categories.length > 0) {
            prefParts.push(`📂 ${preferences.categories.join(', ')}`);
        }
        if (preferences.brands && preferences.brands.length > 0) {
            prefParts.push(`🏷️ ${preferences.brands.join(', ')}`);
        }
        if (preferences.colors && preferences.colors.length > 0) {
            prefParts.push(`🎨 ${preferences.colors.join(', ')}`);
        }
        if (preferences.materials && preferences.materials.length > 0) {
            prefParts.push(`✨ ${preferences.materials.join(', ')}`);
        }
    }
    const prefSummary = prefParts.length > 0 ? prefParts.join(' | ') : 'All bags';

    // Build HTML with separated sections
    let html = `
        <div class="results-container">
            <div class="results-header">
                <h2>Top Recommendations</h2>
                <p class="search-criteria">${prefSummary}</p>
            </div>
            <div class="products-grid">
                ${top3.map((product, index) => createProductCard(product, index)).join('')}
            </div>
    `;
    
    // Add "More Products" section if there are additional products
    if (remaining.length > 0) {
        html += `
            <div class="more-products-section">
                <div class="results-header" style="margin-top: 30px;">
                    <h2>More Products (${remaining.length})</h2>
                </div>
                <div class="products-grid">
                    ${remaining.map((product, index) => createProductCard(product, index + 3)).join('')}
                </div>
            </div>
        `;
    }
    
    html += `</div>`;

    resultsContainer.innerHTML = html;
}

// Display single product for discussion mode
function displaySingleProduct(productContext) {
    const resultsContainer = document.getElementById('resultsContainer');

    // Normalise both formats (flat and {metadata:...}) into one flat object
    let p;
    if (productContext && productContext.metadata) {
        const m = productContext.metadata;
        p = {
            id:          productContext.id || m.id || 'unknown',
            name:        m.name        || 'Unknown',
            brand:       m.brand       || 'Unknown',
            price:       m.price       || 0,
            image:       m.primary_image_url || m.image_url || '',
            url:         m.url_full    || m.url || '#',
            bag_style:   m.category    || m.bag_style || '',
            color:       m.primary_color || m.color || '',
            fabrication: m.material_type || m.fabrication || '',
            dimensions:  m.dimensions  || '',
            pattern:     m.pattern     || '',
            gender:      m.gender      || '',
            description: m.long_description_clean || m.description || m.embedding_text || ''
        };
    } else {
        p = { ...(productContext || {}) };
    }

    const imageHtml = p.image
        ? `<img src="${p.image}" alt="${escapeHtml(p.name)}" class="single-product-image"
               onerror="this.onerror=null;this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22400%22 height=%22400%22><rect width=%22400%22 height=%22400%22 fill=%22%23f0f0f0%22/><text x=%2250%25%22 y=%2250%25%22 dominant-baseline=%22middle%22 text-anchor=%22middle%22 font-size=%22120%22>🛍️</text></svg>';">`
        : `<div class="single-product-image-placeholder">🛍️</div>`;

    // Spec rows — only show rows with real values
    const specs = [
        { label: 'Style',      value: formatBagStyle(p.bag_style) },
        { label: 'Color',      value: realValue(p.color) },
        { label: 'Material',   value: realValue(p.fabrication) },
        { label: 'Dimensions', value: realValue(p.dimensions) },
        { label: 'Pattern',    value: realValue(p.pattern) },
        { label: 'Gender',     value: realValue(p.gender) },
    ].filter(s => s.value);

    const specsHtml = specs.length ? `
        <div class="detail-specs">
            ${specs.map(s => `
            <div class="spec-item">
                <span class="spec-label">${s.label}:</span>
                <span class="spec-value">${escapeHtml(s.value)}</span>
            </div>`).join('')}
        </div>` : '';

    // Show full description; convert newlines to <br> for readability
    const descHtml = p.description ? `
        <div class="detail-description">
            <p>${escapeHtml(p.description).replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>')}</p>
        </div>` : '';

    const url = p.url || '#';
    const html = `
        <div class="single-product-detail-view">
            ${currentProducts && currentProducts.length > 0 ? `
            <div class="detail-back-bar">
                <button class="detail-back-btn" onclick="showProductGrid()">← Back to results</button>
            </div>` : ''}
            <div class="detail-content">
                <div class="detail-image-section">${imageHtml}</div>
                <div class="detail-info-section">
                    <h3 class="detail-product-name">${escapeHtml(p.name)}</h3>
                    <p class="detail-product-brand">${escapeHtml(p.brand)}</p>
                    <p class="detail-product-price">$${parseFloat(p.price || 0).toFixed(2)}</p>
                    ${specsHtml}
                    ${descHtml}
                    <a href="${url}" target="_blank" class="detail-view-product-btn"
                       ${url === '#' ? 'style="opacity:0.5;pointer-events:none;"' : ''}>
                        View Full Product Page →
                    </a>
                </div>
            </div>
        </div>
    `;

    resultsContainer.innerHTML = html;
    hideLoading();
}

// Create product card HTML
function createProductCard(product, index) {
    const name = escapeHtml(product.name || 'N/A').substring(0, 80);
    const brand = product.brand || 'N/A';
    const price = product.price || 0;
    const imageUrl = product.image || '';
    const url = product.url || '#';
    const productId = product.id || index;

    // Clean up values — hide anything that is empty/unknown/not-specified
    const style   = formatBagStyle(product.bag_style);
    const color    = realValue(product.color);
    const material = realValue(product.fabrication);

    // Only render badges that have real data
    const badges = [style, color, material]
        .filter(Boolean)
        .map(v => `<span class="detail-badge">${escapeHtml(v)}</span>`)
        .join('');

    const imageHtml = imageUrl
        ? `<img src="${imageUrl}" alt="${name}" class="product-image" onerror="this.onerror=null; this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22><rect width=%22200%22 height=%22200%22 fill=%22%23f0f0f0%22/><text x=%2250%25%22 y=%2250%25%22 dominant-baseline=%22middle%22 text-anchor=%22middle%22 font-size=%2260%22>🛍️</text></svg>';">`
        : `<div class="product-image-placeholder">🛍️</div>`;

    const inWishlist = wishlistIds.has(String(productId));
    const heartIcon = inWishlist ? '♥' : '♡';
    const heartClass = inWishlist ? 'wishlist-heart-btn in-wishlist' : 'wishlist-heart-btn';
    const heartTitle = inWishlist ? 'Remove from wishlist' : 'Add to wishlist';

    const inCart = cartIds.has(String(productId));
    const cartBtnClass = inCart ? 'cart-add-btn in-cart' : 'cart-add-btn';
    const cartBtnTitle = inCart ? 'In cart' : 'Add to cart';
    const cartBtnIcon = inCart ? '🛒✓' : '🛒';

    return `
        <div class="product-card" data-product-id="${productId}">
            <div class="product-image-container" style="position:relative">
                ${imageHtml}
                <div class="product-number">${index + 1}</div>
                <button class="${heartClass}" data-product-id="${productId}" title="${heartTitle}"
                    onclick="toggleWishlistItem(currentProducts[${index}],this);event.stopPropagation()">
                    ${heartIcon}
                </button>
            </div>
            <div class="product-info">
                <h3 class="product-name">${name}</h3>
                <p class="product-brand">${brand}</p>
                <p class="product-price">$${price.toFixed(2)}</p>
                ${badges ? `<div class="product-details">${badges}</div>` : ''}
                <div class="product-actions">
                    <button class="product-discuss-btn" onclick="selectProductForDiscussion(${index}, event)">
                        💬 Ask about this
                    </button>
                    <button class="${cartBtnClass}" data-product-id="${productId}" title="${cartBtnTitle}"
                        onclick="addToCartFromCard(currentProducts[${index}],this);event.stopPropagation()">
                        ${cartBtnIcon}
                    </button>
                </div>
                <a href="${url || '#'}" target="_blank" class="product-link" onclick="event.stopPropagation()" ${!url || url === '#' ? 'style="opacity: 0.5; pointer-events: none;"' : ''}>View Product →</a>
            </div>
        </div>
    `;
}

// ── Wishlist helpers ──────────────────────────────────────────────────────

async function loadWishlist() {
    try {
        const resp = await fetch(`${API_BASE_URL}/wishlist`, { credentials: 'include' });
        if (!resp.ok) return;
        const data = await resp.json();
        wishlistItems = data.items || [];
        wishlistIds = new Set(wishlistItems.map(i => String(i.product_id)));
        updateWishlistBadge();
        if (activeTab === 'wishlist') renderWishlistPanel();
        refreshHeartButtons();
    } catch (_) {}
}

function updateWishlistBadge() {
    const badge = document.getElementById('wishlistBadge');
    if (!badge) return;
    if (wishlistIds.size > 0) {
        badge.textContent = wishlistIds.size;
        badge.style.display = 'inline-block';
    } else {
        badge.style.display = 'none';
    }
    // Update tab label heart
    const tab = document.getElementById('tabWishlist');
    if (tab) tab.childNodes[0].textContent = wishlistIds.size > 0 ? '♥ Wishlist ' : '♡ Wishlist ';
}

function refreshHeartButtons() {
    document.querySelectorAll('.wishlist-heart-btn').forEach(btn => {
        const pid = String(btn.dataset.productId);
        const inList = wishlistIds.has(pid);
        btn.textContent = inList ? '♥' : '♡';
        btn.classList.toggle('in-wishlist', inList);
        btn.title = inList ? 'Remove from wishlist' : 'Add to wishlist';
    });
}

async function toggleWishlistItem(product, btnEl) {
    const pid = String(product.id);
    const inList = wishlistIds.has(pid);

    // Optimistic update
    if (inList) {
        wishlistIds.delete(pid);
        wishlistItems = wishlistItems.filter(i => String(i.product_id) !== pid);
    } else {
        wishlistIds.add(pid);
        wishlistItems.push({
            product_id:   pid,
            product_name: product.name,
            brand:        product.brand,
            price:        product.price,
            image_url:    product.image,
            retailer_url: product.url,
        });
    }
    updateWishlistBadge();
    if (btnEl) {
        btnEl.textContent = wishlistIds.has(pid) ? '♥' : '♡';
        btnEl.classList.toggle('in-wishlist', wishlistIds.has(pid));
    }
    if (activeTab === 'wishlist') renderWishlistPanel();

    // Server sync
    try {
        const method = inList ? 'DELETE' : 'POST';
        const endpoint = inList ? '/wishlist/remove' : '/wishlist/add';
        await fetch(`${API_BASE_URL}${endpoint}`, {
            method,
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                product_id:   pid,
                product_name: product.name,
                brand:        product.brand,
                price:        product.price,
                image_url:    product.image,
                retailer_url: product.url,
            }),
        });
    } catch (err) {
        console.warn('Wishlist sync failed:', err);
    }
}

async function removeWishlistItem(productId) {
    const pid = String(productId);
    wishlistIds.delete(pid);
    wishlistItems = wishlistItems.filter(i => String(i.product_id) !== pid);
    updateWishlistBadge();
    refreshHeartButtons();
    renderWishlistPanel();

    try {
        await fetch(`${API_BASE_URL}/wishlist/remove`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ product_id: pid }),
        });
    } catch (err) {
        console.warn('Wishlist remove failed:', err);
    }
}

function renderWishlistPanel() {
    const container = document.getElementById('wishlistContainer');
    if (!container) return;
    if (wishlistItems.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">♡</div>
                <h3>Your wishlist is empty</h3>
                <p>Tap the heart on any product to save it here</p>
            </div>`;
        return;
    }
    const items = wishlistItems.map((item, idx) => {
        const pid = escapeHtml(String(item.product_id));
        const name = escapeHtml(item.product_name || 'Product');
        const brand = escapeHtml(item.brand || '');
        const price = item.price ? `$${parseFloat(item.price).toFixed(2)}` : '';
        const imgSrc = item.image_url || '';
        const imgEl = imgSrc
            ? `<img src="${imgSrc}" alt="${name}" class="wishlist-item-img" onerror="this.style.display='none'">`
            : `<div class="wishlist-item-img" style="display:flex;align-items:center;justify-content:center;font-size:28px">🛍️</div>`;
        const alreadyInCart = cartIds.has(pid);
        const cartBtnLabel = alreadyInCart ? '🛒✓' : '🛒';
        const cartBtnCls   = alreadyInCart ? 'cart-add-btn in-cart wishlist-cart-btn' : 'cart-add-btn wishlist-cart-btn';
        // Use data-idx to avoid JSON.stringify breaking onclick attribute quotes
        return `
            <div class="wishlist-item">
                ${imgEl}
                <div class="wishlist-item-info">
                    <div class="wishlist-item-name" title="${name}">${name}</div>
                    ${brand ? `<div class="wishlist-item-brand">${brand}</div>` : ''}
                    ${price ? `<div class="wishlist-item-price">${price}</div>` : ''}
                </div>
                <div class="wishlist-item-actions">
                    <button class="${cartBtnCls}" title="Add to cart"
                        data-widx="${idx}" onclick="addToCartFromWishlistByIdx(this)">
                        ${cartBtnLabel}
                    </button>
                    <button class="wishlist-item-remove" title="Remove" onclick="removeWishlistItem('${pid}')">✕</button>
                </div>
            </div>`;
    }).join('');
    container.innerHTML = `<div class="wishlist-panel-header">${wishlistItems.length} saved item${wishlistItems.length !== 1 ? 's' : ''}</div>${items}`;
}

function switchTab(tab) {
    activeTab = tab;
    const products = document.getElementById('resultsContainer');
    const wishlist = document.getElementById('wishlistContainer');
    const cart     = document.getElementById('cartContainer');
    const orders   = document.getElementById('ordersContainer');
    const tabP     = document.getElementById('tabProducts');
    const tabW     = document.getElementById('tabWishlist');
    const tabC     = document.getElementById('tabCart');
    const tabO     = document.getElementById('tabOrders');

    products.style.display = tab === 'products' ? '' : 'none';
    wishlist.style.display = tab === 'wishlist' ? '' : 'none';
    cart.style.display     = tab === 'cart'     ? '' : 'none';
    orders.style.display   = tab === 'orders'   ? '' : 'none';

    tabP.classList.toggle('active', tab === 'products');
    tabW.classList.toggle('active', tab === 'wishlist');
    tabC.classList.toggle('active', tab === 'cart');
    if (tabO) tabO.classList.toggle('active', tab === 'orders');

    if (tab === 'wishlist') renderWishlistPanel();
    if (tab === 'cart')     renderCartPanel();
    if (tab === 'orders')   loadOrders();
}

// ── Cart helpers ──────────────────────────────────────────────────────────

async function loadCart() {
    try {
        const resp = await fetch(`${API_BASE_URL}/cart`, { credentials: 'include' });
        if (!resp.ok) return;
        const data = await resp.json();
        cartItems = data.items || [];
        cartIds   = new Set(cartItems.map(i => String(i.product_id)));
        updateCartBadge();
        if (activeTab === 'cart') renderCartPanel();
        refreshCartButtons();
    } catch (_) {}
}

function updateCartBadge() {
    const badge = document.getElementById('cartBadge');
    if (!badge) return;
    const totalQty = cartItems.reduce((s, i) => s + (parseInt(i.quantity) || 1), 0);
    if (totalQty > 0) {
        badge.textContent = totalQty;
        badge.style.display = 'inline-block';
    } else {
        badge.style.display = 'none';
    }
}

function refreshCartButtons() {
    document.querySelectorAll('.cart-add-btn').forEach(btn => {
        const pid = String(btn.dataset.productId);
        if (!pid) return;
        const inCart = cartIds.has(pid);
        btn.textContent = inCart ? '🛒✓' : '🛒';
        btn.classList.toggle('in-cart', inCart);
        btn.title = inCart ? 'In cart' : 'Add to cart';
    });
}

async function addToCartFromCard(product, btnEl) {
    const pid = String(product.id);
    if (cartIds.has(pid)) {
        // Already in cart — switch to cart tab for visibility
        switchTab('cart');
        return;
    }

    // Optimistic update
    cartIds.add(pid);
    cartItems.push({
        product_id:   pid,
        product_name: product.name,
        brand:        product.brand,
        price:        product.price,
        image_url:    product.image,
        retailer_url: product.url,
        quantity:     1,
    });
    updateCartBadge();
    if (btnEl) {
        btnEl.textContent = '🛒✓';
        btnEl.classList.add('in-cart');
        btnEl.title = 'In cart';
    }
    if (activeTab === 'cart') renderCartPanel();

    try {
        await fetch(`${API_BASE_URL}/cart/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                product_id:   pid,
                product_name: product.name,
                brand:        product.brand,
                price:        product.price,
                image_url:    product.image,
                retailer_url: product.url,
                quantity:     1,
            }),
        });
    } catch (err) {
        console.warn('Cart add failed:', err);
    }
}

function addToCartFromWishlistByIdx(btnEl) {
    const idx  = parseInt(btnEl.getAttribute('data-widx'), 10);
    const item = wishlistItems[idx];
    if (item) addToCartFromWishlist(item, btnEl);
}

async function addToCartFromWishlist(item, btnEl) {
    const pid = String(item.product_id);
    if (cartIds.has(pid)) {
        switchTab('cart');
        return;
    }

    cartIds.add(pid);
    cartItems.push({
        product_id:   pid,
        product_name: item.product_name,
        brand:        item.brand,
        price:        item.price,
        image_url:    item.image_url,
        retailer_url: item.retailer_url,
        quantity:     1,
    });
    updateCartBadge();
    if (btnEl) {
        btnEl.textContent = '🛒✓';
        btnEl.classList.add('in-cart');
    }

    try {
        await fetch(`${API_BASE_URL}/cart/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                product_id:   pid,
                product_name: item.product_name,
                brand:        item.brand,
                price:        item.price,
                image_url:    item.image_url,
                retailer_url: item.retailer_url,
                quantity:     1,
            }),
        });
    } catch (err) {
        console.warn('Cart add (from wishlist) failed:', err);
    }
}

async function removeFromCart(productId) {
    const pid = String(productId);
    cartIds.delete(pid);
    cartItems = cartItems.filter(i => String(i.product_id) !== pid);
    updateCartBadge();
    refreshCartButtons();
    renderCartPanel();

    try {
        await fetch(`${API_BASE_URL}/cart/remove`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ product_id: pid }),
        });
    } catch (err) {
        console.warn('Cart remove failed:', err);
    }
}

async function updateCartQty(productId, delta) {
    const pid  = String(productId);
    const item = cartItems.find(i => String(i.product_id) === pid);
    if (!item) return;

    const newQty = (parseInt(item.quantity) || 1) + delta;
    if (newQty <= 0) {
        await removeFromCart(pid);
        return;
    }

    item.quantity = newQty;
    updateCartBadge();
    renderCartPanel();

    try {
        await fetch(`${API_BASE_URL}/cart/update`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ product_id: pid, quantity: newQty }),
        });
    } catch (err) {
        console.warn('Cart update failed:', err);
    }
}

function renderCartPanel() {
    const container = document.getElementById('cartContainer');
    if (!container) return;

    if (cartItems.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🛒</div>
                <h3>Your cart is empty</h3>
                <p>Add products from the search results or your wishlist</p>
            </div>`;
        return;
    }

    let subtotal = 0;
    const itemsHtml = cartItems.map(item => {
        const pid      = escapeHtml(String(item.product_id));
        const name     = escapeHtml(item.product_name || 'Product');
        const brand    = escapeHtml(item.brand || '');
        const price    = parseFloat(item.price) || 0;
        const qty      = parseInt(item.quantity) || 1;
        const lineTotal = price * qty;
        subtotal += lineTotal;

        const imgSrc = item.image_url || '';
        const imgEl  = imgSrc
            ? `<img src="${imgSrc}" alt="${name}" class="wishlist-item-img" onerror="this.style.display='none'">`
            : `<div class="wishlist-item-img" style="display:flex;align-items:center;justify-content:center;font-size:28px">🛍️</div>`;

        return `
            <div class="cart-item">
                ${imgEl}
                <div class="wishlist-item-info">
                    <div class="wishlist-item-name" title="${name}">${name}</div>
                    ${brand ? `<div class="wishlist-item-brand">${brand}</div>` : ''}
                    <div class="wishlist-item-price">$${price.toFixed(2)}</div>
                </div>
                <div class="cart-item-controls">
                    <div class="cart-qty-controls">
                        <button class="cart-qty-btn" onclick="updateCartQty('${pid}',-1)">−</button>
                        <span class="cart-qty-value">${qty}</span>
                        <button class="cart-qty-btn" onclick="updateCartQty('${pid}',1)">+</button>
                    </div>
                    <div class="cart-line-total">$${lineTotal.toFixed(2)}</div>
                    <button class="wishlist-item-remove" title="Remove" onclick="removeFromCart('${pid}')">✕</button>
                </div>
            </div>`;
    }).join('');

    const totalQty = cartItems.reduce((s, i) => s + (parseInt(i.quantity) || 1), 0);
    container.innerHTML = `
        <div class="wishlist-panel-header">${totalQty} item${totalQty !== 1 ? 's' : ''} in cart</div>
        ${itemsHtml}
        <div class="cart-subtotal">
            <span>Subtotal</span>
            <span>$${subtotal.toFixed(2)}</span>
        </div>
        <div class="checkout-btn-wrap">
            <button class="checkout-btn" onclick="placeOrder()">✅ Checkout — $${subtotal.toFixed(2)}</button>
        </div>`;
}

// ── Orders ────────────────────────────────────────────────────────────────

async function loadOrders() {
    const container = document.getElementById('ordersContainer');
    if (!container) return;
    container.innerHTML = '<div class="panel-loading">Loading orders…</div>';
    try {
        const resp = await fetch(`${API_BASE_URL}/orders`, { credentials: 'include' });
        if (!resp.ok) throw new Error();
        const data = await resp.json();
        renderOrdersPanel(data.orders || []);
    } catch (_) {
        container.innerHTML = '<div class="panel-error">Could not load orders.</div>';
    }
}

function renderOrdersPanel(orders) {
    const container = document.getElementById('ordersContainer');
    if (!container) return;
    if (!orders.length) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">📦</div>
                <h3>No orders yet</h3>
                <p>Complete checkout to see your order history here</p>
            </div>`;
        return;
    }
    const ordersHtml = orders.map(order => {
        const date  = order.placed_at ? new Date(order.placed_at).toLocaleDateString() : '';
        const items = (order.items || []).map(i =>
            `<div class="order-item-row">
                <span class="order-item-name">${escapeHtml(i.product_name || 'Item')}</span>
                <span class="order-item-qty">×${i.quantity}</span>
                <span class="order-item-price">$${parseFloat(i.line_total || 0).toFixed(2)}</span>
            </div>`
        ).join('');
        return `
            <div class="order-card">
                <div class="order-card-header">
                    <span class="order-id">Order #${order.order_id.slice(-8).toUpperCase()}</span>
                    <span class="order-date">${date}</span>
                    <span class="order-status order-status--${order.status}">${order.status}</span>
                </div>
                <div class="order-items">${items}</div>
                <div class="order-total">Total: <strong>$${parseFloat(order.total || 0).toFixed(2)}</strong></div>
            </div>`;
    }).join('');
    container.innerHTML = `<div class="orders-list">${ordersHtml}</div>`;
}

async function placeOrder() {
    const btn = document.querySelector('.checkout-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Placing order…'; }
    try {
        const resp = await fetch(`${API_BASE_URL}/orders/place`, {
            method: 'POST',
            credentials: 'include',
        });
        const d = await resp.json();
        if (!resp.ok) {
            alert(d.error || 'Could not place order.');
            if (btn) { btn.disabled = false; btn.textContent = btn.textContent.replace('Placing order…', '✅ Checkout'); }
            return;
        }
        // Clear local cart state
        cartItems = [];
        cartIds   = new Set();
        updateCartBadge();
        renderCartPanel();
        // Show confirmation modal
        document.getElementById('orderConfirmMsg').textContent =
            `${d.item_count} item${d.item_count !== 1 ? 's' : ''} confirmed. Order total: $${parseFloat(d.order_total).toFixed(2)}. ` +
            `Reference: #${(d.order_id || '').slice(-8).toUpperCase()}`;
        document.getElementById('orderConfirmModal').style.display = 'flex';
    } catch (_) {
        alert('Network error. Please try again.');
        if (btn) { btn.disabled = false; }
    }
}

function closeOrderConfirmModal() {
    document.getElementById('orderConfirmModal').style.display = 'none';
}

// Handle clear chat
function handleClear() {
    const chatContainer = document.getElementById('chatContainer');
    const resultsContainer = document.getElementById('resultsContainer');

    chatContainer.innerHTML = `
        <div class="chat-placeholder">
            👋 Welcome! Ask me to find bags based on your preferences.
        </div>
    `;

    resultsContainer.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">🛍️</div>
            <h3>Start searching</h3>
            <p>Enter your preferences to find the perfect bag</p>
        </div>
    `;

    chatHistory = [];
    sessionId = generateSessionId();
    searchPerformed = false;  // Reset search state
}

// Select product for discussion.
// Shows a loading spinner (no partial card flash), fetches enriched silver-table data,
// then renders the full detail view with image + specs + description in one shot.
// Also sets the backend session into product_discussion_mode so follow-up questions
// are answered about this specific product rather than triggering a new search.
async function selectProductForDiscussion(productIndex, event) {
    event.stopPropagation();

    const product = currentProducts[productIndex];
    if (!product) return;

    const productName  = product.name  || 'Unknown';
    const productBrand = product.brand || 'Unknown';
    // Capture the working image URL from the card NOW — we'll inject it into
    // the enriched product if the backend doesn't return one.
    const cardImageUrl = product.image || '';

    // 1. Show a clean loading state (no "old card" flash).
    const resultsContainer = document.getElementById('resultsContainer');
    resultsContainer.innerHTML = `
        <div class="single-product-detail-view">
            ${currentProducts && currentProducts.length > 0 ? `
            <div class="detail-back-bar">
                <button class="detail-back-btn" onclick="showProductGrid()">← Back to results</button>
            </div>` : ''}
            <div class="loading" style="padding: 80px 0; text-align: center;">
                <div class="loading-spinner"></div>
                <p style="margin-top: 12px; color: #666;">Loading product details…</p>
            </div>
        </div>`;

    // 2. Add neutral chat message.
    addMessageToChat('assistant',
        `Showing details for **${productName}** by **${productBrand}**. ` +
        `Feel free to ask me anything about this product.`
    );

    // 3. Fetch enriched product data from the silver table (sets backend session too).
    try {
        const resp = await fetch(`${API_BASE_URL}/select_product`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ session_id: sessionId, product: product })
        });

        if (resp.ok) {
            const data = await resp.json();
            const enriched = data.enriched_product;
            if (enriched) {
                // Always use the image URL that was already loading correctly in the
                // product card — the silver table URL is unreliable / may be empty.
                enriched.image = cardImageUrl;
                // Set selectedProductId NOW so that follow-up handleSend calls
                // know we are already in product discussion mode and skip
                // showLoading() + panel re-render.
                selectedProductId = String(product.id || productIndex);
                displaySingleProduct(enriched);
            } else {
                // Backend returned ok but no enriched product — fall back to card data.
                displaySingleProduct(product);
            }
        } else {
            displaySingleProduct(product);
        }
    } catch (err) {
        console.warn('select_product enrichment failed:', err);
        // Network error — show whatever we have from the card.
        displaySingleProduct(product);
    }
}

// Restore grid view (called from ← Back button inside product detail view)
function showProductGrid() {
    if (currentProducts && currentProducts.length > 0) {
        const top3      = currentProducts.slice(0, 3);
        const remaining = currentProducts.slice(3);
        // Re-use the last known preferences stored on the results container (if any)
        displayResults(currentProducts, null, top3, remaining);
    }
}

// Utility: Escape HTML
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Utility: Convert raw bag_style URL path → clean display label
// e.g. "women/handbags/cross-body-bags" → "Cross Body Bags"
//      "offers/women--sale/women-handbags-sale/..." → last meaningful segment
function formatBagStyle(style) {
    if (!style || style === 'N/A' || style === 'Unknown') return null;
    // If it looks like a URL path, take the last non-empty segment
    if (style.includes('/')) {
        const segments = style.split('/').filter(Boolean);
        style = segments[segments.length - 1] || style;
    }
    // Replace hyphens and underscores with spaces, then title-case
    const cleaned = style
        .replace(/[-_]+/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase())
        .trim();
    return cleaned || null;
}

// Utility: Return value if it's a real value, otherwise null
function realValue(val) {
    if (!val) return null;
    const s = String(val).trim();
    if (!s || s.toLowerCase() === 'not specified' || s.toLowerCase() === 'n/a'
            || s.toLowerCase() === 'unknown') return null;
    return s;
}

function markdownToHtml(text) {
    // Escape all HTML first so LLM output cannot inject arbitrary tags.
    // The regex transforms below only introduce known-safe elements.
    text = escapeHtml(text);
    // Convert bold **text**
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Convert italic *text*
    text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Convert bullet points
    text = text.replace(/^- (.+)$/gm, '<li>$1</li>');
    text = text.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
    // Convert line breaks
    text = text.replace(/\n/g, '<br>');
    return text;
}

