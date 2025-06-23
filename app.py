from flask import Flask, render_template, request, redirect, url_for, abort, flash, send_file
from sqlalchemy import create_engine, or_, and_, not_
from sqlalchemy.orm import sessionmaker, joinedload
from models import Base, EagleTrustFundDonor, EagleTrustFundTransaction
from dotenv import load_dotenv
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import ceil
import csv
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from datetime import timedelta

# Load environment variables
load_dotenv()

# Flask application setup
app = Flask(__name__)

# Set the secret key - first try environment variable, then use a default for development
if not os.getenv("FLASK_SECRET_KEY"):
    print("Warning: FLASK_SECRET_KEY not set in .env file. Using default key for development.")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-key-please-change-in-production")

# Enable debug mode if specified in environment
app.debug = os.getenv("FLASK_DEBUG", "False").lower() in ("true", "1", "t")

# Database connection parameters
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Constants for pagination
ITEMS_PER_PAGE = 50

# SQLAlchemy engine and session factory
engine = create_engine(DATABASE_URL)
Base.metadata.bind = engine
Session = sessionmaker(bind=engine)

def parse_multi_values(value_string):
    """Parse comma-separated values and return list"""
    if not value_string:
        return []
    return [v.strip() for v in value_string.split(',') if v.strip()]

def parse_date_range(date_str):
    """Parse date or date range string"""
    if not date_str:
        return None, None
    
    if ' to ' in date_str:
        parts = date_str.split(' to ')
        try:
            start_date = datetime.strptime(parts[0].strip(), '%Y-%m-%d').date() if parts[0].strip() else None
            end_date = datetime.strptime(parts[1].strip(), '%Y-%m-%d').date() if parts[1].strip() else None
            return start_date, end_date
        except ValueError:
            return None, None
    else:
        try:
            single_date = datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
            return single_date, single_date
        except ValueError:
            return None, None

def parse_amount_range(amount_str):
    """Parse amount or amount range string"""
    if not amount_str:
        return None, None
    
    if ' to ' in amount_str:
        parts = amount_str.split(' to ')
        try:
            min_amount = Decimal(parts[0].strip()) if parts[0].strip() else None
            max_amount = Decimal(parts[1].strip()) if parts[1].strip() else None
            return min_amount, max_amount
        except InvalidOperation:
            return None, None
    else:
        try:
            single_amount = Decimal(amount_str.strip())
            return single_amount, single_amount
        except InvalidOperation:
            return None, None

def build_search_query(session, search_params):
    """Helper function to build search query based on parameters"""
    query = session.query(EagleTrustFundDonor)
    field_conditions = []
    filter_conditions = []
    query_joined_transactions = False
    transaction_fields_provided = False

    # Add filter conditions first
    if search_params.get('exclude_deceased') == 'true':
        filter_conditions.append(
            not_(EagleTrustFundDonor.newsletter_status_desc.ilike("DECEASED OR UNDELIVERABLE"))
        )
    if search_params.get('exclude_non_donors') == 'true':
        filter_conditions.append(
            not_(EagleTrustFundDonor.donor_status_desc.ilike("NON DONOR (DO NOT SOLICIT)"))
        )

    # Apply filter conditions
    if filter_conditions:
        query = query.filter(and_(*filter_conditions))

    # Add search conditions (only if specific search fields are provided)
    search_field_provided = False
    
    # Handle multi-value fields
    if search_params.get('alternate_id'):
        field_conditions.append(EagleTrustFundDonor.alternate_id.ilike(f"%{search_params['alternate_id']}%"))
        search_field_provided = True
    if search_params.get('first_name'):
        field_conditions.append(EagleTrustFundDonor.first_name.ilike(f"%{search_params['first_name']}%"))
        search_field_provided = True
    if search_params.get('last_name'):
        field_conditions.append(EagleTrustFundDonor.last_name.ilike(f"%{search_params['last_name']}%"))
        search_field_provided = True
    if search_params.get('email'):
        field_conditions.append(EagleTrustFundDonor.email_address.ilike(f"%{search_params['email']}%"))
        search_field_provided = True
    
    # Handle multiple cities
    if search_params.get('city'):
        cities = parse_multi_values(search_params['city'])
        if cities:
            city_conditions = [EagleTrustFundDonor.city.ilike(f"%{city}%") for city in cities]
            field_conditions.append(or_(*city_conditions))
            search_field_provided = True
    
    # Handle multiple states
    if search_params.get('state'):
        states = parse_multi_values(search_params['state'])
        if states:
            state_conditions = [EagleTrustFundDonor.state.ilike(f"%{state}%") for state in states]
            field_conditions.append(or_(*state_conditions))
            search_field_provided = True
    
    # Handle multiple zip codes
    if search_params.get('zip_code'):
        zip_codes = parse_multi_values(search_params['zip_code'])
        if zip_codes:
            zip_conditions = [EagleTrustFundDonor.zip_plus4.ilike(f"%{zip_code}%") for zip_code in zip_codes]
            field_conditions.append(or_(*zip_conditions))
            search_field_provided = True
    
    if search_params.get('phone'):
        phone_search = f"%{search_params['phone']}%"
        field_conditions.append(or_(
            EagleTrustFundDonor.phone.ilike(phone_search),
            EagleTrustFundDonor.work_phone.ilike(phone_search),
            EagleTrustFundDonor.cell_phone.ilike(phone_search)
        ))
        search_field_provided = True

    # Handle donor status filtering (multiple values)
    if search_params.get('donor_status'):
        statuses = parse_multi_values(search_params['donor_status'])
        if statuses:
            status_conditions = [EagleTrustFundDonor.donor_status.ilike(f"%{status}%") for status in statuses]
            field_conditions.append(or_(*status_conditions))
            search_field_provided = True

    # Handle newsletter status filtering (multiple values)
    if search_params.get('newsletter_status'):
        statuses = parse_multi_values(search_params['newsletter_status'])
        if statuses:
            status_conditions = [EagleTrustFundDonor.newsletter_status.ilike(f"%{status}%") for status in statuses]
            field_conditions.append(or_(*status_conditions))
            search_field_provided = True

    # Handle total donation amount range
    if search_params.get('total_amount_range'):
        min_amount, max_amount = parse_amount_range(search_params['total_amount_range'])
        if min_amount is not None:
            field_conditions.append(EagleTrustFundDonor.total_dollar_amount >= min_amount)
            search_field_provided = True
        if max_amount is not None:
            field_conditions.append(EagleTrustFundDonor.total_dollar_amount <= max_amount)
            search_field_provided = True

    # Handle date added range
    if search_params.get('date_added_range'):
        start_date, end_date = parse_date_range(search_params['date_added_range'])
        if start_date is not None:
            field_conditions.append(EagleTrustFundDonor.date_added_to_database >= start_date.strftime('%Y-%m-%d'))
            search_field_provided = True
        if end_date is not None:
            field_conditions.append(EagleTrustFundDonor.date_added_to_database <= end_date.strftime('%Y-%m-%d'))
            search_field_provided = True

    # Handle expiration date range
    if search_params.get('expiration_date_range'):
        start_date, end_date = parse_date_range(search_params['expiration_date_range'])
        if start_date is not None:
            field_conditions.append(EagleTrustFundDonor.expiration_date >= start_date.strftime('%Y-%m-%d'))
            search_field_provided = True
        if end_date is not None:
            field_conditions.append(EagleTrustFundDonor.expiration_date <= end_date.strftime('%Y-%m-%d'))
            search_field_provided = True

    # Process transaction search parameters
    if search_params.get('trans_date'):
        try:
            trans_date_val = datetime.strptime(search_params['trans_date'], '%Y-%m-%d').date()
            field_conditions.append(EagleTrustFundTransaction.trans_date == trans_date_val)
            transaction_fields_provided = True
        except ValueError:
            flash("Invalid transaction date format. Please use YYYY-MM-DD.", "error")

    if search_params.get('trans_amount'):
        try:
            trans_amount_val = Decimal(search_params['trans_amount'])
            field_conditions.append(EagleTrustFundTransaction.trans_amount == trans_amount_val)
            transaction_fields_provided = True
        except InvalidOperation:
            flash("Invalid transaction amount format.", "error")

    # Process text-based transaction fields
    transaction_text_fields = [
        ('appeal_code', EagleTrustFundTransaction.appeal_code),
        ('payment_type', EagleTrustFundTransaction.payment_type),
        ('update_batch_num', EagleTrustFundTransaction.update_batch_num),
        ('bluebook_job_description', EagleTrustFundTransaction.bluebook_job_description),
        ('bluebook_list_description', EagleTrustFundTransaction.bluebook_list_description)
    ]

    for field_name, field_column in transaction_text_fields:
        if search_params.get(field_name):
            field_conditions.append(field_column.ilike(f"%{search_params[field_name]}%"))
            transaction_fields_provided = True

    # Join with transactions table if any transaction fields were provided
    if transaction_fields_provided:
        query = query.join(EagleTrustFundTransaction, EagleTrustFundDonor.base_donor_id == EagleTrustFundTransaction.base_donor_id).distinct()
        query_joined_transactions = True

    # Apply search conditions only if specific fields were searched
    if field_conditions:
        query = query.filter(and_(*field_conditions))

    # Return the query and whether any search field was provided
    return query, search_field_provided or transaction_fields_provided

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        # Get all search parameters
        search_params = {
            'donor_id': request.form.get("donor_id", "").strip(),
            'alternate_id': request.form.get("alternate_id", "").strip(),
            'first_name': request.form.get("first_name", "").strip(),
            'last_name': request.form.get("last_name", "").strip(),
            'email': request.form.get("email", "").strip(),
            'city': request.form.get("city", "").strip(),
            'state': request.form.get("state", "").strip(),
            'zip_code': request.form.get("zip_code", "").strip(),
            'phone': request.form.get("phone", "").strip(),
            'exclude_deceased': request.form.get("exclude_deceased"),
            'exclude_non_donors': request.form.get("exclude_non_donors"),
            'hidden_columns': request.form.get("hidden_columns", "secondary_title,secondary_first_name,secondary_last_name,secondary_suffix"),
            # Status parameters
            'donor_status': request.form.get("donor_status", "").strip(),
            'newsletter_status': request.form.get("newsletter_status", "").strip(),
            # Range parameters
            'total_amount_range': request.form.get("total_amount_range", "").strip(),
            'date_added_range': request.form.get("date_added_range", "").strip(),
            'expiration_date_range': request.form.get("expiration_date_range", "").strip(),
            # Transaction search parameters
            'trans_date': request.form.get("trans_date", "").strip(),
            'trans_amount': request.form.get("trans_amount", "").strip(),
            'appeal_code': request.form.get("appeal_code", "").strip(),
            'payment_type': request.form.get("payment_type", "").strip(),
            'update_batch_num': request.form.get("update_batch_num", "").strip(),
            'bluebook_job_description': request.form.get("bluebook_job_description", "").strip(),
            'bluebook_list_description': request.form.get("bluebook_list_description", "").strip(),
        }

        # If donor_id is provided, redirect directly to donor page
        if search_params['donor_id']:
            try:
                donor_id = int(search_params['donor_id'])
                return redirect(url_for("donor", donor_id=donor_id))
            except ValueError:
                abort(400, "Invalid donor ID")

        # Create session
        session = Session()
        
        try:
            # Build and execute search query
            query, search_field_provided = build_search_query(session, search_params)
            
            # If no search parameters provided (excluding filter options)
            if not search_field_provided:
                flash("Please enter at least one search criterion (e.g., Name, City, Email, or Transaction details).", "warning")
                return render_template("index.html", search_params=search_params)

            # Get page number from request
            page = request.form.get('page', 1, type=int)
            
            # Count total results
            total_results = query.count()
            total_pages = ceil(total_results / ITEMS_PER_PAGE)
            
            # Apply pagination
            results = query.offset((page - 1) * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE).all()

            # If only one result found, redirect to that donor's page
            if total_results == 1:
                donor_id = results[0].base_donor_id
                session.close()
                return redirect(url_for("donor", donor_id=donor_id))
            
            # If multiple results, show results page
            return render_template(
                "search_results.html",
                donors=results,
                search_params=search_params,
                current_page=page,
                total_pages=total_pages,
                total_results=total_results
            )

        finally:
            session.close()

    # Pass default filter values for GET request
    default_search_params = {
        'exclude_deceased': 'true',
        'exclude_non_donors': 'true',
        'hidden_columns': 'secondary_title,secondary_first_name,secondary_last_name,secondary_suffix'
    }
    return render_template("index.html", search_params=default_search_params)

