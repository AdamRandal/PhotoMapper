import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
import hashlib
import piexif
from datetime import datetime
import sqlite3
from PIL import Image

app = Flask(__name__)
app.secret_key = "secret-key"

app.config['UPLOAD_FOLDER'] = 'static/uploads'

PASSWORD = "test"

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
            if piexif.GPSIFD.GPSLatitude in gps:
                lat = convert_gps(gps[piexif.GPSIFD.GPSLatitude])
            if piexif.GPSIFD.GPSLongitude in gps:
                lon = convert_gps(gps[piexif.GPSIFD.GPSLongitude])

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
    return render_template("dashboard.html")

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
    file = request.files['photo']
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

    file.save(filepath)

    if not allowed_file(file.filename):
        flash("File type not allowed. Only JPG, JPEG, PNG.", "error")
        return redirect(url_for('upload_page'))


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

        return redirect(url_for('output_page', filename=filename))

@app.route('/output/<filename>')
def output_page(filename):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM photos WHERE filepath LIKE ?", (f"%{filename}",))
    data = c.fetchone()
    conn.close()

    return render_template('output.html', data=data)

@app.route("/timelines")
def timelines():
    return render_template("timeline.html")

if __name__ == "__main__":
    app.run(debug=True)