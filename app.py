"""Examination Management Portal. Run with:  python app.py   then open http://127.0.0.1:5000"""
import os
from datetime import date

from flask import Flask, abort, jsonify, render_template
from flask_login import LoginManager, current_user, login_required

from admin import admin_bp
from auth import auth_bp
from examiner import examiner_bp
from models import Booking, Course, Examination, ExaminerProfile, Slot, StudentProfile, User, db
from student import student_bp

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("EMP_SECRET_KEY", "change-this-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///emp.sqlite3"  # stored in the instance/ folder
db.init_app(app)
for blueprint in (auth_bp, admin_bp, examiner_bp, student_bp):
    app.register_blueprint(blueprint)

login_manager = LoginManager(app)
login_manager.login_view = "auth.login"  # where @login_required sends visitors
login_manager.login_message_category = "info"
login_manager.user_loader(lambda user_id: db.session.get(User, int(user_id)))

# Formatting helpers for templates, e.g. {{ slot.date|datefmt }}
app.add_template_filter(lambda d: d.strftime("%d %b %Y") if d else "-", "datefmt")
app.add_template_filter(lambda t: t.strftime("%H:%M") if t else "-", "timefmt")
app.add_template_filter(lambda d: d.strftime("%d %b %Y, %H:%M") if d else "-", "datetimefmt")
app.add_template_filter(lambda n: f"{n:g}" if n is not None else "-", "num")  # 20.0 -> 20
app.context_processor(lambda: {"today": date.today()})


@app.errorhandler(403)
@app.errorhandler(404)
def error_page(error):
    message = "You do not have access to this page." if error.code == 403 else "That page does not exist."
    return render_template("error.html", code=error.code, message=message), error.code


# The database, its tables and the pre-existing Admin are created by code on first run.
with app.app_context():
    db.create_all()
    if User.query.filter_by(role="admin").first() is None:
        admin = User(username="admin", email="admin@emp.local", full_name="Portal Administrator", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()


# ---------- Read-only JSON API (optional feature); needs a logged-in session ----------
def as_dict(row, *fields):
    """Chosen columns of a row; dates and times become ISO text."""
    values = {field: getattr(row, field) for field in fields}
    return {field: v.isoformat() if hasattr(v, "isoformat") else v for field, v in values.items()}


@app.route("/api/examinations")
@login_required
def api_examinations():
    exams = [e for e in Examination.query.all() if current_user.role != "student" or e.status != "Draft"]
    return jsonify([dict(as_dict(e, "id", "course_id", "name", "exam_type", "duration_minutes", "max_marks",
                                 "slot_creation_start", "slot_creation_end", "booking_start", "booking_end",
                                 "status", "results_published"),
                         slots=[as_dict(s, "id", "examiner_id", "date", "start_time", "end_time", "venue",
                                        "capacity", "available_seats", "status") for s in e.slots])
                    for e in exams])


@app.route("/api/bookings")
@login_required
def api_bookings():
    """Admin: every booking. Examiner: bookings in their slots. Student: their own."""
    if current_user.role == "admin":
        rows = Booking.query.all()
    elif current_user.role == "examiner":
        rows = [b for s in current_user.examiner_profile.slots for b in s.bookings]
    else:
        rows = current_user.student_profile.bookings
    data = []
    for b in rows:
        item = dict(as_dict(b, "id", "student_id", "slot_id", "booking_date", "status"), examination=b.examination.name)
        if current_user.role != "student" or b.result_visible:  # students see marks only once published
            item.update(as_dict(b, "total_marks", "remarks"))
        data.append(item)
    return jsonify(data)


@app.route("/api/<any(examiners, students):kind>")
@login_required
def api_people(kind):
    if current_user.role != "admin":
        abort(403)
    if kind == "examiners":
        people, fields = ExaminerProfile.query.all(), ("id", "department", "designation", "status")
    else:
        people, fields = StudentProfile.query.all(), ("id", "roll_number", "program")
    return jsonify([dict(as_dict(p, *fields), name=p.name, email=p.user.email) for p in people])


@app.route("/api/stats")
@login_required
def api_stats():
    if current_user.role != "admin":
        abort(403)
    models = {"courses": Course, "examinations": Examination, "examiners": ExaminerProfile,
              "students": StudentProfile, "slots": Slot, "bookings": Booking}
    return jsonify({name: model.query.count() for name, model in models.items()})


if __name__ == "__main__":
    app.run(debug=True)
