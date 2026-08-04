Audit the `frontend/` directory in this repo for bugs, broken workflows, 
code quality issues, and performance problems. Do not modify any files 
— this is a read-only analysis and report. Cover the following:

1. **Setup & Context**
   - Identify the framework/stack, state management, routing, and build 
     tool in use
   - Note the Node/package manager version and confirm the project 
     installs/builds cleanly (run install + build/typecheck if possible 
     and capture any errors/warnings)

2. **Functional & Logic Bugs**
   - Scan for common bug patterns: unhandled promise rejections, missing 
     try/catch around async calls, race conditions in useEffect/watchers, 
     stale closures, incorrect dependency arrays, null/undefined access 
     without guards
   - Check for broken or inconsistent error handling (silent failures, 
     swallowed errors, generic catch blocks with no user feedback)
   - Check form validation logic for edge cases (empty states, invalid 
     input, double-submits)
   - Check API/data-fetching layers for missing loading/error/empty states
   - Flag any TODO/FIXME/HACK comments and briefly assess their risk

3. **Workflow / User-Flow Bugs**
   - Trace critical user flows (auth, checkout, form submission, 
     navigation, etc. — infer from the app) and check for broken states: 
     dead-end routes, back-button issues, state not resetting between 
     navigations, modals/dialogs not closing properly, unhandled 401/403/500 
     responses
   - Check routing config for orphaned routes, missing 404 handling, or 
     duplicate/conflicting route definitions
   - Check for memory leaks: uncanceled subscriptions, event listeners, 
     timers/intervals not cleaned up on unmount

4. **Code Quality for UI Experience**
   - Check component re-render behavior: unnecessary re-renders, missing 
     memoization where it matters (React.memo, useMemo, useCallback), 
     prop drilling that should be context/state
   - Check accessibility basics: semantic HTML, alt text, keyboard 
     navigation, focus management, ARIA where needed
   - Check for layout shift risks (images/media without dimensions, 
     fonts without fallback/display strategy, skeleton/loading states 
     missing)
   - Check consistency of loading/error/empty UI patterns across the app
   - Run/check linter and type-checker output and summarize recurring 
     violations (not just count them)

5. **Performance & Load Speed**
   - Check bundle composition: large dependencies, duplicate libraries, 
     unused/dead imports, missing tree-shaking opportunities
   - Check code-splitting: is lazy loading / dynamic import used for 
     routes and heavy components, or is everything in one bundle?
   - Check image/asset handling: unoptimized images, missing lazy 
     loading, no modern formats (webp/avif), no responsive sizing
   - Check for render-blocking resources, unnecessary large third-party 
     scripts, or excessive client-side data fetching that could be 
     server-rendered/cached
   - Check caching strategy (API responses, static assets, service worker 
     if applicable)
   - If a build exists, report actual bundle size output; if a lighthouse/
     web-vitals check is feasible in this environment, run it and report 
     LCP/CLS/TBT-style findings — otherwise flag this as a manual step 
     needed with real browser tooling

6. **Report Format**
   - Markdown report with:
     - Executive summary (top 5 critical issues)
     - Table: file/location, issue, category (bug/workflow/quality/
       performance), severity (critical/high/medium/low), suggested fix
     - Section per category above with details
     - A prioritized action list ranked by impact vs. effort, with 
       "quick wins" called out separately
   - Do not make any code changes — this is diagnostic only