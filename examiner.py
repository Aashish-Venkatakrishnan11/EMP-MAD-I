"""Examiner pages: assigned examinations, slots, booked students and rubric evaluation."""
from datetime import date, datetime, time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from auth import flash_all, problems, profile_page, role_required, text
from models import Booking, Examination, RubricScore, Slot, db

examiner_bp = Blueprint("examiner", __name__, url_prefix="/examiner")
examiner_only = role_required("examiner")  # also checks that the Admin approved them


def me():
    return current_user.examiner_profile


def assigned_exam(exam_id):
    """An examination, but only if it is assigned to me."""
    exam = db.get_or_404(Examination, exam_id)
    if me() not in exam.examiners:
        abort(403)
    return exam


def own_slot(slot_id):
    """A slot, but only if it is mine."""
    slot = db.get_or_404(Slot, slot_id)
    if slot.examiner != me():
        abort(403)
    return slot


def waiting_bookings():
    """Bookings in my slots that still need evaluation, earliest first."""
    rows = [b for s in me().slots for b in s.bookings if b.status == "Booked"]
    return sorted(rows, key=lambda b: (b.slot.date, b.slot.start_time))


@examiner_bp.route("/dashboard")
@examiner_only
def dashboard():
    slots = sorted((s for s in me().slots if s.status != "Cancelled"), key=lambda s: (s.date, s.start_time))
    upcoming = [s for s in slots if s.date >= date.today() and s.status != "Completed"]
    return render_template("examiner/dashboard.html", exams=sorted(me().examinations, key=lambda e: e.booking_start),
                           slots=slots, upcoming_slots=upcoming[:6], pending=waiting_bookings(),
                           students_booked=sum(len(s.active_bookings) for s in slots))


@examiner_bp.route("/exams/<int:exam_id>")
@examiner_only
def exam_detail(exam_id):
    exam = assigned_exam(exam_id)
    return render_template("examiner/exam_detail.html", exam=exam,
                           my_slots=[s for s in exam.slots if s.examiner == me()])


@examiner_bp.route("/slots")
@examiner_only
def slots():
    exam_id = request.args.get("exam_id", type=int)
    rows = sorted((s for s in me().slots if not exam_id or s.exam_id == exam_id),
                  key=lambda s: (s.date, s.start_time), reverse=True)
    return render_template("examiner/slots.html", slots=rows, exams=me().examinations, exam_id=exam_id)


# One view for both "create" (needs exam_id) and "update" (needs slot_id).
@examiner_bp.route("/exams/<int:exam_id>/slots/new", methods=["GET", "POST"], endpoint="slot_new")
@examiner_bp.route("/slots/<int:slot_id>/edit", methods=["GET", "POST"], endpoint="slot_edit")
@examiner_only
def slot_form(exam_id=None, slot_id=None):
    slot = own_slot(slot_id) if slot_id else None
    exam = slot.examination if slot else assigned_exam(exam_id)
    # Core rules: create only in the slot creation period; update only before booking starts.
    if slot and (not exam.slots_editable or slot.bookings):
        flash("This slot can no longer be changed because booking has started.", "danger")
        return redirect(url_for("examiner.exam_detail", exam_id=exam.id))
    if not slot and not exam.slot_creation_open:
        flash("Slots can be created only while the slot creation period is open.", "danger")
        return redirect(url_for("examiner.exam_detail", exam_id=exam.id))
    if request.method == "POST":
        get = request.form.get
        day, capacity = get("date", type=date.fromisoformat), get("capacity", type=int)
        start, end = get("start_time", type=time.fromisoformat), get("end_time", type=time.fromisoformat)
        errors = problems((day is None, "Enter the slot date."),
                          (day is not None and day < date.today(), "The slot date cannot be in the past."),
                          (None in (start, end), "Enter both a start time and an end time."),
                          (None not in (start, end) and end <= start, "The end time must be after the start time."),
                          (not (capacity and 1 <= capacity <= 500), "Capacity must be between 1 and 500 students."))
        if not errors and me().has_clash(day, start, end, slot.id if slot else None):
            errors.append("You already have another slot that overlaps this time.")
        if not errors:
            slot = slot or Slot(examination=exam, examiner=me())
            slot.date, slot.start_time, slot.end_time, slot.venue = day, start, end, text("venue")
            slot.capacity = slot.available_seats = capacity  # every seat is free until booking starts
            db.session.add(slot)
            db.session.commit()
            flash("Slot updated." if slot_id else "Slot created.", "success")
            return redirect(url_for("examiner.exam_detail", exam_id=exam.id))
        flash_all(errors)
    return render_template("examiner/slot_form.html", exam=exam, slot=slot, form=request.form)


