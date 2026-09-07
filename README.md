# KiranaOS Supermarket Ops Agent

> A Senior AI Engineering approach to running an entire Indian Kirana store natively from Telegram—bypassing rigid GUI menus in favor of a robust, stateful conversational agent.

**Author:** Faiz Jamal  
**Status:** Completed & Production-Ready  

---

## 🏗️ Architecture & Engineering Blueprint

### 1. The "Thin Harness" & Control Loop
I completely bypassed heavyweight abstraction frameworks (like LangChain, AutoGen, or CrewAI) and built a **custom "Thin Harness"** directly around the **Groq API (Llama 3.3 70B)**. 

**Why?**
Abstraction frameworks obscure the token payload hitting the model, limit granular control over function calling schemas, and mask failure modes (like infinite tool-calling ghost loops). For a mission-critical transactional system handling money, inventory, and dynamic file generation, absolute determinism over the API boundary is a non-negotiable requirement.

**How the Control Loop Works (`src/agent/loop.py`):**
1. **Zero-Cost Inference Pipeline:** Leverages Groq's insanely fast Llama 3 70B for near-instant latency and free-tier operational costs.
2. **Context Memory & Pruning:** Retrieves strictly truncated history from SQLite. To prevent massive context window bloat, the database *only* stores essential text interactions. Raw intermediate `FunctionCall` objects are intentionally pruned from persistent storage.
3. **Universal Context Resolution:** The system prompt dynamically manages state. If the LLM identifies a user selecting an option or SKU from prior context, it enforces a strict rule to bypass redundant lookups and jump directly to the billing tools—preventing API rate limit exhaustion and loop spirals.
4. **Tool Batching & Concurrency:** Multi-tool requests are batched. The loop handles all parallel execution rounds seamlessly and parses the array of `FunctionResponse` objects back to the LLM to save costly round-trips.

### 2. Intelligent Tooling & Document Generation
**No Regex or Intent Routers.** Natural language is the sole router. The system exposes an array of granular backend tools defined via OpenAPI schemas.

* **Dynamic Tokenized SQL Search:** Removed brittle `thefuzz` implementations. Replaced with an advanced explicit SQL tokenization engine (`LOWER(name) LIKE ? OR LOWER(sku_id) LIKE ?`) that guarantees deterministic matching. "Aashirvaad 5kg" dynamically maps to "Aashirvaad Atta 5kg" with zero hardcoded edge cases.
* **Auto-Resolving Artifact Generators:** Includes robust python modules (`reportlab` and `python-pptx`) to instantly generate Indian GST-standard PDF Invoices and PPTX Daily Close Analysis decks. 
  * The backend auto-resolves missing parameters (e.g. automatically pulling the latest `bill_id` for a specific `chat_id`).
  * Explicit termination rules prevent "ghost loops" once the Telegram bot dispatches the physical document to the chat.

### Proof of Deliverables
The generative logic is robust and completely functional. Here are outputs freshly triggered directly from our backend local testing:
- **PDF Invoice (`INV_c3ac0e4d.pdf`)**: Accurately generated with KiranaOS branding, calculated CGST/SGST taxes, and exact line-item rendering for A4 formats.
- **PPTX Analysis (`Daily_Close_2026-09-08.pptx`)**: Generates automated management decks showing Revenue Summaries, Payment Mode Splits, and Top Selling Items.

---

## 🛡️ Solving the "Hard Parts" (Backend Invariants)

### Concurrency, Race Conditions, & Overselling
Selling a high-demand item across concurrent Telegram chats could easily result in negative stock. 
* **Solution:** Stock and Khata are implemented as **append-only ledgers**, not mutable columns. The system uses SQLite `WAL` mode and `BEGIN IMMEDIATE` transactions. When a bill is finalized, a strict database constraint prevents the ledger from dropping below zero, guaranteeing mathematically sound rollbacks.

### Distributed Idempotency
A patch 3G network dropping a Telegram webhook ACK can cause the bot to double-charge a user.
* **Solution:** Every mutating tool (`receive_stock`, `finalize_bill`, `charge_khata`) forces the LLM to generate an `idempotency_key` based on the conversational context. This key is inserted into a dedicated table within the same transaction. Replays hit a `UNIQUE` constraint and abort harmlessly.

### Floating Point Drift & GST Accuracy
Handling money as floats causes devastating accounting drift.
* **Solution:** Absolute strict enforcement of the `Integer Paise` pattern. All prices are stored as paise (Rs * 100), and all GST rates as basis points (bps). GST calculation utilizes exact integer round-half-up math: `(line_subtotal_paise * gst_rate_bps + 5000) // 10000`.

---

## 🚀 Deployment Guide (How to host this)

This system is completely production-ready and can be deployed for free or pennies a month to show off.

### 1. Local Quick Start
```bash
# 1. Environment Configuration
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN and GROQ_API_KEY in .env

# 2. Install Dependencies
pip install -r requirements.txt

# 3. Seed Database (Optional)
python seed_data.py

# 4. Spin up the Agent
python main.py
```

### 2. Deploying to the Cloud (Render / Railway / VPS)
> **Note:** The original deployment on Railway has been disabled as the trial period has expired. You will need to host this yourself using the instructions below.

To deploy this so others can use it 24/7 without your laptop open:

1. **Push to GitHub**: Commit this codebase to a private or public GitHub repository.
2. **Choose a PaaS Platform**: Platforms like **Railway.app** or **Render.com** are perfect for this.
3. **Connect Repository**: Link your GitHub repo to the platform.
4. **Environment Variables**: In your platform's dashboard, inject your `.env` variables:
   - `TELEGRAM_BOT_TOKEN`
   - `GROQ_API_KEY`
5. **Start Command**: Set the start/run command to `python main.py`. 
6. **Persistent Storage (Important)**: Because we are using SQLite, make sure to attach a **Persistent Volume** to your service and set `DATABASE_PATH=/your-persistent-volume-path/store.db`. If you do not do this, your database will reset every time the server restarts!

### 3. Telegram Setup
1. Message **@BotFather** on Telegram to create a new bot and obtain your Token.
2. Start your bot and send a message. The agent will respond instantly.

---
*Built to scale, designed to impress.*
