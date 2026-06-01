# State Management Skill

Choose the right state management pattern based on app complexity.

## Complexity Levels

### Simple State (1-2 pages, < 5 pieces of state)
Use plain objects with update functions.

### Medium State (3-5 pages, 5-15 pieces of state)
Use a centralized store with event emitter pattern.

### Complex State (6+ pages, 15+ pieces of state)
Use immutable patterns with actions/reducers.

## Simple State Pattern

```javascript
// ✓ Good: Simple state for small apps
const AppState = {
    currentUser: null,
    cart: [],
    
    setUser(user) {
        this.currentUser = user;
        this.persist();
        this.render();
    },
    
    addToCart(item) {
        const existing = this.cart.find(i => i.id === item.id);
        if (existing) {
            existing.quantity += item.quantity || 1;
        } else {
            this.cart.push({ ...item, quantity: item.quantity || 1 });
        }
        this.persist();
        this.render();
    },
    
    persist() {
        try {
            localStorage.setItem('appState', JSON.stringify({
                currentUser: this.currentUser,
                cart: this.cart
            }));
        } catch (e) {
            console.warn('State persistence failed');
        }
    },
    
    load() {
        try {
            const saved = localStorage.getItem('appState');
            if (saved) {
                const data = JSON.parse(saved);
                this.currentUser = data.currentUser || null;
                this.cart = data.cart || [];
            }
        } catch (e) {
            console.warn('State load failed');
        }
    },
    
    render() {
        // Trigger UI updates
        document.dispatchEvent(new CustomEvent('statechange'));
    }
};

// Initialize
AppState.load();
document.addEventListener('statechange', renderUI);
```

## Medium State Pattern (Event Emitter Store)

```javascript
// ✓ Good: Centralized store with subscriptions
class Store extends EventTarget {
    constructor(initialState = {}) {
        super();
        this._state = initialState;
    }
    
    get state() {
        return this._state;
    }
    
    setState(updates) {
        this._state = { ...this._state, ...updates };
        this.dispatchEvent(new CustomEvent('change', { 
            detail: this._state 
        }));
        this.persist();
    }
    
    subscribe(callback) {
        const handler = (e) => callback(e.detail);
        this.addEventListener('change', handler);
        return () => this.removeEventListener('change', handler);
    }
    
    persist() {
        try {
            localStorage.setItem('store', JSON.stringify(this._state));
        } catch (e) {}
    }
    
    hydrate() {
        try {
            const saved = localStorage.getItem('store');
            if (saved) {
                this._state = JSON.parse(saved);
            }
        } catch (e) {}
    }
}

// Usage
const store = new Store({
    user: null,
    products: [],
    cart: [],
    orders: []
});

store.hydrate();

// Subscribe to changes
store.subscribe((state) => {
    renderCartBadge(state.cart.length);
});

// Update state
store.setState({ user: { name: 'John', role: 'customer' } });
```

## Domain-Specific Services

```javascript
// ✓ Good: Service per domain with shared store
const UserService = {
    store: null,  // Injected
    
    init(store) {
        this.store = store;
    },
    
    login(email, password) {
        // Validate
        const users = window.mockData?.users || [];
        const user = users.find(u => u.email === email);
        
        if (!user) {
            return { success: false, error: 'User not found' };
        }
        
        this.store.setState({ user });
        return { success: true, user };
    },
    
    logout() {
        this.store.setState({ user: null });
    },
    
    isLoggedIn() {
        return !!this.store.state.user;
    },
    
    isAdmin() {
        return this.store.state.user?.role === 'admin';
    }
};

const CartService = {
    store: null,
    
    init(store) {
        this.store = store;
    },
    
    getCart() {
        return this.store.state.cart || [];
    },
    
    addItem(product, quantity = 1) {
        const cart = [...this.getCart()];
        const existing = cart.find(i => i.id === product.id);
        
        if (existing) {
            existing.quantity += quantity;
        } else {
            cart.push({ ...product, quantity });
        }
        
        this.store.setState({ cart });
    },
    
    removeItem(productId) {
        const cart = this.getCart().filter(i => i.id !== productId);
        this.store.setState({ cart });
    },
    
    updateQuantity(productId, quantity) {
        const cart = this.getCart().map(item => 
            item.id === productId 
                ? { ...item, quantity: Math.max(1, quantity) }
                : item
        );
        this.store.setState({ cart });
    },
    
    getTotal() {
        return this.getCart().reduce(
            (sum, item) => sum + (item.price * item.quantity), 
            0
        );
    },
    
    clear() {
        this.store.setState({ cart: [] });
    }
};

// Initialize services with shared store
UserService.init(store);
CartService.init(store);
```

