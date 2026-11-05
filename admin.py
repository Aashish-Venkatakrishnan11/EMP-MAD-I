"""Admin pages: courses, examinations, rubrics, examiners, students, slots and bookings."""
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from auth import account_errors, arg, flash_all, new_examiner_profile, new_user, positive, problems, role_required, text
from models import (BOOKING_STATUSES, EXAM_STATUSES, EXAM_TYPES, EXAMINER_STATUSES, SLOT_STATUSES, Booking, Course,
                    Examination, ExaminerProfile, Rubric, Slot, StudentProfile, User, db)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
admin_only = role_required("admin")
DATE_FIELDS = ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end")
RUBRIC_LOCKED = "Rubrics are locked because evaluations have already been submitted."


# ---------- Search and filter helpers ----------
def matching(query, q, id_column, *columns):
    """Keep rows where any column contains q, or whose ID equals q."""
    if not q:
        return query
    conditions = [column.ilike(f"%{q}%") for column in columns]
    if q.isdigit():
        conditions.append(id_column == int(q))
    return query.filter(or_(*conditions))


def equal(query, *pairs):
    """Apply `column == value` for every (column, value) pair whose value was chosen."""
    for column, value in pairs:
        if value:
            query = query.filter(column == value)
    return query


def find_students(q):
    return matching(StudentProfile.query.join(User), q, StudentProfile.id, User.full_name, User.username,
                    User.email, StudentProfile.roll_number).order_by(User.full_name)


def find_examiners(q):
    return matching(ExaminerProfile.query.join(User), q, ExaminerProfile.id, User.full_name, User.username,
                    User.email, ExaminerProfile.department).order_by(User.full_name)


def find_exams(q):
    return matching(Examination.query.join(Course), q, Examination.id, Examination.name, Course.code,
                    Course.name).order_by(Examination.id.desc())


def find_bookings(q):
    query = Booking.query.join(StudentProfile).join(User).join(Slot).join(Examination)
    return matching(query, q, Booking.id, User.full_name, StudentProfile.roll_number,
                    Examination.name).order_by(Booking.booking_date.desc())


def to_exam(exam):
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


# ---------- Dashboard and search ----------
@admin_bp.route("/dashboard")
@admin_only
def dashboard():
    totals = {"courses": Course.query.count(), "examinations": Examination.query.count(),
              "examiners": ExaminerProfile.query.count(), "students": StudentProfile.query.count(),
              "slots": Slot.query.count(), "bookings": Booking.query.count()}
    exams = Examination.query.all()
    per_exam = sorted(((e, e.active_booking_count) for e in exams), key=lambda row: -row[1])[:6]
    workload = []  # (examiner, live slots, students booked)
    for examiner in ExaminerProfile.query.filter_by(status="Approved"):
        live = [s for s in examiner.slots if s.status != "Cancelled"]
        workload.append((examiner, len(live), sum(len(s.active_bookings) for s in live)))
    workload = sorted(workload, key=lambda row: -row[2])[:6]
    return render_template(
        "admin/dashboard.html", totals=totals, bookings_per_exam=per_exam, workload=workload,
        pending_examiners=ExaminerProfile.query.filter_by(status="Pending").order_by(ExaminerProfile.id.desc()).all(),
        max_exam_bookings=max([count for _, count in per_exam] + [1]),
        max_workload=max([row[2] for row in workload] + [1]),
        exam_status_counts=[(s, sum(e.status == s for e in exams)) for s in EXAM_STATUSES],
        booking_status_counts=[(s, Booking.query.filter_by(status=s).count()) for s in BOOKING_STATUSES],
        recent_bookings=Booking.query.order_by(Booking.booking_date.desc()).limit(6).all())


@admin_bp.route("/search")
@admin_only
def search():
    """One search box across students, examiners, examinations and bookings."""
    q = arg("q")
    results = {"students": find_students(q).all(), "examiners": find_examiners(q).all(),
               "examinations": find_exams(q).all(), "bookings": find_bookings(q).all()} if q else None
    return render_template("admin/search.html", q=q, results=results)


