
"""KARASUNO TEAM-12 | Library Book Usage Analysis System"""

import io
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from flask import Flask, jsonify, render_template, request, send_file


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "library.db"
)


def db_path():
    """Return the database path configured for the active process."""
    return os.environ.get("KARASUNO_DB", DEFAULT_DB_PATH)


DUE_DAYS = 14
app = Flask(__name__)


# ============================================================
# DATABASE SCHEMA
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    book_id INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    category TEXT NOT NULL,
    copies_total INTEGER NOT NULL CHECK(copies_total >= 0),
    copies_available INTEGER NOT NULL CHECK(copies_available >= 0),
    CHECK(copies_available <= copies_total)
);

CREATE TABLE IF NOT EXISTS borrowers (
    borrower_id INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_name TEXT NOT NULL,
    roll_no TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS loans (
    loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(book_id),
    borrower_id INTEGER NOT NULL REFERENCES borrowers(borrower_id),
    issue_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    return_date TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS puppy_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    name TEXT,
    water INTEGER,
    happiness INTEGER,
    last_watered TEXT
);
"""


@contextmanager
def db():
    """Open a database connection, commit transactions, and close it automatically."""
    database_path = db_path()
    database_dir = os.path.dirname(database_path)

    if database_dir:
        os.makedirs(database_dir, exist_ok=True)

    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init():
    """Create database tables and default settings."""
    with db() as connection:
        connection.executescript(SCHEMA)

        connection.execute(
            "INSERT OR IGNORE INTO settings(k, v) VALUES(?, ?)",
            ("due_days", str(DUE_DAYS))
        )

        connection.execute(
            """
            INSERT OR IGNORE INTO puppy_state
            (id, name, water, happiness, last_watered)
            VALUES (1, 'Karasu Pup', 80, 80, ?)
            """,
            (date.today().isoformat(),)
        )


def due_days():
    """Return the configured loan duration, falling back to the default if missing."""
    with db() as connection:
        row = connection.execute(
            "SELECT v FROM settings WHERE k = 'due_days'"
        ).fetchone()

        if row is None or row["v"] is None:
            connection.execute(
                "INSERT OR IGNORE INTO settings(k, v) VALUES(?, ?)",
                ("due_days", str(DUE_DAYS))
            )
            return DUE_DAYS

        return int(row["v"])


def pdate(value, name):
    """Parse a date in YYYY-MM-DD format."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be a valid date (YYYY-MM-DD)"
        )


def err(message, code=400):
    """Return a standard JSON error response."""
    return jsonify(ok=False, error=message), code


# ============================================================
# LOAN DATA AND ANALYTICS
# ============================================================

def loans_df():
    """Load loan records and calculate their statuses."""
    query = """
        SELECT l.*, b.title, b.category,
               br.borrower_name, br.roll_no
        FROM loans l
        JOIN books b ON l.book_id = b.book_id
        JOIN borrowers br ON l.borrower_id = br.borrower_id
    """

    with db() as connection:
        df = pd.read_sql_query(query, connection)

    if df.empty:
        return df

    for column in ("issue_date", "due_date", "return_date"):
        df[column] = pd.to_datetime(df[column], errors="coerce")

    today = pd.Timestamp(date.today())
    returned = df["return_date"]

    df["status"] = np.where(
        returned.isna(),
        np.where(df["due_date"] < today, "OVERDUE", "ACTIVE"),
        np.where(
            returned > df["due_date"],
            "RETURNED LATE",
            "RETURNED"
        )
    )

    overdue_days = np.where(
        returned.isna(),
        (today - df["due_date"]).dt.days,
        (returned - df["due_date"]).dt.days
    )

    df["days_overdue"] = np.maximum(
        np.nan_to_num(overdue_days, nan=0),
        0
    ).astype(int)

    return df


def report_frames():
    """Create data frames used by the reports and charts."""
    df = loans_df()

    if df.empty:
        return None, None, None

    top = (
        df.groupby(["book_id", "title"])
        .size()
        .reset_index(name="times_borrowed")
        .sort_values("times_borrowed", ascending=False)
    )

    category = (
        df.groupby("category")
        .agg(
            loans=("loan_id", "count"),
            overdue=(
                "status",
                lambda values: int((values == "OVERDUE").sum())
            )
        )
        .reset_index()
    )

    monthly = (
        df.groupby(df["issue_date"].dt.to_period("M").astype(str))
        .size()
        .reset_index(name="loans")
        .rename(columns={"issue_date": "month"})
    )

    return top, category, monthly


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")


# ============================================================
# BOOKS API
# ============================================================

@app.get("/api/books")
def books():
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM books ORDER BY title"
        ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.post("/api/books")