@app.route("/refine_search", methods=["POST"])
def refine_search():
    # Get current search parameters
    search_params = {}
    for key, value in request.form.items():
        if key.startswith('current_'):
            search_params[key[8:]] = value  # Remove 'current_' prefix

    # Add new search parameter
    field = request.form.get('field')
    value = request.form.get('value')
    if field and value:
        search_params[field] = value

    # Create session
    session = Session()
    
    try:
        # Build and execute search query
        query, search_field_provided = build_search_query(session, search_params)
        
        # Get page number from request
        page = request.form.get('page', 1, type=int)
        
        # Count total results
        total_results = query.count()
        total_pages = ceil(total_results / ITEMS_PER_PAGE)
        
        # Apply pagination
        results = query.offset((page - 1) * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE).all()
        
        return render_template(
            "search_results.html", 
            donors=results, 
            search_params=search_params,
            current_page=page,
            total_pages=total_pages,
            total_results=total_results
        )
    finally:
        session.close()

@app.route("/toggle_column", methods=["POST"])
def toggle_column():
    # Get current search parameters
    search_params = {}
    for key, value in request.form.items():
        if key.startswith('current_'):
            search_params[key[8:]] = value  # Remove 'current_' prefix

    # Get column and action
    column_id = request.form.get('column_id')
    action = request.form.get('action')

    # Update hidden columns
    hidden_columns = search_params.get('hidden_columns', '').split(',') if search_params.get('hidden_columns') else []
    if action == 'hide' and column_id not in hidden_columns:
        hidden_columns.append(column_id)
    elif action == 'show' and column_id in hidden_columns:
        hidden_columns.remove(column_id)
    
    # Update search params with new hidden columns
    search_params['hidden_columns'] = ','.join(filter(None, hidden_columns))

    # Create session
    session = Session()
    
    try:
        # Build and execute search query
        query, search_field_provided = build_search_query(session, search_params)
        
        # Get page number from request
        page = request.form.get('page', 1, type=int)
        
        # Count total results
        total_results = query.count()
        total_pages = ceil(total_results / ITEMS_PER_PAGE)
        
        # Apply pagination
        results = query.offset((page - 1) * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE).all()
        
        return render_template(
            "search_results.html", 
            donors=results, 
            search_params=search_params,
            current_page=page,
            total_pages=total_pages,
            total_results=total_results
        )
    finally:
        session.close()

@app.route("/donor/<int:donor_id>")
def donor(donor_id):
    session = Session()
    try:
        donor = (session.query(EagleTrustFundDonor)
                      .options(joinedload(EagleTrustFundDonor.transactions))
                      .filter_by(base_donor_id=donor_id)
                      .first())
        
        if donor is None:
            abort(404, f"Donor #{donor_id} not found")

        return render_template(
            "donor.html",
            donor=donor,
            transactions=donor.transactions
        )
    finally:
        session.close()

