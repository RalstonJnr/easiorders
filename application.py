import os  # Add this import at the top of your file
from flask import Flask, render_template, request, redirect, url_for, flash
import openpyxl
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import uuid
from flask_login import login_required, current_user
from flask_login import UserMixin, LoginManager, login_user, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.sql import func
from flask import session
import pandas as pd
from io import BytesIO
from flask import send_file
from flask_login import user_logged_in, user_logged_out
from flask_socketio import SocketIO, emit
from sqlalchemy.orm import validates
import random
import string
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename


# Set up Flask app and SQLAlchemy
application = Flask(__name__)
socketio = SocketIO(application)
application.config[
    "SQLALCHEMY_DATABASE_URI"
] = "sqlite:///orders.db"  # Use SQLite for database storage
application.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # Disable modification tracking
application.secret_key = (
    "easishoppe"  # Add a secret key for sessions (needed for flash messages)
)
db = SQLAlchemy(application)

# Set up Flask-Login
login_manager = LoginManager()
login_manager.init_app(application)
login_manager.login_view = "login"


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.Integer, default=0)  # 0: normal user, 1: admin, 2: viewer
    status = db.Column(
        db.String(20), default="pending"
    )  # 'pending', 'active', or 'banned'
    special_code = db.Column(
        db.String(50), unique=True, nullable=True
    )  # special code for registration

    # New fields for personal information
    full_name = db.Column(db.String(100), nullable=True)
    surname = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), nullable=True, unique=True)
    cell_number = db.Column(db.String(15), nullable=True)

    def __repr__(self):
        return f"<User {self.username}>"

    @validates("role")
    def validate_role(self, key, value):
        """Ensure role is one of the predefined roles (0, 1, 2)."""
        if value not in [0, 1, 2]:
            raise ValueError("Role must be 0 (user), 1 (admin), or 2 (viewer).")
        return value

    @validates("status")
    def validate_status(self, key, value):
        """Ensure status is one of the predefined statuses."""
        if value not in ["pending", "active", "banned"]:
            raise ValueError("Status must be 'pending', 'active', or 'banned'.")
        return value


# Define the Order model
class Order(db.Model):
    id = db.Column(
        db.String(80),
        primary_key=True,
        default=lambda: f"ORD-{uuid.uuid4().hex[:8].upper()}",
    )
    customer_name = db.Column(db.String(120), nullable=False)
    order_items = db.Column(db.String(500), nullable=False)
    order_number = db.Column(db.String(100), nullable=False)
    comments = db.Column(db.String(500))
    timestamp = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default="Not Invoiced")
    delivery_date = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    items = db.relationship("OrderItem", backref="order", lazy=True)
    order_date = db.Column(db.Date, default=datetime.utcnow)  # Auto-set date
    user = db.relationship("User", backref=db.backref("User", lazy=True))
    priority = db.Column(db.Boolean, default=False)  # Default is not high priority
    row_color = db.Column(
        db.String(7), nullable=True
    )  # Store color as a hex code (e.g., #FFD700)
    # New fields
    flag = db.Column(db.Boolean, default=False)
    meter = db.Column(db.Boolean, default=False)
    # Add individual columns for each size
    kg_5 = db.Column(db.Integer, default=0)
    kg_9 = db.Column(db.Integer, default=0)
    kg_14 = db.Column(db.Integer, default=0)
    kg_19 = db.Column(db.Integer, default=0)
    kg_19_flt = db.Column(db.Integer, default=0)
    kg_48_sv = db.Column(db.Integer, default=0)
    kg_48_dv = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<Order {self.id}>"


# Order Item Model
class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), db.ForeignKey("order.id"), nullable=False)
    size = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)


# Activity Log Item Model
class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(
        db.String(100), nullable=False
    )  # User associated with the activity
    action = db.Column(db.String(200), nullable=False)  # Description of the action
    timestamp = db.Column(
        db.DateTime, default=datetime.utcnow
    )  # Timestamp of when the activity occurred

    def __repr__(self):
        return f"<ActivityLog {self.id} - {self.user}>"


# Log Item Model
class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    order_id = db.Column(db.String(80), db.ForeignKey("order.id"), nullable=True)
    customer_name = db.Column(db.String(100), nullable=True)  # Store customer name here
    action = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)

    # Update relationships: Remove cascade="all, delete-orphan"
    user = db.relationship(
        "User", backref=db.backref("logs", lazy=True), passive_deletes=True
    )
    order = db.relationship("Order", backref=db.backref("logs", lazy=True))

    def __repr__(self):
        return f"<Log {self.id}>"


# Special Code Item Model
class SpecialCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    used = db.Column(
        db.Boolean, default=False
    )  # New column to track if the code is used

    def __repr__(self):
        return f"<SpecialCode {self.code}>"


