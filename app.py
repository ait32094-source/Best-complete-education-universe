import os
import io
import sqlite3
from functools import wraps

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template,
    session,
    flash,
    abort,
    send_file,
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)

from werkzeug.utils import secure_filename

# ============================================================
# GOOGLE OAUTH IMPORTS
# ============================================================

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from googleapiclient.discovery import build
from googleapiclient.http import (
    MediaIoBaseUpload,
    MediaIoBaseDownload,
)

# ============================================================
# CONFIG
# ============================================================

BASE = os.path.dirname(
    os.path.abspath(__file__)
)

DB = os.path.join(
    BASE,
    "portal.db"
)

# ============================================================
# GOOGLE OAUTH FILES
# ============================================================

CREDENTIALS_DIR = os.path.join(
    BASE,
    "credentials"
)

CLIENT_SECRET_FILE = os.path.join(
    CREDENTIALS_DIR,
    "client_secret.json"
)

TOKEN_FILE = os.path.join(
    CREDENTIALS_DIR,
    "token.json"
)

# ============================================================
# GOOGLE DRIVE ROOT
# ============================================================
#
# YOUR EXISTING GOOGLE DRIVE FOLDER
#
ROOT = "1s0d83iEUZDlbP7unoQkN8PGjYOC4UOLa"

# ============================================================
# GOOGLE DRIVE SCOPE
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/drive"
]

# ============================================================
# SECRET KEY
# ============================================================

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "study-notes-portal-change-this-secret"
)

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

app.secret_key = SECRET_KEY

# Maximum upload size = 100 MB
app.config["MAX_CONTENT_LENGTH"] = (
    100 * 1024 * 1024
)

# ============================================================
# GOOGLE DRIVE GLOBALS
# ============================================================

drive = None

drive_error = None

# ============================================================
# DATABASE
# ============================================================

def conn():

    c = sqlite3.connect(DB)

    c.row_factory = sqlite3.Row

    c.execute(
        "PRAGMA foreign_keys=ON"
    )

    return c


def init_db():

    c = conn()

    c.executescript(
        """

        CREATE TABLE IF NOT EXISTS admins(

            id INTEGER PRIMARY KEY,

            username TEXT UNIQUE NOT NULL,

            password_hash TEXT NOT NULL

        );


        CREATE TABLE IF NOT EXISTS classes(

            id INTEGER PRIMARY KEY,

            name TEXT UNIQUE NOT NULL,

            drive_folder_id TEXT,

            active INTEGER DEFAULT 1,

            created_at TEXT
                DEFAULT CURRENT_TIMESTAMP

        );


        CREATE TABLE IF NOT EXISTS subjects(

            id INTEGER PRIMARY KEY,

            class_id INTEGER NOT NULL,

            name TEXT NOT NULL,

            drive_folder_id TEXT,

            active INTEGER DEFAULT 1,

            created_at TEXT
                DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(class_id, name),

            FOREIGN KEY(class_id)
                REFERENCES classes(id)
                ON DELETE CASCADE

        );


        CREATE TABLE IF NOT EXISTS chapters(

            id INTEGER PRIMARY KEY,

            subject_id INTEGER NOT NULL,

            name TEXT NOT NULL,

            drive_folder_id TEXT,

            active INTEGER DEFAULT 1,

            created_at TEXT
                DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(subject_id, name),

            FOREIGN KEY(subject_id)
                REFERENCES subjects(id)
                ON DELETE CASCADE

        );


        CREATE TABLE IF NOT EXISTS documents(

            id INTEGER PRIMARY KEY,

            chapter_id INTEGER NOT NULL,

            name TEXT NOT NULL,

            drive_file_id TEXT NOT NULL,

            created_at TEXT
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(chapter_id)
                REFERENCES chapters(id)
                ON DELETE CASCADE

        );


        CREATE TABLE IF NOT EXISTS users(

            id INTEGER PRIMARY KEY,

            student_id TEXT UNIQUE NOT NULL,

            name TEXT NOT NULL,

            username TEXT UNIQUE NOT NULL,

            email TEXT NOT NULL,

            phone TEXT,

            parent_name TEXT,

            parent_phone TEXT,

            roll_no TEXT,

            class_id INTEGER NOT NULL,

            password_hash TEXT NOT NULL,

            active INTEGER DEFAULT 1,

            created_at TEXT
                DEFAULT CURRENT_TIMESTAMP,

            last_login TEXT,

            FOREIGN KEY(class_id)
                REFERENCES classes(id)

        );


        CREATE TABLE IF NOT EXISTS activity(

            id INTEGER PRIMARY KEY,

            user_id INTEGER,

            action TEXT,

            detail TEXT,

            created_at TEXT
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(user_id)
                REFERENCES users(id)

                ON DELETE SET NULL

        );

        """
    )

    # ========================================================
    # DEFAULT ADMIN
    # ========================================================

    existing_admin = c.execute(
        """
        SELECT id
        FROM admins
        WHERE username=?
        """,
        ("admin",)
    ).fetchone()

    if not existing_admin:

        c.execute(
            """
            INSERT INTO admins(
                username,
                password_hash
            )
            VALUES(?, ?)
            """,
            (
                "admin",
                generate_password_hash(
                    "admin123"
                ),
            )
        )

    c.commit()

    c.close()