# Helper function to extract donor data from form
def extract_donor_data(form):
    return {
        'base_donor_id': form.get("base_donor_id", "").strip() or None,
        'alternate_id': form.get("alternate_id", "").strip() or None,
        'name_prefix': form.get("name_prefix", "").strip() or None,
        'first_name': form.get("first_name", "").strip() or None,
        'last_name': form.get("last_name", "").strip() or None,
        'suffix': form.get("suffix", "").strip() or None,
        'formatted_full_name': form.get("formatted_full_name", "").strip() or None,
        'secondary_title': form.get("secondary_title", "").strip() or None,
        'secondary_first_name': form.get("secondary_first_name", "").strip() or None,
        'secondary_last_name': form.get("secondary_last_name", "").strip() or None,
        'secondary_suffix': form.get("secondary_suffix", "").strip() or None,
        'secondary_full_name': form.get("secondary_full_name", "").strip() or None,
        'address_1_company': form.get("address_1_company", "").strip() or None,
        'address_2_secondary': form.get("address_2_secondary", "").strip() or None,
        'address_3_primary': form.get("address_3_primary", "").strip() or None,
        'city': form.get("city", "").strip() or None,
        'state': form.get("state", "").strip().upper() or None,
        'zip_plus4': form.get("zip_plus4", "").strip() or None,
        'phone': form.get("phone", "").strip() or None,
        'work_phone': form.get("work_phone", "").strip() or None,
        'cell_phone': form.get("cell_phone", "").strip() or None,
        'salutation_dear': form.get("salutation_dear", "").strip() or None,
        'removal_request_note': form.get("removal_request_note", "").strip() or None,
        'twitter': form.get("twitter", "").strip() or None,
        'gender_code': form.get("gender_code", "").strip() or None,
        'birth_date': form.get("birth_date") or None,
        'newsletter_status': form.get("newsletter_status", "").strip() or None,
        'newsletter_status_desc': form.get("newsletter_status_desc", "").strip() or None,
        'donor_status': form.get("donor_status", "").strip() or None,
        'donor_status_desc': form.get("donor_status_desc", "").strip() or None,
        'date_added_to_database': form.get("date_added_to_database") or None,
        'email_address': form.get("email_address", "").strip() or None,
        'interest_borders': form.get("interest_borders", "").strip() or None,
        'interest_pro_life': form.get("interest_pro_life", "").strip() or None,
        'interest_eagle_council': form.get("interest_eagle_council", "").strip() or None,
        'interest_topic_1': form.get("interest_topic_1", "").strip() or None,
        'interest_topic_2': form.get("interest_topic_2", "").strip() or None,
        'interest_topic_3': form.get("interest_topic_3", "").strip() or None,
        'interest_topic_4': form.get("interest_topic_4", "").strip() or None,
        'education_reporter_status': form.get("education_reporter_status", "").strip() or None,
        'expiration_date': form.get("expiration_date") or None,
        'news_and_notes_status': form.get("news_and_notes_status", "").strip() or None,
        'rnc_life_status': form.get("rnc_life_status", "").strip() or None,
        'eagle_status': form.get("eagle_status", "").strip() or None,
        'eagle_state_president': form.get("eagle_state_president", "").strip() or None,
        'flag': form.get("flag", "").strip() or None,
        'changed': form.get("changed", "").strip() or None,
        'interest': form.get("interest", "").strip() or None,
        'house_publications': form.get("house_publications", "").strip() or None,
    }

# Helper function to parse date strings
def parse_date(date_str):
    if not date_str:
        return None
    try:
        # Adjust format as needed based on expected input 'YYYY-MM-DD'
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None

@app.route("/add_donor", methods=["GET", "POST"])
def add_donor():
    if request.method == "POST":
        session = Session()
        try:
            donor_data = extract_donor_data(request.form)

            # Basic validation (e.g., check for last name)
            if not donor_data.get('last_name'):
                flash("Last name is required.", "error")
                return render_template("add_donor.html", donor=donor_data)

            # Validate and handle base_donor_id
            base_donor_id = None
            if donor_data.get('base_donor_id'):
                try:
                    base_donor_id = int(donor_data['base_donor_id'])
                    # Check if this donor ID already exists
                    existing_donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=base_donor_id).first()
                    if existing_donor:
                        flash(f"Donor ID {base_donor_id} already exists. Please choose a different ID.", "error")
                        return render_template("add_donor.html", donor=donor_data)
                except ValueError:
                    flash("Invalid donor ID. Please enter a valid number.", "error")
                    return render_template("add_donor.html", donor=donor_data)

            # Remove base_donor_id from donor_data since we'll set it separately
            donor_data_copy = donor_data.copy()
            donor_data_copy.pop('base_donor_id', None)

            # Create new donor object
            new_donor = EagleTrustFundDonor(**donor_data_copy)

            # Set the base_donor_id if provided
            if base_donor_id:
                new_donor.base_donor_id = base_donor_id

            # Handle date conversions (adjust field names as needed)
            new_donor.birth_date = parse_date(donor_data.get('birth_date'))
            new_donor.date_added_to_database = parse_date(donor_data.get('date_added_to_database')) or datetime.now().date()
            new_donor.expiration_date = parse_date(donor_data.get('expiration_date'))

            session.add(new_donor)
            session.commit()
            flash(f"Donor '{new_donor.first_name} {new_donor.last_name}' added successfully!", "success")
            return redirect(url_for("donor", donor_id=new_donor.base_donor_id))
        except Exception as e:
            session.rollback()
            app.logger.error(f"Error adding donor: {e}")
            flash(f"Error adding donor: {e}", "error")
            return render_template("add_donor.html", donor=request.form)
        finally:
            session.close()
    else:
        # GET request - auto-fill base_donor_id with max + 1
        session = Session()
        try:
            # Get the maximum base_donor_id from the database
            max_donor_id = session.query(EagleTrustFundDonor.base_donor_id).order_by(EagleTrustFundDonor.base_donor_id.desc()).first()
            next_donor_id = (max_donor_id[0] + 1) if max_donor_id and max_donor_id[0] else 1
            
            return render_template("add_donor.html", donor={'base_donor_id': next_donor_id})
        finally:
            session.close()

@app.route("/donor/<int:donor_id>/edit", methods=["GET", "POST"])
def edit_donor(donor_id):
    session = Session()
    try:
        donor_to_edit = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        if not donor_to_edit:
            abort(404, f"Donor #{donor_id} not found")

        if request.method == "POST":
            donor_data = extract_donor_data(request.form)

            # Basic validation
            if not donor_data.get('last_name'):
                flash("Last name is required.", "error")
                current_data = donor_to_edit.__dict__
                current_data.update(donor_data)
                return render_template("edit_donor.html", donor=current_data)

            # Update donor object fields (excluding base_donor_id which should never be modified)
            for key, value in donor_data.items():
                if key == 'base_donor_id':
                    continue  # Skip base_donor_id - it's the primary key and should never be updated
                elif key in ['birth_date', 'date_added_to_database', 'expiration_date']:
                    setattr(donor_to_edit, key, parse_date(value))
                elif hasattr(donor_to_edit, key):
                    setattr(donor_to_edit, key, value)

            session.commit()
            flash(f"Donor #{donor_id} updated successfully!", "success")
            return redirect(url_for("donor", donor_id=donor_id))
        
        else:
            return render_template("edit_donor.html", donor=donor_to_edit)

    except Exception as e:
        session.rollback()
        app.logger.error(f"Error editing donor {donor_id}: {e}")
        flash(f"Error updating donor: {e}", "error")
        if request.method == "POST":
            # Merge form data with existing donor data for display, but preserve the donor object structure
            current_data = donor_to_edit.__dict__.copy() if 'donor_to_edit' in locals() else {}
            form_data = extract_donor_data(request.form)
            current_data.update(form_data)
            # Ensure base_donor_id is preserved from the original donor
            current_data['base_donor_id'] = donor_id
            return render_template("edit_donor.html", donor=current_data)
        else:
            return redirect(url_for('home'))
    finally:
        session.close()

