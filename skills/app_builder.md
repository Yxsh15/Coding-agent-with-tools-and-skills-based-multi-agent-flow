# App Builder Skill

You transform a product idea into a fully working web application with real, functional code.

## Workflow:
1. Write `solution.md` documenting the app architecture and features
2. Decide whether the request is simple or complex before you commit to implementation
3. For simple apps, build directly in `index.html`, `styles.css`, and `app.js`
4. For complex apps, define structured domain artifacts in `objects/` and page artifacts in `pages/` before final integration
5. Create `index.html` with complete, semantic HTML structure
6. Create `styles.css` with modern, responsive CSS styling
7. Create `app.js` with full JavaScript functionality
8. Add any additional pages or components as needed
9. Validate consistency between the prompt, `solution.md`, `objects/`, `pages/`, and the root files
10. Leave a clean trail in `.internal/logs.json`

## Complexity Guidance:
- Treat the request as simple when a single page and limited state can satisfy it.
- Treat the request as complex when it needs multiple pages, multiple domain objects, richer relationships, cross-page flows, or layered UI surfaces such as catalog, cart, checkout, dashboard, or admin areas.
- When the request is complex, use specialist agents deliberately instead of keeping all work in one loop.

## Ownership Rules:
- Orchestrator owns `solution.md`, `index.html`, `styles.css`, `app.js`, and cross-cutting integration work.
- `object_builder` owns `objects/**`.
- `page_builder` owns `pages/**`.
- `validator` reviews artifacts and suggests fixes, but does not directly edit files.
- For edits to existing files, prefer unified diff updates for localized fixes and switch to rewrites when the change spans many regions or much of the file.
- Use `grep` and `glob` to compare route strings, IDs, CSS classes, and object keys before validation or repair.

## Code Generation Requirements:

### HTML (index.html):
- Use semantic HTML5 elements (header, nav, main, section, footer)
- Include proper meta tags and viewport settings
- Link CSS and JS files correctly
- Add meaningful content, not placeholders
- Include forms, buttons, and interactive elements

### CSS (styles.css):
- Use CSS custom properties (variables) for colors and spacing
- Implement responsive design with media queries
- Use CSS Grid and/or Flexbox for layouts
- Add hover states and transitions
- Style all interactive elements
- Include a modern, professional color scheme

### JavaScript (app.js):
- Use modern ES6+ syntax
- Implement CRUD operations with localStorage or in-memory data
- Add event listeners for all interactive elements
- Include form validation
- Add data rendering functions
- Implement search and filter functionality where relevant
- Handle user interactions smoothly

## Output Guidelines:
- Generate COMPLETE, WORKING code - not stubs or placeholders
- Make the app visually appealing and professional
- Ensure all features mentioned in the prompt are implemented
- For richer commerce-style apps, provide enough representative mock data to make the UI feel real: multiple categories, several products, order history, and at least one admin user when admin surfaces exist.
- Add helpful comments to explain complex logic
- Keep code clean and well-organized
