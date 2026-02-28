// Note: products are now loaded from products.js which defines a global `products` array.
// We no longer fetch the large JSON file; products.js is included in index.html
async function loadProducts() {
  // Return the globally defined products array (synchronous)
  // Retrieve the products array from the global window scope
  return (typeof window !== 'undefined' && window.products) ? window.products : [];
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
  const imageHtml = product.image ? `<img src="${product.image}" alt="${product.name}">` : '';
  card.innerHTML = `
    ${imageHtml}
    <h3>${product.name}</h3>
    <p><strong>Department:</strong> ${product.department}</p>
    ${product.pack ? `<p><strong>Pack:</strong> ${product.pack}</p>` : ''}
    ${product.price ? `<p class="price">$${product.price.toFixed(2)}</p>` : ''}
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
    const matchesQuery = p.name.toLowerCase().includes(query) || p.department.toLowerCase().includes(query);
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
  initSearchAndFilter(products);
});