# Create the database if it doesn't exist
with application.app_context():
    db.create_all()


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Registration Route
@application.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        hashed_password = generate_password_hash(password, method="pbkdf2:sha256")

        # Check if the username already exists
        user = User.query.filter_by(username=username).first()
        if user:
            flash("Username already exists!", "error")
            return redirect(url_for("register"))

        # Check if this is the first user (Admin role)
        if User.query.count() == 0:
            new_user = User(
                username=username, password=hashed_password, role=1
            )  # Admin user
            db.session.add(new_user)
            db.session.commit()
            flash("First admin user created successfully!", "success")
            return redirect(url_for("login"))

        # For normal users, check for a valid special code
        special_code = request.form.get("special_code")
        valid_code = SpecialCode.query.filter_by(code=special_code).first()

        if not valid_code:
            flash("Invalid or missing special code.", "error")
            return redirect(url_for("register"))

        # Check if the code has already been used
        if valid_code.used:
            flash("This special code has already been used.", "error")
            return redirect(url_for("register"))

        # If valid and unused, register the user and mark the code as used
        new_user = User(
            username=username, password=hashed_password, role=2
        )  # Normal user
        db.session.add(new_user)
        valid_code.used = True  # Mark the special code as used
        db.session.commit()

        flash("User created successfully!", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# Login Route
@application.route("/login", methods=["GET", "POST"])
def login():
    dark_mode = session.get("dark_mode", False)
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            if user.role == 1:
                return redirect(
                    url_for("admin_dashboard")
                )  # Redirect to admin dashboard
            else:
                return redirect(
                    url_for("index")
                )  # Redirect to home page for non-admin users
        else:
            flash("Incorrect username or password", "error")  # Flash an error message

    return render_template("login.html", dark_mode=dark_mode)


# Logout Route
@application.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# view orders
@application.route("/view_orders", methods=["GET", "POST"])
@login_required
def view_orders():
    filter_date = request.args.get("filter_date")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    orders = []

    dark_mode = session.get("dark_mode", False)
    # Check if a specific filter date is provided
    if filter_date:
        # Convert filter_date to string to match with delivery_date
        filter_date_str = datetime.strptime(filter_date, "%Y-%m-%d").strftime(
            "%Y-%m-%d"
        )
        orders = Order.query.filter_by(delivery_date=filter_date_str).all()

    # If start and end date are provided, filter for orders in that range
    elif start_date and end_date:
        try:
            # Convert start and end dates to datetime objects
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")

            # Convert them back to string format for comparison
            start_date_str = start_date_obj.strftime("%Y-%m-%d")
            end_date_str = end_date_obj.strftime("%Y-%m-%d")

            # Adjust filtering to include the start and end date as strings
            orders = Order.query.filter(
                Order.delivery_date >= start_date_str,  # Include the start date
                Order.delivery_date <= end_date_str,  # Include the end date
            ).all()

        except ValueError:
            flash("Invalid date format. Please use YYYY-MM-DD.")
            return redirect(url_for("view_orders"))

    # If no filters, no orders to show
    else:
        orders = []

    # Process orders for display
    formatted_orders = []
    for order in orders:
        item_counts = {
            "kg_5": 0,
            "kg_9": 0,
            "kg_14": 0,
            "kg_19": 0,
            "kg_19_flt": 0,
            "kg_48_sv": 0,
            "kg_48_dv": 0,
        }
        if order.order_items:
            for item in order.order_items.split(", "):
                try:
                    size, qty = item.split(" x ")
                    qty = int(qty)
                    if size == "5kg":
                        item_counts["kg_5"] = qty
                    elif size == "9kg":
                        item_counts["kg_9"] = qty
                    elif size == "14kg":
                        item_counts["kg_14"] = qty
                    elif size == "19kg":
                        item_counts["kg_19"] = qty
                    elif size == "19kgFLT":
                        item_counts["kg_19_flt"] = qty
                    elif size == "48kgSV":
                        item_counts["kg_48_sv"] = qty
                    elif size == "48kgDV":
                        item_counts["kg_48_dv"] = qty
                except ValueError:
                    continue
        user = User.query.get(order.user_id)
        formatted_orders.append(
            {
                "id": order.id,
                "customer_name": order.customer_name,
                "order_number": order.order_number,
                "kg_5": item_counts["kg_5"],
                "kg_9": item_counts["kg_9"],
                "kg_14": item_counts["kg_14"],
                "kg_19": item_counts["kg_19"],
                "kg_19_flt": item_counts["kg_19_flt"],
                "kg_48_sv": item_counts["kg_48_sv"],
                "kg_48_dv": item_counts["kg_48_dv"],
                "delivery_date": order.delivery_date,
                "status": order.status,
                "user": user.username if user else None,
                "comments": order.comments,
                "timestamp": order.timestamp,
            }
        )

    # Calculate totals only for the selected orders
    totals = {
        "5kg": sum(o["kg_5"] for o in formatted_orders),
        "9kg": sum(o["kg_9"] for o in formatted_orders),
        "14kg": sum(o["kg_14"] for o in formatted_orders),
        "19kg": sum(o["kg_19"] for o in formatted_orders),
        "19kg_flt": sum(o["kg_19_flt"] for o in formatted_orders),
        "48_sv": sum(o["kg_48_sv"] for o in formatted_orders),
        "48_dv": sum(o["kg_48_dv"] for o in formatted_orders),
    }

    total_weight = (
        totals["5kg"] * 5
        + totals["9kg"] * 9
        + totals["14kg"] * 14
        + totals["19kg"] * 19
        + totals["19kg_flt"] * 19
        + totals["48_sv"] * 48
        + totals["48_dv"] * 48
    )

    return render_template(
        "orders.html",
        dark_mode=dark_mode,
        formatted_orders=formatted_orders,
        filter_date=filter_date,
        start_date=start_date,
        end_date=end_date,
        totals=totals,
        total_weight=total_weight,
    )

#Index Home Page
@application.route("/", methods=["GET", "POST"])
@login_required
def index():
    per_page = request.args.get("per_page", session.get("per_page", 10), type=int)
    session["per_page"] = per_page
    dark_mode = session.get("dark_mode", False)

    if request.method == "POST":
        if current_user.role not in [0, 1]:  # Role 0 = normal, 1 = admin
            flash(
                "Your Role is Viewer Only. You do not have permission to place orders.",
                "danger",
            )
            return redirect(url_for("index"))

        customer_name = request.form["customer_name"].strip()
        sizes = request.form.getlist("size[]")
        quantities = request.form.getlist("quantity[]")
        order_number = request.form["order_number"]
        delivery_date_str = request.form["delivery_date"].strip()
        comments = request.form.get("comments", "").strip()

        # Combine sizes and quantities into a standardized format
        item_counts = {
            size: int(quantity)
            for size, quantity in zip(sizes, quantities)
            if int(quantity) > 0
        }
        order_items = ", ".join(
            f"{size} x {qty}" for size, qty in sorted(item_counts.items())
        )

        # Check for duplicate orders based on customer name, delivery date, and sizes/quantities
        existing_order = Order.query.filter(
            Order.customer_name.ilike(customer_name),
            Order.delivery_date == delivery_date_str,
            Order.order_items == order_items,
        ).first()

        if existing_order:
            # If a duplicate is found, set warning flag and pass details to template
            return render_template(
                "index.html",
                dark_mode=False,
                warning=True,
                customer_name=customer_name,
                sizes=sizes,
                quantities=quantities,
                order_items=order_items,
                delivery_date=delivery_date_str,
                comments=comments,
            )

        # Proceed with creating the order if it's not a duplicate
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        new_order = Order(
            id=order_id,
            customer_name=customer_name,
            order_items=order_items,
            order_number=order_number,
            comments=comments,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status="Not Invoiced",
            delivery_date=delivery_date_str,
            user_id=current_user.id,
        )
        db.session.add(new_order)
        db.session.commit()

        # Log the action
        log_entry = Log(
            user_id=current_user.id,
            order_id=new_order.id,
            customer_name=customer_name,
            action="Order Created",
        )
        db.session.add(log_entry)
        db.session.commit()

        flash("Order added successfully!", "success")
        socketio.emit("update", {"data": "New order has been added!"})
        return redirect(url_for("index"))

    # Preserve filter settings for pagination
    session["search"] = request.args.get("search", session.get("search", ""))
    session["status"] = request.args.get("status", session.get("status", ""))
    session["start_date"] = request.args.get("start_date", session.get("start_date", ""))
    session["end_date"] = request.args.get("end_date", session.get("end_date", ""))
    session["order_number"] = request.args.get("order_number", session.get("order_number", ""))
    session["user_id"] = request.args.get("user_id", session.get("user_id", ""))
    session["filter"] = request.args.get("filter", session.get("filter", ""))

    search_query = session["search"]
    status_filter = session["status"]
    start_date = session["start_date"]
    end_date = session["end_date"]
    order_number = session["order_number"]
    user_filter = session["user_id"]
    filter_value = session["filter"]

    query = Order.query

   # Get filter values from the request
    date_range = request.args.get("date_range", session.get("date_range", ""))
    start_date = request.args.get("start_date", session.get("start_date", ""))
    end_date = request.args.get("end_date", session.get("end_date", ""))
    
    session["date_range"] = date_range
    session["start_date"] = start_date
    session["end_date"] = end_date

    query = Order.query

    # Apply date filters based on selected range
    if date_range == "last_7_days":
        last_7_days = datetime.now() - timedelta(days=7)
        query = query.filter(Order.delivery_date >= last_7_days.date())

    elif date_range == "this_month":
        first_day_of_month = datetime.now().replace(day=1)
        query = query.filter(Order.delivery_date >= first_day_of_month)

    elif date_range == "custom_range":
        if start_date and end_date:
            try:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

                if start_date_obj > end_date_obj:
                    flash("Start date cannot be later than end date.", "danger")
                    return redirect(url_for("index"))

                query = query.filter(
                    Order.delivery_date >= start_date_obj,
                    Order.delivery_date <= end_date_obj,
                )
            except ValueError:
                flash("Invalid date format. Please use the correct format.", "danger")
                return redirect(url_for("index"))

    # Apply other filters
    if search_query:
        query = query.filter(Order.customer_name.ilike(f"%{search_query}%"))
    if status_filter:
        query = query.filter(Order.status == status_filter)
    if order_number:
        query = query.filter(Order.order_number.ilike(f"%{order_number}%"))
    if user_filter:
        query = query.filter(Order.user_id == user_filter)
    if filter_value == "flag":
        query = query.filter(Order.flag == True)
    elif filter_value == "meter":
        query = query.filter(Order.meter == True)

   # Sorting Logic
    sort_desc = session.get("sort_desc", False)
    if sort_desc:
        query = query.order_by(Order.delivery_date.desc())
    else:
        query = query.order_by(Order.delivery_date.asc())
        
    # Pagination
    page = request.args.get("page", 1, type=int)
    orders_paginated = query.options(joinedload(Order.user)).paginate(
        page=page, per_page=per_page, error_out=False
    )

    orders = orders_paginated.items

    # Format orders
    formatted_orders = []
    for order in orders:
        item_counts = {
            "kg_5": 0,
            "kg_9": 0,
            "kg_14": 0,
            "kg_19": 0,
            "kg_19_flt": 0,
            "kg_48_sv": 0,
            "kg_48_dv": 0,
        }
        if order.order_items:
            for item in order.order_items.split(", "):
                try:
                    size, qty = item.split(" x ")
                    qty = int(qty)
                    if size == "5kg":
                        item_counts["kg_5"] = qty
                    elif size == "9kg":
                        item_counts["kg_9"] = qty
                    elif size == "14kg":
                        item_counts["kg_14"] = qty
                    elif size == "19kg":
                        item_counts["kg_19"] = qty
                    elif size == "19kgFLT":
                        item_counts["kg_19_flt"] = qty
                    elif size == "48kgSV":
                        item_counts["kg_48_sv"] = qty
                    elif size == "48kgDV":
                        item_counts["kg_48_dv"] = qty
                except ValueError:
                    continue

        user = User.query.get(order.user_id)
        formatted_orders.append(
            {
                "id": order.id,
                "customer_name": order.customer_name,
                "order_number": order.order_number,
                "kg_5": item_counts["kg_5"],
                "kg_9": item_counts["kg_9"],
                "kg_14": item_counts["kg_14"],
                "kg_19": item_counts["kg_19"],
                "kg_19_flt": item_counts["kg_19_flt"],
                "kg_48_sv": item_counts["kg_48_sv"],
                "kg_48_dv": item_counts["kg_48_dv"],
                "delivery_date": order.delivery_date,
                "status": order.status,
                "user": user.username if user else None,
                "comments": order.comments,
                "timestamp": order.timestamp,
            }
        )

    users = User.query.all()

    # Preserve filters when paginating
    next_url = (
        url_for(
            "index",
            page=orders_paginated.next_num,
            per_page=per_page,
            search=search_query,
            status=status_filter,
            order_number=order_number,
            start_date=start_date,
            end_date=end_date,
            user_id=user_filter,
            filter=filter_value,
        )
        if orders_paginated.has_next
        else None
    )

    prev_url = (
        url_for(
            "index",
            page=orders_paginated.prev_num,
            per_page=per_page,
            search=search_query,
            status=status_filter,
            order_number=order_number,
            start_date=start_date,
            end_date=end_date,
            user_id=user_filter,
            filter=filter_value,
        )
        if orders_paginated.has_prev
        else None
    )

    return render_template(
        "index.html",
        dark_mode=dark_mode,
        orders=formatted_orders,
        users=users,
        search_query=search_query,
        status_filter=status_filter,
        order_number=order_number,
        start_date=start_date,
        end_date=end_date,
        user_filter=user_filter,
        next_url=next_url,
        prev_url=prev_url,
        per_page=per_page,
    )

#Confirm Duplicate Route
@application.route("/confirm_duplicate", methods=["POST"])
@login_required
def confirm_duplicate():
    customer_name = request.form["customer_name"].strip()
    order_number = request.form["order_number"].strip()
    delivery_date_str = request.form["delivery_date"].strip()
    comments = request.form.get("comments", "").strip()

    # Fetch the existing order based on customer_name, order_number, and delivery_date
    existing_order = Order.query.filter_by(
        customer_name=customer_name,
        order_number=order_number,
        delivery_date=delivery_date_str,
    ).first()

    if not existing_order:
        flash("No matching order found to duplicate.", "danger")
        return redirect(url_for("index"))

    # Create a new order by duplicating the existing order
    new_order = Order(
        id=f"ORD-{uuid.uuid4().hex[:8].upper()}",  # Assign a new unique ID
        customer_name=existing_order.customer_name,
        order_number="Duplicate Order",  # Adjust this as necessary
        order_items=existing_order.order_items,
        comments=comments
        or existing_order.comments,  # Use the new comments or the existing one
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        status="Not Invoiced",
        delivery_date=delivery_date_str,  # Use the provided delivery date
        user_id=current_user.id,
        kg_5=existing_order.kg_5,
        kg_9=existing_order.kg_9,
        kg_14=existing_order.kg_14,
        kg_19=existing_order.kg_19,
        kg_19_flt=existing_order.kg_19_flt,
        kg_48_sv=existing_order.kg_48_sv,
        kg_48_dv=existing_order.kg_48_dv,
    )

    try:
        # Add the new order to the database
        db.session.add(new_order)
        db.session.commit()

        # Log the action
        log_entry = Log(
            user_id=current_user.id,
            order_id=new_order.id,
            customer_name=customer_name,
            action="Duplicate Order Created",
        )
        db.session.add(log_entry)
        db.session.commit()

        flash("Duplicate order created successfully!", "success")
        return redirect(url_for("index"))
    except Exception as e:
        flash(f"Error creating duplicate order: {str(e)}", "danger")
        return redirect(url_for("index"))


# DarK Mode
@application.route("/toggle-dark-mode")
def toggle_dark_mode():
    # Toggle the dark_mode session variable
    session["dark_mode"] = not session.get("dark_mode", False)

    # Check if user is an admin and redirect accordingly
    if current_user.is_authenticated and current_user.role == 1:
        # If the user is an admin, redirect them back to the admin dashboard
        return redirect(url_for("index"))

    # If user is not an admin, redirect them back to the home page
    return redirect(request.referrer or url_for("index"))


# Sort Orders
@application.route("/sort_orders", methods=["POST"])
@login_required
def sort_orders():
    # Toggle sorting order and store it in the session
    sort_desc = session.get("sort_desc", False)
    sort_desc = not sort_desc  # Toggle between ascending and descending
    session["sort_desc"] = sort_desc

    # Retrieve previous filters from session
    search_query = session.get("search", "")
    status_filter = session.get("status", "")
    start_date = session.get("start_date", "")
    order_number = session.get("order_number", "")
    user_filter = session.get("user_id", "")
    filter_value = session.get("filter", "")

    # Keep dark mode and pagination settings
    dark_mode = session.get("dark_mode", False)
    per_page = session.get("per_page", 10)
    page = request.args.get("page", 1, type=int)

    # Start with a query that respects previous filters
    query = Order.query

    # Apply filters to the query
    if search_query:
        query = query.filter(Order.customer_name.ilike(f"%{search_query}%"))
    if status_filter:
        query = query.filter(Order.status == status_filter)
    if order_number:
        query = query.filter(Order.order_number.ilike(f"%{order_number}%"))
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
            next_day = (start_date_obj + timedelta(days=1)).strftime("%Y-%m-%d")
            query = query.filter(
                Order.delivery_date >= start_date_obj.strftime("%Y-%m-%d"),
                Order.delivery_date < next_day,
            )
        except ValueError:
            pass
    if user_filter:
        query = query.filter(Order.user_id == user_filter)
    if filter_value == "flag":
        query = query.filter(Order.flag == True)
    elif filter_value == "meter":
        query = query.filter(Order.meter == True)

    # Dynamic Sorting
    sort_field = request.form.get(
        "sort_field", "delivery_date"
    )  # Default sorting by delivery_date
    if sort_desc:
        query = query.order_by(
            getattr(Order, sort_field).desc()
        )  # Sort in descending order
    else:
        query = query.order_by(
            getattr(Order, sort_field).asc()
        )  # Sort in ascending order

    # Apply pagination to the filtered and sorted query
    orders_paginated = query.options(joinedload(Order.user)).paginate(
        page=page, per_page=per_page, error_out=False
    )

    orders = orders_paginated.items

    # Format orders with item counts and user details
    formatted_orders = []
    for order in orders:
        item_counts = {
            "kg_5": 0,
            "kg_9": 0,
            "kg_14": 0,
            "kg_19": 0,
            "kg_19_flt": 0,
            "kg_48_sv": 0,
            "kg_48_dv": 0,
        }

        # Extract item quantities from the order's order_items field
        if order.order_items:
            for item in order.order_items.split(", "):
                try:
                    size, qty = item.split(" x ")
                    qty = int(qty)
                    if size == "5kg":
                        item_counts["kg_5"] = qty
                    elif size == "9kg":
                        item_counts["kg_9"] = qty
                    elif size == "14kg":
                        item_counts["kg_14"] = qty
                    elif size == "19kg":
                        item_counts["kg_19"] = qty
                    elif size == "19kgFLT":
                        item_counts["kg_19_flt"] = qty
                    elif size == "48kgSV":
                        item_counts["kg_48_sv"] = qty
                    elif size == "48kgDV":
                        item_counts["kg_48_dv"] = qty
                except ValueError:
                    continue

        # Get the user details for this order
        user = User.query.get(order.user_id)

        # Append formatted order with item counts
        formatted_orders.append(
            {
                "id": order.id,
                "customer_name": order.customer_name,
                "order_number": order.order_number,
                "kg_5": item_counts["kg_5"],
                "kg_9": item_counts["kg_9"],
                "kg_14": item_counts["kg_14"],
                "kg_19": item_counts["kg_19"],
                "kg_19_flt": item_counts["kg_19_flt"],
                "kg_48_sv": item_counts["kg_48_sv"],
                "kg_48_dv": item_counts["kg_48_dv"],
                "delivery_date": order.delivery_date,
                "status": order.status,
                "user": user.username if user else None,
                "comments": order.comments,
                "timestamp": order.timestamp,
            }
        )

    users = User.query.all()

    return render_template(
        "index.html",
        dark_mode=dark_mode,
        orders=formatted_orders,
        users=users,
        search_query=search_query,
        status_filter=status_filter,
        order_number=order_number,
        start_date=start_date,
        user_filter=user_filter,
        sort_desc=sort_desc,
        per_page=per_page,
        next_url=url_for("index", page=orders_paginated.next_num)
        if orders_paginated.has_next
        else None,
        prev_url=url_for("index", page=orders_paginated.prev_num)
        if orders_paginated.has_prev
        else None,
    )


# Reset Filters
@application.route("/reset_filters", methods=["POST"])
def reset_filters():
    session.pop("search", None)
    session.pop("status", None)
    session.pop("start_date", None)
    session.pop("order_number", None)
    session.pop("user_id", None)
    session.pop("filter", None)
    return "", 200  # Return empty response


# Invoiced (Marking)
@application.route("/invoiced/<order_id>", methods=["POST"])
@login_required
def invoiced(order_id):
    if request.method == "POST":
        if current_user.role not in [0, 1]:  # Assuming role 0 is normal and 1 is admin
            flash(
                "Your Role is Viewer Only. You do not have permission to mark orders.",
                "danger",
            )
            return redirect(url_for("orders"))

    order = Order.query.get(order_id)

    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("orders"))

    # **Prevent marking as invoiced if the order is flagged**
    if order.flag:
        flash("Cannot mark order as invoiced. Order is Flagged!", "danger")
        return redirect(url_for("index"))

    # **Proceed with marking as invoiced if no flag is set**
    order.status = "Invoiced"
    db.session.commit()

    flash("Order marked as invoiced successfully!", "success")

    # Log the action
    log_entry = Log(
        user_id=current_user.id,
        order_id=order.id,
        action="Marked as Invoice",
        customer_name=order.customer_name,
    )
    db.session.add(log_entry)
    db.session.commit()

    # Redirect to orders page while preserving filters
    return redirect(
        url_for(
            "index",
            date_range=session.get("date_range", ""),
            start_date=session.get("start_date", ""),
            end_date=session.get("end_date", ""),
        )
    )