@app.route("/donor/<int:donor_id>/add_transaction", methods=["GET", "POST"])
def add_transaction(donor_id):
    session = Session()
    try:
        donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
        if not donor:
            abort(404, f"Donor #{donor_id} not found")

        if request.method == "POST":
            trans_date_str = request.form.get('trans_date')
            trans_amount_str = request.form.get('trans_amount', "").strip()
            
            errors = []
            trans_date = parse_date(trans_date_str)
            if not trans_date:
                errors.append("Transaction date is required and must be in YYYY-MM-DD format.")
            
            trans_amount = None
            if not trans_amount_str:
                errors.append("Transaction amount is required.")
            else:
                try:
                    trans_amount = Decimal(trans_amount_str)
                    if trans_amount < 0:
                        errors.append("Transaction amount cannot be negative.")
                except InvalidOperation:
                    errors.append("Invalid transaction amount.")
            
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("add_transaction.html", donor_id=donor_id, transaction=request.form)

            new_transaction = EagleTrustFundTransaction(
                base_donor_id=donor_id,
                trans_date=trans_date,
                trans_amount=trans_amount,
                appeal_code=request.form.get('appeal_code', "").strip() or None,
                payment_type=request.form.get('payment_type', "").strip() or None,
                update_batch_num=request.form.get('update_batch_num', "").strip() or None,
                bluebook_job_description=request.form.get('bluebook_job_description', "").strip() or None,
                bluebook_list_description=request.form.get('bluebook_list_description', "").strip() or None,
                payment_method=request.form.get('payment_method', "").strip() or None
            )

            # Update donor's transaction summary fields
            # Update latest transaction
            if donor.latest_date is None or trans_date > parse_date(donor.latest_date):
                donor.latest_date = trans_date.strftime('%Y-%m-%d')
                donor.latest_amount = trans_amount
                
            # Update largest transaction
            if donor.largest_amount is None or trans_amount > donor.largest_amount:
                donor.largest_amount = trans_amount
                donor.largest_date = trans_date.strftime('%Y-%m-%d')

            # Update first/inception transaction
            if donor.inception_date is None or trans_date < parse_date(donor.inception_date):
                donor.inception_amount = trans_amount
                donor.inception_date = trans_date.strftime('%Y-%m-%d')

            # Update total amounts and response counts
            donor.total_dollar_amount = (donor.total_dollar_amount or Decimal('0')) + trans_amount
            donor.total_responses_includes_zero = (donor.total_responses_includes_zero or 0) + 1
            if trans_amount > 0:
                donor.total_responses_non_zero = (donor.total_responses_non_zero or 0) + 1

            session.add(new_transaction)
            session.commit()
            flash(f"Transaction added successfully for donor #{donor_id}!", "success")
            return redirect(url_for("donor", donor_id=donor_id))

        else:
            return render_template("add_transaction.html", donor_id=donor_id, transaction={})

    except Exception as e:
        session.rollback()
        app.logger.error(f"Error adding transaction for donor {donor_id}: {e}")
        flash(f"Error adding transaction: {e}", "error")
        if request.method == "POST":
            return render_template("add_transaction.html", donor_id=donor_id, transaction=request.form)
        else:
            return redirect(url_for('donor', donor_id=donor_id))
    finally:
        session.close()

@app.route("/donor_quick_add", methods=["GET", "POST"])
def donor_quick_add():
    session = Session()
    try:
        # Get the starting donor ID (max + 1) for both GET and POST
        max_donor_id = session.query(EagleTrustFundDonor.base_donor_id).order_by(EagleTrustFundDonor.base_donor_id.desc()).first()
        next_donor_id = (max_donor_id[0] + 1) if max_donor_id and max_donor_id[0] else 1
        
        if request.method == "POST":
            # Get number of donor rows
            max_row = 0
            for key in request.form.keys():
                if key.startswith('first_name_'):
                    try:
                        row_num = int(key.split('_')[-1])
                        max_row = max(max_row, row_num)
                    except ValueError:
                        continue
            
            if max_row == 0:
                flash("Please add at least one donor.", "warning")
                return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
            
            donors_to_add = []
            errors = []
            used_donor_ids = set()
            
            # Process each donor row
            for i in range(1, max_row + 1):
                first_name = request.form.get(f'first_name_{i}', '').strip()
                last_name = request.form.get(f'last_name_{i}', '').strip()
                salutation = request.form.get(f'salutation_{i}', '').strip() or None
                email = request.form.get(f'email_{i}', '').strip() or None
                phone = request.form.get(f'phone_{i}', '').strip() or None
                address_line_1 = request.form.get(f'address_line_1_{i}', '').strip() or None
                address_line_2 = request.form.get(f'address_line_2_{i}', '').strip() or None
                city = request.form.get(f'city_{i}', '').strip() or None
                state = request.form.get(f'state_{i}', '').strip().upper() or None
                zip_code = request.form.get(f'zip_code_{i}', '').strip() or None
                newsletter_status = request.form.get(f'newsletter_status_{i}', '').strip() or None
                donor_id_str = request.form.get(f'donor_id_{i}', '').strip()
                
                # Skip empty rows
                if not first_name and not last_name and not donor_id_str:
                    continue
                
                # Validate required fields
                if not last_name:
                    errors.append(f"Row {i}: Last name is required")
                    continue
                
                # Validate and handle donor ID
                if not donor_id_str:
                    errors.append(f"Row {i}: Donor ID is required")
                    continue
                
                try:
                    donor_id = int(donor_id_str)
                except ValueError:
                    errors.append(f"Row {i}: Invalid donor ID '{donor_id_str}'")
                    continue
                
                # Check for duplicate donor IDs in this batch
                if donor_id in used_donor_ids:
                    errors.append(f"Row {i}: Duplicate donor ID {donor_id} in this batch")
                    continue
                
                # Check if donor ID already exists in database
                existing_donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
                if existing_donor:
                    errors.append(f"Row {i}: Donor ID {donor_id} already exists in database")
                    continue
                
                used_donor_ids.add(donor_id)
                
                # Create new donor object
                new_donor = EagleTrustFundDonor(
                    base_donor_id=donor_id,
                    first_name=first_name or None,
                    last_name=last_name,
                    salutation_dear=salutation,
                    email_address=email,
                    phone=phone,
                    address_1_company=address_line_1,
                    address_2_secondary=address_line_2,
                    city=city,
                    state=state,
                    zip_plus4=zip_code,
                    newsletter_status=newsletter_status,
                    date_added_to_database=datetime.now().date(),
                    # Set reasonable defaults
                    donor_status='A',  # Active
                    total_dollar_amount=Decimal('0'),
                    total_responses_includes_zero=0,
                    total_responses_non_zero=0
                )
                
                donors_to_add.append(new_donor)
            
            # If there were validation errors, return with errors
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
            
            if not donors_to_add:
                flash("No valid donors to process.", "warning")
                return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
            
            # Add all donors to session
            for donor in donors_to_add:
                session.add(donor)
            
            # Commit all changes
            session.commit()
            
            flash(f"Successfully added {len(donors_to_add)} donors!", "success")
            return redirect(url_for("donor_quick_add"))
        
        # GET request - show empty form with next donor ID
        return render_template("donor_quick_add.html", form_data={}, next_donor_id=next_donor_id)
        
    except Exception as e:
        if request.method == "POST":
            session.rollback()
            app.logger.error(f"Error adding donors: {e}")
            flash(f"Error adding donors: {e}", "error")
            return render_template("donor_quick_add.html", form_data=request.form, next_donor_id=next_donor_id)
        else:
            app.logger.error(f"Error loading donor quick add: {e}")
            flash(f"Error loading page: {e}", "error")
            return redirect(url_for("home"))
    finally:
        session.close()

