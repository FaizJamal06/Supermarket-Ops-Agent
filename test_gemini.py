import os
from dotenv import load_dotenv
from google import genai

def test_ping():
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found in .env")
        return

    print("Connecting to Gemini...")
    client = genai.Client(api_key=api_key)
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Say 'Hello from Gemini!' and nothing else."
        )
        print("Success! Response from model:", response.text)
    except Exception as e:
        print("API Error:", str(e))

if __name__ == "__main__":
    test_ping()