# Not Invoiced (Marking)
@application.route("/not_invoiced/<string:order_id>", methods=["GET", "POST"])
@login_required
def not_invoiced(order_id):

    dark_mode = session.get("dark_mode", False)
    order = Order.query.get(order_id)

    if not order:
        flash("Order not found.", "danger")
        return redirect(
            url_for(
                "orders",
                date_range=session.get("date_range", ""),
                start_date=session.get("start_date", ""),
                end_date=session.get("end_date", ""),
            )
        )

    if request.method == "POST":
        password_input = request.form.get("password")

        if current_user.role == 1 and check_password_hash(
            current_user.password, password_input
        ):
            order.status = "Not Invoiced"
            db.session.commit()
            flash("Order status set to Not Invoiced.", "success")
            return redirect(
                url_for(
                    "index",
                    date_range=session.get("date_range", ""),
                    start_date=session.get("start_date", ""),
                    end_date=session.get("end_date", ""),
                )
            )
        else:
            flash("Incorrect password.", "danger")

    # Log the action
    log_entry = Log(
        user_id=current_user.id,
        order_id=order.id,
        action="Marked as Not Invoice",
        customer_name=order.customer_name,
    )
    db.session.add(log_entry)
    db.session.commit()

    # Render template with order details
    return render_template("not_invoiced.html", dark_mode=dark_mode, order=order)


