import sqlite3
from functools import wraps
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from PIL import Image
import os

if os.environ.get("RENDER"):
    pipeline = None
else:
    from transformers import pipeline


app = Flask(__name__)


@app.template_filter("ist_datetime")
def to_ist(value):
    if not value:
        return ""

    try:
        utc_time = datetime.strptime(
            value, "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        ist_time = utc_time + timedelta(hours=5, minutes=30)

        return ist_time.strftime("%d-%m-%Y %I:%M:%S %p IST")
    except (ValueError, TypeError):
        return value


app.secret_key = "swachhmitra-college-cep-secret-key"

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "swachhmitra.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

# ---------------------------------------------------------
# AI WASTE CLASSIFIER
# ---------------------------------------------------------
if os.environ.get("RENDER"):
    classifier = None
else:
    classifier = pipeline(
        "image-classification",
        model="yangy50/garbage-classification"
    )


# ---------------------------------------------------------
# AI WASTE CLASSIFIER
# ---------------------------------------------------------

WASTE_MAP = {
    # Recyclable dry materials
    "paper": "Dry Waste",
    "cardboard": "Dry Waste",
    "metal": "Dry Waste",
    "glass": "Dry Waste",
    "brown-glass": "Dry Waste",
    "green-glass": "Dry Waste",
    "white-glass": "Dry Waste",

    # Plastic
    "plastic": "Plastic Waste",

    # Organic / wet waste
    "biological": "Wet Waste",
    "organic": "Wet Waste",
    "food": "Wet Waste",
    "food-waste": "Wet Waste",

    # Batteries and electronic / hazardous items
    "battery": "E-Waste",
    "batteries": "E-Waste",
    "electronic": "E-Waste",
    "electronics": "E-Waste",
    "e-waste": "E-Waste",

    # Unsorted waste
    "trash": "Mixed Waste",
    "mixed": "Mixed Waste",
    "mixed-waste": "Mixed Waste",

    # Materials outside the main reporting categories
    "clothes": "Other",
    "shoes": "Other",
}


def classify_waste(image_path):
    """
    Returns:
        (mapped waste category, confidence percentage, original label)

    The model predicts a material class. The mapping converts that
    prediction into one of the application's reporting categories.
    """
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        predictions = waste_classifier(image)

    if not predictions:
        return "Other", 0.0, "unknown"

    best = predictions[0]

    raw_label = str(best["label"]).strip().lower()
    confidence = float(best["score"]) * 100

    # Normalize common label variations.
    normalized_label = raw_label.replace("_", "-").replace(" ", "-")

    waste_type = WASTE_MAP.get(normalized_label, "Other")

    return waste_type, confidence, raw_label
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

ADMIN_EMAIL = "admin@swachhmitra.com"
ADMIN_PASSWORD = "admin123"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            profile_image TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_code TEXT NOT NULL UNIQUE,
            user_id INTEGER,
            waste_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            address TEXT DEFAULT '',
            latitude TEXT DEFAULT '',
            longitude TEXT DEFAULT '',
            image_filename TEXT DEFAULT '',
            anonymous INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Reported',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    # Add newer columns if an older database already exists.
    user_columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(users)").fetchall()
    }

    for column, definition in [
        ("address", "TEXT DEFAULT ''"),
        ("phone", "TEXT DEFAULT ''"),
        ("profile_image", "TEXT DEFAULT ''"),
    ]:
        if column not in user_columns:
            db.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    existing_admin = db.execute(
        "SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)
    ).fetchone()

    if existing_admin is None:
        db.execute(
            """
            INSERT INTO users
            (full_name, email, password_hash, role, address, phone)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "SwachhMitra Admin",
                ADMIN_EMAIL,
                generate_password_hash(ADMIN_PASSWORD),
                "admin",
                "",
                "",
            ),
        )

    db.commit()
    db.close()


def login_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view_function(*args, **kwargs)

    return wrapper


def admin_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in as admin first.", "warning")
            return redirect(url_for("admin_login"))

        if session.get("role") != "admin":
            flash("You do not have admin access.", "danger")
            return redirect(url_for("user_dashboard"))

        return view_function(*args, **kwargs)

    return wrapper


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


@app.context_processor
def inject_current_user():
    return {
        "current_user_name": session.get("full_name"),
        "current_user_role": session.get("role"),
        "is_logged_in": "user_id" in session,
    }


# ---------------------------------------------------------
# LANGUAGE SUPPORT
# Marathi = primary/default
# ---------------------------------------------------------

SUPPORTED_LANGUAGES = {
    "mr": "मराठी",
    "en": "English",
    "hi": "हिंदी",
}
TRANSLATIONS = {
    "mr": {"SwachhMitra": "स्वच्छमित्र",
        "home_description": "सामाजिक कचरा व्यवस्थापनासाठी एक सोपे वेब अप्लिकेशन.",
"home_features": "या आवृत्तीमध्ये वापरकर्ता नोंदणी, लॉगिन आणि डॅशबोर्डचा समावेश आहे.",
"create_account": "खाते तयार करा",
"user_login": "वापरकर्ता लॉगिन",
"go_to_admin_dashboard": "प्रशासक डॅशबोर्डवर जा",
"go_to_dashboard": "डॅशबोर्डवर जा",
"email": "ईमेल",
"password": "पासवर्ड",
"new_user": "नवीन वापरकर्ता?",
"register_here": "येथे नोंदणी करा",
"already_account": "आधीच खाते आहे?",
"log_in": "लॉगिन करा",
"user_registration": "वापरकर्ता नोंदणी",
"confirm_password": "पासवर्डची पुष्टी करा",
"report_garbage_help": "कचऱ्याची नोंद करून आपला परिसर स्वच्छ ठेवण्यास मदत करा.",
"select_waste_type": "कचऱ्याचा प्रकार निवडा",
"ai_detection_note": "फोटो अपलोड करत असल्यास 'कचऱ्याचा प्रकार निवडा' तसेच ठेवा — AI तो ओळखेल.",
"garbage_image": "कचऱ्याचा फोटो",
"upload_clear_photo": "कचऱ्याचा स्पष्ट फोटो अपलोड करा.",
"maximum_size": "कमाल आकार: 5 MB.",
"description": "वर्णन",
"description_placeholder": "कचऱ्याच्या समस्येचे वर्णन करा...",
"location_locality": "ठिकाण / परिसर",
"location_example": "उदाहरण: पिंपरी, पुणे",
"latitude": "अक्षांश",
"longitude": "रेखांश",
"optional": "पर्यायी",
"anonymous_report": "हा अहवाल निनावी स्वरूपात सादर करा",
"submit_garbage_report": "कचरा अहवाल सादर करा",
"back_to_dashboard": "← डॅशबोर्डवर परत जा",
"full_name": "पूर्ण नाव",
"email_cannot_change": "ईमेल बदलता येणार नाही.",
"phone_number": "फोन नंबर",
"enter_phone": "आपला फोन नंबर प्रविष्ट करा",
"address_locality": "पत्ता / परिसर",
"enter_address": "आपला परिसर, ठिकाण किंवा पत्ता प्रविष्ट करा",
"save_profile": "प्रोफाइल जतन करा",
"logout": "लॉगआउट",
        "admin_dashboard": "प्रशासक डॅशबोर्ड",
"admin_welcome": "स्वागत आहे",
"manage_reports": "अहवाल व्यवस्थापित करा आणि स्वच्छता प्रगतीवर लक्ष ठेवा.",
"registered_users": "नोंदणीकृत वापरकर्ते",
"total_reports": "एकूण अहवाल",
"awaiting_action": "कृतीची प्रतीक्षा",
"cleaned_reports": "स्वच्छ केलेले अहवाल",
"garbage_report_management": "कचरा अहवाल व्यवस्थापन",
"reporter": "अहवालकर्ता",
"image": "फोटो",
"current_status": "सध्याची स्थिती",
"update_status": "स्थिती अपडेट करा",
"view_image": "फोटो पहा",
"no_image": "फोटो उपलब्ध नाही",
"anonymous": "निनावी",
"unknown_user": "अज्ञात वापरकर्ता",
"save_status": "स्थिती जतन करा",
"new_report_status": "नवीन अहवालाची स्थिती",
"no_garbage_reports": "अद्याप कोणतेही कचरा अहवाल नाहीत",
"submitted_reports_here": "सादर केलेले अहवाल येथे दिसतील.",
"admin_login": "प्रशासक लॉगिन",
"admin_email": "प्रशासक ईमेल",
"default_admin_account": "या प्रकल्पासाठी डीफॉल्ट प्रशासक खाते वापरा.",
"password": "पासवर्ड",
        "home": "मुख्यपृष्ठ",
        "dashboard": "डॅशबोर्ड",
        "report_garbage": "कचरा नोंदवा",
        "my_profile": "माझे प्रोफाइल",
        "logout": "लॉगआउट",
        "login": "लॉगिन",
        "register": "नोंदणी",
        "welcome": "स्वच्छमित्रमध्ये आपले स्वागत आहे",
        "garbage_reports": "माझे कचरा अहवाल",
        "report": "अहवाल",
        "status": "स्थिती",
        "date": "दिनांक",
        "action": "कृती",
        "submit": "सबमिट करा",
        "name": "नाव",
        "email": "ईमेल",
        "phone": "फोन",
        "address": "पत्ता",
        "save": "जतन करा",
        "admin": "प्रशासक",
        "users": "वापरकर्ते",
        "reports": "अहवाल",
        "waste_type": "कचऱ्याचा प्रकार",
        "dry_waste": "सुका कचरा",
        "wet_waste": "ओला कचरा",
        "plastic_waste": "प्लास्टिक कचरा",
        "e_waste": "ई-कचरा",
        "construction": "बांधकाम कचरा",
        "medical": "वैद्यकीय कचरा",
        "mixed_waste": "मिश्र कचरा",
        "other": "इतर",
        "upload_image": "कचऱ्याचा फोटो अपलोड करा",
        "report_description": "फोटो अपलोड करून आणि ठिकाणाची माहिती देऊन आपल्या परिसरातील कचऱ्याची नोंद करा.",
"profile_description": "आपले नाव, फोन नंबर आणि परिसराची माहिती अपडेट करा.",
"view_profile": "प्रोफाइल पहा",
"report_id": "अहवाल क्रमांक",
"location": "ठिकाण",
"not_provided": "माहिती दिलेली नाही",
"reported": "नोंदवले",
"verified": "पडताळले",
"assigned": "नियुक्त केले",
"cleaning_in_progress": "स्वच्छता सुरू आहे",
"cleaned": "स्वच्छ केले",
"closed": "बंद केले",
"no_reports": "आपण अद्याप कोणताही कचरा अहवाल सादर केलेला नाही.",
"first_report": "पहिला अहवाल सादर करा",
    },

    "en": {"SwachhMitra": "SwachhMitra","home_description": "A simple web application for social waste management.",
"home_features": "This version includes user registration, login, and dashboards.",
"create_account": "Create Account",
"user_login": "User Login",
"go_to_admin_dashboard": "Go to Admin Dashboard",
"go_to_dashboard": "Go to Dashboard",
"email": "Email",
"password": "Password",
"new_user": "New user?",
"register_here": "Register here",
"already_account": "Already have an account?",
"log_in": "Log in",
"user_registration": "User Registration",
"confirm_password": "Confirm Password",
"report_garbage_help": "Help keep your locality clean by reporting garbage.",
"select_waste_type": "Select waste type",
"ai_detection_note": "Leave as \"Select waste type\" if you're uploading a photo — AI will detect it.",
"garbage_image": "Garbage Image",
"upload_clear_photo": "Upload a clear photo of the garbage.",
"maximum_size": "Maximum size: 5 MB.",
"description": "Description",
"description_placeholder": "Describe the garbage problem...",
"location_locality": "Location / Locality",
"location_example": "Example: Pimpri, Pune",
"latitude": "Latitude",
"longitude": "Longitude",
"optional": "Optional",
"anonymous_report": "Submit this report anonymously",
"submit_garbage_report": "Submit Garbage Report",
"back_to_dashboard": "← Back to Dashboard",
"full_name": "Full Name",
"email_cannot_change": "Email cannot be changed.",
"phone_number": "Phone Number",
"enter_phone": "Enter your phone number",
"address_locality": "Address / Locality",
"enter_address": "Enter your area, locality or address",
"save_profile": "Save Profile",
        "admin_dashboard": "Admin Dashboard",
"admin_welcome": "Welcome",
"manage_reports": "Manage reports and monitor cleanup progress.",
"registered_users": "Registered Users",
"total_reports": "Total Reports",
"awaiting_action": "Awaiting Action",
"cleaned_reports": "Cleaned Reports",
"garbage_report_management": "Garbage Report Management",
"reporter": "Reporter",
"image": "Image",
"current_status": "Current Status",
"update_status": "Update Status",
"view_image": "View Image",
"no_image": "No image",
"anonymous": "Anonymous",
"unknown_user": "Unknown user",
"save_status": "Save Status",
"new_report_status": "New report status",
"no_garbage_reports": "No garbage reports yet",
"submitted_reports_here": "Submitted reports will appear here.",
"admin_login": "Admin Login",
"admin_email": "Admin Email",
"default_admin_account": "Use the default admin account for this project.",
"password": "Password",
        "home": "Home",
        "dashboard": "Dashboard",
        "report_garbage": "Report Garbage",
        "my_profile": "My Profile",
        "logout": "Logout",
        "login": "Login",
        "register": "Register",
        "welcome": "Welcome to SwachhMitra",
        "garbage_reports": "My Garbage Reports",
        "report": "Report",
        "status": "Status",
        "date": "Date",
        "action": "Action",
        "submit": "Submit",
        "name": "Name",
        "email": "Email",
        "phone": "Phone",
        "address": "Address",
        "save": "Save",
        "admin": "Admin",
        "users": "Users",
        "reports": "Reports",
        "waste_type": "Waste Type",
        "dry_waste": "Dry Waste",
        "wet_waste": "Wet Waste",
        "plastic_waste": "Plastic Waste",
        "e_waste": "E-Waste",
        "construction": "Construction Waste",
        "medical": "Medical Waste",
        "mixed_waste": "Mixed Waste",
        "other": "Other",
        "upload_image": "Upload Waste Image","report_description": "Report garbage in your locality by uploading a photo and providing its location.",
"profile_description": "Update your name, phone number and locality.",
"view_profile": "View Profile",
"report_id": "Report ID",
"location": "Location",
"status": "Status",
"not_provided": "Not provided",
"reported": "Reported",
"verified": "Verified",
"assigned": "Assigned",
"cleaning_in_progress": "Cleaning in Progress",
"cleaned": "Cleaned",
"closed": "Closed",
"no_reports": "You haven't submitted any garbage reports yet.",
"first_report": "Submit Your First Report",
    },

    "hi": {
   
   "SwachhMitra": "स्वच्छमित्र 🧹","home_description": "सामाजिक कचरा प्रबंधन के लिए एक सरल वेब एप्लिकेशन।",
"home_features": "इस संस्करण में उपयोगकर्ता पंजीकरण, लॉगिन और डैशबोर्ड शामिल हैं।",
"create_account": "खाता बनाएं",
"user_login": "उपयोगकर्ता लॉगिन",
"go_to_admin_dashboard": "प्रशासक डैशबोर्ड पर जाएं",
"go_to_dashboard": "डैशबोर्ड पर जाएं",
"email": "ईमेल",
"password": "पासवर्ड",
"new_user": "नए उपयोगकर्ता हैं?",
"register_here": "यहां पंजीकरण करें",
"already_account": "क्या आपके पास पहले से खाता है?",
"log_in": "लॉगिन करें",
"user_registration": "उपयोगकर्ता पंजीकरण",
"confirm_password": "पासवर्ड की पुष्टि करें",
"report_garbage_help": "कचरे की रिपोर्ट करके अपने क्षेत्र को स्वच्छ रखने में मदद करें।",
"select_waste_type": "कचरे का प्रकार चुनें",
"ai_detection_note": "यदि आप फोटो अपलोड कर रहे हैं तो 'कचरे का प्रकार चुनें' ही रहने दें — AI इसे पहचान लेगा।",
"garbage_image": "कचरे का फोटो",
"upload_clear_photo": "कचरे का स्पष्ट फोटो अपलोड करें।",
"maximum_size": "अधिकतम आकार: 5 MB।",
"description": "विवरण",
"description_placeholder": "कचरे की समस्या का वर्णन करें...",
"location_locality": "स्थान / क्षेत्र",
"location_example": "उदाहरण: पिंपरी, पुणे",
"latitude": "अक्षांश",
"longitude": "देशांतर",
"optional": "वैकल्पिक",
"anonymous_report": "इस रिपोर्ट को गुमनाम रूप से जमा करें",
"submit_garbage_report": "कचरा रिपोर्ट जमा करें",
"back_to_dashboard": "← डैशबोर्ड पर वापस जाएं",
"full_name": "पूरा नाम",
"email_cannot_change": "ईमेल बदला नहीं जा सकता।",
"phone_number": "फोन नंबर",
"enter_phone": "अपना फोन नंबर दर्ज करें",
"address_locality": "अपना पता / क्षेत्र",
"enter_address": "अपना क्षेत्र, स्थान या पता दर्ज करें",
"save_profile": "प्रोफ़ाइल save करें ?",
        "admin_dashboard": "प्रशासक डैशबोर्ड",
"admin_welcome": "स्वागत है",
"manage_reports": "रिपोर्ट प्रबंधित करें और सफाई की प्रगति पर नज़र रखें।",
"registered_users": "पंजीकृत उपयोगकर्ता",
"total_reports": "कुल रिपोर्ट",
"awaiting_action": "कार्रवाई की प्रतीक्षा",
"cleaned_reports": "साफ की गई रिपोर्ट",
"garbage_report_management": "कचरा रिपोर्ट प्रबंधन",
"reporter": "रिपोर्टकर्ता",
"image": "फोटो",
"current_status": "वर्तमान स्थिति",
"update_status": "स्थिति अपडेट करें",
"view_image": "फोटो देखें",
"no_image": "कोई फोटो नहीं",
"anonymous": "गुमनाम",
"unknown_user": "अज्ञात उपयोगकर्ता",
"save_status": "स्थिति सहेजें",
"new_report_status": "नई रिपोर्ट की स्थिति",
"no_garbage_reports": "अभी तक कोई कचरा रिपोर्ट नहीं है",
"submitted_reports_here": "जमा की गई रिपोर्ट यहां दिखाई देंगी।",
"admin_login": "प्रशासक लॉगिन",
"admin_email": "प्रशासक ईमेल",
"default_admin_account": "इस प्रोजेक्ट के लिए डिफ़ॉल्ट प्रशासक खाते का उपयोग करें।",
"password": "पासवर्ड",
        "report_description": "फोटो अपलोड करके और स्थान की जानकारी देकर अपने क्षेत्र के कचरे की रिपोर्ट करें।",
"profile_description": "अपना नाम, फोन नंबर और क्षेत्र की जानकारी अपडेट करें।",
"view_profile": "प्रोफ़ाइल देखें",
"report_id": "रिपोर्ट क्रमांक",
"location": "स्थान",
"status": "स्थिति",
"not_provided": "जानकारी उपलब्ध नहीं है",
"reported": "रिपोर्ट किया गया",
"verified": "सत्यापित",
"assigned": "नियुक्त किया गया",
"cleaning_in_progress": "सफाई जारी है",
"cleaned": "साफ किया गया",
"closed": "बंद किया गया",
"no_reports": "आपने अभी तक कोई कचरा रिपोर्ट जमा नहीं की है।",
"first_report": "पहली रिपोर्ट जमा करें",
        "home": "होम",
        "dashboard": "डैशबोर्ड",
        "report_garbage": "कचरे की रिपोर्ट करें",
        "my_profile": "मेरी प्रोफ़ाइल",
        "logout": "लॉगआउट",
        "login": "लॉगिन",
        "register": "पंजीकरण",
        "welcome": "स्वच्छमित्र में आपका स्वागत है",
        "garbage_reports": "मेरी कचरा रिपोर्ट",
        "report": "रिपोर्ट",
        "status": "स्थिति",
        "date": "दिनांक",
        "action": "कार्रवाई",
        "submit": "सबमिट करें",
        "name": "नाम",
        "email": "ईमेल",
        "phone": "फोन",
        "address": "पता",
        "save": "सहेजें",
        "admin": "प्रशासक",
        "users": "उपयोगकर्ता",
        "reports": "रिपोर्ट",
        "waste_type": "कचरे का प्रकार",
        "dry_waste": "सूखा कचरा",
        "wet_waste": "गीला कचरा",
        "plastic_waste": "प्लास्टिक कचरा",
        "e_waste": "ई-कचरा",
        "construction": "निर्माण कचरा",
        "medical": "चिकित्सा कचरा",
        "mixed_waste": "मिश्रित कचरा",
        "other": "अन्य",
        "upload_image": "कचरे की फोटो अपलोड करें",
    }
}





@app.route("/set-language/<language>")
def set_language(language):
    if language not in SUPPORTED_LANGUAGES:
        language = "mr"

    session["language"] = language

    return redirect(request.referrer or url_for("home"))

# ---------------------------------------------------------
# LANGUAGE SUPPORT
# Marathi = primary/default
# ---------------------------------------------------------



@app.context_processor
def inject_language():
    return {
        "current_language": session.get("language", "mr"),
        "supported_languages": SUPPORTED_LANGUAGES,
    }

@app.context_processor
def inject_translations():
    language = session.get("language", "mr")

    return {
        "t": TRANSLATIONS.get(
            language,
            TRANSLATIONS["mr"]
        )
    }

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("user_dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not full_name or not email or not password:
            flash("Please fill in all fields.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        db = get_db()

        existing_user = db.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()

        if existing_user:
            flash("This email is already registered. Please log in.", "warning")
            return redirect(url_for("login"))

        db.execute(
            """
            INSERT INTO users
            (full_name, email, password_hash, role)
            VALUES (?, ?, ?, ?)
            """,
            (
                full_name,
                email,
                generate_password_hash(password),
                "user",
            ),
        )

        db.commit()

        flash("Registration successful. You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))

        return redirect(url_for("user_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()

        user = db.execute(
            "SELECT * FROM users WHERE email = ? AND role = 'user'",
            (email,),
        ).fetchone()

        if user is None or not check_password_hash(
            user["password_hash"], password
        ):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        session.clear()

        session["user_id"] = user["id"]
        session["full_name"] = user["full_name"]
        session["email"] = user["email"]
        session["role"] = user["role"]

        flash("Welcome back to SwachhMitra!", "success")

        return redirect(url_for("user_dashboard"))

    return render_template("login.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if "user_id" in session and session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()

        admin = db.execute(
            "SELECT * FROM users WHERE email = ? AND role = 'admin'",
            (email,),
        ).fetchone()

        if admin is None or not check_password_hash(
            admin["password_hash"], password
        ):
            flash("Invalid admin email or password.", "danger")
            return render_template("admin_login.html")

        session.clear()

        session["user_id"] = admin["id"]
        session["full_name"] = admin["full_name"]
        session["email"] = admin["email"]
        session["role"] = admin["role"]

        flash("Admin login successful.", "success")

        return redirect(url_for("admin_dashboard"))

    return render_template("admin_login.html")


@app.route("/dashboard")
@login_required
def user_dashboard():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))

    db = get_db()

    user = db.execute(
        "SELECT * FROM users WHERE id = ?",
        (session["user_id"],),
    ).fetchone()

    reports = db.execute(
        """
        SELECT *
        FROM reports
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (session["user_id"],),
    ).fetchall()

    return render_template(
        "user_dashboard.html",
        user=user,
        reports=reports,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))

    db = get_db()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()

        if not full_name:
            flash("Name cannot be empty.", "danger")
            return redirect(url_for("profile"))

        db.execute(
            """
            UPDATE users
            SET full_name = ?, address = ?, phone = ?
            WHERE id = ?
            """,
            (
                full_name,
                address,
                phone,
                session["user_id"],
            ),
        )

        db.commit()

        session["full_name"] = full_name

        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))

    user = db.execute(
        "SELECT * FROM users WHERE id = ?",
        (session["user_id"],),
    ).fetchone()

    return render_template("profile.html", user=user)