@app.route("/batch_transactions", methods=["GET", "POST"])
def batch_transactions():
    if request.method == "POST":
        # Get global settings
        trans_date_str = request.form.get('global_trans_date', '').strip()
        update_batch_num = request.form.get('global_update_batch_num', '').strip() or None
        payment_method = request.form.get('global_payment_method', '').strip() or None
        
        # Validate global trans_date
        trans_date = parse_date(trans_date_str)
        if not trans_date:
            flash("Transaction date is required and must be in YYYY-MM-DD format.", "error")
            return render_template("batch_transactions.html", form_data=request.form)
        
        # Get number of transaction rows
        max_row = 0
        for key in request.form.keys():
            if key.startswith('donor_id_'):
                try:
                    row_num = int(key.split('_')[-1])
                    max_row = max(max_row, row_num)
                except ValueError:
                    continue
        
        if max_row == 0:
            flash("Please add at least one transaction.", "warning")
            return render_template("batch_transactions.html", form_data=request.form)
        
        session = Session()
        try:
            transactions_to_add = []
            donors_to_update = {}
            errors = []
            
            # Process each transaction row
            for i in range(1, max_row + 1):
                donor_id_str = request.form.get(f'donor_id_{i}', '').strip()
                amount_str = request.form.get(f'trans_amount_{i}', '').strip()
                appeal_code = request.form.get(f'appeal_code_{i}', '').strip() or None
                payment_type = request.form.get(f'payment_type_{i}', '').strip() or None
                job_description = request.form.get(f'bluebook_job_description_{i}', '').strip() or None
                list_description = request.form.get(f'bluebook_list_description_{i}', '').strip() or None
                
                # Skip empty rows
                if not donor_id_str and not amount_str:
                    continue
                
                # Validate donor ID
                if not donor_id_str:
                    errors.append(f"Row {i}: Donor ID is required")
                    continue
                
                try:
                    donor_id = int(donor_id_str)
                except ValueError:
                    errors.append(f"Row {i}: Invalid donor ID '{donor_id_str}'")
                    continue
                
                # Check if donor exists
                donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
                if not donor:
                    errors.append(f"Row {i}: Donor #{donor_id} not found")
                    continue
                
                # Validate amount
                if not amount_str:
                    errors.append(f"Row {i}: Transaction amount is required")
                    continue
                
                try:
                    trans_amount = Decimal(amount_str)
                    if trans_amount < 0:
                        errors.append(f"Row {i}: Transaction amount cannot be negative")
                        continue
                except InvalidOperation:
                    errors.append(f"Row {i}: Invalid transaction amount '{amount_str}'")
                    continue
                
                # Create transaction object
                new_transaction = EagleTrustFundTransaction(
                    base_donor_id=donor_id,
                    trans_date=trans_date,
                    trans_amount=trans_amount,
                    appeal_code=appeal_code,
                    payment_type=payment_type,
                    update_batch_num=update_batch_num,
                    bluebook_job_description=job_description,
                    bluebook_list_description=list_description,
                    payment_method=payment_method
                )
                
                transactions_to_add.append(new_transaction)
                
                # Track donors that need summary updates
                if donor_id not in donors_to_update:
                    donors_to_update[donor_id] = donor
            
            # If there were validation errors, return with errors
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("batch_transactions.html", form_data=request.form)
            
            if not transactions_to_add:
                flash("No valid transactions to process.", "warning")
                return render_template("batch_transactions.html", form_data=request.form)
            
            # Add all transactions to session
            for transaction in transactions_to_add:
                session.add(transaction)
            
            # Update donor summary fields for each affected donor
            for donor_id, donor in donors_to_update.items():
                donor_transactions = [t for t in transactions_to_add if t.base_donor_id == donor_id]
                
                for transaction in donor_transactions:
                    # Update latest transaction
                    if donor.latest_date is None or trans_date > parse_date(donor.latest_date):
                        donor.latest_date = trans_date.strftime('%Y-%m-%d')
                        donor.latest_amount = transaction.trans_amount
                    
                    # Update largest transaction
                    if donor.largest_amount is None or transaction.trans_amount > donor.largest_amount:
                        donor.largest_amount = transaction.trans_amount
                        donor.largest_date = trans_date.strftime('%Y-%m-%d')
                    
                    # Update first/inception transaction
                    if donor.inception_date is None or trans_date < parse_date(donor.inception_date):
                        donor.inception_amount = transaction.trans_amount
                        donor.inception_date = trans_date.strftime('%Y-%m-%d')
                    
                    # Update total amounts and response counts
                    donor.total_dollar_amount = (donor.total_dollar_amount or Decimal('0')) + transaction.trans_amount
                    donor.total_responses_includes_zero = (donor.total_responses_includes_zero or 0) + 1
                    if transaction.trans_amount > 0:
                        donor.total_responses_non_zero = (donor.total_responses_non_zero or 0) + 1
            
            # Commit all changes
            session.commit()
            
            flash(f"Successfully processed {len(transactions_to_add)} transactions for {len(donors_to_update)} donors!", "success")
            return redirect(url_for("batch_transactions"))
            
        except Exception as e:
            session.rollback()
            app.logger.error(f"Error processing batch transactions: {e}")
            flash(f"Error processing batch transactions: {e}", "error")
            return render_template("batch_transactions.html", form_data=request.form)
        finally:
            session.close()
    
    # GET request - show empty form
    return render_template("batch_transactions.html", form_data={})

@app.route("/transactions", methods=["GET", "POST"])
def transaction_search():
    # Initialize empty search params for both GET and POST
    search_params = {
        'start_date': '',
        'end_date': '',
        'min_amount': '',
        'max_amount': '',
        'appeal_code': '',
        'payment_type': '',
        'update_batch_num': '',
        'bluebook_job_description': '',
        'bluebook_list_description': '',
    }

    if request.method == "POST":
        # Update search parameters from form data
        for key in search_params:
            search_params[key] = request.form.get(key, "").strip()

        session = Session()
        try:
            # Start with base query joining transactions with donors
            query = session.query(EagleTrustFundTransaction).join(
                EagleTrustFundDonor,
                EagleTrustFundTransaction.base_donor_id == EagleTrustFundDonor.base_donor_id
            )

            # Add date range conditions
            if search_params['start_date']:
                try:
                    start_date = datetime.strptime(search_params['start_date'], '%Y-%m-%d').date()
                    query = query.filter(EagleTrustFundTransaction.trans_date >= start_date)
                except ValueError:
                    flash("Invalid start date format. Please use YYYY-MM-DD.", "error")

            if search_params['end_date']:
                try:
                    end_date = datetime.strptime(search_params['end_date'], '%Y-%m-%d').date()
                    query = query.filter(EagleTrustFundTransaction.trans_date <= end_date)
                except ValueError:
                    flash("Invalid end date format. Please use YYYY-MM-DD.", "error")

            # Add amount range conditions
            if search_params['min_amount']:
                try:
                    min_amount = Decimal(search_params['min_amount'])
                    query = query.filter(EagleTrustFundTransaction.trans_amount >= min_amount)
                except InvalidOperation:
                    flash("Invalid minimum amount format.", "error")

            if search_params['max_amount']:
                try:
                    max_amount = Decimal(search_params['max_amount'])
                    query = query.filter(EagleTrustFundTransaction.trans_amount <= max_amount)
                except InvalidOperation:
                    flash("Invalid maximum amount format.", "error")

            # Add text search conditions
            if search_params['appeal_code']:
                query = query.filter(EagleTrustFundTransaction.appeal_code.ilike(f"%{search_params['appeal_code']}%"))
            if search_params['payment_type']:
                query = query.filter(EagleTrustFundTransaction.payment_type.ilike(f"%{search_params['payment_type']}%"))
            if search_params['update_batch_num']:
                query = query.filter(EagleTrustFundTransaction.update_batch_num.ilike(f"%{search_params['update_batch_num']}%"))
            if search_params['bluebook_job_description']:
                query = query.filter(EagleTrustFundTransaction.bluebook_job_description.ilike(f"%{search_params['bluebook_job_description']}%"))
            if search_params['bluebook_list_description']:
                query = query.filter(EagleTrustFundTransaction.bluebook_list_description.ilike(f"%{search_params['bluebook_list_description']}%"))

            # Get page number from request
            page = request.form.get('page', 1, type=int)
            
            # Count total results and calculate total pages
            total_results = query.count()
            total_pages = ceil(total_results / ITEMS_PER_PAGE)
            
            # Apply pagination and ordering
            transactions = query.order_by(EagleTrustFundTransaction.trans_date.desc())\
                              .offset((page - 1) * ITEMS_PER_PAGE)\
                              .limit(ITEMS_PER_PAGE)\
                              .all()

            # Calculate total amount for displayed transactions
            total_amount = sum(t.trans_amount for t in transactions)

            return render_template(
                "transaction_search.html",
                transactions=transactions,
                total_amount=total_amount,
                search_params=search_params,
                search_performed=True,
                current_page=page,
                total_pages=total_pages,
                total_results=total_results
            )

        finally:
            session.close()

    # GET request or no search parameters
    return render_template(
        "transaction_search.html",
        search_params=search_params,
        search_performed=False
    )