# Delete Order Route
@application.route("/delete_confirm/<order_id>", methods=["GET", "POST"])
@login_required
def delete_confirm(order_id):
    dark_mode = session.get("dark_mode", False)

    order = Order.query.filter_by(
        id=order_id
    ).first()  # Get the order once at the start

    if request.method == "POST":
        password_input = request.form.get("password")

        # Check if the entered password matches the user's stored password
        if current_user.role == 1 and check_password_hash(
            current_user.password, password_input
        ):
            if order:
                # If password is correct and order exists, delete the order
                db.session.delete(order)
                db.session.commit()

                # Log the action AFTER the order is deleted
                log_entry = Log(
                    user_id=current_user.id,
                    action="Order Deleted",
                    customer_name=order.customer_name,
                )
                db.session.add(log_entry)
                db.session.commit()

                flash("Order deleted successfully", "success")
            else:
                flash("Order not found", "danger")
            return redirect(url_for("index"))

        else:
            flash("Incorrect password", "danger")
            return redirect(url_for("delete_confirm", order_id=order_id))

    # If it's a GET request, render the page to confirm password
    return render_template(
        "confirm_delete.html", dark_mode=dark_mode, order_id=order_id
    )


@application.route("/update_flag_meter/<string:order_id>", methods=["POST"])
def update_flag_meter(order_id):
    if request.method == "POST":
        if current_user.role not in [0, 1]:  # Assuming role 0 is normal and 1 is admin
            flash(
                "Your Role is Viewer Only. You do not have permission to modify orders.",
                "danger",
            )
            return redirect(url_for("index"))

        order = Order.query.filter_by(id=order_id).first()

        if not order:
            flash("Order not found.", "danger")
            return redirect(url_for("index"))

        flag = "flag" in request.form
        meter = "meter" in request.form
        invoiced = (
            "invoiced" in request.form
        )  # Check if the invoiced checkbox is in the form

        # Prevent marking as invoiced if the order is flagged
        if order.flag and invoiced:
            flash("Cannot mark order as invoiced when the Flag is set.", "danger")
            return redirect(url_for("index"))

        # Update the order fields
        order.flag = flag
        order.meter = meter
        order.invoiced = invoiced  # Ensure 'invoiced' status is being updated properly

        db.session.commit()

        # Log the activity
        action = "Flag and Meter cleared"
        if flag:
            action = "Order Flagged"
        if meter:
            action = "Meter Marked"
        if invoiced:
            action = "Order Marked as Invoiced"

        log_entry = Log(
            user_id=current_user.id,
            order_id=order.id,
            action=action,
            customer_name=order.customer_name,
        )

        db.session.add(log_entry)
        db.session.commit()

        flash("Updated successfully!", "success")

    return redirect(
        url_for(
            "index",
            date_range=session.get("date_range", ""),
            start_date=session.get("start_date", ""),
            end_date=session.get("end_date", ""),
        )
    )