# ---------- Courses ----------
@admin_bp.route("/courses")
@admin_only
def courses():
    q = arg("q")
    rows = matching(Course.query, q, Course.id, Course.code, Course.name).order_by(Course.code).all()
    return render_template("admin/courses.html", courses=rows, q=q)


# One view for both "add" and "edit"; course_id is only present when editing.
@admin_bp.route("/courses/new", methods=["GET", "POST"], endpoint="course_new")
@admin_bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"], endpoint="course_edit")
@admin_only
def course_form(course_id=None):
    course = db.get_or_404(Course, course_id) if course_id else None
    if request.method == "POST":
        code = text("code").upper()
        same_code = Course.query.filter_by(code=code).first()
        errors = problems((not code, "Enter a course code."), (not text("name"), "Enter a course name."),
                          (text("status") not in ("Active", "Inactive"), "Choose a valid course status."),
                          (same_code is not None and same_code != course, "Another course already uses that code."))
        if not errors:
            course = course or Course()
            course.code, course.name = code, text("name")
            course.description, course.status = text("description"), text("status")
            db.session.add(course)
            db.session.commit()
            flash("Course updated." if course_id else "Course created.", "success")
            return redirect(url_for("admin.courses"))
        flash_all(errors)
    return render_template("admin/course_form.html", course=course, form=request.form)


@admin_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@admin_only
def course_delete(course_id):
    course = db.get_or_404(Course, course_id)
    if course.examinations:  # removing it would wipe examination history
        flash("This course has examinations. Remove them first, or set the course to Inactive.", "danger")
    else:
        db.session.delete(course)
        db.session.commit()
        flash("Course removed.", "success")
    return redirect(url_for("admin.courses"))


# ---------- Examinations ----------
@admin_bp.route("/exams")
@admin_only
def exams():
    q, status, exam_type, course_id = arg("q"), arg("status"), arg("exam_type"), request.args.get("course_id", type=int)
    rows = equal(find_exams(q), (Examination.status, status), (Examination.exam_type, exam_type),
                 (Examination.course_id, course_id)).all()
    return render_template("admin/exams.html", exams=rows, courses=Course.query.order_by(Course.code).all(), q=q,
                           status=status, exam_type=exam_type, course_id=course_id,
                           exam_statuses=EXAM_STATUSES, exam_types=EXAM_TYPES)


@admin_bp.route("/exams/new", methods=["GET", "POST"], endpoint="exam_new")
@admin_bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"], endpoint="exam_edit")
@admin_only
def exam_form(exam_id=None):
    exam = db.get_or_404(Examination, exam_id) if exam_id else None
    if request.method == "POST":
        get = request.form.get
        data = {"course_id": get("course_id", type=int), "name": text("name"), "exam_type": text("exam_type"),
                "description": text("description"), "duration_minutes": get("duration_minutes", type=int),
                "max_marks": get("max_marks", type=float)}
        dates = [get(field, type=date.fromisoformat) for field in DATE_FIELDS]  # None if missing or not a date
        data.update(zip(DATE_FIELDS, dates))
        errors = problems(
            (db.session.get(Course, data["course_id"] or 0) is None, "Choose a course."),
            (not data["name"], "Enter an examination name."),
            (data["exam_type"] not in EXAM_TYPES, "Choose a valid examination type."),
            (not positive(data["duration_minutes"]), "Duration must be a positive number of minutes."),
            (not positive(data["max_marks"]), "Maximum marks must be greater than zero."),
            (exam is not None and exam.has_evaluations and data["max_marks"] != exam.max_marks,
             "Maximum marks cannot change after evaluations have been submitted."))
        if None in dates:
            errors.append("Enter all four timeline dates.")
        else:
            create_start, create_end, book_start, book_end = dates
            errors += problems((create_end < create_start, "Slot creation cannot end before it starts."),
                               (book_end < book_start, "Slot booking cannot end before it starts."),
                               (book_start < create_end, "Slot booking cannot start before slot creation ends."))
        if not errors:
            exam = exam or Examination(status="Draft")
            for field, value in data.items():
                setattr(exam, field, value)
            db.session.add(exam)
            db.session.commit()
            flash("Examination updated." if exam_id else
                  "Examination created as a draft. Add rubrics and assign examiners next.", "success")
            return to_exam(exam)
        flash_all(errors)
    courses = Course.query if exam else Course.query.filter_by(status="Active")
    return render_template("admin/exam_form.html", exam=exam, form=request.form,
                           courses=courses.order_by(Course.code).all(), exam_types=EXAM_TYPES)