@app.route("/report", methods=["GET", "POST"])
@login_required
def report_waste():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))

    if request.method != "POST":
        return render_template("report.html")

    waste_type = request.form.get("waste_type", "").strip()
    description = request.form.get("description", "").strip()
    address = request.form.get("address", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()
    anonymous = 1 if request.form.get("anonymous") else 0

    image = request.files.get("image")
    image_filename = ""
    ai_confidence = None
    ai_label = None

    # Preserve the category selected by the user.
    selected_waste_type = waste_type

    # Process an optional image.
    if image and image.filename:
        if not allowed_file(image.filename):
            flash(
                "Only PNG, JPG, JPEG and WEBP images are allowed.",
                "danger",
            )
            return render_template("report.html")

        extension = image.filename.rsplit(".", 1)[1].lower()
        image_filename = f"{uuid4().hex}.{extension}"
        safe_name = secure_filename(image_filename)
        image_path = UPLOAD_FOLDER / safe_name

        try:
            image.stream.seek(0)
            with Image.open(image.stream) as uploaded_image:
                uploaded_image.verify()

            image.stream.seek(0)
            image.save(image_path)

        except Exception:
            flash(
                "The uploaded file is not a valid image. "
                "Please upload a valid JPG, PNG or WEBP image.",
                "danger",
            )
            return render_template("report.html")

        try:
            detected_type, ai_confidence, ai_label = classify_waste(
                image_path
            )

            # Use AI when the user did not choose a category.
            if not selected_waste_type:
                if ai_confidence >= 35:
                    waste_type = detected_type
                else:
                    waste_type = "Other"
                    flash(
                        "AI confidence is low. Please review "
                        "the image and verify its category.",
                        "warning",
                    )
            else:
                # Respect an explicit user selection.
                waste_type = selected_waste_type

        except Exception as exc:
            print("AI classification failed:", exc)
            ai_confidence = None
            ai_label = None
            waste_type = selected_waste_type

    # A category is required if AI did not determine one.
    if not waste_type:
        flash(
            "Please select a waste category or upload a photo "
            "for AI classification.",
            "danger",
        )
        return render_template("report.html")

    # Create a unique report code.
    report_code = "SM-" + uuid4().hex[:8].upper()

    # Save the report to the database.
    db = get_db()
    db.execute(
        """
        INSERT INTO reports (
            report_code,
            user_id,
            waste_type,
            description,
            address,
            latitude,
            longitude,
            image_filename,
            anonymous,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_code,
            session["user_id"],
            waste_type,
            description,
            address,
            latitude,
            longitude,
            image_filename,
            anonymous,
            "Reported",
        ),
    )
    db.commit()

    # Tell the user what happened.
    if ai_confidence is not None and not selected_waste_type:
        flash(
            f"Waste report {report_code} submitted successfully! "
            f"AI detected: {waste_type} "
            f"(model label: {ai_label}, "
            f"{ai_confidence:.1f}% confidence).",
            "success",
        )
    else:
        flash(
            f"Waste report {report_code} submitted successfully!",
            "success",
        )

    return redirect(url_for("user_dashboard"))
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    db = get_db()

    total_users = db.execute(
        "SELECT COUNT(*) AS total FROM users WHERE role = 'user'"
    ).fetchone()["total"]

    total_reports = db.execute(
        "SELECT COUNT(*) AS total FROM reports"
    ).fetchone()["total"]

    reported = db.execute(
        "SELECT COUNT(*) AS total FROM reports WHERE status = 'Reported'"
    ).fetchone()["total"]

    cleaned = db.execute(
        "SELECT COUNT(*) AS total FROM reports WHERE status = 'Cleaned'"
    ).fetchone()["total"]

    reports = db.execute(
        """
        SELECT reports.*, users.full_name, users.email
        FROM reports
        LEFT JOIN users ON reports.user_id = users.id
        ORDER BY reports.id DESC
        """
    ).fetchall()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_reports=total_reports,
        reported=reported,
        cleaned=cleaned,
        reports=reports,
    )
@app.route("/certificate")
def certificate():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    conn.close()

    if not user:
        return redirect(url_for("login"))

    return render_template(
        "certificate.html",
        user=user,
        current_date=datetime.now().strftime("%d %B %Y")
    )
@app.route(
    "/admin/report/<int:report_id>/status",
    methods=["POST"],
)
@admin_required
def update_report_status(report_id):
    allowed_statuses = {
        "Reported",
        "Verified",
        "Assigned",
        "Cleaning in Progress",
        "Cleaned",
        "Closed",
    }

    new_status = request.form.get("status", "").strip()

    if new_status not in allowed_statuses:
        flash("Invalid report status.", "danger")
        return redirect(url_for("admin_dashboard"))

    db = get_db()

    report = db.execute(
        "SELECT id FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()

    if report is None:
        flash("Report not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    db.execute(
        "UPDATE reports SET status = ? WHERE id = ?",
        (new_status, report_id),
    )
    db.commit()

    flash(
        f"Report #{report_id} status updated to {new_status}.",
        "success",
    )

    return redirect(url_for("admin_dashboard"))

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
