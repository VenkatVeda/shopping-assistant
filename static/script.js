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
let activeTab = 'products';   // 'products' | 'wishlist'

// Check authentication on page load
document.addEventListener('DOMContentLoaded', () => {
    displayUserInfo();
    loadWishlist();

    const sendBtn = document.getElementById('sendBtn');
    const clearBtn = document.getElementById('clearBtn');
    const userInput = document.getElementById('userInput');

    sendBtn.addEventListener('click', handleSend);
    clearBtn.addEventListener('click', handleClear);
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleSend();
    });
});

// Get cookie value
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Display user info in header
function displayUserInfo() {
    const userName = getCookie('user_name');
    const userEmail = getCookie('user_email');
    const userPicture = getCookie('user_picture');
    
    if (userName) {
        const headerContent = document.querySelector('.header-content');
        
        // Create user info section
        const userInfo = document.createElement('div');
        userInfo.className = 'user-info';
        userInfo.style.marginLeft = 'auto';
        userInfo.style.display = 'flex';
        userInfo.style.alignItems = 'center';
        userInfo.style.gap = '10px';
        
        if (userPicture && userPicture !== 'undefined') {
            const img = document.createElement('img');
            img.src = decodeURIComponent(userPicture);
            img.alt = userName;
            img.style.width = '32px';
            img.style.height = '32px';
            img.style.borderRadius = '50%';
            userInfo.appendChild(img);
        }
        
        const nameSpan = document.createElement('span');
        nameSpan.textContent = decodeURIComponent(userName);
        nameSpan.style.color = 'white';
        nameSpan.style.fontSize = '14px';
        userInfo.appendChild(nameSpan);
        
        const logoutBtn = document.createElement('button');
        logoutBtn.textContent = 'Logout';
        logoutBtn.style.background = 'rgba(255,255,255,0.2)';
        logoutBtn.style.color = 'white';
        logoutBtn.style.border = 'none';
        logoutBtn.style.padding = '5px 15px';
        logoutBtn.style.borderRadius = '4px';
        logoutBtn.style.cursor = 'pointer';
        logoutBtn.onclick = handleLogout;
        userInfo.appendChild(logoutBtn);
        
        headerContent.appendChild(userInfo);
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
                <button class="product-discuss-btn" onclick="selectProductForDiscussion(${index}, event)">
                    💬 Ask about this
                </button>
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
    const items = wishlistItems.map(item => {
        const pid = escapeHtml(String(item.product_id));
        const name = escapeHtml(item.product_name || 'Product');
        const brand = escapeHtml(item.brand || '');
        const price = item.price ? `$${parseFloat(item.price).toFixed(2)}` : '';
        const imgSrc = item.image_url || '';
        const imgEl = imgSrc
            ? `<img src="${imgSrc}" alt="${name}" class="wishlist-item-img" onerror="this.style.display='none'">`
            : `<div class="wishlist-item-img" style="display:flex;align-items:center;justify-content:center;font-size:28px">🛍️</div>`;
        return `
            <div class="wishlist-item">
                ${imgEl}
                <div class="wishlist-item-info">
                    <div class="wishlist-item-name" title="${name}">${name}</div>
                    ${brand ? `<div class="wishlist-item-brand">${brand}</div>` : ''}
                    ${price ? `<div class="wishlist-item-price">${price}</div>` : ''}
                </div>
                <button class="wishlist-item-remove" title="Remove" onclick="removeWishlistItem('${pid}')">✕</button>
            </div>`;
    }).join('');
    container.innerHTML = `<div class="wishlist-panel-header">${wishlistItems.length} saved item${wishlistItems.length !== 1 ? 's' : ''}</div>${items}`;
}

function switchTab(tab) {
    activeTab = tab;
    const products = document.getElementById('resultsContainer');
    const wishlist = document.getElementById('wishlistContainer');
    const tabP = document.getElementById('tabProducts');
    const tabW = document.getElementById('tabWishlist');
    if (tab === 'products') {
        products.style.display = '';
        wishlist.style.display = 'none';
        tabP.classList.add('active');
        tabW.classList.remove('active');
    } else {
        products.style.display = 'none';
        wishlist.style.display = '';
        tabP.classList.remove('active');
        tabW.classList.add('active');
        renderWishlistPanel();
    }
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