@admin_bp.route("/exams/<int:exam_id>")
@admin_only
def exam_detail(exam_id):
    exam = db.get_or_404(Examination, exam_id)
    others = [e for e in ExaminerProfile.query.filter_by(status="Approved") if e not in exam.examiners]
    return render_template("admin/exam_detail.html", exam=exam,
                           available_examiners=sorted(others, key=lambda e: e.name.lower()),
                           pending_evaluations=sum(b.status == "Booked" for b in exam.bookings))


@admin_bp.route("/exams/<int:exam_id>/delete", methods=["POST"])
@admin_only
def exam_delete(exam_id):
    exam = db.get_or_404(Examination, exam_id)
    if exam.bookings:
        flash("This examination has bookings, so it is kept for the record and cannot be removed.", "danger")
        return to_exam(exam)
    exam.examiners = []
    db.session.delete(exam)  # its rubrics and empty slots are removed with it
    db.session.commit()
    flash("Examination removed.", "success")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/exams/<int:exam_id>/status", methods=["POST"])
@admin_only
def exam_status(exam_id):
    """Open or close slot creation and slot booking, or mark the examination completed."""
    exam = db.get_or_404(Examination, exam_id)
    no_slot = not any(s.status == "Available" for s in exam.slots)
    # action: (new status, success message, reason it is refused or None)
    rules = {
        "open_slot_creation": ("Slot Creation", "Slot creation is open for assigned examiners.", None),
        "close_slot_creation": ("Closed", "Slot creation is closed.",
                                "Slot creation is not open." if exam.status != "Slot Creation" else None),
        "open_booking": ("Booking Open", "Slot booking is open for students.",
                         "There is no available slot to book. Booking was not opened." if no_slot else None),
        "close_booking": ("Closed", "Slot booking is closed. Examiners can now evaluate.",
                          "Slot booking is not open." if exam.status != "Booking Open" else None),
        "complete": ("Completed", "Examination marked as completed.",
                     "Open slot booking before completing the examination." if not exam.booking_started else None),
    }
    if request.form.get("action") not in rules:
        abort(400)
    new_status, done, refused = rules[request.form["action"]]
    if exam.results_published:
        refused = "Results are published. Unpublish them before changing the status."
    if refused:
        flash(refused, "danger")
        return to_exam(exam)
    exam.status = new_status
    if new_status == "Booking Open" and not exam.booking_started:
        exam.booking_opened_at = datetime.now()
    db.session.commit()
    flash(done, "success")
    if new_status == "Slot Creation" and not exam.examiners:
        flash("No examiner is assigned yet, so nobody can create slots.", "warning")
    return to_exam(exam)


