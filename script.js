(function () {
  'use strict';

  const PAGE_SIZE = 36;
  const formatNumber = value => Number(value).toLocaleString('en-US');

  // These helpers also run in Node for catalog-wide search and navigation checks.
  function normalize(value) {
    return String(value ?? '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/(\d)\s+(ml|l|oz|pk|pack|ct)\b/g, '$1$2')
      .replace(/[^a-z0-9.]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function queryTokens(query) {
    return normalize(query).split(' ').filter(Boolean);
  }

  function productSearchText(product, categoryLabel = '', departmentLabel = '') {
    return normalize([product.name, product.department, product.category, categoryLabel, departmentLabel,
      ...product.variants.flatMap(variant => [variant.sku, variant.upc, variant.label])].join(' '));
  }

  function shoppingCategoryLabel(product, categories = []) {
    return categories.find(category => category.id === product.category)?.label || product.category;
  }

  function filterProducts(products, filters = {}, categories = []) {
    const tokens = queryTokens(filters.query || '');
    const labels = new Map(categories.map(category => [category.id, category.label]));
    const departments = new Map(categories.flatMap(category => (category.departments || []).map(department => [department.id, department.label])));
    return products.filter(product => {
      if (filters.category && filters.category !== 'all' && product.category !== filters.category) return false;
      if (filters.department !== undefined && filters.department !== 'all' && product.department !== filters.department) return false;
      const haystack = productSearchText(product, labels.get(product.category) || '', departments.get(product.department) || '');
      return tokens.every(token => haystack.includes(token));
    });
  }

  function paginate(products, requestedPage = 1, pageSize = PAGE_SIZE) {
    const safeSize = Math.max(1, Math.floor(Number(pageSize) || PAGE_SIZE));
    const totalPages = Math.max(1, Math.ceil(products.length / safeSize));
    const page = Math.max(1, Math.min(totalPages, Math.floor(Number(requestedPage) || 1)));
    return { items: products.slice((page - 1) * safeSize, page * safeSize), page, totalPages,
      start: products.length ? (page - 1) * safeSize + 1 : 0, end: Math.min(page * safeSize, products.length) };
  }

  function matchingVariant(product, query = '', selectedSku) {
    if (selectedSku !== undefined) {
      const selected = product.variants.find(variant => variant.sku === selectedSku);
      if (selected) return selected;
    }
    const tokens = queryTokens(query);
    const exactIdentifier = product.variants.find(variant =>
      query.trim() && [variant.sku, variant.upc].some(value => String(value) === query.trim()));
    if (exactIdentifier) return exactIdentifier;
    const matches = product.variants.filter(variant => {
      const text = normalize([product.name, product.department, product.category,
        variant.sku, variant.upc, variant.label].join(' '));
      return tokens.every(token => text.includes(token));
    });
    // Default to a photographed matching size, while keeping its exact size/SKU
    // selected. A size-specific query or explicit user choice always takes priority.
    return matches.find(variant => getVariantImage(variant)) || matches[0] || product.variants[0];
  }

  function getVariantImage(variant) {
    const image = variant?.image;
    // Product photos are reviewed, local assets tied to one SKU. Never borrow a sibling image.
    if (!image || typeof image !== 'object' || typeof image.src !== 'string' || typeof image.alt !== 'string') return null;
    if (!/^(?:images|assets)\/[a-zA-Z0-9_./-]+\.(?:png|jpe?g|webp|avif)$/i.test(image.src) || image.src.includes('..')) return null;
    if (!image.alt.trim()) return null;
    const safeLink = value => typeof value === 'string' && /^https:\/\//.test(value) ? value : null;
    return { src: image.src, alt: image.alt, attribution: image.attribution || '',
      sourcePage: safeLink(image.sourcePage), license: image.license || '', licenseUrl: safeLink(image.licenseUrl) };
  }

  const helpers = { PAGE_SIZE, normalize, productSearchText, shoppingCategoryLabel, filterProducts, paginate, matchingVariant, getVariantImage };
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (typeof document === 'undefined') return;
  window.QuickStopCatalog = helpers;

  const elements = {
    form: document.getElementById('filters'), search: document.getElementById('search'),
    department: document.getElementById('department'), sort: document.getElementById('sort'),
    reset: document.getElementById('reset'), nav: document.getElementById('category-nav'),
    count: document.getElementById('result-count'), total: document.getElementById('catalog-total'),
    state: document.getElementById('catalog-state'), grid: document.getElementById('product-grid'),
    pagination: document.getElementById('pagination')
  };
  let catalog = null;
  let state = { query: '', category: 'all', department: 'all', sort: 'az', page: 1 };
  let searchTimer;
  const selectedVariants = new Map();

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function getDepartments() {
    const categories = state.category === 'all' ? catalog.categories : catalog.categories.filter(category => category.id === state.category);
    const departments = new Map();
    categories.forEach(category => category.departments.forEach(department => departments.set(department.id, department)));
    return [...departments.values()].sort((a, b) => a.label.localeCompare(b.label));
  }

  function populateDepartments() {
    const departments = getDepartments();
    if (!departments.some(department => department.id === state.department)) state.department = 'all';
    elements.department.replaceChildren(new Option('All departments', 'all'));
    departments.forEach(department => elements.department.add(new Option(department.label, department.id)));
    elements.department.value = state.department;
  }

  function readUrl() {
    selectedVariants.clear();
    const params = new URLSearchParams(window.location.search);
    const category = params.get('category') || 'all';
    state = {
      query: (params.get('q') || '').slice(0, 200),
      category: catalog.categories.some(item => item.id === category) ? category : 'all',
      department: params.has('department') ? params.get('department') : 'all',
      sort: params.get('sort') === 'za' ? 'za' : 'az',
      page: Math.max(1, Number.parseInt(params.get('page'), 10) || 1)
    };
    elements.search.value = state.query;
    elements.sort.value = state.sort;
    populateDepartments();
  }

  function writeUrl() {
    const url = new URL(window.location.href);
    ['q', 'category', 'department', 'sort', 'page'].forEach(key => url.searchParams.delete(key));
    if (state.query) url.searchParams.set('q', state.query);
    if (state.category !== 'all') url.searchParams.set('category', state.category);
    if (state.department !== 'all') url.searchParams.set('department', state.department);
    if (state.sort !== 'az') url.searchParams.set('sort', state.sort);
    if (state.page > 1) url.searchParams.set('page', String(state.page));
    window.history.replaceState(null, '', url);
  }

  function buildCategories() {
    elements.nav.replaceChildren();
    const categories = [{ id: 'all', label: 'All products' }, ...catalog.categories];
    categories.forEach(category => {
      const count = category.id === 'all' ? catalog.products.length : catalog.products.filter(product => product.category === category.id).length;
      if (!count) return;
      const button = element('button', 'category-button', category.label);
      button.type = 'button';
      button.dataset.category = category.id;
      button.setAttribute('aria-pressed', String(state.category === category.id));
      button.append(element('span', '', formatNumber(count)));
      button.addEventListener('click', () => {
        clearTimeout(searchTimer);
        if (state.query !== elements.search.value.trim()) selectedVariants.clear();
        state.query = elements.search.value.trim();
        state.category = category.id;
        state.department = 'all';
        state.page = 1;
        populateDepartments();
        render();
      });
      elements.nav.append(button);
    });
  }

  function showState(heading, message, actionLabel, action) {
    elements.state.replaceChildren(element('h3', '', heading), element('p', '', message));
    elements.state.hidden = false;
    if (action) {
      const button = element('button', 'state-action', actionLabel);
      button.type = 'button';
      button.addEventListener('click', action);
      elements.state.append(button);
    }
  }

  function createProductCard(product) {
    const card = element('article', 'product-card');
    card.dataset.productId = product.id;
    const category = element('p', 'product-department', shoppingCategoryLabel(product, catalog.categories));
    const title = element('h3', 'product-name', product.name);
    const area = element('div', 'variant-area');
    const label = element(product.variants.length > 1 ? 'label' : 'div', 'variant-label', 'Listed sizes');
    const info = element('p', 'variant-info');
    const selected = matchingVariant(product, state.query, selectedVariants.get(product.id));
    const imageSlot = element('div', 'image-slot');
    imageSlot.hidden = true;
    card.append(category, imageSlot, title, area);
    area.append(label);
    if (product.variants.length > 1) {
      const select = element('select', 'variant-select');
      const id = `variant-${product.id}`;
      select.id = id;
      label.htmlFor = id;
      label.append(element('span', '', `${formatNumber(product.variants.length)} listings`));
      const repeatedLabels = new Map();
      product.variants.forEach(variant => repeatedLabels.set(variant.label, (repeatedLabels.get(variant.label) || 0) + 1));
      product.variants.forEach(variant => {
        const optionLabel = repeatedLabels.get(variant.label) > 1 ? `${variant.label} · SKU ${variant.sku}` : variant.label;
        select.add(new Option(optionLabel, variant.sku));
      });
      select.value = selected.sku;
      select.setAttribute('aria-label', `Listed sizes for ${product.name}`);
      select.addEventListener('change', () => {
        selectedVariants.set(product.id, select.value);
        updateVariant(product.variants.find(variant => variant.sku === select.value));
      });
      area.append(select);
    } else {
      area.append(element('p', 'single-size', selected.label));
    }
    area.append(info);

    function updateVariant(variant) {
      info.textContent = `SKU ${variant.sku}`;
      card.dataset.selectedSku = variant.sku;
      imageSlot.replaceChildren();
      imageSlot.hidden = true;
      const image = getVariantImage(variant);
      if (image) {
        const img = element('img', 'product-image');
        img.src = image.src;
        img.alt = image.alt;
        img.loading = 'lazy';
        img.decoding = 'async';
        img.addEventListener('error', () => {
          if (img.parentNode !== imageSlot) return;
          imageSlot.replaceChildren();
          imageSlot.hidden = true;
        }, { once: true });
        imageSlot.append(img);
        imageSlot.hidden = false;
      }
    }
    updateVariant(selected);
    return card;
  }

  function changePage(page) {
    state.page = page;
    render();
    document.getElementById('catalog-title').scrollIntoView({ block: 'start', behavior: 'auto' });
    const firstTitle = elements.grid.querySelector('h3');
    if (firstTitle) {
      firstTitle.tabIndex = -1;
      firstTitle.focus({ preventScroll: true });
    }
  }

  function renderPagination(pageData) {
    elements.pagination.replaceChildren();
    elements.pagination.hidden = pageData.totalPages <= 1;
    if (pageData.totalPages <= 1) return;
    function addButton(label, page, disabled, current = false) {
      const button = element('button', '', label);
      button.type = 'button';
      button.disabled = disabled;
      button.setAttribute('aria-label', /^\d+$/.test(label) ? `Page ${label}` : `${label} page`);
      if (current) button.setAttribute('aria-current', 'page');
      button.addEventListener('click', () => changePage(page));
      elements.pagination.append(button);
    }
    addButton('Previous', pageData.page - 1, pageData.page === 1);
    const pages = new Set([1, pageData.totalPages, pageData.page - 1, pageData.page, pageData.page + 1]);
    let previous = 0;
    [...pages].filter(page => page >= 1 && page <= pageData.totalPages).sort((a, b) => a - b).forEach(page => {
      if (previous && page > previous + 1) {
        const gap = element('span', 'page-gap', '…');
        gap.setAttribute('aria-hidden', 'true');
        elements.pagination.append(gap);
      }
      addButton(String(page), page, false, page === pageData.page);
      previous = page;
    });
    addButton('Next', pageData.page + 1, pageData.page === pageData.totalPages);
  }

  function render() {
    if (!catalog) return;
    const filtered = filterProducts(catalog.products, state, catalog.categories)
      .sort((a, b) => (state.sort === 'za' ? -1 : 1) * a.name.localeCompare(b.name, 'en', { numeric: true }) || a.id.localeCompare(b.id));
    const pageData = paginate(filtered, state.page);
    state.page = pageData.page;
    elements.nav.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.category === state.category)));
    elements.grid.replaceChildren(...pageData.items.map(createProductCard));
    elements.state.hidden = filtered.length > 0;
    if (!filtered.length) showState('No matching products', 'Try a different spelling, a brand name or a broader department.', 'Reset filters', resetFilters);
    const variants = filtered.reduce((total, product) => total + product.variants.length, 0);
    elements.count.replaceChildren();
    if (filtered.length) {
      elements.count.append(`Showing ${formatNumber(pageData.start)}–${formatNumber(pageData.end)} of `,
        element('strong', '', `${formatNumber(filtered.length)} ${filtered.length === 1 ? 'product' : 'products'}`), ` · ${formatNumber(variants)} listed ${variants === 1 ? 'variant' : 'variants'}`);
    } else elements.count.textContent = '0 products found';
    renderPagination(pageData);
    writeUrl();
  }

  function resetFilters() {
    clearTimeout(searchTimer);
    state = { query: '', category: 'all', department: 'all', sort: 'az', page: 1 };
    selectedVariants.clear();
    elements.search.value = '';
    elements.sort.value = 'az';
    populateDepartments();
    render();
  }

  function applySearch() {
    state.query = elements.search.value.trim();
    state.page = 1;
    selectedVariants.clear();
    render();
  }

  elements.form.addEventListener('submit', event => { event.preventDefault(); clearTimeout(searchTimer); applySearch(); });
  elements.search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(applySearch, 120); });
  function commitPendingSearch() {
    clearTimeout(searchTimer);
    const query = elements.search.value.trim();
    if (state.query !== query) selectedVariants.clear();
    state.query = query;
  }
  elements.department.addEventListener('change', () => { commitPendingSearch(); state.department = elements.department.value; state.page = 1; render(); });
  elements.sort.addEventListener('change', () => { commitPendingSearch(); state.sort = elements.sort.value; state.page = 1; render(); });
  elements.reset.addEventListener('click', resetFilters);
  window.addEventListener('popstate', () => { if (catalog) { readUrl(); render(); } });

  async function loadCatalog() {
    clearTimeout(searchTimer);
    elements.grid.replaceChildren();
    elements.nav.replaceChildren();
    elements.pagination.hidden = true;
    [...elements.form.elements].forEach(control => { control.disabled = true; });
    elements.count.textContent = 'Loading the catalog…';
    showState('Getting the catalog ready', 'One moment while we load the product list.');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('catalog.json', { cache: 'no-cache', signal: controller.signal });
      if (!response.ok) throw new Error('Catalog request failed');
      const data = await response.json();
      if (data.schemaVersion !== 1 || !Array.isArray(data.products) || !data.products.length || !Array.isArray(data.categories) ||
        data.products.some(product => typeof product.id !== 'string' || typeof product.name !== 'string' || !Array.isArray(product.variants) || !product.variants.length || product.variants.some(variant => typeof variant.sku !== 'string' || typeof variant.label !== 'string')) ||
        data.categories.some(category => typeof category.id !== 'string' || typeof category.label !== 'string' || !Array.isArray(category.departments))) throw new Error('Catalog format is invalid');
      catalog = data;
      elements.total.textContent = `${formatNumber(catalog.products.length)} products to explore`;
      readUrl();
      buildCategories();
      render();
    } catch (error) {
      catalog = null;
      elements.count.textContent = 'Catalog unavailable';
      showState('The catalog couldn’t load', 'Check your connection and try again. Your browser may need a moment to reconnect.', 'Try again', loadCatalog);
    } finally {
      clearTimeout(timeout);
      [...elements.form.elements].forEach(control => { control.disabled = !catalog; });
    }
  }

  loadCatalog();
}());