# ============================================================
# GOOGLE DRIVE OAUTH
# ============================================================

def init_drive():

    global drive
    global drive_error

    drive = None
    drive_error = None

    try:

        # ----------------------------------------------------
        # Check credentials folder
        # ----------------------------------------------------

        if not os.path.exists(
            CREDENTIALS_DIR
        ):

            os.makedirs(
                CREDENTIALS_DIR,
                exist_ok=True
            )

        # ----------------------------------------------------
        # Check client_secret.json
        # ----------------------------------------------------

        if not os.path.exists(
            CLIENT_SECRET_FILE
        ):

            drive_error = (
                "Missing OAuth client_secret.json. "
                "Put your Google OAuth client JSON here: "
                f"{CLIENT_SECRET_FILE}"
            )

            print()
            print(
                "GOOGLE DRIVE ERROR:"
            )
            print(drive_error)

            return

        creds = None

        # ----------------------------------------------------
        # Existing token
        # ----------------------------------------------------

        if os.path.exists(
            TOKEN_FILE
        ):

            try:

                creds = (
                    Credentials
                    .from_authorized_user_file(
                        TOKEN_FILE,
                        SCOPES
                    )
                )

            except Exception:

                creds = None

        # ----------------------------------------------------
        # Refresh expired token
        # ----------------------------------------------------

        if (
            creds
            and creds.expired
            and creds.refresh_token
        ):

            print(
                "Refreshing Google OAuth token..."
            )

            creds.refresh(
                Request()
            )

            with open(
                TOKEN_FILE,
                "w",
                encoding="utf-8"
            ) as token:

                token.write(
                    creds.to_json()
                )

        # ----------------------------------------------------
        # First-time OAuth login
        # ----------------------------------------------------

        if (
            not creds
            or not creds.valid
        ):

            print()
            print("=" * 60)
            print(
                "GOOGLE DRIVE FIRST-TIME LOGIN"
            )
            print("=" * 60)

            print(
                "A browser window will open."
            )

            print(
                "Login with the Google account "
                "that owns/accesses your ROOT folder."
            )

            print("=" * 60)

            flow = (
                InstalledAppFlow
                .from_client_secrets_file(
                    CLIENT_SECRET_FILE,
                    SCOPES
                )
            )

            creds = (
                flow.run_local_server(
                    port=0,
                    access_type="offline",
                    prompt="consent"
                )
            )

            with open(
                TOKEN_FILE,
                "w",
                encoding="utf-8"
            ) as token:

                token.write(
                    creds.to_json()
                )

        # ----------------------------------------------------
        # Build Google Drive API
        # ----------------------------------------------------

        drive = build(
            "drive",
            "v3",
            credentials=creds,
            cache_discovery=False
        )

        # ----------------------------------------------------
        # Check ROOT folder
        # ----------------------------------------------------

        root_info = (
            drive.files()
            .get(
                fileId=ROOT,

                fields=(
                    "id,"
                    "name,"
                    "mimeType,"
                    "parents,"
                    "driveId"
                ),

                supportsAllDrives=True
            )
            .execute()
        )

        # ----------------------------------------------------
        # Check folder type
        # ----------------------------------------------------

        folder_mime = (
            "application/vnd.google-apps.folder"
        )

        if (
            root_info.get("mimeType")
            != folder_mime
        ):

            drive = None

            drive_error = (
                "ROOT ID is not a Google Drive folder."
            )

            print()
            print(
                "GOOGLE DRIVE ERROR:"
            )
            print(drive_error)

            return

        # ----------------------------------------------------
        # Connected
        # ----------------------------------------------------

        print()
        print("=" * 60)
        print(
            "GOOGLE DRIVE CONNECTED"
        )
        print("=" * 60)

        print(
            "ROOT NAME:",
            root_info.get("name")
        )

        print(
            "ROOT ID:",
            ROOT
        )

        print(
            "ROOT DRIVE ID:",
            root_info.get("driveId")
        )

        print(
            "ROOT PARENTS:",
            root_info.get("parents")
        )

        print("=" * 60)

    except Exception as e:

        drive = None

        drive_error = str(e)

        print()
        print("=" * 60)
        print(
            "GOOGLE DRIVE ERROR"
        )
        print("=" * 60)

        print(e)

        print("=" * 60)