def format_currency(amount):
    """Helper function to format currency values"""
    if amount is None:
        return "$0.00"
    return f"${amount:,.2f}"

def generate_csv(data, headers, filename):
    """Generate CSV file from data"""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    
    # Write headers
    writer.writerow(headers)
    
    # Write data rows - ensure all values are strings and handle long text properly
    for row in data:
        cleaned_row = []
        for cell in row:
            if cell is None:
                cleaned_row.append('')
            else:
                # Convert to string and clean up any problematic characters
                cell_str = str(cell).replace('\n', ' ').replace('\r', ' ')
                cleaned_row.append(cell_str)
        writer.writerow(cleaned_row)
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

def generate_pdf(data, headers, filename, title):
    """Generate PDF file from data with dynamic sizing based on column count"""
    from reportlab.lib.pagesizes import letter, A4, A3, A2, A1, landscape, portrait
    
    buffer = io.BytesIO()
    
    # Determine optimal page size and font size based on number of columns
    num_columns = len(headers)
    
    if num_columns <= 6:
        # Small tables - use letter size
        pagesize = landscape(letter)
        header_font_size = 10
        data_font_size = 8
        margin = 30
    elif num_columns <= 10:
        # Medium tables - use A4 landscape
        pagesize = landscape(A4)
        header_font_size = 9
        data_font_size = 7
        margin = 20
    elif num_columns <= 15:
        # Large tables - use A3 landscape
        pagesize = landscape(A3)
        header_font_size = 8
        data_font_size = 6
        margin = 20
    elif num_columns <= 25:
        # Very large tables - use A2 landscape
        pagesize = landscape(A2)
        header_font_size = 7
        data_font_size = 5
        margin = 15
    else:
        # Extremely large tables - use A1 landscape
        pagesize = landscape(A1)
        header_font_size = 6
        data_font_size = 4
        margin = 15
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=margin,
        leftMargin=margin,
        topMargin=margin,
        bottomMargin=margin
    )
    
    elements = []
    
    # Add title with appropriate font size
    styles = getSampleStyleSheet()
    title_style = styles['Heading1'].clone('CustomTitle')
    title_style.fontSize = max(12, header_font_size + 2)
    
    normal_style = styles['Normal'].clone('CustomNormal')
    normal_style.fontSize = max(8, data_font_size + 1)
    
    elements.append(Paragraph(title, title_style))
    elements.append(Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_style))
    
    # Create table with dynamic column widths
    table_data = [headers] + data
    
    # Calculate available width
    page_width = pagesize[0] - (2 * margin)
    
    # Set column widths - distribute evenly but with minimums
    min_col_width = 0.5 * inch
    available_width = page_width - (num_columns * min_col_width)
    
    if available_width > 0:
        # Distribute extra width evenly
        col_width = min_col_width + (available_width / num_columns)
        col_widths = [col_width] * num_columns
    else:
        # Use minimum widths - will cause horizontal scrolling but prevent cutoff
        col_widths = [min_col_width] * num_columns
    
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), header_font_size),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), data_font_size),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ('WORDWRAP', (0, 0), (-1, -1), True),
    ]))
    
    elements.append(table)
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )

@app.route("/download_donor_results/<format>", methods=["POST"])
def download_donor_results(format):
    session = Session()
    try:
        # Build search query from form data
        search_params = {
            'donor_id': request.form.get("donor_id", "").strip(),
            'alternate_id': request.form.get("alternate_id", "").strip(),
            'first_name': request.form.get("first_name", "").strip(),
            'last_name': request.form.get("last_name", "").strip(),
            'email': request.form.get("email", "").strip(),
            'city': request.form.get("city", "").strip(),
            'state': request.form.get("state", "").strip(),
            'zip_code': request.form.get("zip_code", "").strip(),
            'phone': request.form.get("phone", "").strip(),
            'exclude_deceased': request.form.get("exclude_deceased"),
            'exclude_non_donors': request.form.get("exclude_non_donors"),
            'hidden_columns': request.form.get("hidden_columns", ""),
            # Status parameters
            'donor_status': request.form.get("donor_status", "").strip(),
            'newsletter_status': request.form.get("newsletter_status", "").strip(),
            # Range parameters
            'total_amount_range': request.form.get("total_amount_range", "").strip(),
            'date_added_range': request.form.get("date_added_range", "").strip(),
            'expiration_date_range': request.form.get("expiration_date_range", "").strip(),
        }
        
        query, _ = build_search_query(session, search_params)
        donors = query.all()
        
        # Get selected columns from form
        selected_columns = request.form.getlist('selected_columns')
        
        # Define all available columns with their display names
        all_columns = {
            'base_donor_id': 'Donor ID',
            'old_donor_id': 'Legacy Donor ID',
            'alternate_id': 'Alternate ID',
            'name_prefix': 'Name Prefix',
            'first_name': 'First Name',
            'last_name': 'Last Name',
            'suffix': 'Suffix',
            'formatted_full_name': 'Full Name',
            'secondary_title': 'Secondary Title',
            'secondary_first_name': 'Secondary First Name',
            'secondary_last_name': 'Secondary Last Name',
            'secondary_suffix': 'Secondary Suffix',
            'secondary_full_name': 'Secondary Full Name',
            'address_1_company': 'Company',
            'address_2_secondary': 'Address Secondary',
            'address_3_primary': 'Address Primary',
            'city': 'City',
            'state': 'State',
            'zip_plus4': 'ZIP+4',
            'phone': 'Phone',
            'work_phone': 'Work Phone',
            'cell_phone': 'Cell Phone',
            'salutation_dear': 'Salutation',
            'removal_request_note': 'Removal Request',
            'twitter': 'Twitter',
            'gender_code': 'Gender',
            'birth_date': 'Birth Date',
            'newsletter_status': 'Newsletter Status',
            'newsletter_status_desc': 'Newsletter Status Desc',
            'donor_status': 'Donor Status',
            'donor_status_desc': 'Donor Status Desc',
            'date_added_to_database': 'Date Added',
            'email_address': 'Email',
            'interest_borders': 'Interests - Borders',
            'interest_pro_life': 'Interests - Pro Life',
            'interest_eagle_council': 'Interests - Eagle Council',
            'interest_topic_1': 'Interest Topic 1',
            'interest_topic_2': 'Interest Topic 2',
            'interest_topic_3': 'Interest Topic 3',
            'interest_topic_4': 'Interest Topic 4',
            'education_reporter_status': 'Education Reporter Status',
            'expiration_date': 'Expiration Date',
            'news_and_notes_status': 'News Notes Status',
            'rnc_life_status': 'RNC Life Status',
            'eagle_status': 'Eagle Status',
            'eagle_state_president': 'Eagle State President',
            'flag': 'Flag',
            'changed': 'Changed',
            'interest': 'Interest',
            'house_publications': 'House Publications',
            'latest_date': 'Latest Date',
            'latest_amount': 'Latest Amount',
            'largest_date': 'Largest Date',
            'largest_amount': 'Largest Amount',
            'inception_date': 'Inception Date',
            'inception_amount': 'Inception Amount',
            'total_dollar_amount': 'Total Amount',
            'total_responses_non_zero': 'Total Responses (Non-Zero)',
            'total_responses_includes_zero': 'Total Responses'
        }
        
        # If no columns selected, use default set
        if not selected_columns:
            selected_columns = ['base_donor_id', 'first_name', 'last_name', 'email_address', 'city', 'state', 'total_dollar_amount']
        
        # Build columns list in the order selected, filtering out invalid ones
        visible_columns = [(col_id, all_columns[col_id]) for col_id in selected_columns if col_id in all_columns]
        headers = [label for _, label in visible_columns]
        
        # Prepare data rows
        data = []
        for donor in donors:
            row = []
            for col_id, _ in visible_columns:
                value = getattr(donor, col_id, None)
                if col_id in ['total_dollar_amount', 'latest_amount', 'largest_amount', 'inception_amount']:
                    value = format_currency(value)
                elif value is None:
                    value = ''
                row.append(str(value))
            data.append(row)
        
        # Generate appropriate file format
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if format == 'csv':
            return generate_csv(data, headers, f'donor_results_{timestamp}.csv')
        elif format == 'pdf':
            return generate_pdf(data, headers, f'donor_results_{timestamp}.pdf', 'Donor Search Results')
        else:
            abort(400, "Invalid format specified")
            
    finally:
        session.close()

