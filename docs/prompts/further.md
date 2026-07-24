Not approved yet — three revisions, then re-show the table:

1. LEXICAL OVERLAP AUDIT. The benchmark must discriminate between naive
   retrieval and the full system, so it needs questions where the answer's
   identifiers do NOT appear in the question text. Tokenize each question
   and each truth.symbols entry (split snake_case and CamelCase); ignore
   the stoplist {http, httpx, request, response, client, url}. Report
   per-question overlap. Requirement: at least 9 of 20 questions with
   ZERO overlap. 

2. REWRITES. Keep q01–q07 as-is — an intentionally easy locate tier is
   fine. Rewrite the conceptual and flow tiers into user vocabulary,
   e.g.:
   - q08 → "When I pass auth=(user, pass), what does httpx actually add
     to the outgoing request?"
   - q09 → "How does httpx handle responses the server has compressed?"
   - q11 → "What controls how many simultaneous connections httpx keeps
     open?"
   - q13 → "When I pass json= or files= to a request, how does the body
     get produced?"
   - q16 → "If a server replies 302, what does httpx do next and how is
     the follow-up request built?"
   - q17 → "How does a request get retried after a 401 challenge?"
   Also swap the weakest duplicates (q11/q15 overlap heavily with other
   _config/_models questions) for two genuinely traversal-heavy
   candidates — verify ground truth against the clone before adopting:
   - "How does httpx decide which proxy applies to a given URL?"
   - "Why can response.text decode with the wrong charset — where does
     the charset come from?"

3. MULTI-FILE TRUTH for flow questions: truth.files is any-of, so list
   every file a correct answer would plausibly cite (q18 adds
   httpx/_client.py; check q16, q19, q20 similarly).

Also: approved — keep short names in truth.symbols, and write the match
rule into EVAL.md's rules line now: a symbol matches if the chunk
qualname equals it or ends with "." + it. Re-verify all edited ground
truth programmatically at the pinned SHA, then show me the revised
20 with the overlap audit output.