## Route-Based State

```javascript
// ✓ Good: Hash-based routing with state
const Router = {
    routes: {},
    currentRoute: null,
    
    register(hash, handler) {
        this.routes[hash] = handler;
    },
    
    navigate(hash) {
        window.location.hash = hash;
    },
    
    init() {
        window.addEventListener('hashchange', () => this.handleRoute());
        this.handleRoute();
    },
    
    handleRoute() {
        const hash = window.location.hash || '#/';
        const route = this.routes[hash];
        
        if (route) {
            this.currentRoute = hash;
            route();
        } else {
            // 404 handling
            this.navigate('#/');
        }
    }
};

// Register routes
Router.register('#/', renderHomePage);
Router.register('#/catalog', renderCatalogPage);
Router.register('#/cart', renderCartPage);
Router.register('#/checkout', renderCheckoutPage);
Router.register('#/account', renderAccountPage);

Router.init();
```

## State Persistence Patterns

### Selective Persistence
```javascript
// ✓ Good: Only persist what's needed
function persistState(state) {
    const toPersist = {
        user: state.user,
        cart: state.cart,
        preferences: state.preferences
        // Don't persist:
        // - products (reload from source)
        // - ui state (modals, loading)
        // - errors
    };
    
    try {
        localStorage.setItem('app_state', JSON.stringify(toPersist));
    } catch (e) {
        console.warn('Persistence failed:', e);
    }
}
```

### Migration Handling
```javascript
// ✓ Good: Handle state version changes
const CURRENT_VERSION = 2;

function loadState() {
    try {
        const raw = localStorage.getItem('app_state');
        if (!raw) return getDefaultState();
        
        const saved = JSON.parse(raw);
        
        // Migrate if needed
        if (saved._version !== CURRENT_VERSION) {
            return migrateState(saved);
        }
        
        return saved;
    } catch (e) {
        return getDefaultState();
    }
}

function migrateState(oldState) {
    // Handle version 1 → 2 migration
    if (!oldState._version || oldState._version === 1) {
        return {
            ...oldState,
            cart: (oldState.cart || []).map(item => ({
                ...item,
                quantity: item.quantity || item.qty || 1
            })),
            _version: CURRENT_VERSION
        };
    }
    return oldState;
}
```

## Best Practices

### Do
- Keep state normalized (no deep nesting)
- Use immutable updates (spread operator)
- Persist only essential data
- Validate state on load
- Provide default values

### Don't
- Store derived data (calculate on render)
- Store DOM references in state
- Mutate state directly
- Store sensitive data (passwords, tokens)
- Over-engineer for simple apps

### State Structure Example
```javascript
// ✓ Good: Flat, normalized state
const state = {
    // Entities (normalized by ID)
    products: {
        '1': { id: '1', name: 'Laptop', price: 999 },
        '2': { id: '2', name: 'Mouse', price: 29 }
    },
    
    // References (arrays of IDs)
    productIds: ['1', '2'],
    
    // Current user
    user: { id: '1', name: 'John', role: 'customer' },
    
    // Cart items (with quantities)
    cart: [
        { productId: '1', quantity: 1 },
        { productId: '2', quantity: 2 }
    ],
    
    // UI state (not persisted)
    ui: {
        loading: false,
        error: null,
        activeModal: null
    }
};
```
