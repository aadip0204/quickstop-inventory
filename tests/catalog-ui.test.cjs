const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ui = require('../script.js');
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../catalog.json'), 'utf8'));

test('same-size Friskies flavors are independently searchable products', () => {
  const cases = [
    {sku: '3475', upc: '050000424948', query: 'friskies ocean whitefish tuna'},
    {sku: '3479', upc: '050000425044', query: 'friskies mariners catch'}
  ];
  const products = cases.map(({sku, upc, query}) => {
    const matches = ui.filterProducts(catalog.products, {query}, catalog.categories);
    const product = matches.find(item => item.variants.some(variant => variant.sku === sku));
    assert.ok(product, `${sku} must be found by its own flavor`);
    assert.deepEqual(product.variants.map(variant => variant.sku), [sku],
      'different flavors must not be presented as sizes of one product');
    assert.equal(ui.matchingVariant(product, upc).sku, sku);
    assert.equal(product.variants[0].upc, upc);
    assert.equal(product.variants[0].label, '5.5 oz');
    return product;
  });
  assert.notEqual(products[0].id, products[1].id);
});

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

test('exact identifiers lead partial matches without losing results or changing name order within tiers', () => {
  const product = (id, name, sku, upc) => ({ id, name, category: 'food', department: 'Snacks',
    variants: [{ sku, upc, label: '12 oz', image: null }] });
  const partials = Array.from({length: 40}, (_, i) =>
    product(`partial-${i}`, `Aardvark Snack ${i}`, `P${i}`, `0002002${String(i).padStart(5, '0')}`));
  const exactSku = product('exact-sku', 'Zesty Snack', '2002', '009876543210');
  const exactUpc = product('exact-upc', 'Middle Snack', 'UPC-1', '2002');
  const products = [...partials, exactUpc, exactSku];
  for (const sort of ['az', 'za']) {
    const filters = {query: ' 2002 ', sort};
    const matches = ui.filterProducts(products, filters);
    const sorted = ui.sortProducts(matches, filters);
    assert.equal(sorted.length, products.length);
    assert.equal(sorted[0], exactSku);
    assert.equal(sorted[1], exactUpc);
    assert.equal(ui.paginate(sorted).items[0], exactSku);
    const expectedPartials = [...partials].sort((a, b) =>
      (sort === 'za' ? -1 : 1) * a.name.localeCompare(b.name, 'en', {numeric: true}) || a.id.localeCompare(b.id));
    assert.deepEqual(sorted.slice(2), expectedPartials);
    assert.deepEqual(matches, products, 'sorting must not mutate the filtered list');
    assert.deepEqual(ui.sortProducts(ui.filterProducts(products, {query: 'Snack'}), {query: 'Snack', sort}),
      [...products].sort((a, b) => (sort === 'za' ? -1 : 1) * a.name.localeCompare(b.name, 'en', {numeric: true}) || a.id.localeCompare(b.id)));
  }
  const exactUpcQuery = {query: '009876543210', sort: 'az'};
  const upcPart = product('upc-partial', 'Aardvark', 'P99', '10098765432109');
  assert.deepEqual(ui.sortProducts(ui.filterProducts([upcPart, exactSku], exactUpcQuery), exactUpcQuery), [exactSku, upcPart]);
  const leadingZero = product('leading-zero', 'Aardvark Zero', '02002', '123');
  assert.equal(ui.sortProducts([leadingZero, exactSku], {query: '2002'})[0], exactSku);
  assert.equal(ui.sortProducts([exactSku, leadingZero], {query: '02002'})[0], leadingZero);
  const mixed = {...exactSku, variants: [...exactUpc.variants, ...exactSku.variants]};
  assert.equal(ui.matchingVariant(mixed, '2002').sku, '2002');
});

