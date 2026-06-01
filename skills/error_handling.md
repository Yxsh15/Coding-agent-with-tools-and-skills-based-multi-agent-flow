# Error Handling Skill

When operations fail, follow a structured recovery process to maintain app quality.

## Error Classification

### Transient Errors (Retry)
- Network timeouts
- Connection resets
- Rate limits (with backoff)
- Temporary file locks

### Permanent Errors (Don't Retry)
- Validation failures
- Missing required files
- Permission denied
- Invalid syntax

## Recovery Workflow

1. **Log the full error context** - tool name, arguments, error message
2. **Classify the error** - transient vs permanent
3. **For transient errors** - retry with exponential backoff (100ms, 200ms, 400ms)
4. **For permanent errors** - try fallback tool or report to user with fix suggestion

## Tool-Specific Recovery

### read_file fails
- Verify path exists with `glob`
- Check for typos in path
- Confirm file wasn't deleted by another operation

### apply_diff fails
- Read the latest file version first
- If diff context doesn't match, use `write_file` instead
- After 2 failures, always switch to full rewrite

### grep fails
- Use a simpler search pattern
- Fall back to `glob` to find files, then `read_file`
- Check if path exists before searching

### bash fails
- Log stderr output
- Check if command is in allowed list
- Provide reproducible minimal example

### write_file fails
- Verify parent directory exists (create with mkdir in objects/ or pages/)
- Check file path doesn't contain invalid characters
- Validate JSON syntax before writing .json files

## JavaScript Error Patterns

When generating JavaScript code, include proper error handling:

```javascript
// ✓ Good: Wrap async operations
async function fetchData() {
    try {
        const response = await fetch('/api/data');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch:', error);
        showErrorMessage('Unable to load data. Please try again.');
        return null;
    }
}

// ✓ Good: Validate DOM elements
function initializeUI() {
    const form = document.getElementById('checkout-form');
    if (!form) {
        console.warn('Checkout form not found');
        return;
    }
    form.addEventListener('submit', handleSubmit);
}

// ✓ Good: Check localStorage availability
function saveToStorage(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
        console.warn('Storage unavailable:', error);
    }
}

// ✓ Good: Robust initialization
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
```

## Recovery Strategies

### Partial Failure
- Save working state before attempting risky operations
- Track which items succeeded vs failed
- Retry only failed operations

### Conflicting Edits
- Always read the latest file version before editing
- Use apply_diff for surgical changes
- Reread and reapply if context mismatch

### Timeout
- Use exponential backoff: 100ms → 200ms → 400ms
- Maximum 3 retries for transient errors
- Give up gracefully with clear error message

## Validation Before Commit

Before declaring success:
1. Use `glob` to verify all expected files exist
2. Use `grep` to check cross-file references (IDs, routes, imports)
3. Read key files to confirm content is correct
4. Report any remaining issues clearly
