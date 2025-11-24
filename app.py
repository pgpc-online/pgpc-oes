from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, jsonify, flash, abort, session
)
import sqlite3, os, uuid, json, random, time
from werkzeug.utils import secure_filename
from datetime import datetime
from docx.shared import Mm
from flask_mail import Mail, Message
import time
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "replace-with-a-secure-random-string"


app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'PGPCaden@gmail.com'
app.config['MAIL_PASSWORD'] = 'xpygveycmpdihphv'  
mail = Mail(app)

# ---------------------- FOLDERS & DB ----------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT = os.path.abspath(os.path.join(APP_DIR, "uploads"))
os.makedirs(UPLOAD_ROOT, exist_ok=True)

DB_FILE = os.path.join(APP_DIR, "pgpc.db")
ALLOWED_EXT = {"png", "jpg", "jpeg", "pdf"}

def ensure_app_folder(app_id):
    folder = os.path.abspath(os.path.join(UPLOAD_ROOT, app_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

# DB helpers
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    # STUDENTS TABLE (needed for OTP, login, applications)
    c.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        email TEXT UNIQUE,
        password TEXT
    )
    """)

    # ADMIN TABLE
    c.execute("""
    CREATE TABLE IF NOT EXISTS admin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )
    """)

    # Default admin
    ADMIN_DEFAULT_PW = generate_password_hash("admin123")

    c.execute("""
    INSERT OR IGNORE INTO admin (id, username, password)
    VALUES (1, 'admin', ?)
    """, (ADMIN_DEFAULT_PW,))

    # APPLICANTS TABLE
    c.execute("""
    CREATE TABLE IF NOT EXISTS applicants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT UNIQUE,
        student_id INTEGER,
        first_name TEXT,
        last_name TEXT,
        email TEXT,
        program TEXT,
        created_at TEXT,
        form_json TEXT,
        submitted INTEGER DEFAULT 0,
        FOREIGN KEY(student_id) REFERENCES students(id)
    )
    """)

    # DOCUMENTS TABLE
    c.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT,
        doc_type TEXT,
        file_path TEXT
    )
    """)
    
    # EXAM SCHEDULE
    c.execute("""
    CREATE TABLE IF NOT EXISTS exam_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT UNIQUE,
        exam_date TEXT,
        exam_time TEXT,
        exam_room TEXT,
        notes TEXT,
        created_at TEXT
    )
    """)

    # APPLICATION STATUS
    c.execute("""
    CREATE TABLE IF NOT EXISTS application_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT UNIQUE,
        exam_taken INTEGER DEFAULT 0,   -- 0/1
        approved INTEGER DEFAULT 0,     -- 0/1
        enrolled INTEGER DEFAULT 0,     -- 0/1
        updated_at TEXT
    )
    """)

    try:
        c.execute("ALTER TABLE application_status ADD COLUMN rejected INTEGER DEFAULT 0")
    except:
     pass

    try:
        c.execute("ALTER TABLE application_status ADD COLUMN reject_reason TEXT")
    except:
        pass
    
    try:
        c.execute("ALTER TABLE application_status ADD COLUMN exam_status TEXT DEFAULT 'not_set'")
    except:
        pass

    # MESSAGES (student <-> admin)
    c.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT,
        student_id INTEGER,
        sender TEXT,       -- 'student' or 'admin'
        message TEXT,
        created_at TEXT,
        read_by_admin INTEGER DEFAULT 0,
        read_by_student INTEGER DEFAULT 0
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS application_counter (
    year INTEGER PRIMARY KEY,
    counter INTEGER
    )
    """)

    conn.commit()
    conn.close()

init_db()

#auto increment application id:

def generate_application_id():
    year = datetime.now().year

    conn = get_conn()
    c = conn.cursor()

    # Get current counter for this year
    c.execute("SELECT counter FROM application_counter WHERE year=?", (year,))
    row = c.fetchone()

    if row is None:
        counter = 1
        c.execute("INSERT INTO application_counter (year, counter) VALUES (?, ?)", (year, counter))
    else:
        counter = row["counter"] + 1
        c.execute("UPDATE application_counter SET counter=? WHERE year=?", (counter, year))

    conn.commit()
    conn.close()

    # Format: AN-2025-00001
    counter_str = str(counter).zfill(5)
    return f"AN-{year}-{counter_str}"


@app.route("/")
def index():
    return render_template('homepage.html')


@app.route("/login", methods=["GET"])
def login_page():
    return render_template("loginn.html")
    
@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email")
    password = request.form.get("password")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, password FROM students WHERE email=?", (email,))
    row = c.fetchone()

    if not row:
        conn.close()
        return "Invalid email or password", 400

    # Password check (hashed version)
    if not check_password_hash(row["password"], password):
        conn.close()
        return "Invalid email or password", 400

    student_id = row["id"]

    # Store session
    session["student_id"] = student_id
    session["student_email"] = email

    # Check for existing application
    c.execute("SELECT app_id, submitted FROM applicants WHERE student_id=?", (student_id,))
    app = c.fetchone()
    conn.close()

    # CASE 1: No application yet → go to form
    if not app:
        return redirect("/form")

    # CASE 2: Application exists but NOT submitted → go to form
    if app["submitted"] == 0:
        return redirect("/form")

    # CASE 3: Application exists *AND* submitted → success page
    app_id = app["app_id"]
    return redirect(f"/success/{app_id}")
    
from functools import wraps
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "admin" not in session:
            return redirect("/admin")  # Redirect to admin login
        return f(*args, **kwargs)
    return wrapper
    
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "student_id" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function
# ---------- ROUTES ----------



# ---- FORM: GET shows blank or existing application, POST creates/updates DB entry ----

@app.route('/send_otp', methods=['POST'])
def send_otp():
    email = request.form['email'].strip()
    password = request.form['password'].strip()
    username = request.form['username'].strip()

    session['temp_username'] = username
    session['temp_email'] = email
    session['temp_password'] = password

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM students WHERE email=?", (email,))
    if c.fetchone():
        conn.close()
        return jsonify({'success': False, 'message': 'Email already registered.'})
    conn.close()

    otp = str(random.randint(100000, 999999))
    session['otp'] = otp
    session['otp_time'] = time.time()

    try:
        msg = Message(
            'Your OTP Code',
            sender=app.config['MAIL_USERNAME'],
            recipients=[email]
        )
        msg.body = f"Hello {username},\n\nYour OTP is {otp}.\nThis code expires in 10 minutes."
        mail.send(msg)
        return jsonify({'success': True, 'message': 'OTP sent to your email!'})
    except Exception as e:
        print("MAIL ERROR:", e)
        return jsonify({'success': False, 'message': 'Failed to send OTP'})
 
@app.route('/verify_otp', methods=['POST'])
def verify_otp():
    user_otp = request.form.get('otp', '').strip()
    current_time = time.time()
    stored_otp = session.get('otp')
    otp_time = session.get('otp_time')
    email = session.get('temp_email')
    password = session.get('temp_password')
    username = session.get('temp_username')

    if not stored_otp or not otp_time or not email or not password or not username:
        return jsonify({'success': False, 'message': 'Missing information. Restart registration.'})

    if current_time - otp_time > 600:
        return jsonify({'success': False, 'message': 'OTP expired. Please resend.'})

    if user_otp == stored_otp:
    
        hashed_pw = generate_password_hash(password)
    
        conn = get_conn()
        c = conn.cursor()
        c.execute("""INSERT INTO students (username, email, password)
                     VALUES (?, ?, ?)""",
                  (username, email, hashed_pw))
        conn.commit()
    
        # AUTO LOGIN NEW ACCOUNT
        session["student_id"] = c.lastrowid
        session["student_email"] = email
    
        conn.close()
        
        for key in ['otp', 'otp_time', 'temp_email', 'temp_password', 'temp_username']:
            session.pop(key, None)

        return jsonify({'success': True, 'message': 'Account registered!', 'redirect': '/form'})
    else:
        return jsonify({'success': False, 'message': 'Incorrect OTP.'})

    
            
@app.route("/reset_password", methods=["POST"])
def reset_password():
    otp = request.form.get("otp")
    new_password = request.form.get("new_password")
    saved_otp = session.get("reset_otp")
    email = session.get("reset_email")

    if otp != saved_otp:
        return jsonify({"success": False, "message": "Incorrect OTP!"})

    hashed_pw = generate_password_hash(new_password)

    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE students SET password=? WHERE email=?", (hashed_pw, email))
    conn.commit()
    conn.close()

    session.pop("reset_otp", None)
    session.pop("reset_email", None)

    return jsonify({"success": True, "message": "Password successfully reset!"})
    
 #FORGET PASS
@app.route("/forget_password", methods=["POST"])
def forget_password():
    email = request.form.get("email")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM students WHERE email=?", (email,))
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({"success": False, "message": "Email not found!"})

    otp = str(random.randint(100000, 999999))
    session["reset_otp"] = otp
    session["reset_email"] = email

    print("RESET OTP:", otp)

    return jsonify({"success": True, "message": "OTP sent!"})

#FORM    
@app.route("/form", methods=["GET", "POST"])
@login_required
def form_page():
    student_id = session["student_id"]

    # allow editing via ?app_id=<uuid>
    app_id = request.args.get("app_id")

    conn = get_conn()
    c = conn.cursor()

    # When POST: use hidden app_id from form if present (safe path)
    if request.method == "POST":
        submitted_app_id = request.form.get("app_id") or app_id

        form = {k: request.form.get(k) for k in request.form.keys()}
        first = (form.get("firstName") or "").strip()
        last = (form.get("lastName") or "").strip()
        email = session.get("student_email")  # force email from account
        program = (form.get("program") or "").strip()
        now = datetime.utcnow().isoformat()

        # If app_id supplied → update the existing record (ensure ownership)
        if submitted_app_id:
            c.execute("SELECT student_id, submitted FROM applicants WHERE app_id=?", (submitted_app_id,))
            existing_row = c.fetchone()
            if not existing_row or existing_row["student_id"] != student_id:
                conn.close()
                abort(403)
            if existing_row["submitted"] == 1:
                conn.close()
                flash("This application has already been submitted and cannot be edited.")
                return redirect(url_for("preview", app_id=submitted_app_id))

            c.execute("""
                UPDATE applicants
                SET first_name=?, last_name=?, email=?, program=?, form_json=?, created_at=?
                WHERE app_id=? AND student_id=?
            """, (first, last, email, program, json.dumps(form), now, submitted_app_id, student_id))
            app_id = submitted_app_id

        else:
            # Create new application
            app_id = generate_application_id()
            c.execute("""
                INSERT INTO applicants (app_id, student_id, first_name, last_name, email, program, created_at, form_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (app_id, student_id, first, last, email, program, now, json.dumps(form)))

        conn.commit()

        # handle photo upload (same as before)
        photo = request.files.get("pic2x2") or request.files.get("photo")
        if photo and photo.filename != "" and allowed_file(photo.filename):
            folder = os.path.join(UPLOAD_ROOT, app_id)
            os.makedirs(folder, exist_ok=True)
            filename = secure_filename("photo_" + photo.filename)
            full = os.path.join(folder, filename)
            photo.save(full)

            c.execute("DELETE FROM documents WHERE app_id=? AND doc_type='photo'", (app_id,))
            c.execute("INSERT INTO documents (app_id, doc_type, file_path) VALUES (?, 'photo', ?)",
                      (app_id, f"{app_id}/{filename}"))
            conn.commit()

        conn.close()
        return redirect(url_for("upload_documents", app_id=app_id))

    # GET: if app_id provided, load it and ensure it belongs to user
    form = {}
    documents = {}
    if app_id:
        c.execute("SELECT student_id, form_json, submitted FROM applicants WHERE app_id=?", (app_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            flash("Application not found.")
            return redirect(url_for("form_page"))

        if row["student_id"] != student_id:
            conn.close()
            abort(403)

        # Prevent editing if already submitted
        if row["submitted"] == 1:
            conn.close()
            flash("This application has already been submitted and is locked.")
            return redirect(url_for("preview", app_id=app_id))

        form = json.loads(row["form_json"] or "{}")
        # load documents for previewing photo/thumb
        c.execute("SELECT doc_type, file_path FROM documents WHERE app_id=?", (app_id,))
        documents = {r["doc_type"]: r["file_path"] for r in c.fetchall()}

    else:
        # Try to load existing application for this student (if any) to prefill
        
        c.execute("""
            SELECT app_id, form_json, submitted
            FROM applicants
            WHERE student_id=? AND submitted=0
        """, (student_id,))
        existing = c.fetchone()
        if existing:
            # allow editing only if not submitted
            if existing["submitted"] == 0:
                app_id = existing["app_id"]
                form = json.loads(existing["form_json"] or "{}")
                c.execute("SELECT doc_type, file_path FROM documents WHERE app_id=?", (app_id,))
                documents = {r["doc_type"]: r["file_path"] for r in c.fetchall()}
            else:
                # already submitted → redirect to success/preview depending on your flow
                app_id = existing["app_id"]
                conn.close()
                return redirect(url_for("preview", app_id=app_id))

    conn.close()
    return render_template("form.html", form=form, app_id=app_id, documents=documents)
    
# ---- UPLOAD DOCUMENTS (uses app_id in route) ----
@app.route("/upload_documents/<app_id>", methods=["GET", "POST"])
@login_required
def upload_documents(app_id):

    student_id = session["student_id"]

    conn = get_conn()
    c = conn.cursor()

    # verify application exists & belongs to the logged-in student
    c.execute("SELECT student_id, form_json FROM applicants WHERE app_id=?", (app_id,))
    app_row = c.fetchone()

    if not app_row:
        conn.close()
        flash("Application not found.")
        return redirect(url_for("form_page"))

    if app_row["student_id"] != student_id:
        conn.close()
        abort(403)  # forbidden access

    # POST — Upload documents
    if request.method == "POST":

        for key in ("psa", "form138", "goodmoral", "psa_certificate", "form_138"):
            f = request.files.get(key)

            if f and f.filename != "" and allowed_file(f.filename):
                folder = os.path.join(UPLOAD_ROOT, app_id)
                os.makedirs(folder, exist_ok=True)

                filename = secure_filename(f"{key}_" + f.filename)
                full = os.path.join(folder, filename)
                f.save(full)

                rel = f"{app_id}/{filename}"

                # Remove old file of same type
                c.execute("DELETE FROM documents WHERE app_id=? AND doc_type=?", (app_id, key))
                c.execute("""
                    INSERT INTO documents (app_id, doc_type, file_path)
                    VALUES (?, ?, ?)
                """, (app_id, key, rel))

        conn.commit()
        conn.close()

        return redirect(url_for("preview", app_id=app_id))

    # GET — Load documents list
    c.execute("SELECT doc_type, file_path FROM documents WHERE app_id=?", (app_id,))
    docs = {row["doc_type"]: row["file_path"] for row in c.fetchall()}

    conn.close()

    form = json.loads(app_row["form_json"] or "{}")

    return render_template("upload_documents.html", form=form, documents=docs, app_id=app_id)




# ---- FINAL SUBMIT ----
@app.route("/final_submit/<app_id>", methods=["POST"])
@login_required
def final_submit(app_id):
    student_id = session["student_id"]

    conn = get_conn()
    c = conn.cursor()

    # Verify ownership and current submitted state
    c.execute("SELECT student_id, form_json, submitted FROM applicants WHERE app_id=?", (app_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        abort(404)
    if row["student_id"] != student_id:
        conn.close()
        abort(403)

    if row["submitted"] == 1:
        conn.close()
        flash("This application has already been submitted.")
        return redirect(url_for("preview", app_id=app_id))

    form = json.loads(row["form_json"] or "{}")

    # Required fields check: ensure name fields present
    if not form.get("firstName") or not form.get("lastName"):
        conn.close()
        flash("Complete all required fields first.")
        return redirect(url_for("preview", app_id=app_id))

    # Mark submitted
    c.execute("UPDATE applicants SET submitted=1 WHERE app_id=?", (app_id,))
    conn.commit()

    # (Optional) save summary file
    folder = os.path.join(UPLOAD_ROOT, app_id)
    os.makedirs(folder, exist_ok=True)
    summary = os.path.join(folder, "summary.txt")
    with open(summary, "w", encoding="utf-8") as f:
        f.write(f"Application ID: {app_id}\n\n")
        for k, v in form.items():
            f.write(f"{k}: {v}\n")

    conn.close()

    # Redirect to success page (you already have success/<app_id> route)
    return redirect(url_for("success_page", app_id=app_id))
    
 #SUCCESS/ DASHBOARD ----
@app.route("/success/<app_id>")
@login_required
def success_page(app_id):

    student_id = session["student_id"]
    conn = get_conn()
    c = conn.cursor()

    # Get form data
    c.execute("SELECT form_json, submitted FROM applicants WHERE app_id=?", (app_id,))
    row = c.fetchone()
    form = json.loads(row["form_json"] or "{}")
    submitted = row["submitted"]

    # Get exam schedule
    c.execute("SELECT * FROM exam_schedule WHERE app_id=?", (app_id,))
    exam = c.fetchone()   # may be None

    # Load photo
    c.execute("SELECT file_path FROM documents WHERE app_id=? AND doc_type='photo'", (app_id,))
    d = c.fetchone()
    photo_filename = d["file_path"] if d else None

    # Get application status
    c.execute("SELECT * FROM application_status WHERE app_id=?", (app_id,))
    st = c.fetchone()

    # Determine dashboard mode
    if not exam:
        mode = "waiting_exam"
    else:
        if not st or st["exam_taken"] == 0:
            mode = "exam_scheduled"
        elif st["exam_taken"] == 1 and st["approved"] == 0:
            mode = "exam_pending"
        elif st["approved"] == 1 and st["enrolled"] == 0:
            mode = "approved"
        elif st["enrolled"] == 1:
            mode = "enrolled"
        else:
            mode = "exam_pending"

    # Unread messages
    c.execute("""
        SELECT COUNT(*)
        FROM messages
        WHERE app_id=? AND sender='admin' AND read_by_student=0
    """, (app_id,))
    unread = c.fetchone()[0]

    conn.close()

    return render_template(
        "success.html",
        form=form,
        app_id=app_id,
        exam=exam,
        mode=mode,
        unread=unread,
        photo_filename=photo_filename
    )

# ---------------- Additional helpers ----------------
def get_app_by_student(student_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT app_id, form_json, submitted FROM applicants WHERE student_id=? ORDER BY id DESC LIMIT 1", (student_id,))
    r = c.fetchone()
    conn.close()
    return r

# ---------------- Messaging (student) ----------------
@app.route("/messages/<app_id>")
@login_required
def messages_page(app_id):
    # student messages view
    student_id = session["student_id"]
    conn = get_conn(); c = conn.cursor()
    # mark admin messages as read
    c.execute("UPDATE messages SET read_by_student=1 WHERE app_id=? AND sender='admin'", (app_id,))
    conn.commit()
    conn.close()
    return render_template("messages.html", app_id=app_id)

@app.route("/api/messages/<app_id>", methods=["GET"])
@login_required
def api_get_messages(app_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT id, student_id, sender, message, created_at FROM messages WHERE app_id=? ORDER BY id ASC", (app_id,))
    msgs = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(msgs)

@app.route("/api/messages/send", methods=["POST"])
@login_required
def api_send_message():
    data = request.form or request.json
    app_id = data.get("app_id")
    text = data.get("message")
    sender = data.get("sender")  # should be 'student' here
    student_id = session["student_id"]
    now = datetime.utcnow().isoformat()

    if not app_id or not text or not sender:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    conn = get_conn(); c = conn.cursor()
    c.execute("""
        INSERT INTO messages (app_id, student_id, sender, message, created_at, read_by_admin, read_by_student)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (app_id, student_id, sender, text, now, 0 if sender=='student' else 1, 1 if sender=='student' else 0))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ---------------- Admin: view messages for student ----------------
@app.route("/admin/messages/<app_id>")
@admin_required
def admin_messages(app_id):
    conn = get_conn(); c = conn.cursor()

    # mark student messages as read_by_admin
    c.execute("UPDATE messages SET read_by_admin=1 WHERE app_id=? AND sender='student'", (app_id,))

    # get student name
    c.execute("SELECT form_json FROM applicants WHERE app_id=?", (app_id,))
    row = c.fetchone()

    if row:
        form = json.loads(row["form_json"] or "{}")
        full_name = f"{form.get('lastName', '')}, {form.get('firstName', '')}"
    else:
        full_name = "Unknown Student"

    conn.commit()
    conn.close()

    return render_template(
        "admin_messages.html",
        app_id=app_id,
        student_name=full_name  # ← send name
    )

@app.route("/admin/api/messages/<app_id>", methods=["GET"])
@admin_required
def admin_api_get_messages(app_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT id, student_id, sender, message, created_at FROM messages WHERE app_id=? ORDER BY id ASC", (app_id,))
    msgs = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(msgs)

# ---- Serve uploaded files ----
@app.route("/uploads/<path:filepath>")
@app.route("/uploads/<app_id>/<filename>")
def uploaded_file(filepath=None, app_id=None, filename=None):
    # Build absolute target path
    if filepath:
        candidate = os.path.abspath(os.path.join(UPLOAD_ROOT, filepath))
    elif app_id and filename:
        candidate = os.path.abspath(os.path.join(UPLOAD_ROOT, app_id, filename))
    else:
        abort(404)

    # Security — ensure inside uploads folder
    upload_root_abs = os.path.abspath(UPLOAD_ROOT)
    if not candidate.startswith(upload_root_abs + os.sep):
        abort(403)

    if not os.path.exists(candidate):
        return "File not found", 404

    folder = os.path.dirname(candidate)
    fname = os.path.basename(candidate)
    return send_from_directory(folder, fname)

# ---- Minimal student registration/login copy (keeps your student table usage) ----
@app.route("/students")  
def students_list():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, username, email FROM students")
    rows = c.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
 

@app.route("/preview/<app_id>")
@login_required
def preview(app_id):
    student_id = session["student_id"]

    conn = get_conn()
    c = conn.cursor()

    # Load application with submitted flag
    c.execute("SELECT student_id, form_json, submitted FROM applicants WHERE app_id=?", (app_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        flash("Application not found.")
        return redirect(url_for("form_page"))

    if row["student_id"] != student_id:
        conn.close()
        abort(403)

    form = json.loads(row["form_json"] or "{}")

    # Load uploaded docs
    c.execute("SELECT doc_type, file_path FROM documents WHERE app_id=?", (app_id,))
    docs = {r["doc_type"]: r["file_path"] for r in c.fetchall()}
    conn.close()

    submitted_flag = bool(row["submitted"])

    # pass submitted flag so template can lock the UI
    return render_template(
        "preview.html",
        form=form,
        documents=docs,
        app_id=app_id,
        submitted=submitted_flag
    )
@app.route('/logout')

def student_logout():
    session.pop('student_id', None)
    session.pop('student_email', None)
    return redirect('/')
  #=========== ADMIN SIDE ==================


#---------- ADMIN DASHBOARD ----------

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():

    conn = get_conn()
    c = conn.cursor()

    # Total number of students
    c.execute("SELECT COUNT(*) FROM students")
    total_students = c.fetchone()[0]

    # Total submitted applications
    c.execute("SELECT COUNT(*) FROM applicants WHERE submitted = 1")
    total_submitted = c.fetchone()[0]

    # Total approved
    c.execute("""
        SELECT COUNT(*) 
        FROM application_status 
        WHERE approved = 1
    """)
    total_approved = c.fetchone()[0]

    # Total enrolled
    c.execute("""
        SELECT COUNT(*) 
        FROM application_status 
        WHERE enrolled = 1
    """)
    total_enrolled = c.fetchone()[0]

    # Gender count
    c.execute("""
        SELECT COUNT(*) 
        FROM applicants 
        WHERE json_extract(form_json, '$.gender') = 'Male'
    """)
    male_count = c.fetchone()[0]

    c.execute("""
        SELECT COUNT(*) 
        FROM applicants 
        WHERE json_extract(form_json, '$.gender') = 'Female'
    """)
    female_count = c.fetchone()[0]

    # Course distribution
    c.execute("""
        SELECT program, COUNT(*) as count
        FROM applicants
        WHERE program IS NOT NULL AND program != ''
        GROUP BY program
    """)
    course_rows = c.fetchall()

    courses = [row['program'] for row in course_rows]
    course_counts = [row['count'] for row in course_rows]
    # Recent 5 submitted applications
    c.execute("""
        SELECT 
            a.app_id,
            a.first_name,
            a.last_name,
            a.program,
            a.created_at
        FROM applicants a
        WHERE a.submitted = 1
        ORDER BY a.created_at DESC
        LIMIT 5
    """)
    recent_submitted = c.fetchall()
    conn.close()

    return render_template(
    "admin_dashboard.html",
    total_students=total_students,
    total_submitted=total_submitted,
    total_approved=total_approved,
    total_enrolled=total_enrolled,
    male_count=male_count,
    female_count=female_count,
    courses=courses,
    course_counts=course_counts,
    recent_submitted=recent_submitted
)






#----- ADMIN LOGIN --------
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT username, password FROM admin WHERE username=?", (username,))
        admin = c.fetchone()
        conn.close()

        if admin and check_password_hash(admin["password"], password):
            session['admin'] = username
            return redirect('/admin/dashboard')
        else:
            return "Invalid admin credentials"

    return render_template('admin_login.html')

#---------- MANAGE STUDENTS ----------

@app.route("/admin/manage-students")
@admin_required
def manage_students():
    course_filter = request.args.get("course", "")
    search_query = request.args.get("search", "")
    sort_by = request.args.get("sort", "new")   # default newest first

    conn = get_conn()
    c = conn.cursor()

    # ---- BASE QUERY WITH JOINS ----
    
    sql = """
    SELECT 
    s.id AS student_id,
    s.email,
    a.app_id,
    a.form_json,
    a.program,
    a.created_at,
    a.submitted,

    COALESCE(st.exam_taken, 0)   AS exam_taken,
    COALESCE(st.approved, 0)     AS approved,
    COALESCE(st.enrolled, 0)     AS enrolled,
    COALESCE(st.rejected, 0)     AS rejected,
    st.reject_reason,

    CASE
        WHEN st.rejected = 1 THEN 'Rejected'
        WHEN st.enrolled = 1 THEN 'Enrolled'
        WHEN st.approved = 1 THEN 'Approved'
        WHEN st.exam_taken = 1 THEN 'Exam Taken'
        WHEN a.submitted = 1 THEN 'Submitted'
        ELSE 'Draft'
    END AS status

    FROM students s
    LEFT JOIN applicants a ON a.student_id = s.id
    LEFT JOIN application_status st ON st.app_id = a.app_id
    WHERE 1=1
    """

    params = []

    # --- COURSE FILTER ---
    if course_filter:
        sql += " AND a.program = ?"
        params.append(course_filter)

    # --- SEARCH FILTER (name or email) ---
    if search_query:
        sql += " AND (s.email LIKE ? OR a.form_json LIKE ?)"
        params.append(f"%{search_query}%")
        params.append(f"%{search_query}%")

    # --- SORTING ---
    if sort_by == "new":
        sql += " ORDER BY a.created_at DESC"
    elif sort_by == "old":
        sql += " ORDER BY a.created_at ASC"
    elif sort_by == "az":
        sql += " ORDER BY json_extract(a.form_json, '$.lastName') ASC"
    elif sort_by == "za":
        sql += " ORDER BY json_extract(a.form_json, '$.lastName') DESC"
    else:
        sql += " ORDER BY a.created_at DESC"

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    # ---- PROCESS JSON + STATUS ----
    students = []
    for r in rows:
        form = json.loads(r["form_json"] or "{}")

        # Determine display status
        if r["enrolled"] == 1:
            status = "Enrolled"
        elif r["approved"] == 1:
            status = "Approved"
        elif r["rejected"] == 1:
            status = "Rejected"
        elif r["submitted"] == 1:
            status = "Submitted"
        else:
            status = "Not Submitted"

        students.append({
        "id": r["student_id"],
        "email": r["email"],
        "app_id": r["app_id"],
        "submitted": r["submitted"],
        "status": status,
        "created_at": r["created_at"],

        "firstName": form.get("firstName", ""),
        "lastName": form.get("lastName", ""),
        "program": r["program"] or " ",

        "exam_taken": int(r["exam_taken"]), 
        "approved": int(r["approved"]),
        "enrolled": int(r["enrolled"]),
        "rejected": int(r["rejected"]),
})

    return render_template(
        "manage_students.html",
        students=students,
        course_filter=course_filter,
        search_query=search_query,
        sort_by=sort_by,
        active_page="users"
    )

#admin view and edit students info

@app.route("/admin/preview/<app_id>", methods=["GET"])
@admin_required
def admin_preview(app_id):
    conn = get_conn()
    c = conn.cursor()

    # Get student form JSON
    c.execute("SELECT form_json FROM applicants WHERE app_id=?", (app_id,))
    row = c.fetchone()
    form = json.loads(row["form_json"] or "{}") if row else {}

    # Load documents (photo, psa, form138, good moral, etc.)
    c.execute("SELECT doc_type, file_path FROM documents WHERE app_id=?", (app_id,))
    docs = {d["doc_type"]: d["file_path"] for d in c.fetchall()}

    mode = request.args.get("mode", "view")
    
    conn.close()

    return render_template(
        "admin_preview_edit.html",
        app_id=app_id,
        form=form,
        documents=docs,
        photo_filename=docs.get("photo"),
        mode=mode
    )
#admin saves edited info

@app.route("/admin/save-student/<app_id>", methods=["POST"])
@admin_required
def admin_save_student(app_id):
    conn = get_conn()
    c = conn.cursor()

    # ------------- Save Form JSON -------------
    updated_form = {}

    # Loop through all POST fields
    for key in request.form:
        updated_form[key] = request.form[key]

    # Convert to JSON and store
    updated_json = json.dumps(updated_form)

    c.execute(
        "UPDATE applicants SET form_json=? WHERE app_id=?",
        (updated_json, app_id)
    )

    # ------------- Save New Photo (Optional) -------------
    photo = request.files.get("photo")

    if photo and photo.filename != "":
        filename = secure_filename(photo.filename)
        ext = filename.split(".")[-1].lower()
        new_name = f"{app_id}_photo.{ext}"

        save_path = os.path.join("uploads", "students", new_name)
        photo.save(save_path)

        # update DB
        c.execute("""
            INSERT OR REPLACE INTO documents (app_id, doc_type, file_path)
            VALUES (?, 'photo', ?)
        """, (app_id, f"students/{new_name}"))

    conn.commit()
    conn.close()

    # Redirect back to view mode
    return redirect(url_for("admin_preview", app_id=app_id))


#APPROVE APPLICATION
@app.route("/admin/approve/<app_id>", methods=["POST"])
@admin_required
def admin_approve(app_id):
    conn = get_conn(); c = conn.cursor()

    # CHECK IF EXAM TAKEN
    c.execute("SELECT exam_taken FROM application_status WHERE app_id=?", (app_id,))
    row = c.fetchone()

    if not row or row["exam_taken"] != 1:
        flash("❌ Cannot approve: Student has NOT taken the exam.", "error")
        return redirect(url_for("manage_students"))

    now = datetime.utcnow().isoformat()
    c.execute("""
        INSERT INTO application_status (app_id, approved, updated_at)
        VALUES (?, 1, ?)
        ON CONFLICT(app_id) DO UPDATE SET approved=1, updated_at=?
    """, (app_id, now, now))

    conn.commit(); conn.close()
    flash("✔ Student approved successfully!", "success")
    return redirect(url_for("manage_students"))

#ENROLL
@app.route("/admin/enroll/<app_id>", methods=["POST"])
@admin_required
def admin_enroll(app_id):
    conn = get_conn(); c = conn.cursor()

    # CHECK IF EXAM TAKEN
    c.execute("SELECT exam_taken FROM application_status WHERE app_id=?", (app_id,))
    row = c.fetchone()

    if not row or row["exam_taken"] != 1:
        flash("❌ Cannot enroll: Student has NOT taken the exam.", "error")
        return redirect(url_for("manage_students"))

    now = datetime.utcnow().isoformat()
    c.execute("""
        INSERT INTO application_status (app_id, enrolled, updated_at)
        VALUES (?, 1, ?)
        ON CONFLICT(app_id) DO UPDATE SET enrolled=1, updated_at=?
    """, (app_id, now, now))

    conn.commit(); conn.close()
    flash("🎓 Student enrolled successfully!", "success")
    return redirect(url_for("manage_students"))

#REJECT 
@app.route("/admin/reject/<app_id>", methods=["POST"])
@admin_required
def admin_reject(app_id):
    reason = request.form.get("reason", "No reason provided")
    now = datetime.utcnow().isoformat()

    conn = get_conn(); c = conn.cursor()
    c.execute("""
        INSERT INTO application_status (app_id, rejected, reject_reason, updated_at)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(app_id) DO UPDATE SET rejected=1, reject_reason=?, updated_at=?
    """, (app_id, reason, now, reason, now))

    conn.commit(); conn.close()
    return redirect(url_for("manage_students"))

#DELETE STUDENTS
@app.route("/admin/delete-student/<int:student_id>", methods=["POST"])
@admin_required
def admin_delete_student(student_id):
    conn = get_conn()
    c = conn.cursor()

    # Get all app_ids belonging to this student
    c.execute("SELECT app_id FROM applicants WHERE student_id=?", (student_id,))
    apps = [row["app_id"] for row in c.fetchall()]

    # Delete related rows for each app_id
    for app_id in apps:
        c.execute("DELETE FROM application_status WHERE app_id=?", (app_id,))
        c.execute("DELETE FROM exam_schedule WHERE app_id=?", (app_id,))
        c.execute("DELETE FROM documents WHERE app_id=?", (app_id,))
        c.execute("DELETE FROM messages WHERE app_id=?", (app_id,))

    # Now delete applicant & student
    c.execute("DELETE FROM applicants WHERE student_id=?", (student_id,))
    c.execute("DELETE FROM students WHERE id=?", (student_id,))

    conn.commit()
    conn.close()

    flash("Student and all related data deleted!", "success")
    return redirect(url_for("manage_students"))



#MESSAGE ADMIN
@app.route("/admin/messages")
@admin_required
def admin_all_messages():
    conn = get_conn(); c = conn.cursor()

    c.execute("""
        SELECT 
            a.app_id,
            json_extract(a.form_json, '$.firstName') AS firstName,
            json_extract(a.form_json, '$.lastName') AS lastName,
            COUNT(CASE WHEN m.read_by_admin=0 AND m.sender='student' THEN 1 END) AS unread
        FROM applicants a
        LEFT JOIN messages m ON a.app_id = m.app_id
        GROUP BY a.app_id
        ORDER BY unread DESC, lastName ASC;
    """)
    rows = c.fetchall()
    conn.close()

    return render_template("admin_all_messages.html", items=rows)



#MESSAGE STUDENT
@app.route("/admin/api/messages/send", methods=["POST"])
@admin_required
def admin_send_message():
    data = request.json
    app_id = data.get("app_id")
    text = data.get("message")
    sender = "admin"
    now = datetime.utcnow().isoformat()

    if not app_id or not text:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    conn = get_conn(); c = conn.cursor()

    # Insert admin message
    c.execute("""
        INSERT INTO messages (app_id, student_id, sender, message, created_at, read_by_admin, read_by_student)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (app_id, None, sender, text, now, 1, 0))

    conn.commit()
    conn.close()

    return jsonify({"success": True})



# Admin: set exam schedule for an app_id (GET form / POST save)
@app.route("/admin/set-schedule/<app_id>", methods=["POST"])
@admin_required
def admin_set_schedule(app_id):
    exam_date = request.form["exam_date"]
    exam_time = request.form["exam_time"]
    exam_room = request.form["exam_room"]
    notes = request.form.get("notes", "")
    now = datetime.utcnow().isoformat()

    conn = get_conn()
    c = conn.cursor()

    # UPsert into exam_schedule
    c.execute("""
        INSERT INTO exam_schedule (app_id, exam_date, exam_time, exam_room, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(app_id) DO UPDATE SET
            exam_date=excluded.exam_date,
            exam_time=excluded.exam_time,
            exam_room=excluded.exam_room,
            notes=excluded.notes,
            created_at=excluded.created_at
    """, (app_id, exam_date, exam_time, exam_room, notes, now))

    # UPDATE STATUS to "set"
    c.execute("""
        INSERT INTO application_status (app_id, exam_status, updated_at)
        VALUES (?, 'set', ?)
        ON CONFLICT(app_id) DO UPDATE SET
            exam_status='set',
            updated_at=excluded.updated_at
    """, (app_id, now))

    conn.commit()
    conn.close()

    flash("Exam schedule saved!", "success")
    return redirect(url_for("admin_exam_schedule"))

#DELETE SCHEDULE
@app.route("/admin/api/delete-schedule/<app_id>", methods=["POST"])
@admin_required
def admin_delete_schedule(app_id):
    conn = get_conn(); c = conn.cursor()

    # Remove schedule
    c.execute("DELETE FROM exam_schedule WHERE app_id=?", (app_id,))

    # Reset exam status + exam_taken flag
    c.execute("""
        INSERT INTO application_status (app_id, exam_status, exam_taken)
        VALUES (?, NULL, 0)
        ON CONFLICT(app_id) DO UPDATE SET
            exam_status=NULL,
            exam_taken=0
    """, (app_id,))

    conn.commit()
    conn.close()
    return redirect(url_for("admin_exam_schedule"))

#manage student exam schedule
@app.route("/admin/exam-schedule")
@admin_required
def admin_exam_schedule():
    conn = get_conn()
    c = conn.cursor()

    # Fetch all submitted applications & schedule + status
    c.execute("""
        SELECT 
            a.app_id,
            a.form_json,
            a.program,
            a.submitted,

            es.exam_date,
            es.exam_time,
            es.exam_room,
            es.notes,

            st.exam_taken,
            st.approved,
            st.enrolled,
            st.rejected,
            st.exam_status

        FROM applicants a
        LEFT JOIN exam_schedule es ON es.app_id = a.app_id
        LEFT JOIN application_status st ON st.app_id = a.app_id
        WHERE a.submitted = 1
        ORDER BY a.created_at DESC
    """)

    rows = c.fetchall()
    conn.close()

    schedules = []

    for r in rows:
        form = json.loads(r["form_json"] or "{}")

        # Determine visible exam_status
        if r["exam_taken"] == 1:
            exam_status = "exam_taken"
        elif r["exam_status"] == "retake":
            exam_status = "retake"
        elif r["exam_status"] == "set":
            exam_status = "set"
        else:
            exam_status = "not_set"

        schedules.append({
            "app_id": r["app_id"],
            "firstName": form.get("firstName", ""),
            "lastName": form.get("lastName", ""),
            "program": r["program"] or "N/A",

            "exam_date": r["exam_date"],
            "exam_time": r["exam_time"],
            "exam_room": r["exam_room"],
            "notes": r["notes"],

            "exam_taken": r["exam_taken"],
            "exam_status": exam_status,
        })

    return render_template(
        "admin_exam_schedule.html",
        schedules=schedules,
        active_page="exam_schedule"
    )
   
#retake
@app.route("/admin/mark-retake/<app_id>", methods=["POST"])
@admin_required
def admin_mark_retake(app_id):
    conn = get_conn(); c = conn.cursor()
    now = datetime.utcnow().isoformat()

    c.execute("""
        INSERT INTO application_status (app_id, exam_status, exam_taken, updated_at)
        VALUES (?, 'retake', 0, ?)
        ON CONFLICT(app_id) DO UPDATE SET
            exam_status='retake',
            exam_taken=0,
            updated_at=excluded.updated_at
    """, (app_id, now))

    conn.commit()
    conn.close()

    flash("Marked as Retake.", "info")
    return redirect(url_for("admin_exam_schedule"))

#SAVE SCHEDULE
@app.route("/admin/api/save-schedule", methods=["POST"])
@admin_required
def admin_save_schedule():
    data = request.form
    app_id = data.get("app_id")
    date = data.get("exam_date")
    time = data.get("exam_time")
    room = data.get("exam_room")
    notes = data.get("notes", "")
    now = datetime.utcnow().isoformat()

    conn = get_conn(); c = conn.cursor()

    # insert or update
    c.execute("""
        INSERT INTO exam_schedule (app_id, exam_date, exam_time, exam_room, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(app_id) DO UPDATE SET
            exam_date=excluded.exam_date,
            exam_time=excluded.exam_time,
            exam_room=excluded.exam_room,
            notes=excluded.notes
    """, (app_id, date, time, room, notes, now))

    conn.commit()
    conn.close()
    return jsonify({"success": True})

##mark student taken exam
@app.route("/admin/mark-exam-taken/<app_id>", methods=["POST"])
@admin_required
def admin_mark_exam_taken(app_id):
    conn = get_conn(); c = conn.cursor()
    now = datetime.utcnow().isoformat()

    c.execute("""
        INSERT INTO application_status (app_id, exam_status, exam_taken, updated_at)
        VALUES (?, 'exam_taken', 1, ?)
        ON CONFLICT(app_id) DO UPDATE SET
            exam_status='exam_taken',
            exam_taken=1,
            updated_at=excluded.updated_at
    """, (app_id, now))

    conn.commit()
    conn.close()

    flash("Marked as Taken.", "success")
    return redirect(url_for("admin_exam_schedule"))

#------ADMIN SETTINGS---------
@app.route("/admin/settings")
@admin_required
def admin_settings():
    return render_template("admin_settings.html", active_page="settings")

#---------- ADMIN LOGOUT ----------
@app.route('/admin/logout')

def admin_logout():
    session.pop('admin', None)
    return redirect('/admin')

if __name__ == "__main__":
    app.run(debug=True)