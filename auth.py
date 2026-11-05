"""Login, logout and registration, plus small helpers every role uses."""
import re
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from models import ExaminerProfile, StudentProfile, User, db

auth_bp = Blueprint("auth", __name__)


# ---------- Helpers ----------
def role_required(role):
    """Allow a page only for a logged-in user of `role`. Examiners must also be approved."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role != role:
                abort(403)
            if role == "examiner" and current_user.examiner_profile.status != "Approved":
                return redirect(url_for("auth.pending"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def home_url(user):
    """Each role's dashboard. Blueprint names match role names, e.g. "student.dashboard"."""
    if user.role == "examiner" and user.examiner_profile.status != "Approved":
        return url_for("auth.pending")
    return url_for(user.role + ".dashboard")


def text(name):
    """A trimmed value from the submitted form."""
    return request.form.get(name, "").strip()


def arg(name):
    """A trimmed value from the URL's query string (search boxes and filters)."""
    return request.args.get(name, "").strip()


def problems(*checks):
    """Turn (failed?, message) pairs into a list of error messages."""
    return [message for failed, message in checks if failed]


def positive(number):
    return number is not None and number > 0


def flash_all(errors):
    for message in errors:
        flash(message, "danger")


def account_errors(user=None):
    """Check name, email, phone and password. `user` is None when registering
    and the logged-in user when they edit their profile."""
    form, email, phone = request.form, text("email").lower(), text("phone")
    owner = User.query.filter_by(email=email).first()
    errors = problems(
        (len(text("full_name")) < 2, "Enter your full name."),
        (not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email), "Enter a valid email address."),
        (owner is not None and owner != user, "An account with that email already exists."),
        (phone and not re.fullmatch(r"[0-9+\- ]{7,20}", phone), "Enter a valid phone number."))
    if user is None:  # registering: username and password are required
        username, password = text("username").lower(), form.get("password", "")
        errors += problems(
            (not re.fullmatch(r"[a-z0-9_.]{3,40}", username),
             "Username must be 3 to 40 characters: letters, digits, dot or underscore."),
            (User.query.filter_by(username=username).first() is not None, "That username is already taken."))
    else:  # editing a profile: the password changes only if a new one is typed
        password = form.get("new_password", "")
        if password and not user.check_password(form.get("current_password", "")):
            errors.append("Your current password is incorrect.")
    if user is None or password:
        errors += problems((len(password) < 6, "Password must be at least 6 characters long."),
                           (password != form.get("confirm_password", ""), "The two passwords do not match."))
    return errors


def new_user(role):
    """Build a User from the registration form (call after account_errors() passes)."""
    user = User(username=text("username").lower(), email=text("email").lower(),
                full_name=text("full_name"), role=role)
    user.set_password(request.form["password"])
    return user


def new_examiner_profile(status):
    return ExaminerProfile(department=text("department"), designation=text("designation"),
                           phone=text("phone"), status=status)


def profile_page(template, profile, fields, required=()):
    """Edit-profile page shared by examiners and students."""
    if request.method == "POST":
        errors = account_errors(current_user) + [f"Enter your {name}." for name in required if not text(name)]
        if not errors:
            current_user.full_name, current_user.email = text("full_name"), text("email").lower()
            for name in ("phone",) + fields:
                setattr(profile, name, text(name))
            if request.form.get("new_password"):
                current_user.set_password(request.form["new_password"])
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(request.path)
        flash_all(errors)
    # The template calls the profile "examiner" or "student", the same as the role.
    return render_template(template, form=request.form, **{current_user.role: profile})


# ---------- Pages ----------
@auth_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(home_url(current_user))
    return render_template("index.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(home_url(current_user))
    identifier = text("identifier").lower()
    if request.method == "POST":
        # One form for every role; the username or the email both work.
        user = User.query.filter(or_(User.username == identifier, User.email == identifier)).first()
        if user is None or not user.check_password(request.form.get("password", "")):
            flash("Incorrect username or password.", "danger")
        elif user.role == "examiner" and user.examiner_profile.status in ("Rejected", "Deactivated"):
            flash("Your examiner account is not active. Contact the Admin.", "danger")
        else:
            login_user(user)
            return redirect(home_url(user))
    return render_template("auth/login.html", identifier=identifier)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# One view serves both registration pages; `role` comes from the URL rule.
@auth_bp.route("/register/student", methods=["GET", "POST"], endpoint="register_student",
               defaults={"role": "student"})
@auth_bp.route("/register/examiner", methods=["GET", "POST"], endpoint="register_examiner",
               defaults={"role": "examiner"})
def register(role):
    if current_user.is_authenticated:
        return redirect(home_url(current_user))
    if request.method == "POST":
        errors, roll = account_errors(), text("roll_number").upper()
        if role == "student":
            errors += problems((not roll, "Enter your roll number."),
                               (StudentProfile.query.filter_by(roll_number=roll).first() is not None,
                                "That roll number is already registered."))
        elif not text("department"):
            errors.append("Enter your department.")
        if not errors:
            user = new_user(role)
            if role == "student":
                user.student_profile = StudentProfile(roll_number=roll, program=text("program"), phone=text("phone"))
                flash("Registration complete. Log in to continue.", "success")
            else:  # a self-registered examiner waits for the Admin's approval
                user.examiner_profile = new_examiner_profile("Pending")
                flash("Registration received. You can use the dashboard once the Admin approves it.", "success")
            db.session.add(user)
            db.session.commit()
            return redirect(url_for("auth.login"))
        flash_all(errors)
    return render_template(f"auth/register_{role}.html", form=request.form)


@auth_bp.route("/pending")
@login_required
def pending():
    """Shown to an examiner who is logged in but not approved."""
    if current_user.role != "examiner" or current_user.examiner_profile.status == "Approved":
        return redirect(url_for("auth.index"))
    return render_template("auth/pending.html")
