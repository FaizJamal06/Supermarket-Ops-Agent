"""
Groq Tool Loop — PRD §4.1 (The "Thin Harness").

Orchestrates the Groq API (Llama 3.3 70B), system instructions, tool execution loop,
and conversation history (with truncation).
"""

import os
import json
import time
from groq import Groq
from src.agent.tools import GROQ_TOOLS, execute_tool
from src.db.context import load_recent_turns, save_turn, clear_history
from src.tools.preferences import get_all_preferences


def _build_system_prompt(owner_id: str, chat_id: str) -> str:
    """
    Build the dynamic system prompt including the owner's durable preferences (PRD §4.5).
    """
    prefs = get_all_preferences(owner_id)["preferences"]
    prefs_xml = "<preferences>\n"
    for k, v in prefs.items():
        prefs_xml += f"  <{k}>{v}</{k}>\n"
    prefs_xml += "</preferences>"

    return f"""You are the Supermarket Ops Agent for a busy Indian kirana store.
You interact with the shop owner via Telegram to manage billing, inventory, and khata (credit).
You have access to a suite of tools to perform these actions.

CRITICAL RULES:
1. NEVER assume a SKU ID. Always use lookup_product to find the exact SKU ID by name before adding to a bill or receiving stock.
2. Money is in paise. Always convert user-facing Rs values to paise (Rs * 100) before calling tools.
3. You must use the idempotency_key parameter on any mutating tools. Generate a unique key based on the user's request context (e.g. "bill_add_maggi_turn3").
4. If a user asks to clear chat or start a new day, you can acknowledge it, but note that the /new command handles clearing context automatically.
5. If you get a 'below_cost' warning when finalizing a bill, you MUST ask the user for confirmation before proceeding with confirm_below_cost=True.
6. If a customer is not found when charging khata, ask the user if they want to create a new account, then use confirm_new=True.
7. If lookup_product returns multiple matches (ambiguous matches), immediately STOP calling tools and ask the user to clarify which specific item they meant.
8. When the user provides a clarification or specifies an item name/variant (e.g., 'Sugar loose' or 'Aashirvaad 5kg'), immediately call the appropriate tool (lookup_product then update_draft_bill) to add/update the item. Do NOT ask for re-confirmation if the user has already confirmed their choice.
9. You are a backend orchestrator. NEVER ask the user for a chat_id, bill_id, or database key. Implicitly use the injected chat_id for all billing and memory tool calls.
10. UNIVERSAL CONTEXT RESOLUTION: When a user selects or clarifies an item from a list of options you previously presented, NEVER trigger the lookup_product tool again. Extract the corresponding SKU_ID directly from your own prior message in the conversation history and immediately trigger the billing tool (e.g. update_draft_bill). This eliminates redundant lookups and prevents ambiguity loops.
11. If the user mentions an item that was already listed in the recent options or mentions an SKU, directly add or update the draft bill with that SKU. Do not call lookup_product again if the product was already identified earlier in the conversation.
12. If you call a document generation tool (like generate_invoice_pdf or generate_analysis_deck) and it returns "PDF generated successfully and uploaded" (or PPTX), STOP. Do not trigger any more tools and do not ask the user for further instructions or repeat the Bill ID. Just inform the user the document was sent.
13. If the user asks for a document of their recent bill, call the document generation tools WITHOUT passing a bill_id. The backend will automatically resolve the latest transaction.

System Info: The current user's chat_id is {chat_id}
Owner ID: {owner_id}

OWNER PREFERENCES (Memory):
These are the durable preferences set by the owner. Respect them in your actions.
{prefs_xml}
"""

def process_message(owner_id: str, chat_id: str, text: str) -> tuple[str, list[str]]:
    """
    Process a user message through the Groq tool loop.
    Returns the final text response and a list of generated file paths.
    """
    if text.strip().lower() == "/new":
        clear_history(chat_id)
        return "Started a new conversation. Chat history cleared (business data like stock and khata is untouched).", []

    generated_files = []
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    # 1. Build messages in OpenAI-compatible format
    system_prompt = _build_system_prompt(owner_id, chat_id)
    messages = [{"role": "system", "content": system_prompt}]

    # Load context from DB (last N turns)
    db_messages = load_recent_turns(chat_id)
    for msg in db_messages:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Append new user message
    messages.append({"role": "user", "content": text})
    save_turn(chat_id, "user", text)

    # 2. Enter tool loop
    max_iterations = 12
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # Call Groq
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=messages,
                tools=GROQ_TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=4096,
            )
        except Exception as e:
            error_msg = f"Sorry, I encountered an API error: {str(e)}"
            save_turn(chat_id, "assistant", error_msg)
            return error_msg, generated_files

        # 3. Process the response
        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            # Append the assistant's message (with tool_calls) to history as a strict dictionary
            assistant_msg = {
                "role": "assistant",
                "content": response_message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in tool_calls
                ]
            }
            messages.append(assistant_msg)

            # Execute ALL tool calls in batch, then append results in one go
            for tc in tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)

                print(f"  [Tool Use] {fn_name}({fn_args})")

                result = execute_tool(fn_name, fn_args)
                print(f"  [Tool Result] {result}")

                # Collect any generated files
                if isinstance(result, dict) and "file_path" in result:
                    generated_files.append(result["file_path"])

                # Append tool result with the matching tool_call_id
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": json.dumps(result),
                })

        else:
            # No tool calls — final text response
            final_text = response_message.content
            if final_text:
                save_turn(chat_id, "assistant", final_text)
            return (final_text or "I processed your request but have nothing to add."), generated_files

    # If we hit max_iterations, break the spiral
    fallback_msg = "I'm having trouble completing this request right now (too many steps). Please try breaking it down."
    save_turn(chat_id, "assistant", fallback_msg)
    return fallback_msg, generated_files