@examiner_bp.route("/slots/<int:slot_id>/delete", methods=["POST"])
@examiner_only
def slot_delete(slot_id):
    slot = own_slot(slot_id)
    if not slot.examination.slots_editable or slot.bookings:
        flash("This slot can no longer be removed because booking has started.", "danger")
    else:
        db.session.delete(slot)
        db.session.commit()
        flash("Slot removed.", "success")
    return redirect(url_for("examiner.exam_detail", exam_id=slot.exam_id))


@examiner_bp.route("/slots/<int:slot_id>")
@examiner_only
def slot_detail(slot_id):
    """The students booked into one of my slots."""
    slot = own_slot(slot_id)
    return render_template("examiner/slot_detail.html", slot=slot,
                           bookings=sorted(slot.bookings, key=lambda b: b.booking_date))


@examiner_bp.route("/evaluations")
@examiner_only
def evaluations():
    done = [b for s in me().slots for b in s.bookings if b.is_evaluated]
    return render_template("examiner/evaluations.html", pending=waiting_bookings(),
                           done=sorted(done, key=lambda b: b.evaluated_at, reverse=True))


@examiner_bp.route("/bookings/<int:booking_id>/evaluate", methods=["GET", "POST"])
@examiner_only
def evaluate(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    slot, exam = booking.slot, booking.examination
    if slot.examiner != me():  # core rule: only students booked into my own slots
        abort(403)
    if booking.status == "Cancelled":
        refused = "A cancelled booking cannot be evaluated."
    elif not exam.rubrics:
        refused = "The Admin has not defined a rubric for this examination yet."
    elif exam.results_published:
        refused = "Results are published, so evaluations are locked."
    elif not exam.evaluation_open:
        refused = "Evaluation opens once the Admin closes slot booking."
    else:
        refused = None
    if refused:
        flash(refused, "danger")
        return redirect(url_for("examiner.slot_detail", slot_id=slot.id))

    saved = {score.rubric_id: score for score in booking.scores}
    if request.method == "POST":
        marks = {r.id: request.form.get(f"marks_{r.id}", type=float) for r in exam.rubrics}
        errors = [f"Marks for '{r.criterion_name}' must be between 0 and {r.max_marks:g}." for r in exam.rubrics
                  if marks[r.id] is None or not 0 <= marks[r.id] <= r.max_marks]
        errors += problems((not text("remarks"), "Write overall remarks for the student."))
        if not errors:
            for r in exam.rubrics:
                score = saved.get(r.id)
                if score is None:  # first evaluation: one new score row per criterion
                    score = RubricScore(rubric=r)
                    booking.scores.append(score)
                score.marks_awarded, score.comment = marks[r.id], text(f"comment_{r.id}")
            booking.total_marks = booking.calculate_total()
            booking.remarks, booking.evaluated_at, booking.status = text("remarks"), datetime.now(), "Completed"
            if not any(b.status == "Booked" for b in slot.bookings):  # nobody left to evaluate
                slot.status = "Completed"
            db.session.commit()
            flash(f"Evaluation saved. Final score: {booking.total_marks:g} / {exam.max_marks:g}.", "success")
            return redirect(url_for("examiner.slot_detail", slot_id=slot.id))
        flash_all(errors)
    return render_template("examiner/evaluate.html", booking=booking, slot=slot, exam=exam,
                           existing=saved, form=request.form)


@examiner_bp.route("/profile", methods=["GET", "POST"])
@examiner_only
def profile():
    return profile_page("examiner/profile.html", me(), ("department", "designation"), required=("department",))