# ============================================================
# REQUIRE DRIVE
# ============================================================

def require_drive():

    if not drive:

        raise RuntimeError(
            drive_error
            or
            "Google Drive is not connected."
        )


# ============================================================
# CREATE DRIVE FOLDER
# ============================================================

def create_folder(
    name,
    parent_id
):

    require_drive()

    if not parent_id:

        raise RuntimeError(
            "Google Drive parent folder ID is missing."
        )

    body = {

        "name": name,

        "mimeType":
            "application/vnd.google-apps.folder",

        "parents": [
            parent_id
        ]

    }

    result = (
        drive.files()
        .create(

            body=body,

            fields=(
                "id,"
                "name,"
                "parents,"
                "driveId"
            ),

            supportsAllDrives=True

        )
        .execute()
    )

    return result["id"]


# ============================================================
# UPLOAD PDF
# ============================================================

def upload_pdf(
    file_storage,
    parent_id
):

    require_drive()

    # --------------------------------------------------------
    # Check parent folder
    # --------------------------------------------------------

    if not parent_id:

        raise RuntimeError(
            "Chapter Google Drive folder ID is missing."
        )

    # --------------------------------------------------------
    # Filename
    # --------------------------------------------------------

    filename = secure_filename(
        file_storage.filename
    )

    if not filename:

        raise RuntimeError(
            "Invalid PDF filename."
        )

    if not filename.lower().endswith(
        ".pdf"
    ):

        raise RuntimeError(
            "Only PDF files are allowed."
        )

    # --------------------------------------------------------
    # Read file into memory
    # --------------------------------------------------------

    data = io.BytesIO(
        file_storage.read()
    )

    # --------------------------------------------------------
    # PDF media
    # --------------------------------------------------------

    media = MediaIoBaseUpload(

        data,

        mimetype="application/pdf",

        resumable=True

    )

    # --------------------------------------------------------
    # Google Drive file body
    # --------------------------------------------------------

    body = {

        "name": filename,

        "parents": [
            parent_id
        ],

        "mimeType":
            "application/pdf"

    }

    # --------------------------------------------------------
    # Upload
    # --------------------------------------------------------

    result = (
        drive.files()
        .create(

            body=body,

            media_body=media,

            fields=(
                "id,"
                "name,"
                "parents,"
                "driveId"
            ),

            supportsAllDrives=True

        )
        .execute()
    )

    return result["id"]


# ============================================================
# DOWNLOAD PDF
# ============================================================

def download_pdf(
    file_id
):

    require_drive()

    buffer = io.BytesIO()

    request_media = (
        drive.files()
        .get_media(
            fileId=file_id
        )
    )

    downloader = (
        MediaIoBaseDownload(
            buffer,
            request_media
        )
    )

    done = False

    while not done:

        status, done = (
            downloader.next_chunk()
        )

    buffer.seek(0)

    return buffer


# ============================================================
# DELETE DRIVE FILE
# ============================================================

def delete_drive_file(
    file_id
):

    if not drive:

        return

    try:

        (
            drive.files()
            .delete(
                fileId=file_id,
                supportsAllDrives=True
            )
            .execute()
        )

    except Exception as e:

        print(
            "Google Drive delete error:",
            e
        )


# ============================================================
# ACTIVITY LOG
# ============================================================

def log_activity(
    user_id,
    action,
    detail=""
):

    try:

        c = conn()

        c.execute(
            """
            INSERT INTO activity(
                user_id,
                action,
                detail
            )
            VALUES(?, ?, ?)
            """,
            (
                user_id,
                action,
                detail
            )
        )

        c.commit()

        c.close()

    except Exception as e:

        print(
            "Activity log error:",
            e
        )


# ============================================================
# ADMIN DECORATOR
# ============================================================

def admin_required(f):

    @wraps(f)
    def wrapper(
        *args,
        **kwargs
    ):

        if not session.get(
            "admin"
        ):

            return redirect(
                url_for(
                    "admin_login"
                )
            )

        return f(
            *args,
            **kwargs
        )

    return wrapper


admin = admin_required


# ============================================================
# STUDENT DECORATOR
# ============================================================

def student_required(f):

    @wraps(f)
    def wrapper(
        *args,
        **kwargs
    ):

        if not session.get(
            "student"
        ):

            return redirect(
                url_for(
                    "login"
                )
            )

        return f(
            *args,
            **kwargs
        )

    return wrapper


student = student_required


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    if session.get(
        "student"
    ):

        return redirect(
            url_for(
                "dashboard"
            )
        )

    if session.get(
        "admin"
    ):

        return redirect(
            url_for(
                "admin_dashboard"
            )
        )

    return redirect(
        url_for(
            "login"
        )
    )


