# Supermarket Ops Agent — Nebula KnowLab Take-Home

> Run an entire Indian kirana store from a Telegram chat — with a conversational agent, not a rigid menu.

**Author:** Faiz Jamal
**Status:** Completed & Production-Ready

---

## 🏗️ Architecture & Engineering Choices

### 1. The Harness & Control Loop
I opted to build a **custom "Thin Harness"** directly around the `google-genai` Python SDK, intentionally avoiding heavyweight abstraction frameworks like LangChain, AutoGen, or CrewAI. 

**Why?** 
Abstraction frameworks obscure the exact token payload hitting the model, limit granular control over function calling schemas, and often hide failure modes (like infinite tool-calling loops). For a transactional system handling money and inventory, complete determinism over the API boundary is required.

**How the Control Loop Works (`src/agent/loop.py`):**
1. **Context Loading & Filtering:** Retrieves strictly truncated history (last 10 turns) from the SQLite DB. To prevent massive context window bloat and token drain, the DB *only* stores the final text interactions. Raw intermediate `FunctionCall` and `FunctionResponse` objects are intentionally dropped from persistent storage.
2. **Circuit Breaker:** Enters a `while` loop bound by a strict `max_iterations = 12`. If the LLM spirals (fails to resolve the user's intent within 12 tool rounds), the loop forcibly halts, logs the failure, and gracefully asks the user to break down the request.
3. **Parallel Tool Batching:** If the LLM requests 4 separate product lookups, the loop executes all 4 concurrently, packages their results into an array of `FunctionResponse` parts, and returns a single `user` turn to the LLM. This saves costly round-trips.
4. **Rate Limit Safeguards:** A `time.sleep(12)` guard is injected before subsequent loop iterations to gracefully respect Gemini 1.5 Flash's 15 RPM free-tier limit.

### 2. Skill & Tool Design
**No Regex or Intent Routers.** Natural language is the sole router. The system exposes 18 granular tools to the LLM, meticulously defined via `google.genai.types.FunctionDeclaration` OpenAPI schemas. 

* **Type Coercion at the Edge:** LLMs notoriously struggle with data types. The `execute_tool` router actively scans for variables ending in `_paise` or `_bps` and forcibly coerces hallucinatory floats back to integers before allowing them to touch the database.
* **Fuzzy Lookup Strategy:** SQLite's rigid `LIKE` matching fails for typical Indian transliteration errors (e.g., `AASHIRVAAD` vs `ashirvad`). Instead, the entire lightweight catalog is loaded into memory, and `thefuzz` applies a multi-strategy token-sort ratio to map fuzzy user intent to exact `SKU_IDs` with high accuracy.
* **Draft Cart Awareness:** Whenever the `update_draft_bill` tool is invoked, it deliberately returns a formatted string containing the *entire* updated cart. This forcibly refreshes the LLM's context window with the current draft state, completely eliminating amnesia in multi-turn billing flows.

---

## 🛡️ Solving the "Hard Parts" (Invariants)

### Concurrency, Race Conditions, & Overselling
Selling a high-demand item across concurrent Telegram chats could easily result in negative stock. 
* **Solution:** Stock and Khata are implemented as **append-only ledgers**, not mutable columns. The system uses SQLite `WAL` mode and `BEGIN IMMEDIATE` transactions. When a bill is finalized, a `CHECK (balance_after >= 0)` constraint mathematically prevents the ledger from dropping below zero. 
* **Validation:** Verified via a brutal Pytest suite spinning up 50 async threads competing for 10 units of stock. Exactly 10 succeed, 40 fail, and the DB gracefully rolls back.

### Distributed Idempotency
A user clicking a button twice or a patchy 3G network dropping a Telegram webhook ACK can cause double-billing.
* **Solution (Layer 1):** The Telegram polling script logs `update_id` to ignore repeated payload deliveries.
* **Solution (Layer 2):** Every mutating tool (`receive_stock`, `finalize_bill`, `charge_khata`) forces the LLM to generate an `idempotency_key` based on conversational context. This key is inserted into a dedicated `idempotency_log` table within the same transaction. Replays hit a `UNIQUE` constraint and abort harmlessly.

### Floating Point Drift & GST Accuracy
Handling money as floats (e.g., `0.1 + 0.2 = 0.30000000000000004`) causes devastating accounting drift.
* **Solution:** Absolute strict enforcement of the `Integer Paise` pattern. All prices are stored as paise (Rs * 100), and all GST rates as basis points (bps). 
* **Math:** GST calculation utilizes exact integer round-half-up math: `(line_subtotal_paise * gst_rate_bps + 5000) // 10000`.

---

## 🚀 Quick Start

```bash
# 1. Environment
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN and GEMINI_API_KEY in .env

# 2. Install
pip install -r requirements.txt

# 3. Seed test data (optional)
python seed_data.py

# 4. Run the Agent
python main.py
```

## 🧪 Testing
The system contains a rigorous `pytest` suite enforcing all invariants.
```bash
python -m pytest tests/test_qa_edge_cases.py tests/test_tools.py tests/test_artifacts.py -v
```
