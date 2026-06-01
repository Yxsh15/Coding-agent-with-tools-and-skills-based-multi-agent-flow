# Accessibility Skill (WCAG 2.1 Level AA)

Generate accessible web applications following WCAG 2.1 guidelines.

## Semantic HTML

### Use Semantic Elements
```html
<!-- ✗ BAD: Non-semantic -->
<div class="button" onclick="submit()">Submit</div>
<div class="header">Page Title</div>

<!-- ✓ GOOD: Semantic elements -->
<button type="submit">Submit</button>
<h1>Page Title</h1>
```

### Document Structure
```html
<!-- ✓ Good: Proper heading hierarchy -->
<main>
    <h1>Product Catalog</h1>
    
    <section>
        <h2>Electronics</h2>
        <article>
            <h3>Laptop Pro</h3>
            <p>Description...</p>
        </article>
    </section>
    
    <section>
        <h2>Clothing</h2>
        <!-- ... -->
    </section>
</main>
```

### Landmarks
```html
<header role="banner">
    <nav aria-label="Main navigation">...</nav>
</header>

<main role="main">
    <!-- Primary content -->
</main>

<aside role="complementary">
    <!-- Secondary content -->
</aside>

<footer role="contentinfo">
    <!-- Footer content -->
</footer>
```

## Forms & Inputs

### Labels Are Required
```html
<!-- ✗ BAD: No label -->
<input type="email" placeholder="Email">

<!-- ✓ GOOD: Associated label -->
<label for="email">Email Address</label>
<input type="email" id="email" name="email" required>

<!-- ✓ GOOD: Wrapped label -->
<label>
    Email Address
    <input type="email" name="email" required>
</label>
```

### Form Accessibility
```html
<form aria-labelledby="form-title">
    <h2 id="form-title">Contact Us</h2>
    
    <div class="form-group">
        <label for="name">Full Name <span aria-hidden="true">*</span></label>
        <input 
            type="text" 
            id="name" 
            name="name" 
            required
            aria-required="true"
            autocomplete="name">
    </div>
    
    <div class="form-group">
        <label for="email">Email <span aria-hidden="true">*</span></label>
        <input 
            type="email" 
            id="email" 
            name="email"
            required
            aria-required="true"
            aria-describedby="email-hint"
            autocomplete="email">
        <p id="email-hint" class="hint">We'll never share your email.</p>
    </div>
    
    <button type="submit">Send Message</button>
</form>
```

### Error Messages
```html
<div class="form-group">
    <label for="password">Password</label>
    <input 
        type="password" 
        id="password"
        aria-invalid="true"
        aria-describedby="password-error">
    <p id="password-error" class="error" role="alert">
        Password must be at least 8 characters.
    </p>
</div>
```

## Images & Media

### Alternative Text
```html
<!-- ✓ Good: Descriptive alt text -->
<img src="product.jpg" alt="Blue running shoes, side view">

<!-- ✓ Good: Decorative images -->
<img src="decoration.png" alt="" role="presentation">

<!-- ✓ Good: Complex images -->
<figure>
    <img src="chart.png" alt="Sales chart showing 25% growth">
    <figcaption>Q4 2024 sales increased 25% year-over-year</figcaption>
</figure>
```

### Icon Buttons
```html
<!-- ✗ BAD: No accessible label -->
<button><i class="icon-cart"></i></button>

<!-- ✓ GOOD: With accessible label -->
<button aria-label="Add to cart">
    <i class="icon-cart" aria-hidden="true"></i>
</button>

<!-- ✓ GOOD: With visible text -->
<button>
    <i class="icon-cart" aria-hidden="true"></i>
    <span>Add to Cart</span>
</button>
```

## Keyboard Navigation

### Focus Management
```css
/* ✓ Good: Visible focus indicators */
:focus {
    outline: 2px solid #005fcc;
    outline-offset: 2px;
}

/* Never remove focus outline completely */
:focus:not(:focus-visible) {
    outline: none;  /* Only for mouse users */
}

:focus-visible {
    outline: 2px solid #005fcc;
}
```

### Interactive Elements
```html
<!-- All interactive elements must be focusable -->
<a href="#section">Skip to content</a>
<button type="button">Open Menu</button>
<input type="text">
<select>...</select>
<textarea></textarea>

<!-- Custom controls need tabindex -->
<div 
    role="button" 
    tabindex="0"
    onclick="toggle()"
    onkeydown="handleKeydown(event)">
    Toggle
</div>
```

### Keyboard Handlers
```javascript
// ✓ Good: Support keyboard interaction
function handleKeydown(event) {
    if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        toggle();
    }
    if (event.key === 'Escape') {
        closeMenu();
    }
}

// ✓ Good: Trap focus in modals
function trapFocus(modal) {
    const focusable = modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    
    modal.addEventListener('keydown', (e) => {
        if (e.key !== 'Tab') return;
        
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    });
    
    first.focus();
}
```

## Color & Contrast

### Contrast Requirements
```css
/* Text contrast: 4.5:1 for normal, 3:1 for large */

/* ✓ Good: High contrast */
.text {
    color: #1a1a1a;      /* Dark text */
    background: #ffffff;  /* Light background */
}

/* ✓ Good: Using CSS custom properties */
:root {
    --text-primary: #1a1a1a;    /* Passes 4.5:1 on white */
    --text-secondary: #595959;   /* Passes 4.5:1 on white */
    --bg-primary: #ffffff;
    --focus-color: #005fcc;
}
```

### Don't Rely on Color Alone
```html
<!-- ✗ BAD: Color only -->
<span class="status-green">Active</span>

<!-- ✓ GOOD: Color + text/icon -->
<span class="status status-active">
    <i class="icon-check" aria-hidden="true"></i>
    Active
</span>

<!-- ✓ Good: Required field indicator -->
<label>
    Email <span class="required" aria-hidden="true">*</span>
    <span class="sr-only">(required)</span>
</label>
```

## Screen Reader Support

### Visually Hidden Text
```css
/* For screen reader only content */
.sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}
```

### Live Regions
```html
<!-- Announce dynamic updates -->
<div aria-live="polite" aria-atomic="true" class="sr-only">
    <!-- JS updates this with status messages -->
</div>

<!-- Announce errors immediately -->
<div role="alert" aria-live="assertive">
    <!-- Error messages appear here -->
</div>
```

```javascript
// ✓ Good: Announce changes
function showNotification(message) {
    const region = document.getElementById('live-region');
    region.textContent = message;
}

function updateCartCount(count) {
    const badge = document.getElementById('cart-count');
    badge.textContent = count;
    showNotification(`Cart updated: ${count} items`);
}
```

## Accessibility Checklist

### Structure
- [ ] Heading hierarchy is logical (h1 → h2 → h3)
- [ ] Landmarks are used (main, nav, header, footer)
- [ ] Skip link provided for keyboard users

### Forms
- [ ] All inputs have labels
- [ ] Required fields are indicated
- [ ] Errors are announced and described
- [ ] Autocomplete attributes used

### Interaction
- [ ] All features work with keyboard only
- [ ] Focus is visible on all elements
- [ ] Focus order is logical
- [ ] Escape closes modals/menus

### Content
- [ ] Images have alt text
- [ ] Color is not the only indicator
- [ ] Text contrast meets 4.5:1
- [ ] Links have descriptive text

### Dynamic Content
- [ ] Updates are announced via live regions
- [ ] Loading states are communicated
- [ ] Errors are announced with role="alert"
