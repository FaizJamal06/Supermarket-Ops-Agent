# PRD: Supermarket Ops Agent
**Nebula KnowLab — Engineering Take-Home**
**Author:** Faiz | **Assignment Duration:** 5 calendar days | **Self-Imposed Target:** 3 days | **Version:** 2.0

---

## 0. Document Scope

This PRD is the single source of truth for implementation. It intentionally over-specifies the "hard parts" (invariants, schema, tool contracts) because that is where the assignment is graded, and under-specifies UI/UX polish because there is none — the chat *is* the product. Every section maps directly to a numbered requirement or evaluation criterion in the assignment brief.

> **Grading signal (per the brief):** "This is not a CRUD app with a chatbot in front of it. It's an agent that reasons over messy human requests and keeps a store's books consistent." The architecture below is designed so that the LLM is the *dispatcher*, never the *ledger*.

---

## 1. Problem Statement

A kirana (Indian neighborhood grocery) owner runs a high-friction, high-trust, low-tech business. Every day they:

- Receive stock from 5–10 different distributors, often mid-transaction, with a supplier standing at the counter.
- Cut 100+ small bills, frequently for regulars who ask for the same basket with small variations ("same as yesterday, minus the sugar").
- Extend informal credit (**khata**) to trusted customers — a socially binding ledger with no formal contract, historically kept in a paper notebook.
- Need day-end totals split by GST slab and payment mode for their accountant, without ever having used a POS system.
- Operate **standing on their feet, one hand on a phone**, in the middle of a transaction — not seated at a keyboard filling out forms.

### Why not a GUI/CRUD app?

A form-based app forces the owner to translate their own mental model ("Ramesh wants his usual, but no sugar this time, and put it on his khata") into a sequence of dropdowns, quantity steppers, and "confirm" buttons. That translation tax is exactly what keeps kirana owners on pen-and-paper today — they've already rejected GUI POS software for decades. The interface must accept the same terse, elliptical, error-tolerant language they already use with a human assistant.

### Why not a rigid keyword/regex bot?

A fixed grammar (`/bill add <sku> <qty>`) pushes the translation tax back onto the owner in a different form — they now have to remember *syntax* instead of clicking *buttons*. It also can't handle the genuinely ambiguous cases the domain is full of ("add atta" — loose or Aashirvaad 5kg? which Maggi variant?) without either guessing wrong (corrupting the books) or crashing out to an error message.

> **Assignment §1 warning:** "If you find yourself writing a large if/elif intent router full of regexes, you've taken the wrong turn." This is a disqualifying anti-pattern.

### Why an LLM-orchestrated agent is the correct solution

The owner's requests are *semantically* well-defined (a bill, a stock receipt, a khata entry) but *syntactically* unconstrained. An agent that reasons over intent and calls typed, invariant-enforcing tools gets the best of both: natural language in, but every mutation still passes through code that cannot double-decrement stock, invent a price, or oversell — because the LLM is the *dispatcher*, never the *ledger*.

---

## 2. Solution Overview

**Interface:** Telegram bot (single channel, no web app, no admin panel, no forms). One Telegram chat = one "session," but business state (stock, khata, bills, preferences) lives in a durable store *outside* the conversation, keyed to the owner's Telegram user/shop ID — not to the chat's context window.

**Core loop (agentic, not scripted):**

```
Telegram update arrives
   → dedupe on update_id (idempotency gate, before any LLM call)
   → load durable context (open draft bill if any, owner preferences)
   → LLM turn: observe (message + tool results so far)
                reason (what does the owner want; what's missing/ambiguous)
                act (call 0..N tools, possibly chained in one turn)
   → tool executes against DB with its own invariant checks (independent of the LLM's judgment)
   → tool result fed back to LLM
   → LLM decides: call another tool, ask a clarifying question, or respond in natural language
   → final natural-language reply sent to Telegram
```

The LLM never writes SQL and never computes GST, totals, or stock deltas itself — it only decides *which tool, with which arguments, in which order*. All arithmetic and all business rules live in tool code. This is the load-bearing architectural decision of the whole system and is referenced repeatedly below.

**Harness:** Direct Anthropic Messages API with native tool use (see §4.1 for justification over Claude Agent SDK / Deep Agent / Vercel AI SDK for this specific scope).

---

## 3. User Stories

Numbered exhaustively, grouped by capability. Each maps directly to §3 of the assignment brief and to a tool in §4.3.

### A. Stock & Catalog Management

1. As the owner, I want to say "50 packets of Maggi came in, cost ₹12, MRP ₹14" so that stock and cost basis update without me opening a spreadsheet.
2. As the owner, I want to add a brand-new SKU by saying "new item: Amul Butter 100g, GST 12%, MRP ₹62" so that I don't need a separate onboarding form for new products.
3. As the owner, I want the agent to ask me for a missing but required field (e.g., HSN code, cost price) rather than guessing it, so that my tax filings stay correct.
4. As the owner, I want to ask "how much sugar is left?" and get the true current stock, so that I never oversell what I don't have.
5. As the owner, I want to ask "what's running out?" and get every SKU at or below its reorder level, so that I know what to order before I run out.
6. As the owner, I want stock updates from receiving and from billing to be atomic, so that two things happening at once (a delivery and a sale) never leave my stock count wrong.
7. As the owner, I want the agent to refuse a stock correction that would make quantity negative (e.g., "remove 20kg rice" when I only have 15kg), so that a typo never corrupts my inventory.
8. As the owner, I want every stock change (receipt, sale, correction) recorded as an immutable ledger entry, so that I can reconstruct "why is stock what it is" at any point.