@app.route("/download_transaction_results/<format>", methods=["POST"])
def download_transaction_results(format):
    session = Session()
    try:
        # Get search parameters
        search_params = {
            'start_date': request.form.get('start_date', '').strip(),
            'end_date': request.form.get('end_date', '').strip(),
            'min_amount': request.form.get('min_amount', '').strip(),
            'max_amount': request.form.get('max_amount', '').strip(),
            'appeal_code': request.form.get('appeal_code', '').strip(),
            'payment_type': request.form.get('payment_type', '').strip(),
            'update_batch_num': request.form.get('update_batch_num', '').strip(),
            'bluebook_job_description': request.form.get('bluebook_job_description', '').strip(),
            'bluebook_list_description': request.form.get('bluebook_list_description', '').strip(),
        }
        
        # Build query
        query = session.query(EagleTrustFundTransaction).join(
            EagleTrustFundDonor,
            EagleTrustFundTransaction.base_donor_id == EagleTrustFundDonor.base_donor_id
        )
        
        # Apply filters
        if search_params['start_date']:
            start_date = datetime.strptime(search_params['start_date'], '%Y-%m-%d').date()
            query = query.filter(EagleTrustFundTransaction.trans_date >= start_date)
        if search_params['end_date']:
            end_date = datetime.strptime(search_params['end_date'], '%Y-%m-%d').date()
            query = query.filter(EagleTrustFundTransaction.trans_date <= end_date)
        if search_params['min_amount']:
            query = query.filter(EagleTrustFundTransaction.trans_amount >= Decimal(search_params['min_amount']))
        if search_params['max_amount']:
            query = query.filter(EagleTrustFundTransaction.trans_amount <= Decimal(search_params['max_amount']))
        if search_params['appeal_code']:
            query = query.filter(EagleTrustFundTransaction.appeal_code.ilike(f"%{search_params['appeal_code']}%"))
        if search_params['payment_type']:
            query = query.filter(EagleTrustFundTransaction.payment_type.ilike(f"%{search_params['payment_type']}%"))
        if search_params['update_batch_num']:
            query = query.filter(EagleTrustFundTransaction.update_batch_num.ilike(f"%{search_params['update_batch_num']}%"))
        if search_params['bluebook_job_description']:
            query = query.filter(EagleTrustFundTransaction.bluebook_job_description.ilike(f"%{search_params['bluebook_job_description']}%"))
        if search_params['bluebook_list_description']:
            query = query.filter(EagleTrustFundTransaction.bluebook_list_description.ilike(f"%{search_params['bluebook_list_description']}%"))
        
        transactions = query.order_by(EagleTrustFundTransaction.trans_date.desc()).all()
        
        # Define columns
        headers = ['Date', 'Donor Name', 'Amount', 'Payment Type', 'Payment Method', 'Batch #', 'Job Description']
        
        # Prepare data rows
        data = []
        for trans in transactions:
            # Apply display logic for job description
            job_description = trans.bluebook_job_description or ''
            if (trans.trans_date.year > 2018 and 
                trans.payment_type == "E" and 
                trans.bluebook_job_description == "DUES OR EAGLES"):
                job_description = "PS EAGLES"
            
            data.append([
                trans.trans_date.strftime('%Y-%m-%d'),
                trans.donor.formatted_full_name or f"{trans.donor.first_name} {trans.donor.last_name}",
                format_currency(trans.trans_amount),
                trans.payment_type or '',
                trans.payment_method or '',
                trans.update_batch_num or '',
                job_description
            ])
        
        # Generate appropriate file format
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if format == 'csv':
            return generate_csv(data, headers, f'transaction_results_{timestamp}.csv')
        elif format == 'pdf':
            return generate_pdf(data, headers, f'transaction_results_{timestamp}.pdf', 'Transaction Search Results')
        else:
            abort(400, "Invalid format specified")
            
    finally:
        session.close()

