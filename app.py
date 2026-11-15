
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import sqlite3
import csv
import io
from flask import make_response
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-demo-only")


# DATABASE CONNECTION
def get_db():
    connection = sqlite3.connect("astra.db")
    connection.row_factory = sqlite3.Row
    return connection


# CREATE AND UPGRADE DATABASE
def create_database():
    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            astronaut_id TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS health_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            heart_rate INTEGER,
            systolic INTEGER,
            diastolic INTEGER,
            temperature REAL,
            sleep REAL,
            mood TEXT,
            symptoms TEXT,
            recorded_at TEXT
        )
    """)

    # Add user_id to an existing health_records table
    columns = [
        row["name"] for row in
        connection.execute("PRAGMA table_info(health_records)").fetchall()
    ]

    if "user_id" not in columns:
        connection.execute(
            "ALTER TABLE health_records ADD COLUMN user_id INTEGER"
        )

    # New mission profile table, with one profile per astronaut
    connection.execute("""
        CREATE TABLE IF NOT EXISTS mission_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            mission_name TEXT,
            astronaut_id TEXT,
            mission_day INTEGER,
            mission_duration INTEGER,
            spacecraft TEXT
        )
    """)

    # Keep the old mission_profile table untouched for migration
    connection.execute("""
        CREATE TABLE IF NOT EXISTS mission_profile (
            id INTEGER PRIMARY KEY,
            mission_name TEXT,
            astronaut_id TEXT,
            mission_day INTEGER,
            mission_duration INTEGER,
            spacecraft TEXT
        )
    """)

    connection.execute("""
        INSERT OR IGNORE INTO mission_profile
        (id, mission_name, astronaut_id, mission_day,
         mission_duration, spacecraft)
        VALUES (1, 'ASTRA-01', 'CREW-001', 1, 180, 'ISS')
    """)

    connection.commit()
    connection.close()


# LOGIN PROTECTION
def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access your dashboard.")
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


# SIGN UP
@app.route("/signup", methods=["GET", "POST"])
def signup():

    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        astronaut_id = request.form.get("astronaut_id", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not full_name or not astronaut_id or not email or not password:
            flash("Please complete all fields.")
            return redirect(url_for("signup"))

        if len(password) < 8:
            flash("Your password must be at least 8 characters.")
            return redirect(url_for("signup"))

        connection = get_db()

        existing_count = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        try:
            cursor = connection.execute("""
                INSERT INTO users
                (full_name, astronaut_id, email, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                full_name,
                astronaut_id,
                email,
                generate_password_hash(password),
                datetime.now().isoformat(timespec="seconds")
            ))

            new_user_id = cursor.lastrowid

            # Give the first astronaut the existing test records
            if existing_count == 0:
                connection.execute("""
                    UPDATE health_records
                    SET user_id = ?
                    WHERE user_id IS NULL
                """, (new_user_id,))

                old_profile = connection.execute("""
                    SELECT mission_name, astronaut_id, mission_day,
                           mission_duration, spacecraft
                    FROM mission_profile
                    WHERE id = 1
                """).fetchone()

                if old_profile:
                    profile_values = tuple(old_profile)
                else:
                    profile_values = (
                        "ASTRA-01", astronaut_id, 1, 180, "ISS"
                    )

                connection.execute("""
                    INSERT INTO mission_profiles
                    (user_id, mission_name, astronaut_id, mission_day,
                     mission_duration, spacecraft)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (new_user_id, *profile_values))

            else:
                connection.execute("""
                    INSERT INTO mission_profiles
                    (user_id, mission_name, astronaut_id, mission_day,
                     mission_duration, spacecraft)
                    VALUES (?, ?, ?, 1, 180, 'ISS')
                """, (
                    new_user_id,
                    "ASTRA-01",
                    astronaut_id
                ))

            connection.commit()
            connection.close()

            flash("Account created successfully! Please log in.")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            connection.rollback()
            connection.close()
            flash("That email or astronaut ID is already registered.")
            return redirect(url_for("signup"))

    return render_template("signup.html")


# LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():

    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        connection = get_db()
        user = connection.execute("""
            SELECT id, full_name, password_hash
            FROM users
            WHERE email = ?
        """, (email,)).fetchone()
        connection.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            return redirect(url_for("home"))

        flash("Incorrect email or password.")

    return render_template("login.html")


# LOGOUT
@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


# PERSONAL DASHBOARD

@app.route("/")
@login_required
def home():
    user_id = session["user_id"]
    connection = get_db()

    latest = connection.execute("""
        SELECT heart_rate, systolic, diastolic,
               temperature, sleep, mood, symptoms
        FROM health_records
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,)).fetchone()

    previous = connection.execute("""
        SELECT heart_rate, systolic, diastolic,
               temperature, sleep
        FROM health_records
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1 OFFSET 1
    """, (user_id,)).fetchone()

    records = connection.execute("""
        SELECT recorded_at, heart_rate, sleep,
       systolic, diastolic, temperature
FROM health_records
WHERE user_id = ?
ORDER BY id ASC
LIMIT 30
        
    """, (user_id,)).fetchall()

    records = [tuple(record) for record in records]

    mission = connection.execute("""
        SELECT mission_name, astronaut_id, mission_day,
               mission_duration, spacecraft
        FROM mission_profiles
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    connection.close()

    alerts = []

    if latest:
        if latest["symptoms"] and latest["symptoms"].strip():
            alerts.append(
                "Symptoms or notes were reported. Review this entry using the mission medical protocol."
            )
        else:
            alerts.append("No symptoms reported in the latest check-in.")
    else:
        alerts.append(
            "No health check-in recorded yet. Complete your first check-in."
        )

    trends = []

    if latest and previous:
        measurements = [
            ("Heart Rate", latest["heart_rate"], previous["heart_rate"], "BPM"),
            ("Systolic BP", latest["systolic"], previous["systolic"], "mmHg"),
            ("Diastolic BP", latest["diastolic"], previous["diastolic"], "mmHg"),
            ("Temperature", latest["temperature"], previous["temperature"], "°C"),
            ("Sleep", latest["sleep"], previous["sleep"], "hours")
        ]

        for name, current, old, unit in measurements:
            difference = float(current) - float(old)
            change = f"+{difference:.1f}" if difference > 0 else f"{difference:.1f}"
            trends.append((name, change, unit))

        # DAILY WELLNESS INTELLIGENCE

    wellness_summary = []

    if not latest:
        wellness_summary.append(
            "No check-in available. Complete a health check-in to generate your wellness summary."
        )

    else:
        wellness_summary.append(
            f"You reported {latest['sleep']} hours of sleep."
        )

        wellness_summary.append(
            f"Your recorded mood was: {latest['mood']}."
        )

        if latest["symptoms"] and latest["symptoms"].strip():
            wellness_summary.append(
                "You entered symptoms or notes. Review them with the mission medical team."
            )
        else:
            wellness_summary.append(
                "No symptoms or additional notes were entered."
            )
    return render_template(
    "index.html",
    latest=latest,
    records=records,
    alerts=alerts,
    mission=mission,
    trends=trends,
    full_name=session["full_name"],
    wellness_summary=wellness_summary
)



# SAVE HEALTH CHECK-IN
@app.route("/checkin", methods=["POST"])
@login_required
def checkin():

    heart_rate = request.form.get("heart_rate", type=int)
    systolic = request.form.get("systolic", type=int)
    diastolic = request.form.get("diastolic", type=int)
    temperature = request.form.get("temperature", type=float)
    sleep = request.form.get("sleep", type=float)
    mood = request.form.get("mood")
    symptoms = request.form.get("symptoms", "")

    if (heart_rate is None or systolic is None or
        diastolic is None or temperature is None or
        sleep is None or not mood or
        not 0 <= sleep <= 24):
        flash("Please check your entries and try again.")
        return redirect(url_for("home"))

    connection = get_db()

    connection.execute("""
        INSERT INTO health_records
        (heart_rate, systolic, diastolic, temperature,
         sleep, mood, symptoms, recorded_at, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        heart_rate, systolic, diastolic, temperature,
        sleep, mood, symptoms,
        datetime.now().isoformat(timespec="seconds"),
        session["user_id"]
    ))

    connection.commit()
    connection.close()

    flash("Health record saved successfully!")
    return redirect(url_for("home"))


