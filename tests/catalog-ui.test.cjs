const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ui = require('../script.js');
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../catalog.json'), 'utf8'));

test('every retained SKU and UPC participates in search and selects its own variant', () => {
  let variants = 0;
  for (const product of catalog.products) {
    for (const variant of product.variants) {
      assert.equal(ui.filterProducts([product], {query: variant.sku}, catalog.categories).length, 1);
      assert.equal(ui.matchingVariant(product, variant.sku).sku, variant.sku);
      if (variant.upc) assert.equal(ui.filterProducts([product], {query: variant.upc}).length, 1);
      variants++;
    }
  }
  assert.equal(variants, catalog.counts.publicVariants);
});

test('every populated category and original department is reachable without losing products', () => {
  const reached = new Set();
  for (const category of catalog.categories) {
    const main = ui.filterProducts(catalog.products, {category: category.id}, catalog.categories);
    assert.ok(main.length > 0);
    let count = 0;
    for (const department of category.departments) {
      const products = ui.filterProducts(catalog.products, {category: category.id, department: department.id}, catalog.categories);
      assert.ok(products.length > 0, department.id);
      for (const product of products) {
        assert.equal(product.category, category.id);
        assert.equal(product.department, department.id);
        assert.equal(reached.has(product.id), false);
        reached.add(product.id);
        count++;
      }
    }
    assert.equal(count, main.length);
  }
  assert.equal(reached.size, catalog.products.length);
});

test('pagination exposes the entire catalog exactly once, bounded to 36 cards', () => {
  const ids = [];
  const pages = ui.paginate(catalog.products).totalPages;
  for (let page = 1; page <= pages; page++) {
    const result = ui.paginate(catalog.products, page);
    assert.ok(result.items.length <= 36 && result.items.length > 0);
    ids.push(...result.items.map(product => product.id));
  }
  assert.equal(new Set(ids).size, catalog.products.length);
  assert.deepEqual(ids, catalog.products.map(product => product.id));
  assert.equal(ui.paginate(catalog.products, -99).page, 1);
  assert.equal(ui.paginate(catalog.products, 999999).page, pages);
  assert.deepEqual(ui.paginate([], 4).items, []);
});

test('brand/flavor and normalized size search remains distinct', () => {
  const coconut = ui.filterProducts(catalog.products, {query: '1800 coconut 1 L'});
  assert.ok(coconut.length);
  assert.ok(coconut.every(product => /1800.*coconut/i.test(product.name)));
  const otherBrand = ui.filterProducts(catalog.products, {query: '818'});
  assert.ok(otherBrand.some(product => /818/.test(product.name)));
  assert.equal(ui.filterProducts(catalog.products, {query: 'zzzz-no-such-product-zzzz'}).length, 0);
});

test('a selected variant never inherits another size image and unsafe image paths are rejected', () => {
  const product = {name:'Example', department:'Tequila', category:'spirits', variants:[
    {sku:'001', upc:'001234567890', label:'750 mL', image:{src:'images/exact-750.webp', alt:'Example 750 mL bottle'}},
    {sku:'002', upc:'001234567891', label:'1 L', image:null}
  ]};
  assert.equal(ui.getVariantImage(ui.matchingVariant(product, '', '002')), null);
  assert.equal(ui.getVariantImage(ui.matchingVariant(product, '001')).src, 'images/exact-750.webp');
  const reversed = {...product, variants:[...product.variants].reverse()};
  assert.equal(ui.matchingVariant(reversed, '').sku, '001');
  assert.equal(ui.matchingVariant(reversed, '1 L').sku, '002');
  assert.equal(ui.getVariantImage(ui.matchingVariant(reversed, '1 L')), null);
  for (const src of ['https://example.com/a.jpg', 'data:image/png;base64,AA', 'images/../wrong.jpg']) {
    assert.equal(ui.getVariantImage({image:{src,alt:'Invalid'}}), null);
  }
});

test('public document contains no legacy data, sale section, stock promise, or numeric prices', () => {
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  assert.doesNotMatch(html, /products(?:_grouped_filtered)?\.(?:js|json)|Weekly Deals|updated daily|available now|\$\s*\d/i);
  for (const name of ['products.js', 'products.json', 'products_grouped_filtered.js']) {
    assert.equal(fs.existsSync(path.join(__dirname, '..', name)), false);
  }
});

test('a late failure from a previous size cannot hide the currently selected photo', async () => {
  const vm = require('node:vm');
  // Exercise the real card and selection handlers with asynchronous catalog loading.
  class TestElement {
    constructor(tag) {
      this.tag = tag;
      this.children = [];
      this.listeners = {};
      this.dataset = {};
      this.value = '';
      this.parentNode = null;
    }
    append(...children) {
      for (const child of children) {
        this.children.push(child);
        if (typeof child === 'object') child.parentNode = this;
      }
    }
    replaceChildren(...children) {
      for (const child of this.children) if (typeof child === 'object') child.parentNode = null;
      this.children = [];
      this.append(...children);
    }
    add(child) { this.append(child); }
    setAttribute() {}
    addEventListener(type, callback) { this.listeners[type] = callback; }
    descendants() {
      return this.children.filter(child => typeof child === 'object')
        .flatMap(child => [child, ...child.descendants()]);
    }
    querySelectorAll(tag) { return this.descendants().filter(child => child.tag === tag); }
    querySelector(tag) { return this.querySelectorAll(tag)[0] || null; }
  }
  const ids = ['filters', 'search', 'department', 'sort', 'reset', 'category-nav',
    'result-count', 'catalog-total', 'catalog-state', 'product-grid', 'pagination'];
  const elements = Object.fromEntries(ids.map(id => [id, new TestElement(id)]));
  elements.filters.elements = [elements.search, elements.department, elements.sort, elements.reset];
  const data = {
    schemaVersion: 1,
    categories: [{ id: 'drinks', label: 'Drinks', departments: [{ id: 'Soda', label: 'Soda' }] }],
    products: [{ id: 'example', name: 'Example', category: 'drinks', department: 'Soda', variants: [
      { sku: '001', upc: '001234567890', label: '12 oz', image: { src: 'images/exact-12.jpg', alt: 'Example 12 oz' } },
      { sku: '002', upc: '001234567891', label: '20 oz', image: { src: 'images/exact-20.jpg', alt: 'Example 20 oz' } }
    ] }]
  };
  const context = {
    setTimeout, clearTimeout, AbortController, URL, URLSearchParams,
    Option: class extends TestElement {
      constructor(label, value) { super('option'); this.textContent = label; this.value = value; }
    },
    document: { createElement: tag => new TestElement(tag), getElementById: id => elements[id] },
    window: { location: { href: 'http://localhost/', search: '' }, history: { replaceState() {} }, addEventListener() {} },
    fetch: async () => ({ ok: true, json: async () => data })
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../script.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  const card = elements['product-grid'].children[0];
  const previousImage = card.querySelector('img');
  const imageSlot = previousImage.parentNode;
  const select = card.querySelector('select');
  select.value = '002';
  select.listeners.change();
  const currentImage = card.querySelector('img');
  assert.equal(card.dataset.selectedSku, '002');
  assert.equal(currentImage.src, 'images/exact-20.jpg');
  assert.equal(previousImage.parentNode, null);

  previousImage.listeners.error();
  assert.equal(card.querySelector('img'), currentImage);
  assert.equal(imageSlot.hidden, false);

  currentImage.listeners.error();
  assert.equal(card.querySelector('img'), null);
  assert.equal(imageSlot.hidden, true);
});
