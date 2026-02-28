
async function loadProducts() {
  const response = await fetch('products.json');
  const data = await response.json();
  return data;
}

function renderProducts(products) {
  const grid = document.getElementById('productGrid');
  grid.innerHTML = '';
  products.forEach(product => {
    const card = document.createElement('div');
    card.className = 'product-card';
    // Only include an image element if the product has an `image` property defined. Otherwise no image will be shown.
    const imageHtml = product.image ? `<img src="${product.image}" alt="${product.name}">` : '';
    card.innerHTML = `
      ${imageHtml}
      <h3>${product.name}</h3>
      <p><strong>Category:</strong> ${product.department}</p>
      ${product.pack ? `<p><strong>Pack:</strong> ${product.pack}</p>` : ''}
      ${product.price ? `<p class="price">$${product.price.toFixed(2)}</p>` : ''}
    `;
    grid.appendChild(card);
  });
}

function populateCategories(products) {
  const select = document.getElementById('categoryFilter');
  const categories = Array.from(new Set(products.map(p => p.department))).sort();
  categories.forEach(cat => {
    const option = document.createElement('option');
    option.value = cat;
    option.textContent = cat;
    select.appendChild(option);
  });
}

function initSearchAndFilter(products) {
  const searchInput = document.getElementById('search');
  const categorySelect = document.getElementById('categoryFilter');

  function filter() {
    const query = searchInput.value.toLowerCase();
    const category = categorySelect.value;
    const filtered = products.filter(prod => {
      const matchesSearch = prod.name.toLowerCase().includes(query);
      const matchesCategory = category === '' || prod.department === category;
      return matchesSearch && matchesCategory;
    });
    renderProducts(filtered);
  }

  searchInput.addEventListener('input', filter);
  categorySelect.addEventListener('change', filter);
}

window.addEventListener('DOMContentLoaded', async () => {
  const products = await loadProducts();
  populateCategories(products);
  initSearchAndFilter(products);
  renderProducts(products);
});
