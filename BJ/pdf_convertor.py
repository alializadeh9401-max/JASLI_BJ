from PIL import Image
import sys
from pathlib import Path


def jpg_to_pdf(input_path: str, output_path: str | None = None) -> None:
    input_file = Path(input_path)

    if not input_file.exists():
        raise FileNotFoundError(f"File not found: {input_file}")

    if input_file.suffix.lower() not in [".jpg", ".jpeg"]:
        raise ValueError("Input file must be a .jpg or .jpeg image")

    if output_path is None:
        output_file = input_file.with_suffix(".pdf")
    else:
        output_file = Path(output_path)

    image = Image.open(input_file)

    if image.mode != "RGB":
        image = image.convert("RGB")

    image.save(output_file, "PDF", resolution=100.0)
    print(f"PDF created: {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python jpg_to_pdf.py input.jpg [output.pdf]")
        sys.exit(1)

    input_jpg = r"C:\Users\alial\OneDrive\Documents\Important\Ali passport 2.jpg"
    output_pdf = r"C:\Users\alial\OneDrive\Documents\Important\Ali passport 2.pdf"

    jpg_to_pdf(input_jpg, output_pdf)
    