def add_book():
    data = request.get_json(silent=True) or {}

    try:
        copies = int(data.get("copies", 0))

        if copies <= 0:
            raise ValueError

        for field in ("isbn", "title", "author", "category"):
            if not str(data.get(field, "")).strip():
                raise ValueError

    except (TypeError, ValueError):
        return err(
            "All fields are required and copies must be a positive number"
        )

    try:
        with db() as connection:
            connection.execute(
                """
                INSERT INTO books
                (isbn, title, author, category,
                 copies_total, copies_available)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data["isbn"].strip(),
                    data["title"].strip(),
                    data["author"].strip(),
                    data["category"].strip(),
                    copies,
                    copies
                )
            )

    except sqlite3.IntegrityError:
        return err("A book with this ISBN already exists")

    return jsonify(ok=True)


# ============================================================
# BORROWERS API
# ============================================================

@app.get("/api/borrowers")
def borrowers():
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM borrowers ORDER BY borrower_name"
        ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.post("/api/borrowers")
def add_borrower():
    data = request.get_json(silent=True) or {}

    name = str(data.get("borrower_name", "")).strip()
    roll_no = str(data.get("roll_no", "")).strip()

    if not name or not roll_no:
        return err("Name and roll number are required")

    try:
        with db() as connection:
            connection.execute(
                """
                INSERT INTO borrowers (borrower_name, roll_no)
                VALUES (?, ?)
                """,
                (name, roll_no)
            )

    except sqlite3.IntegrityError:
        return err("This roll number is already registered")

    return jsonify(ok=True)


# ============================================================
# ISSUE BOOK API
# ============================================================

@app.post("/api/issue")
def issue():
    data = request.get_json(silent=True) or {}

    try:
        issue_date = pdate(
            data.get("issue_date") or date.today().isoformat(),
            "Issue date"
        )
    except ValueError as error:
        return err(str(error))

    if issue_date > date.today():
        return err("Issue date cannot be in the future")

    with db() as connection:
        book = connection.execute(
            "SELECT * FROM books WHERE book_id = ?",
            (data.get("book_id"),)
        ).fetchone()

        borrower = connection.execute(
            "SELECT * FROM borrowers WHERE borrower_id = ?",
            (data.get("borrower_id"),)
        ).fetchone()

        if not book or not borrower:
            return err("Book or borrower not found", 404)

        if book["copies_available"] < 1:
            return err("No copies available for this book")

        existing = connection.execute(
            """
            SELECT 1 FROM loans
            WHERE book_id = ?
              AND borrower_id = ?
              AND return_date IS NULL
            """,
            (book["book_id"], borrower["borrower_id"])
        ).fetchone()

        if existing:
            return err(
                "This borrower already holds an unreturned copy of this book"
            )

        due_date = issue_date + timedelta(days=due_days())

        connection.execute(
            """
            INSERT INTO loans
            (book_id, borrower_id, issue_date, due_date)
            VALUES (?, ?, ?, ?)
            """,
            (
                book["book_id"],
                borrower["borrower_id"],
                issue_date.isoformat(),
                due_date.isoformat()
            )
        )

        connection.execute(
            """
            UPDATE books
            SET copies_available = copies_available - 1
            WHERE book_id = ?
            """,
            (book["book_id"],)
        )

    return jsonify(ok=True)


# ============================================================
# RETURN BOOK API
# ============================================================

@app.post("/api/return")
def return_book():
    data = request.get_json(silent=True) or {}

    try:
        return_date = pdate(
            data.get("return_date") or date.today().isoformat(),
            "Return date"
        )
    except ValueError as error:
        return err(str(error))

    with db() as connection:
        loan = connection.execute(
            "SELECT * FROM loans WHERE loan_id = ?",
            (data.get("loan_id"),)
        ).fetchone()

        if not loan:
            return err("Loan not found", 404)

        if loan["return_date"]:
            return err("This loan is already returned")

        issue_date = pdate(loan["issue_date"], "Issue date")

        if return_date < issue_date:
            return err("Return date cannot be before the issue date")

        if return_date > date.today():
            return err("Return date cannot be in the future")

        connection.execute(
            "UPDATE loans SET return_date = ? WHERE loan_id = ?",
            (return_date.isoformat(), loan["loan_id"])
        )

        connection.execute(
            """
            UPDATE books
            SET copies_available = copies_available + 1
            WHERE book_id = ?
            """,
            (loan["book_id"],)
        )

    return jsonify(ok=True)


# ============================================================
# SETTINGS API
# ============================================================

@app.get("/api/settings")
def get_settings():
    return jsonify(due_days=due_days())


@app.post("/api/settings")
def set_settings():
    data = request.get_json(silent=True) or {}

    try:
        days = int(data["due_days"])

        if not 1 <= days <= 90:
            raise ValueError

    except (KeyError, TypeError, ValueError):
        return err("Due days must be between 1 and 90")

    with db() as connection:
        connection.execute(
            "UPDATE settings SET v = ? WHERE k = 'due_days'",
            (str(days),)
        )

    return jsonify(ok=True)


# ============================================================
# LOANS API
# ============================================================

@app.get("/api/loans")
def loans():
    df = loans_df()

    if df.empty:
        return jsonify([])

    status_filter = request.args.get("status")

    if status_filter == "open":
        df = df[df["status"].isin(["ACTIVE", "OVERDUE"])]
    elif status_filter:
        df = df[df["status"] == status_filter.upper()]

    for column in ("issue_date", "due_date", "return_date"):
        df[column] = df[column].dt.strftime("%Y-%m-%d").fillna("")

    df = df.sort_values("loan_id", ascending=False)

    return jsonify(df.to_dict("records"))


# ============================================================
# DASHBOARD SUMMARY API
# ============================================================

@app.get("/api/summary")
def summary():
    with db() as connection:
        book_stats = connection.execute(
            """
            SELECT COUNT(*) AS titles,
                   COALESCE(SUM(copies_total), 0) AS copies,
                   COALESCE(SUM(copies_available), 0) AS available
            FROM books
            """
        ).fetchone()

        borrower_count = connection.execute(
            "SELECT COUNT(*) FROM borrowers"
        ).fetchone()[0]

    df = loans_df()

    def count_status(status):
        if df.empty:
            return 0
        return int((df["status"] == status).sum())

    if not df.empty and df["return_date"].notna().any():
        returned = df[df["return_date"].notna()]
        durations = (
            returned["return_date"] - returned["issue_date"]
        ).dt.days
    else:
        durations = pd.Series(dtype=float)

    return jsonify(
        titles=book_stats["titles"],
        copies=book_stats["copies"],
        available=book_stats["available"],
        borrowers=borrower_count,
        total_loans=len(df),
        active=count_status("ACTIVE"),
        overdue=count_status("OVERDUE"),
        late_returns=count_status("RETURNED LATE"),
        due_days=due_days(),
        avg_days=round(float(durations.mean()), 1) if len(durations) else 0,
        median_days=float(durations.median()) if len(durations) else 0,
        std_days=round(float(durations.std(ddof=0)), 1) if len(durations) else 0
    )


# ============================================================
# REPORTS API
# ============================================================

@app.get("/api/reports")
def reports():
    top, category, monthly = report_frames()

    if top is None:
        return jsonify(
            top=[],
            category=[],
            monthly=[],
            overdue=[]
        )

    df = loans_df()

    overdue = df[df["status"] == "OVERDUE"].copy()
    overdue = overdue.sort_values(
        "days_overdue",
        ascending=False
    )

    overdue = overdue[
        [
            "loan_id",
            "title",
            "borrower_name",
            "roll_no",
            "due_date",
            "days_overdue"
        ]
    ].copy()

    overdue["due_date"] = overdue["due_date"].dt.strftime("%Y-%m-%d")

    return jsonify(
        top=top.head(10).to_dict("records"),
        category=category.to_dict("records"),
        monthly=monthly.to_dict("records"),
        overdue=overdue.to_dict("records")
    )


# ============================================================
# CHARTS
# ============================================================

NEON = ["#00f0ff", "#b967ff", "#05ffa1", "#ff71ce", "#fffb96"]
BG = "#0b1020"


def png(fig):
    fig.patch.set_facecolor(BG)

    buffer = io.BytesIO()

    fig.savefig(
        buffer,
        format="png",
        dpi=110,
        facecolor=BG,
        bbox_inches="tight"
    )

    plt.close(fig)
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")


def style(ax, title):
    ax.set_facecolor(BG)
    ax.set_title(
        title,
        color="#e8f4ff",
        fontsize=13,
        pad=12
    )

    ax.tick_params(colors="#9fb3d1")

    for spine in ax.spines.values():
        spine.set_color("#27355c")

    ax.grid(color="#1a2547", alpha=0.7)


@app.get("/chart/<kind>.png")
def chart(kind):
    if kind not in ("monthly", "popular", "category"):
        return err("Unknown chart type", 404)

    top, category, monthly = report_frames()

    fig, ax = plt.subplots(figsize=(7, 4))
    style(ax, kind.title())

    if top is None:
        ax.text(
            0.5,
            0.5,
            "No data yet",
            color="#9fb3d1",
            ha="center",
            transform=ax.transAxes
        )
        return png(fig)

    if kind == "monthly":
        positions = np.arange(len(monthly))

        ax.plot(
            positions,
            monthly["loans"],
            color=NEON[0],
            marker="o",
            linewidth=2.5
        )

        ax.fill_between(
            positions,
            monthly["loans"].to_numpy(),
            color=NEON[0],
            alpha=0.15
        )

        ax.set_xticks(positions)
        ax.set_xticklabels(monthly["month"], rotation=30)
        ax.set_title("Monthly Circulation", color="#e8f4ff")

    elif kind == "popular":
        data = top.head(8).iloc[::-1]

        ax.barh(
            [title[:28] for title in data["title"]],
            data["times_borrowed"],
            color=NEON[1]
        )

        ax.set_title("Most Borrowed Books", color="#e8f4ff")

    else:
        ax.bar(
            category["category"],
            category["loans"],
            color=NEON[2]
        )

        ax.set_title("Loans by Category", color="#e8f4ff")
        plt.setp(ax.get_xticklabels(), rotation=25)

    fig.tight_layout()

    return png(fig)


# ============================================================
# DEMO DATA
# ============================================================

def seed():
    """Insert demonstration records when the books table is empty."""
    init()
    random.seed(12)

    book_data = [
        ("9780132350884", "Clean Code", "Robert Martin", "Programming"),
        ("9780262033848", "Introduction to Algorithms", "Cormen", "Computer Science"),
        ("9780134685991", "Effective Java", "Joshua Bloch", "Programming"),
        ("9780596517748", "JavaScript: The Good Parts", "D. Crockford", "Programming"),
        ("9780135957059", "The Pragmatic Programmer", "Hunt & Thomas", "Programming"),
        ("9781492032649", "Hands-On Machine Learning", "A. Geron", "AI/ML"),
        ("9780136042594", "Artificial Intelligence: A Modern Approach", "Russell & Norvig", "AI/ML"),
        ("9780321573513", "Algorithms", "Sedgewick", "Computer Science"),
        ("9780073523323", "Digital Design", "M. Morris Mano", "Electronics"),
        ("9780131103627", "The C Programming Language", "Kernighan", "Programming"),
        ("9781119800361", "Database System Concepts", "Silberschatz", "Databases"),
        ("9780140449136", "Discrete Mathematics", "K. Rosen", "Mathematics")
    ]

    names = [
        "Aarav", "Diya", "Kiran", "Meera", "Rohan", "Sneha",
        "Vikram", "Anjali", "Tejas", "Pooja", "Harsha", "Isha"
    ]

    weights = [9, 8, 7, 3, 5, 6, 4, 3, 2, 3, 2, 1]

    with db() as connection:
        existing = connection.execute(
            "SELECT COUNT(*) FROM books"
        ).fetchone()[0]

        if existing:
            return

        book_ids = []

        for isbn, title, author, category_name in book_data:
            cursor = connection.execute(
                """
                INSERT INTO books
                (isbn, title, author, category,
                 copies_total, copies_available)
                VALUES (?, ?, ?, ?, 3, 3)
                """,
                (isbn, title, author, category_name)
            )

            book_ids.append(cursor.lastrowid)

        borrower_ids = []

        for index, name in enumerate(names):
            cursor = connection.execute(
                """
                INSERT INTO borrowers (borrower_name, roll_no)
                VALUES (?, ?)
                """,
                (name, f"25102A0107{10 + index}")
            )

            borrower_ids.append(cursor.lastrowid)

        # Create sample circulation history.
        # Returned loans do not reduce available copies.
        # Active loans reduce available copies.
        active_pairs = set()

        for _ in range(70):
            book_id = random.choices(book_ids, weights=weights)[0]
            borrower_id = random.choice(borrower_ids)

            issue_date = date.today() - timedelta(
                days=random.randint(1, 170)
            )

            book = connection.execute(
                "SELECT copies_available FROM books WHERE book_id = ?",
                (book_id,)
            ).fetchone()

            if not book or book["copies_available"] < 1:
                continue

            if (book_id, borrower_id) in active_pairs:
                continue

            existing_active = connection.execute(
                """
                SELECT 1 FROM loans
                WHERE book_id = ?
                  AND borrower_id = ?
                  AND return_date IS NULL
                """,
                (book_id, borrower_id)
            ).fetchone()

            if existing_active:
                continue

            if random.random() < 0.15:
                return_date = None
            else:
                return_date = issue_date + timedelta(
                    days=random.randint(3, 24)
                )

                if return_date > date.today():
                    return_date = date.today()

            connection.execute(
                """
                INSERT INTO loans
                (book_id, borrower_id, issue_date, due_date, return_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    borrower_id,
                    issue_date.isoformat(),
                    (issue_date + timedelta(days=DUE_DAYS)).isoformat(),
                    return_date.isoformat() if return_date else None
                )
            )

            if return_date is None:
                connection.execute(
                    """
                    UPDATE books
                    SET copies_available = copies_available - 1
                    WHERE book_id = ?
                    """,
                    (book_id,)
                )

                active_pairs.add((book_id, borrower_id))


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":
    seed()

    print("KARASUNO running at http://127.0.0.1:5000")

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
