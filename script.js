// Note: products are now loaded from products.js which defines a global `products` array.
// We no longer fetch the large JSON file; products.js is included in index.html
async function loadProducts() {
  // Return the globally defined products array (synchronous)
  // Retrieve the products array from the global scope. When products_grouped.js is loaded, it defines
  // a top-level `products` const which is not attached to window, but still available in the global scope.
  if (typeof products !== 'undefined') {
    return products;
  }
  if (typeof window !== 'undefined' && window.products) {
    return window.products;
  }
  return [];
}

/**
 * Create a product card DOM element for a given product.
 * Cards contain an optional image, the product name, department, pack information and price.
 * @param {Object} product
 * @returns {HTMLElement}
 */
function createProductCard(product) {
  const card = document.createElement('div');
  card.className = 'product-card';
  // Use the provided image directly if present. Many images live under the images/ folder already.
  const imageHtml = product.image ? `<img src="${product.image}" alt="${product.base_name}">` : '';
  // Prepare sizes display: show available sizes joined by commas
  const sizesHtml = (product.sizes && product.sizes.length > 0)
    ? `<p><strong>Sizes:</strong> ${product.sizes.join(', ')}</p>`
    : '';
  card.innerHTML = `
    ${imageHtml}
    <h3>${product.base_name}</h3>
    <p><strong>Department:</strong> ${product.department}</p>
    ${sizesHtml}
    <!-- Always prompt users to call for price and availability. The price is no longer displayed -->
    <p class="call-price">Call for price &amp; availability</p>
  `;
  return card;
}

/**
 * Populate the category dropdown with all unique departments found in the products list.
 * @param {Array<Object>} products
 */
function populateCategories(products) {
  const select = document.getElementById('categorySelect');
  // Clear existing options except the first 'All' option
  select.innerHTML = '<option value="all">All Departments</option>';
  const departments = Array.from(new Set(products.map(p => p.department))).sort();
  departments.forEach(dept => {
    const option = document.createElement('option');
    option.value = dept;
    option.textContent = dept;
    select.appendChild(option);
  });
}

/**
 * Render featured products and department sections based on current filters.
 * @param {Array<Object>} products
 * @param {string} query Lowercase search query
 * @param {string} category Selected department or 'all'
 */
function render(products, query, category) {
  const featuredContainer = document.getElementById('grid-featured');
  const departmentsContainer = document.getElementById('departments');
  featuredContainer.innerHTML = '';
  departmentsContainer.innerHTML = '';

  // Filter products by search and category
  const filtered = products.filter(p => {
    const matchesQuery = p.base_name.toLowerCase().includes(query) || p.department.toLowerCase().includes(query);
    const matchesCategory = category === 'all' || p.department === category;
    return matchesQuery && matchesCategory;
  });
  // Featured products: premium items
  const featured = filtered.filter(p => p.tier === 'premium');
  // Limit the number of featured items to avoid overly long list
  const featuredLimit = 12;
  const featuredToDisplay = featured.slice(0, featuredLimit);
  featuredToDisplay.forEach(prod => {
    const card = createProductCard(prod);
    featuredContainer.appendChild(card);
  });
  // Remaining products (remove those already displayed in featured if they are premium)
  const remaining = filtered.filter(p => !(p.tier === 'premium' && featuredToDisplay.includes(p)));
  // Group remaining products by department
  const grouped = {};
  remaining.forEach(p => {
    if (!grouped[p.department]) grouped[p.department] = [];
    grouped[p.department].push(p);
  });
  // Sort department names alphabetically for display
  const sortedDepts = Object.keys(grouped).sort();
  sortedDepts.forEach(dept => {
    const items = grouped[dept];
    if (items.length === 0) return;
    // Create section for this department
    const section = document.createElement('div');
    section.className = 'department-section';
    const heading = document.createElement('h2');
    heading.textContent = dept;
    section.appendChild(heading);
    const grid = document.createElement('div');
    grid.className = 'grid';
    items.forEach(item => {
      const card = createProductCard(item);
      grid.appendChild(card);
    });
    section.appendChild(grid);
    departmentsContainer.appendChild(section);
  });
}