@admin_bp.route("/exams/<int:exam_id>/publish", methods=["POST"])
@admin_only
def exam_publish(exam_id):
    """Students see their marks only after results are published."""
    exam = db.get_or_404(Examination, exam_id)
    if request.form.get("action") == "unpublish":
        exam.results_published = False
        flash("Results are hidden from students again.", "success")
    elif exam.status not in ("Closed", "Completed") or not exam.booking_started:
        flash("Close slot booking before publishing results.", "danger")
    else:
        exam.results_published, exam.status = True, "Completed"
        flash("Results published.", "success")
        waiting = sum(b.status == "Booked" for b in exam.bookings)
        if waiting:
            flash(f"{waiting} booking(s) have not been evaluated and have no result.", "warning")
    db.session.commit()
    return to_exam(exam)


@admin_bp.route("/exams/<int:exam_id>/examiners", methods=["POST"])
@admin_only
def exam_assign_examiner(exam_id):
    exam = db.get_or_404(Examination, exam_id)
    examiner = db.session.get(ExaminerProfile, request.form.get("examiner_id", 0, type=int))
    if examiner is None or examiner.status != "Approved":
        flash("Choose an approved examiner.", "danger")
    elif examiner not in exam.examiners:
        exam.examiners.append(examiner)
        db.session.commit()
        flash(f"{examiner.name} assigned to this examination.", "success")
    return to_exam(exam)


@admin_bp.route("/exams/<int:exam_id>/examiners/<int:examiner_id>/remove", methods=["POST"])
@admin_only
def exam_unassign_examiner(exam_id, examiner_id):
    exam, examiner = db.get_or_404(Examination, exam_id), db.get_or_404(ExaminerProfile, examiner_id)
    if any(s.examiner == examiner and s.status != "Cancelled" for s in exam.slots):
        flash("This examiner has slots for the examination. Reassign those slots first.", "danger")
    elif examiner in exam.examiners:
        exam.examiners.remove(examiner)
        db.session.commit()
        flash(f"{examiner.name} is no longer assigned.", "success")
    return to_exam(exam)


# ---------- Rubrics ----------
def save_rubric(exam, rubric):
    """Validate the rubric form and save it. Returns True if it was saved."""
    marks, weight = request.form.get("max_marks", type=float), request.form.get("weightage", type=float)
    left = 100 - sum(r.weightage for r in exam.rubrics if r is not rubric)  # weightage still free
    errors = problems((not text("criterion_name"), "Enter a criterion name."),
                      (not positive(marks), "Criterion maximum marks must be greater than zero."),
                      (not (positive(weight) and weight <= 100), "Weightage must be between 1 and 100 percent."),
                      (positive(weight) and weight > left + 0.0001,
                       f"Total weightage would exceed 100%. Only {left:g}% is left."))
    if errors:
        flash_all(errors)
        return False
    rubric.examination, rubric.criterion_name, rubric.description = exam, text("criterion_name"), text("description")
    rubric.max_marks, rubric.weightage = marks, weight
    db.session.add(rubric)
    db.session.commit()
    return True


@admin_bp.route("/exams/<int:exam_id>/rubrics", methods=["POST"])
@admin_only
def rubric_add(exam_id):
    exam = db.get_or_404(Examination, exam_id)
    if exam.has_evaluations:
        flash(RUBRIC_LOCKED, "danger")
    elif save_rubric(exam, Rubric()):
        flash("Criterion added to the rubric.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")


@admin_bp.route("/rubrics/<int:rubric_id>/edit", methods=["GET", "POST"])
@admin_only
def rubric_edit(rubric_id):
    rubric = db.get_or_404(Rubric, rubric_id)
    exam = rubric.examination
    if exam.has_evaluations:
        flash(RUBRIC_LOCKED, "danger")
        return to_exam(exam)
    if request.method == "POST" and save_rubric(exam, rubric):
        flash("Criterion updated.", "success")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")
    return render_template("admin/rubric_form.html", rubric=rubric, exam=exam, form=request.form)


