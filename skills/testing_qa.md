# Testing & QA Skill

Generate testable code and verify functionality before declaring success.

## Code Testability Principles

### Separation of Concerns
```javascript
// ✓ Good: Separate data logic from UI
const CartService = {
    items: [],
    
    addItem(product, quantity) {
        const existing = this.items.find(i => i.id === product.id);
        if (existing) {
            existing.quantity += quantity;
        } else {
            this.items.push({ ...product, quantity });
        }
        return this.items;
    },
    
    getTotal() {
        return this.items.reduce((sum, item) => 
            sum + (item.price * item.quantity), 0);
    },
    
    removeItem(productId) {
        this.items = this.items.filter(i => i.id !== productId);
        return this.items;
    }
};

// UI layer uses the service
function renderCart() {
    const items = CartService.items;
    // Update DOM
}
```

### Pure Functions for Logic
```javascript
// ✓ Good: Pure, testable functions
function calculateDiscount(subtotal, couponCode, validCoupons) {
    const coupon = validCoupons.find(c => c.code === couponCode);
    if (!coupon) return 0;
    if (coupon.minPurchase && subtotal < coupon.minPurchase) return 0;
    
    if (coupon.type === 'percentage') {
        return subtotal * (coupon.value / 100);
    }
    return Math.min(coupon.value, subtotal);
}

function formatPrice(cents) {
    return `$${(cents / 100).toFixed(2)}`;
}

function validateCartItem(item) {
    const errors = [];
    if (typeof item.quantity !== 'number' || item.quantity < 1) {
        errors.push('Invalid quantity');
    }
    if (typeof item.price !== 'number' || item.price < 0) {
        errors.push('Invalid price');
    }
    return errors;
}
```

## Manual Verification Steps

Before marking work complete, verify:

### Functional Tests
1. Load the app - no console errors
2. Navigate between all pages - links work
3. Submit each form - validation fires
4. Create/read/update/delete data - CRUD works
5. Refresh page - state persists correctly

### Edge Cases
1. Empty states - what shows when no data?
2. Boundary values - min/max quantities, prices
3. Invalid input - proper error messages
4. Missing data - graceful handling

### Cross-File Verification

**Rule: never assert a mismatch you haven't confirmed with a tool call.**

ID consistency workflow:
1. `grep("getElementById", "app.js")` → extract every ID the JS queries (these are required)
2. For each extracted ID, `grep("<id>", "pages/")` → confirm it exists in HTML
3. Report only IDs that are confirmed present in app.js AND confirmed absent from all pages

Route consistency workflow:
1. `grep("path ===", "app.js")` → extract all handled routes
2. `grep("href=\"#/", "")` → extract all link targets
3. Cross-reference: report links with no handler, and handlers with no reachable link

CSS class consistency workflow:
1. `grep("class=\"", "pages/")` → collect structural class names from HTML
2. For layout-shell classes (ending in -page, -layout, -shell, -grid, -container), `grep("<class>", "styles.css")`
3. Report only classes confirmed missing from CSS

**Never infer what an ID or class "should be" based on naming conventions.
Only report what grep tool output proves.**

## QA Checklist

### Before Completion
- [ ] App loads without console errors
- [ ] All navigation links work
- [ ] Forms validate input correctly
- [ ] Data persists across page refresh
- [ ] Interactive elements respond to clicks
- [ ] Mobile layout works (responsive)

### Cross-File Consistency
- [ ] HTML IDs match JavaScript selectors
- [ ] CSS classes match HTML usage
- [ ] Route hashes match page IDs
- [ ] Object references match defined objects

### Data Integrity
- [ ] Mock data follows defined schema
- [ ] Foreign keys reference valid IDs
- [ ] Required fields have values
- [ ] Enum fields use allowed values

## Integration Testing Approach

### Test User Flows
```
Flow: Add to Cart
1. Browse catalog page
2. Click "Add to Cart" on product
3. Verify cart icon updates
4. Navigate to cart page
5. Verify product appears
6. Adjust quantity
7. Verify total updates
```

### Test State Management
```javascript
// Verify state flows correctly
function testCartFlow() {
    // Setup
    CartService.items = [];
    
    // Add item
    CartService.addItem({ id: 1, name: 'Test', price: 1000 }, 2);
    console.assert(CartService.items.length === 1, 'Item added');
    console.assert(CartService.getTotal() === 2000, 'Total correct');
    
    // Update quantity
    CartService.addItem({ id: 1, name: 'Test', price: 1000 }, 1);
    console.assert(CartService.items[0].quantity === 3, 'Quantity updated');
    
    // Remove
    CartService.removeItem(1);
    console.assert(CartService.items.length === 0, 'Item removed');
    
    console.log('Cart tests passed');
}
```

## Validation Patterns

### Form Validation Testing
```javascript
function runValidationTests() {
    const tests = [
        { input: '', expected: false, desc: 'empty rejected' },
        { input: 'test@example.com', expected: true, desc: 'valid email' },
        { input: 'not-an-email', expected: false, desc: 'invalid email' },
        { input: 'a'.repeat(255), expected: false, desc: 'too long' },
    ];
    
    tests.forEach(({ input, expected, desc }) => {
        const result = validateEmail(input);
        console.assert(result === expected, `Email ${desc}: ${input}`);
    });
}
```

### Object Model Validation
Verify mock data matches schema:
```javascript
function validateMockData(schema, data) {
    const issues = [];
    
    schema.attributes.forEach(attr => {
        data.mockData.forEach((item, index) => {
            const value = item[attr.name];
            
            // Required check
            if (attr.required && (value === undefined || value === null)) {
                issues.push(`Item ${index}: missing required ${attr.name}`);
            }
            
            // Type check
            if (value !== undefined && typeof value !== attr.type) {
                issues.push(`Item ${index}: ${attr.name} wrong type`);
            }
            
            // Enum check
            if (attr.validation?.enum && !attr.validation.enum.includes(value)) {
                issues.push(`Item ${index}: ${attr.name} invalid enum`);
            }
        });
    });
    
    return issues;
}
```

## Debugging Helpers

When something isn't working:

1. **Check Console** - Look for errors
2. **Inspect Network** - Are assets loading?
3. **Verify Elements** - Do IDs/classes exist?
4. **Test Isolation** - Does feature work standalone?
5. **Check Data** - Is mock data valid?

```javascript
// Add debug logging during development
function debugState(label) {
    console.group(label);
    console.log('Cart:', JSON.stringify(CartService.items));
    console.log('User:', JSON.stringify(UserService.currentUser));
    console.log('Route:', window.location.hash);
    console.groupEnd();
}
```