/**
 * Initialize the search and category filter functionality.
 * @param {Array<Object>} products
 */
function initSearchAndFilter(products) {
  const searchInput = document.getElementById('search');
  const categorySelect = document.getElementById('categorySelect');
  function applyFilters() {
    const query = searchInput.value.trim().toLowerCase();
    const category = categorySelect.value;
    render(products, query, category);
  }
  searchInput.addEventListener('input', applyFilters);
  categorySelect.addEventListener('change', applyFilters);
  // Initial render
  applyFilters();
}

window.addEventListener('DOMContentLoaded', async () => {
  const products = await loadProducts();
  populateCategories(products);
  // Render category cards and weekly deals on load
  renderCategoryCards(products);
  renderDeals(products);
  initSearchAndFilter(products);
  // Back to top button functionality
  const backBtn = document.getElementById('backToTop');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }
});

/**
 * Determine the most common departments and return the top N.
 * @param {Array<Object>} products
 * @param {number} limit
 * @returns {Array<string>}
 */
function getTopCategories(products, limit = 6) {
  // Preferred categories to highlight if present
  const preferred = [
    'Vodka',
    'Tequila',
    'Whiskey',
    'Beer',
    'Wine',
    'Snacks & Beverages',
    'Snacks',
    'Cordials'
  ];
  // Build a set of departments present in products
  const departmentsSet = new Set(products.map(p => p.department));
  // Filter preferred categories to those available
  const availablePreferred = preferred.filter(cat => departmentsSet.has(cat));
  const categories = [];
  availablePreferred.forEach(cat => {
    if (categories.length < limit) {
      categories.push(cat);
    }
  });
  // If not enough categories yet, fill with the most common remaining departments
  if (categories.length < limit) {
    const counts = {};
    products.forEach(p => {
      counts[p.department] = (counts[p.department] || 0) + 1;
    });
    const sorted = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(e => e[0]);
    for (const dept of sorted) {
      if (categories.length >= limit) break;
      if (!categories.includes(dept)) {
        categories.push(dept);
      }
    }
  }
  return categories;
}

/**
 * Render clickable category cards based on popular departments.
 * Clicking a card will set the filter and scroll to products.
 * @param {Array<Object>} products
 */
function renderCategoryCards(products) {
  const container = document.getElementById('categoryCards');
  if (!container) return;
  container.innerHTML = '';
  const topCats = getTopCategories(products);
  topCats.forEach(cat => {
    const card = document.createElement('div');
    card.className = 'category-card';
    card.textContent = cat;
    card.addEventListener('click', () => {
      const categorySelect = document.getElementById('categorySelect');
      if (categorySelect) {
        categorySelect.value = cat;
      }
      const searchInput = document.getElementById('search');
      if (searchInput) {
        searchInput.value = '';
      }
      render(products, '', cat);
      // Scroll down to the departments section smoothly
      const depts = document.getElementById('departments');
      if (depts) {
        depts.scrollIntoView({ behavior: 'smooth' });
      }
    });
    container.appendChild(card);
  });
}

/**
 * Render a limited list of deals (low priced items) in the deals section.
 * Deals are items under a price threshold and not snacks.
 * @param {Array<Object>} products
 */
function renderDeals(products) {
  const dealsContainer = document.getElementById('deals-grid');
  if (!dealsContainer) return;
  dealsContainer.innerHTML = '';
  // Select products with a defined price less than $25 and not snacks
  const deals = products.filter(p => typeof p.price === 'number' && p.price > 0 && p.price < 25 && p.tier !== 'snack');
  // Sort deals by price ascending
  deals.sort((a, b) => a.price - b.price);
  const dealsToShow = deals.slice(0, 10);
  dealsToShow.forEach(item => {
    const card = createProductCard(item);
    dealsContainer.appendChild(card);
  });
}