# ============================================================
# STUDENT REGISTER
# ============================================================

@app.route(
    "/register",
    methods=[
        "GET",
        "POST"
    ]
)
def register():

    c = conn()

    classes = c.execute(
        """
        SELECT *
        FROM classes
        WHERE active=1
        ORDER BY name
        """
    ).fetchall()

    if request.method == "POST":

        f = request.form

        name = f.get(
            "name",
            ""
        ).strip()

        username = f.get(
            "username",
            ""
        ).strip()

        email = f.get(
            "email",
            ""
        ).strip()

        phone = f.get(
            "phone",
            ""
        ).strip()

        parent_name = f.get(
            "parent_name",
            ""
        ).strip()

        parent_phone = f.get(
            "parent_phone",
            ""
        ).strip()

        roll_no = f.get(
            "roll_no",
            ""
        ).strip()

        class_id = f.get(
            "class_id"
        )

        password = f.get(
            "password",
            ""
        )

        if (
            not name
            or not username
            or not email
            or not class_id
            or not password
        ):

            flash(
                "Required fields are missing."
            )

            c.close()

            return render_template(
                "register.html",
                classes=classes
            )

        existing = c.execute(
            """
            SELECT id
            FROM users
            WHERE username=?
               OR email=?
            """,
            (
                username,
                email
            )
        ).fetchone()

        if existing:

            flash(
                "Username or email already exists."
            )

            c.close()

            return render_template(
                "register.html",
                classes=classes
            )

        class_exists = c.execute(
            """
            SELECT id
            FROM classes
            WHERE id=?
              AND active=1
            """,
            (
                class_id,
            )
        ).fetchone()

        if not class_exists:

            flash(
                "Selected class is not available."
            )

            c.close()

            return render_template(
                "register.html",
                classes=classes
            )

        count = c.execute(
            """
            SELECT COUNT(*) AS n
            FROM users
            """
        ).fetchone()["n"]

        student_id = (
            f"STU{count + 1:05d}"
        )

        c.execute(
            """
            INSERT INTO users(
                student_id,
                name,
                username,
                email,
                phone,
                parent_name,
                parent_phone,
                roll_no,
                class_id,
                password_hash
            )
            VALUES(
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            )
            """,
            (
                student_id,
                name,
                username,
                email,
                phone,
                parent_name,
                parent_phone,
                roll_no,
                int(class_id),
                generate_password_hash(
                    password
                )
            )
        )

        c.commit()

        c.close()

        flash(
            "Registration successful. "
            f"Your Student ID is {student_id}"
        )

        return redirect(
            url_for(
                "login"
            )
        )

    c.close()

    return render_template(
        "register.html",
        classes=classes
    )


# ============================================================
# STUDENT LOGIN
# ============================================================