# UPDATE PERSONAL MISSION PROFILE
@app.route("/mission-profile", methods=["POST"])
@login_required
def mission_profile():

    mission_name = request.form.get("mission_name")
    astronaut_id = request.form.get("astronaut_id")
    mission_day = request.form.get("mission_day", type=int)
    mission_duration = request.form.get("mission_duration", type=int)
    spacecraft = request.form.get("spacecraft")

    if (not mission_name or not astronaut_id or
        not spacecraft or mission_day is None or
        mission_duration is None or
        mission_day < 1 or mission_duration < 1 or
        mission_day > mission_duration):

        flash("Please check your mission profile entries.")
        return redirect(url_for("home"))

    connection = get_db()

    connection.execute("""
        UPDATE mission_profiles
        SET mission_name = ?,
            astronaut_id = ?,
            mission_day = ?,
            mission_duration = ?,
            spacecraft = ?
        WHERE user_id = ?
    """, (
        mission_name, astronaut_id, mission_day,
        mission_duration, spacecraft, session["user_id"]
    ))

    connection.commit()
    connection.close()

    flash("Mission profile updated successfully!")
    return redirect(url_for("home"))


# PERSONAL FLIGHT HEALTH REPORT
@app.route("/report")
@login_required
def report():

    connection = get_db()

    mission = connection.execute("""
        SELECT mission_name, astronaut_id, mission_day,
               mission_duration, spacecraft
        FROM mission_profiles
        WHERE user_id = ?
    """, (session["user_id"],)).fetchone()

    records = connection.execute("""
        SELECT heart_rate, systolic, diastolic,
               temperature, sleep, mood, symptoms, recorded_at
        FROM health_records
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 30
    """, (session["user_id"],)).fetchall()

    connection.close()

    return render_template(
        "report.html",
        mission=mission,
        records=records
    )

# DOWNLOAD PERSONAL HEALTH DATA
@app.route("/download-data")
@login_required
def download_data():

    connection = get_db()

    records = connection.execute("""
        SELECT heart_rate, systolic, diastolic,
               temperature, sleep, mood, symptoms, recorded_at
        FROM health_records
        WHERE user_id = ?
        ORDER BY id DESC
    """, (session["user_id"],)).fetchall()

    connection.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Heart Rate", "Systolic BP", "Diastolic BP",
        "Temperature", "Sleep", "Mood", "Symptoms", "Date"
    ])

    for record in records:
        writer.writerow(tuple(record))

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = (
        "attachment; filename=astra_health_records.csv"
    )
    response.headers["Content-Type"] = "text/csv"

    return response
create_database()

if __name__ == "__main__":
    app.run(debug=True)