test('the real SKU 2002 is on the first page ahead of UPC substring matches in both sort orders', () => {
  for (const sort of ['az', 'za']) {
    const filters = {query: '2002', sort};
    const matches = ui.filterProducts(catalog.products, filters, catalog.categories);
    assert.ok(matches.length > ui.PAGE_SIZE, 'regression needs competing partial matches beyond one page');
    const first = ui.paginate(ui.sortProducts(matches, filters)).items[0];
    assert.equal(ui.matchingVariant(first, '2002').sku, '2002');
  }
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

test('shopping badges and navigation use categories while preserving shared original departments', () => {
  const categories = [
    { id: 'food', label: 'Snacks & groceries', departments: [{ id: 'MISC', label: 'General merchandise', sourceLabel: 'MISC' }] },
    { id: 'essentials', label: 'Everyday essentials', departments: [{ id: 'MISC', label: 'General merchandise', sourceLabel: 'MISC' }] }
  ];
  const jam = { id: 'jam', name: 'Verified Jam', category: 'food', department: 'MISC', variants: [{ sku: 'jam-1', upc: '001234567890', label: '30 oz' }] };
  const item = { id: 'item', name: 'Other Item', category: 'essentials', department: 'MISC', variants: [{ sku: 'item-1', upc: '001234567891', label: 'Single' }] };
  const products = [jam, item];
  assert.equal(ui.shoppingCategoryLabel(jam, categories), 'Snacks & groceries');
  assert.equal(jam.department, 'MISC');
  assert.deepEqual(ui.filterProducts(products, { category: 'food', department: 'MISC' }, categories), [jam]);
  assert.deepEqual(ui.filterProducts(products, { category: 'essentials', department: 'MISC' }, categories), [item]);
  assert.deepEqual(ui.filterProducts(products, { department: 'MISC' }, categories), products);
  assert.deepEqual(ui.filterProducts(products, { query: 'General merchandise' }, categories), products);
  assert.deepEqual(ui.filterProducts(products, { query: 'Snacks groceries' }, categories), [jam]);
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

test('brand searches work with and without apostrophes', () => {
  for (const query of ['Jack Daniels', "Jack Daniel's", 'Jack Daniel’s']) {
    const matches = ui.filterProducts(catalog.products, {query}, catalog.categories);
    assert.ok(matches.some(p => p.name === "Jack Daniel's"), query);
    assert.ok(matches.some(p => p.name === "Jack Daniel's Honey"), query);
  }
});

test('Ne-Mo spellings find the verified breads on the first page and preserve the ambiguous family', () => {
  for (const query of ['Ne-Mo', "Ne-Mo's", 'Nemos']) {
    for (const sort of ['az', 'za']) {
      const filters = {query, sort};
      const matches = ui.filterProducts(catalog.products, filters, catalog.categories);
      const firstPage = ui.paginate(ui.sortProducts(matches, filters)).items;
      const skus = new Set(firstPage.flatMap(product => product.variants.map(variant => variant.sku)));
      for (const sku of ['6101', '6102', '6103', '6104', '6105', '101228']) assert.ok(skus.has(sku), `${query}: ${sku}`);
      assert.ok(!matches.some(product => /moscato/i.test(product.name)), query);
    }
  }
});

test('alphabetic hyphens fold generally alongside apostrophes and initialisms', () => {
  const product = {id:'hyphen', name:"Ac-Me's A-B-C Snack", category:'food', department:'Snacks',
    variants:[{sku:'hyphen-1', upc:'001234567890', label:'4 oz', image:null}]};
  const unrelated = {id:'unrelated', name:'Moscato Wine', category:'wine', department:'Wine', variants:[]};
  for (const query of ['Ac-Me', "Ac-Me's", 'Acmes', 'A-B-C', 'ABC', 'A.B.C.']) {
    assert.deepEqual(ui.filterProducts([unrelated, product], {query}), [product], query);
  }
});

test('hyphen folding preserves decimal quantities, numeric brands and measure or pack separators', () => {
  for (const [input, expected] of [
    ['1.75 L', '1.75l'], ['175 L', '175l'], ['1.75-L', '1.75 l'],
    ['20-oz', '20 oz'], ['6-pack', '6 pack'], ['12oz-can', '12oz can'],
    ['20 fl-oz', '20 fl oz'], ['20-fl-oz', '20 fl oz'], ['7-UP', '7 up'], ['WD-40', 'wd 40']
  ]) assert.equal(ui.normalize(input), expected, input);
  const product = {id:'decimal', name:'Example Drink', category:'drinks', department:'Soda',
    variants:[{sku:'decimal-1', upc:'001234567890', label:'1.75 L', image:null}]};
  assert.deepEqual(ui.filterProducts([product], {query:'Example Drink 1.75 L'}), [product]);
  assert.deepEqual(ui.filterProducts([product], {query:'Example Drink 175 L'}), []);
});

test('Alka-Seltzer retains all five spaced and hyphenated catalog products', () => {
  for (const query of ['Alka-Seltzer', 'Alka Seltzer']) {
    const matches = ui.filterProducts(catalog.products, {query}, catalog.categories);
    assert.deepEqual(matches.flatMap(product => product.variants.map(variant => variant.sku)).sort(),
      ['350', '351', '352', '353', '354'], query);
  }
});

test('joined product-name words support spaced brands and select the exact queried size', () => {
  for (const name of ['Ac Me Drink', 'Ac-Me Drink']) {
    const product = {id:name, name, category:'drinks', department:'Soda', variants:[
      {sku:'small', upc:'000000000001', label:'12 oz', image:{src:'images/small.jpg', alt:'Small'}},
      {sku:'large', upc:'000000000002', label:'20 oz', image:null},
      {sku:'decimal', upc:'000000000003', label:'1.75 L', image:null},
      {sku:'integer', upc:'000000000004', label:'175 L', image:null}
    ]};
    for (const [query, sku] of [['Ac-Me 20 oz', 'large'], ['Acme 20 oz', 'large'],
      ['Ac-Me 1.75 L', 'decimal'], ['Acme 175 L', 'integer']]) {
      assert.deepEqual(ui.filterProducts([product], {query}), [product], `${name}: ${query}`);
      assert.equal(ui.matchingVariant(product, query).sku, sku, `${name}: ${query}`);
    }
    assert.equal(ui.matchingVariant(product, '000000000003').sku, 'decimal');
  }
});

test('joined-name matching starts at word boundaries and never crosses category or department fields', () => {
  const product = (name, category, department) => ({id:name, name, category, department,
    variants:[{sku:'example', upc:'000000000001', label:'20 oz', image:null}]});
  const wine = product('Wine Moscato', 'wine', 'Wine');
  const fields = product('Other', 'ac', 'Me');
  const midword = product('Mac Meadow', 'food', 'Snacks');
  const categories = [{id:'wine', label:'Ne', departments:[{id:'Wine', label:'Mo'}]}];
  assert.deepEqual(ui.filterProducts([wine], {query:'Ne-Mo'}, categories), []);
  assert.deepEqual(ui.filterProducts([fields, midword], {query:'Ac-Me'}), []);
  const boundary = product('Regular Ac Me Drink', 'drinks', 'Soda');
  assert.deepEqual(ui.filterProducts([boundary], {query:'Ac-Me 20 oz'}), [boundary]);
});

test('verified initialism names remain searchable with ordinary or dotted spelling', () => {
  const expected = ['4058', '4059', '4062', '4063', '4060', '4061'];
  for (const query of ['Hennessy VSOP', 'Hennessy V.S.O.P', 'Hennessy V.S.O.P.']) {
    const matches = ui.filterProducts(catalog.products, {query}, catalog.categories);
    const product = matches.find(item => item.variants.some(variant => variant.sku === '4058'));
    assert.ok(product, query);
    assert.deepEqual(product.variants.map(variant => variant.sku), expected);
    assert.equal(ui.matchingVariant(product, `${query} 750 mL`).sku, '4063');
  }
});

test('Deluxe spelling variants find the verified unit without collapsing decimal sizes', () => {
  for (const wording of ['Deluxe', 'De Luxe']) {
    const query = `Crown Royal ${wording} 1.75 L`;
    const matches = ui.filterProducts(catalog.products, {query}, catalog.categories);
    const product = matches.find(item => item.variants.some(variant => variant.sku === '2559'));
    assert.ok(product, query);
    assert.deepEqual(product.variants.map(variant => variant.sku), ['2559']);
    assert.equal(ui.matchingVariant(product, query).label, '1.75 L');
    assert.equal(ui.filterProducts([product], {query: `Crown Royal ${wording} 175 L`}).length, 0);
  }
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

test('photo cards stay free of source lines and a late failure cannot hide the selected image', async () => {
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
      { sku: '001', upc: '001234567890', label: '12 oz', image: { src: 'images/exact-12.jpg', alt: 'Example 12 oz',
          usageBasis: 'user_authorized_direct_reference', attributionRequired: false } },
      { sku: '002', upc: '001234567891', label: '20 oz', image: { src: 'images/exact-20.jpg', alt: 'Example 20 oz',
          usageBasis: 'user_authorized_direct_reference', attributionRequired: false } }
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
  assert.equal(card.querySelectorAll('a').length, 0);
  assert.ok(!card.descendants().some(element => element.className === 'image-credit'));
  assert.equal(card.children[0].textContent, 'Drinks');
  assert.ok(elements.department.children.some(option => option.value === 'Soda'));
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
