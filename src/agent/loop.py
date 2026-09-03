"""
Google Gemini Tool Loop — PRD §4.1 (The "Thin Harness").

Orchestrates the Gemini API, system instructions, tool execution loop,
and conversation history (with truncation).
"""

import os
import json
import time
from google import genai
from google.genai import types
from src.agent.tools import GEMINI_TOOLS, execute_tool
from src.db.context import load_recent_turns, save_turn, clear_history
from src.tools.preferences import get_all_preferences


def _build_system_prompt(owner_id: str) -> str:
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

OWNER PREFERENCES (Memory):
These are the durable preferences set by the owner. Respect them in your actions.
{prefs_xml}
"""

def process_message(owner_id: str, chat_id: str, text: str) -> str:
    """
    Process a user message through the Gemini tool loop.
    Returns the final text response to send back to Telegram.
    """
    if text.strip().lower() == "/new":
        clear_history(chat_id)
        return "Started a new conversation. Chat history cleared (business data like stock and khata is untouched)."

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    # 1. Load context and map to Gemini format
    db_messages = load_recent_turns(chat_id)
    contents = []
    for msg in db_messages:
        role = "model" if msg["role"] == "assistant" else "user"
        # We only save text to DB, so content is always string
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))

    # 2. Append new user message
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
    save_turn(chat_id, "user", text)

    # 3. Enter tool loop
    system_prompt = _build_system_prompt(owner_id)
    
    max_iterations = 12
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Call Gemini
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=GEMINI_TOOLS,
                    temperature=0.2,
                )
            )
        except Exception as e:
            error_msg = f"Sorry, I encountered an API error: {str(e)}"
            save_turn(chat_id, "assistant", error_msg)
            return error_msg

        # Process the response
        if response.function_calls:
            # Model wants to call tools
            # Append the model's tool calls to history
            contents.append(response.candidates[0].content)
            
            tool_responses = []
            for fc in response.function_calls:
                print(f"  [Tool Use] {fc.name}({fc.args})")
                
                # Execute tool
                result = execute_tool(fc.name, fc.args)
                print(f"  [Tool Result] {result}")
                
                # Append result as FunctionResponse
                tool_responses.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response=result
                    )
                )
            
            # Send the tool results back as the user role
            contents.append(types.Content(role="user", parts=tool_responses))
            
            # Sleep to prevent hitting 5 RPM rate limit on Gemini 2.5 Flash Free Tier
            print("  [Rate Limit Guard] Sleeping for 12 seconds before next round-trip...")
            time.sleep(12)
            
        else:
            # No tool calls, final text is ready
            final_text = response.text
            if final_text:
                save_turn(chat_id, "assistant", final_text)
            return final_text

    # If we hit max_iterations, break the spiral
    fallback_msg = "I'm having trouble completing this request right now (too many steps). Please try breaking it down."
    save_turn(chat_id, "assistant", fallback_msg)
    return fallback_msg


