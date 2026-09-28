import os
from dotenv import load_dotenv
import glob
import base64
import pandas as pd
from PIL import Image
from openai import OpenAI
import argparse



def encode_image_to_base64(image_path):
    """Encodes the image to base64 for API transmission."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
def process(file_path):
    global client
    filename = os.path.basename(file_path)
    #print(f"Processing: {filename}...")
    
    try:
        base64_image = encode_image_to_base64(file_path)
        
        # Strict prompt to force the model to ignore color and return a clean tag
        prompt = (
            "Identify the underlying core symbol or object shape in this icon (e.g., 'home', 'settings', 'user', 'arrow_left'). "
            "Completely IGNORE the color of the icon. "
            "Respond with ONLY the name of the symbol in 1-2 words. Do not write full sentences, do not mention colors."
        )
        
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=10,
            temperature=0.0 # Lowest temperature for consistent categorical naming
        )
        
        # Clean up the output string to create a valid folder/category name
        icon_type = response.choices[0].message.content.strip().lower().replace(" ", "_").replace(".", "")
        #print(f"-> Detected Type: {icon_type}")
        
        return {"uuid": filename.replace(".png", ""), "iconname": icon_type}
        
    except Exception as e:
        raise
        print(f"❌ Error processing {filename}: {e}")
        return {"uuid": filename.replace(".png", ""), "iconname": "error"}
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a file.")
    parser.add_argument("--file", "-f", required=True, help="Path to the input file")
    args = parser.parse_args()

    # 1. API Configuration
    load_dotenv()
    API_KEY = os.environ.get("OPEN_API_KEY", None)
    BASE_URL = os.environ.get("OPEN_API_BASE", None)
    MODEL_NAME = os.environ.get("MODEL_NAME", None)
    if  not(API_KEY or BASE_URL or MODEL_NAME):
        print("Need openai api variables in .env: API_KEY  BASE_URL  MODEL_NAME")
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    result = process(args.file)
    print(result)
