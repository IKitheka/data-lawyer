from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

input_file = "README.md"
output_file = "README.pdf"

c = canvas.Canvas(output_file, pagesize=letter)
width, height = letter

y = height - 40
line_height = 14

with open(input_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if y < 40:
            c.showPage()
            y = height - 40
        c.drawString(40, y, line[:120])
        y -= line_height

c.save()
print("✅ PDF generated:", output_file)
