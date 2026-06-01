# Performance Skill

Generate performant web applications following optimization best practices.

## DOM Performance

### Batch DOM Updates
```javascript
// ✗ BAD: Multiple reflows
items.forEach(item => {
    const li = document.createElement('li');
    li.textContent = item.name;
    list.appendChild(li);  // Causes reflow each time
});

// ✓ GOOD: Use DocumentFragment
const fragment = document.createDocumentFragment();
items.forEach(item => {
    const li = document.createElement('li');
    li.textContent = item.name;
    fragment.appendChild(li);
});
list.appendChild(fragment);  // Single reflow
```

### Avoid Layout Thrashing
```javascript
// ✗ BAD: Read-write-read-write pattern
elements.forEach(el => {
    const height = el.offsetHeight;  // Read
    el.style.height = height + 10 + 'px';  // Write
});

// ✓ GOOD: Batch reads, then batch writes
const heights = elements.map(el => el.offsetHeight);  // All reads
elements.forEach((el, i) => {
    el.style.height = heights[i] + 10 + 'px';  // All writes
});
```

### Efficient Selectors
```javascript
// ✗ BAD: Query in loop
items.forEach(item => {
    const container = document.querySelector('.container');  // Queries every time
    container.appendChild(createItem(item));
});

// ✓ GOOD: Cache selectors
const container = document.querySelector('.container');
items.forEach(item => {
    container.appendChild(createItem(item));
});
```

## Event Handling

### Event Delegation
```javascript
// ✗ BAD: Event listener per item
document.querySelectorAll('.product-card').forEach(card => {
    card.addEventListener('click', handleClick);
});

// ✓ GOOD: Single delegated listener
document.querySelector('.product-grid').addEventListener('click', (e) => {
    const card = e.target.closest('.product-card');
    if (card) handleClick(card);
});
```

### Debounce & Throttle
```javascript
// ✓ Good: Debounce for input events
function debounce(fn, delay) {
    let timeoutId;
    return (...args) => {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => fn(...args), delay);
    };
}

const handleSearch = debounce((query) => {
    filterProducts(query);
}, 300);

searchInput.addEventListener('input', (e) => handleSearch(e.target.value));

// ✓ Good: Throttle for scroll events
function throttle(fn, limit) {
    let lastCall = 0;
    return (...args) => {
        const now = Date.now();
        if (now - lastCall >= limit) {
            lastCall = now;
            fn(...args);
        }
    };
}

window.addEventListener('scroll', throttle(handleScroll, 100));
```

### Cleanup Event Listeners
```javascript
// ✓ Good: Remove listeners when not needed
function initModal(modal) {
    const handleEscape = (e) => {
        if (e.key === 'Escape') closeModal();
    };
    
    function closeModal() {
        document.removeEventListener('keydown', handleEscape);
        modal.classList.add('hidden');
    }
    
    document.addEventListener('keydown', handleEscape);
    modal.querySelector('.close-btn').onclick = closeModal;
}
```

## Memory Management

### Avoid Memory Leaks
```javascript
// ✗ BAD: Closure holding references
function createHandler(element) {
    const data = fetchLargeData();  // Large object
    element.onclick = () => {
        console.log(data);  // data never garbage collected
    };
}

// ✓ GOOD: Reference only what's needed
function createHandler(element) {
    const data = fetchLargeData();
    const id = data.id;  // Extract only needed value
    element.onclick = () => {
        console.log(id);  // Only id is retained
    };
}
```

### Use WeakMap for Caches
```javascript
// ✓ Good: WeakMap allows garbage collection
const elementData = new WeakMap();

function setData(element, data) {
    elementData.set(element, data);
}

function getData(element) {
    return elementData.get(element);
}
// When element is removed from DOM, its data is automatically cleaned up
```

## Rendering Performance

