import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
import hashlib
import piexif
from datetime import datetime
import sqlite3
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import mm
import io
import urllib.request
from PIL import Image, ExifTags
import re

app = Flask(__name__)
app.secret_key = "secret-key"

app.config['UPLOAD_FOLDER'] = '/tmp/uploads'
os.makedirs('/tmp/uploads', exist_ok=True)

PASSWORD = "forensic26"

# ---------------------------
# Database Setup
# ---------------------------
def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    filename TEXT,
                    filepath TEXT,
                    upload_datetime TEXT,
                    taken_datetime TEXT,
                    make TEXT,
                    model TEXT,
                    gps_lat REAL,
                    gps_lon REAL,
                    hash TEXT
                )''')
    conn.commit()
    conn.close()

init_db()



# ---------------------------
# Helper Functions
# ---------------------------
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def compute_hash(filepath):
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        sha.update(f.read())
    return sha.hexdigest()

def extract_exif(filepath):
    try:
        exif_dict = piexif.load(filepath)
        exif = exif_dict["Exif"]
        gps = exif_dict["GPS"]

        # Date Taken
        taken = exif.get(piexif.ExifIFD.DateTimeOriginal, b"").decode("utf-8") if exif.get(piexif.ExifIFD.DateTimeOriginal) else None

        # Make & Model
        make = exif_dict["0th"].get(piexif.ImageIFD.Make, b"").decode("utf-8")
        model = exif_dict["0th"].get(piexif.ImageIFD.Model, b"").decode("utf-8")

        # GPS
        def convert_gps(value):
            d, m, s = value
            return d[0]/d[1] + m[0]/m[1]/60 + s[0]/s[1]/3600

        lat = lon = None

        if gps:
            # Latitude
            if piexif.GPSIFD.GPSLatitude in gps:
                lat = convert_gps(gps[piexif.GPSIFD.GPSLatitude])
                lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef, b'N').decode()
                if lat_ref == "S":
                    lat = -lat

            # Longitude
            if piexif.GPSIFD.GPSLongitude in gps:
                lon = convert_gps(gps[piexif.GPSIFD.GPSLongitude])
                lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef, b'E').decode()
                if lon_ref == "W":
                    lon = -lon



        return taken, make, model, lat, lon

    except Exception:
        return None, None, None, None, None

from datetime import datetime

def format_exif_datetime(exif_str):
    if not exif_str:
        return None

    try:
        # EXIF format: YYYY:MM:DD HH:MM:SS
        dt = datetime.strptime(exif_str, "%Y:%m:%d %H:%M:%S")
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except:
        return exif_str  # fallback if EXIF is weird

@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")

        if password == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))

        flash("Incorrect password.")
        return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM photos")
    photos = c.fetchall()
    conn.close()
    return render_template('dashboard.html', photos=photos)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route('/upload', methods=['GET'])
def upload_page():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload():

    photo_name = request.form['photo_name']
    file = request.files['file']
    # Prevent duplicate photo names in DB
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT id FROM photos WHERE name = ?", (photo_name,))
    existing = c.fetchone()

    if existing:
        conn.close()
        flash("A photo with this name already exists in the database", "error")
        return redirect(url_for('upload_page'))

    filename = file.filename
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    if os.path.exists(filepath):
        flash("A file with this filename already exists", "error")
        return redirect(url_for('upload_page'))

    if not allowed_file(file.filename):
        flash("File type not allowed. Only JPG, JPEG, PNG.", "error")
        return redirect(url_for('upload_page'))

    file.save(filepath)

    if file:

        # Extract metadata
        taken, make, model, lat, lon = extract_exif(filepath)
        original_filename = file.filename
        date_taken = format_exif_datetime(taken)
        upload_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        # Compute hash
        file_hash = compute_hash(filepath)

        # Store in DB
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("""INSERT INTO photos 
                     (name, filename, filepath, upload_datetime, taken_datetime, make, model, gps_lat, gps_lon, hash)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (photo_name, original_filename, filepath, upload_time, date_taken, make, model, lat, lon, file_hash))
        conn.commit()
        conn.close()

        return redirect(url_for('output_page', name=photo_name))

@app.route('/output/<name>')
def output_page(name):
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM photos WHERE name = ?", (name,))
    data = c.fetchone()
    conn.close()

    return render_template('output.html', data=data)

@app.route("/delete/<name>", methods=["POST"])
def delete_photo(name):
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT * FROM photos WHERE name = ?", (name,))
    photo = c.fetchone()

    if not photo:
        conn.close()
        flash("Photo not found.", "error")
        return redirect(url_for("dashboard"))

    filepath = photo["filepath"]
    if os.path.exists(filepath):
        os.remove(filepath)

    c.execute("DELETE FROM photos WHERE name = ?", (name,))
    conn.commit()
    conn.close()

    flash("Photo deleted successfully.", "success")
    return redirect(url_for("dashboard"))


def wrap_text(text, max_chars=60):
    return [text[i:i+max_chars] for i in range(0, len(text), max_chars)]

def load_oriented_image(path):
    img = Image.open(path)
    try:
        for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == 'Orientation':
                break
        exif = img._getexif()
        if exif and orientation in exif:
            o = exif[orientation]
            if o == 3:
                img = img.rotate(180, expand=True)
            elif o == 6:
                img = img.rotate(270, expand=True)
            elif o == 8:
                img = img.rotate(90, expand=True)
    except:
        pass
    return img

@app.route("/report/<name>")
def generate_report(name):
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM photos WHERE name = ?", (name,))
    data = c.fetchone()
    conn.close()

    if not data:
        flash("Photo not found.", "error")
        return redirect(url_for("dashboard"))

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)

    # -----------------------------
    # PAGE 1 — IMAGE + METADATA
    # -----------------------------
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(40, height - 40, "Forensic Image Metadata Report")

    pdf.setFont("Helvetica", 12)
    generated_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    pdf.drawString(40, height - 65, f"Report Generated: {generated_time}")

    # Image
    # -----------------------------
    # IMAGE LOADING (Render-safe)
    # -----------------------------
    try:
        # Determine correct file path
        filepath = data["filepath"]
        if not os.path.exists(filepath):
            static_path = os.path.join("static/uploads", data["filename"])
            if os.path.exists(static_path):
                filepath = static_path
            else:
                raise FileNotFoundError("Image not found in /tmp or static/uploads")

        # Load the image directly from disk
        oriented = load_oriented_image(filepath)

        img_buffer = io.BytesIO()
        oriented.save(img_buffer, format="JPEG")
        img_buffer.seek(0)
        img = ImageReader(img_buffer)

        pdf.drawImage(
            img,
            40,
            height - 380,
            width=350,
            height=300,
            preserveAspectRatio=True,
            anchor='nw'
        )

    except Exception as e:
        pdf.setFont("Helvetica", 12)
        pdf.drawString(40, height - 380, "(Image unavailable on server)")

    # Metadata (without hash)
    text_x = 420
    text_y = height - 120

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(text_x, text_y, "Extracted Metadata")
    text_y -= 25

    fields = [
        ("Photo Name", data["name"]),
        ("File Name", data["filename"]),
        ("Upload Time", data["upload_datetime"]),
        ("Date Taken (EXIF)", data["taken_datetime"] or "None"),
        ("Camera Make", data["make"] or "Unknown"),
        ("Camera Model", data["model"] or "Unknown"),
        ("GPS Latitude", data["gps_lat"] if data["gps_lat"] else "None"),
        ("GPS Longitude", data["gps_lon"] if data["gps_lon"] else "None"),
    ]

    pdf.setFont("Helvetica", 12)
    for label, value in fields:
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(text_x, text_y, f"{label}:")
        pdf.setFont("Helvetica", 12)
        pdf.drawString(text_x + 160, text_y, str(value))
        text_y -= 18

    # HASH moved to bottom of page 1
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, 80, "SHA-256 Hash:")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(160, 80, data["hash"])

    pdf.showPage()

    pdf.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{data['name']}_report.pdf",
        mimetype="application/pdf"
    )

@app.route('/tmp_uploads/<filename>')
def tmp_uploads(filename):
    return send_file(os.path.join('/tmp/uploads', filename))


@app.route("/timeline/select")
def timeline_select():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT * FROM photos ORDER BY taken_datetime ASC")
    rows = c.fetchall()
    conn.close()

    photos = [dict(r) for r in rows]

    return render_template("timeline_select.html", photos=photos)

@app.route("/timeline/build", methods=["POST"])
def timeline_build():
    selected_ids = request.form.getlist("photo_ids")

    if not selected_ids:
        flash("No images selected for timeline.")
        return redirect(url_for("timeline/select"))

    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    query = f"""
        SELECT * FROM photos
        WHERE id IN ({','.join(['?'] * len(selected_ids))})
        AND gps_lat IS NOT NULL AND gps_lon IS NOT NULL
        ORDER BY taken_datetime ASC
    """

    c.execute(query, selected_ids)
    rows = c.fetchall()
    conn.close()

    photos = [dict(r) for r in rows]

    return render_template("timeline.html", photos=photos)

if __name__ == "__main__":
    app.run(debug=True)