### B. Multi-turn Billing

9. As the owner, I want to say "make a bill: 2kg sugar, 1 Aashirvaad atta 5kg, 4 Maggi, 1 Amul butter, UPI" and get a running draft, so that I don't have to itemize one line at a time.
10. As the owner, I want to add to a bill I've already started ("also add 2 Parle-G") so that I can build the bill the way the customer actually shops — incrementally.
11. As the owner, I want to edit a bill mid-build ("drop the butter, make it 6 Maggi") so that last-minute customer changes don't force me to restart.
12. As the owner, I want an ambiguous item ("add atta") to trigger a clarifying question ("Which one — Aashirvaad 5kg or loose?") instead of a silent wrong guess, so that the bill is always correct.
13. As the owner, I want stock to be decremented **only when I finalize** the bill, not while it's still a draft, so that abandoned or edited-away line items never phantom-reduce my inventory.
14. As the owner, I want to see a running subtotal and tax as I build the bill, so that I can tell the customer the total before they pay.
15. As the owner, I want the finalized bill to record the payment mode (Cash/UPI/Card) and a reference where relevant, so that my day-end reconciliation is accurate.
16. As the owner, if my phone hiccups and Telegram resends the same "finalize" tap, I want the bill charged exactly once, so that I never double-bill a customer or double-deduct my own stock.
17. As the owner, I want to cut two different bills for two different customers "at the same time" (one on hold while I help another customer) without them interfering with each other's stock deductions or item state.

### C. Khata (Credit Ledger)