@application.route("/copy_order/<string:order_id>", methods=["POST"])
@login_required
def copy_order(order_id):
    # Retrieve the original order to copy
    original_order = Order.query.filter_by(id=order_id).first()
    if not original_order:
        flash("Original order not found. Cannot create duplicate.", "danger")
        return redirect(url_for("index"))

    # Create a new order by duplicating the original order's details
    new_order = Order(
        id=f"ORD-{uuid.uuid4().hex[:8].upper()}",  # Generate a new unique ID
        customer_name=original_order.customer_name,
        order_number=f"{original_order.order_number}-COPY",  # Adjust order number
        order_items=original_order.order_items,
        comments=original_order.comments,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        status="Not Invoiced",
        delivery_date=original_order.delivery_date,
        user_id=current_user.id,
        # Duplicate other necessary fields as well
        kg_5=original_order.kg_5,
        kg_9=original_order.kg_9,
        kg_14=original_order.kg_14,
        kg_19=original_order.kg_19,
        kg_19_flt=original_order.kg_19_flt,
        kg_48_sv=original_order.kg_48_sv,
        kg_48_dv=original_order.kg_48_dv,
    )

    try:
        # Add the new order to the database
        db.session.add(new_order)
        db.session.commit()

        # Log the action of duplicating the order
        log_entry = Log(
            user_id=current_user.id,
            order_id=new_order.id,
            customer_name=new_order.customer_name,
            action="Duplicate Order Created",
        )
        db.session.add(log_entry)
        db.session.commit()

        flash("Duplicate order created successfully!", "success")

        # Redirect back to the index (order book)
        return redirect(url_for("index"))

    except Exception as e:
        flash(f"Error copying order: {str(e)}", "danger")
        return redirect(url_for("index"))