@app.route(
    "/login",
    methods=[
        "GET",
        "POST"
    ]
)
def login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        c = conn()

        user = c.execute(
            """
            SELECT *
            FROM users
            WHERE username=?
            """,
            (
                username,
            )
        ).fetchone()

        if (
            user
            and user["active"]
            and check_password_hash(
                user["password_hash"],
                password
            )
        ):

            session.clear()

            session[
                "student"
            ] = user["id"]

            c.execute(
                """
                UPDATE users
                SET last_login=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    user["id"],
                )
            )

            c.commit()

            c.close()

            log_activity(
                user["id"],
                "login",
                "Student login"
            )

            return redirect(
                url_for(
                    "dashboard"
                )
            )

        c.close()

        flash(
            "Invalid username/password "
            "or blocked account."
        )

    return render_template(
        "login.html"
    )


# ============================================================
# STUDENT LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for(
            "login"
        )
    )


# ============================================================
# STUDENT DASHBOARD
# ============================================================

@app.route("/dashboard")
@student
def dashboard():

    c = conn()

    user = c.execute(
        """
        SELECT
            u.*,
            c.name AS class_name
        FROM users u
        JOIN classes c
            ON c.id=u.class_id
        WHERE u.id=?
        """,
        (
            session["student"],
        )
    ).fetchone()

    if not user:

        c.close()

        session.clear()

        return redirect(
            url_for(
                "login"
            )
        )

    subjects = c.execute(
        """
        SELECT *
        FROM subjects
        WHERE class_id=?
          AND active=1
        ORDER BY name
        """,
        (
            user["class_id"],
        )
    ).fetchall()

    c.close()

    return render_template(
        "dashboard.html",
        user=user,
        subjects=subjects
    )


# ============================================================
# STUDENT SUBJECT
# ============================================================

@app.route(
    "/subject/<int:sid>"
)
@student
def subject(sid):

    c = conn()

    user = c.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (
            session["student"],
        )
    ).fetchone()

    subject_data = c.execute(
        """
        SELECT
            s.*,
            c.name AS class_name
        FROM subjects s
        JOIN classes c
            ON c.id=s.class_id
        WHERE s.id=?
          AND s.active=1
        """,
        (
            sid,
        )
    ).fetchone()

    if (
        not subject_data
        or not user
        or subject_data["class_id"]
        != user["class_id"]
    ):

        c.close()

        abort(403)

    chapters = c.execute(
        """
        SELECT *
        FROM chapters
        WHERE subject_id=?
          AND active=1
        ORDER BY name
        """,
        (
            sid,
        )
    ).fetchall()

    c.close()

    return render_template(
        "subject.html",
        user=user,
        subject=subject_data,
        chapters=chapters
    )


# ============================================================
# STUDENT CHAPTER
# ============================================================

@app.route(
    "/chapter/<int:cid>"
)
@student
def chapter(cid):

    c = conn()

    user = c.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (
            session["student"],
        )
    ).fetchone()

    chapter_data = c.execute(
        """
        SELECT
            ch.*,
            s.name AS subject_name,
            s.class_id
        FROM chapters ch
        JOIN subjects s
            ON s.id=ch.subject_id
        WHERE ch.id=?
          AND ch.active=1
        """,
        (
            cid,
        )
    ).fetchone()

    if (
        not chapter_data
        or not user
        or chapter_data["class_id"]
        != user["class_id"]
    ):

        c.close()

        abort(403)

    documents = c.execute(
        """
        SELECT *
        FROM documents
        WHERE chapter_id=?
        ORDER BY name
        """,
        (
            cid,
        )
    ).fetchall()

    c.close()

    return render_template(
        "chapter.html",
        user=user,
        data=chapter_data,
        documents=documents
    )


# ============================================================
# DOCUMENT ACCESS CHECK
# ============================================================

def get_document_for_student(
    document_id
):

    c = conn()

    user = c.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (
            session["student"],
        )
    ).fetchone()

    document = c.execute(
        """
        SELECT
            d.*,
            ch.id AS chapter_id,
            ch.name AS chapter_name,
            s.class_id,
            s.name AS subject_name
        FROM documents d
        JOIN chapters ch
            ON ch.id=d.chapter_id
        JOIN subjects s
            ON s.id=ch.subject_id
        WHERE d.id=?
        """,
        (
            document_id,
        )
    ).fetchone()

    c.close()

    if not document or not user:

        abort(404)

    if (
        document["class_id"]
        != user["class_id"]
    ):

        abort(403)

    return document, user


# ============================================================
# STUDENT VIEW PDF
# ============================================================

@app.route(
    "/document/<int:did>/view"
)
@student
def view_document(did):

    document, user = (
        get_document_for_student(
            did
        )
    )

    try:

        pdf = download_pdf(
            document[
                "drive_file_id"
            ]
        )

        log_activity(
            user["id"],
            "view_pdf",
            document["name"]
        )

        return send_file(

            pdf,

            mimetype="application/pdf",

            download_name=
                document["name"],

            as_attachment=False

        )

    except Exception as e:

        flash(
            f"Could not open PDF: {e}"
        )

        return redirect(
            url_for(
                "chapter",
                cid=document[
                    "chapter_id"
                ]
            )
        )


# ============================================================
# STUDENT DOWNLOAD PDF
# ============================================================

@app.route(
    "/document/<int:did>/download"
)
@student
def download_document(did):

    document, user = (
        get_document_for_student(
            did
        )
    )

    try:

        pdf = download_pdf(
            document[
                "drive_file_id"
            ]
        )

        log_activity(
            user["id"],
            "download_pdf",
            document["name"]
        )

        return send_file(

            pdf,

            mimetype="application/pdf",

            download_name=
                document["name"],

            as_attachment=True

        )

    except Exception as e:

        flash(
            f"Could not download PDF: {e}"
        )

        return redirect(
            url_for(
                "chapter",
                cid=document[
                    "chapter_id"
                ]
            )
        )


# ============================================================
# ADMIN LOGIN
# ============================================================

@app.route(
    "/admin/login",
    methods=[
        "GET",
        "POST"
    ]
)
def admin_login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        c = conn()

        admin_user = c.execute(
            """
            SELECT *
            FROM admins
            WHERE username=?
            """,
            (
                username,
            )
        ).fetchone()

        c.close()

        if (
            admin_user
            and check_password_hash(
                admin_user[
                    "password_hash"
                ],
                password
            )
        ):

            session.clear()

            session[
                "admin"
            ] = admin_user["id"]

            return redirect(
                url_for(
                    "admin_dashboard"
                )
            )

        flash(
            "Invalid admin username/password."
        )

    return render_template(
        "admin_login.html"
    )


# ============================================================
# ADMIN LOGOUT
# ============================================================

@app.route(
    "/admin/logout"
)
def admin_logout():

    session.clear()

    return redirect(
        url_for(
            "admin_login"
        )
    )


# ============================================================
# ADMIN DASHBOARD
# ============================================================

@app.route(
    "/admin",
    endpoint="admin_dashboard"
)
@admin
def admin_dashboard():

    c = conn()

    total = c.execute(
        """
        SELECT COUNT(*) AS n
        FROM users
        """
    ).fetchone()["n"]

    active = c.execute(
        """
        SELECT COUNT(*) AS n
        FROM users
        WHERE active=1
        """
    ).fetchone()["n"]

    classes = c.execute(
        """
        SELECT
            c.*,
            COUNT(u.id) AS students
        FROM classes c
        LEFT JOIN users u
            ON u.class_id=c.id
        GROUP BY c.id
        ORDER BY c.name
        """
    ).fetchall()

    users = c.execute(
        """
        SELECT
            u.*,
            c.name AS class_name
        FROM users u
        JOIN classes c
            ON c.id=u.class_id
        ORDER BY u.id DESC
        """
    ).fetchall()

    activities = c.execute(
        """
        SELECT
            a.*,
            u.student_id
        FROM activity a
        LEFT JOIN users u
            ON u.id=a.user_id
        ORDER BY a.id DESC
        LIMIT 50
        """
    ).fetchall()

    c.close()

    return render_template(

        "admin.html",

        total=total,

        active=active,

        blocked=total - active,

        classes=classes,

        users=users,

        activities=activities,

        drive=drive is not None,

        drive_error=drive_error,

        root=ROOT

    )


# ============================================================
# ADD CLASS
# ============================================================

@app.route(
    "/admin/class/add",
    methods=["POST"]
)
@admin
def add_class():

    name = request.form.get(
        "name",
        ""
    ).strip()

    if not name:

        flash(
            "Class name required."
        )

        return redirect(
            url_for(
                "admin_dashboard"
            )
        )

    c = conn()

    existing = c.execute(
        """
        SELECT id
        FROM classes
        WHERE name=?
        """,
        (
            name,
        )
    ).fetchone()

    if existing:

        c.close()

        flash(
            "Class already exists."
        )

        return redirect(
            url_for(
                "admin_dashboard"
            )
        )

    try:

        folder_id = create_folder(
            name,
            ROOT
        )

    except Exception as e:

        c.close()

        flash(
            f"Google Drive error: {e}"
        )

        return redirect(
            url_for(
                "admin_dashboard"
            )
        )

    c.execute(
        """
        INSERT INTO classes(
            name,
            drive_folder_id
        )
        VALUES(?, ?)
        """,
        (
            name,
            folder_id
        )
    )

    c.commit()

    c.close()

    flash(
        "Class created successfully."
    )

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# ============================================================
# TOGGLE CLASS
# ============================================================

@app.route(
    "/admin/class/<int:cid>/toggle",
    methods=["POST"]
)
@admin
def toggle_class(cid):

    c = conn()

    row = c.execute(
        """
        SELECT active
        FROM classes
        WHERE id=?
        """,
        (
            cid,
        )
    ).fetchone()

    if row:

        new_value = (
            0
            if row["active"]
            else 1
        )

        c.execute(
            """
            UPDATE classes
            SET active=?
            WHERE id=?
            """,
            (
                new_value,
                cid
            )
        )

        c.commit()

    c.close()

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# ============================================================
# ADD SUBJECT
# ============================================================

@app.route(
    "/admin/subject/add",
    methods=["POST"]
)
@admin
def add_subject():

    class_id = request.form.get(
        "class_id"
    )

    name = request.form.get(
        "name",
        ""
    ).strip()

    if not class_id or not name:

        flash(
            "Class and subject name are required."
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    c = conn()

    class_row = c.execute(
        """
        SELECT *
        FROM classes
        WHERE id=?
        """,
        (
            class_id,
        )
    ).fetchone()

    if not class_row:

        c.close()

        flash(
            "Class not found."
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    existing = c.execute(
        """
        SELECT id
        FROM subjects
        WHERE class_id=?
          AND name=?
        """,
        (
            class_id,
            name
        )
    ).fetchone()

    if existing:

        c.close()

        flash(
            "Subject already exists."
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    try:

        folder_id = create_folder(
            name,
            class_row[
                "drive_folder_id"
            ]
        )

    except Exception as e:

        c.close()

        flash(
            f"Google Drive error: {e}"
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    c.execute(
        """
        INSERT INTO subjects(
            class_id,
            name,
            drive_folder_id
        )
        VALUES(?, ?, ?)
        """,
        (
            class_id,
            name,
            folder_id
        )
    )

    c.commit()

    c.close()

    flash(
        "Subject created successfully."
    )

    return redirect(
        url_for(
            "structure"
        )
    )


# ============================================================
# STRUCTURE
# ============================================================

@app.route(
    "/admin/structure"
)
@admin
def structure():

    c = conn()

    classes = c.execute(
        """
        SELECT *
        FROM classes
        ORDER BY name
        """
    ).fetchall()

    subjects = c.execute(
        """
        SELECT
            s.*,
            c.name AS class_name
        FROM subjects s
        JOIN classes c
            ON c.id=s.class_id
        ORDER BY
            c.name,
            s.name
        """
    ).fetchall()

    chapters = c.execute(
        """
        SELECT
            ch.*,
            s.name AS subject_name,
            c.name AS class_name
        FROM chapters ch
        JOIN subjects s
            ON s.id=ch.subject_id
        JOIN classes c
            ON c.id=s.class_id
        ORDER BY
            c.name,
            s.name,
            ch.name
        """
    ).fetchall()

    c.close()

    return render_template(

        "structure.html",

        classes=classes,

        subjects=subjects,

        chapters=chapters

    )


# ============================================================
# ADD CHAPTER
# ============================================================

@app.route(
    "/admin/chapter/add",
    methods=["POST"]
)
@admin
def add_chapter():

    subject_id = request.form.get(
        "subject_id"
    )

    name = request.form.get(
        "name",
        ""
    ).strip()

    if not subject_id or not name:

        flash(
            "Subject and chapter name are required."
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    c = conn()

    subject_row = c.execute(
        """
        SELECT *
        FROM subjects
        WHERE id=?
        """,
        (
            subject_id,
        )
    ).fetchone()

    if not subject_row:

        c.close()

        flash(
            "Subject not found."
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    existing = c.execute(
        """
        SELECT id
        FROM chapters
        WHERE subject_id=?
          AND name=?
        """,
        (
            subject_id,
            name
        )
    ).fetchone()

    if existing:

        c.close()

        flash(
            "Chapter already exists."
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    try:

        folder_id = create_folder(
            name,
            subject_row[
                "drive_folder_id"
            ]
        )

    except Exception as e:

        c.close()

        flash(
            f"Google Drive error: {e}"
        )

        return redirect(
            url_for(
                "structure"
            )
        )

    c.execute(
        """
        INSERT INTO chapters(
            subject_id,
            name,
            drive_folder_id
        )
        VALUES(?, ?, ?)
        """,
        (
            subject_id,
            name,
            folder_id
        )
    )

    c.commit()

    c.close()

    flash(
        "Chapter created successfully."
    )

    return redirect(
        url_for(
            "structure"
        )
    )


# ============================================================
# MANAGE CHAPTER
# ============================================================

@app.route(
    "/admin/chapter/<int:cid>"
)
@admin
def manage_chapter(cid):

    c = conn()

    chapter_row = c.execute(
        """
        SELECT
            ch.*,
            s.name AS subject_name,
            c.name AS class_name
        FROM chapters ch
        JOIN subjects s
            ON s.id=ch.subject_id
        JOIN classes c
            ON c.id=s.class_id
        WHERE ch.id=?
        """,
        (
            cid,
        )
    ).fetchone()

    documents = c.execute(
        """
        SELECT *
        FROM documents
        WHERE chapter_id=?
        ORDER BY name
        """,
        (
            cid,
        )
    ).fetchall()

    c.close()

    if not chapter_row:

        abort(404)

    return render_template(

        "upload.html",

        chapter=chapter_row,

        documents=documents

    )


# ============================================================
# ADMIN UPLOAD PDF
# ============================================================

@app.route(
    "/admin/chapter/<int:cid>/upload",
    methods=["POST"]
)
@admin
def upload(cid):

    file_storage = request.files.get(
        "pdf"
    )

    c = conn()

    chapter_row = c.execute(
        """
        SELECT *
        FROM chapters
        WHERE id=?
        """,
        (
            cid,
        )
    ).fetchone()

    if not chapter_row:

        c.close()

        abort(404)

    if (
        not file_storage
        or not file_storage.filename
    ):

        c.close()

        flash(
            "Please select a PDF file."
        )

        return redirect(
            url_for(
                "manage_chapter",
                cid=cid
            )
        )

    filename = secure_filename(
        file_storage.filename
    )

    if not filename.lower().endswith(
        ".pdf"
    ):

        c.close()

        flash(
            "Only PDF files are allowed."
        )

        return redirect(
            url_for(
                "manage_chapter",
                cid=cid
            )
        )

    try:

        drive_file_id = upload_pdf(
            file_storage,

            chapter_row[
                "drive_folder_id"
            ]
        )

    except Exception as e:

        c.close()

        flash(
            f"PDF upload failed: {e}"
        )

        return redirect(
            url_for(
                "manage_chapter",
                cid=cid
            )
        )

    c.execute(
        """
        INSERT INTO documents(
            chapter_id,
            name,
            drive_file_id
        )
        VALUES(?, ?, ?)
        """,
        (
            cid,
            filename,
            drive_file_id
        )
    )

    c.commit()

    c.close()

    flash(
        "PDF uploaded successfully."
    )

    return redirect(
        url_for(
            "manage_chapter",
            cid=cid
        )
    )


# ============================================================
# DELETE DOCUMENT
# ============================================================

@app.route(
    "/admin/document/<int:did>/delete",
    methods=["POST"]
)
@admin
def delete_document(did):

    c = conn()

    document = c.execute(
        """
        SELECT *
        FROM documents
        WHERE id=?
        """,
        (
            did,
        )
    ).fetchone()

    if not document:

        c.close()

        abort(404)

    chapter_id = document[
        "chapter_id"
    ]

    drive_file_id = document[
        "drive_file_id"
    ]

    c.execute(
        """
        DELETE FROM documents
        WHERE id=?
        """,
        (
            did,
        )
    )

    c.commit()

    c.close()

    delete_drive_file(
        drive_file_id
    )

    flash(
        "PDF deleted successfully."
    )

    return redirect(
        url_for(
            "manage_chapter",
            cid=chapter_id
        )
    )


# ============================================================
# TOGGLE STUDENT
# ============================================================

@app.route(
    "/admin/student/<int:uid>/toggle",
    methods=["POST"]
)
@admin
def toggle_student(uid):

    c = conn()

    user = c.execute(
        """
        SELECT active
        FROM users
        WHERE id=?
        """,
        (
            uid,
        )
    ).fetchone()

    if user:

        new_value = (
            0
            if user["active"]
            else 1
        )

        c.execute(
            """
            UPDATE users
            SET active=?
            WHERE id=?
            """,
            (
                new_value,
                uid
            )
        )

        c.commit()

    c.close()

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# ============================================================
# STUDENT PROFILE
# ============================================================

@app.route(
    "/admin/student/<int:uid>"
)
@admin
def profile(uid):

    c = conn()

    user = c.execute(
        """
        SELECT
            u.*,
            c.name AS class_name
        FROM users u
        JOIN classes c
            ON c.id=u.class_id
        WHERE u.id=?
        """,
        (
            uid,
        )
    ).fetchone()

    c.close()

    if not user:

        abort(404)

    return render_template(
        "profile.html",
        user=user
    )


# ============================================================
# ERROR: FILE TOO LARGE
# ============================================================

@app.errorhandler(413)
def file_too_large(error):

    flash(
        "File is too large. "
        "Maximum size is 100 MB."
    )

    return redirect(
        request.referrer
        or
        url_for(
            "admin_dashboard"
        )
    )


# ============================================================
# ERROR: FORBIDDEN
# ============================================================

@app.errorhandler(403)
def forbidden(error):

    return """
    <h1>403 - Access Denied</h1>

    <p>
        You do not have permission
        to access this page.
    </p>

    <p>
        <a href="/">Go Home</a>
    </p>
    """, 403


# ============================================================
# ERROR: NOT FOUND
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return """
    <h1>404 - Page Not Found</h1>

    <p>
        The requested page does not exist.
    </p>

    <p>
        <a href="/">Go Home</a>
    </p>
    """, 404


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print(
        "       STUDY NOTES PORTAL"
    )
    print("=" * 60)

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    print(
        "Database: READY"
    )

    # --------------------------------------------------------
    # GOOGLE DRIVE
    # --------------------------------------------------------

    init_drive()

    if drive:

        print()
        print(
            "Google Drive: CONNECTED"
        )

        print(
            "ROOT:",
            ROOT
        )

    else:

        print()
        print(
            "Google Drive: NOT CONNECTED"
        )

        print(
            "Drive Error:",
            drive_error
        )

    print()

    print(
        "Admin Login:"
    )

    print(
        "Username: admin"
    )

    print(
        "Password: admin123"
    )

    print()

    print(
        "Student Website:"
    )

    print(
        "http://127.0.0.1:5000"
    )

    print()

    print(
        "Admin Website:"
    )

    print(
        "http://127.0.0.1:5000/admin/login"
    )

    print()

    print("=" * 60)

    app.run(

        host="0.0.0.0",

        port=5000,

        debug=True

    )