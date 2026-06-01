# Security Skill

Follow these security practices when generating web application code.

## Input Validation

### Sanitize User Input
- Validate type and length of all inputs
- Use allowlists over denylists
- Escape special characters before use

```javascript
// ✓ Good: Validate before processing
function validateEmail(email) {
    const pattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return typeof email === 'string' && 
           email.length <= 254 && 
           pattern.test(email);
}

function validateQuantity(value) {
    const num = parseInt(value, 10);
    return !isNaN(num) && num >= 1 && num <= 100;
}
```

### Form Validation
```javascript
// ✓ Good: Comprehensive form validation
function validateForm(formData) {
    const errors = [];
    
    if (!formData.name?.trim()) {
        errors.push('Name is required');
    } else if (formData.name.length > 100) {
        errors.push('Name too long');
    }
    
    if (!validateEmail(formData.email)) {
        errors.push('Invalid email address');
    }
    
    return errors;
}
```

## XSS Prevention

### DOM Manipulation
```javascript
// ✗ BAD: Direct innerHTML with user data
element.innerHTML = userInput;

// ✓ GOOD: Use textContent for user data
element.textContent = userInput;

// ✓ GOOD: Create elements safely
function createListItem(text) {
    const li = document.createElement('li');
    li.textContent = text;  // Auto-escapes HTML
    return li;
}

// ✓ GOOD: Template with escaped values
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
```

### When HTML is Needed
```javascript
// Only use innerHTML with trusted/sanitized content
function renderMarkdown(trustedHtml) {
    const container = document.getElementById('content');
    container.innerHTML = trustedHtml;  // Only if source is trusted
}

// Better: Use a sanitization library if available
function renderUserContent(userHtml) {
    // Strip dangerous tags
    const temp = document.createElement('div');
    temp.textContent = userHtml;
    return temp.innerHTML;  // Now safe
}
```

## Data Storage

### localStorage Guidelines
- Store only non-sensitive data
- Never store passwords or tokens in localStorage
- Validate data when reading back

```javascript
// ✓ Good: Safe storage patterns
const STORAGE_KEY = 'app_cart';

function saveCart(cart) {
    try {
        // Validate before saving
        if (!Array.isArray(cart)) return false;
        localStorage.setItem(STORAGE_KEY, JSON.stringify(cart));
        return true;
    } catch (e) {
        console.warn('Storage failed:', e);
        return false;
    }
}

function loadCart() {
    try {
        const data = localStorage.getItem(STORAGE_KEY);
        const cart = data ? JSON.parse(data) : [];
        // Validate structure
        return Array.isArray(cart) ? cart : [];
    } catch (e) {
        return [];
    }
}

// Clear sensitive data on logout
function logout() {
    localStorage.removeItem('user_preferences');
    sessionStorage.clear();
}
```

## Event Handling Security

```javascript
// ✓ Good: Validate event sources
document.addEventListener('message', (event) => {
    // Check origin for postMessage
    if (event.origin !== window.location.origin) {
        return;
    }
    handleMessage(event.data);
});

// ✓ Good: Prevent unintended navigation
document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', (e) => {
        e.preventDefault();
        const hash = link.getAttribute('href');
        // Validate hash before using
        if (/^#[a-zA-Z][\w-]*$/.test(hash)) {
            navigateTo(hash);
        }
    });
});
```

## URL Handling

```javascript
// ✗ BAD: Direct URL construction
window.location = 'page.html?user=' + username;

// ✓ GOOD: Use URL API
function buildUrl(page, params) {
    const url = new URL(page, window.location.origin);
    for (const [key, value] of Object.entries(params)) {
        url.searchParams.set(key, value);  // Auto-encodes
    }
    return url.toString();
}

// ✓ Good: Validate URLs before use
function isValidRedirect(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        return parsed.origin === window.location.origin;
    } catch {
        return false;
    }
}
```

## Object Model Security

When generating object definitions:
- Define field types and constraints
- Include validation rules
- Never store sensitive data in mockData

```json
{
    "objectName": "User",
    "attributes": [
        {
            "name": "email",
            "type": "string",
            "validation": {
                "pattern": "^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$",
                "maxLength": 254
            }
        },
        {
            "name": "role",
            "type": "string",
            "validation": {
                "enum": ["customer", "admin"]
            }
        }
    ]
}
```

## Security Checklist

Before finalizing an app:
- [ ] All user inputs are validated
- [ ] DOM updates use textContent for user data
- [ ] localStorage contains no sensitive data
- [ ] URLs are properly encoded
- [ ] Forms include client-side validation
- [ ] Error messages don't expose internals