#Edit Order Route
@application.route("/edit_order/<string:order_id>", methods=["GET", "POST"])
@login_required
def edit_order(order_id):
    # Ensure order_id is treated as a string
    order = Order.query.filter_by(id=order_id).first_or_404()
    dark_mode = session.get("dark_mode", False)

    if request.method == "POST":
        if current_user.role not in [0, 1]:  # Assuming role 0 is normal and 1 is admin
            flash(
                "Your Role is Viewer Only. You do not have permission to edit orders.",
                "danger",
            )
            return redirect(url_for("index"))

        try:
            customer_name = request.form["customer_name"]
            sizes = request.form.getlist("size[]")
            quantities = request.form.getlist("quantity[]")
            order_items = [
                f"{size} x {quantity}" for size, quantity in zip(sizes, quantities)
            ]
            order_number = request.form.get("order_number")
            comments = request.form.get("comments", "").strip()
            status = request.form.get("status", order.status).strip()

            # Handle delivery date
            delivery_date_str = request.form.get("delivery_date", "").strip()
            delivery_date = None
            if delivery_date_str:
                try:
                    delivery_date = datetime.strptime(delivery_date_str, "%Y-%m-%d")
                except ValueError:
                    flash(
                        "Invalid delivery date format. Please use YYYY-MM-DD.", "danger"
                    )
                    return redirect(url_for("edit_order", order_id=order_id))

            # Check for duplicate order
            existing_order = Order.query.filter(
                Order.customer_name.ilike(customer_name),
                Order.delivery_date == delivery_date_str,
                Order.order_items == ", ".join(order_items),
            ).first()

            if existing_order:
                # If duplicate is found, show the modal without making any changes
                return render_template(
                    "edit.html",
                    dark_mode=dark_mode,
                    order=order,
                    show_modal=True,
                    customer_name=customer_name,
                    order_items=", ".join(order_items),
                    delivery_date=delivery_date_str,
                )

            # If no duplicate, proceed to update the order
            order.customer_name = customer_name
            order.order_items = ", ".join(order_items)
            order.order_number = order_number
            order.comments = comments
            order.status = status
            if delivery_date:
                order.delivery_date = delivery_date.strftime("%Y-%m-%d")

            db.session.commit()
            flash("Order updated successfully!", "success")

            # Log the action
            log_entry = Log(
                user_id=current_user.id,
                order_id=order.id,
                action="Order Edited",
                customer_name=order.customer_name,
            )
            db.session.add(log_entry)
            db.session.commit()

        except Exception as e:
            flash(f"Error updating order: {str(e)}", "danger")

        return redirect(url_for("index"))

    return render_template("edit.html", dark_mode=dark_mode, order=order)


import csv
from io import StringIO
from flask import Response

# Generate CSV DOC
@application.route("/generate", methods=["GET"])
@login_required
def generate():
    # Fetch orders from the database
    orders = Order.query.all()

    # Create a CSV in memory
    output = StringIO()
    csv_writer = csv.writer(output)

    # Write the header
    csv_writer.writerow(
        [
            "Order ID",
            "Customer Name",
            "Order Number",
            "5kg",
            "9kg",
            "14kg",
            "19kg",
            "19kg FLT",
            "48kg SV",
            "48kg DV",
            "Delivery Date",
            "Status",
        ]
    )

    # Write the order data
    for order in orders:
        # Prepare the size and quantity columns
        item_counts = {
            "kg_5": 0,
            "kg_9": 0,
            "kg_14": 0,
            "kg_19": 0,
            "kg_19_flt": 0,
            "kg_48_sv": 0,
            "kg_48_dv": 0,
        }

        # Parse the order_items
        if order.order_items:
            items = order.order_items.split(", ")
            for item in items:
                size, qty = item.split(" x ")
                qty = int(qty)
                if size == "5kg":
                    item_counts["kg_5"] = qty
                elif size == "9kg":
                    item_counts["kg_9"] = qty
                elif size == "14kg":
                    item_counts["kg_14"] = qty
                elif size == "19kg":
                    item_counts["kg_19"] = qty
                elif size == "19kgFLT":
                    item_counts["kg_19_flt"] = qty
                elif size == "48kgSV":
                    item_counts["kg_48_sv"] = qty
                elif size == "48kgDV":
                    item_counts["kg_48_dv"] = qty
        # Write the order data to the CSV row
        csv_writer.writerow(
            [
                order.id,
                order.customer_name,
                order.order_number,
                item_counts["kg_5"],
                item_counts["kg_9"],
                item_counts["kg_14"],
                item_counts["kg_19"],
                item_counts["kg_19_flt"],
                item_counts["kg_48_sv"],
                item_counts["kg_48_dv"],
                order.delivery_date,
                order.status,
            ]
        )
    # Prepare the response as a CSV file for download
    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=orders_report.csv"},
    )


