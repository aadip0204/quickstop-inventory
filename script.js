
async function loadProducts() {
  const response = await fetch('products.json');
  const data = await response.json();
  return data;
}

function createProductCard(product) {
  const card = document.createElement('div');
  card.className = 'product-card';
  const imageHtml = product.image ? `<img src="${product.image}" alt="${product.name}">` : '';
  card.innerHTML = `
    ${imageHtml}
    <h3>${product.name}</h3>
    <p><strong>Category:</strong> ${product.department}</p>
    ${product.pack ? `<p><strong>Pack:</strong> ${product.pack}</p>` : ''}
    ${product.price ? `<p class="price">$${product.price.toFixed(2)}</p>` : ''}
  `;
  return card;
}

function renderSections(products) {
  // Containers for each tier
  const sections = {
    premium: document.getElementById('grid-premium'),
    core: document.getElementById('grid-core'),
    snack: document.getElementById('grid-snack'),
    tobacco: document.getElementById('grid-tobacco')
  };
  // Clear existing content
  Object.values(sections).forEach(el => {
    if (el) el.innerHTML = '';
  });
  products.forEach(product => {
    const tier = product.tier || 'core';
    const container = sections[tier] || sections['core'];
    const card = createProductCard(product);
    container.appendChild(card);
  });
}

// Category filter removed in this design
function populateCategories(products) {
  return;
}

function initSearchAndFilter(products) {
  const searchInput = document.getElementById('search');
  function filter() {
    const query = searchInput.value.toLowerCase();
    const filtered = products.filter(prod => {
      return prod.name.toLowerCase().includes(query) || prod.department.toLowerCase().includes(query);
    });
    renderSections(filtered);
  }
  searchInput.addEventListener('input', filter);
}

window.addEventListener('DOMContentLoaded', async () => {
  const products = await loadProducts();
  populateCategories(products);
  initSearchAndFilter(products);
  renderSections(products);
});