18. As the owner, I want to say "put ₹500 on Ramesh's credit" so that a credit sale is recorded against that customer without a formal contract or paperwork.
19. As the owner, I want to ask "what's Ramesh's balance?" and get his current outstanding amount, so that I know how much he owes before extending more credit.
20. As the owner, I want to say "Ramesh paid ₹300" and have his balance reduce accordingly, so that partial settlements are tracked accurately.
21. As the owner, I want the agent to refuse to settle a khata payment against a customer who has no open balance (or doesn't exist), so that I don't accidentally create a negative-owed / phantom customer record.
22. As the owner, I want every khata movement (charge, payment) timestamped and itemized, so that if Ramesh disputes his balance I can show him exactly how we got there.
23. As the owner, I want the option to link a khata charge to the bill that generated it, so that "why does Ramesh owe ₹500" traces back to an actual list of goods.

### D. Daily Close & Analytics

24. As the owner, I want to ask "today's sales?" or "close the day" and get total revenue, tax collected, cash vs UPI vs card split, and top-selling items, so that I can hand my accountant a clean daily summary.
25. As the owner, I want the day-close numbers to reconcile exactly with the underlying bills (no rounding drift), so that my books are trustworthy.
26. As the owner, I want to request a GST-correct PDF invoice for any specific bill ("send me that bill as a PDF"), so that customers who need one (for their own accounting) can get it immediately.
27. As the owner, I want the invoice to show CGST/SGST split per line, HSN codes, and correctly rounded totals, so that it's actually usable for tax purposes and not just a receipt-shaped document.
28. As the owner, I want to request "this week's sales analysis deck" and get a PPTX with real charts (revenue trend, top items, stock health, GST collected), so that I can review my business at a glance instead of staring at raw numbers.

### E. Preferences & Memory

29. As the owner, I want to say "always assume UPI unless I say cash" and have that apply to every future bill, even in a brand-new chat, so that I don't repeat myself every session.
30. As the owner, I want to say "default atta = Aashirvaad 5kg" so that "add atta" resolves without a clarifying question from then on, unless I explicitly ask for something else.
31. As the owner, I want to set my shop name and GSTIN once, and have every invoice use it automatically, so that I never have to dictate my own business's legal details per bill.
32. As the owner, I want these preferences to survive a bot restart or redeploy, so that my settings aren't tied to an ephemeral conversation.

### F. Guardrails & Trust

33. As the owner, I want the agent to refuse a bill that would sell more of an item than I currently have in stock, so that I physically can never oversell.
34. As the owner, I want the agent to warn or refuse if I try to sell below cost price, so that a fat-fingered price doesn't lose me money silently.
35. As the owner, I want the agent to never delete a stock or khata record outright (only append correcting/reversing entries), so that my history is always auditable.
36. As the owner, I want the agent to never invent a product, price, or GST rate that isn't in my catalog, so that I can trust every number it gives me.

### G. Real Artifacts (Document Generation)

37. As the owner, I want a PDF invoice that looks like a proper GST invoice — with my shop name, GSTIN, HSN codes, per-line CGST/SGST breakup, and rounded totals — not a screenshot or plain text dump.
38. As the owner, I want a PPTX analysis deck with real charts (bar/line/pie) analyzing my sales, top items, stock health, and GST collected — not just text slides.
39. As the owner, I want both artifacts generated entirely by the agent's own tools — triggered by a natural-language request, not an external script.

### H. Edge Cases & Resilience

40. As the owner, if I start a bill and abandon it (never finalize), I want my stock to remain unchanged, so that phantom drafts don't silently eat my inventory.
41. As the owner, I want to be able to void a finalized bill if it was a mistake, with stock restored via compensating ledger entries (never deletion).
42. As the owner, I want the bot to handle back-to-back rapid messages gracefully (e.g., "add sugar" immediately followed by "2kg") without corrupting state, so I can type as fast as I think.

---

## 4. Implementation Decisions ("The Hard Parts")

### 4.1 Agent Harness

**Choice: Direct Anthropic Messages API, native tool-use, single control loop.** No LangGraph, no Claude Agent SDK, no Deep Agent, no Vercel AI SDK for the core loop.

**Justification against each named alternative:**

| Harness | Why not for this scope |
|---|---|
| **Claude Agent SDK** | Adds a dependency layer on top of the Messages API without adding capability we need. We have ~12–15 tools, one agent, one store — the SDK's value (managed tool execution, multi-agent coordination) is overkill. Going direct keeps the loop transparent and debuggable. |
| **Deep Agent** | Designed for autonomous multi-step task execution with delegation. Our domain is interactive (owner ↔ agent conversation), not fire-and-forget autonomous. Each turn is short and reactive; no need for task planning/decomposition frameworks. |
| **Vercel AI SDK** | Optimized for streaming UI in a web frontend (React Server Components, streaming responses). There is no web frontend here — Telegram-only — so its main value proposition doesn't apply. |
| **LangGraph** | The task is explicitly *not* a multi-step workflow graph with well-known nodes and edges. The owner jumps between intents arbitrarily, mid-conversation, in any order. A LangGraph-style node-per-command state machine is a re-implementation of the "regex router" anti-pattern the brief explicitly warns against, just drawn as a graph instead of an if/elif chain. |

**Why direct Anthropic API *is* the right choice:**

- Claude's native tool-use loop (`tool_use` → `tool_result` → continue) already **is** the observe → reason → act → feed-back loop required in §5 of the brief. Adding a heavier orchestration framework on top adds indirection without adding capability for a single-agent, single-store system.
- The **skills/tools I author** — not the harness — are the graded surface. A thin harness keeps 100% of engineering effort on tool design, invariants, and the domain model, which is where the brief says the signal lives ("did they actually think about it").
- **Anti-pattern avoided explicitly:** there is no `if intent == "bill"` branch anywhere in the code. The system prompt describes available tools and domain concepts; Claude decides tool selection, argument extraction, and multi-tool chaining per turn. A regex layer exists *only* at the transport level (Telegram webhook parsing), never for intent classification.

**Loop mechanics per Telegram update:**
1. Idempotency gate on `telegram_update_id` (§4.4) — reject duplicates before touching the LLM or DB.
2. Load `conversation_state` for this `chat_id`: open draft bill (if any), last N turns for local context.
3. Load durable `preferences` for this `owner_id` (survives across chats — this is the memory requirement).
4. Single Messages API call with the full tool schema available; loop on `tool_use` blocks, executing each tool server-side and appending `tool_result` blocks, until Claude returns a plain text turn.
5. Persist any draft-bill mutation to DB *inside* the tool call itself (not after the loop), so a crash mid-loop never loses a committed step.
6. Send Claude's final text back to Telegram.

### 4.2 Database Schema

**Engine:** SQLite in WAL mode for the 3-day build (trivial to swap to Postgres later; WAL mode gives real concurrent-reader/single-writer semantics which is sufficient here). All monetary values stored as **integer paise** (₹1 = 100), never floats — this single decision eliminates most GST rounding bugs.

#### 4.2.1 Seed Catalog — Required SKUs from the Brief

The assignment explicitly names these products (§2). The seed must include all of them:

| Product | Unit | Loose? | GST Slab | HSN Code | Notes |
|---|---|---|---|---|---|
| Aashirvaad Atta 5kg | packet | No | 5% | 1101 | Packaged staple |
| Tata Salt 1kg | packet | No | 5% | 2501 | Packaged staple |
| Amul Butter 100g | packet | No | 12% | 0405 | Dairy product |
| Fortune Sunflower Oil 1L | packet | No | 5% | 1512 | Packaged cooking oil |
| Maggi 70g | packet | No | 18% | 1902 | Instant noodles |
| Parle-G | packet | No | 18% | 1905 | Biscuits |
| Surf Excel | packet | No | 18% | 3402 | Detergent |
| Sugar (loose) | kg | Yes | 0% | 1701 | Loose staple |
| Rice (loose) | kg | Yes | 0% | 1006 | Loose staple |
| Dal (loose) | kg | Yes | 0% | 0713 | Loose pulse |
| Atta (loose) | kg | Yes | 0% | 1101 | Loose staple — disambiguates from Aashirvaad |

#### 4.2.2 Schema Definition

```sql
-- ============================================================
-- CATALOG
-- ============================================================
CREATE TABLE products (
  sku_id        TEXT PRIMARY KEY,           -- e.g. 'AASHIRVAAD_ATTA_5KG'
  name          TEXT NOT NULL,
  unit          TEXT NOT NULL,               -- 'kg' | 'g' | 'l' | 'ml' | 'packet' | 'piece' | 'dozen'
  is_loose      INTEGER NOT NULL DEFAULT 0,  -- loose (sold by weight/volume, no fixed pack)
  hsn_code      TEXT NOT NULL,
  gst_rate_bps  INTEGER NOT NULL,            -- basis points, e.g. 500 = 5%, 1800 = 18%, 0 = exempt
  cost_price_paise INTEGER NOT NULL,
  mrp_paise     INTEGER NOT NULL,
  sell_price_paise INTEGER NOT NULL,
  reorder_level_qty REAL NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- ============================================================
-- STOCK LEDGER — append-only, the ONLY writer of truth for stock quantity
-- ============================================================
CREATE TABLE stock_ledger (
  ledger_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  sku_id        TEXT NOT NULL REFERENCES products(sku_id),
  delta_qty     REAL NOT NULL,               -- positive = receipt/correction-in, negative = sale/correction-out
  reason        TEXT NOT NULL,               -- 'RECEIPT' | 'SALE' | 'CORRECTION' | 'REVERSAL'
  ref_type      TEXT,                        -- 'bill' | 'manual' | NULL
  ref_id        TEXT,                        -- bills.bill_id if reason='SALE'
  balance_after REAL NOT NULL CHECK(balance_after >= 0),  -- DB-enforced oversell guard
  created_at    TEXT NOT NULL,
  idempotency_key TEXT UNIQUE                -- see §4.4
);
-- current stock = SELECT balance_after FROM stock_ledger WHERE sku_id=? ORDER BY ledger_id DESC LIMIT 1
-- never a mutable `products.qty` column — this is deliberate, see §4.4.3

-- Convenience view for quick stock lookups (avoid repeated subqueries)
CREATE VIEW stock_current AS
SELECT sl.sku_id, sl.balance_after AS qty, p.name, p.reorder_level_qty
FROM stock_ledger sl
JOIN products p ON sl.sku_id = p.sku_id
WHERE sl.ledger_id = (
  SELECT MAX(ledger_id) FROM stock_ledger WHERE sku_id = sl.sku_id
);

-- ============================================================
-- BILLS — header + lines, with explicit draft/final state machine
-- ============================================================
CREATE TABLE bills (
  bill_id       TEXT PRIMARY KEY,            -- UUID, generated at draft-open time
  owner_id      TEXT NOT NULL,
  chat_id       TEXT NOT NULL,
  status        TEXT NOT NULL CHECK(status IN ('DRAFT','FINALIZED','VOID')),
  customer_name TEXT,                        -- nullable, for walk-in
  khata_customer_id TEXT REFERENCES khata_accounts(customer_id),
  payment_mode  TEXT CHECK(payment_mode IN ('CASH','UPI','CARD','KHATA')),
  payment_ref   TEXT,
  subtotal_paise INTEGER,                    -- computed at finalize, not draft
  cgst_paise    INTEGER,
  sgst_paise    INTEGER,
  total_paise   INTEGER,
  finalized_at  TEXT,
  finalize_idempotency_key TEXT UNIQUE,       -- see §4.4
  created_at    TEXT NOT NULL
);

CREATE TABLE bill_lines (
  line_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  bill_id       TEXT NOT NULL REFERENCES bills(bill_id),
  sku_id        TEXT NOT NULL REFERENCES products(sku_id),
  qty           REAL NOT NULL,
  unit_price_paise INTEGER NOT NULL,          -- snapshot at add-time, immutable once bill finalized
  gst_rate_bps  INTEGER NOT NULL,             -- snapshot
  hsn_code      TEXT NOT NULL,                -- snapshot for invoice rendering
  line_subtotal_paise INTEGER NOT NULL,       -- qty × unit_price_paise
  line_cgst_paise INTEGER NOT NULL,
  line_sgst_paise INTEGER NOT NULL,
  UNIQUE(bill_id, sku_id)                     -- one line per SKU per bill; "add 2 more" updates qty
);

-- ============================================================
-- KHATA (credit)
-- ============================================================
CREATE TABLE khata_accounts (
  customer_id   TEXT PRIMARY KEY,
  customer_name TEXT NOT NULL,
  phone         TEXT,
  balance_paise INTEGER NOT NULL DEFAULT 0,   -- positive = customer owes shop
  created_at    TEXT NOT NULL
);

CREATE TABLE khata_ledger (
  entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id   TEXT NOT NULL REFERENCES khata_accounts(customer_id),
  delta_paise   INTEGER NOT NULL,             -- positive = charge, negative = payment received
  reason        TEXT NOT NULL,                -- 'CHARGE' | 'PAYMENT'
  ref_bill_id   TEXT REFERENCES bills(bill_id),
  balance_after_paise INTEGER NOT NULL,
  created_at    TEXT NOT NULL,
  idempotency_key TEXT UNIQUE
);

-- ============================================================
-- PREFERENCES — durable, outside conversation context (the "memory" requirement)
-- ============================================================
CREATE TABLE preferences (
  owner_id      TEXT NOT NULL,
  key           TEXT NOT NULL,                -- 'default_payment_mode' | 'default_atta_sku' | 'shop_name' | 'gstin' | ...
  value         TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (owner_id, key)
);

-- ============================================================
-- TELEGRAM IDEMPOTENCY GATE (transport-level dedupe, before any tool logic)
-- ============================================================
CREATE TABLE processed_updates (
  update_id     INTEGER PRIMARY KEY,
  processed_at  TEXT NOT NULL
);

-- ============================================================
-- CONVERSATION CONTEXT (for multi-turn continuity within a chat)
-- ============================================================
CREATE TABLE conversation_turns (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id       TEXT NOT NULL,
  role          TEXT NOT NULL,                -- 'user' | 'assistant' | 'tool_use' | 'tool_result'
  content       TEXT NOT NULL,                -- JSON-serialized message content
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_turns_chat ON conversation_turns(chat_id, id DESC);
```

**Why append-only ledgers for stock and khata instead of a mutable `quantity`/`balance` column:** every "hard part" the assignment names (oversell guard, idempotency, concurrency, auditability, guardrails against deletion) is a direct, almost mechanical consequence of this one modeling choice. A mutable column requires read-modify-write races to be handled by application-level locking; an append-only ledger with a DB-enforced `CHECK` on the running balance turns the invariant into something the database itself refuses to violate (see §4.4.3).

### 4.3 Tool Interfaces (Deep Modules)

Each tool is a *deep module*: a small, unambiguous interface hiding real logic (validation, transactions, rounding) behind it. The LLM only ever sees the interface below — never the SQL. These are the **skills** the assignment asks us to design.

#### Inventory & Catalog Tools

| Tool | Args (essential) | Returns | Enforces |
|---|---|---|---|
| `lookup_product(query)` | fuzzy name/SKU | `{matches: [{sku_id, name, price, stock, gst_rate}...]}` or `{error: "not_found"}` | Grounding — returns real price/GST/stock or "not found," never fabricated. Returns multiple candidates for ambiguous queries. |
| `list_low_stock()` | — | `{items: [{sku_id, name, qty, reorder_level}...]}` | Reorder-level query against `stock_current` view |
| `receive_stock(sku_id, qty, cost_price_paise?, mrp_paise?, idempotency_key)` | | `{new_balance, ledger_id}` | Writes `stock_ledger` (+), optionally updates catalog cost/MRP |
| `create_product(name, unit, is_loose, hsn_code, gst_rate_bps, cost_price_paise, mrp_paise, sell_price_paise, reorder_level_qty)` | | `{sku_id}` | New SKU onboarding; rejects duplicate names without confirmation |

#### Billing Tools

| Tool | Args (essential) | Returns | Enforces |
|---|---|---|---|
| `open_draft_bill(chat_id, customer_name?)` | | `{bill_id, status: "DRAFT"}` | Creates or returns existing open `DRAFT` bill for this chat |
| `update_draft_bill(bill_id, op, sku_id, qty)` | `op ∈ {ADD, SET_QTY, REMOVE}` | `{bill_id, lines: [...], subtotal, tax_preview}` | Mutates `bill_lines` only; **never touches `stock_ledger`** — this is what makes multi-turn edits free |
| `get_draft_bill_summary(bill_id)` | | `{lines, subtotal, cgst, sgst, total}` | Running subtotal/tax preview, computed live, not persisted |
| `finalize_bill(bill_id, payment_mode, payment_ref?, khata_customer_id?, idempotency_key)` | | `{bill_id, total, status: "FINALIZED"}` or `{error: "insufficient_stock", details}` | The single transaction boundary: oversell check → stock decrement → tax computation → status flip → (optional) khata charge, all atomic (§4.4) |
| `void_bill(bill_id, reason)` | | `{bill_id, status: "VOID"}` | Reversal only if already finalized in error; writes compensating `stock_ledger` entries, never deletes |

#### Khata Tools

| Tool | Args (essential) | Returns | Enforces |
|---|---|---|---|
| `get_khata_balance(customer_id_or_name)` | | `{customer_id, name, balance_paise}` | |
| `charge_khata(customer_id_or_name, amount_paise, ref_bill_id?, idempotency_key)` | | `{entry_id, new_balance}` | Creates account if genuinely new **only** with explicit `confirm_new=true` flag from the agent's dialogue turn |
| `settle_khata(customer_id_or_name, amount_paise, idempotency_key)` | | `{entry_id, new_balance}` | Rejects if `customer_id` doesn't exist — no phantom accounts on payment |
| `list_khata_customers()` | | `{customers: [{id, name, balance}...]}` | Lists all customers with outstanding balances |

#### Analytics & Document Generation Tools

| Tool | Args (essential) | Returns | Enforces |
|---|---|---|---|
| `get_daily_close(date)` | | `{revenue, cgst, sgst, mode_split, top_items}` | Aggregates `bills` where `status='FINALIZED'` |
| `generate_invoice_pdf(bill_id)` | | `{file_path}` | Reads finalized bill + shop preferences → renders GST-correct PDF → returns Telegram file handle |
| `generate_analysis_deck(date_range)` | | `{file_path}` | Reads aggregates → renders PPTX with real charts (matplotlib/plotly images embedded) |

#### Preference Tools

| Tool | Args (essential) | Returns | Enforces |
|---|---|---|---|
| `set_preference(key, value)` | | `{key, value, status: "saved"}` | Writes `preferences` table, keyed on `owner_id` (not `chat_id`) |
| `get_preference(key)` | | `{key, value}` or `{error: "not_set"}` | Reads from `preferences`, independent of chat |
| `get_all_preferences()` | | `{preferences: {key: value, ...}}` | Bulk load at turn start for system prompt injection |

**Total: ~18 tools.** This is the capability surface — the "skills" the brief asks us to design.

**Ambiguity handling is a non-tool path:** when `lookup_product` returns multiple plausible matches (or none), the tool returns that ambiguity explicitly as structured data (candidate list) rather than picking one — forcing the *model*, in its next text turn, to ask the owner. The disambiguation question is generated by Claude, not hardcoded copy.

### 4.4 Invariants & Constraints — enforced at the tool/DB layer, never in the prompt

> **Assignment §4:** "Business rules live in the skills/tools, not the prompt. Oversell guard, GST maths, idempotency and khata rules are enforced where the data changes — not hoped for in the system prompt."

#### 4.4.1 Oversell Guard

`finalize_bill` runs a single SQL transaction that, for every line:

1. Reads current stock via the latest `stock_ledger.balance_after` for that SKU with `SELECT ... ORDER BY ledger_id DESC LIMIT 1` inside the transaction.
2. Computes `new_balance = current - qty`.
3. Inserts the ledger row **only if** `new_balance >= 0`, guarded by a `CHECK (balance_after >= 0)` constraint on the table itself as the final backstop.
4. If any line fails the check, the **whole transaction rolls back** — a bill either decrements *all* its lines or *none*.

The LLM is told the failure as a structured error (`{error: "insufficient_stock", sku: "MAGGI_70G", available: 6, requested: 10}`) and must relay it in its own words; it cannot override or retry-with-force.

#### 4.4.2 GST Correctness

- Every product carries `gst_rate_bps` and `hsn_code` at the catalog level — the tax rate is a **fact looked up**, never computed or guessed by the model.
- Intra-state assumption (per brief §2) → CGST = SGST = `gst_rate_bps / 2`.
- Per-line tax computed on `unit_price_paise × qty` in integer paise, using round-half-up at the line level:

  ```
  taxable_amount = unit_price_paise × qty
  cgst = (taxable_amount × (gst_rate_bps / 2) + 5000) // 10000
  sgst = cgst   (symmetric split)
  line_total = taxable_amount + cgst + sgst
  ```

  The `+5000` before integer division by `10000` implements round-half-up at basis-point precision.

- Bill-level totals are the **sum of already-rounded line amounts**, not a re-rounding of the subtotal — this matches how real GST invoices reconcile line-by-line and avoids the classic "sum of parts ≠ rounded whole" bug.
- `generate_invoice_pdf` renders the exact stored per-line CGST/SGST columns — it never recomputes tax; it only formats what `finalize_bill` already committed. This guarantees the PDF can never disagree with the DB.

#### 4.4.3 Idempotency

Three independent layers, because Telegram redelivery and "double-tap finalize" are different failure modes:

1. **Transport layer:** `processed_updates(update_id)` — every incoming Telegram `update_id` is inserted before processing; a duplicate delivery hits a `UNIQUE` violation and is dropped with no side effects, before the LLM is even called.
2. **Tool layer:** every state-mutating tool (`receive_stock`, `finalize_bill`, `charge_khata`, `settle_khata`) requires an `idempotency_key` generated deterministically by the calling code (not the LLM) from `(chat_id, tool_name, draft_bill_id or logical_op, a monotonic turn counter)`. The key has a `UNIQUE` constraint on the relevant ledger table; a retried call with the same key is detected and returns the *original* result rather than re-executing.
3. **Business layer:** `finalize_bill` additionally checks `bills.status` — a bill already `FINALIZED` cannot be finalized again regardless of key, so even a key-generation bug fails closed, not open.

#### 4.4.4 Concurrency

- SQLite WAL mode: one writer at a time, but writers never block readers, and each tool call's DB work is wrapped in a single short transaction (`BEGIN IMMEDIATE ... COMMIT`), so two `finalize_bill` calls (or a `finalize_bill` racing a `receive_stock`) on overlapping SKUs serialize correctly rather than interleaving.
- Because stock is derived from the *ledger's last row*, not a cached column, "read current stock, then write" is done inside one transaction — there is no separate read step the LLM (or a second process) can race between.
- Draft bills are scoped to `chat_id`, so two owners' devices (or two concurrent conversations) building bills against the same SKU never contend on bill state — only on the shared stock ledger, which is protected as above.

#### 4.4.5 Guardrails (soft business rules, hard enforcement)

- **Sell-below-cost:** `finalize_bill` compares `unit_price_paise` (snapshotted at add-time) against the product's current `cost_price_paise`; if lower, it returns a structured `warning` requiring an explicit `confirm=true` re-call — the agent must surface this to the owner and get an explicit yes before retrying, it cannot silently proceed.
- **No deletion, ever:** there is no `DELETE` statement anywhere in tool code against `stock_ledger`, `khata_ledger`, or `bills`. Corrections are always new rows (`reason='CORRECTION'`/`'REVERSAL'`). This is enforced by code review discipline + (stretch) a DB trigger that rejects `DELETE` on these three tables outright.
- **Khata on nonexistent customer:** `settle_khata` requires an existing `khata_accounts` row; `charge_khata` on an unrecognized name returns "no such customer — create one?" rather than auto-creating, so the model must get explicit owner confirmation before onboarding a new khata customer.
- **No fabrication:** every tool that returns product info (`lookup_product`, `update_draft_bill`) fetches from the DB. There is no path by which the LLM can inject a product name, price, or GST rate that doesn't exist in `products`. The system prompt explicitly instructs: "never invent a product — use `lookup_product` first."

### 4.5 Memory (Cross-Session Preferences)

`preferences` is keyed on `owner_id`, **not** `chat_id` — a `/new` chat in Telegram resets the conversation's context window but the owner's Telegram user ID is stable, so `get_preference`/`set_preference` reads the same durable row regardless of which chat thread it's called from.

The system prompt, at the start of every turn (not just every chat), injects the current preference set (default payment mode, default SKUs for ambiguous names, shop name, GSTIN) as plain facts — so "remembering across chats" is really just "the tool result includes durable state on every single call," which requires zero conversational memory at all.

**Memory is demonstrated in the demo video** by: setting a preference → starting a `/new` chat (clearing conversation context) → issuing a request that depends on the preference → showing it's applied without being re-stated.

---

## 5. Testing & Verification Decisions

Testing targets the **tools**, not the LLM — the LLM's job (intent → tool call) is validated only at the end-to-end/demo level; correctness of business logic is validated with deterministic, LLM-free unit and integration tests.

### 5.1 Oversell / Concurrency Under Load

- **Unit test:** Seed stock at N units; issue two concurrent `finalize_bill` calls (via threads/async tasks) each requesting > N/2 units of the same SKU; assert exactly one succeeds, the other fails with "insufficient stock," and final ledger balance is `N - (winning qty)`, never negative.
- **Property-style test:** Fire 50 concurrent `finalize_bill` calls against a stock level of 10, all requesting 1 unit each; assert exactly 10 succeed and the `CHECK (balance_after >= 0)` constraint is never violated (test asserts no exception escapes as a DB corruption, only as a handled "insufficient stock" result).

### 5.2 GST Rounding Correctness

- **Table-driven test** with known "textbook" values:
  - ₹100 @ 5% → CGST ₹2.50, SGST ₹2.50 (250 paise each)
  - ₹99.99 @ 18% → verify per-line CGST+SGST = expected within ±1 paise
  - ₹33.33 @ 12% at qty=3 — the classic sum-of-thirds rounding trap
  - Multi-item bill mixing 0%, 5%, 12%, 18% slabs
- Assert: per-line CGST+SGST = expected within ±1 paise, and bill total = exact sum of stored (already-rounded) lines — never a fresh re-round of the subtotal.
- **Cross-check** against a hand-computed spreadsheet for one full multi-item, multi-slab bill to catch any slab-boundary bug.

### 5.3 Idempotency

- **Unit test:** Call `finalize_bill` twice with the identical `idempotency_key`; assert stock is decremented exactly once and both calls return the same `bill_id`/total.
- **Simulate Telegram redelivery:** Feed the same raw webhook payload (same `update_id`) through the handler twice; assert only one `processed_updates` row and one downstream tool execution.

### 5.4 Khata Invariants

- `settle_khata` against a non-existent `customer_id` → assert rejection, no row created.
- `charge_khata` + `settle_khata` sequence → assert `balance_after_paise` in the ledger matches `khata_accounts.balance_paise` exactly at every step (no drift between the cached balance and the ledger it's derived from).
- `settle_khata` for more than the outstanding balance → assert rejection (can't overpay into negative balance).

### 5.5 End-to-End Acceptance Flow (Mirrors the Demo Video Requirement)

Run against the live bot, scripted as a fixed Telegram message sequence, asserting on bot replies + DB state after each step. This sequence exactly matches the **4–5 min recording** specified in the brief §6:

1. **Receive stock** — "50 packets of Maggi came in, cost ₹12, MRP ₹14" → assert ledger +50.
2. **Multi-item draft bill with an ambiguous item** → assert clarifying question fires → resolve it → `update_draft_bill` edit ("drop the butter, make it 6 Maggi") → verify draft reflects edits → `finalize_bill`.
3. **Oversell attempt** on a known-low-stock SKU → assert refusal, no ledger write.
4. **Khata cycle:** charge → balance query → partial payment → balance query again, arithmetic verified.
5. **Generate PDF invoice** on the finalized bill → assert PDF exists, opens, and its printed totals match the DB row (script re-extracts text from PDF and diffs against DB).
6. **Generate analysis deck** → assert PPTX has the expected number of chart-bearing slides.
7. **Set preference** ("default_payment_mode", "UPI") → start `/new` chat (fresh context, same `owner_id`) → issue a bill with no explicit payment mode mentioned → assert it defaults to UPI without being told again.

---

## 6. Deliverables & Execution Plan

### 6.1 Deliverables Checklist (from brief §6)

| # | Deliverable | Status |
|---|---|---|
| 1 | Live, continuously-running Telegram bot (handle in README, kept running during review) | [ ] |
| 2 | Built on a modern agent harness — Anthropic Messages API tool-use loop (justified in §4.1) | [ ] |
| 3 | Full skill/tool surface (§4.3) — the capability surface designed to run the store | [ ] |
| 4 | PDF invoices — GST-correct, generated by a tool (ReportLab), not a screenshot | [ ] |
| 5 | PPTX analysis deck — real charts (matplotlib/plotly), generated by a tool (python-pptx) | [ ] |
| 6 | README (~1 page): harness rationale, control-loop description, skill/tool design, hard-parts solutions | [ ] |
| 7 | 4–5 min recording covering the exact sequence in §5.5 | [ ] |
| 8 | Private GitHub repo, collaborators invited: **Aswath363**, **akshaiP**, **ashwanthnebula** | [ ] |
| 9 | Clean commit history showing incremental progression (not a single squash) | [ ] |

### 6.2 Three-Day Vertical-Slice Plan

#### Day 0 (Pre-work, ~2 hours) — Project Skeleton & Environment

- Initialize the GitHub repo with `.gitignore`, `requirements.txt`, `README.md` stub.
- Set up the project structure:
  ```
  supermarket-ops-agent/
  ├── src/
  │   ├── db/          # schema, migrations, seed data
  │   ├── tools/       # one module per tool group (inventory, billing, khata, analytics, prefs)
  │   ├── agent/       # Anthropic API loop, system prompt, tool registry
  │   ├── telegram/    # webhook handler, idempotency gate
  │   └── documents/   # PDF and PPTX generators
  ├── tests/           # unit + integration tests
  ├── seed_data.py     # catalog seeding script
  ├── main.py          # entry point
  └── README.md
  ```
- Create the Telegram bot via @BotFather, store token in `.env`.
- Set up the Anthropic API key in `.env`.
- First commit: skeleton with config.

#### Day 1 — Schema, Invariants, and Core Tool Logic (no LLM, no Telegram)

- Stand up SQLite schema exactly as §4.2; seed catalog with all named real SKUs + GST slabs + HSN codes from the seed table.
- Implement every tool in §4.3 as plain Python functions with the transaction/invariant logic of §4.4, fully unit-testable via direct calls (no agent involved).
- Write and pass all tests in §5.1–5.4 against this tool layer.
- **Exit criterion:** I can oversell-guard, finalize, void, and khata-cycle entirely from a test script with zero LLM calls.

#### Day 2 — Agent Loop, Telegram Integration, and Document Generation

- Wrap tools as Anthropic tool schemas; write the system prompt describing domain concepts (khata, GST slabs, loose vs packaged) without ever describing *syntax*.
- Implement the Telegram webhook handler with the idempotency gate (§4.4.3 layer 1) in front of the agent loop.
- Implement `generate_invoice_pdf` (ReportLab) — clean GST invoice with shop name, GSTIN, HSN codes, per-line CGST/SGST, rounded totals.
- Implement `generate_analysis_deck` (python-pptx + matplotlib for chart images embedded as slide images) — revenue trend, top items, stock health, GST collected.
- Manually run through the core intents from §3 conversationally against the real bot to shake out prompt/tool-schema issues.

#### Day 3 — Edge Cases, Deployment, and Recording

- Harden ambiguity handling (multi-candidate `lookup_product` responses), sell-below-cost confirmation flow, khata-on-nonexistent-customer refusal.
- Run the full §5.5 acceptance script against the deployed bot.
- Deploy to **Railway** (or Render) as a small always-on webhook service; confirm it survives a restart with state intact (SQLite DB persists on volume).
- Write the README per the brief's spec (~1 page: harness rationale, control loop, tool design, hard parts).
- Record the 4–5 min demo per the exact sequence in §5.5.
- Push final commit history, invite collaborators (**Aswath363**, **akshaiP**, **ashwanthnebula**).

### 6.3 Technology Stack Summary

| Component | Technology | Why |
|---|---|---|
| Language | Python 3.12+ | Fastest for prototyping, best library support for all components |
| Agent / LLM | Anthropic Messages API (Claude 3.5 Sonnet) | Native tool-use, cost-effective, fast enough for conversational latency |
| Database | SQLite (WAL mode) | Zero-ops, sufficient for single-process bot, easy to persist on Railway volume |
| Telegram | python-telegram-bot (webhook mode) | Mature, async-capable, handles transport concerns |
| PDF Generation | ReportLab | Industry standard for programmatic PDF in Python |
| PPTX Generation | python-pptx + matplotlib | python-pptx for slides, matplotlib for chart images |
| Deployment | Railway (always-on, persistent volume) | Simple, free tier available, persistent disk for SQLite |
| Fuzzy Search | `difflib.SequenceMatcher` or `thefuzz` | Lightweight product name matching without a full search engine |

---

## 7. Out of Scope

To protect the 3-day timeline, the following are explicitly **not** built:

- **No web/admin dashboard of any kind** — Telegram is the only interface, per the brief.
- **No real payment gateway integration** — Cash/UPI/Card are recorded as metadata only (mode + free-text reference), never processed.
- **No multi-tenant / multi-shop support** — single owner_id, single shop, hardcoded shop identity in preferences.
- **No multi-language support (Hindi/Tamil)** — English only (listed as stretch in the brief, not core).
- **No voice-note ordering** — stretch goal, deferred entirely.
- **No barcode/photo recognition** — stretch goal, deferred entirely.
- **No scheduled auto-sent decks** — stretch goal; decks are on-demand only.
- **No khata payment reminders** — stretch goal, deferred.
- **No branded/templated invoice PDFs** — stretch goal; clean functional PDF is sufficient.
- **No reorder suggestions from sales velocity** — stretch goal, deferred.
- **No expiry/batch/FEFO tracking** — out of scope for the core kirana model as specified.
- **No Postgres migration** — SQLite/WAL is sufficient for a single-process bot; swapping later is a config change, not an architecture change, since all access already goes through the tool layer's transaction boundaries.
- **No custom auth/session system** beyond Telegram's own user identity — `owner_id` = Telegram user ID, no separate login.
- **No horizontal scaling / multi-instance deployment** — a single always-on process is sufficient for the review window and avoids distributed-idempotency complexity that the single-writer SQLite model doesn't need.

---

## 8. Stretch Goals (If Time Permits)

In priority order, if Day 3 finishes early:

1. **Branded invoice PDFs** — shop logo, better typography, professional layout.
2. **Reorder suggestions** — "Based on your sales velocity, you'll run out of Maggi in 3 days."
3. **Khata payment reminders** — "Ramesh has owed ₹500 for 7 days, want to send a reminder?"
4. **Multi-language** — Hindi support in the system prompt.

---

*End of PRD v2.0.*