# Export Orders CSV - Specialized
@application.route("/export_orders", methods=["GET"])
@login_required
def export_orders():
    # Fetch the filtered orders based on the current filters (same as view_orders)
    filter_date = request.args.get("filter_date")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    orders = []

    # Check if a specific filter date is provided
    if filter_date:
        try:
            # Convert the filter_date string to a datetime object
            filter_date_obj = datetime.strptime(
                filter_date, "%Y-%m-%d"
            ).date()  # Ensure it's a date object
            orders = Order.query.filter_by(delivery_date=filter_date_obj).all()
        except ValueError:
            flash(
                "Invalid date format for filter_date. Please use YYYY-MM-DD.", "danger"
            )
            return redirect(url_for("export_orders"))

    # If start and end date are provided, filter for orders in that range
    elif start_date and end_date:
        try:
            # Ensure start_date and end_date are not None before trying to convert them
            if start_date and end_date:
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

                # Adjust filtering to include the start and end date based on the delivery_date
                orders = Order.query.filter(
                    Order.delivery_date >= start_date_obj,  # Include the start date
                    Order.delivery_date <= end_date_obj,  # Include the end date
                ).all()
            else:
                flash("Start date or end date is missing.", "danger")
                return redirect(url_for("export_orders"))

        except ValueError:
            flash(
                "Invalid date format for start or end date. Please use YYYY-MM-DD.",
                "danger",
            )
            return redirect(url_for("export_orders"))

    # If no filter is applied, fetch all orders
    else:
        orders = Order.query.all()

    # Process orders for export
    data = []
    for order in orders:
        item_counts = {
            "kg_5": 0,
            "kg_9": 0,
            "kg_14": 0,
            "kg_19": 0,
            "kg_19_flt": 0,
            "kg_48_sv": 0,
            "kg_48_dv": 0,
        }
        if order.order_items:
            for item in order.order_items.split(", "):
                try:
                    size, qty = item.split(" x ")
                    qty = int(qty)
                    if size == "5kg":
                        item_counts["kg_5"] = qty
                    elif size == "9kg":
                        item_counts["kg_9"] = qty
                    elif size == "14kg":
                        item_counts["kg_14"] = qty
                    elif size == "19kg":
                        item_counts["kg_19"] = qty
                    elif size == "19kgFLT":
                        item_counts["kg_19_flt"] = qty
                    elif size == "48kgSV":
                        item_counts["kg_48_sv"] = qty
                    elif size == "48kgDV":
                        item_counts["kg_48_dv"] = qty
                except ValueError:
                    continue
        user = User.query.get(order.user_id)

        data.append(
            {
                "Customer Name": order.customer_name,
                "Order Number": order.order_number,
                "5kg": item_counts["kg_5"],
                "9kg": item_counts["kg_9"],
                "14kg": item_counts["kg_14"],
                "19kg": item_counts["kg_19"],
                "19kgFLT": item_counts["kg_19_flt"],
                "48kgSV": item_counts["kg_48_sv"],
                "48kgDV": item_counts["kg_48_dv"],
                "Delivery Date": order.delivery_date,
                "Status": order.status,
                "User": user.username if user else None,
                "Comments": order.comments,
                "Timestamp": order.timestamp,
            }
        )

    # Create a pandas DataFrame
    df = pd.DataFrame(data)

    # Save the DataFrame to a BytesIO object
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Orders")
    output.seek(0)

    # Log the activity of exporting the report
    log_entry = Log(
        user_id=current_user.id,
        action="Exported Orders Report",
        # details=f"Filtered from {start_date} to {end_date} (if provided)",
    )
    db.session.add(log_entry)
    db.session.commit()

    # Return the Excel file as a response
    return send_file(
        output,
        as_attachment=True,
        download_name="filtered_orders.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# Activity Log
@application.route("/activity_log", methods=["GET", "POST"])
@login_required
def activity_log():
    # Only allow admins to view the activity log
    if current_user.role != 1:
        flash("Access denied: Admins only!", "danger")
        return redirect(url_for("index"))

    dark_mode = session.get("dark_mode", False)

    # Handle POST request for sorting
    if request.method == "POST":
        # Toggle the sorting order on button click
        if session.get("sort_desc", True):
            session["sort_desc"] = False  # Ascending order
        else:
            session["sort_desc"] = True  # Descending order

    # Check for reset action
    reset = request.args.get("reset", False)
    if reset:
        session.pop("user_filter", None)
        session.pop("date_range", None)
        session.pop("start_date", None)
        session.pop("end_date", None)
        session.pop("sort_desc", None)
        return redirect(
            url_for("activity_log")
        )  # Reset and redirect to the activity log page without filters

    # Retrieve filters from session or use query parameters if available
    user_filter = request.args.get("user", session.get("user_filter"))
    date_range = request.args.get("date_range", session.get("date_range"))
    start_date, end_date = None, None

    # Date Range Filters
    if date_range == "last_7_days":
        start_date = datetime.now() - timedelta(days=7)
        end_date = datetime.now()
    elif date_range == "this_month":
        start_date = datetime(datetime.now().year, datetime.now().month, 1)
        end_date = datetime.now()
    elif date_range == "custom_range":
        start_date_str = request.args.get("start_date", session.get("start_date"))
        end_date_str = request.args.get("end_date", session.get("end_date"))

        def parse_date(date_str):
            try:
                return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return datetime.strptime(date_str, "%Y-%m-%d")

        if start_date_str:
            start_date = parse_date(start_date_str)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        if end_date_str:
            end_date = parse_date(end_date_str)
            end_date = end_date.replace(
                hour=23, minute=59, second=59, microsecond=999999
            )

    # Save filters to session for future use
    if user_filter:
        session["user_filter"] = user_filter
    if date_range:
        session["date_range"] = date_range
    if start_date:
        session["start_date"] = start_date.strftime(
            "%Y-%m-%d"
        )  # Save the formatted date string
    if end_date:
        session["end_date"] = end_date.strftime(
            "%Y-%m-%d"
        )  # Save the formatted date string

    # Get the sort order (ascending or descending) from session
    sort_desc = session.get("sort_desc", True)  # Default to descending if not set

    # Query the logs based on filters
    query = Log.query
    if user_filter:
        query = query.filter_by(user_id=int(user_filter))
    if start_date and end_date:
        query = query.filter(Log.timestamp >= start_date, Log.timestamp <= end_date)

    # Apply sorting based on the sort_desc value (ascending or descending)
    if sort_desc:
        query = query.order_by(Log.timestamp.desc())  # Descending order
    else:
        query = query.order_by(Log.timestamp.asc())  # Ascending order

    # Paginate the results
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    activity_logs = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get the pagination links with filters
    next_url = (
        url_for(
            "activity_log",
            page=activity_logs.next_num,
            per_page=per_page,
            user=user_filter,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
        )
        if activity_logs.has_next
        else None
    )
    prev_url = (
        url_for(
            "activity_log",
            page=activity_logs.prev_num,
            per_page=per_page,
            user=user_filter,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
        )
        if activity_logs.has_prev
        else None
    )

    # Retrieve all users for the filter dropdown
    users = User.query.order_by(User.username).all()

    return render_template(
        "activity_log.html",
        dark_mode=dark_mode,
        logs=activity_logs.items,
        users=users,
        user_filter=user_filter,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
        next_url=next_url,
        prev_url=prev_url,
        per_page=per_page,
        sort_desc=sort_desc,  # Pass the sorting state to the template
    )


# Log when a user logs in
@user_logged_in.connect
def log_user_login(sender, user, **extra):
    log_entry = Log(
        user_id=user.id,  # Use the 'user' parameter, not sender.id
        action="Logged In",
        order_id=None,  # No specific order related to login
    )
    db.session.add(log_entry)
    db.session.commit()


# Log when a user logs out
@user_logged_out.connect
def log_user_logout(sender, user, **extra):
    log_entry = Log(
        user_id=user.id,  # Use the 'user' parameter
        action="Logged Out",
        order_id=None,
    )
    db.session.add(log_entry)
    db.session.commit()


# This is the WebSocket route that the client will connect to
@socketio.on("message")
def handle_message(data):
    print("Received message: " + data)
    emit("response", {"data": "Message received!"})


# To send real-time updates to clients, create a function like this:
@application.route("/send_update")
def send_update():
    # You can send a message to all connected clients
    socketio.emit("update", {"data": "This is a real-time update!"})
    return "Update sent!"


# Admin Dashboard
@application.route("/admin/dashboard")
@login_required
def admin_dashboard():
    if current_user.role != 1:
        flash("You do not have permission to view this page.", "error")
        return redirect(url_for("index"))

    dark_mode = session.get("dark_mode", False)

    # Check if a specific filter date is provided
    users = User.query.all()  # Fetch all users

    # If you want to highlight the current user in the dashboard:
    current_user_info = (
        current_user  # This will give you the current logged-in user object
    )

    return render_template(
        "admin_dashboard.html",
        dark_mode=dark_mode,
        users=users,
        current_user_info=current_user_info,
    )


# Function to generate random special code
def generate_random_code():
    code_length = 8  # Length of the special code
    characters = string.ascii_letters + string.digits
    return "".join(random.choice(characters) for i in range(code_length))


# Generate Special Code (Used For Registration New User )
@application.route("/generate_code", methods=["GET", "POST"])
@login_required
def generate_code():
    if current_user.role != 1:  # Only admins can generate codes
        flash("Unauthorized action!", "error")
        return redirect(url_for("index"))

    dark_mode = session.get("dark_mode", False)

    # Generate a random special code (e.g., 6 characters long)
    new_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

    # Store the generated code in the database
    special_code = SpecialCode(code=new_code)
    db.session.add(special_code)
    db.session.commit()

    # Log the activity of generating the code
    log_entry = Log(
        user_id=current_user.id,
        action="Special Code Generated",
    )
    db.session.add(log_entry)
    db.session.commit()

    return render_template(
        "admin_dashboard.html", dark_mode=dark_mode, special_code=new_code
    )


# View all generated codes (Admin only)
@application.route("/view_codes")
@login_required
def view_codes():
    if current_user.role != 1:  # Only admins can view the codes
        flash("Unauthorized action!", "error")
        return redirect(url_for("index"))
    dark_mode = session.get("dark_mode", False)

    # Fetch all special codes and their usage status
    codes = SpecialCode.query.all()

    return render_template("view_codes.html", dark_mode=dark_mode, codes=codes)


# Change Role
@application.route("/admin/change_role/<int:user_id>", methods=["GET", "POST"])
@login_required
def change_role(user_id):
    if current_user.role != 1:  # Only admins can change roles
        flash("You do not have permission to view this page.", "error")
        return redirect(url_for("index"))
    dark_mode = session.get("dark_mode", False)

    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        admin_password = request.form["admin_password"]  # Admin password from the form

        # Check if the admin entered the correct password
        if not check_password_hash(current_user.password, admin_password):
            flash("Incorrect admin password. Role update failed.", "error")
            return redirect(url_for("change_role", user_id=user.id))

        # Role is correct, update the role
        new_role = int(request.form["role"])  # Convert to integer for the role
        user.role = new_role
        db.session.commit()
        flash("User role updated successfully!", "success")
        return redirect(url_for("admin_dashboard"))
    log_entry = Log(
        user_id=user.id,  # Use the 'user' parameter
        action="Role Changed",
        order_id=None,
    )
    db.session.add(log_entry)
    db.session.commit()

    return render_template("change_role.html", dark_mode=dark_mode, user=user)


# Delete User
@application.route("/admin/delete_user/<int:user_id>", methods=["GET", "POST"])
@login_required
def delete_user(user_id):
    if current_user.role != 1:
        flash("You do not have permission to perform this action.", "error")
        return redirect(url_for("index"))
    dark_mode = session.get("dark_mode", False)

    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        admin_password = request.form["admin_password"]
        if not check_password_hash(current_user.password, admin_password):
            flash("Incorrect admin password.", "error")
            return redirect(url_for("delete_user", user_id=user_id))
        # Before deleting the user, set logs' user_id to None
        Log.query.filter_by(user_id=user.id).update({Log.user_id: None})
        db.session.commit()

        # Now delete the user
        db.session.delete(user)
        db.session.commit()

        flash(f"User {user.username} deleted successfully!", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("delete_user.html", dark_mode=dark_mode, user=user)


# Currently Not Using - Seems Redundant (Future Implementation)
ALLOWED_EXTENSIONS = {"xls", "xlsx"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

'''
@application.route("/upload_excel", methods=["POST"])
@login_required
def upload_excel():
    if "file" not in request.files:
        return redirect(request.url)

    file = request.files["file"]

    if file.filename == "":
        return redirect(request.url)

    if file and allowed_file(file.filename):
        # Secure the filename
        filename = secure_filename(file.filename)

        # Save the file to the upload folder
        file_path = os.path.join("uploads", filename)
        file.save(file_path)

        # Process the Excel file
        data = pd.read_excel(file_path)

        # Remove leading/trailing spaces from the column names
        data.columns = data.columns.str.strip()

        for index, row in data.iterrows():
            try:
                # Ensure the order ID is unique
                order_id = str(uuid.uuid4())  # Generate a new unique ID for the order

                # Check if the required fields are present in the row, and use default if not
                order = Order(
                    id=order_id,
                    customer_name=row.get("customer_name", ""),
                    order_items=row.get("order_items", ""),
                    order_number=row.get("order_number", ""),
                    comments=row.get("comments", ""),
                    timestamp=row.get("timestamp", ""),
                    status=row.get("status", "Not Invoiced"),
                    delivery_date=row.get("delivery_date", ""),
                    user_id=row.get("user_id", None),  # Assuming 'user_id' is available
                    kg_5=row.get("kg_5", 0),
                    kg_9=row.get("kg_9", 0),
                    kg_14=row.get("kg_14", 0),
                    kg_19=row.get("kg_19", 0),
                    kg_19_flt=row.get("kg_19_flt", 0),
                    kg_48_sv=row.get("kg_48_sv", 0),
                    kg_48_dv=row.get("kg_48_dv", 0),
                    flag=row.get("flag", False),
                    meter=row.get("meter", False),
                    order_date=datetime.utcnow(),  # Set the order date to the current date
                    priority=row.get("priority", False),
                    row_color=row.get(
                        "row_color", None
                    ),  # Assuming 'row_color' is optional
                )

                # Debugging line to check order data
                print(f"Adding order {order.order_number}")
                db.session.add(order)

            except Exception as e:
                print(f"Error processing row {index}: {e}")
                continue

        try:
            db.session.commit()
            print("Database updated successfully")
        except Exception as e:
            print(f"Error during commit: {e}")
            db.session.rollback()  # Rollback the transaction in case of error

        return redirect(url_for("index"))  # Redirect back to the main page

    return "File not allowed", 400
'''

#User Profile Route
@application.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    dark_mode = session.get("dark_mode", False)

    if (
        not current_user.full_name
        or not current_user.surname
        or not current_user.email
        or not current_user.cell_number
    ):
        # Redirect to the edit profile page if details are missing
        return redirect(url_for("edit_profile"))

    return render_template("profile.html", dark_mode=dark_mode, user=current_user)


#Edit User Profile
@application.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    dark_mode = session.get("dark_mode", False)
    if request.method == "POST":
        full_name = request.form["full_name"]
        surname = request.form["surname"]
        email = request.form["email"]
        cell_number = request.form["cell_number"]

        # Basic validation (can be expanded)
        if not full_name or not surname or not email or not cell_number:
            flash("All fields are required!", "danger")
            return redirect(url_for("edit_profile"))

        # Update user details
        current_user.full_name = full_name
        current_user.surname = surname
        current_user.email = email
        current_user.cell_number = cell_number

        db.session.commit()

        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))

    return render_template("edit_profile.html", dark_mode=dark_mode, user=current_user)

#Change User PASSWORD
@application.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    dark_mode = session.get("dark_mode", False)
    if request.method == "POST":
        current_password = request.form["current_password"]
        new_password = request.form["new_password"]
        confirm_password = request.form["confirm_password"]

        # Check if the current password matches
        if not check_password_hash(current_user.password, current_password):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("change_password"))

        # Check if the new passwords match
        if new_password != confirm_password:
            flash("New passwords do not match.", "danger")
            return redirect(url_for("change_password"))

        # Update password
        current_user.password = generate_password_hash(new_password)
        db.session.commit()
        flash("Password changed successfully!", "success")
        return redirect(url_for("profile"))

    return render_template("change_password.html", dark_mode=dark_mode)


if __name__ == "__main__":
    socketio.run(application)