@admin_bp.route("/rubrics/<int:rubric_id>/delete", methods=["POST"])
@admin_only
def rubric_delete(rubric_id):
    rubric = db.get_or_404(Rubric, rubric_id)
    exam = rubric.examination
    if exam.has_evaluations:
        flash(RUBRIC_LOCKED, "danger")
    else:
        db.session.delete(rubric)
        db.session.commit()
        flash("Criterion removed.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id) + "#rubric")


# ---------- Examiners and students ----------
@admin_bp.route("/examiners")
@admin_only
def examiners():
    q, status = arg("q"), arg("status")
    rows = equal(find_examiners(q), (ExaminerProfile.status, status)).all()
    return render_template("admin/examiners.html", examiners=rows, q=q, status=status, statuses=EXAMINER_STATUSES)


@admin_bp.route("/examiners/new", methods=["GET", "POST"])
@admin_only
def examiner_new():
    """An examiner added by the Admin is approved straight away."""
    if request.method == "POST":
        errors = account_errors() + problems((not text("department"), "Enter the examiner's department."))
        if not errors:
            user = new_user("examiner")
            user.examiner_profile = new_examiner_profile("Approved")
            db.session.add(user)
            db.session.commit()
            flash("Examiner added and approved.", "success")
            return redirect(url_for("admin.examiners"))
        flash_all(errors)
    return render_template("admin/examiner_form.html", form=request.form)


@admin_bp.route("/examiners/<int:examiner_id>")
@admin_only
def examiner_detail(examiner_id):
    examiner = db.get_or_404(ExaminerProfile, examiner_id)
    slots = sorted(examiner.slots, key=lambda s: (s.date, s.start_time), reverse=True)
    return render_template("admin/examiner_detail.html", examiner=examiner, slots=slots)


@admin_bp.route("/examiners/<int:examiner_id>/status", methods=["POST"])
@admin_only
def examiner_status(examiner_id):
    examiner = db.get_or_404(ExaminerProfile, examiner_id)
    action = request.form.get("action")
    # action: (statuses it may start from, status it leads to)
    moves = {"approve": (("Pending", "Rejected"), "Approved"), "reject": (("Pending",), "Rejected"),
             "deactivate": (("Approved",), "Deactivated"), "reactivate": (("Deactivated",), "Approved")}
    starts_from, new_status = moves.get(action, ((), None))
    if examiner.status in starts_from:
        examiner.status = new_status
        db.session.commit()
        flash(f"{examiner.name} has been {action}d.", "success")
    else:
        flash("That action is not available for this examiner.", "danger")
    next_page = request.form.get("next", "")  # go back to the page the button was on (local paths only)
    return redirect(next_page if next_page.startswith("/") and not next_page.startswith("//")
                    else url_for("admin.examiners"))


@admin_bp.route("/students")
@admin_only
def students():
    q = arg("q")
    return render_template("admin/students.html", students=find_students(q).all(), q=q)


@admin_bp.route("/students/<int:student_id>")
@admin_only
def student_detail(student_id):
    student = db.get_or_404(StudentProfile, student_id)
    bookings = sorted(student.bookings, key=lambda b: b.booking_date, reverse=True)
    return render_template("admin/student_detail.html", student=student, bookings=bookings)


# ---------- Slots ----------
@admin_bp.route("/slots")
@admin_only
def slots():
    q, status, exam_id = arg("q"), arg("status"), request.args.get("exam_id", type=int)
    query = Slot.query.join(Examination).join(ExaminerProfile).join(User)
    query = equal(matching(query, q, Slot.id, User.full_name, Examination.name, Slot.venue),
                  (Slot.exam_id, exam_id), (Slot.status, status))
    return render_template("admin/slots.html", slots=query.order_by(Slot.date.desc(), Slot.start_time).all(),
                           exams=Examination.query.order_by(Examination.name).all(), exam_id=exam_id,
                           status=status, q=q, slot_statuses=SLOT_STATUSES)


@admin_bp.route("/slots/<int:slot_id>/examiner", methods=["GET", "POST"])
@admin_only
def slot_change_examiner(slot_id):
    """Hand a slot to a different examiner in exceptional situations."""
    slot = db.get_or_404(Slot, slot_id)
    if slot.status in ("Cancelled", "Completed"):
        flash("The examiner of a cancelled or completed slot cannot be changed.", "danger")
        return redirect(url_for("admin.slots"))
    candidates = sorted((e for e in ExaminerProfile.query.filter_by(status="Approved") if e != slot.examiner),
                        key=lambda e: e.name.lower())
    if request.method == "POST":
        new = db.session.get(ExaminerProfile, request.form.get("examiner_id", 0, type=int))
        if new not in candidates:
            flash("Choose a different approved examiner.", "danger")
        elif new.has_clash(slot.date, slot.start_time, slot.end_time):
            flash(f"{new.name} already has a slot at that time.", "danger")
        else:
            flash(f"Slot reassigned from {slot.examiner.name} to {new.name}.", "success")
            slot.examiner = new
            if new not in slot.examination.examiners:  # so the slot shows on their dashboard
                slot.examination.examiners.append(new)
            db.session.commit()
            return redirect(url_for("admin.slots", exam_id=slot.exam_id))
    return render_template("admin/slot_examiner_form.html", slot=slot, candidates=candidates)


@admin_bp.route("/slots/<int:slot_id>/cancel", methods=["POST"])
@admin_only
def slot_cancel(slot_id):
    slot = db.get_or_404(Slot, slot_id)
    if slot.status in ("Cancelled", "Completed") or any(b.is_evaluated for b in slot.bookings):
        flash("This slot cannot be cancelled.", "danger")
    else:
        booked = [b for b in slot.bookings if b.status == "Booked"]
        for booking in booked:
            booking.status, booking.cancelled_at, booking.note = "Cancelled", datetime.now(), "Slot cancelled by Admin"
        slot.status, slot.available_seats = "Cancelled", 0
        db.session.commit()
        flash(f"Slot cancelled. {len(booked)} booking(s) were cancelled with it.", "success")
    return redirect(url_for("admin.slots", exam_id=slot.exam_id))


# ---------- Bookings ----------
@admin_bp.route("/bookings")
@admin_only
def bookings():
    q, status, exam_id = arg("q"), arg("status"), request.args.get("exam_id", type=int)
    rows = equal(find_bookings(q), (Booking.status, status), (Slot.exam_id, exam_id)).all()
    return render_template("admin/bookings.html", bookings=rows, q=q, status=status, exam_id=exam_id,
                           exams=Examination.query.order_by(Examination.name).all(), booking_statuses=BOOKING_STATUSES)


@admin_bp.route("/bookings/<int:booking_id>")
@admin_only
def booking_detail(booking_id):
    return render_template("admin/booking_detail.html", booking=db.get_or_404(Booking, booking_id))


@admin_bp.route("/bookings/<int:booking_id>/reschedule", methods=["GET", "POST"])
@admin_only
def booking_reschedule(booking_id):
    """Move a booking to another slot of the same examination that has a free seat."""
    booking = db.get_or_404(Booking, booking_id)
    if booking.status != "Booked":
        flash("Only an active booking can be rescheduled.", "danger")
        return redirect(url_for("admin.booking_detail", booking_id=booking.id))
    old = booking.slot
    targets = [s for s in old.examination.slots if s != old and s.status == "Available"]
    if request.method == "POST":
        new = db.session.get(Slot, request.form.get("slot_id", 0, type=int))
        if new not in targets:
            flash("Choose a slot with a free seat.", "danger")
        else:
            old.release_seat()
            new.take_seat()
            booking.slot = new
            booking.note = f"Rescheduled by Admin from {old.date:%d %b %Y} {old.start_time:%H:%M}"
            db.session.commit()
            flash("Booking rescheduled.", "success")
            return redirect(url_for("admin.booking_detail", booking_id=booking.id))
    return render_template("admin/reschedule.html", booking=booking, targets=targets)