@app.route("/refresh_transactions", methods=["GET", "POST"])
def refresh_transactions():
    if request.method == "POST":
        session = Session()
        try:
            # Get the cutoff date from form or default to 30 days ago
            cutoff_days = int(request.form.get('cutoff_days', 30))
            cutoff_date = datetime.now().date() - timedelta(days=cutoff_days)
            
            app.logger.info(f"Starting transaction refresh for transactions since {cutoff_date}")
            
            # Get all transactions since the cutoff date, grouped by donor
            recent_transactions = session.query(EagleTrustFundTransaction)\
                .filter(EagleTrustFundTransaction.trans_date >= cutoff_date)\
                .order_by(EagleTrustFundTransaction.base_donor_id, EagleTrustFundTransaction.trans_date)\
                .all()
            
            app.logger.info(f"Found {len(recent_transactions)} transactions to process")
            
            if not recent_transactions:
                flash(f"No transactions found since {cutoff_date}. Nothing to update.", "warning")
                return render_template("refresh_transactions.html", refresh_completed=False)
            
            # Group transactions by donor ID
            transactions_by_donor = {}
            for trans in recent_transactions:
                donor_id = trans.base_donor_id
                if donor_id not in transactions_by_donor:
                    transactions_by_donor[donor_id] = []
                transactions_by_donor[donor_id].append(trans)
            
            updated_donors = 0
            processed_transactions = 0
            
            # Process each donor that has recent transactions
            for donor_id, transactions in transactions_by_donor.items():
                # Get the donor record
                donor = session.query(EagleTrustFundDonor).filter_by(base_donor_id=donor_id).first()
                if not donor:
                    app.logger.warning(f"Donor {donor_id} not found, skipping transactions")
                    continue
                
                donor_updated = False
                
                # Process each transaction for this donor
                for transaction in transactions:
                    trans_date = transaction.trans_date
                    trans_amount = transaction.trans_amount
                    processed_transactions += 1
                    
                    # Update latest transaction
                    latest_date_obj = None
                    if donor.latest_date:
                        try:
                            latest_date_obj = datetime.strptime(donor.latest_date, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    
                    if latest_date_obj is None or trans_date > latest_date_obj:
                        donor.latest_date = trans_date.strftime('%Y-%m-%d')
                        donor.latest_amount = trans_amount
                        donor_updated = True
                        
                    # Update largest transaction
                    if donor.largest_amount is None or trans_amount > donor.largest_amount:
                        donor.largest_amount = trans_amount
                        donor.largest_date = trans_date.strftime('%Y-%m-%d')
                        donor_updated = True
                    
                    # Update first/inception transaction
                    inception_date_obj = None
                    if donor.inception_date:
                        try:
                            inception_date_obj = datetime.strptime(donor.inception_date, '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    
                    if inception_date_obj is None or trans_date < inception_date_obj:
                        donor.inception_amount = trans_amount
                        donor.inception_date = trans_date.strftime('%Y-%m-%d')
                        donor_updated = True
                    
                    # Update total amounts and response counts
                    donor.total_dollar_amount = (donor.total_dollar_amount or Decimal('0')) + trans_amount
                    donor.total_responses_includes_zero = (donor.total_responses_includes_zero or 0) + 1
                    if trans_amount > 0:
                        donor.total_responses_non_zero = (donor.total_responses_non_zero or 0) + 1
                    donor_updated = True
                
                if donor_updated:
                    updated_donors += 1
                
                # Commit in batches to avoid timeouts
                if updated_donors % 50 == 0:
                    session.commit()
                    app.logger.info(f"Processed {processed_transactions} transactions, updated {updated_donors} donors")
            
            # Final commit
            session.commit()
            
            app.logger.info(f"Transaction refresh completed: {processed_transactions} transactions processed, {updated_donors} donors updated")
            flash(f"Transaction refresh completed! Processed {processed_transactions} transactions and updated {updated_donors} donors.", "success")
            return render_template("refresh_transactions.html", 
                                 refresh_completed=True,
                                 processed_count=processed_transactions,
                                 updated_count=updated_donors,
                                 cutoff_date=cutoff_date)
            
        except Exception as e:
            session.rollback()
            app.logger.error(f"Error during transaction refresh: {e}")
            flash(f"Error during transaction refresh: {e}", "error")
            return render_template("refresh_transactions.html", refresh_completed=False)
        finally:
            session.close()
    
    # GET request - show confirmation page
    return render_template("refresh_transactions.html", refresh_completed=False)

@app.route("/mailing_list_generator", methods=["GET", "POST"])
def mailing_list_generator():
    if request.method == "POST":
        session = Session()
        try:
            # Get form parameters
            query_title = request.form.get('query_title', '').strip()
            start_date_str = request.form.get('start_date', '').strip()
            
            if not query_title:
                flash("Query title is required.", "error")
                return render_template("mailing_list_generator.html")
            
            if not start_date_str:
                flash("Start date is required.", "error")
                return render_template("mailing_list_generator.html")
            
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                current_date = datetime.now().date()
                three_years_before = start_date - timedelta(days=3*365)
            except ValueError:
                flash("Invalid date format. Please use YYYY-MM-DD.", "error")
                return render_template("mailing_list_generator.html")
            
            app.logger.info(f"Generating mailing list '{query_title}' for date range {start_date} to {current_date}")
            
            # Build the complex query with multiple criteria
            query_conditions = []
            
            # Criteria 1: Donors with transactions between start_date and now with newsletter status A
            subquery1 = session.query(EagleTrustFundDonor.base_donor_id).join(
                EagleTrustFundTransaction,
                EagleTrustFundDonor.base_donor_id == EagleTrustFundTransaction.base_donor_id
            ).filter(
                and_(
                    EagleTrustFundTransaction.trans_date >= start_date,
                    EagleTrustFundTransaction.trans_date <= current_date,
                    EagleTrustFundDonor.newsletter_status == 'A'
                )
            ).distinct()
            
            # Criteria 2: Donors with largest transaction over $100 within [start_date - 3 years] to start_date with newsletter status A
            criteria2 = and_(
                EagleTrustFundDonor.newsletter_status == 'A',
                EagleTrustFundDonor.largest_amount > 100,
                or_(
                    EagleTrustFundDonor.largest_date.is_(None),
                    and_(
                        EagleTrustFundDonor.largest_date >= three_years_before.strftime('%Y-%m-%d'),
                        EagleTrustFundDonor.largest_date <= start_date.strftime('%Y-%m-%d')
                    )
                )
            )
            
            # Criteria 3: Donors with newsletter status A and donor status L
            criteria3 = and_(
                EagleTrustFundDonor.newsletter_status == 'A',
                EagleTrustFundDonor.donor_status == 'L'
            )
            
            # Criteria 4: Donors with newsletter status E (from all time)
            criteria4 = EagleTrustFundDonor.newsletter_status == 'E'
            
            # Combine all criteria with OR
            inclusion_criteria = or_(
                EagleTrustFundDonor.base_donor_id.in_(subquery1),
                criteria2,
                criteria3,
                criteria4
            )
            
            # Exclusion criteria: OMIT donors with newsletter status M, N, or D
            exclusion_criteria = not_(EagleTrustFundDonor.newsletter_status.in_(['M', 'N', 'D']))
            
            # Build final query
            final_query = session.query(EagleTrustFundDonor).filter(
                and_(inclusion_criteria, exclusion_criteria)
            ).order_by(EagleTrustFundDonor.last_name, EagleTrustFundDonor.first_name)
            
            # Execute query
            donors = final_query.all()
            
            app.logger.info(f"Found {len(donors)} donors matching mailing list criteria")
            
            if not donors:
                flash("No donors found matching the specified criteria.", "warning")
                return render_template("mailing_list_generator.html")
            
            # Prepare CSV data
            headers = [
                'Full Name',
                'Salutation', 
                'Company/Address Line 1',
                'Address Line 2',
                'Address Line 3',
                'City',
                'State',
                'ZIP Code',
                'Latest Transaction Date'
            ]
            
            data = []
            for donor in donors:
                # Build full name
                full_name_parts = []
                if donor.name_prefix:
                    full_name_parts.append(donor.name_prefix)
                if donor.first_name:
                    full_name_parts.append(donor.first_name)
                if donor.last_name:
                    full_name_parts.append(donor.last_name)
                if donor.suffix:
                    full_name_parts.append(donor.suffix)
                
                full_name = ' '.join(full_name_parts) if full_name_parts else donor.formatted_full_name or ''
                
                data.append([
                    full_name,
                    donor.salutation_dear or '',
                    donor.address_1_company or '',
                    donor.address_2_secondary or '',
                    donor.address_3_primary or '',
                    donor.city or '',
                    donor.state or '',
                    donor.zip_plus4 or '',
                    donor.latest_date or ''
                ])
            
            # Generate CSV
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{query_title}_allmail_query_{timestamp}.csv"
            
            return generate_csv(data, headers, filename)
            
        except Exception as e:
            app.logger.error(f"Error generating mailing list: {e}")
            flash(f"Error generating mailing list: {e}", "error")
            return render_template("mailing_list_generator.html")
        finally:
            session.close()
    
    # GET request - show form
    return render_template("mailing_list_generator.html")

if __name__ == "__main__":
    port = int(os.getenv("PORT") or os.getenv("FLASK_PORT", 5001))  # Check PORT first, then FLASK_PORT, default to 5001
    app.run(debug=True, port=port)
