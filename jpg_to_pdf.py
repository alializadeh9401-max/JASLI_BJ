from PIL import Image
from pathlib import Path

input_path = input("Enter the full path to your JPG file: ").strip().strip('"')

try:
    input_file = Path(input_path)

    print(f"Looking for: {input_file}")

    if not input_file.exists():
        print("ERROR: File does not exist.")
    elif input_file.suffix.lower() not in [".jpg", ".jpeg"]:
        print("ERROR: File is not a JPG/JPEG.")
    else:
        output_file = input_file.with_suffix(".pdf")
        print(f"Will save PDF as: {output_file}")

        image = Image.open(input_file)
        if image.mode != "RGB":
            image = image.convert("RGB")

        image.save(output_file, "PDF")
        print("SUCCESS: PDF created.")

except Exception as e:
    print(f"ERROR: {e}")

input("Press Enter to exit...")