### Virtual Scrolling for Large Lists
```javascript
// ✓ Good: Only render visible items
function renderVisibleItems(container, items, itemHeight) {
    const scrollTop = container.scrollTop;
    const viewportHeight = container.clientHeight;
    
    const startIndex = Math.floor(scrollTop / itemHeight);
    const endIndex = Math.min(
        startIndex + Math.ceil(viewportHeight / itemHeight) + 1,
        items.length
    );
    
    const fragment = document.createDocumentFragment();
    for (let i = startIndex; i < endIndex; i++) {
        const item = createItemElement(items[i]);
        item.style.transform = `translateY(${i * itemHeight}px)`;
        fragment.appendChild(item);
    }
    
    container.innerHTML = '';
    container.style.height = `${items.length * itemHeight}px`;
    container.appendChild(fragment);
}
```

### Lazy Loading Images
```html
<!-- ✓ Good: Native lazy loading -->
<img src="product.jpg" alt="Product" loading="lazy">

<!-- ✓ Good: With placeholder -->
<img 
    src="placeholder.jpg" 
    data-src="product.jpg" 
    alt="Product" 
    class="lazy">
```

```javascript
// ✓ Good: Intersection Observer for lazy loading
const imageObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const img = entry.target;
            img.src = img.dataset.src;
            img.classList.remove('lazy');
            imageObserver.unobserve(img);
        }
    });
});

document.querySelectorAll('img.lazy').forEach(img => {
    imageObserver.observe(img);
});
```

## CSS Performance

### Efficient Animations
```css
/* ✓ Good: Use transform and opacity (GPU accelerated) */
.card {
    transition: transform 0.3s ease, opacity 0.3s ease;
}

.card:hover {
    transform: translateY(-4px);
    opacity: 0.9;
}

/* ✗ BAD: Animating layout properties */
.card-bad {
    transition: margin-top 0.3s, height 0.3s;  /* Causes reflow */
}

/* ✓ Good: will-change for known animations */
.modal {
    will-change: transform, opacity;
}
```

### Reduce Selector Complexity
```css
/* ✗ BAD: Over-qualified selector */
div.container > ul.list > li.item > a.link { }

/* ✓ GOOD: Simple, flat selectors */
.product-link { }

/* ✗ BAD: Universal selector in complex chain */
.container * .item { }

/* ✓ GOOD: Direct targeting */
.container-item { }
```

## Data Handling

### Pagination for Large Data
```javascript
// ✓ Good: Load data in pages
async function loadProducts(page = 1, pageSize = 20) {
    const mockProducts = window.mockData?.products || [];
    
    const start = (page - 1) * pageSize;
    const end = start + pageSize;
    
    return {
        items: mockProducts.slice(start, end),
        total: mockProducts.length,
        hasMore: end < mockProducts.length
    };
}

// Render with "Load More" button
let currentPage = 1;

async function loadMore() {
    const result = await loadProducts(++currentPage);
    appendProducts(result.items);
    
    if (!result.hasMore) {
        loadMoreBtn.style.display = 'none';
    }
}
```

### Memoization
```javascript
// ✓ Good: Cache expensive calculations
function memoize(fn) {
    const cache = new Map();
    return (...args) => {
        const key = JSON.stringify(args);
        if (cache.has(key)) {
            return cache.get(key);
        }
        const result = fn(...args);
        cache.set(key, result);
        return result;
    };
}

const calculateDiscount = memoize((price, discountPercent) => {
    // Expensive calculation
    return price * (1 - discountPercent / 100);
});
```

## Performance Checklist

### Loading
- [ ] Images use lazy loading
- [ ] Scripts defer/async where appropriate
- [ ] CSS is minimal and efficient
- [ ] Initial render is fast (< 3s)

### Runtime
- [ ] No layout thrashing
- [ ] Event delegation used
- [ ] Scroll/resize handlers throttled
- [ ] Large lists use virtual scrolling

### Memory
- [ ] Event listeners cleaned up
- [ ] No circular references
- [ ] WeakMap for element metadata
- [ ] Large data paginated

### CSS
- [ ] Animations use transform/opacity
- [ ] Selectors are simple and flat
- [ ] No expensive pseudo-selectors in loops
- [ ] will-change used sparingly
